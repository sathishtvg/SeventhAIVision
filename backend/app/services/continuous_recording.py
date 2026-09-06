"""Continuous recording supervisor (Gap 85).

Runs as a background task in the API process (started from main.py's
lifespan — the API already owns the recording task registry). Every
SUPERVISOR_INTERVAL seconds it walks all active tenants and, for every
stream flagged continuous_recording=TRUE on an active camera:

  - starts a recording if none is active (crash recovery / first enable)
  - rotates the active recording once it exceeds RECORDING_SEGMENT_MINUTES,
    so 24/7 footage lands as bounded, crash-safe MP4 segments

Hourly, it also purges recordings older than the tenant's
recording.retention_days setting (env RECORDING_RETENTION_DAYS default 7),
deleting the DB row and the file together so disk usage stays bounded.

The tenants table is a global catalogue (no RLS), so the supervisor can list
tenants with the app role, then sets the tenant GUC per iteration for all
RLS-scoped work.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SUPERVISOR_INTERVAL = int(os.environ.get("RECORDING_SUPERVISOR_INTERVAL_SECONDS", "60"))
SEGMENT_MINUTES = int(os.environ.get("RECORDING_SEGMENT_MINUTES", "15"))
DEFAULT_RETENTION_DAYS = int(os.environ.get("RECORDING_RETENTION_DAYS", "7"))
_PURGE_EVERY_TICKS = max(1, 3600 // max(SUPERVISOR_INTERVAL, 1))  # ~hourly


def needs_rotation(started_at: datetime, now: datetime,
                   segment_minutes: int = SEGMENT_MINUTES) -> bool:
    """Pure: has this active segment exceeded its length?"""
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return now - started_at >= timedelta(minutes=segment_minutes)


async def find_streams_needing_recording(session: AsyncSession) -> list[dict]:
    """Streams flagged for continuous recording with NO active recording row.
    Tenant GUC must already be set.

    A site whose policy sets record_mode to 'off' or 'ai_event' is excluded —
    those modes mean "no continuous capture here". 'motion' and 'scheduled'
    stay included: neither gate is implemented yet (X-A is policy, not the
    capture engine), and over-capturing is recoverable where under-capturing
    loses footage permanently. Filtered in SQL rather than per stream in
    Python to avoid a policy lookup per stream on every supervisor tick.
    """
    result = await session.execute(text("""
        SELECT s.id AS stream_id, s.camera_id, s.url, s.auth_config, c.site_id
        FROM streams s
        JOIN cameras c ON c.id = s.camera_id
        LEFT JOIN recording_policies p
               ON p.site_id = c.site_id AND p.is_active = TRUE
        WHERE s.continuous_recording = TRUE
          AND c.is_active = TRUE
          AND COALESCE(p.record_mode, 'continuous') NOT IN ('off', 'ai_event')
          AND NOT EXISTS (
              SELECT 1 FROM recordings r
              WHERE r.stream_id = s.id AND r.status = 'recording'
          )
    """))
    return [dict(r._mapping) for r in result]


async def find_recordings_to_rotate(session: AsyncSession,
                                    segment_minutes: int = SEGMENT_MINUTES) -> list[str]:
    """Active recordings on continuous streams that exceeded the segment length."""
    result = await session.execute(
        text("""
            SELECT r.id
            FROM recordings r
            JOIN streams s ON s.id = r.stream_id
            WHERE r.status = 'recording'
              AND s.continuous_recording = TRUE
              AND r.started_at <= now() - make_interval(mins => :mins)
        """),
        {"mins": segment_minutes},
    )
    return [str(r.id) for r in result]


async def get_retention_days(session: AsyncSession) -> int:
    """Tenant recording.retention_days setting, env default when unset.

    Delegates to services/recording_policy so the tenant-level fallback has
    one implementation now that per-site policy (Phase X-A) also needs it.
    """
    from app.services.recording_policy import get_tenant_retention_days

    return await get_tenant_retention_days(session, DEFAULT_RETENTION_DAYS)


async def purge_expired_recordings(
    session: AsyncSession, recordings_root: str, retention_days: int
) -> int:
    """Delete finished recordings older than their cutoff — file first, then row.

    `retention_days` is the FALLBACK, not a flat rule: a recording belonging to
    a site with an explicit central_retention_days is judged against that
    instead. Resolved inside the query rather than by looping sites in Python
    because the alternative is one query per site per purge pass, and this way
    a recording with no site (site_id IS NULL) or a site with no policy falls
    through to the tenant value via the same COALESCE.

    COALESCE, not `p.central_retention_days IS NOT NULL` — 0 is a meaningful
    value ("keep nothing centrally", what a local-only site wants) and must not
    be confused with NULL ("inherit").
    """
    result = await session.execute(
        text("""
            SELECT r.id, r.file_path
            FROM recordings r
            LEFT JOIN recording_policies p
                   ON p.site_id = r.site_id AND p.is_active = TRUE
            WHERE r.status IN ('completed', 'failed')
              AND r.started_at < now() - make_interval(
                      days => COALESCE(p.central_retention_days, :days))
            LIMIT 500
        """),
        {"days": retention_days},
    )
    rows = result.all()
    purged = 0
    for rec_id, rel_path in rows:
        if rel_path:
            full = os.path.join(recordings_root, rel_path)
            try:
                if os.path.exists(full):
                    os.remove(full)
            except OSError as exc:
                logger.warning("recording purge: could not delete %s: %s", full, exc)
                continue  # keep the row so the file is retried next pass
        await session.execute(
            text("DELETE FROM recordings WHERE id = CAST(:id AS uuid)"), {"id": rec_id}
        )
        purged += 1
    if purged:
        # Commit clears the transaction-local GUC — capture and restore it so
        # callers can keep using this session for RLS-scoped work.
        tid = (await session.execute(
            text("SELECT current_setting('app.current_tenant', true)")
        )).scalar()
        await session.commit()
        if tid:
            await session.execute(
                text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tid}
            )
    return purged


async def _start_segment(session: AsyncSession, tenant_id: str, stream: dict) -> None:
    """Insert a recording row and launch the capture task (same machinery as
    the manual start endpoint)."""
    from app.routers.streams import (
        RECORDINGS_ROOT, _active_recordings, _build_auth_url, _recording_task,
    )

    recording_id = str(_uuid.uuid4())
    camera_id = str(stream["camera_id"])

    # Bail before touching the table if an identifier is missing. Postgres
    # rejects CAST('' AS uuid), so an empty value here aborts the whole tick
    # with "invalid input syntax for type uuid" — and the tick retries
    # forever, filling the log and never recording anything. Skipping one bad
    # stream lets the other cameras keep recording.
    stream_id = str(stream["stream_id"] or "")
    if not tenant_id or not camera_id or not stream_id or "" in (tenant_id, camera_id, stream_id):
        logger.warning(
            "continuous_recording: skipping stream with missing ids "
            "(tenant=%r camera=%r stream=%r)", tenant_id, camera_id, stream_id,
        )
        return

    rel_path = f"{tenant_id}/{camera_id}/{recording_id}.mp4"
    await session.execute(
        text(
            "INSERT INTO recordings "
            "  (id, tenant_id, camera_id, stream_id, site_id, status, file_path) "
            "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:cid AS uuid), "
            "        CAST(:sid AS uuid), CAST(:site_id AS uuid), 'recording', :fp)"
        ),
        {
            "id": recording_id,
            "tid": tenant_id,
            "cid": camera_id,
            "sid": str(stream["stream_id"]),
            "site_id": str(stream["site_id"]) if stream.get("site_id") else None,
            "fp": rel_path,
        },
    )
    await session.commit()

    rtsp_url = _build_auth_url(stream["url"], stream.get("auth_config") or {})
    stop_event = asyncio.Event()
    task = asyncio.create_task(_recording_task(
        recording_id, tenant_id, camera_id, rtsp_url,
        os.path.join(RECORDINGS_ROOT, rel_path), stop_event,
    ))
    _active_recordings[recording_id] = (task, stop_event)
    logger.info("continuous_recording: started segment %s stream=%s", recording_id, stream["stream_id"])


async def _rotate_segment(recording_id: str) -> None:
    """Signal an active segment to close; the next tick starts its successor."""
    from app.routers.streams import _active_recordings

    entry = _active_recordings.get(recording_id)
    if entry is None:
        return
    task, stop_event = entry
    stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=15)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    _active_recordings.pop(recording_id, None)
    logger.info("continuous_recording: rotated segment %s", recording_id)


async def supervisor_tick(session_factory, tick_count: int) -> None:
    """One pass over all tenants: rotate over-length segments, start missing
    recordings, and (hourly) purge expired ones."""
    recordings_root = os.environ.get("RECORDINGS_ROOT", "/data/recordings")

    async with session_factory() as tenants_session:
        tenant_rows = (await tenants_session.execute(
            text("SELECT id FROM tenants WHERE is_active = TRUE")
        )).all()

    for (tenant_id,) in tenant_rows:
        tid = str(tenant_id)
        try:
            async with session_factory() as session:
                await session.execute(
                    text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tid}
                )
                for rec_id in await find_recordings_to_rotate(session):
                    await _rotate_segment(rec_id)
                for stream in await find_streams_needing_recording(session):
                    await _start_segment(session, tid, stream)
                if tick_count % _PURGE_EVERY_TICKS == 0:
                    days = await get_retention_days(session)
                    purged = await purge_expired_recordings(session, recordings_root, days)
                    if purged:
                        logger.info("continuous_recording: purged %d expired recordings tenant=%s", purged, tid)
        except Exception as exc:
            logger.error("continuous_recording: tenant=%s tick failed: %s", tid, exc)


async def continuous_recording_supervisor() -> None:
    """Forever-loop entry point started from the API lifespan."""
    from app.db.session import AsyncSessionLocal

    logger.info(
        "continuous_recording supervisor started interval=%ss segment=%smin retention=%sd",
        SUPERVISOR_INTERVAL, SEGMENT_MINUTES, DEFAULT_RETENTION_DAYS,
    )
    tick = 0
    while True:
        try:
            await supervisor_tick(AsyncSessionLocal, tick)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("continuous_recording supervisor pass failed: %s", exc)
        tick += 1
        await asyncio.sleep(SUPERVISOR_INTERVAL)
