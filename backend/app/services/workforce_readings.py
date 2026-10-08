"""What is recorded of each guard's work, counted: a reading per guard and per site.

  read()      every section the caller may read, for each guard, each site and the sites together
  people()    who the guards of a reading are
  blank()     a section with nothing counted in it. Pure.

SIX SECTIONS, each from rows the platform already keeps: shifts; patrols;
responses to incidents; violations; training and certifications; handovers.
Until now each was on its own screen. Nothing is stored: a reading is counted
when it is asked for.

A READING IS NOT AN APPRAISAL. It is counts, and each count stands beside how
much there was to do: shifts late beside shifts worked, tours missed beside
tours due, responses declined beside responses sent. A guard given more shifts
has more chances to be late, and a count without its denominator would say
something it does not know.

NOTHING IS SCORED AND NOBODY IS RANKED. There is no total, index or grade of a
person, and a list of guards is in order of name. Two guards are not compared
by anything here.

A VIOLATION THAT WAS WAIVED IS SAID TO BE WAIVED, and one that is disputed is
said to be disputed. A reading does not count against a guard what a reviewer
set aside.

EACH SECTION IS READ UNDER ITS OWN EXISTING PERMISSION, on top of the one to
read readings at all. A person's own reading is theirs and is given whole.

A SITE'S FIGURES ARE WHAT IS RECORDED AT IT; somebody held to particular sites
reads what is recorded at those sites. Training and certifications are a
person's and not a site's: they are in a guard's reading and in no site's.

Read-only: nothing here writes to the database.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SECTIONS = ("SHIFTS", "PATROLS", "RESPONSES", "VIOLATIONS", "TRAINING", "HANDOVERS")
TITLE = {"SHIFTS": "Shifts", "PATROLS": "Patrols", "RESPONSES": "Responses", "VIOLATIONS": "Violations",
         "TRAINING": "Training and certifications", "HANDOVERS": "Handovers"}
#: The permission each section is read under. It is the one its own screen asks for.
NEEDS = {"SHIFTS": "shift:read", "PATROLS": "patrol:read", "RESPONSES": "response:read",
         "VIOLATIONS": "violation:read", "TRAINING": "training:read", "HANDOVERS": "handover:read"}
#: What is a person's and not a site's: it is in a guard's reading and in no site's.
PERSONAL = ("TRAINING",)
VIOLATION_TYPES = ("no_show", "late_checkin", "geofence_failure", "early_departure", "manual")
PERIOD_DAYS = (7, 28, 90)
DEFAULT_DAYS = 28
#: How far ahead a certificate or a course counts as about to lapse.
SOON_DAYS = 30
COUNTED_FROM = {
    "SHIFTS": "Shifts due to begin in the period: worked, started late, and never started.",
    "PATROLS": "Tours assigned to the guard that fell due and are over, and the patrols they walked with the "
               "checkpoints scanned of those on the route.",
    "RESPONSES": "Each time the guard was sent to an incident in the period: accepted, declined, arrived, and the "
                 "middle time from being sent to arriving.",
    "VIOLATIONS": "Violations recorded in the period, with how many of them a reviewer waived and how many the "
                  "guard disputes.",
    "TRAINING": "Courses passed in the period; courses still run and certificates that have lapsed or lapse within "
                "30 days, as they stand today; and rostered shifts whose requirement the guard does not meet.",
    "HANDOVERS": "Handovers the guard gave in the period, and how many the incoming guard disputed.",
}
NOTE = ("Counts of what is recorded of each guard's work at the sites shown, each beside how much there was to do. "
        "It is not an appraisal, it ranks nobody, and it decides nothing about anybody's employment.")

_IN = "x.at >= :start AND x.at < :end"

#: One statement for each part that happens at a site: rows as `x` with a guard and a site, then what is counted.
_PARTS: dict[str, tuple[str, str]] = {
    "SHIFTS": ("""
        SELECT s.guard_user_id AS guard, s.site_id, s.status, s.scheduled_start AS at, s.scheduled_end, s.actual_start,
               COALESCE(s.is_late, FALSE) AS is_late, COALESCE(s.late_minutes, 0) AS late_minutes
          FROM shifts s WHERE s.scheduled_start >= :start AND s.scheduled_start < :end""", """
        count(*) AS shifts,
        count(x.actual_start) AS worked,
        count(*) FILTER (WHERE x.is_late) AS late,
        COALESCE(sum(x.late_minutes) FILTER (WHERE x.is_late), 0) AS late_minutes,
        count(*) FILTER (WHERE x.actual_start IS NULL AND x.status = 'scheduled' AND x.scheduled_end <= :now) AS not_started"""),
    "TOURS": ("""
        SELECT t.assigned_guard_user_id AS guard, r.site_id, o.status FROM tour_occurrences o
          JOIN tour_schedules t ON t.id = o.schedule_id LEFT JOIN patrol_routes r ON r.id = t.route_id
         WHERE o.scheduled_at >= :start AND o.scheduled_at < :end AND t.assigned_guard_user_id IS NOT NULL""", """
        count(*) FILTER (WHERE x.status = 'completed') AS tours_done,
        count(*) FILTER (WHERE x.status = 'missed') AS tours_missed"""),
    "WALKED": ("""
        SELECT p.guard_user_id AS guard, r.site_id, p.total_checkpoints, p.scanned_checkpoints FROM patrol_sessions p
          LEFT JOIN patrol_routes r ON r.id = p.route_id
         WHERE COALESCE(p.started_at, p.created_at) >= :start AND COALESCE(p.started_at, p.created_at) < :end""", """
        count(*) AS walked,
        COALESCE(sum(x.scanned_checkpoints), 0) AS checkpoints_scanned,
        COALESCE(sum(x.total_checkpoints), 0) AS checkpoints_total"""),
    "RESPONSES": ("""
        SELECT r.guard_user_id AS guard, r.site_id, r.state, r.dispatched_at AS at, r.accepted_at, r.arrived_at
          FROM incident_responses r
         WHERE r.dispatched_at >= :start AND r.dispatched_at < :end AND r.guard_user_id IS NOT NULL""", """
        count(*) AS sent,
        count(x.accepted_at) AS accepted,
        count(*) FILTER (WHERE x.state = 'DECLINED') AS declined,
        count(x.arrived_at) AS arrived,
        percentile_cont(0.5) WITHIN GROUP (
            ORDER BY CAST(EXTRACT(EPOCH FROM x.arrived_at - x.at) AS double precision)) AS arrive_seconds"""),
    "VIOLATIONS": ("""
        SELECT v.guard_user_id AS guard, v.site_id, v.violation_type, v.status FROM violations v
         WHERE v.occurred_at >= :start AND v.occurred_at < :end""", """
        count(*) AS recorded,
        count(*) FILTER (WHERE x.status = 'waived') AS waived,
        count(*) FILTER (WHERE x.status = 'disputed') AS disputed,
        """ + ",\n        ".join(
            f"count(*) FILTER (WHERE x.status <> 'waived' AND x.violation_type = '{kind}') AS {kind}"
            for kind in VIOLATION_TYPES)),
    "HANDOVERS": ("""
        SELECT h.outgoing_guard_id AS guard, s.site_id, h.status, h.dispute_reason FROM shift_handovers h
          JOIN shifts s ON s.id = h.shift_id
         WHERE h.created_at >= :start AND h.created_at < :end AND h.outgoing_guard_id IS NOT NULL""", """
        count(*) AS given,
        count(*) FILTER (WHERE x.status IN ('accepted', 'resolved') AND x.dispute_reason IS NULL) AS accepted,
        count(*) FILTER (WHERE x.status = 'disputed' OR x.dispute_reason IS NOT NULL) AS disputed"""),
}

#: What is a person's: one row for each of the people asked for.
_PERSONAL = {
    "completed": """
        SELECT t.user_id AS guard, count(*) AS n FROM training_records t
         WHERE t.user_id = ANY(CAST(:guards AS uuid[])) AND t.passed
           AND t.completed_at >= CAST(:from_day AS date) AND t.completed_at < CAST(:to_day AS date) + 1
         GROUP BY t.user_id""",
    "courses": """
        SELECT l.user_id AS guard,
               count(*) FILTER (WHERE l.expires_at < CAST(:today AS date)) AS lapsed,
               count(*) FILTER (WHERE l.expires_at >= CAST(:today AS date) AND l.expires_at < CAST(:soon AS date)) AS lapsing
          FROM (SELECT DISTINCT ON (t.user_id, t.course_id) t.user_id, t.expires_at FROM training_records t
                  JOIN training_courses c ON c.id = t.course_id
                 WHERE t.user_id = ANY(CAST(:guards AS uuid[])) AND t.passed AND c.is_active
                 ORDER BY t.user_id, t.course_id, t.completed_at DESC, t.created_at DESC) l
         GROUP BY l.user_id""",
    "certificates": """
        SELECT c.user_id AS guard,
               count(*) FILTER (WHERE c.expires_at < CAST(:today AS date)) AS lapsed,
               count(*) FILTER (WHERE c.expires_at >= CAST(:today AS date) AND c.expires_at < CAST(:soon AS date)) AS lapsing
          FROM guard_certifications c WHERE c.user_id = ANY(CAST(:guards AS uuid[])) AND c.is_valid
         GROUP BY c.user_id""",
    "at_risk": """
        SELECT f.guard_user_id AS guard, count(DISTINCT f.shift_id) AS n FROM shift_certification_findings f
         WHERE f.guard_user_id = ANY(CAST(:guards AS uuid[])) AND f.resolved_at IS NULL
           AND f.shift_date >= CAST(:today AS date) AND f.status <> 'EXPIRING'
         GROUP BY f.guard_user_id""",
}


def blank(section: str) -> dict:
    """A section with nothing counted in it: every count 0 and every time None."""
    if section == "SHIFTS":
        return {"shifts": 0, "worked": 0, "late": 0, "late_minutes": 0, "not_started": 0}
    if section == "PATROLS":
        return {"tours_done": 0, "tours_missed": 0, "walked": 0, "checkpoints_scanned": 0, "checkpoints_total": 0}
    if section == "RESPONSES":
        return {"sent": 0, "accepted": 0, "declined": 0, "arrived": 0, "arrive_seconds": None}
    if section == "VIOLATIONS":
        return {"recorded": 0, "waived": 0, "disputed": 0, "by_type": dict.fromkeys(VIOLATION_TYPES, 0)}
    if section == "TRAINING":
        return {"completed": 0, "courses_lapsed_now": 0, "courses_lapsing_now": 0, "certificates_lapsed_now": 0,
                "certificates_lapsing_now": 0, "shifts_at_risk_now": 0}
    return {"given": 0, "accepted": 0, "disputed": 0}


def not_read(held: frozenset[str] | set[str]) -> list[dict]:
    """Every section the caller may not read, with the permission it wants."""
    return [{"key": s, "title": TITLE[s], "needs": NEEDS[s], "reason": f"Read under {NEEDS[s]}, which you do not hold."}
            for s in SECTIONS if NEEDS[s] not in held]


async def _counted(db: AsyncSession, part: str, params: Mapping, where: str) -> list[tuple[str, Any, dict]]:
    """One part, counted three ways: for each guard, for each site, and in all.
    Each as (which way, whose, the figures)."""
    rows, counts = _PARTS[part]
    wanted = {k: v for k, v in params.items() if f":{k}" in rows + counts + where}
    result = await db.execute(text(f"""
        SELECT x.guard, x.site_id, GROUPING(x.guard) AS no_guard, GROUPING(x.site_id) AS no_site, {counts}
          FROM ({rows}) x WHERE {where}
         GROUP BY GROUPING SETS ((x.guard), (x.site_id), ())
    """), wanted)
    out = []
    for r in result.mappings():
        figures = {k: (float(v) if k.endswith("_seconds") and v is not None else int(v) if v is not None else None)
                   for k, v in r.items() if k not in ("guard", "site_id", "no_guard", "no_site")}
        if r["no_guard"] and r["no_site"]:
            out.append(("total", None, figures))
        elif r["no_site"]:
            if r["guard"] is not None:
                out.append(("guard", str(r["guard"]), figures))
        else:
            out.append(("site", str(r["site_id"]) if r["site_id"] else None, figures))
    return out


async def read(db: AsyncSession, held: frozenset[str] | set[str], site_ids: Sequence[Any] | None,
               start: datetime, end: datetime, now: datetime, *, guard: Any = None) -> dict:
    """Every section the caller may read: `guards` for each guard with
    something recorded, `sites` for each site, `total` for all of it.
    `site_ids` None is every site and what has no site; a list is those sites
    only. `guard` keeps it to one person's records."""
    params: dict = {"start": start, "end": end, "now": now}
    where = ["TRUE"]
    if site_ids is not None:
        where.append("x.site_id = ANY(CAST(:sites AS uuid[]))")
        params["sites"] = [str(s) for s in site_ids]
    if guard is not None:
        where.append("x.guard = CAST(:guard AS uuid)")
        params["guard"] = str(guard)
    scope = " AND ".join(where)
    sections = [s for s in SECTIONS if NEEDS[s] in held]
    at_site = [s for s in sections if s not in PERSONAL]
    guards: dict[str, dict] = {}
    sites: dict[Any, dict] = {}
    total = {s: blank(s) for s in at_site}

    def into(way: str, whose: Any) -> dict:
        if way == "total":
            return total
        return (guards if way == "guard" else sites).setdefault(whose, {s: blank(s) for s in at_site})

    for part, section in (("SHIFTS", "SHIFTS"), ("TOURS", "PATROLS"), ("WALKED", "PATROLS"), ("RESPONSES", "RESPONSES"),
                          ("VIOLATIONS", "VIOLATIONS"), ("HANDOVERS", "HANDOVERS")):
        if section not in sections:
            continue
        for way, whose, figures in await _counted(db, part, params, scope):
            if section == "VIOLATIONS":
                figures = {**{k: v for k, v in figures.items() if k not in VIOLATION_TYPES},
                           "by_type": {k: figures[k] for k in VIOLATION_TYPES}}
            into(way, whose)[section].update(figures)
    return {"guards": guards, "sites": sites, "total": total, "not_read": not_read(held)}


async def personal(db: AsyncSession, guard_ids: Sequence[Any], start: datetime, end: datetime,
                   today: date) -> dict[str, dict]:
    """What is a person's and not a site's, for each of these people: courses
    passed in the period, and courses and certificates as they stand today."""
    out = {str(g): blank("TRAINING") for g in guard_ids}
    if not out:
        return out
    params = {"guards": list(out), "from_day": start.date(), "to_day": end.date(), "today": today,
              "soon": today + timedelta(days=SOON_DAYS)}
    for name, sql in _PERSONAL.items():
        wanted = {k: v for k, v in params.items() if f":{k}" in sql}
        for r in (await db.execute(text(sql), wanted)).mappings():
            figures = out[str(r["guard"])]
            if name == "completed":
                figures["completed"] = r["n"]
            elif name == "at_risk":
                figures["shifts_at_risk_now"] = r["n"]
            else:
                figures[f"{name}_lapsed_now"], figures[f"{name}_lapsing_now"] = r["lapsed"], r["lapsing"]
    return out


async def people(db: AsyncSession, counted: Sequence[str], site_ids: Sequence[Any] | None) -> list[dict]:
    """Who a reading is of, in order of name: everybody with something
    recorded, and every guard in use who is posted to the sites shown — one
    with nothing recorded is still a guard."""
    params: dict = {"ids": list(counted)}
    posted = "TRUE"
    if site_ids is not None:
        params["sites"] = [str(s) for s in site_ids]
        posted = ("(u.primary_site_id = ANY(CAST(:sites AS uuid[])) OR EXISTS (SELECT 1 FROM user_sites us "
                  "WHERE us.user_id = u.id AND us.site_id = ANY(CAST(:sites AS uuid[]))))")
    rows = await db.execute(text(f"""
        SELECT u.id, u.full_name, u.role_id, u.is_active FROM users u
         WHERE u.id = ANY(CAST(:ids AS uuid[])) OR (u.role_id = 5 AND u.is_active AND {posted})
         ORDER BY u.full_name, u.id
    """), params)
    return [dict(r) for r in rows.mappings()]
