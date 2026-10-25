"""Closing a shift nobody checked out of, and warning the guard first.

Design: docs/plans/2026-10-25-roster-autoclose-overtime-design.md, section 2,
agreed with the owner on 25 October 2026. A shift still open
`attendance.auto_close_hours` (default 15) after check-in - plus any overtime the
guard asked for that was not refused - is closed by the system:
`auto_closed_at` set, shown as "Closed by the system - no check-out", audited.
`attendance.auto_close_reminder_minutes` (default 15) before that, the guard's
phone is told, once for each closing time (an overtime request moves it).

The scheduler runs this every few minutes on the application's own connection,
one organisation at a time with its scope set, as the other shift sweeps do. The
pushes go after each organisation's commit, so a failed push changes nothing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.attendance_status import get_attendance_setting
from app.services.audit import write_audit_log
from app.services.overtime_rules import overtime_minutes

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 300
PUSH_TITLE_REMINDER = "Shift closing soon"
PUSH_TITLE_CLOSED = "Shift closed automatically"


def closes_at(actual_start: datetime, hours: int, extra_hours: float) -> datetime:
    """When an open shift is closed: hours after check-in, plus the overtime asked for and not refused."""
    return actual_start + timedelta(hours=hours) + timedelta(hours=float(extra_hours or 0))


def _zone(name: str | None):
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001
        return timezone.utc


async def _push(redis, tenant_id: str, user_id: str, title: str, body: str, data: dict) -> None:
    if redis is None or not user_id:
        return
    try:
        from app.realtime.redis_listener import _send_expo_push  # noqa: PLC0415
        from app.services.response_notify import tokens_of  # noqa: PLC0415

        tokens = await tokens_of(redis, tenant_id, [user_id])
        if tokens:
            await _send_expo_push(tokens, title, body, data)
    except Exception:  # noqa: BLE001 - a push that fails changes nothing that was decided
        logger.exception("shift auto-close: push failed for tenant %s", tenant_id)


async def sweep(db: AsyncSession, redis=None, now: datetime | None = None,
                only_tenants: list[str] | None = None) -> dict:
    """Warn and close, every organisation (or only those named - for tests). Returns how many were warned and
    closed."""
    now = now or datetime.now(timezone.utc)
    out = {"reminded": 0, "closed": 0}
    tenants = (await db.execute(text(
        "SELECT id, COALESCE(timezone, 'UTC') FROM tenants WHERE is_active = TRUE"
        + (" AND id = ANY(CAST(:only AS uuid[]))" if only_tenants else "")
    ), {"only": list(only_tenants)} if only_tenants else {})).fetchall()
    for tenant_id, tz_name in tenants:
        tid = str(tenant_id)
        tz = _zone(tz_name)
        pushes: list[tuple[str, str, str, dict]] = []
        try:
            await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tid})
            hours = await get_attendance_setting(db, "attendance.auto_close_hours")
            remind_minutes = await get_attendance_setting(db, "attendance.auto_close_reminder_minutes")
            threshold = await get_attendance_setting(db, "attendance.overtime_threshold_minutes")
            rows = (await db.execute(text("""
                SELECT sh.id, sh.guard_user_id, sh.actual_start, sh.scheduled_end, sh.overtime_needs_approval,
                       sh.auto_close_reminded_for, s.name AS site_name,
                       COALESCE((SELECT SUM(r.hours_requested) FROM overtime_requests r
                                  WHERE r.shift_id = sh.id AND r.status IN ('pending', 'approved')), 0) AS extra_hours,
                       COALESCE((SELECT SUM(r.hours_requested) FROM overtime_requests r
                                  WHERE r.shift_id = sh.id AND r.status = 'approved'), 0) AS approved_hours
                  FROM shifts sh LEFT JOIN sites s ON s.id = sh.site_id
                 WHERE sh.status = 'active' AND sh.actual_start IS NOT NULL
            """))).mappings().all()
            for r in rows:
                close_at = closes_at(r["actual_start"], hours, r["extra_hours"])
                local = close_at.astimezone(tz).strftime("%H:%M")
                site = r["site_name"] or "your site"
                data = {"type": "shift_auto_close", "shift_id": str(r["id"])}
                if now >= close_at:
                    ot = overtime_minutes(needs_approval=r["overtime_needs_approval"], scheduled_end=r["scheduled_end"],
                                          actual_end=now, approved_minutes=float(r["approved_hours"]) * 60,
                                          threshold_minutes=threshold)
                    closed = (await db.execute(text("""
                        UPDATE shifts SET status = 'completed', actual_end = :now, auto_closed_at = :now,
                               overtime_minutes = :ot
                         WHERE id = :id AND status = 'active'
                        RETURNING id
                    """), {"now": now, "ot": ot, "id": r["id"]})).first()
                    if closed is None:
                        continue  # checked out a moment ago
                    await db.execute(text(
                        "UPDATE shift_breaks SET break_end = :now WHERE shift_id = :id AND break_end IS NULL"
                    ), {"now": now, "id": r["id"]})
                    await write_audit_log(
                        db, tenant_id=tid, user_id=None, action="shift.auto_closed", resource_type="shift",
                        resource_id=str(r["id"]),
                        detail={"why": "No check-out", "hours_after_check_in": hours,
                                "overtime_hours_asked": float(r["extra_hours"]), "closed_at": now.isoformat(),
                                "guard_user_id": str(r["guard_user_id"]) if r["guard_user_id"] else None},
                    )
                    out["closed"] += 1
                    pushes.append((str(r["guard_user_id"]), PUSH_TITLE_CLOSED,
                                   f"Your shift at {site} was closed automatically at {local} because there was no "
                                   "check-out. If you worked on, ask your supervisor.", data))
                elif now >= close_at - timedelta(minutes=remind_minutes) and r["auto_close_reminded_for"] != close_at:
                    await db.execute(text("UPDATE shifts SET auto_close_reminded_for = :at WHERE id = :id"),
                                     {"at": close_at, "id": r["id"]})
                    out["reminded"] += 1
                    pushes.append((str(r["guard_user_id"]), PUSH_TITLE_REMINDER,
                                   f"Your shift at {site} closes automatically at {local}. Check out at your site, "
                                   "or tell the app you are working overtime.", data))
            await db.commit()
        except Exception:  # noqa: BLE001 - one organisation's trouble is its own
            await db.rollback()
            logger.exception("shift auto-close failed for tenant %s", tid)
            continue
        for user_id, title, body, data in pushes:
            await _push(redis, tid, user_id, title, body, data)
        if redis is not None and pushes:
            try:
                import json  # noqa: PLC0415

                await redis.publish(f"tenant_events:{tid}", json.dumps({
                    "event_type": "attendance_status_changed", "tenant_id": tid,
                    "payload": {"reason": "auto_close"}, "occurred_at": now.isoformat(),
                }))
            except Exception:  # noqa: BLE001
                pass
    return out
