"""Gaps in what the platform records itself, found every 15 minutes; and the keeping of camera checks and gaps.

Phase 6 of closing the feature reference's known limits (docs/plans/2026-11-04-device-health-measured-design.md).

  unrecorded()   the stretches of a time in which a stream has no usable recording. Pure.
  expected()     from when, and to when, a stream was meant to be recording in the time since the last pass. Pure.
  run()          one pass over every organisation, each as itself: find and keep the gaps, then remove the checks and
                 gaps older than the organisation's device health history period (90 days when it has none).

A GAP is a stretch of at least SMALLEST_S seconds, while a stream was meant to be recording, with no usable piece:
no piece at all, a piece that failed or holds nothing (`EMPTY`), or a piece whose file the integrity sweep found
missing (`FILE_MISSING`). One piece ending and the next beginning takes a second or two, and is not one.

MEANT TO BE RECORDING is what the recorder itself goes by (services/continuous_recording.py): the stream's "record
all the time" is on, its camera is switched on, and its site's recording policy asks for recording. The switch is
known to the moment, from the notes the database makes of it (`recording_switches`, migration 0180). The camera and
the policy are as this pass sees them every 15 minutes, read so as never to make a gap of a time they were off: a
stretch is counted from when they were first seen on, and up to when they were last seen on. Nothing from before a
stream was first watched is counted, so nothing is backdated.

The cause of a gap is not guessed. That the camera was offline then is said, from its own disconnections.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.camera_checks import limits

logger = logging.getLogger(__name__)

KINDS = ("NOT_RECORDED", "EMPTY", "FILE_MISSING")
KIND_TEXT = {"NOT_RECORDED": "Nothing was recorded", "EMPTY": "A recording that failed or holds nothing",
             "FILE_MISSING": "A recording whose file is missing"}
#: Shorter than this is one piece ending and the next beginning.
SMALLEST_S = 10
#: How far back one pass looks, at most, for pieces that finished since the last.
WINDOW = timedelta(hours=48)
#: Checks and gaps are kept for the organisation's device health history period, else for this many days.
KEEP_DAYS = 90
SETTING = "retention.device_health_changes_days"


def _usable(piece: Mapping) -> bool:
    if piece.get("checksum_status") == "FILE_MISSING":
        return False
    if piece["status"] == "recording":
        return True
    return piece["status"] == "completed" and (piece.get("file_size_bytes") or 0) > 0


def unrecorded(start: datetime, end: datetime, pieces: Sequence[Mapping], *, open_at_end: bool) -> list[dict]:
    """The stretches of [start, end] with no usable piece, each with what was there instead. With `open_at_end`, a
    stretch reaching `end` is still going on (its end is None); else it ends at `end`."""
    if end <= start:
        return []
    covered = sorted((max(p["started_at"], start), min(p["ended_at"] or end, end))
                     for p in pieces if _usable(p))
    holes: list[tuple[datetime, datetime]] = []
    cursor = start
    for a, b in covered:
        if b <= cursor:
            continue
        if a > cursor:
            holes.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        holes.append((cursor, end))
    out = []
    for a, b in holes:
        if (b - a).total_seconds() < SMALLEST_S:
            continue
        there = [p for p in pieces if p["started_at"] < b and (p["ended_at"] or end) > a]
        kind = ("FILE_MISSING" if any(p.get("checksum_status") == "FILE_MISSING" for p in there)
                else "EMPTY" if there else "NOT_RECORDED")
        still = open_at_end and b == end
        out.append({"started_at": a, "ended_at": None if still else b, "kind": kind,
                    "seconds": int((b - a).total_seconds())})
    return out


def expected(row: Mapping, now: datetime) -> tuple[datetime, datetime, bool, datetime | None] | None:
    """(from, to, still meant to be recording, since when the camera and policy are seen on) for the time since the
    last pass, or None when the stream was not meant to be recording in it. `row` is the stream as the pass reads it:
    its switch now, when it was last switched on and off, its camera and policy now, and what the last pass saw."""
    others_now = bool(row["camera_on"] and row["policy_records"])
    first = row["first_watched_at"]
    if first is None:                                       # first seen now: watching begins, nothing before it
        return None
    before = row["others_ok_since"]
    others_since = (before or now) if others_now else None
    last_on = row["last_on_at"] or first
    if row["switch_on"] and others_now:
        return max(last_on, others_since, first), now, True, others_since
    # Not meant to be recording now. Was it at the last pass, and when did that end?
    if before is None:
        return None
    if not others_now:
        stop = row["seen_at"]                              # last seen on: never a gap of a time it may have been off
    elif row["last_off_at"] and row["last_off_at"] > row["seen_at"]:
        stop = row["last_off_at"]                          # switched off since the last pass, to the moment
    else:
        return None
    if row["last_off_at"] and row["last_off_at"] <= row["seen_at"] and not row["switch_on"]:
        return None                                        # the switch was already off at the last pass
    return max(last_on, before, first), stop, False, others_since


_STREAMS = """
    SELECT s.id AS stream_id, s.camera_id, s.continuous_recording AS switch_on, c.is_active AS camera_on,
           COALESCE(p.record_mode, 'continuous') NOT IN ('off', 'ai_event') AS policy_records,
           w.first_watched_at, w.others_ok_since, w.seen_at,
           (SELECT max(at) FROM recording_switches x WHERE x.stream_id = s.id AND x.switched_on) AS last_on_at,
           (SELECT max(at) FROM recording_switches x WHERE x.stream_id = s.id AND NOT x.switched_on) AS last_off_at,
           (SELECT min(g.started_at) FROM recording_gaps g WHERE g.stream_id = s.id AND g.ended_at IS NULL) AS open_from
      FROM streams s JOIN cameras c ON c.id = s.camera_id
      LEFT JOIN LATERAL (SELECT record_mode FROM recording_policies
                          WHERE site_id = c.site_id AND is_active = TRUE LIMIT 1) p ON TRUE
      LEFT JOIN recording_watch w ON w.stream_id = s.id
     WHERE s.continuous_recording OR w.stream_id IS NOT NULL
"""

_OFFLINE = """
    SELECT EXISTS (SELECT 1 FROM camera_health_events
                    WHERE camera_id = CAST(:c AS uuid) AND event_type = 'stream_disconnected'
                      AND occurred_at BETWEEN CAST(:a AS timestamptz) - interval '5 minutes'
                                          AND CAST(:b AS timestamptz))
        OR COALESCE((SELECT event_type = 'stream_disconnected' FROM camera_health_events
                      WHERE camera_id = CAST(:c AS uuid) AND occurred_at < CAST(:a AS timestamptz)
                      ORDER BY occurred_at DESC LIMIT 1), FALSE)
"""


async def watch_tenant(db: AsyncSession, now: datetime) -> dict:
    """Find and keep the gaps of the calling organisation's streams. The caller has set the organisation, commits."""
    limit_s = int((await limits(db))["most_gap_s"])
    counts = {"streams": 0, "gaps": 0, "open": 0}
    for row in (await db.execute(text(_STREAMS))).mappings().all():
        counts["streams"] += 1
        span = expected(row, now)
        others_now = bool(row["camera_on"] and row["policy_records"])
        if row["first_watched_at"] is None:
            await db.execute(text("""
                INSERT INTO recording_watch (stream_id, tenant_id, camera_id, first_watched_at, others_ok_since, seen_at)
                VALUES (:s, current_setting('app.current_tenant')::uuid, :c, :now, :since, :now)
                ON CONFLICT (stream_id) DO NOTHING
            """), {"s": row["stream_id"], "c": row["camera_id"], "now": now, "since": now if others_now else None})
            continue
        if span is not None:
            meant_from, stop, still, _ = span
            start = max(meant_from, now - WINDOW)
            if row["open_from"] is not None and meant_from <= row["open_from"] < start:
                start = row["open_from"]                   # a gap still going on keeps where it began
            elif row["open_from"] is not None and row["open_from"] < meant_from:
                # Left open from before recording was last switched on: it ended when it was switched off.
                await db.execute(text("""
                    UPDATE recording_gaps SET ended_at = GREATEST(started_at, :b), updated_at = now(),
                           seconds = GREATEST(0, EXTRACT(EPOCH FROM (GREATEST(started_at, :b) - started_at))::int)
                     WHERE stream_id = :s AND ended_at IS NULL AND started_at < :m
                """), {"s": row["stream_id"], "b": row["last_off_at"] or meant_from, "m": meant_from})
            pieces = (await db.execute(text("""
                SELECT started_at, ended_at, status, file_size_bytes, checksum_status FROM recordings
                 WHERE stream_id = :s AND started_at < :b AND COALESCE(ended_at, :now) > :a
            """), {"s": row["stream_id"], "a": start, "b": stop, "now": now})).mappings().all()
            found = unrecorded(start, stop, pieces, open_at_end=still)
            if not still:
                # A gap left open by the last pass ends when recording stopped being meant to happen.
                await db.execute(text("""
                    UPDATE recording_gaps SET ended_at = GREATEST(started_at, :b), updated_at = now(),
                           seconds = GREATEST(0, EXTRACT(EPOCH FROM (GREATEST(started_at, :b) - started_at))::int)
                     WHERE stream_id = :s AND ended_at IS NULL
                """), {"s": row["stream_id"], "b": stop})
            for gap in found:
                offline = (await db.execute(text(_OFFLINE), {"c": str(row["camera_id"]), "a": gap["started_at"],
                                                             "b": gap["ended_at"] or now})).scalar()
                await db.execute(text("""
                    INSERT INTO recording_gaps (tenant_id, camera_id, stream_id, kind, started_at, ended_at, seconds,
                                                camera_was_offline, limit_s, over_limit, found_at, updated_at)
                    VALUES (current_setting('app.current_tenant')::uuid, :c, :s, :kind, :a, :b, :secs, :off, :lim,
                            :over, :now, :now)
                    ON CONFLICT (stream_id, started_at) DO UPDATE
                       SET ended_at = EXCLUDED.ended_at, seconds = EXCLUDED.seconds, kind = EXCLUDED.kind,
                           camera_was_offline = EXCLUDED.camera_was_offline, limit_s = EXCLUDED.limit_s,
                           over_limit = EXCLUDED.over_limit, updated_at = EXCLUDED.updated_at
                """), {"c": row["camera_id"], "s": row["stream_id"], "kind": gap["kind"], "a": gap["started_at"],
                       "b": gap["ended_at"], "secs": gap["seconds"], "off": bool(offline), "lim": limit_s,
                       "over": gap["seconds"] > limit_s, "now": now})
                counts["gaps"] += 1
                counts["open"] += gap["ended_at"] is None
        await db.execute(text("""
            UPDATE recording_watch SET others_ok_since = :since, seen_at = :now WHERE stream_id = :s
        """), {"s": row["stream_id"], "now": now,
               "since": (row["others_ok_since"] or now) if others_now else None})
    return counts


async def keep_period(db: AsyncSession, now: datetime) -> dict:
    """Remove the calling organisation's checks and closed gaps older than its period; a stream's latest check never."""
    from app.services.record_retention import days_of

    value = (await db.execute(text("SELECT setting_value FROM tenant_settings WHERE setting_key = :k"),
                              {"k": SETTING})).scalar()
    days = days_of(value) or KEEP_DAYS
    cutoff = now - timedelta(days=days)
    checks = (await db.execute(text("""
        DELETE FROM camera_checks k WHERE k.checked_at < :cutoff
           AND EXISTS (SELECT 1 FROM camera_checks n WHERE n.stream_id = k.stream_id AND n.checked_at > k.checked_at)
    """), {"cutoff": cutoff})).rowcount
    gaps = (await db.execute(text("DELETE FROM recording_gaps WHERE ended_at IS NOT NULL AND ended_at < :cutoff"),
                             {"cutoff": cutoff})).rowcount
    return {"days": days, "checks_removed": checks or 0, "gaps_removed": gaps or 0}


async def run(session_factory, now: datetime | None = None) -> dict:
    """One pass over every organisation. One organisation's trouble is its own."""
    now = now or datetime.now(timezone.utc)
    async with session_factory() as db:
        tenants = [str(r[0]) for r in await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))]
    counts = {"gaps": 0, "open": 0, "checks_removed": 0, "gaps_removed": 0, "failed": 0}
    for tenant_id in tenants:
        try:
            async with session_factory() as db:
                await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
                found = await watch_tenant(db, now)
                kept = await keep_period(db, now)
                await db.commit()
        except Exception:
            logger.exception("recording gaps: could not look at tenant %s", tenant_id)
            counts["failed"] += 1
            continue
        for k in ("gaps", "open"):
            counts[k] += found[k]
        for k in ("checks_removed", "gaps_removed"):
            counts[k] += kept[k]
    return counts


async def would_remove(db: AsyncSession, now: datetime) -> dict:
    """What `keep_period` would remove now, for the calling organisation, without removing it."""
    from app.services.record_retention import days_of

    value = (await db.execute(text("SELECT setting_value FROM tenant_settings WHERE setting_key = :k"),
                              {"k": SETTING})).scalar()
    cutoff = now - timedelta(days=days_of(value) or KEEP_DAYS)
    row = (await db.execute(text("""
        SELECT (SELECT count(*) FROM camera_checks k WHERE k.checked_at < :cutoff AND EXISTS
                  (SELECT 1 FROM camera_checks n WHERE n.stream_id = k.stream_id AND n.checked_at > k.checked_at)),
               (SELECT count(*) FROM recording_gaps WHERE ended_at IS NOT NULL AND ended_at < :cutoff)
    """), {"cutoff": cutoff})).first()
    return {"checks": row[0], "gaps": row[1]}

