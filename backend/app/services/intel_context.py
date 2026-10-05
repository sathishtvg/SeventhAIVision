"""The context engine: what was expected at this place, at that moment.

An event says "a person, at Gate 1, 0.87". This says what that means here: is
the site open, is the zone in force, is anybody meant to be on site, was a door
refused nearby a minute ago, is a patrol under way, when did an officer on a
virtual patrol last look at this camera and what did they report, and what has
this camera reported before. It does not decide how much any of it matters — that is risk,
later, and it reads this.

TWO HALVES, AND ONLY ONE TOUCHES THE DATABASE. `load()` reads the facts that
were true at the event's time; `build()` turns facts into a context with no
database at all. Every rule about what the facts mean is therefore a test that
needs nothing running, and the same facts always give the same context.

EVERY ENTRY SAYS WHERE IT CAME FROM. The context is a list of statements, each
with the table or setting behind it, so the explanation an officer reads later
is made only of things the platform actually recorded.

UNKNOWN IS AN ANSWER. Where the platform has nothing — no hours set for the
site, nobody identified, too little history — the context says so in `unknowns`
and claims nothing. A site whose hours have never been entered is never said to
be "after hours". A face that matched no watchlist is "not identified", which is
not "unauthorised".

AS OF THE EVENT, NOT AS OF NOW. Guards on shift, visitors on site, a permit in
force, a patrol under way: all asked about the moment the event happened. Read
an hour later, the context of a 02:17 event is still the context of 02:17.

READ-ONLY. Nothing here writes anywhere.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
CRITICALITIES = ("low", "medium", "high", "critical")
DEFAULT_TZ = "Asia/Singapore"

#: How far either side of an event a door or an alarm counts as "nearby in time".
WINDOW = timedelta(minutes=10)
#: How far back a camera's record is read.
HISTORY_DAYS = 30
#: Fewer decided alerts than this and a false-positive share is not stated: two
#: dismissals out of three is not a pattern.
HISTORY_FLOOR = 5
#: A patrol or a flight that was started and never closed is not still under way
#: a week later. Past this long it is no longer counted as in progress.
STALE_AFTER = timedelta(hours=6)
#: How far back the last virtual patrol check of a camera is still worth saying.
PATROL_LOOKBACK = timedelta(hours=24)

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# ─── Business hours ──────────────────────────────────────────────────────────

def validate_business_hours(value: Any) -> dict | None:
    """The stored shape, or ValueError saying what is wrong. None means "not
    defined", which is allowed and is not the same as closed."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("business_hours must be an object keyed by weekday, or null")
    out: dict = {}
    for day, periods in value.items():
        if day not in WEEKDAYS:
            raise ValueError(f"'{day}' is not a weekday; use {', '.join(WEEKDAYS)}")
        if not isinstance(periods, list) or len(periods) > 6:
            raise ValueError(f"{day}: a list of up to six [start, end] periods")
        clean = []
        for p in periods:
            if (not isinstance(p, (list, tuple)) or len(p) != 2
                    or not all(isinstance(x, str) and _HHMM.match(x) for x in p)):
                raise ValueError(f"{day}: each period is [\"HH:MM\", \"HH:MM\"]")
            if p[0] == p[1]:
                raise ValueError(f"{day}: a period cannot start and end at the same time")
            clean.append([p[0], p[1]])
        out[day] = clean
    return out


def _t(hhmm: str) -> time:
    return time(int(hhmm[:2]), int(hhmm[3:]))


def hours_state(local: datetime, business_hours: dict | None, holiday: str | None,
                closed_on_holidays: bool) -> tuple[str, str]:
    """(inside | outside | not_defined, why). A period that ends before it
    starts runs past midnight and belongs to the day it starts on."""
    if business_hours is None:
        return "not_defined", "business hours are not defined for this site"
    if holiday and closed_on_holidays:
        return "outside", f"public holiday ({holiday})"
    now, day = local.time(), WEEKDAYS[local.weekday()]
    for start, end in business_hours.get(day, []):
        s, e = _t(start), _t(end)
        if (s <= now < e) if s < e else (now >= s):
            return "inside", f"open {start}–{end} on {day.capitalize()}"
    yesterday = WEEKDAYS[(local.weekday() - 1) % 7]
    for start, end in business_hours.get(yesterday, []):
        s, e = _t(start), _t(end)
        if s > e and now < e:
            return "inside", f"open {start}–{end} from {yesterday.capitalize()}"
    todays = ", ".join(f"{a}–{b}" for a, b in business_hours.get(day, [])) or "closed"
    return "outside", f"{day.capitalize()} hours: {todays}"


# ─── Zones ───────────────────────────────────────────────────────────────────

def zone_in_force(zone: Mapping, at: datetime) -> tuple[bool, str]:
    """Whether a camera's restricted zone applied at `at`, and why — the same
    rule the zones API and the intrusion worker use, asked about a moment
    rather than about now."""
    if not zone.get("is_active", True):
        return False, "the zone is switched off"
    bypass = zone.get("bypass_until")
    if bypass is not None and bypass > at:
        return False, "the zone was bypassed at the time"
    if not zone.get("schedule_enabled"):
        return True, "the zone is always in force"
    local = at.astimezone(_zone(zone.get("schedule_timezone")))
    start, end = zone.get("active_start_time"), zone.get("active_end_time")
    days = zone.get("active_days") or []
    if local.weekday() in days and start is not None and end is not None and start <= local.time() <= end:
        return True, f"scheduled {start:%H:%M}–{end:%H:%M}"
    return False, "outside the zone's schedule"


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:  # noqa: BLE001 — an unknown zone name must not stop an assessment
        return ZoneInfo(DEFAULT_TZ)


# ─── The facts, and the context made from them ───────────────────────────────

@dataclass
class Facts:
    """What the platform had recorded about a place at a moment. `None` means
    "could not be asked" (no site, no camera); an empty list or zero means
    "asked, and there was nothing"."""

    timezone: str = DEFAULT_TZ
    site: dict | None = None
    profile: dict | None = None
    camera: dict | None = None
    camera_profile: dict | None = None
    holiday: str | None = None
    zones: list[dict] = field(default_factory=list)
    guards_on_shift: int | None = None
    visitors_on_site: int | None = None
    permits: list[dict] = field(default_factory=list)
    access: dict | None = None
    alarm_events: int | None = None
    virtual_patrol_in_progress: bool | None = None
    drone_in_flight: bool | None = None
    #: The last time a virtual patrol reached this camera before the event:
    #: {at, patrol_number, status, answered, exceptions, noted, snapshot}.
    last_patrol_check: dict | None = None
    history: dict | None = None


def _before(delta: timedelta) -> str:
    minutes = max(0, round(delta.total_seconds() / 60))
    if minutes < 90:
        return f"{minutes} min before"
    return f"{round(minutes / 60)} h before"


def _attrs(event: Mapping) -> dict:
    a = event.get("attributes")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            a = {}
    return a if isinstance(a, dict) else {}


def _say(out: dict, kind: str, text_: str, source: str) -> None:
    out["statements"].append({"kind": kind, "text": text_, "source": source})


def build(event: Mapping, facts: Facts) -> dict:
    """The context of one event. Pure: the same event and facts, the same answer."""
    out: dict = {"statements": [], "unknowns": [], "expected": []}
    attrs = _attrs(event)
    at: datetime = event["occurred_at"]
    profile, cam_profile = facts.profile or {}, facts.camera_profile or {}

    # ── Place ────────────────────────────────────────────────────────────────
    criticality = cam_profile.get("criticality") or profile.get("criticality")
    criticality_source = ("camera profile" if cam_profile.get("criticality")
                          else "site profile" if profile.get("criticality") else None)
    zones = []
    for z in facts.zones:
        in_force, why = zone_in_force(z, at)
        zones.append({"id": str(z["id"]), "name": z["name"], "severity": z["severity"], "in_force": in_force,
                      "why": why, "event_zone": str(z["id"]) == str(attrs.get("zone_id") or "")})
    out["place"] = {
        "site_id": str(facts.site["id"]) if facts.site else None,
        "site_name": facts.site["name"] if facts.site else None,
        "camera_id": str(facts.camera["id"]) if facts.camera else None,
        "camera_name": facts.camera["name"] if facts.camera else None,
        "area": cam_profile.get("area_label"),
        "criticality": criticality,
        "restricted_area": bool(cam_profile.get("is_restricted_area")),
        "zones": zones,
    }
    if facts.site is None:
        out["unknowns"].append("The event has no site, so nothing about a site can be said.")
    elif criticality is None:
        out["unknowns"].append("No criticality has been set for this site or camera.")
    else:
        _say(out, "place", f"Criticality: {criticality}", criticality_source)
    if cam_profile.get("is_restricted_area"):
        _say(out, "place", f"Restricted area{': ' + cam_profile['area_label'] if cam_profile.get('area_label') else ''}",
             "camera profile")
    for z in zones:
        if z["event_zone"] or z["in_force"]:
            state = "in force" if z["in_force"] else "not in force"
            _say(out, "place", f"Restricted zone “{z['name']}” ({z['severity']}) was {state}: {z['why']}",
                 "restricted_zones")
    if attrs.get("zone_type"):
        _say(out, "place", f"Drone security zone: {event.get('location_label') or 'unnamed'} "
                           f"({str(attrs['zone_type']).lower().replace('_', ' ')})", "drone_events")

    # ── Time ─────────────────────────────────────────────────────────────────
    tz_name = profile.get("timezone") or facts.timezone or DEFAULT_TZ
    local = at.astimezone(_zone(tz_name))
    state, why = hours_state(local, profile.get("business_hours") if facts.profile else None, facts.holiday,
                             profile.get("closed_on_public_holidays", True))
    out["time"] = {
        "local": local.isoformat(),
        "timezone": tz_name,
        "weekday": WEEKDAYS[local.weekday()],
        "business_hours": state,
        "after_hours": None if state == "not_defined" else state == "outside",
        "holiday": facts.holiday,
    }
    if state == "not_defined":
        if facts.site is not None:
            out["unknowns"].append("Business hours are not defined for this site, so “after hours” cannot be said.")
    elif state == "outside":
        _say(out, "time", f"Outside business hours — {why}", "site profile")
    else:
        _say(out, "time", f"Within business hours — {why}", "site profile")
        out["expected"].append("the site was open")
    if facts.holiday:
        _say(out, "time", f"Public holiday: {facts.holiday}", "public_holidays")

    # ── People ───────────────────────────────────────────────────────────────
    verdict = event.get("subject_verdict")
    kind = event.get("subject_kind") or "NONE"
    noun = {"PERSON": "person", "VEHICLE": "vehicle"}.get(kind)
    out["people"] = {
        "subject_kind": kind,
        "subject_verdict": verdict,
        "guards_on_shift": facts.guards_on_shift,
        "visitors_on_site": facts.visitors_on_site,
        "contractor_permits_in_force": len(facts.permits) if facts.site else None,
    }
    if noun and verdict == "ALLOW":
        _say(out, "people", f"The {noun} is on a watchlist as allowed", "watchlist")
        out["expected"].append(f"the {noun} is on an allow list")
    elif noun and verdict == "BLOCK":
        _say(out, "people", f"The {noun} is on a block list", "watchlist")
    elif noun and verdict == "UNKNOWN":
        _say(out, "people", f"The {noun} was not identified", "watchlist")
        out["unknowns"].append(f"Who the {noun} is. Not identified is not the same as not authorised.")
    if facts.guards_on_shift:
        _say(out, "people", f"{facts.guards_on_shift} guard(s) were on shift at the site", "shifts")
    elif facts.guards_on_shift == 0:
        _say(out, "people", "No guard was on shift at the site", "shifts")
    if facts.visitors_on_site:
        _say(out, "people", f"{facts.visitors_on_site} visitor(s) were signed in", "visitors")
        out["expected"].append("visitors were signed in")
    for p in facts.permits:
        what = p.get("work_type") or "work"
        _say(out, "people", f"A contractor permit was in force ({what}, {p.get('workers_count') or 0} worker(s))",
             "work_permits")
    if facts.permits:
        out["expected"].append("a contractor permit was in force")

    # ── Access and alarms ────────────────────────────────────────────────────
    out["access"] = None
    if facts.access is not None:
        a = facts.access
        out["access"] = {"window_minutes": int(WINDOW.total_seconds() // 60), **a}
        if a.get("denied"):
            reason = f" ({a['last_denial_reason']})" if a.get("last_denial_reason") else ""
            _say(out, "access", f"Access was denied {a['denied']} time(s) at this site within "
                                f"{out['access']['window_minutes']} minutes{reason}", "access_events")
        if a.get("forced"):
            _say(out, "access", f"A door was forced {a['forced']} time(s) within "
                                f"{out['access']['window_minutes']} minutes", "access_events")
        if a.get("granted"):
            _say(out, "access", f"Access was granted {a['granted']} time(s) at this site within "
                                f"{out['access']['window_minutes']} minutes", "access_events")
            out["expected"].append("someone was let in nearby")
    if facts.alarm_events:
        _say(out, "access", f"{facts.alarm_events} alarm event(s) on a zone linked to this camera within "
                            f"{int(WINDOW.total_seconds() // 60)} minutes", "alarm_events")

    # ── Operations ───────────────────────────────────────────────────────────
    out["operations"] = {
        "virtual_patrol_in_progress": facts.virtual_patrol_in_progress,
        "drone_in_flight": facts.drone_in_flight,
    }
    if facts.virtual_patrol_in_progress:
        _say(out, "operations", "A virtual patrol of the site was in progress", "virtual_patrol_sessions")
    if facts.drone_in_flight:
        _say(out, "operations", "A drone patrol was in the air at the site", "drone_patrol_sessions")
    # The last time an officer on a virtual patrol looked at this camera. Said
    # as what was recorded: a check that reported nothing is not a claim that
    # nothing has happened since.
    out["operations"]["last_patrol_check"] = None
    if facts.last_patrol_check is not None:
        p = facts.last_patrol_check
        out["operations"]["last_patrol_check"] = {
            "at": p["at"].isoformat(), "patrol_number": p.get("patrol_number"), "status": p.get("status"),
            "answered": int(p.get("answered") or 0), "exceptions": int(p.get("exceptions") or 0),
            "noted": bool(p.get("noted")), "snapshot": bool(p.get("snapshot"))}
        when = _before(at - p["at"])
        patrol = f" ({p['patrol_number']})" if p.get("patrol_number") else ""
        if p.get("status") == "CAMERA_UNAVAILABLE":
            _say(out, "operations", f"A virtual patrol could not see this camera {when}{patrol}: the camera was "
                                    "unavailable", "virtual_patrol_session_cameras")
        elif p.get("exceptions"):
            _say(out, "operations", f"A virtual patrol checked this camera {when}{patrol} and reported "
                                    f"{p['exceptions']} exception(s)", "virtual_patrol_session_answers")
        else:
            _say(out, "operations", f"A virtual patrol checked this camera {when}{patrol}: "
                                    f"{int(p.get('answered') or 0)} question(s) answered, nothing reported",
                 "virtual_patrol_session_answers")

    # ── History ──────────────────────────────────────────────────────────────
    out["history"] = None
    if facts.history is not None:
        h = facts.history
        decided, fp = h.get("decided", 0), h.get("false_positive", 0)
        share = round(fp / decided, 2) if decided >= HISTORY_FLOOR else None
        out["history"] = {"days": HISTORY_DAYS, "same_kind_at_camera": h.get("same_kind", 0), "decided": decided,
                          "false_positive": fp, "false_positive_share": share,
                          "incidents_at_camera": h.get("incidents", 0)}
        if h.get("same_kind"):
            _say(out, "history", f"{h['same_kind']} earlier alert(s) of this kind from this camera in "
                                 f"{HISTORY_DAYS} days", "alerts")
        if share is not None:
            _say(out, "history", f"{fp} of {decided} decided alerts of this kind from this camera were marked "
                                 f"false ({round(share * 100)}%)", "alerts")
        elif facts.camera is not None:
            out["unknowns"].append("How reliable this camera is for this kind of alert: too few have been decided.")
        if h.get("incidents"):
            _say(out, "history", f"{h['incidents']} incident(s) at this camera in {HISTORY_DAYS} days", "incidents")
    return out


# ─── Loading the facts ───────────────────────────────────────────────────────

async def _one(db: AsyncSession, sql: str, params: dict) -> dict | None:
    row = (await db.execute(text(sql), params)).mappings().first()
    return dict(row) if row is not None else None


async def _scalar(db: AsyncSession, sql: str, params: dict):
    return (await db.execute(text(sql), params)).scalar()


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def load(db: AsyncSession, event: Mapping) -> Facts:
    """Read what was true at the event's place and time. The session must
    already be scoped to the event's tenant."""
    at: datetime = event["occurred_at"]
    site_id, camera_id = event.get("site_id"), event.get("camera_id")
    f = Facts()
    f.timezone = await _scalar(
        db, "SELECT timezone FROM tenants WHERE id = current_setting('app.current_tenant')::uuid", {}) or DEFAULT_TZ

    if camera_id is not None:
        f.camera = await _one(db, "SELECT id, name, location, site_id FROM cameras WHERE id = :c", {"c": camera_id})
        f.camera_profile = await _one(
            db, "SELECT area_label, criticality, is_restricted_area FROM security_camera_profiles "
                "WHERE camera_id = :c", {"c": camera_id})
        f.zones = [dict(r) for r in (await db.execute(text(
            "SELECT id, name, severity, is_active, bypass_until, schedule_enabled, schedule_timezone, "
            "       active_days, active_start_time, active_end_time "
            "  FROM restricted_zones WHERE camera_id = :c ORDER BY name"), {"c": camera_id})).mappings().all()]
        f.alarm_events = await _scalar(db, """
            SELECT count(*) FROM alarm_events ae JOIN alarm_zones az ON az.id = ae.zone_id
             WHERE az.linked_camera_id = :c AND ae.occurred_at BETWEEN :a AND :b
        """, {"c": camera_id, "a": at - WINDOW, "b": at + WINDOW})
        f.last_patrol_check = await _one(db, """
            SELECT COALESCE(sc.completed_at, sc.started_at) AS at, sc.status, vs.patrol_number,
                   (sc.snapshot_path IS NOT NULL) AS snapshot,
                   (btrim(COALESCE(sc.officer_notes, '')) <> '') AS noted,
                   (SELECT count(*) FROM virtual_patrol_session_answers a
                      JOIN virtual_patrol_session_questions q ON q.id = a.session_question_id
                     WHERE q.session_camera_id = sc.id) AS answered,
                   (SELECT count(*) FROM virtual_patrol_session_answers a
                      JOIN virtual_patrol_session_questions q ON q.id = a.session_question_id
                     WHERE q.session_camera_id = sc.id AND a.is_exception) AS exceptions
              FROM virtual_patrol_session_cameras sc
              JOIN virtual_patrol_sessions vs ON vs.id = sc.session_id
             WHERE sc.camera_id = :c AND sc.status IN ('COMPLETED', 'SNAPSHOT_FAILED', 'CAMERA_UNAVAILABLE')
               AND COALESCE(sc.completed_at, sc.started_at) BETWEEN :since AND :at
             ORDER BY COALESCE(sc.completed_at, sc.started_at) DESC LIMIT 1
        """, {"c": camera_id, "since": at - PATROL_LOOKBACK, "at": at})
        module = _attrs(event).get("module_type")
        if module:
            f.history = await _one(db, """
                SELECT count(*) AS same_kind,
                       count(*) FILTER (WHERE a.status <> 'open' OR a.fp_marked_at IS NOT NULL) AS decided,
                       count(*) FILTER (WHERE a.fp_marked_at IS NOT NULL) AS false_positive,
                       (SELECT count(*) FROM incidents i WHERE i.camera_id = :c
                           AND i.created_at >= :since AND i.created_at < :at) AS incidents
                  FROM alerts a
                 WHERE a.camera_id = :c AND a.module_type = :m
                   AND a.created_at >= :since AND a.created_at < :at
            """, {"c": camera_id, "m": module, "since": at - timedelta(days=HISTORY_DAYS), "at": at})

    if site_id is None:
        return f
    f.site = await _one(db, "SELECT id, name FROM sites WHERE id = :s", {"s": site_id})
    profile = await _one(
        db, "SELECT timezone, business_hours, closed_on_public_holidays, criticality "
            "  FROM security_site_profiles WHERE site_id = :s", {"s": site_id})
    if profile is not None:
        profile["business_hours"] = _json(profile["business_hours"])
        f.profile = profile
    local_date = at.astimezone(_zone((profile or {}).get("timezone") or f.timezone)).date()
    f.holiday = await _scalar(db, "SELECT name FROM public_holidays WHERE holiday_date = :d LIMIT 1", {"d": local_date})

    f.guards_on_shift = await _scalar(db, """
        SELECT count(DISTINCT guard_user_id) FROM shifts
         WHERE site_id = :s AND status IN ('active', 'completed') AND actual_start IS NOT NULL
           AND actual_start <= :at AND COALESCE(actual_end, 'infinity'::timestamptz) >= :at
    """, {"s": site_id, "at": at})
    f.visitors_on_site = await _scalar(db, """
        SELECT count(*) FROM visitors
         WHERE site_id = :s AND arrived_at IS NOT NULL AND arrived_at <= :at
           AND COALESCE(departed_at, 'infinity'::timestamptz) >= :at
    """, {"s": site_id, "at": at})
    f.permits = [dict(r) for r in (await db.execute(text("""
        SELECT id, work_type, workers_count, start_at, end_at FROM work_permits
         WHERE site_id = :s AND status IN ('approved', 'active') AND start_at <= :at AND end_at >= :at
         ORDER BY start_at LIMIT 10
    """), {"s": site_id, "at": at})).mappings().all()]
    f.access = await _one(db, """
        SELECT count(*) FILTER (WHERE ae.event_type = 'granted') AS granted,
               count(*) FILTER (WHERE ae.event_type = 'denied')  AS denied,
               count(*) FILTER (WHERE ae.event_type = 'forced')  AS forced,
               (array_agg(ae.denial_reason ORDER BY ae.occurred_at DESC)
                    FILTER (WHERE ae.event_type = 'denied' AND ae.denial_reason IS NOT NULL))[1] AS last_denial_reason
          FROM access_events ae JOIN access_doors d ON d.id = ae.door_id
         WHERE d.site_id = :s AND ae.occurred_at BETWEEN :a AND :b
    """, {"s": site_id, "a": at - WINDOW, "b": at + WINDOW})
    f.virtual_patrol_in_progress = bool(await _scalar(db, """
        SELECT 1 FROM virtual_patrol_sessions
         WHERE site_id = :s AND started_at IS NOT NULL AND started_at <= :at AND started_at >= :fresh
           AND COALESCE(completed_at, 'infinity'::timestamptz) >= :at
           AND status IN ('STARTED', 'IN_PROGRESS', 'COMPLETED', 'PARTIALLY_COMPLETED')
         LIMIT 1
    """, {"s": site_id, "at": at, "fresh": at - STALE_AFTER}))
    f.drone_in_flight = bool(await _scalar(db, """
        SELECT 1 FROM drone_patrol_sessions
         WHERE site_id = :s AND started_at IS NOT NULL AND started_at <= :at AND started_at >= :fresh
           AND COALESCE(ended_at, 'infinity'::timestamptz) >= :at
         LIMIT 1
    """, {"s": site_id, "at": at, "fresh": at - STALE_AFTER}))
    return f


async def context_for(db: AsyncSession, event: Mapping) -> dict:
    """Load and build, for a session already scoped to the event's tenant."""
    return build(event, await load(db, event))
