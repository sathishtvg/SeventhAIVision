"""AI security intelligence, phase 4: events that are one thing happening.

  A — The rules, with nothing running: what links two events, and what does not
  B — Against the database: one situation from many alerts, duplicates folded,
      and the alerts themselves untouched
  C — The runner's pass: only for tenants that asked, announced on their channel
  D — The API: situations, their events and the reason for each, camera links
  E — The schema

Two things this phase must never do, each with its own tests: join events that
are not related, and say more than it knows about who somebody is.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.db.session import AsyncSessionLocal
from app.services import intel_correlation as corr
from app.services import intel_runner as runner
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql
from tests.test_intel_events import _alert, _camera, _events, _read, _world

NOW = datetime.now(timezone.utc)
BASE = "/api/v1/security-intelligence"
METHODS = {"FIRST_EVENT", "SAME_ALERT", "DRONE_CCTV", "SAME_IDENTITY", "SAME_SOURCE_REPEAT", "ACCESS_AT_CAMERA",
           "ALARM_AT_CAMERA", "ADJACENT_CAMERA", "NEAR_POSITION", "PATROL_FINDING", "GUARD_SOS_AT_SITE"}
SITE, OTHER_SITE = uuid.uuid4(), uuid.uuid4()
CAM_12, CAM_14, CAM_FAR = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def _e(**over) -> dict:
    """A person seen by camera 12, a minute ago — unless told otherwise."""
    base = {"id": uuid.uuid4(), "source_type": "CCTV_AI", "source_table": "alerts", "source_id": uuid.uuid4(),
            "event_type": "intrusion.zone_breach", "occurred_at": _ago(seconds=60), "site_id": SITE,
            "camera_id": CAM_12, "alert_id": None, "subject_kind": "PERSON", "subject_ref": None,
            "severity": "high", "title": "Restricted zone breach", "location_label": "Camera 12",
            "latitude": 1.3000, "longitude": 103.8000}
    base.update(over)
    if "alert_id" not in over and base["source_table"] == "alerts":
        base["alert_id"] = base["source_id"]
    return base


def _at_14(**over) -> dict:
    """The same kind of thing at camera 14, about 78 m north."""
    return _e(**{"camera_id": CAM_14, "location_label": "Camera 14", "latitude": 1.3007, **over})


# ─── A. The rules ────────────────────────────────────────────────────────────

def test_two_records_of_the_same_alert_are_one_thing_and_the_second_adds_nothing():
    alert = uuid.uuid4()
    worker = _e(source_id=alert, alert_id=alert)
    drone = _e(source_type="DRONE_PATROL", source_table="drone_events", alert_id=alert, camera_id=None)
    link = corr.match(drone, worker)
    assert (link.method, link.confidence, link.duplicate) == ("SAME_ALERT", 0.99, True)
    assert link.matched_event_id == worker["id"]
    assert corr.match(worker, worker) is None, "an event is not linked to itself"


def test_the_drone_modules_own_corroboration_links_a_sighting_to_the_camera_that_agreed():
    camera = _e()
    drone = _e(source_type="DRONE_PATROL", source_table="drone_events", alert_id=None, camera_id=None,
               latitude=None, longitude=None)
    pairs = {frozenset({str(drone["source_id"]), str(camera["alert_id"])})}
    for a, b in ((drone, camera), (camera, drone)):
        link = corr.match(a, b, drone_pairs=pairs)
        assert link.method == "DRONE_CCTV" and link.confidence == 0.9 and "corroborated by Camera 12" in link.reason
    assert corr.match(drone, camera) is None, "without it, and with no position, nothing links them"


def test_events_at_different_sites_are_never_joined_however_alike():
    plate = dict(source_type="LPR", event_type="lpr.blocklist_match", subject_kind="VEHICLE", subject_ref="SGX1234A")
    here, there = _e(**plate), _e(site_id=OTHER_SITE, **plate)
    assert corr.match(here, there) is None
    assert corr.match(_e(), _e(site_id=OTHER_SITE)) is None, "not even the same camera id and kind"
    assert corr.match(_e(site_id=None), _e(site_id=None)) is None, "nor two events with no site at all"


def test_the_same_plate_or_watchlist_entry_is_the_same_identity_within_half_an_hour():
    plate = dict(source_type="LPR", event_type="lpr.read", subject_kind="VEHICLE", subject_ref="SGX1234A")
    gate, yard = _e(**plate), _at_14(occurred_at=_ago(minutes=9), **plate)
    link = corr.match(gate, yard)
    assert (link.method, link.confidence) == ("SAME_IDENTITY", 0.95)
    assert link.reason == "The same number plate, SGX1234A, 8 min apart."
    assert corr.match(gate, _at_14(occurred_at=_ago(minutes=45), **plate)) is None, "too long ago"
    assert corr.match(gate, _at_14(**{**plate, "subject_ref": "SGX9999Z"})).method != "SAME_IDENTITY"

    entry = str(uuid.uuid4())
    face = dict(source_type="FACE_RECOGNITION", event_type="face.match", subject_kind="PERSON", subject_ref=entry)
    known = corr.match(_e(**face), _e(camera_id=CAM_FAR, latitude=1.33, occurred_at=_ago(minutes=20), **face))
    assert (known.method, known.confidence) == ("SAME_IDENTITY", 0.9) and entry not in known.reason


def test_two_unidentified_people_far_apart_are_not_said_to_be_the_same_person():
    face = dict(source_type="FACE_RECOGNITION", event_type="face.unknown", subject_ref=None)
    here = _e(**face)
    far = _e(camera_id=CAM_FAR, location_label="Far camera", latitude=1.3100, **face)   # about 1.1 km
    assert corr.match(here, far) is None


def test_the_same_camera_raising_the_same_alert_again_is_a_duplicate_for_five_minutes():
    first = _e(occurred_at=_ago(minutes=4))
    again = corr.match(_e(), first)
    assert (again.method, again.duplicate, again.confidence) == ("SAME_SOURCE_REPEAT", True, 0.9)
    assert again.reason == "The same alert from Camera 12 again, 3 min apart."
    assert corr.match(_e(), _e(occurred_at=_ago(minutes=8))) is None, "eight minutes later it is a new matter"
    assert corr.match(_e(event_type="weapon.detected", subject_kind="NONE"), first) is None, \
        "a different alert from the same camera is not a repeat"


def test_a_source_with_no_camera_repeats_by_its_own_place():
    zone = dict(source_type="ALARM", event_type="alarm.zone_alarm", camera_id=None, subject_kind="NONE",
                latitude=None, longitude=None, location_label="Roof hatch")
    link = corr.match(_e(**zone), _e(occurred_at=_ago(minutes=3), **zone))
    assert (link.method, link.duplicate, link.confidence) == ("SAME_SOURCE_REPEAT", True, 0.85)
    assert corr.match(_e(**zone), _e(**{**zone, "location_label": "Plant room"})) is None


def test_a_door_event_and_what_the_camera_saw_around_then():
    door = dict(source_type="ACCESS_CONTROL", event_type="access.denied", subject_kind="NONE",
                title="Access denied at Rear door", occurred_at=_ago(minutes=3))
    at_camera = corr.match(_e(), _e(**door))
    elsewhere = corr.match(_e(), _e(camera_id=None, latitude=None, longitude=None, **door))
    assert (at_camera.method, at_camera.confidence) == ("ACCESS_AT_CAMERA", 0.85)
    assert at_camera.reason == "An access event at the door this camera watches, 2 min apart."
    assert (elsewhere.method, elsewhere.confidence) == ("ACCESS_AT_CAMERA", 0.6)
    assert corr.match(_e(), _e(**{**door, "occurred_at": _ago(minutes=20)})) is None
    assert corr.match(_e(**door), _e(**{**door, "event_type": "access.forced"})) is None, "two door events alone"


def test_an_alarm_and_what_the_camera_saw_around_then():
    alarm = dict(source_type="ALARM", event_type="alarm.zone_alarm", subject_kind="NONE", occurred_at=_ago(minutes=2))
    assert corr.match(_e(), _e(**alarm)).method == "ALARM_AT_CAMERA"
    assert corr.match(_e(), _e(**alarm)).confidence == 0.85
    assert corr.match(_e(), _e(camera_id=None, **alarm)).confidence == 0.55


def test_a_person_at_the_next_camera_is_nearby_and_moments_later_and_said_to_be_no_more():
    link = corr.match(_at_14(occurred_at=_ago(seconds=10)), _e())
    assert link.method == "ADJACENT_CAMERA" and 0.5 <= link.confidence < 0.7 and not link.duplicate
    assert "78 m away, 50 s apart" in link.reason
    assert "not identified as the same person" in link.reason
    too_late = _at_14(occurred_at=NOW + timedelta(minutes=10))
    assert corr.match(too_late, _e()) is None, "ten minutes is too long for 78 metres"
    # Two cameras given the same coordinates — a site's, usually — are not "0 m apart":
    # nobody measured that. The sentence says what is recorded.
    here = _e()
    beside = _e(camera_id=uuid.uuid4(), occurred_at=_ago(seconds=10))
    same_place = corr.match(beside, here)
    assert same_place.method == "ADJACENT_CAMERA" and same_place.confidence == 0.7
    assert "then at another camera recorded at the same position, " in same_place.reason
    assert "0 m away" not in same_place.reason
    far = _e(camera_id=CAM_FAR, latitude=1.3040)                    # about 445 m
    assert corr.match(far, _e()) is None
    assert corr.match(_at_14(subject_kind="VEHICLE"), _e()) is None, "a vehicle there is not the person here"
    assert corr.match(_at_14(subject_kind="NONE", event_type="fire_smoke.detected"), _e(subject_kind="NONE")) is None


def test_cameras_an_administrator_linked_are_neighbours_whatever_their_coordinates():
    no_position = dict(latitude=None, longitude=None)
    a, b = _e(**no_position), _at_14(occurred_at=_ago(seconds=20), **no_position)
    assert corr.match(b, a) is None, "no coordinates and no link: nothing to go on"
    links = {frozenset({str(CAM_12), str(CAM_14)}): 45}
    link = corr.match(b, a, camera_links=links)
    assert (link.method, link.confidence) == ("ADJACENT_CAMERA", 0.75) and "the next camera" in link.reason
    after_the_walk = _at_14(occurred_at=NOW + timedelta(minutes=5), latitude=None, longitude=None)
    assert corr.match(after_the_walk, a, camera_links=links) is None, "more than twice the walk"


def test_a_drone_sighting_near_a_camera_and_a_patrol_finding_followed_by_a_sighting():
    drone = _e(source_type="DRONE_PATROL", source_table="drone_events", event_type="drone.intrusion",
               camera_id=None, alert_id=None, latitude=1.3003, occurred_at=_ago(seconds=20))
    near = corr.match(drone, _e())
    assert (near.method, near.confidence) == ("NEAR_POSITION", 0.65) and "33 m from Camera 12" in near.reason

    patrol = dict(source_type="VIRTUAL_PATROL", source_table="virtual_patrol_session_answers",
                  event_type="vpatrol.exception", subject_kind="NONE", alert_id=None, occurred_at=_ago(minutes=40))
    on_camera = corr.match(_e(), _e(**patrol))
    with_drone = corr.match(drone, _e(camera_id=CAM_FAR, latitude=1.31, **patrol))
    assert (on_camera.method, on_camera.confidence) == ("PATROL_FINDING", 0.7)
    assert (with_drone.method, with_drone.confidence) == ("PATROL_FINDING", 0.5)
    assert "a drone then saw something there" in with_drone.reason
    assert corr.match(_e(), _e(camera_id=CAM_FAR, latitude=1.31, **patrol)) is None, "a far camera, no drone"
    assert corr.match(_e(), _e(**{**patrol, "occurred_at": _ago(hours=3)})) is None


def test_a_guards_sos_joins_a_serious_event_at_the_site_and_not_a_minor_one():
    sos = dict(source_type="GUARD", source_table="incidents", event_type="guard.sos", camera_id=None,
               alert_id=None, severity="critical", latitude=None, longitude=None, occurred_at=_ago(seconds=10))
    link = corr.match(_e(**sos), _e(event_type="weapon.detected", severity="critical", occurred_at=_ago(minutes=4)))
    assert (link.method, link.confidence) == ("GUARD_SOS_AT_SITE", 0.55)
    assert corr.match(_e(**sos), _e(severity="low")) is None
    assert corr.match(_e(**sos), _e(severity="critical", occurred_at=_ago(minutes=40))) is None


def test_unrelated_events_at_one_site_in_the_same_minute_are_not_joined():
    """A fire alarm and a number plate. A fall and a crowd. Close in time is not related."""
    pairs = [
        (_e(source_type="LPR", event_type="lpr.read", subject_kind="VEHICLE", subject_ref="SGX1A"),
         _at_14(event_type="fire_smoke.detected", subject_kind="NONE")),
        (_e(event_type="fall.detected"), _e(event_type="crowd.density", subject_kind="NONE")),
        (_e(source_type="SENSOR", event_type="iot.threshold_breach", camera_id=None, subject_kind="NONE",
            location_label="Cold room"), _e()),
        (_e(source_type="SYSTEM", event_type="camera.stream_disconnected", subject_kind="NONE"),
         _at_14(source_type="SYSTEM", event_type="camera.stream_disconnected", subject_kind="NONE")),
    ]
    for a, b in pairs:
        assert corr.match(a, b) is None and corr.match(b, a) is None, (a["event_type"], b["event_type"])


def test_the_strongest_rule_wins_and_every_link_is_explained():
    plate = dict(source_type="LPR", event_type="lpr.read", subject_kind="VEHICLE", subject_ref="SGX1234A")
    assert corr.match(_at_14(**plate), _e(**plate)).method == "SAME_IDENTITY", "stronger than the next camera"
    door = dict(source_type="ACCESS_CONTROL", event_type="access.denied", subject_kind="NONE")
    cases = [corr.match(_e(), _e(occurred_at=_ago(minutes=2))), corr.match(_at_14(), _e()),
             corr.match(_e(), _e(**door)), corr.match(_at_14(**plate), _e(**plate))]
    for link in cases:
        assert link.method in METHODS and link.reason.strip().endswith(".") and 0 < link.confidence <= 1
        assert link.matched_event_id is not None


def test_an_event_joins_the_situation_it_is_most_surely_part_of_or_none():
    quiet = {"id": uuid.uuid4(), "last_event_at": _ago(minutes=20), "severity": "high"}
    recent = {"id": uuid.uuid4(), "last_event_at": _ago(minutes=1), "severity": "high"}
    door = _e(source_type="ACCESS_CONTROL", event_type="access.denied", subject_kind="NONE",
              occurred_at=_ago(minutes=2))
    event = _e()
    picked = corr.choose(event, [(quiet, [_at_14(occurred_at=_ago(seconds=90))]), (recent, [door])])
    assert picked[0]["id"] == recent["id"] and picked[1].method == "ACCESS_AT_CAMERA", "0.85 beats the next camera"
    same = [(quiet, [_e(occurred_at=_ago(minutes=2))]), (recent, [_e(occurred_at=_ago(minutes=3))])]
    assert corr.choose(event, same)[0]["id"] == recent["id"], "between equals, the one heard from last"
    assert corr.choose(event, [(quiet, [_e(site_id=OTHER_SITE)])]) is None
    assert corr.choose(event, []) is None


def test_a_situations_severity_never_goes_down():
    assert corr.promotes({"severity": "medium"}, {"severity": "critical"}) is True
    assert corr.promotes({"severity": "critical"}, {"severity": "high"}) is False
    assert corr.promotes({"severity": "high"}, {"severity": "high"}) is False, "the first of equals keeps the name"


# ─── B. Against the database ─────────────────────────────────────────────────

async def _place(w: dict, now: datetime | None = None) -> dict:
    """Read the sources, then place what was read."""
    await _read(w, now=now or NOW)
    return await corr.correlate_tenant(AsyncSessionLocal, w["tenant"], now or NOW)


async def _situations(w: dict) -> list[dict]:
    rows = await _sql("SELECT * FROM security_situations WHERE tenant_id = :t ORDER BY started_at, situation_number",
                      {"t": w["tenant"]})
    return [dict(r) for r in rows]


async def _links(situation_id) -> list[dict]:
    rows = await _sql(
        "SELECT l.method, l.reason, l.confidence, l.is_duplicate, e.source_type, e.title, e.occurred_at "
        "  FROM security_situation_events l JOIN security_events e ON e.id = l.event_id "
        " WHERE l.situation_id = :s ORDER BY e.occurred_at, e.id", {"s": situation_id})
    return [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_a_refused_door_two_cameras_and_a_drone_become_one_situation_with_a_reason_for_each():
    w = await _world()
    w["cam_14"] = await _camera(w, "site_a", "Camera 14", 1.3004, 103.8001)         # about 33 m from Gate 1
    a1 = await _alert(w, "intrusion", camera="cam_a", code="intrusion.zone_breach", title="Person at Gate 1",
                      at=_ago(seconds=240))
    a2 = await _alert(w, "access", camera="cam_a", code="access.denied", title="Access denied at Rear door",
                      severity="high", at=_ago(seconds=200))
    a3 = await _alert(w, "intrusion", camera="cam_14", code="intrusion.zone_breach", title="Person at Camera 14",
                      at=_ago(seconds=170))
    drone = uuid.uuid4()
    await _run([
        ("INSERT INTO drone_events (id, tenant_id, site_id, module_type, detected_at, risk_level, risk_score, "
         "    ai_confidence, verification_state, verified_at, label, estimated_latitude, estimated_longitude, "
         "    location_method, created_at) VALUES (:i,:t,:s,'intrusion',:at,'CRITICAL',91,0.94,'VERIFIED',:at,"
         "    'Person confirmed by drone',1.3009,103.8001,'PROJECTED',:at)",
         {"i": drone, "t": w["tenant"], "s": w["site_a"], "at": _ago(seconds=100)}),
    ])
    before = [dict(r) for r in await _sql(
        "SELECT id, status, severity, correlation_id, acknowledged_at FROM alerts WHERE tenant_id = :t ORDER BY id",
        {"t": w["tenant"]})]

    result = await _place(w)
    assert (result["opened"], result["joined"], result["failed"]) == (1, 3, 0), result

    situations = await _situations(w)
    assert len(situations) == 1
    s = situations[0]
    assert s["event_count"] == 4 and s["duplicate_count"] == 0 and s["status"] == "ACTIVE"
    assert s["source_types"] == ["ACCESS_CONTROL", "CCTV_AI", "DRONE_PATROL"]
    assert s["severity"] == "critical" and s["title"] == "Person confirmed by drone", "named after the most severe"
    assert s["situation_number"].startswith("SIT-") and s["site_id"] == w["site_a"]
    assert float(s["correlation_confidence"]) < 0.9, "as sure as its least sure link"

    links = await _links(s["id"])
    assert [l["method"] for l in links] == ["FIRST_EVENT", "ACCESS_AT_CAMERA", "ADJACENT_CAMERA", "NEAR_POSITION"]
    assert all(l["reason"].strip() and 0 < float(l["confidence"]) <= 1 for l in links)
    assert "not identified as the same person" in links[2]["reason"]
    assert all(e["status"] == "LINKED" for e in await _events(w))

    after = [dict(r) for r in await _sql(
        "SELECT id, status, severity, correlation_id, acknowledged_at FROM alerts WHERE tenant_id = :t ORDER BY id",
        {"t": w["tenant"]})]
    assert after == before and len(after) == 3, "the alerts are exactly as the workers left them"
    assert {a1, a2, a3} == {r["id"] for r in after}


@pytest.mark.asyncio
async def test_one_camera_raising_the_same_alert_six_times_is_one_situation_to_look_at():
    w = await _world()
    for i in range(6):
        await _alert(w, "face", code="face.unknown", title="Unrecognized face detected", severity="medium",
                     at=_ago(seconds=600 - i * 100))
    result = await _place(w)
    assert (result["opened"], result["joined"], result["duplicates"]) == (1, 5, 5)
    (s,) = await _situations(w)
    assert (s["event_count"], s["duplicate_count"]) == (6, 5)
    assert [l["is_duplicate"] for l in await _links(s["id"])] == [False, True, True, True, True, True]
    alerts = await _sql("SELECT count(*) AS n, count(*) FILTER (WHERE status = 'open') AS open FROM alerts "
                        " WHERE tenant_id = :t", {"t": w["tenant"]})
    assert (alerts[0]["n"], alerts[0]["open"]) == (6, 6), "folded on the situation; every alert still there"


@pytest.mark.asyncio
async def test_other_sites_and_unrelated_events_get_situations_of_their_own():
    w = await _world()
    await _alert(w, "intrusion", camera="cam_a", site="site_a", code="intrusion.zone_breach", at=_ago(seconds=90))
    await _alert(w, "intrusion", camera="cam_b", site="site_b", code="intrusion.zone_breach", at=_ago(seconds=80))
    await _alert(w, "fire_smoke", camera="cam_a", site="site_a", code="fire_smoke.detected", title="Smoke",
                 at=_ago(seconds=70))
    result = await _place(w)
    assert (result["opened"], result["joined"]) == (3, 0)
    situations = await _situations(w)
    assert [s["event_count"] for s in situations] == [1, 1, 1]
    assert {s["site_id"] for s in situations} == {w["site_a"], w["site_b"]}
    assert [s["correlation_confidence"] for s in situations] == [None, None, None], "nothing was correlated"
    assert [s["situation_number"][-4:] for s in situations] == ["0001", "0002", "0003"]


@pytest.mark.asyncio
async def test_a_situation_that_has_gone_quiet_settles_and_the_next_event_is_a_new_matter():
    w = await _world()
    await _alert(w, "intrusion", code="intrusion.zone_breach", at=_ago(seconds=60))
    await _place(w)
    later = NOW + corr.QUIET + timedelta(minutes=5)
    await _alert(w, "intrusion", code="intrusion.zone_breach", at=later - timedelta(seconds=30))
    result = await _place(w, now=later)
    assert (result["settled"], result["opened"], result["joined"]) == (1, 1, 0)
    first, second = await _situations(w)
    assert (first["status"], second["status"]) == ("SETTLED", "ACTIVE") and first["settled_at"] is not None
    assert (first["event_count"], second["event_count"]) == (1, 1)


@pytest.mark.asyncio
async def test_a_more_severe_event_renames_the_situation_and_a_lesser_one_does_not():
    w = await _world()
    await _alert(w, "intrusion", code="intrusion.zone_breach", severity="medium", title="Person in the yard",
                 at=_ago(seconds=100))
    await _place(w)
    await _alert(w, "access", code="access.forced", severity="critical", title="Door forced open: Rear door",
                 at=_ago(seconds=60))
    await _place(w)
    (s,) = await _situations(w)
    assert (s["severity"], s["title"]) == ("critical", "Door forced open: Rear door")
    await _alert(w, "intrusion", code="intrusion.zone_breach", severity="low", title="Person in the yard again",
                 at=_ago(seconds=30))
    await _place(w)
    (s,) = await _situations(w)
    assert (s["severity"], s["title"], s["event_count"]) == ("critical", "Door forced open: Rear door", 3)


@pytest.mark.asyncio
async def test_placing_again_changes_nothing():
    w = await _world()
    await _alert(w, "intrusion", code="intrusion.zone_breach", at=_ago(seconds=90))
    await _alert(w, "intrusion", code="intrusion.zone_breach", at=_ago(seconds=30))
    first = await _place(w)
    again = await _place(w)
    assert (first["opened"], first["joined"]) == (1, 1)
    assert (again["opened"], again["joined"], again["announce"]) == (0, 0, [])
    (s,) = await _situations(w)
    assert s["event_count"] == 2 and len(await _links(s["id"])) == 2


@pytest.mark.asyncio
async def test_a_drone_sighting_joins_the_camera_the_drone_module_said_agreed_with_it():
    w = await _world()
    alert = await _alert(w, "intrusion", camera="cam_b", site="site_b", code="intrusion.zone_breach",
                         at=_ago(seconds=120))
    drone = uuid.uuid4()
    await _run([
        # No position on the sighting: only the drone module's own finding can link it.
        ("INSERT INTO drone_events (id, tenant_id, site_id, module_type, detected_at, risk_level, "
         "    verification_state, verified_at, created_at) VALUES (:i,:t,:s,'intrusion',:at,'HIGH','VERIFIED',:at,:at)",
         {"i": drone, "t": w["tenant"], "s": w["site_b"], "at": _ago(seconds=60)}),
        ("INSERT INTO drone_event_cameras (tenant_id, event_id, camera_id, related_alert_id, corroborates) "
         "VALUES (:t,:e,:c,:a,TRUE)", {"t": w["tenant"], "e": drone, "c": w["cam_b"], "a": alert}),
    ])
    result = await _place(w)
    assert (result["opened"], result["joined"]) == (1, 1)
    (s,) = await _situations(w)
    links = await _links(s["id"])
    assert [l["method"] for l in links] == ["FIRST_EVENT", "DRONE_CCTV"]
    assert links[1]["reason"] == "The drone's sighting was corroborated by Dock 4 — North fence."


@pytest.mark.asyncio
async def test_two_tenants_number_and_place_their_own_events_and_see_only_their_own():
    a, b = await _world(), await _world()
    for w in (a, b):
        await _alert(w, "intrusion", code="intrusion.zone_breach", at=_ago(seconds=60))
        await _alert(w, "weapon", code="weapon.detected", title="Weapon", at=_ago(seconds=30))
        await _place(w)
    for w in (a, b):
        assert [s["situation_number"][-4:] for s in await _situations(w)] == ["0001", "0002"]

    async with AsyncSessionLocal() as db:
        row = (await db.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))).first()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(a["tenant"])})
        mine = (await db.execute(text("SELECT count(*) FROM security_situations"))).scalar_one()
        links = (await db.execute(text("SELECT count(*) FROM security_situation_events"))).scalar_one()
        await db.rollback()
    assert row.rolsuper is False and row.rolbypassrls is False
    assert (mine, links) == (2, 2), "the application role, as tenant A, sees A's two and not B's"


# ─── C. The runner's pass ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_runner_places_events_only_for_tenants_that_asked_and_tells_each_on_its_own_channel():
    on, off = await _world(enabled=True), await _world(enabled=False)
    for w in (on, off):
        await _alert(w, "intrusion", code="intrusion.zone_breach", title="Person at the gate", at=_ago(seconds=90))
        await _alert(w, "intrusion", code="intrusion.zone_breach", title="Person at the gate", at=_ago(seconds=40))
    pub = runner.ListPublisher()
    await runner.run_ingest_tick(AsyncSessionLocal, now=NOW)
    result = await runner.run_correlate_tick(AsyncSessionLocal, pub, now=NOW)
    assert result["failed"] == 0 and result["opened"] >= 1 and result["joined"] >= 1
    assert len(await _situations(on)) == 1 and await _situations(off) == []

    mine = [(kind, payload) for tenant, kind, payload in pub.events if tenant == str(on["tenant"])]
    assert [kind for kind, _ in mine] == ["intel_situation_opened", "intel_situation_updated"]
    assert not [1 for tenant, _, _ in pub.events if tenant == str(off["tenant"])]
    opened, updated = mine[0][1], mine[1][1]
    (s,) = await _situations(on)
    assert opened["situation_id"] == str(s["id"]) and opened["opened"] is True and opened["link"] is None
    assert opened["title"] == "Person at the gate" and opened["event_count"] == 1
    assert updated["event_count"] == 2 and updated["duplicate_count"] == 1
    assert updated["link"]["method"] == "SAME_SOURCE_REPEAT" and updated["link"]["is_duplicate"] is True
    assert json.loads(json.dumps(updated)) == updated, "plain data: it goes on the live channel as JSON"


@pytest.mark.asyncio
async def test_a_situation_is_on_record_even_when_it_cannot_be_announced():
    class Down:
        async def publish(self, *a):
            raise ConnectionError("redis is away")

    w = await _world()
    await _alert(w, "intrusion", code="intrusion.zone_breach", at=_ago(seconds=30))
    await runner.run_ingest_tick(AsyncSessionLocal, now=NOW)
    result = await runner.run_correlate_tick(AsyncSessionLocal, Down(), now=NOW)
    assert result["opened"] >= 1 and result["failed"] == 0
    assert len(await _situations(w)) == 1


# ─── D. The API ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_situation_is_shown_with_its_events_the_reason_for_each_and_its_sources():
    w = await _world()
    await _alert(w, "intrusion", camera="cam_a", code="intrusion.zone_breach", title="Person at Gate 1",
                 at=_ago(seconds=200))
    await _alert(w, "access", camera="cam_a", code="access.denied", title="Access denied", at=_ago(seconds=150))
    await _alert(w, "intrusion", camera="cam_a", code="intrusion.zone_breach", title="Person at Gate 1",
                 at=_ago(seconds=100))
    await _alert(w, "weapon", camera="cam_b", site="site_b", code="weapon.detected", title="Weapon at Dock 4",
                 severity="critical", at=_ago(seconds=50))
    await _place(w)
    at_a = next(s for s in await _situations(w) if s["site_id"] == w["site_a"])
    async with _client() as c:
        listed = await c.get(BASE + "/situations", headers=w["h"][OPERATOR])
        one = await c.get(f"{BASE}/situations/{at_a['id']}", headers=w["h"][VIEWER])
        mine = await c.get(BASE + "/situations", headers=w["h"][SUPERVISOR])
        active = await c.get(BASE + "/situations", params={"status": "ACTIVE", "severity": "critical"},
                             headers=w["h"][ADMIN])
        with_door = await c.get(BASE + "/situations", params={"source_type": "ACCESS_CONTROL"}, headers=w["h"][ADMIN])
        at_b = await c.get(BASE + "/situations", params={"site_id": str(w["site_b"])}, headers=w["h"][ADMIN])
        bad = [await c.get(BASE + "/situations", params=p, headers=w["h"][ADMIN]) for p in (
            {"status": "OPEN"}, {"severity": "urgent"}, {"source_type": "RADAR"})]
    assert listed.status_code == 200 and listed.json()["total"] == 2
    assert [s["title"] for s in listed.json()["items"]] == ["Weapon at Dock 4", "Person at Gate 1"], "latest first"
    assert [s["site_name"] for s in mine.json()["items"]] == ["Factory A"], "a restricted supervisor's own site"
    assert [s["title"] for s in active.json()["items"]] == ["Weapon at Dock 4"]
    assert [s["title"] for s in with_door.json()["items"]] == ["Person at Gate 1"]
    assert [s["title"] for s in at_b.json()["items"]] == ["Weapon at Dock 4"]
    assert [r.status_code for r in bad] == [422, 422, 422]

    body = one.json()
    assert body["event_count"] == 3 and body["duplicate_count"] == 1 and body["site_name"] == "Factory A"
    assert [e["method"] for e in body["events"]] == ["FIRST_EVENT", "ACCESS_AT_CAMERA", "SAME_SOURCE_REPEAT"]
    assert all(e["reason"] and e["link_confidence"] is not None for e in body["events"])
    assert body["events"][2]["is_duplicate"] is True and body["events"][2]["matched_event_id"] == body["events"][0]["id"]
    assert [(s["source_type"], s["label"], s["events"]) for s in body["sources"]] == [
        ("CCTV_AI", "Gate 1", 2), ("ACCESS_CONTROL", "Gate 1", 1)]


@pytest.mark.asyncio
async def test_a_situation_at_another_site_or_in_another_tenant_is_not_found():
    w, other = await _world(), await _world()
    await _alert(w, "weapon", camera="cam_b", site="site_b", code="weapon.detected", at=_ago(seconds=50))
    await _place(w)
    (s,) = await _situations(w)
    client_user = uuid.uuid4()
    await _sql("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
               "VALUES (:i,:t,7,:e,'x','A Client')",
               {"i": client_user, "t": w["tenant"], "e": f"client-{client_user.hex[:8]}@intel.test"})
    from tests.test_drone_api import _auth
    async with _client() as c:
        restricted = await c.get(f"{BASE}/situations/{s['id']}", headers=w["h"][SUPERVISOR])
        elsewhere = await c.get(f"{BASE}/situations/{s['id']}", headers=other["h"][ADMIN])
        missing = await c.get(f"{BASE}/situations/{uuid.uuid4()}", headers=w["h"][ADMIN])
        theirs = await c.get(BASE + "/situations", headers=other["h"][ADMIN])
        client = await c.get(BASE + "/situations", headers=_auth(client_user, w["tenant"], 7))
        guard = await c.get(f"{BASE}/situations/{s['id']}", headers=w["h"][GUARD])
    assert [restricted.status_code, elsewhere.status_code, missing.status_code] == [404, 404, 404]
    assert theirs.json()["total"] == 0 and client.status_code == 403 and guard.status_code == 200


@pytest.mark.asyncio
async def test_an_administrator_says_which_cameras_are_next_to_each_other():
    w = await _world()
    w["cam_14"] = await _camera(w, "site_a", "Camera 14")
    url = f"{BASE}/site-profiles/{w['site_a']}/camera-links"
    pair = {"camera_a": str(w["cam_a"]), "camera_b": str(w["cam_14"]), "walk_seconds": 45, "note": "Through the yard"}
    async with _client() as c:
        empty = await c.get(url, headers=w["h"][VIEWER])
        refused = [await c.put(url, headers=w["h"][role], json={"links": [pair]})
                   for role in (SUPERVISOR, OPERATOR, GUARD, VIEWER)]
        put = await c.put(url, headers=w["h"][MANAGER], json={"links": [pair]})
        got = await c.get(url, headers=w["h"][OPERATOR])
        bad = [await c.put(url, headers=w["h"][ADMIN], json={"links": [link]}) for link in (
            {**pair, "camera_b": str(w["cam_a"])},                     # a camera with itself
            {**pair, "camera_b": str(w["cam_b"])},                     # a camera of another site
            {**pair, "walk_seconds": 0}, {**pair, "walk_seconds": 99999}, {**pair, "weight": 3})]
        twice = await c.put(url, headers=w["h"][ADMIN], json={"links": [pair, {
            "camera_a": pair["camera_b"], "camera_b": pair["camera_a"], "walk_seconds": 30}]})
        cleared = await c.put(url, headers=w["h"][ADMIN], json={"links": []})
    assert empty.status_code == 200 and empty.json()["links"] == []
    assert [r.status_code for r in refused] == [403, 403, 403, 403]
    assert put.status_code == 200, put.text
    (link,) = got.json()["links"]
    assert {link["camera_a_name"], link["camera_b_name"]} == {"Gate 1", "Camera 14"}
    assert link["walk_seconds"] == 45 and link["note"] == "Through the yard"
    assert [r.status_code for r in bad] == [422, 422, 422, 422, 422], [r.text for r in bad]
    assert twice.status_code == 422 and "listed twice" in twice.json()["detail"]
    assert cleared.status_code == 200 and cleared.json()["links"] == []
    audit = await _sql("SELECT detail FROM audit_logs WHERE tenant_id = :t AND action = 'intel.camera_links.update' "
                       " ORDER BY created_at", {"t": w["tenant"]})
    counts = [(d["detail"] if isinstance(d["detail"], dict) else json.loads(d["detail"]))["links"] for d in audit]
    assert counts == [1, 0], "each change that was made is on the record, and the refused ones are not"


@pytest.mark.asyncio
async def test_a_camera_link_is_what_joins_two_cameras_that_have_no_coordinates():
    w = await _world()
    for key, name in (("cam_x", "Stair 1"), ("cam_y", "Stair 2"), ("cam_z", "Stair 3")):
        w[key] = await _camera(w, "site_a", name)
    await _alert(w, "intrusion", camera="cam_x", code="intrusion.zone_breach", at=_ago(seconds=100))
    await _alert(w, "intrusion", camera="cam_z", code="intrusion.zone_breach", at=_ago(seconds=90))
    assert (await _place(w))["opened"] == 2, "nothing says these two cameras are near each other"

    low, high = sorted((w["cam_x"], w["cam_y"]), key=str)
    await _sql("INSERT INTO security_camera_links (tenant_id, camera_a, camera_b, walk_seconds) VALUES (:t,:a,:b,40)",
               {"t": w["tenant"], "a": low, "b": high})
    await _alert(w, "intrusion", camera="cam_y", code="intrusion.zone_breach", at=_ago(seconds=40))
    result = await _place(w)
    assert result["joined"] == 1 and result["opened"] == 0
    joined = [l for s in await _situations(w) for l in await _links(s["id"]) if l["method"] != "FIRST_EVENT"]
    assert [l["method"] for l in joined] == ["ADJACENT_CAMERA"] and float(joined[0]["confidence"]) == 0.75


# ─── E. The schema ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_situation_tables_are_tenant_isolated_and_forced():
    rows = await _sql(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies "
        "  FROM pg_class c WHERE c.relname IN ('security_situations','security_situation_events',"
        "       'security_camera_links') ORDER BY 1")
    assert len(rows) == 3
    assert all(r["relrowsecurity"] and r["relforcerowsecurity"] and r["policies"] == 1 for r in rows)


@pytest.mark.asyncio
async def test_an_event_is_in_one_situation_at_most_and_a_link_without_a_reason_is_refused():
    w = await _world()
    await _alert(w, "intrusion", code="intrusion.zone_breach", at=_ago(seconds=60))
    await _place(w)
    (s,) = await _situations(w)
    event = (await _events(w))[0]["id"]
    other = uuid.uuid4()
    await _sql("INSERT INTO security_situations (id, tenant_id, situation_number, title, severity, started_at, "
               "    last_event_at) VALUES (:i,:t,'SIT-X-1','Another','low',now(),now())",
               {"i": other, "t": w["tenant"]})
    insert = ("INSERT INTO security_situation_events (tenant_id, situation_id, event_id, method, reason, confidence) "
              "VALUES (:t,:s,:e,:m,:r,:c)")
    spare = uuid.uuid4()
    await _sql("INSERT INTO security_events (id, tenant_id, source_type, source_table, source_id, event_type, "
               "    occurred_at, severity, title) VALUES (:i,:t,'OTHER','alerts',:i,'x',now(),'low','spare')",
               {"i": spare, "t": w["tenant"]})
    ok = {"t": w["tenant"], "s": other, "e": spare, "m": "SAME_IDENTITY", "r": "The same plate.", "c": 0.9}
    for bad in ({"e": event}, {"r": "   "}, {"m": "LOOKS_SIMILAR"}, {"c": 1.4}):
        with pytest.raises(Exception):
            await _sql(insert, {**ok, **bad})
    await _sql(insert, ok)
    low, high = sorted((w["cam_a"], w["cam_b"]), key=str)
    with pytest.raises(Exception):
        await _sql("INSERT INTO security_camera_links (tenant_id, camera_a, camera_b, walk_seconds) "
                   "VALUES (:t,:a,:b,30)", {"t": w["tenant"], "a": high, "b": low})
    await _sql("DELETE FROM security_situations WHERE id = :s", {"s": s["id"]})
    assert await _sql("SELECT 1 FROM security_situation_events WHERE situation_id = :s", {"s": s["id"]}) == []
    assert (await _events(w))[0]["id"] == event, "removing a situation does not remove what was reported"
