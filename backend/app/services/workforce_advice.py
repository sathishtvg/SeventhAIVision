"""Recommendations for a manager, made of what is recorded: training for a guard, cover for a site.

  training()        what the records say a guard may need: a certificate, a course again, a route shown
  coverage()        what the records say of a site's cover: shifts not started, busy hours thinly rostered,
                    guards slower to arrive at night
  cover_by_hour()   guard-hours rostered in each hour of the day. Pure.
  the pure rules    certification(), course(), missed_tours(), unstarted(), hours_cover(), slow_at_night()

A RECOMMENDATION IS NEVER AN EMPLOYMENT DECISION AND NEVER A CHANGE TO A ROSTER.
Each is a statement of what is recorded, with the counts in it, and one thing a
manager might consider. Nothing here assigns a course, moves a shift, warns a
guard or records anything against anybody.

FIXED RULES OVER COUNTS, COUNTED WHEN ASKED. No model, nothing learned, nothing
stored (owner decision E2). The same records give the same recommendations.

TRAINING IS RECOMMENDED FROM WHAT A GUARD WAS NOT GIVEN, NOT FROM WHAT THEY DID
WRONG. A certificate a rostered shift needs; a course whose pass has lapsed;
tours missed often enough that the route or the time for it is worth looking
at. Lateness and violations are not turned into recommendations: they have
their own review, by a person, and are counted in the reading with what was
waived and what is disputed.

COVERAGE COMPARES A SITE WITH ITSELF. Busy hours against the hours rostered in
them; nights against days. No site is compared with another.

NO COURSE IS INVENTED. A recommendation names the courses the library holds
under a category, or says it holds none.

Read-only: nothing here writes to the database.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import risk_patterns
from app.services.daily_briefing import lasting
from app.services.workforce_readings import SOON_DAYS

KINDS = ("TRAINING", "COVERAGE")
CODES = {"TRAINING": ("CERTIFICATION", "COURSE_LAPSED", "COURSE_LAPSING", "MISSED_TOURS"),
         "COVERAGE": ("UNSTARTED_SHIFTS", "HOURS_COVER", "SLOW_AT_NIGHT")}
#: Recommendations look at the last four weeks, whatever period a reading is asked for.
WEEKS = 4
#: Tours missed, and shifts not started, before either is worth a manager's look.
MISSED_AT = 3
UNSTARTED_AT = 3
#: Incidents a site needs before its busy hours are compared with its roster.
BUSY_FLOOR = 10
#: Night, for how long guards take to arrive: from this hour to that, where the site is.
NIGHT_FROM, NIGHT_TO = 20, 6
#: Sendings each of night and day needs, and how much slower night must be, to be said.
SLOW_FLOOR, SLOW_TIMES = 5, 1.5
#: The category a course on walking a route is filed under. The library has no category of its own for patrols.
TOUR_CATEGORY = "security"
NOTE = ("Recommendations for a manager, made of what is recorded over the last 4 weeks. Each is advice: accepting "
        "one assigns no course and changes no roster, and none is a decision about anybody's employment.")
STATUS_WORDS = {"MISSING": "and holds none on file", "EXPIRED": "and the one on file will have lapsed by then",
                "REVOKED": "and the one on file is marked not valid",
                "EXPIRING": "and the one on file is close to lapsing"}
ROSTER = "The roster is changed in the roster, by a person."


def _rec(kind: str, code: str, key: str, statement: str, consider: str, *, guard: Mapping | None = None,
         site: Mapping | None = None, **rests_on) -> dict:
    return {"key": key, "kind": kind, "code": code, "statement": statement, "consider": consider,
            "subject": {"user_id": guard["id"], "name": guard["full_name"]} if guard else None,
            "site": {"id": site["id"], "name": site["name"]} if site else None,
            "rests_on": rests_on, "is_advisory": True, "is_decision": False}


def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ─── Pure: training ──────────────────────────────────────────────────────────

def certification(guard: Mapping, kind: str, status: str, shifts: int, first: date) -> dict:
    """A certificate a rostered shift needs and the guard does not hold, or will not."""
    return _rec("TRAINING", "CERTIFICATION", f"CERTIFICATION:{guard['id']}:{kind}:{status}",
                f"{guard['full_name']} is rostered for {_n(shifts, 'shift', 'shifts')} from {_day(first)} that "
                f"{'needs' if shifts == 1 else 'need'} {kind}, {STATUS_WORDS[status]}.",
                f"Arranging the certificate before then, or rostering somebody who holds it. {ROSTER}",
                guard=guard, certification=kind, status=status, shifts=shifts, first_shift=first.isoformat())


def course(guard: Mapping, course_id: Any, name: str, expires: date, today: date) -> dict | None:
    """A course whose pass has lapsed, or lapses within thirty days."""
    if expires < today:
        return _rec("TRAINING", "COURSE_LAPSED", f"COURSE:{guard['id']}:{course_id}",
                    f"{guard['full_name']}'s pass in {name} lapsed on {_day(expires)}.",
                    "Assigning the course again. It is assigned in Training.",
                    guard=guard, course=name, lapsed_on=expires.isoformat())
    if expires < today + timedelta(days=SOON_DAYS):
        return _rec("TRAINING", "COURSE_LAPSING", f"COURSE:{guard['id']}:{course_id}",
                    f"{guard['full_name']}'s pass in {name} lapses on {_day(expires)}.",
                    "Assigning the course again before then. It is assigned in Training.",
                    guard=guard, course=name, lapses_on=expires.isoformat())
    return None


def missed_tours(guard: Mapping, missed: int, done: int, courses: Sequence[str]) -> dict | None:
    """Tours missed often enough that the route, or the time given for it, is worth a look."""
    if missed < MISSED_AT:
        return None
    filed = (f"Courses filed under {TOUR_CATEGORY}: {', '.join(courses)}." if courses
             else f"No course is filed under {TOUR_CATEGORY} in the library.")
    return _rec("TRAINING", "MISSED_TOURS", f"MISSED_TOURS:{guard['id']}",
                f"{guard['full_name']} missed {missed} of the {missed + done} tours assigned to them that fell due "
                f"in the last {WEEKS} weeks.",
                f"Whether the route can be walked in the time given, and whether they have been shown it. {filed}",
                guard=guard, missed=missed, due=missed + done, weeks=WEEKS, courses=list(courses))


# ─── Pure: coverage ──────────────────────────────────────────────────────────

def unstarted(site: Mapping, not_started: int, shifts: int) -> dict | None:
    if not_started < UNSTARTED_AT:
        return None
    return _rec("COVERAGE", "UNSTARTED_SHIFTS", f"UNSTARTED_SHIFTS:{site['id']}",
                f"{not_started} of the {shifts} shifts due at {site['name']} in the last {WEEKS} weeks were not started.",
                f"Who covers when a shift is not started, and whether there is anybody to call on. {ROSTER}",
                site=site, not_started=not_started, shifts=shifts, weeks=WEEKS)


def cover_by_hour(shifts: Sequence[Mapping], zone: str, since: datetime, now: datetime) -> list[float]:
    """Guard-hours rostered in each hour of the day, where the site is, over
    the period: each shift's scheduled time, cut to the period, shared out
    over the hours it covers."""
    tz = ZoneInfo(zone)
    hours = [0.0] * 24
    for s in shifts:
        at, end = max(s["scheduled_start"], since), min(s["scheduled_end"], now)
        while at < end:
            local = at.astimezone(tz)
            until = min(end, at + timedelta(minutes=60 - local.minute, seconds=-local.second,
                                            microseconds=-local.microsecond))
            hours[local.hour] += (until - at).total_seconds() / 3600
            at = until
    return hours


def hours_cover(site: Mapping, by_hour: Sequence[int], cover: Sequence[float]) -> dict | None:
    """The site's busiest four hours, when they hold half its incidents and are
    rostered more thinly than an average four hours of its day."""
    total = sum(by_hour)
    if total < BUSY_FLOOR or not sum(cover):
        return None
    start, n = risk_patterns.busiest_band(by_hour)
    share = round(100 * n / total)
    if share < risk_patterns.HOURS_SHARE:
        return None
    band = [(start + k) % 24 for k in range(risk_patterns.BAND_HOURS)]
    then, usual = sum(cover[h] for h in band), sum(cover) * risk_patterns.BAND_HOURS / 24
    if then >= usual:
        return None
    until = (start + risk_patterns.BAND_HOURS) % 24
    return _rec("COVERAGE", "HOURS_COVER", f"HOURS_COVER:{site['id']}:{start:02d}",
                f"{share}% of the incidents at {site['name']} in the last {WEEKS} weeks fell between {start:02d}:00 "
                f"and {until:02d}:00 ({n} of {total}). {round(then)} guard-hours were rostered in those hours, "
                f"against {round(usual)} for an average four hours of its day.",
                f"Whether those hours have the people the rest of the day has. {ROSTER}",
                site=site, from_hour=start, to_hour=until, incidents_in_band=n, incidents=total,
                guard_hours_in_band=round(then, 1), guard_hours_usual=round(usual, 1), weeks=WEEKS)


def is_night(hour: int) -> bool:
    return hour >= NIGHT_FROM or hour < NIGHT_TO


def slow_at_night(site: Mapping, night: Sequence[float], day: Sequence[float]) -> dict | None:
    """Guards arriving more slowly at night than in the day, when there are enough of each to say so."""
    if len(night) < SLOW_FLOOR or len(day) < SLOW_FLOOR:
        return None
    by_night, by_day = median(night), median(day)
    if by_night < SLOW_TIMES * by_day:
        return None
    return _rec("COVERAGE", "SLOW_AT_NIGHT", f"SLOW_AT_NIGHT:{site['id']}",
                f"At {site['name']}, half of the guards sent between {NIGHT_FROM:02d}:00 and {NIGHT_TO:02d}:00 in the "
                f"last {WEEKS} weeks arrived within {lasting(by_night)}; in the rest of the day, within "
                f"{lasting(by_day)} ({len(night)} and {len(day)} sendings).",
                f"Where guards are posted at night, and how far that is from where they are sent. {ROSTER}",
                site=site, night_seconds=round(by_night), day_seconds=round(by_day), night_sendings=len(night),
                day_sendings=len(day), weeks=WEEKS)


# ─── Reading ─────────────────────────────────────────────────────────────────

def _scope(site_ids: Sequence[Any] | None, column: str, params: dict) -> str:
    if site_ids is None:
        return "TRUE"
    params["sites"] = [str(s) for s in site_ids]
    return f"{column} = ANY(CAST(:sites AS uuid[]))"


async def training(db: AsyncSession, guards: Sequence[Mapping], site_ids: Sequence[Any] | None,
                   now: datetime, today: date) -> list[dict]:
    """What the records say each of these guards may need, by name of guard."""
    by_id = {str(g["id"]): g for g in guards}
    if not by_id:
        return []
    out: list[dict] = []
    params: dict = {"guards": list(by_id), "today": today}
    scope = _scope(site_ids, "f.site_id", params)
    for r in (await db.execute(text(f"""
        SELECT f.guard_user_id AS guard, f.certification_type, f.status, count(DISTINCT f.shift_id) AS shifts,
               min(f.shift_date) AS first
          FROM shift_certification_findings f
         WHERE f.guard_user_id = ANY(CAST(:guards AS uuid[])) AND f.resolved_at IS NULL
           AND f.shift_date >= CAST(:today AS date) AND {scope}
         GROUP BY f.guard_user_id, f.certification_type, f.status
    """), params)).mappings():
        out.append(certification(by_id[str(r["guard"])], r["certification_type"], r["status"], r["shifts"], r["first"]))
    for r in (await db.execute(text("""
        SELECT DISTINCT ON (t.user_id, t.course_id) t.user_id AS guard, t.course_id, c.name, t.expires_at
          FROM training_records t JOIN training_courses c ON c.id = t.course_id
         WHERE t.user_id = ANY(CAST(:guards AS uuid[])) AND t.passed AND c.is_active
         ORDER BY t.user_id, t.course_id, t.completed_at DESC, t.created_at DESC
    """), {"guards": list(by_id)})).mappings():
        if r["expires_at"] is not None:
            found = course(by_id[str(r["guard"])], r["course_id"], r["name"], r["expires_at"], today)
            if found:
                out.append(found)
    params = {"guards": list(by_id), "since": now - timedelta(weeks=WEEKS), "now": now}
    scope = _scope(site_ids, "r.site_id", params)
    tours = (await db.execute(text(f"""
        SELECT t.assigned_guard_user_id AS guard, count(*) FILTER (WHERE o.status = 'missed') AS missed,
               count(*) FILTER (WHERE o.status = 'completed') AS done
          FROM tour_occurrences o JOIN tour_schedules t ON t.id = o.schedule_id
          LEFT JOIN patrol_routes r ON r.id = t.route_id
         WHERE t.assigned_guard_user_id = ANY(CAST(:guards AS uuid[])) AND o.scheduled_at >= :since
           AND o.scheduled_at < :now AND {scope}
         GROUP BY t.assigned_guard_user_id
    """), params)).mappings().all()
    if any(r["missed"] >= MISSED_AT for r in tours):
        courses = [r.name for r in await db.execute(text(
            "SELECT name FROM training_courses WHERE is_active AND category = :c ORDER BY name"), {"c": TOUR_CATEGORY})]
        for r in tours:
            found = missed_tours(by_id[str(r["guard"])], r["missed"], r["done"], courses)
            if found:
                out.append(found)
    return sorted(out, key=lambda f: (f["subject"]["name"] or "", f["key"]))


async def coverage(db: AsyncSession, sites: Sequence[Mapping], zone_of, now: datetime) -> list[dict]:
    """What the records say of each of these sites' cover. `zone_of` gives a
    site's time zone."""
    out: list[dict] = []
    since = now - timedelta(weeks=WEEKS)
    for site in sites:
        params = {"site": str(site["id"]), "since": since, "now": now}
        shifts = [dict(r) for r in (await db.execute(text("""
            SELECT s.scheduled_start, s.scheduled_end, s.actual_start, s.status FROM shifts s
             WHERE s.site_id = CAST(:site AS uuid) AND s.scheduled_end > :since AND s.scheduled_start < :now
        """), params)).mappings()]
        due = [s for s in shifts if s["scheduled_start"] >= since]
        found = unstarted(site, sum(1 for s in due if s["actual_start"] is None and s["status"] == "scheduled"
                                    and s["scheduled_end"] <= now), len(due))
        if found:
            out.append(found)
        zone = await zone_of(site["id"])
        incidents, _ = await risk_patterns.events(db, "INCIDENT", since, now, [site["id"]])
        counted = risk_patterns.summarise(incidents, zone, since, WEEKS)
        by_hour = [sum(day[h] for day in counted["grid"]) for h in range(24)]
        found = hours_cover(site, by_hour, cover_by_hour(shifts, zone, since, now))
        if found:
            out.append(found)
        sent = (await db.execute(text("""
            SELECT r.dispatched_at, EXTRACT(EPOCH FROM r.arrived_at - r.dispatched_at) AS seconds
              FROM incident_responses r
             WHERE r.site_id = CAST(:site AS uuid) AND r.dispatched_at >= :since AND r.dispatched_at < :now
               AND r.arrived_at IS NOT NULL
        """), params)).mappings().all()
        tz = ZoneInfo(zone)
        night = [float(r["seconds"]) for r in sent if is_night(r["dispatched_at"].astimezone(tz).hour)]
        day = [float(r["seconds"]) for r in sent if not is_night(r["dispatched_at"].astimezone(tz).hour)]
        found = slow_at_night(site, night, day)
        if found:
            out.append(found)
    return out
