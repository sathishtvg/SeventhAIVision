"""How much of a shift is overtime: one rule for check-out, the automatic close and an approval.

Design: docs/plans/2026-10-25-roster-autoclose-overtime-design.md, section 5 (the
owner's choice: only approved overtime is paid, up to the time actually worked).

A shift checked in after this was released has `overtime_needs_approval` set: its
overtime is what a supervisor or admin approved, never more than the time worked
past the planned end. A shift checked in before keeps the rule it was worked
under: time past the planned end beyond `attendance.overtime_threshold_minutes`
counts by itself (the rule shifts.py applied at check-out until then).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def overtime_minutes(*, needs_approval: bool, scheduled_end: datetime | None, actual_end: datetime | None,
                     approved_minutes: float, threshold_minutes: int) -> int:
    """Minutes of overtime a shift carries for pay."""
    if scheduled_end is None or actual_end is None:
        return 0
    worked_past = max(0.0, (actual_end - scheduled_end).total_seconds() / 60)
    if needs_approval:
        return int(min(max(0.0, float(approved_minutes)), worked_past))
    return max(0, round(worked_past - threshold_minutes)) if worked_past > threshold_minutes else 0


def unapproved_extra_minutes(*, needs_approval: bool, scheduled_end: datetime | None, actual_end: datetime | None,
                             approved_minutes: float) -> int:
    """Time worked past the planned end that no approval covers - shown for review, not paid."""
    if not needs_approval or scheduled_end is None or actual_end is None:
        return 0
    worked_past = max(0.0, (actual_end - scheduled_end).total_seconds() / 60)
    return int(max(0.0, worked_past - max(0.0, float(approved_minutes))))


async def request_hours(db: AsyncSession, shift_id: str) -> tuple[float, float]:
    """(hours asked for and not refused - pending or approved; hours approved) on a shift."""
    row = (await db.execute(text("""
        SELECT COALESCE(SUM(hours_requested) FILTER (WHERE status IN ('pending', 'approved')), 0),
               COALESCE(SUM(hours_requested) FILTER (WHERE status = 'approved'), 0)
          FROM overtime_requests WHERE shift_id = CAST(:s AS uuid)
    """), {"s": str(shift_id)})).first()
    return float(row[0]), float(row[1])
