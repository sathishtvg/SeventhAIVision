"""Attendance policy shared by every surface that reports guard presence.

WHY THIS EXISTS
    Three callers now need the same two answers — "what state is this shift
    in?" and "how long after the scheduled start does a no-show count as
    late?":

        routers/shifts.py        writes is_late at check-in time
        routers/attendance.py    the live attendance monitor
        routers/command_centre.py  the Command Centre and the Site Map

    Those three had begun to diverge: the status ladder lived in
    attendance.py as a private helper, the grace default lived in shifts.py
    as a private dict, and the Site Map was about to need both. A second
    copy of "what counts as late" is the kind of drift that later shows up
    as two screens disagreeing about whether a guard turned up — so this
    consolidates rather than duplicates, matching the same "share once
    there is a real second caller" call already made for services/face.py.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Effective defaults when a tenant has set no override. Kept here rather
# than in shifts.py so the writer of is_late and the readers that render it
# can never drift apart.
ATTENDANCE_DEFAULTS = {
    "attendance.geofence_radius_meters": 200,
    "attendance.late_grace_minutes": 10,
    "attendance.overtime_threshold_minutes": 15,
}


async def get_attendance_setting(db: AsyncSession, key: str) -> int:
    """Tenant override if set, else the shipped default. The isinstance
    check rejects a bool because Python's bool is an int subclass and a
    JSONB `true` would otherwise silently become 1 minute."""
    row = (await db.execute(
        text("SELECT setting_value FROM tenant_settings WHERE setting_key = :k"),
        {"k": key},
    )).first()
    if row is not None and isinstance(row[0], int) and not isinstance(row[0], bool):
        return row[0]
    return ATTENDANCE_DEFAULTS[key]


def live_status(row: dict) -> str:
    """The state ladder rendered by the attendance monitor and the map.

    Order matters: a guard on break is 'active' in the shifts table, so
    on_break must be tested before status, or a guard on a meal break shows
    as simply present.

    Note what this deliberately does NOT decide: whether a `not_started`
    shift is *overdue*. is_late is only ever written at check-in, so a
    guard who never turns up keeps is_late = FALSE forever and stays
    'not_started' — the absence that matters most would otherwise read as
    the calmest state on the board. Overdue is therefore derived from
    scheduled_start against the grace period at query time; see
    is_overdue_sql().
    """
    if row["status"] == "completed":
        return "checked_out"
    if row["on_break"]:
        return "on_break"
    if row["status"] == "active":
        return "checked_in"
    if row["is_late"]:
        return "late"
    return "not_started"


#: The ladder the live attendance monitor renders. Richer than live_status()
#: above, which stays as-is because the Command Centre and the Site Map read
#: it and a five-state board is the right density for them.
#:
#: The monitor needs states those two deliberately collapse:
#:   * on_leave      — rostered but excused; must not read as an absence
#:   * not_yet_on_duty / awaiting — a shift that has not started yet is not a
#:     problem, and one inside its grace window is not a problem *yet*. Fusing
#:     them into "not checked in" is what makes a board cry wolf all morning.
#:   * on_time vs late — live_status() answers "present?" and returns
#:     checked_in for both. A command office needs to see punctuality at a
#:     glance, so lateness survives check-in here instead of being erased by
#:     it.
MONITOR_STATUSES = (
    "on_leave", "not_yet_on_duty", "awaiting", "on_time", "late",
    "on_break", "not_reported", "checked_out",
)


def monitor_status(row: dict, now, grace_minutes: int) -> str:
    """State for one rostered guard on the live attendance board.

    `now` is passed in rather than read here so every row in a single refresh
    is judged against one instant — rows evaluated microseconds apart must not
    land on different sides of a grace boundary and make the board flicker.

    Order matters. Leave outranks everything: an excused guard is not missing,
    and showing them red would send the command office chasing someone who
    filed leave weeks ago.
    """
    if row.get("on_leave"):
        return "on_leave"
    if row["status"] == "completed":
        return "checked_out"
    if row.get("on_break"):
        return "on_break"
    if row["status"] == "active":
        # Lateness is recorded at check-in and kept visible afterwards; a guard
        # who arrived 40 minutes late is still the story of that shift.
        return "late" if row.get("is_late") else "on_time"

    start = row.get("scheduled_start")
    if start is None:
        return "not_yet_on_duty"
    if now < start:
        return "not_yet_on_duty"
    if (now - start).total_seconds() <= grace_minutes * 60:
        return "awaiting"
    return "not_reported"


def is_overdue_sql(shift_alias: str = "sh", grace_param: str = "grace_minutes") -> str:
    """SQL fragment: this shift should have started by now and nobody has
    checked in. Expressed in SQL rather than Python so it is evaluated
    against the database clock — the API server and Postgres can disagree,
    and 'is this guard missing right now' should not depend on which.
    """
    return (
        f"({shift_alias}.status = 'scheduled'"
        f" AND now() > {shift_alias}.scheduled_start"
        f"       + make_interval(mins => :{grace_param}))"
    )
