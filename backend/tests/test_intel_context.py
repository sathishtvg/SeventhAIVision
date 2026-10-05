"""AI security intelligence, phase 3: what was expected at a place and time.

  A — The rules, with nothing running: hours, zones, and the context they make
  B — Reading the facts, as of the event and not as of now
  C — The API: an event's context, and saying what a site expects
  D — The schema

The rule the whole phase turns on: what the platform does not know is said to
be unknown. It is never filled with a default that then reads as a finding.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.db.session import AsyncSessionLocal
from app.services import intel_context as ctx
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql
from tests.test_intel_events import _alert, _events, _read, _world

SGT = ZoneInfo("Asia/Singapore")
#: Monday 5 October 2026, 02:17 in Singapore — the middle of the night.
NIGHT = datetime(2026, 10, 5, 2, 17, tzinfo=SGT)
#: The same Monday at 10:00.
MORNING = datetime(2026, 10, 5, 10, 0, tzinfo=SGT)
WEEKDAY_HOURS = {d: [["08:00", "18:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}
BASE = "/api/v1/security-intelligence"


def _event(at: datetime = NIGHT, **over) -> dict:
    return {"occurred_at": at, "site_id": uuid.uuid4(), "camera_id": uuid.uuid4(), "subject_kind": "PERSON",
            "subject_verdict": None, "location_label": "Gate 1", "attributes": {"module_type": "intrusion"}, **over}


def _facts(**over) -> ctx.Facts:
    base = dict(site={"id": uuid.uuid4(), "name": "Factory A"}, camera={"id": uuid.uuid4(), "name": "Gate 1"},
                profile={"timezone": None, "business_hours": WEEKDAY_HOURS, "closed_on_public_holidays": True,
                         "criticality": "high"},
                guards_on_shift=0, visitors_on_site=0, access={"granted": 0, "denied": 0, "forced": 0,
                                                              "last_denial_reason": None},
                alarm_events=0, virtual_patrol_in_progress=False, drone_in_flight=False)
    return ctx.Facts(**{**base, **over})


def _texts(context: dict, kind: str | None = None) -> list[str]:
    return [s["text"] for s in context["statements"] if kind is None or s["kind"] == kind]


# ─── A. The rules ────────────────────────────────────────────────────────────

def test_business_hours_are_checked_before_they_are_stored():
    assert ctx.validate_business_hours(None) is None, "not defined is allowed, and is not 'closed'"
    assert ctx.validate_business_hours(WEEKDAY_HOURS) == WEEKDAY_HOURS
    assert ctx.validate_business_hours({"sat": []}) == {"sat": []}, "a day with no periods is a closed day"
    for bad in ("08:00-18:00", [], {"monday": [["08:00", "18:00"]]}, {"mon": "08:00"},
                {"mon": [["8am", "6pm"]]}, {"mon": [["08:00"]]}, {"mon": [["25:00", "26:00"]]},
                {"mon": [["09:00", "09:00"]]}, {"mon": [["08:00", "18:00"]] * 7}):
        with pytest.raises(ValueError):
            ctx.validate_business_hours(bad)


def test_inside_outside_and_not_defined_are_three_different_answers():
    assert ctx.hours_state(MORNING, WEEKDAY_HOURS, None, True)[0] == "inside"
    assert ctx.hours_state(NIGHT, WEEKDAY_HOURS, None, True)[0] == "outside"
    assert ctx.hours_state(NIGHT, None, None, True)[0] == "not_defined"
    assert ctx.hours_state(MORNING, {}, None, True)[0] == "outside", "defined, and closed every day"
    saturday = MORNING + timedelta(days=5)
    assert ctx.hours_state(saturday, WEEKDAY_HOURS, None, True) == ("outside", "Sat hours: closed")
    assert ctx.hours_state(MORNING.replace(hour=18), WEEKDAY_HOURS, None, True)[0] == "outside", "closing time is closed"


def test_a_public_holiday_closes_the_site_unless_it_says_otherwise():
    assert ctx.hours_state(MORNING, WEEKDAY_HOURS, "National Day", True) == ("outside", "public holiday (National Day)")
    assert ctx.hours_state(MORNING, WEEKDAY_HOURS, "National Day", False)[0] == "inside"
    assert ctx.hours_state(MORNING, None, "National Day", True)[0] == "not_defined", "a holiday does not define hours"


def test_a_night_shift_runs_past_midnight_and_belongs_to_the_day_it_starts():
    nights = {"sun": [["22:00", "06:00"]]}
    assert ctx.hours_state(NIGHT, nights, None, True) == ("inside", "open 22:00–06:00 from Sun")
    assert ctx.hours_state(NIGHT.replace(hour=7), nights, None, True)[0] == "outside"
    sunday_late = NIGHT - timedelta(hours=3)                       # Sunday 23:17
    assert ctx.hours_state(sunday_late, nights, None, True)[0] == "inside"
    tuesday_early = NIGHT + timedelta(days=1)                      # Tuesday 02:17 — Monday has no night period
    assert ctx.hours_state(tuesday_early, nights, None, True)[0] == "outside"


ZONE = {"id": uuid.uuid4(), "name": "Fuel store", "severity": "high", "is_active": True, "bypass_until": None,
        "schedule_enabled": False, "schedule_timezone": "Asia/Singapore", "active_days": list(range(7)),
        "active_start_time": time(0, 0), "active_end_time": time(23, 59, 59)}


def test_a_zone_is_judged_as_of_the_event_not_as_of_now():
    assert ctx.zone_in_force(ZONE, NIGHT) == (True, "the zone is always in force")
    assert ctx.zone_in_force({**ZONE, "is_active": False}, NIGHT)[0] is False
    bypassed_then = {**ZONE, "bypass_until": NIGHT + timedelta(minutes=30)}
    bypass_over = {**ZONE, "bypass_until": NIGHT - timedelta(minutes=30)}
    assert ctx.zone_in_force(bypassed_then, NIGHT) == (False, "the zone was bypassed at the time")
    assert ctx.zone_in_force(bypass_over, NIGHT)[0] is True, "a bypass that had ended does not excuse the event"

    nights_only = {**ZONE, "schedule_enabled": True, "active_start_time": time(0, 0), "active_end_time": time(6, 0)}
    assert ctx.zone_in_force(nights_only, NIGHT) == (True, "scheduled 00:00–06:00")
    assert ctx.zone_in_force(nights_only, MORNING) == (False, "outside the zone's schedule")
    weekends = {**nights_only, "active_days": [5, 6]}
    assert ctx.zone_in_force(weekends, NIGHT)[0] is False, "02:17 on a Monday is not the weekend"
    # The zone's own time zone decides the weekday: 18:17 UTC on Sunday is Monday in Singapore.
    assert ctx.zone_in_force({**nights_only, "active_days": [0]}, NIGHT.astimezone(timezone.utc))[0] is True


def test_a_site_nobody_has_described_is_never_said_to_be_after_hours():
    c = ctx.build(_event(), _facts(profile=None))
    assert c["time"]["business_hours"] == "not_defined" and c["time"]["after_hours"] is None
    assert not any("hours" in t.lower() for t in _texts(c, "time")), "no claim either way"
    assert any("Business hours are not defined" in u for u in c["unknowns"])
    assert any("No criticality has been set" in u for u in c["unknowns"])
    assert c["place"]["criticality"] is None, "not set is not 'medium'"


def test_out_of_hours_and_in_hours_are_stated_with_their_reason():
    night, day = ctx.build(_event(NIGHT), _facts()), ctx.build(_event(MORNING), _facts())
    assert night["time"]["after_hours"] is True and day["time"]["after_hours"] is False
    assert "Outside business hours — Mon hours: 08:00–18:00" in _texts(night, "time")
    assert "Within business hours — open 08:00–18:00 on Mon" in _texts(day, "time")
    assert "the site was open" in day["expected"] and "the site was open" not in night["expected"]
    assert night["time"]["local"].startswith("2026-10-05T02:17") and night["time"]["weekday"] == "mon"


def test_the_site_profiles_time_zone_decides_what_time_it_was():
    in_london = _facts(profile={"timezone": "Europe/London", "business_hours": WEEKDAY_HOURS,
                                "closed_on_public_holidays": True, "criticality": None})
    c = ctx.build(_event(MORNING), in_london)                      # 10:00 SGT is 03:00 in London
    assert c["time"]["timezone"] == "Europe/London" and c["time"]["after_hours"] is True
    nonsense = _facts(profile={"timezone": "Mars/Olympus", "business_hours": WEEKDAY_HOURS,
                               "closed_on_public_holidays": True, "criticality": None})
    assert ctx.build(_event(MORNING), nonsense)["time"]["after_hours"] is False, "an unknown zone falls back, quietly"


def test_a_cameras_criticality_overrides_its_sites_and_says_where_it_came_from():
    site_only = ctx.build(_event(), _facts())
    camera = ctx.build(_event(), _facts(camera_profile={"area_label": "Server room", "criticality": "critical",
                                                        "is_restricted_area": True}))
    assert site_only["place"]["criticality"] == "high"
    assert {"kind": "place", "text": "Criticality: high", "source": "site profile"} in site_only["statements"]
    assert camera["place"]["criticality"] == "critical" and camera["place"]["area"] == "Server room"
    assert {"kind": "place", "text": "Criticality: critical", "source": "camera profile"} in camera["statements"]
    assert "Restricted area: Server room" in _texts(camera, "place")


def test_the_events_own_zone_is_reported_in_force_or_not_and_other_zones_only_when_in_force():
    other = {**ZONE, "id": uuid.uuid4(), "name": "Yard", "is_active": False}
    e = _event(attributes={"module_type": "intrusion", "zone_id": str(ZONE["id"])})
    c = ctx.build(e, _facts(zones=[ZONE, other]))
    assert [z["name"] for z in c["place"]["zones"]] == ["Fuel store", "Yard"]
    place = _texts(c, "place")
    assert any("“Fuel store” (high) was in force" in t for t in place)
    assert not any("Yard" in t for t in place), "a zone that was off and is not the event's says nothing"
    bypassed = ctx.build(e, _facts(zones=[{**ZONE, "bypass_until": NIGHT + timedelta(hours=1)}]))
    assert any("was not in force: the zone was bypassed at the time" in t for t in _texts(bypassed, "place"))


def test_not_identified_is_never_reported_as_not_authorised():
    unknown = ctx.build(_event(subject_verdict="UNKNOWN"), _facts())
    allowed = ctx.build(_event(subject_verdict="ALLOW"), _facts())
    blocked = ctx.build(_event(subject_kind="VEHICLE", subject_verdict="BLOCK"), _facts())
    silent = ctx.build(_event(subject_verdict=None), _facts())
    assert "The person was not identified" in _texts(unknown, "people")
    assert any("Not identified is not the same as not authorised" in u for u in unknown["unknowns"])
    everything = json.dumps(unknown).lower()
    assert "unauthorised" not in everything and "unauthorized" not in everything and "intruder" not in everything
    assert "The person is on a watchlist as allowed" in _texts(allowed, "people")
    assert "the person is on an allow list" in allowed["expected"]
    assert "The vehicle is on a block list" in _texts(blocked, "people") and blocked["expected"] == []
    assert not any("identified" in t or "list" in t for t in _texts(silent, "people")), "no verdict, nothing said"


def test_who_was_on_site_is_stated_and_counts_as_a_possible_explanation():
    c = ctx.build(_event(MORNING), _facts(guards_on_shift=2, visitors_on_site=3, permits=[
        {"work_type": "electrical", "workers_count": 4}]))
    people = _texts(c, "people")
    assert "2 guard(s) were on shift at the site" in people and "3 visitor(s) were signed in" in people
    assert "A contractor permit was in force (electrical, 4 worker(s))" in people
    assert {"visitors were signed in", "a contractor permit was in force"} <= set(c["expected"])
    nobody = ctx.build(_event(), _facts())
    assert "No guard was on shift at the site" in _texts(nobody, "people")


def test_a_refused_door_nearby_is_stated_and_a_granted_one_may_explain_the_event():
    denied = ctx.build(_event(), _facts(access={"granted": 0, "denied": 2, "forced": 1,
                                                "last_denial_reason": "card expired"}))
    granted = ctx.build(_event(MORNING), _facts(access={"granted": 1, "denied": 0, "forced": 0,
                                                        "last_denial_reason": None}))
    access = _texts(denied, "access")
    assert "Access was denied 2 time(s) at this site within 10 minutes (card expired)" in access
    assert "A door was forced 1 time(s) within 10 minutes" in access
    assert "someone was let in nearby" in granted["expected"] and "someone was let in nearby" not in denied["expected"]
    assert denied["access"]["window_minutes"] == 10


def test_a_false_positive_share_is_stated_only_once_there_is_enough_to_go_on():
    thin = ctx.build(_event(), _facts(history={"same_kind": 3, "decided": 3, "false_positive": 2, "incidents": 0}))
    solid = ctx.build(_event(), _facts(history={"same_kind": 40, "decided": 20, "false_positive": 15, "incidents": 2}))
    assert thin["history"]["false_positive_share"] is None, "two of three is not a pattern"
    assert any("too few have been decided" in u for u in thin["unknowns"])
    assert solid["history"]["false_positive_share"] == 0.75
    history = _texts(solid, "history")
    assert "15 of 20 decided alerts of this kind from this camera were marked false (75%)" in history
    assert "40 earlier alert(s) of this kind from this camera in 30 days" in history
    assert "2 incident(s) at this camera in 30 days" in history


def test_patrols_under_way_are_part_of_the_picture():
    c = ctx.build(_event(), _facts(virtual_patrol_in_progress=True, drone_in_flight=True))
    assert _texts(c, "operations") == ["A virtual patrol of the site was in progress",
                                       "A drone patrol was in the air at the site"]
    drone = ctx.build(_event(attributes={"module_type": "intrusion", "zone_type": "NO_GO"}), _facts())
    assert "Drone security zone: Gate 1 (no go)" in _texts(drone, "place")


def test_an_event_with_no_site_gets_a_context_that_says_so():
    c = ctx.build(_event(site_id=None, camera_id=None), ctx.Facts())
    assert c["place"]["site_name"] is None and c["time"]["business_hours"] == "not_defined"
    assert c["unknowns"] == ["The event has no site, so nothing about a site can be said."]
    assert c["people"]["contractor_permits_in_force"] is None and c["access"] is None and c["history"] is None


def test_every_statement_names_its_source_and_the_same_facts_give_the_same_context():
    facts = _facts(zones=[ZONE], guards_on_shift=1, visitors_on_site=2, holiday="Deepavali",
                   permits=[{"work_type": None, "workers_count": None}],
                   camera_profile={"area_label": "Dock", "criticality": "medium", "is_restricted_area": True},
                   access={"granted": 1, "denied": 1, "forced": 0, "last_denial_reason": None}, alarm_events=2,
                   virtual_patrol_in_progress=True, drone_in_flight=True,
                   history={"same_kind": 9, "decided": 6, "false_positive": 1, "incidents": 1})
    e = _event(subject_verdict="UNKNOWN", attributes={"module_type": "intrusion", "zone_id": str(ZONE["id"])})
    c = ctx.build(e, facts)
    assert len(c["statements"]) >= 12
    assert all(s["source"] and s["text"] and s["kind"] for s in c["statements"]), c["statements"]
    assert ctx.build(e, facts) == c
    assert json.loads(json.dumps(c)) == c, "the context is plain data: it can be stored with an assessment"


# ─── B. Reading the facts ────────────────────────────────────────────────────

async def _scene(at: datetime) -> dict:
    """A site at a moment: described, with a zone, people on site, a door
    refused, an alarm, patrols under way — and, beside each, a row that had
    ended before the moment or lies outside the window."""
    w = await _world()
    t, s, cam = w["tenant"], w["site_a"], w["cam_a"]
    door, panel, zone_alarm, contractor = (uuid.uuid4() for _ in range(4))
    w["zone"] = uuid.uuid4()
    await _run([
        ("INSERT INTO security_site_profiles (tenant_id, site_id, business_hours, criticality) "
         "VALUES (:t,:s,CAST(:h AS jsonb),'high')", {"t": t, "s": s, "h": json.dumps(WEEKDAY_HOURS)}),
        ("INSERT INTO security_camera_profiles (tenant_id, camera_id, area_label, criticality, is_restricted_area) "
         "VALUES (:t,:c,'Fuel store','critical',TRUE)", {"t": t, "c": cam}),
        ("INSERT INTO restricted_zones (id, tenant_id, camera_id, name, polygon) VALUES (:i,:t,:c,'Fuel store','[]')",
         {"i": w["zone"], "t": t, "c": cam}),
        ("INSERT INTO public_holidays (tenant_id, holiday_date, name) VALUES (:t,:d,'Founders Day')",
         {"t": t, "d": at.astimezone(SGT).date()}),
        # On shift then; and a shift that had ended an hour before.
        ("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, status) "
         "VALUES (:t,:s,:g,:a,:b,:a,'active')",
         {"t": t, "s": s, "g": w["users"][GUARD], "a": at - timedelta(hours=2), "b": at + timedelta(hours=6)}),
        ("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, "
         "    actual_end, status) VALUES (:t,:s,:g,:a,:b,:a,:b,'completed')",
         {"t": t, "s": s, "g": w["users"][OPERATOR], "a": at - timedelta(hours=9), "b": at - timedelta(hours=1)}),
        # Signed in then; and one who had left.
        ("INSERT INTO visitors (tenant_id, site_id, full_name, qr_token, arrived_at) VALUES (:t,:s,'V One',:q,:a)",
         {"t": t, "s": s, "q": uuid.uuid4().hex, "a": at - timedelta(minutes=30)}),
        ("INSERT INTO visitors (tenant_id, site_id, full_name, qr_token, arrived_at, departed_at) "
         "VALUES (:t,:s,'V Two',:q,:a,:d)",
         {"t": t, "s": s, "q": uuid.uuid4().hex, "a": at - timedelta(hours=5), "d": at - timedelta(hours=3)}),
        ("INSERT INTO contractors (id, tenant_id, company_name) VALUES (:i,:t,'Sparks Pte Ltd')",
         {"i": contractor, "t": t}),
        # In force then; one that had expired; one never approved.
        ("INSERT INTO work_permits (tenant_id, contractor_id, site_id, work_description, work_type, workers_count, "
         "    start_at, end_at, status) VALUES (:t,:c,:s,'Rewire bay 3','electrical',4,:a,:b,'approved')",
         {"t": t, "c": contractor, "s": s, "a": at - timedelta(hours=1), "b": at + timedelta(hours=3)}),
        ("INSERT INTO work_permits (tenant_id, contractor_id, site_id, work_description, start_at, end_at, status) "
         "VALUES (:t,:c,:s,'Last week',:a,:b,'approved')",
         {"t": t, "c": contractor, "s": s, "a": at - timedelta(days=8), "b": at - timedelta(days=7)}),
        ("INSERT INTO work_permits (tenant_id, contractor_id, site_id, work_description, start_at, end_at, status) "
         "VALUES (:t,:c,:s,'Not approved',:a,:b,'pending')",
         {"t": t, "c": contractor, "s": s, "a": at - timedelta(hours=1), "b": at + timedelta(hours=3)}),
        ("INSERT INTO access_doors (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Rear door')",
         {"i": door, "t": t, "s": s}),
        # Refused three minutes before; granted, but an hour before.
        ("INSERT INTO access_events (tenant_id, door_id, event_type, denial_reason, occurred_at) "
         "VALUES (:t,:d,'denied','card expired',:at)", {"t": t, "d": door, "at": at - timedelta(minutes=3)}),
        ("INSERT INTO access_events (tenant_id, door_id, event_type, occurred_at) VALUES (:t,:d,'granted',:at)",
         {"t": t, "d": door, "at": at - timedelta(hours=1)}),
        ("INSERT INTO alarm_panels (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Panel 1')",
         {"i": panel, "t": t, "s": s}),
        ("INSERT INTO alarm_zones (id, tenant_id, panel_id, zone_number, name, linked_camera_id) "
         "VALUES (:i,:t,:p,1,'Rear PIR',:c)", {"i": zone_alarm, "t": t, "p": panel, "c": cam}),
        ("INSERT INTO alarm_events (tenant_id, panel_id, zone_id, event_type, occurred_at) "
         "VALUES (:t,:p,:z,'zone_alarm',:at)", {"t": t, "p": panel, "z": zone_alarm, "at": at - timedelta(minutes=1)}),
        # A patrol begun twenty minutes before; one begun ten hours before and never closed.
        ("INSERT INTO virtual_patrol_sessions (tenant_id, site_id, patrol_number, schedule_name, scheduled_for, "
         "    status, started_at) VALUES (:t,:s,'VP-1','Night round',:a,'IN_PROGRESS',:a)",
         {"t": t, "s": s, "a": at - timedelta(minutes=20)}),
        ("INSERT INTO virtual_patrol_sessions (tenant_id, site_id, patrol_number, schedule_name, scheduled_for, "
         "    status, started_at) VALUES (:t,:s,'VP-0','Abandoned',:a,'STARTED',:a)",
         {"t": t, "s": w["site_b"], "a": at - timedelta(hours=10)}),
        ("INSERT INTO drone_patrol_sessions (tenant_id, session_number, site_id, mission_name, drone_name, status, "
         "    started_at) VALUES (:t,:n,:s,'Night Watch','Drone One','ACTIVE',:a)",
         {"t": t, "n": f"DP-{uuid.uuid4().hex[:10]}", "s": s, "a": at - timedelta(minutes=5)}),
    ])
    # The camera's record: eight alerts of this kind in the month before, six decided, three of them false.
    for i in range(8):
        aid = await _alert(w, "intrusion", at=at - timedelta(days=i + 1), title=f"earlier {i}")
        if i < 6:
            await _sql("UPDATE alerts SET status = 'dismissed', fp_marked_at = :fp WHERE id = :i",
                       {"i": aid, "fp": at - timedelta(days=i) if i < 3 else None})
    await _alert(w, "weapon", at=at - timedelta(days=1), title="another kind")
    await _alert(w, "intrusion", at=at - timedelta(days=45), title="too long ago")
    return w


async def _load(w: dict, event: dict) -> ctx.Facts:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        facts = await ctx.load(db, event)
        await db.rollback()
    return facts


@pytest.mark.asyncio
async def test_the_facts_are_those_of_the_moment_the_event_happened():
    at = datetime.now(timezone.utc) - timedelta(minutes=10)
    w = await _scene(at)
    event = {"occurred_at": at, "site_id": w["site_a"], "camera_id": w["cam_a"], "subject_kind": "PERSON",
             "subject_verdict": "UNKNOWN", "attributes": json.dumps({"module_type": "intrusion",
                                                                     "zone_id": str(w["zone"])})}
    f = await _load(w, event)
    assert f.timezone == "Asia/Singapore" and f.site["name"] == "Factory A" and f.camera["name"] == "Gate 1"
    assert f.profile["criticality"] == "high" and f.profile["business_hours"] == WEEKDAY_HOURS
    assert f.camera_profile == {"area_label": "Fuel store", "criticality": "critical", "is_restricted_area": True}
    assert f.holiday == "Founders Day" and [z["name"] for z in f.zones] == ["Fuel store"]
    assert f.guards_on_shift == 1, "the shift that ended an hour earlier is not counted"
    assert f.visitors_on_site == 1, "nor the visitor who had left"
    assert [p["work_type"] for p in f.permits] == ["electrical"], "nor a lapsed permit, nor one never approved"
    assert (f.access["denied"], f.access["granted"], f.access["last_denial_reason"]) == (1, 0, "card expired")
    assert f.alarm_events == 1 and f.virtual_patrol_in_progress is True and f.drone_in_flight is True
    assert f.history == {"same_kind": 8, "decided": 6, "false_positive": 3, "incidents": 0}

    c = ctx.build(event, f)
    assert c["place"]["criticality"] == "critical" and c["place"]["restricted_area"] is True
    assert c["time"]["after_hours"] is True, "a holiday, with the site closed on holidays"
    assert c["history"]["false_positive_share"] == 0.5
    assert {s["source"] for s in c["statements"]} >= {"camera profile", "site profile", "restricted_zones",
                                                     "public_holidays", "shifts", "visitors", "work_permits",
                                                     "access_events", "alarm_events", "virtual_patrol_sessions",
                                                     "drone_patrol_sessions", "alerts", "watchlist"}


@pytest.mark.asyncio
async def test_a_patrol_begun_long_ago_and_never_closed_is_not_still_under_way():
    at = datetime.now(timezone.utc) - timedelta(minutes=10)
    w = await _scene(at)
    f = await _load(w, {"occurred_at": at, "site_id": w["site_b"], "camera_id": w["cam_b"], "attributes": {}})
    assert f.virtual_patrol_in_progress is False and f.drone_in_flight is False
    assert f.profile is None and f.guards_on_shift == 0 and f.permits == [] and f.history is None
    c = ctx.build({"occurred_at": at, "site_id": w["site_b"], "camera_id": w["cam_b"], "attributes": {}}, f)
    assert c["time"]["after_hours"] is None and c["place"]["criticality"] is None


@pytest.mark.asyncio
async def test_the_same_event_read_later_has_the_same_context():
    """Guards go home and visitors leave; what was true at 02:17 stays true of 02:17."""
    at = datetime.now(timezone.utc) - timedelta(minutes=10)
    w = await _scene(at)
    event = {"occurred_at": at, "site_id": w["site_a"], "camera_id": w["cam_a"],
             "attributes": {"module_type": "intrusion"}}
    before = ctx.build(event, await _load(w, event))
    await _run([
        ("UPDATE shifts SET status = 'completed', actual_end = now() WHERE tenant_id = :t AND status = 'active'",
         {"t": w["tenant"]}),
        ("UPDATE visitors SET departed_at = now() WHERE tenant_id = :t AND departed_at IS NULL", {"t": w["tenant"]}),
        ("UPDATE work_permits SET status = 'completed' WHERE tenant_id = :t AND status = 'approved'",
         {"t": w["tenant"]}),
    ])
    after = ctx.build(event, await _load(w, event))
    assert after["people"]["guards_on_shift"] == before["people"]["guards_on_shift"] == 1
    assert after["people"]["visitors_on_site"] == before["people"]["visitors_on_site"] == 1
    # A permit since marked completed is the one fact that cannot be recovered:
    # the table keeps a status, not when it changed.
    assert before["people"]["contractor_permits_in_force"] == 1


@pytest.mark.asyncio
async def test_another_tenants_facts_are_never_read():
    at = datetime.now(timezone.utc) - timedelta(minutes=10)
    theirs, mine = await _scene(at), await _world()
    # My event, pointed at their site and camera by id.
    f = await _load(mine, {"occurred_at": at, "site_id": theirs["site_a"], "camera_id": theirs["cam_a"],
                           "attributes": {"module_type": "intrusion"}})
    assert f.site is None and f.camera is None and f.profile is None and f.zones == []
    assert f.guards_on_shift == 0 and f.visitors_on_site == 0 and f.permits == []
    assert f.access["denied"] == 0 and f.alarm_events == 0 and f.history["same_kind"] == 0


# ─── C. The API ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_events_context_is_served_to_those_who_may_see_the_event():
    w = await _world()
    await _alert(w, "intrusion", camera="cam_a", site="site_a")
    await _alert(w, "weapon", camera="cam_b", site="site_b")
    await _read(w)
    ids = {e["site_id"]: e["id"] for e in await _events(w)}
    other = await _world()
    async with _client() as c:
        mine = await c.get(f"{BASE}/events/{ids[w['site_a']]}/context", headers=w["h"][SUPERVISOR])
        not_mine = await c.get(f"{BASE}/events/{ids[w['site_b']]}/context", headers=w["h"][SUPERVISOR])
        viewer = await c.get(f"{BASE}/events/{ids[w['site_b']]}/context", headers=w["h"][VIEWER])
        elsewhere = await c.get(f"{BASE}/events/{ids[w['site_a']]}/context", headers=other["h"][ADMIN])
        missing = await c.get(f"{BASE}/events/{uuid.uuid4()}/context", headers=w["h"][ADMIN])
    assert mine.status_code == 200 and viewer.status_code == 200
    assert not_mine.status_code == 404 and elsewhere.status_code == 404 and missing.status_code == 404
    body = mine.json()
    assert body["event_id"] == str(ids[w["site_a"]]) and body["place"]["site_name"] == "Factory A"
    assert body["time"]["business_hours"] == "not_defined" and body["place"]["criticality"] is None
    assert set(body) >= {"statements", "unknowns", "expected", "place", "time", "people", "access", "operations",
                         "history"}


@pytest.mark.asyncio
async def test_an_administrator_describes_a_site_and_the_context_follows():
    w = await _world()
    await _alert(w, "intrusion", camera="cam_a", site="site_a")
    await _read(w)
    event = (await _events(w))[0]["id"]
    every_day = {d: [["00:00", "23:59"]] for d in ctx.WEEKDAYS}
    async with _client() as c:
        before = await c.get(f"{BASE}/site-profiles/{w['site_a']}", headers=w["h"][ADMIN])
        put = await c.put(f"{BASE}/site-profiles/{w['site_a']}", headers=w["h"][ADMIN], json={
            "timezone": "Asia/Singapore", "business_hours": every_day, "criticality": "critical",
            "closed_on_public_holidays": False, "notes": "Bonded warehouse"})
        cam = await c.put(f"{BASE}/site-profiles/{w['site_a']}/cameras/{w['cam_a']}", headers=w["h"][MANAGER],
                          json={"area_label": "Loading dock", "criticality": "high", "is_restricted_area": True})
        after = await c.get(f"{BASE}/site-profiles/{w['site_a']}", headers=w["h"][VIEWER])
        listed = await c.get(f"{BASE}/site-profiles", headers=w["h"][ADMIN])
        context = await c.get(f"{BASE}/events/{event}/context", headers=w["h"][OPERATOR])
    assert before.status_code == 200 and before.json()["has_profile"] is False
    assert before.json()["business_hours"] is None and before.json()["criticality"] is None
    assert put.status_code == 200 and cam.status_code == 200, (put.text, cam.text)
    a = after.json()
    assert a["has_profile"] is True and a["criticality"] == "critical" and a["business_hours"] == every_day
    assert a["notes"] == "Bonded warehouse" and a["closed_on_public_holidays"] is False
    assert a["cameras"] == [{"camera_id": str(w["cam_a"]), "camera_name": "Gate 1", "location": "North fence",
                             "area_label": "Loading dock", "criticality": "high", "is_restricted_area": True}]
    by_site = {s["site_name"]: s["has_profile"] for s in listed.json()}
    assert by_site == {"Factory A": True, "Factory B": False}
    ctx_body = context.json()
    assert ctx_body["time"]["after_hours"] is False and ctx_body["place"]["criticality"] == "high"
    assert ctx_body["place"]["area"] == "Loading dock" and ctx_body["place"]["restricted_area"] is True


@pytest.mark.asyncio
async def test_a_profile_is_replaced_whole_so_a_field_left_out_is_no_longer_set():
    w = await _world()
    async with _client() as c:
        await c.put(f"{BASE}/site-profiles/{w['site_a']}", headers=w["h"][ADMIN],
                    json={"business_hours": WEEKDAY_HOURS, "criticality": "high"})
        await c.put(f"{BASE}/site-profiles/{w['site_a']}", headers=w["h"][ADMIN], json={"criticality": "low"})
        got = (await c.get(f"{BASE}/site-profiles/{w['site_a']}", headers=w["h"][ADMIN])).json()
    assert got["criticality"] == "low" and got["business_hours"] is None, "hours are 'not defined' again"
    rows = await _sql("SELECT count(*) AS n FROM security_site_profiles WHERE site_id = :s", {"s": w["site_a"]})
    assert rows[0]["n"] == 1


@pytest.mark.asyncio
async def test_only_those_who_manage_the_layer_may_describe_a_site_and_bad_input_is_refused():
    w = await _world()
    url = f"{BASE}/site-profiles/{w['site_a']}"
    cam_url = f"{url}/cameras/{w['cam_a']}"
    async with _client() as c:
        refused = [await c.put(url, headers=w["h"][role], json={"criticality": "high"})
                   for role in (SUPERVISOR, OPERATOR, GUARD, VIEWER)]
        cam_refused = await c.put(cam_url, headers=w["h"][SUPERVISOR], json={"area_label": "x"})
        bad = [await c.put(url, headers=w["h"][ADMIN], json=body) for body in (
            {"criticality": "extreme"}, {"business_hours": {"monday": [["08:00", "18:00"]]}},
            {"business_hours": {"mon": [["8", "18"]]}}, {"timezone": "Mars/Olympus"},
            {"criticality": "high", "risk_weight": 99})]
        wrong_site_camera = await c.put(f"{url}/cameras/{w['cam_b']}", headers=w["h"][ADMIN], json={})
        no_site = await c.put(f"{BASE}/site-profiles/{uuid.uuid4()}", headers=w["h"][ADMIN], json={})
        bad_camera = await c.put(cam_url, headers=w["h"][ADMIN], json={"criticality": "vital"})
    assert [r.status_code for r in refused] == [403, 403, 403, 403] and cam_refused.status_code == 403
    assert [r.status_code for r in bad] == [422, 422, 422, 422, 422], [r.text for r in bad]
    assert "monday" in bad[1].json()["detail"] and "Mars/Olympus" in bad[3].json()["detail"]
    assert wrong_site_camera.status_code == 404 and no_site.status_code == 404 and bad_camera.status_code == 422
    assert await _sql("SELECT 1 FROM security_site_profiles WHERE tenant_id = :t", {"t": w["tenant"]}) == []


@pytest.mark.asyncio
async def test_a_restricted_supervisor_sees_their_own_sites_profile_only():
    w = await _world()
    async with _client() as c:
        listed = await c.get(f"{BASE}/site-profiles", headers=w["h"][SUPERVISOR])
        own = await c.get(f"{BASE}/site-profiles/{w['site_a']}", headers=w["h"][SUPERVISOR])
        other = await c.get(f"{BASE}/site-profiles/{w['site_b']}", headers=w["h"][SUPERVISOR])
    assert [s["site_name"] for s in listed.json()] == ["Factory A"]
    assert own.status_code == 200 and other.status_code == 404


@pytest.mark.asyncio
async def test_describing_a_site_is_audited_with_who_where_and_under_which_request():
    w = await _world()
    async with _client() as c:
        r = await c.put(f"{BASE}/site-profiles/{w['site_a']}", headers=w["h"][ADMIN],
                        json={"criticality": "high", "business_hours": WEEKDAY_HOURS})
        await c.put(f"{BASE}/site-profiles/{w['site_a']}/cameras/{w['cam_a']}", headers=w["h"][ADMIN],
                    json={"is_restricted_area": True})
    rows = await _sql("SELECT action, user_id, resource_type, resource_id, detail, row_hash FROM audit_logs "
                      " WHERE tenant_id = :t AND action LIKE 'intel.%' ORDER BY created_at", {"t": w["tenant"]})
    assert [x["action"] for x in rows] == ["intel.site_profile.update", "intel.camera_profile.update"]
    site = rows[0]
    detail = site["detail"] if isinstance(site["detail"], dict) else json.loads(site["detail"])
    assert site["user_id"] == w["users"][ADMIN] and str(site["resource_id"]) == str(w["site_a"]) and site["row_hash"]
    assert detail["actor_role"] == ADMIN and detail["site_id"] == str(w["site_a"])
    assert detail["source"] == "user" and detail["result"] == "ok" and detail["hours_defined"] is True
    assert detail["request_id"] and detail["request_id"] == r.headers["X-Request-Id"]


# ─── D. The schema ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_profile_tables_are_tenant_isolated_and_forced():
    rows = await _sql(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies "
        "  FROM pg_class c WHERE c.relname IN ('security_site_profiles','security_camera_profiles') ORDER BY 1")
    assert [r["relname"] for r in rows] == ["security_camera_profiles", "security_site_profiles"]
    assert all(r["relrowsecurity"] and r["relforcerowsecurity"] and r["policies"] == 1 for r in rows)


@pytest.mark.asyncio
async def test_a_profile_goes_with_its_site_or_camera_and_cannot_be_given_twice():
    w = await _world()
    await _run([
        ("INSERT INTO security_site_profiles (tenant_id, site_id) VALUES (:t,:s)", {"t": w["tenant"], "s": w["site_a"]}),
        ("INSERT INTO security_camera_profiles (tenant_id, camera_id) VALUES (:t,:c)",
         {"t": w["tenant"], "c": w["cam_a"]}),
    ])
    for stmt, params in (
            ("INSERT INTO security_site_profiles (tenant_id, site_id) VALUES (:t,:s)",
             {"t": w["tenant"], "s": w["site_a"]}),
            ("INSERT INTO security_camera_profiles (tenant_id, camera_id) VALUES (:t,:c)",
             {"t": w["tenant"], "c": w["cam_a"]}),
            ("INSERT INTO security_site_profiles (tenant_id, site_id, business_hours) VALUES (:t,:s,'[]'::jsonb)",
             {"t": w["tenant"], "s": w["site_b"]}),
            ("INSERT INTO security_site_profiles (tenant_id, site_id, criticality) VALUES (:t,:s,'extreme')",
             {"t": w["tenant"], "s": w["site_b"]})):
        with pytest.raises(Exception):
            await _sql(stmt, params)
    await _sql("DELETE FROM cameras WHERE id = :c", {"c": w["cam_a"]})
    assert await _sql("SELECT 1 FROM security_camera_profiles WHERE camera_id = :c", {"c": w["cam_a"]}) == []
