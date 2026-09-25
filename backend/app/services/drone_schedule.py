"""When a drone mission is due to fly.

A sibling of vpatrol_scheduler rather than a caller of it: the drone brief adds
two schedule types Virtual Patrolling does not have, and sharing one module
would mean changing a working one. The rules below are the same ones, restated.

TIME IS COMPUTED IN THE SCHEDULE'S OWN ZONE, THEN CONVERTED. A mission set for
23:00 means 23:00 where the site is. Reading the server clock instead works until
the stack runs somewhere else, and then every flight silently moves by hours —
or by one hour, twice a year, in a zone with daylight saving.

PURE. No database, no clock of its own: every function takes the moment it is
asked about. That is what lets the API preview the next runs, the scheduler
decide what is due, and the tests pin both to a known instant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ONCE, DAILY, WEEKLY = "ONCE", "DAILY", "WEEKLY"
SELECTED_DAYS, SPECIFIC_DATE = "SELECTED_DAYS", "SPECIFIC_DATE"
SCHEDULE_TYPES = (ONCE, DAILY, WEEKLY, SELECTED_DAYS, SPECIFIC_DATE)

#: How far ahead next_occurrences will look before giving up. A year and a bit
#: covers an annual SPECIFIC_DATE without letting a schedule that never fires
#: turn a preview into a long loop.
HORIZON_DAYS = 400


@dataclass(frozen=True)
class Schedule:
    schedule_type: str
    timezone: str
    start_date: date
    launch_time: time
    end_date: date | None = None
    weekdays: tuple[int, ...] = field(default_factory=tuple)        # 0 = Monday
    specific_dates: tuple[date, ...] = field(default_factory=tuple)

    @classmethod
    def from_row(cls, row: dict) -> "Schedule":
        return cls(
            schedule_type=row["schedule_type"],
            timezone=row.get("timezone") or "Asia/Singapore",
            start_date=row["start_date"],
            launch_time=row["launch_time"],
            end_date=row.get("end_date"),
            weekdays=tuple(row.get("weekdays") or ()),
            specific_dates=tuple(row.get("specific_dates") or ()),
        )


def problems(s: Schedule) -> list[str]:
    """Everything wrong with a schedule, in words an administrator can act on.

    An empty list means valid. Returned rather than raised so the API can report
    every problem at once instead of one per attempt.
    """
    out: list[str] = []
    if s.schedule_type not in SCHEDULE_TYPES:
        out.append(f"Unknown schedule type {s.schedule_type!r}. Use one of: {', '.join(SCHEDULE_TYPES)}.")
    try:
        ZoneInfo(s.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        out.append(f"Unknown timezone {s.timezone!r}. Use an IANA name such as Asia/Singapore.")
    if s.end_date is not None and s.end_date < s.start_date:
        out.append("The end date is before the start date.")
    if any(d not in range(7) for d in s.weekdays):
        out.append("Weekdays are numbered 0 (Monday) to 6 (Sunday).")
    if s.schedule_type == SELECTED_DAYS and not s.weekdays:
        out.append("Choose at least one day of the week.")
    if s.schedule_type == SPECIFIC_DATE:
        if not s.specific_dates:
            out.append("Choose at least one date.")
        early = [d for d in s.specific_dates if d < s.start_date]
        late = [d for d in s.specific_dates if s.end_date is not None and d > s.end_date]
        if early:
            out.append(f"{len(early)} chosen date(s) fall before the start date and would never run.")
        if late:
            out.append(f"{len(late)} chosen date(s) fall after the end date and would never run.")
    return out


def occurs_on(s: Schedule, day: date) -> bool:
    """Does this schedule fly on this local calendar day?"""
    if day < s.start_date or (s.end_date is not None and day > s.end_date):
        return False
    if s.schedule_type == ONCE:
        return day == s.start_date
    if s.schedule_type == DAILY:
        return True
    if s.schedule_type == WEEKLY:
        return day.weekday() == s.start_date.weekday()
    if s.schedule_type == SELECTED_DAYS:
        return day.weekday() in s.weekdays
    if s.schedule_type == SPECIFIC_DATE:
        return day in s.specific_dates
    return False


def launch_at(s: Schedule, day: date) -> datetime:
    """The UTC instant of the launch on a local day.

    Combined in the schedule's zone and then converted, so 23:00 Singapore is
    15:00 UTC, and 07:00 New York is 11:00 UTC in summer and 12:00 in winter.
    """
    local = datetime.combine(day, s.launch_time, tzinfo=ZoneInfo(s.timezone))
    return local.astimezone(timezone.utc)


def next_occurrences(s: Schedule, after: datetime, count: int = 5) -> list[datetime]:
    """The next `count` launches strictly after `after`, in UTC."""
    if problems(s):
        return []
    zone = ZoneInfo(s.timezone)
    # Start a day early: the local date of `after` can be a day behind its UTC
    # date, and a launch late that local evening is still in the future.
    day = after.astimezone(zone).date() - timedelta(days=1)
    out: list[datetime] = []
    for _ in range(HORIZON_DAYS + 2):
        if occurs_on(s, day):
            at = launch_at(s, day)
            if at > after:
                out.append(at)
                if len(out) >= count:
                    break
        if s.end_date is not None and day > s.end_date:
            break
        day += timedelta(days=1)
    return out


def due_between(s: Schedule, start: datetime, end: datetime) -> list[datetime]:
    """Launches with start < t <= end, in UTC — what a scheduler tick owes.

    Half-open on the left so consecutive ticks never count one launch twice;
    the database constraint on (schedule_id, scheduled_for) is the backstop.
    """
    if end <= start or problems(s):
        return []
    zone = ZoneInfo(s.timezone)
    day = start.astimezone(zone).date() - timedelta(days=1)
    last = end.astimezone(zone).date() + timedelta(days=1)
    out: list[datetime] = []
    while day <= last:
        if occurs_on(s, day):
            at = launch_at(s, day)
            if start < at <= end:
                out.append(at)
        day += timedelta(days=1)
    return out
