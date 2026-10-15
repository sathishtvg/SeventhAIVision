"""How long fleet positions and archived fleet vehicles are kept: the organisation's own periods.

The same rules as the other records an organisation may give a period
(services/record_retention.py), kept beside them rather than among them:

NOTHING IS REMOVED UNLESS A PERIOD IS SET, and a period is at least thirty days.
Setting one that already has records older than it is asked for twice, and the
second time says how many it will remove (routers/fleet_tracking.py).

TWO KINDS:

    FLEET_POSITIONS     a position, counted from its fix time. Removed in large
                        batches: a tracker reporting every ten seconds is 8,640
                        positions a day.
    ARCHIVED_VEHICLES   an archived vehicle, counted from when it was archived,
                        with everything recorded about it: its positions,
                        journeys, geofence events and status history. Kept
                        whatever its age while a GPS alert about it is open or
                        acknowledged, or an incident raised from one is not
                        closed.

REMOVED BY THE SCHEDULER, ONCE A DAY, ON ITS SUPERUSER SESSION: the application's
own role may not delete a vehicle or its history at all (migration 0159). Each
organisation's removal is one line in its audit log.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.audit import write_audit_log
from app.services.record_retention import LEAST_DAYS, MOST_DAYS, REMOVED_BY, days_of

logger = logging.getLogger(__name__)

POSITIONS_BATCH = 20_000
POSITIONS_BATCHES = 100
VEHICLES_BATCH = 100


@dataclass(frozen=True)
class Kind:
    key: str
    setting_key: str
    label: str
    tables: tuple[str, ...]
    counted_from: str
    removes: str
    kept_whatever: str | None
    #: True of a row `x` that is old enough to remove (`:cutoff`).
    old_enough: str
    table: str


#: Still referred to: an open or acknowledged GPS alert about the vehicle, or an incident from one that is not closed.
_REFERRED = (
    "EXISTS (SELECT 1 FROM alerts a WHERE a.module_type = 'gps' AND a.message_params->>'vehicle_id' = x.id::text "
    "        AND (a.status IN ('open', 'acknowledged') "
    "             OR EXISTS (SELECT 1 FROM incidents i WHERE i.alert_id = a.id AND i.status <> 'closed')))"
)

KINDS: tuple[Kind, ...] = (
    Kind("FLEET_POSITIONS", "retention.fleet_positions_days", "Fleet vehicle positions", ("vehicle_positions",),
         "its fix time", "The position. The journeys worked out from it go with it; the vehicle stays",
         None, "x.recorded_at < :cutoff", "vehicle_positions"),
    Kind("ARCHIVED_VEHICLES", "retention.archived_vehicles_days", "Archived fleet vehicles",
         ("vehicles", "vehicle_positions", "vehicle_journeys", "geofence_events", "fleet_status_changes",
          "fleet_position_rejects", "fleet_vehicle_group_members", "fleet_geofence_state"),
         "when it was archived",
         "The vehicle with its positions, journeys, geofence events, status history and group memberships. "
         "The alerts and incidents about it are not touched",
         "A vehicle a GPS alert that is open or acknowledged, or an incident from one that is not closed, refers to",
         f"x.archived_at IS NOT NULL AND x.archived_at < :cutoff AND NOT {_REFERRED}", "vehicles"),
)
BY_KEY = {k.key: k for k in KINDS}
SETTING_KEYS = tuple(k.setting_key for k in KINDS)


async def periods(db: AsyncSession) -> dict[str, dict]:
    """The calling organisation's two periods: `{"days": n | None, "set_at": when | None}` each."""
    rows = (await db.execute(text("""
        SELECT setting_key, setting_value, updated_at FROM tenant_settings
         WHERE setting_key = ANY(CAST(:keys AS text[]))
    """), {"keys": list(SETTING_KEYS)})).mappings().all()
    said = {r["setting_key"]: r for r in rows}
    out = {}
    for kind in KINDS:
        row = said.get(kind.setting_key)
        days = days_of(row["setting_value"]) if row else None
        out[kind.key] = {"days": days, "set_at": row["updated_at"] if days is not None else None}
    return out


async def waiting(db: AsyncSession, kind: Kind, days: int, now: datetime | None = None) -> int:
    """How many are already older than `days`, for the calling organisation: what a person is told first."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    return (await db.execute(text(f"SELECT count(*) FROM {kind.table} x WHERE {kind.old_enough}"),
                             {"cutoff": cutoff})).scalar() or 0


async def _positions(db: AsyncSession, tenant_id: str, cutoff: datetime) -> int:
    total = 0
    for _ in range(POSITIONS_BATCHES):
        gone = (await db.execute(text("""
            DELETE FROM vehicle_positions
             WHERE tenant_id = CAST(:t AS uuid)
               AND (id, recorded_at) IN (SELECT id, recorded_at FROM vehicle_positions
                                          WHERE tenant_id = CAST(:t AS uuid) AND recorded_at < :cutoff LIMIT :n)
        """), {"t": tenant_id, "cutoff": cutoff, "n": POSITIONS_BATCH})).rowcount
        total += gone
        if gone < POSITIONS_BATCH:
            break
    return total


async def _vehicles(db: AsyncSession, tenant_id: str, cutoff: datetime) -> int:
    kind = BY_KEY["ARCHIVED_VEHICLES"]
    ids = [str(r[0]) for r in await db.execute(text(f"""
        SELECT x.id FROM vehicles x WHERE x.tenant_id = CAST(:t AS uuid) AND {kind.old_enough} LIMIT :n
    """), {"t": tenant_id, "cutoff": cutoff, "n": VEHICLES_BATCH})]
    if not ids:
        return 0
    # Its history first: the database refuses to remove a vehicle that still has any (0159).
    for table in ("vehicle_positions", "vehicle_journeys", "geofence_events", "fleet_status_changes"):
        await db.execute(text(f"DELETE FROM {table} WHERE vehicle_id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})
    return (await db.execute(text("DELETE FROM vehicles WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})).rowcount


async def run(db: AsyncSession, now: datetime | None = None) -> dict:
    """Every organisation that has set a period, on a superuser session. One organisation's trouble is its own."""
    now = now or datetime.now(timezone.utc)
    rows = (await db.execute(text("""
        SELECT s.tenant_id, s.setting_key, s.setting_value
          FROM tenant_settings s JOIN tenants t ON t.id = s.tenant_id
         WHERE s.setting_key = ANY(CAST(:keys AS text[])) AND t.is_active = TRUE
         ORDER BY s.tenant_id, s.setting_key
    """), {"keys": list(SETTING_KEYS)})).mappings().all()
    by_setting = {k.setting_key: k for k in KINDS}
    wanted: dict[str, list[tuple[Kind, int]]] = {}
    for row in rows:
        days = days_of(row["setting_value"])
        if days is None:
            logger.warning("fleet retention: %s of tenant %s is not a period of %d to %d days; nothing removed",
                           row["setting_key"], row["tenant_id"], LEAST_DAYS, MOST_DAYS)
            continue
        wanted.setdefault(str(row["tenant_id"]), []).append((by_setting[row["setting_key"]], days))
    await db.commit()
    out = {"organisations": 0, "removed": {}, "failed": 0}
    for tenant_id, kinds in wanted.items():
        try:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
            removed = {}
            for kind, days in kinds:
                cutoff = now - timedelta(days=days)
                removed[kind.key] = await (_positions if kind.key == "FLEET_POSITIONS" else _vehicles)(db, tenant_id, cutoff)
            if any(removed.values()):
                await write_audit_log(db, tenant_id=tenant_id, user_id=None, action="retention.purge",
                                      resource_type="fleet_retention", resource_id=None,
                                      detail={"removed": removed, "days": {k.key: d for k, d in kinds}})
            await db.commit()
        except Exception:  # noqa: BLE001 — this organisation's pass, not everyone's
            await db.rollback()
            logger.exception("fleet retention failed for tenant %s", tenant_id)
            out["failed"] += 1
            continue
        out["organisations"] += 1
        for key, n in removed.items():
            out["removed"][key] = out["removed"].get(key, 0) + n
    return out


def statement(chosen: dict[str, dict]) -> list[dict]:
    """The two kinds as the retention statement lists them."""
    return [{"key": k.key, "label": k.label, "setting_key": k.setting_key, "tables": list(k.tables),
             "days": chosen[k.key]["days"], "set_at": chosen[k.key]["set_at"], "counted_from": k.counted_from,
             "removes": k.removes, "kept_whatever": k.kept_whatever, "removed_by": REMOVED_BY} for k in KINDS]
