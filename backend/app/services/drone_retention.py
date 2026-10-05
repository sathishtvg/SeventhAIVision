"""How long drone footage and flight tracks are kept.

Until this existed nothing deleted either. Drone media was put in its own table
precisely so the platform's evidence purge would not take it — that purge deletes
by age alone, including evidence an open incident depends on (gap analysis §1.5).
Being outside it meant being kept for ever, and the organisation's own retention
setting meant nothing for footage from a drone.

FOOTAGE follows the organisation's `evidence.retention_days` — the same setting,
the same default, as every other picture and clip in the product. Past that age
a snapshot or clip is deleted, file first and then its record, UNLESS

    it belongs to an event that became an incident — kept, whatever its age; or
    it belongs to an event that was confirmed and is still open — nobody has
    finished with it; or
    its flight is still in progress.

So what goes is what nobody acted on: footage of events that were resolved,
marked false, or never confirmed, and routine footage attached to no event at
all. Deleting cannot be undone, which is why the rule errs toward keeping.

FLIGHT TRACKS — the telemetry samples — are kept for
`drone.telemetry_retention_days` (a year unless the organisation sets otherwise).
The flight's own record stays: when it flew, how far, what it found. Only the
second-by-second track goes, and the replay and the report of a flight that old
show the planned route without the flown line.

NOT TOUCHED: events, incidents, the stored report documents, and anything still
held only at a site (a gateway prunes its own disk).

Each organisation's purge is one audit entry saying how much went and under what
periods. Run by the drone runner beside its loop, never in front of a flight.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.audit import write_audit_log

logger = logging.getLogger(__name__)

#: A year of flight tracks unless the organisation says otherwise.
DEFAULT_TELEMETRY_DAYS = int(os.environ.get("DRONE_TELEMETRY_RETENTION_DAYS", "365"))
MEDIA_BATCH = 200
#: Telemetry goes in slices so one organisation's backlog never holds a long lock.
TELEMETRY_BATCH = 5000
MAX_TELEMETRY_BATCHES = 200

OPEN_EVENT = "'NEW','ACKNOWLEDGED','INVESTIGATING','ESCALATED'"
LIVE_FLIGHT = "'SCHEDULED','PRECHECK','READY','LAUNCHING','ACTIVE','PAUSED','EVENT_DETECTED','RETURNING'"

DeleteFile = Callable[[str], Awaitable[None]]


async def _scope(db: AsyncSession, tenant_id: str) -> None:
    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})


async def _setting_days(db: AsyncSession, key: str, default: int) -> int:
    value = (await db.execute(text(
        "SELECT setting_value FROM tenant_settings "
        " WHERE tenant_id = current_setting('app.current_tenant')::uuid AND setting_key = :k"),
        {"k": key})).scalar()
    try:
        days = int(value)
    except (TypeError, ValueError):
        return default
    return days if days > 0 else default


async def delete_stored_file(storage_path: str) -> None:
    """Remove one file from wherever this deployment keeps evidence. A file that
    is already gone is not an error: the point is that it is gone."""
    if settings.STORAGE_BACKEND == "s3":
        from app.core.object_store import delete_object
        await asyncio.to_thread(delete_object, storage_path)
        return
    root = Path(settings.EVIDENCE_ROOT).resolve()
    path = (root / storage_path).resolve()
    if root in path.parents and path.is_file():
        path.unlink()


async def purge_media(db: AsyncSession, tenant_id: str, now: datetime, days: int,
                      delete_file: DeleteFile = delete_stored_file) -> dict:
    """Delete this organisation's expired footage. File first, then the record:
    a crash between the two leaves a record of a missing file, which says so when
    opened, rather than a file nothing knows about."""
    cutoff = now - timedelta(days=days)
    deleted = freed = failed = 0
    while True:
        await _scope(db, tenant_id)
        rows = (await db.execute(text(f"""
            SELECT m.id, m.storage_path, m.storage_location, COALESCE(m.size_bytes, 0) AS size_bytes
              FROM drone_event_media m
              LEFT JOIN drone_events e ON e.id = m.event_id
              LEFT JOIN drone_patrol_sessions s ON s.id = COALESCE(m.session_id, e.session_id)
             WHERE m.captured_at < :cutoff
               AND (e.id IS NULL
                    OR (e.incident_id IS NULL
                        AND NOT (e.verification_state = 'VERIFIED' AND e.status IN ({OPEN_EVENT}))))
               AND (s.id IS NULL OR s.status NOT IN ({LIVE_FLIGHT}))
             ORDER BY m.captured_at
             LIMIT :n
        """), {"cutoff": cutoff, "n": MEDIA_BATCH})).mappings().all()
        if not rows:
            await db.commit()
            break
        gone = []
        for row in rows:
            if row["storage_location"] == "central" and row["storage_path"]:
                try:
                    await delete_file(row["storage_path"])
                except Exception as exc:
                    # Keep the record: it is the only thing that still knows the file exists.
                    failed += 1
                    logger.warning("drone retention: could not delete %s: %s", row["storage_path"],
                                   type(exc).__name__)
                    continue
                freed += int(row["size_bytes"])
            gone.append(row["id"])
        if gone:
            await db.execute(text("DELETE FROM drone_event_media WHERE id = ANY(:ids)"), {"ids": gone})
        await db.commit()
        deleted += len(gone)
        if len(gone) < len(rows):
            break            # some would not delete; do not loop on them tonight
    return {"media_deleted": deleted, "bytes_freed": freed, "media_failed": failed}


async def purge_telemetry(db: AsyncSession, tenant_id: str, now: datetime, days: int) -> int:
    cutoff = now - timedelta(days=days)
    total = 0
    for _ in range(MAX_TELEMETRY_BATCHES):
        await _scope(db, tenant_id)
        n = (await db.execute(text("""
            DELETE FROM drone_telemetry
             WHERE (id, recorded_at) IN (
                   SELECT id, recorded_at FROM drone_telemetry
                    WHERE recorded_at < :cutoff ORDER BY recorded_at LIMIT :n)
        """), {"cutoff": cutoff, "n": TELEMETRY_BATCH})).rowcount
        await db.commit()
        total += n
        if n < TELEMETRY_BATCH:
            break
    return total


async def purge_tenant(db: AsyncSession, tenant_id: str, now: datetime,
                       delete_file: DeleteFile = delete_stored_file) -> dict:
    """One organisation's purge. The tenant scope is set again after every
    commit: it does not survive one."""
    await _scope(db, tenant_id)
    media_days = await _setting_days(db, "evidence.retention_days", settings.EVIDENCE_RETENTION_DAYS)
    telemetry_days = await _setting_days(db, "drone.telemetry_retention_days", DEFAULT_TELEMETRY_DAYS)
    await db.commit()

    result = await purge_media(db, tenant_id, now, media_days, delete_file)
    result["telemetry_deleted"] = await purge_telemetry(db, tenant_id, now, telemetry_days)
    if result["media_deleted"] or result["telemetry_deleted"]:
        await _scope(db, tenant_id)
        await write_audit_log(
            db, tenant_id=str(tenant_id), user_id=None, action="drone.retention.purge",
            resource_type="drone_retention", resource_id=None,
            detail={**result, "footage_kept_days": media_days, "tracks_kept_days": telemetry_days})
        await db.commit()
    return result


async def run_retention(factory, now: datetime, delete_file: DeleteFile = delete_stored_file) -> dict:
    """Every organisation in turn, each on its own session so one's failure is
    one's alone."""
    async with factory() as db:
        tenants = [str(r[0]) for r in await db.execute(text("SELECT id FROM tenants"))]
        await db.rollback()
    totals = {"tenants": 0, "media_deleted": 0, "bytes_freed": 0, "media_failed": 0, "telemetry_deleted": 0}
    for tenant_id in tenants:
        async with factory() as db:
            try:
                result = await purge_tenant(db, tenant_id, now, delete_file)
            except Exception:
                await db.rollback()
                logger.exception("drone retention failed for tenant %s", tenant_id)
                continue
        if result["media_deleted"] or result["telemetry_deleted"] or result["media_failed"]:
            totals["tenants"] += 1
        for key in ("media_deleted", "bytes_freed", "media_failed", "telemetry_deleted"):
            totals[key] += result[key]
    return totals
