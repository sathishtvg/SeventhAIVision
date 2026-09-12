"""Deciding when a patrol is due, and creating it exactly once.

TIME IS COMPUTED IN THE SCHEDULE'S OWN ZONE, THEN CONVERTED. A patrol set for
07:00 means 07:00 where the site is. Reading the server clock instead works
perfectly until the stack is deployed somewhere else, and then every patrol in
the system silently shifts by hours — or by one hour, twice a year, in any zone
with daylight saving. Sites have no timezone column, so the schedule carries its
own (migration 0116).

ONE EXECUTION, ONE SESSION, ENFORCED BY THE DATABASE. Two scheduler workers, a
restart mid-run, or a retry after a timeout all lose a read-then-write race. So
this does not check whether a session exists — it tries to create one and treats
the unique-violation on (schedule_id, scheduled_for) as "already handled". The
constraint is the coordination, not an if statement.

A MISSED PATROL IS RECORDED, NOT IGNORED. Past its grace period with nothing
started, a session becomes MISSED. Silence would let a site go uninspected for a
week with nothing to show that it had.

THE LOOKBACK IS DELIBERATELY SHORT. Enabling a schedule that started in January
must not spawn two hundred sessions for patrols nobody could have carried out.
Only an occurrence within the recent window is created; older ones are simply
gone, which is the honest outcome.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import virtual_patrol as vp

logger = logging.getLogger(__name__)

#: How far back an occurrence may be and still be worth creating. Long enough to
#: survive a worker outage of a few hours, short enough that enabling an old
#: schedule does not manufacture history.
LOOKBACK = timedelta(hours=6)

ONCE, DAILY, WEEKLY = "ONCE", "DAILY", "WEEKLY"


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Asia/Singapore")
    except (ZoneInfoNotFoundError, ValueError):
        # A tenant can type anything into a timezone field. Falling back is
        # better than a scheduler that stops for every schedule after the bad
        # one, but it is logged because a patrol running in the wrong zone is
        # not a small thing.
        logger.warning("virtual patrol: unknown timezone %r, using Asia/Singapore", name)
        return ZoneInfo("Asia/Singapore")


def occurs_on(
    *, schedule_type: str, start_date: date, end_date: date | None,
    weekdays: list[int] | None, day: date,
) -> bool:
    """Should this schedule run on this calendar day, in its own zone?"""
    if day < start_date:
        return False
    if end_date is not None and day > end_date:
        return False
    if schedule_type == ONCE:
        return day == start_date
    if schedule_type == DAILY:
        return True
    if schedule_type == WEEKLY:
        # isoweekday: Monday = 1 .. Sunday = 7, matching the stored values.
        return day.isoweekday() in (weekdays or [])
    return False


def latest_due(
    *, schedule_type: str, start_date: date, end_date: date | None,
    weekdays: list[int] | None, patrol_time: time, tz_name: str | None,
    now_utc: datetime, lookback: timedelta = LOOKBACK,
) -> datetime | None:
    """The most recent occurrence that should already exist, as UTC.

    Returns None when nothing is due — before the start date, after the end
    date, on a weekday the schedule skips, before today's time has arrived, or
    when the last occurrence is older than the lookback.

    Checks today and yesterday in the schedule's zone: at 00:30 local the
    occurrence that matters is usually yesterday evening's, and a scheduler that
    only looked at today would quietly skip every late-evening patrol.
    """
    tz = _zone(tz_name)
    local_now = now_utc.astimezone(tz)

    for delta in (0, 1):
        day = (local_now - timedelta(days=delta)).date()
        if not occurs_on(schedule_type=schedule_type, start_date=start_date,
                         end_date=end_date, weekdays=weekdays, day=day):
            continue
        # Constructed in the zone, so DST is applied by the zone rather than
        # assumed by arithmetic.
        occurrence = datetime.combine(day, patrol_time, tzinfo=tz)
        if occurrence > local_now:
            continue
        as_utc = occurrence.astimezone(timezone.utc)
        if now_utc - as_utc > lookback:
            return None
        return as_utc
    return None


async def create_due_sessions(db: AsyncSession, *, now_utc: datetime | None = None) -> dict:
    """Create a session for every schedule whose occurrence has arrived.

    Runs across all tenants, so it reads schedules with the tenant GUC set per
    row — the same pattern as the other cross-tenant sweeps in scheduler_main.
    """
    now_utc = now_utc or datetime.now(timezone.utc)

    schedules = (await db.execute(text("""
        SELECT s.id, s.tenant_id, s.schedule_type, s.start_date, s.end_date,
               s.weekdays, s.patrol_time, s.timezone, s.assigned_user_id
          FROM virtual_patrol_schedules s
          JOIN tenants t ON t.id = s.tenant_id
         WHERE s.enabled AND t.is_active
    """))).mappings().all()

    created, skipped, failed = 0, 0, 0
    for row in schedules:
        due = latest_due(
            schedule_type=row["schedule_type"], start_date=row["start_date"],
            end_date=row["end_date"], weekdays=list(row["weekdays"] or []),
            patrol_time=row["patrol_time"], tz_name=row["timezone"],
            now_utc=now_utc,
        )
        if due is None:
            continue

        # SET LOCAL, so it must be set inside the transaction that does the
        # INSERT. This is the trap that has bitten this codebase repeatedly:
        # after a commit the GUC is gone and every later statement fails.
        try:
            await db.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(row["tenant_id"])},
            )
            await vp.create_session(
                db, schedule_id=str(row["id"]), scheduled_for=due,
                officer_user_id=str(row["assigned_user_id"])
                if row["assigned_user_id"] else None,
            )
            await db.commit()
            created += 1
        except Exception as exc:
            await db.rollback()
            # The unique constraint doing its job is the expected path, not an
            # error: another worker got there first, or this run repeated.
            if "uq_vpsess_execution" in str(exc) or "duplicate key" in str(exc).lower():
                skipped += 1
            else:
                failed += 1
                logger.warning("virtual patrol: could not create session for "
                               "schedule %s: %s", row["id"], exc)

    return {"created": created, "already_existed": skipped, "failed": failed}


async def sweep_missed_sessions(db: AsyncSession, *, now_utc: datetime | None = None) -> int:
    """Mark as MISSED any patrol nobody started before its grace ran out.

    Only SCHEDULED sessions. One that was STARTED and abandoned is a different
    fact — the officer began and stopped — and flattening the two would hide it.
    """
    now_utc = now_utc or datetime.now(timezone.utc)

    rows = (await db.execute(text("""
        SELECT s.id, s.tenant_id
          FROM virtual_patrol_sessions s
          LEFT JOIN virtual_patrol_schedules sc ON sc.id = s.schedule_id
         WHERE s.status = 'SCHEDULED'
           AND s.scheduled_for
               + make_interval(mins => COALESCE(sc.grace_minutes, 15)) < :now
    """), {"now": now_utc})).mappings().all()

    missed = 0
    for row in rows:
        try:
            await db.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(row["tenant_id"])},
            )
            await db.execute(text("""
                UPDATE virtual_patrol_sessions
                   SET status = 'MISSED', updated_at = now()
                 WHERE id = :id AND status = 'SCHEDULED'
            """), {"id": row["id"]})
            await db.commit()
            missed += 1
        except Exception as exc:
            await db.rollback()
            logger.warning("virtual patrol: could not mark session %s missed: %s",
                           row["id"], exc)
    return missed
