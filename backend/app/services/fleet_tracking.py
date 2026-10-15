"""Fleet tracking: what a forwarded position does, and whether a vehicle is being heard from.

Design: docs/plans/2026-10-14-gps-fleet-tracking-design.md, sections 2 and 3.

TRACKING STATUS, from the time of the last fix and nothing else:

    not_configured  tracking is off, or no device is linked
    online          the last fix is newer than the organisation's "online" minutes (5)
    stale           older than that, newer than its "offline" minutes (30)
    offline         older than that, or no fix has ever come

It is worked out whenever it is read, so it cannot be left saying "online" about
a vehicle that has stopped reporting. The minute job records each change once
(`fleet_status_changes`) and keeps `current_status` - which the phone and the
dashboard read - saying "offline" when it is.

A POSITION IS KEPT AT THE DEVICE'S FIX TIME. One per vehicle and fix time: a
position forwarded twice is kept once. One older than the vehicle's last known
fix (sent late, after the device was out of signal) is kept in its place in the
history and does not move the vehicle back.

A POSITION THAT IS NOT KEPT IS COUNTED with its reason, per vehicle
(`fleet_position_rejects`), so a device that sends nonsense can be seen doing so.
Nothing here makes up a position or calls a vehicle live that has not reported.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import fleet_alerts, fleet_geofences
from app.services.fleet_providers import ParsedPosition

logger = logging.getLogger(__name__)

STATUSES = ("online", "stale", "offline", "not_configured")
STATUS_LABEL = {"online": "Online", "stale": "Stale", "offline": "Offline", "not_configured": "Not configured"}
ONLINE_KEY, OFFLINE_KEY = "fleet.online_minutes", "fleet.offline_minutes"
DEFAULT_ONLINE, DEFAULT_OFFLINE = 5, 30
MOST_MINUTES = 24 * 60
MOVING_KMH = 5.0
REASON_WORDS = {
    "not_a_position": "Not a position",
    "no_device_id": "No device ID",
    "bad_coordinates": "Coordinates out of range",
    "zero_position": "Position 0,0 (no GPS fix)",
    "no_fix_time": "No fix time",
    "fix_in_future": "Fix time in the future (the device's clock is wrong)",
    "not_a_fix": "Marked by the provider as not a valid fix",
    "duplicate": "Sent again (already kept)",
    "tracking_off": "Tracking is switched off for this vehicle",
    "archived": "The vehicle is archived",
}


def status_of(*, tracking_enabled: bool, has_device: bool, last_fix_at: datetime | None,
              online_minutes: int, offline_minutes: int, now: datetime) -> str:
    if not tracking_enabled or not has_device:
        return "not_configured"
    if last_fix_at is None:
        return "offline"
    age = (now - last_fix_at).total_seconds() / 60
    if age < online_minutes:
        return "online"
    if age < offline_minutes:
        return "stale"
    return "offline"


def status_of_row(v: Mapping[str, Any], limits: Mapping[str, int], now: datetime) -> str:
    return status_of(tracking_enabled=bool(v.get("tracking_enabled")), has_device=bool(v.get("device_identifier")),
                     last_fix_at=v.get("last_fix_at"), online_minutes=limits["online_minutes"],
                     offline_minutes=limits["offline_minutes"], now=now)


def status_sql(alias: str = "v") -> str:
    """The same status, worked out by the database: needs :online_minutes and :offline_minutes (`limits`)."""
    return f"""(CASE WHEN NOT {alias}.tracking_enabled OR {alias}.device_identifier IS NULL THEN 'not_configured'
                 WHEN {alias}.last_fix_at IS NULL THEN 'offline'
                 WHEN {alias}.last_fix_at > now() - make_interval(mins => :online_minutes) THEN 'online'
                 WHEN {alias}.last_fix_at > now() - make_interval(mins => :offline_minutes) THEN 'stale'
                 ELSE 'offline' END)"""


def check_limits(online: Any, offline: Any) -> dict:
    """The two limits, as an organisation may set them: whole minutes, online before offline."""
    for value in (online, offline):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MOST_MINUTES:
            raise ValueError(f"Each limit is a whole number of minutes from 1 to {MOST_MINUTES}")
    if offline <= online:
        raise ValueError("A vehicle counts as offline later than it counts as stale")
    return {"online_minutes": online, "offline_minutes": offline}


async def limits(db: AsyncSession) -> dict:
    """The calling organisation's two limits, or the defaults where it has set none (or set them wrongly)."""
    rows = await db.execute(text(
        "SELECT setting_key, setting_value FROM tenant_settings WHERE setting_key = ANY(:keys)"),
        {"keys": [ONLINE_KEY, OFFLINE_KEY]})
    found: dict = {}
    for row in rows:
        value = row.setting_value
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        found[row.setting_key] = value
    try:
        return check_limits(found.get(ONLINE_KEY, DEFAULT_ONLINE), found.get(OFFLINE_KEY, DEFAULT_OFFLINE))
    except ValueError:
        return {"online_minutes": DEFAULT_ONLINE, "offline_minutes": DEFAULT_OFFLINE}


async def note_reject(db: AsyncSession, tenant_id: str, vehicle_id: str, reason: str, detail: dict | None = None) -> None:
    await db.execute(text("""
        INSERT INTO fleet_position_rejects (tenant_id, vehicle_id, reason, last_detail)
        VALUES (CAST(:t AS uuid), CAST(:v AS uuid), :r, CAST(:d AS jsonb))
        ON CONFLICT (vehicle_id, reason)
        DO UPDATE SET times = fleet_position_rejects.times + 1, last_at = now(), last_detail = EXCLUDED.last_detail
    """), {"t": tenant_id, "v": vehicle_id, "r": reason, "d": json.dumps(detail or {}, default=str)})


async def record_status(db: AsyncSession, tenant_id: str, v: Mapping[str, Any], to_status: str, now: datetime) -> bool:
    """Keep a status change once, in the history and on the vehicle. Says whether there was one."""
    if v["tracking_status"] == to_status:
        return False
    await db.execute(text("""
        INSERT INTO fleet_status_changes (tenant_id, vehicle_id, from_status, to_status, last_fix_at, changed_at)
        VALUES (CAST(:t AS uuid), CAST(:v AS uuid), :f, :s, :fix, :at)
    """), {"t": tenant_id, "v": str(v["id"]), "f": v["tracking_status"], "s": to_status, "fix": v.get("last_fix_at"), "at": now})
    await db.execute(text("""
        UPDATE vehicles
           SET tracking_status = CAST(:s AS varchar), tracking_status_at = :at,
               current_status = CASE WHEN CAST(:s AS varchar) IN ('offline', 'not_configured') THEN 'offline'
                                     ELSE current_status END
         WHERE id = CAST(:v AS uuid)
    """), {"s": to_status, "at": now, "v": str(v["id"])})
    return True


async def accept(db: AsyncSession, tenant_id: str, vehicle_id: str, pos: ParsedPosition, *, source: str,
                 now: datetime | None = None) -> dict:
    """Keep one position of one of the calling organisation's vehicles. Returns what happened, in words a test reads."""
    at = now or datetime.now(timezone.utc)
    v = (await db.execute(text("SELECT * FROM vehicles WHERE id = CAST(:v AS uuid)"), {"v": vehicle_id})).mappings().first()
    if v is None:
        return {"accepted": False, "reason": "unknown_device"}
    if v["archived_at"] is not None:
        await note_reject(db, tenant_id, vehicle_id, "archived")
        return {"accepted": False, "reason": "archived"}
    if not v["tracking_enabled"]:
        await note_reject(db, tenant_id, vehicle_id, "tracking_off")
        return {"accepted": False, "reason": "tracking_off"}
    kept = (await db.execute(text("""
        INSERT INTO vehicle_positions (tenant_id, vehicle_id, lat, lon, speed, heading, accuracy, recorded_at,
                                       received_at, source, ignition, satellites, altitude, valid)
        VALUES (CAST(:t AS uuid), CAST(:v AS uuid), :lat, :lon, :speed, :heading, :accuracy, :fix,
                :received, :source, :ignition, :sat, :alt, :valid)
        ON CONFLICT (vehicle_id, recorded_at) DO NOTHING
        RETURNING id
    """), {"t": tenant_id, "v": vehicle_id, "lat": pos.lat, "lon": pos.lon, "speed": pos.speed_kmh, "heading": pos.heading,
           "accuracy": pos.accuracy, "fix": pos.fix_at, "received": at, "source": source, "ignition": pos.ignition,
           "sat": pos.satellites, "alt": pos.altitude, "valid": pos.valid})).first()
    if kept is None:
        await note_reject(db, tenant_id, vehicle_id, "duplicate", {"fix_time": pos.fix_at.isoformat()})
        return {"accepted": False, "reason": "duplicate"}
    newer = v["last_fix_at"] is None or pos.fix_at > v["last_fix_at"]
    if not newer:
        # Sent late: kept in its place in the history; the vehicle stays where its newest fix put it.
        await db.execute(text("UPDATE vehicles SET last_received_at = :at WHERE id = CAST(:v AS uuid)"),
                         {"at": at, "v": vehicle_id})
        return {"accepted": True, "latest": False, "moved": False}
    moving = pos.speed_kmh is not None and pos.speed_kmh >= MOVING_KMH
    await db.execute(text("""
        UPDATE vehicles
           SET last_lat = :lat, last_lon = :lon, last_speed = :speed, last_heading = :heading,
               last_accuracy = :accuracy, last_altitude = :alt, last_ignition = :ignition, last_satellites = :sat,
               last_fix_at = :fix, last_position_at = :fix, last_received_at = :at,
               current_status = :motion
         WHERE id = CAST(:v AS uuid)
    """), {"lat": pos.lat, "lon": pos.lon, "speed": pos.speed_kmh, "heading": pos.heading, "accuracy": pos.accuracy,
           "alt": pos.altitude, "ignition": pos.ignition, "sat": pos.satellites, "fix": pos.fix_at, "at": at,
           "motion": "moving" if moving else "idle", "v": vehicle_id})
    status = status_of(tracking_enabled=True, has_device=bool(v["device_identifier"]), last_fix_at=pos.fix_at,
                       now=at, **(await limits(db)))
    raised: list = []
    if await record_status(db, tenant_id, v, status, at) and status == "online":
        # Reporting again: a note on the episode's alerts. Stale and offline are raised by the minute pass only.
        raised += await fleet_alerts.status_changed(db, tenant_id, v, v["tracking_status"], status, at)
    if pos.valid is not False:
        raised += await fleet_geofences.evaluate(db, tenant_id, v, fleet_geofences.Fix(
            pos.fix_at, pos.lat, pos.lon, pos.accuracy, pos.speed_kmh))
    # `raised` is announced by the caller once this is committed.
    return {"accepted": True, "latest": True, "moved": True, "status": status, "raised": raised}


async def sweep(session_factory, redis=None, now: datetime | None = None) -> dict:
    """One pass over every organisation: record each tracked vehicle whose status the clock has changed.
    Each organisation is a transaction of its own."""
    at = now or datetime.now(timezone.utc)
    async with session_factory() as db:
        tenants = [str(r[0]) for r in await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))]
    changed = 0
    for tenant_id in tenants:
        try:
            async with session_factory() as db:
                await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
                bounds = await limits(db)
                chosen = await fleet_alerts.settings_of(db)
                rows = (await db.execute(text("""
                    SELECT id, name, plate_number, site_id, service_status, tracking_enabled, device_identifier,
                           last_fix_at, tracking_status
                      FROM vehicles
                     WHERE archived_at IS NULL AND (tracking_enabled OR tracking_status <> 'not_configured')
                """))).mappings().all()
                moved, raised = [], []
                for v in rows:
                    to_status = status_of_row(v, bounds, at)
                    if await record_status(db, tenant_id, v, to_status, at):
                        moved.append(str(v["id"]))
                        raised += await fleet_alerts.status_changed(db, tenant_id, v, v["tracking_status"], to_status,
                                                                    at, chosen)
                raised += await fleet_geofences.minute(db, tenant_id, at)
                await db.commit()
        except Exception:
            logger.exception("fleet status: could not look at tenant %s", tenant_id)
            continue
        changed += len(moved)
        if moved and redis is not None:
            await publish(redis, tenant_id, moved, "fleet_status_changed")
        await fleet_alerts.announce(redis, tenant_id, raised)
    return {"changed": changed}


async def publish(redis, tenant_id: str, vehicle_ids: list[str], event_type: str = "fleet_moved") -> None:
    """Say which vehicles changed - never where they are. The live connection reaches everybody in the
    organisation, and somebody limited to sites must not learn another site's vehicle's position from it;
    each screen asks for the positions it may see."""
    try:
        await redis.publish(f"tenant_events:{tenant_id}", json.dumps({
            "event_type": event_type, "tenant_id": tenant_id,
            "payload": {"vehicle_ids": vehicle_ids},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }))
    except Exception:
        logger.warning("fleet: could not publish %s for tenant %s", event_type, tenant_id)
