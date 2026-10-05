"""AI security intelligence, phase 10: a drone look a person asked for, and what a patrol recorded.

  A — The rules, with nothing running: what a look is as an event, why it
      belongs with a situation, what asking takes, what it is worth
  B — A real simulated flight: an officer asks it to hold and look again;
      what it sees comes back, the situation is assessed again
  C — Starting a mission the site already has; refusals; approval
  D — What a virtual patrol recorded, as context
  E — The schema, and who may see what

The claims this phase makes, each with tests: a drone looks only because a
person decided it should and said how; the asking goes through the drone
module's own functions under that person's own drone permission, and that
module's refusal is the step's record; what the drone saw comes back as an
event in the same situation, which is then assessed again; and nothing seen
after a person closed a matter is hidden inside it.
"""
from __future__ import annotations

import importlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.services import drone_runner as runner
from app.services import intel_actions
from app.services import intel_context as ctx
from app.services import intel_correlation as corr
from app.services import intel_decisions as dec
from app.services import intel_drone as drone
from app.services import intel_events as events
from app.services import intel_recommend as rec
from app.services import intel_risk as risk
from app.services import intel_runner
from app.routers import drone_operations
from tests.test_drone_ai_pipeline import _ai, _detect, _events as _sightings, _world as _drone_world
from tests.test_drone_api import ADMIN, GUARD, OPERATOR, VIEWER, _client, _run, _sql
from tests.test_drone_edge_sync import _session
from tests.test_intel_context import NIGHT, _event as _ctx_event, _facts, _texts
from tests.test_intel_decisions import ALERTS, _facts as _dfacts
from tests.test_intel_events import _alert, _world as _intel_world
from tests.test_intel_recommend import _a, _can, _steps
from tests.test_intel_risk import _context, _ev, _points, _situation as _sit
from tests.test_intel_timeline import without_docstrings

BASE = "/api/v1/security-intelligence"
SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"
DE, SESSION, MISSION = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── A. The rules ────────────────────────────────────────────────────────────

def _look_row(**over) -> dict:
    """A finished look, joined to its sighting, as the source select returns it."""
    return {"id": uuid.uuid4(), "event_id": DE, "session_id": SESSION, "hold_seconds": 30, "detections_before": 3,
            "risk_before": "MEDIUM", "completed_at": NIGHT, "requested_by_user_id": uuid.uuid4(),
            "result": {"detections_added": 4, "risk_before": "MEDIUM", "risk_after": "HIGH"},
            "site_id": uuid.uuid4(), "drone_id": uuid.uuid4(), "module_type": "intrusion",
            "label": "Possible unauthorised person", "zone_name": "Loading Bay", "zone_type": "RESTRICTED",
            "risk_level": "HIGH", "risk_score": 62, "ai_confidence": 0.91, "verification_state": "VERIFIED",
            "estimated_latitude": 1.301, "estimated_longitude": 103.8, "drone_latitude": 1.3, "drone_longitude": 103.8,
            **over}


def test_what_a_drone_saw_on_a_second_look_is_an_event_of_its_own():
    row = _look_row()
    n = events.from_drone_look(row)
    assert (n.source_type, n.source_table, n.event_type) == ("DRONE_PATROL", "drone_verification_requests",
                                                              "drone.verification")
    assert n.source_id == row["id"] and n.occurred_at == NIGHT and n.site_id == row["site_id"]
    assert n.title == "Drone looked again: 4 more detection(s) — Possible unauthorised person"
    assert n.severity == "high", "the drone module's own risk after the look"
    assert (n.latitude, n.longitude, n.location_label) == (1.301, 103.8, "Loading Bay")
    assert n.subject_kind == "PERSON" and n.confidence == 0.91
    a = n.attributes
    assert a["drone_event_id"] == str(DE) and a["session_id"] == str(SESSION) and a["hold_seconds"] == 30
    assert (a["detections_added"], a["risk_before"], a["drone_risk_level"], a["drone_risk_score"]) == (4, "MEDIUM",
                                                                                                         "HIGH", 62.0)
    # A new observation of the same thing, not a second record of its alert:
    # carrying the alert would fold it away as a duplicate.
    assert n.alert_id is None and n.incident_id is None and n.detection_id is None


def test_a_look_that_saw_nothing_more_is_information_not_an_alarm():
    n = events.from_drone_look(_look_row(result={"detections_added": 0, "risk_before": "HIGH", "risk_after": "HIGH"}))
    assert n.severity == "info"
    assert n.title == "Drone looked again and saw nothing more — Possible unauthorised person"
    assert n.attributes["detections_added"] == 0
    stringly = events.from_drone_look(_look_row(result=json.dumps({"detections_added": 2, "risk_after": "MEDIUM"})))
    assert stringly.severity == "medium" and stringly.attributes["detections_added"] == 2
    bare = events.from_drone_look(_look_row(label=None, zone_name=None, result=None, estimated_latitude=None,
                                            estimated_longitude=None))
    assert bare.title.endswith("— drone sighting: intrusion") and bare.location_label is None
    assert (bare.latitude, bare.longitude) == (1.3, 103.8), "where the drone was, when where it looked is not known"


def test_looks_are_read_after_sightings_and_only_finished_ones_of_verified_sightings():
    names = [s.name for s in events.SOURCES]
    assert names.index("drone_looks") == names.index("drone_events") + 1
    source = next(s for s in events.SOURCES if s.name == "drone_looks")
    assert source.time_column == "completed_at" and source.normalise is events.from_drone_look
    sql = " ".join(source.select.split())
    assert "v.status = 'COMPLETED'" in sql and "de.verification_state = 'VERIFIED'" in sql
    assert "source_table = 'drone_verification_requests'" in sql


def _as_event(n: events.Normalised) -> dict:
    return {"id": uuid.uuid4(), "source_table": n.source_table, "source_id": n.source_id, "attributes": n.attributes}


def test_a_look_belongs_with_what_it_looked_at_and_says_why():
    matched = uuid.uuid4()
    link = corr.asked_link(_as_event(events.from_drone_look(_look_row())), matched)
    assert (link.method, link.confidence, link.matched_event_id, link.duplicate) == ("DRONE_LOOK", 0.95, matched, False)
    assert link.reason == ("A person asked the drone to hold for 30 s and look again at this sighting: it saw 4 more "
                           "detection(s). The drone module's own risk went from MEDIUM to HIGH.")
    nothing = corr.asked_link(_as_event(events.from_drone_look(
        _look_row(result={"detections_added": 0, "risk_before": "HIGH", "risk_after": "HIGH"}, risk_before="HIGH"))))
    assert nothing.reason == ("A person asked the drone to hold for 30 s and look again at this sighting: it saw "
                              "nothing more. The drone module's own risk stayed HIGH.")
    attrs_as_text = {"source_table": "drone_verification_requests",
                     "attributes": json.dumps({"detections_added": 1, "drone_event_id": str(DE)})}
    assert corr.asked_link(attrs_as_text).reason.endswith("it saw 1 more detection(s).")


def test_a_sighting_from_a_flight_a_person_started_is_linked_for_that_reason_and_less_surely():
    sighting = {"source_table": "drone_events", "attributes": {"session_id": str(SESSION), "module_type": "intrusion"}}
    link = corr.asked_link(sighting)
    assert (link.method, link.confidence) == ("DRONE_LOOK", 0.6)
    assert "the flight a person started to look at this situation" in link.reason
    assert "may be the same matter, or something else it passed" in link.reason, "it does not claim more than it knows"
    assert link.confidence >= corr.MIN_LINK
    # Nothing else is a look anybody asked for.
    for other in ({"source_table": "alerts", "attributes": {"session_id": str(SESSION)}},
                  {"source_table": "drone_events", "attributes": {}},
                  {"source_table": "virtual_patrol_session_answers", "attributes": {}}):
        assert corr.asked_link(other) is None


def test_asking_a_drone_is_planned_only_from_the_officers_own_choice():
    def steps(drone_):
        return [(s.action, s.permission, s.target_type, s.target_id)
                for s in dec.plan("VERIFY_WITH_DRONE", alerts=ALERTS, incident=None, drone=drone_)]

    assert steps(None) == [], "no choice made: a record, and the officer flies it themselves"
    assert steps({"event_id": str(DE), "hold_seconds": 20}) == [("DRONE_HOLD", "drone:operate", "drone_event", str(DE))]
    assert steps({"mission_id": str(MISSION)}) == [("DRONE_LAUNCH", "drone:mission:execute", "drone_mission",
                                                    str(MISSION))]
    # A drone choice changes nothing about any other decision.
    for action in dec.DECISIONS:
        if action != "VERIFY_WITH_DRONE":
            with_, without = (dec.plan(action, alerts=ALERTS, incident=None, drone=d)
                              for d in ({"event_id": str(DE)}, None))
            assert with_ == without, action
    assert dec.drone_choice(None) is None and dec.drone_choice({}) is None
    assert dec.drone_choice({"guard_user_id": "x"}) is None
    assert dec.drone_choice({"drone_event_id": str(DE), "hold_seconds": 45}) == {"event_id": str(DE),
                                                                                "hold_seconds": 45}
    assert dec.drone_choice({"drone_mission_id": str(MISSION)}) == {"mission_id": str(MISSION)}


def test_asking_needs_the_drone_modules_own_permission_not_one_of_this_layers():
    suggested = [{"id": uuid.uuid4(), "rank": 1, "action": "VERIFY_WITH_DRONE", "available": True}]
    f = _dfacts(level="MEDIUM", current=suggested)
    base = dict(situation={"closed_at": None}, f=f, roles=None, role_id=OPERATOR)
    mine = {"intel:decide", "intel:override", "alert:acknowledge"}
    hold, launch = {"event_id": str(DE)}, {"mission_id": str(MISSION)}

    assert dec.check("VERIFY_WITH_DRONE", mine=mine, **base).refusal is None, "recording it asks nothing of a drone"
    assert dec.check("VERIFY_WITH_DRONE", mine=mine, drone=hold, **base).refusal == (
        403, "Carrying this out needs the permission drone:operate.")
    assert dec.check("VERIFY_WITH_DRONE", mine=mine, drone=launch, **base).refusal == (
        403, "Carrying this out needs the permission drone:mission:execute.")
    assert dec.check("VERIFY_WITH_DRONE", mine=mine | {"drone:operate"}, drone=hold, **base).refusal is None
    # Holding a flight is not starting one.
    assert dec.check("VERIFY_WITH_DRONE", mine=mine | {"drone:operate"}, drone=launch, **base).refusal is not None
    ok = dec.check("VERIFY_WITH_DRONE", mine=mine | {"drone:mission:execute"}, drone=launch, **base)
    assert ok.refusal is None and [s.action for s in ok.steps] == ["DRONE_LAUNCH"] and ok.basis == "FOLLOWED"


def test_the_drone_steps_go_through_the_drone_modules_own_endpoint_functions():
    assert intel_actions.THROUGH["DRONE_HOLD"] == "app.routers.drone_operations.verify_with_drone"
    assert intel_actions.THROUGH["DRONE_LAUNCH"] == "app.routers.drone_planning.run_mission"
    for step in ("DRONE_HOLD", "DRONE_LAUNCH"):
        module, name = intel_actions.THROUGH[step].rsplit(".", 1)
        assert callable(getattr(importlib.import_module(module), name))
    # The hold a decision may ask for is the drone module's own range, not a wider one.
    field = drone_operations.VerifyIn.model_fields["hold_seconds"]
    bounds = {type(m).__name__: getattr(m, "ge", None) or getattr(m, "le", None) for m in field.metadata}
    assert (bounds["Ge"], bounds["Le"], field.default) == (drone.HOLD_MIN, drone.HOLD_MAX, drone.HOLD_DEFAULT)
    assert drone.hold_seconds(None) == 30 and drone.hold_seconds(5) == 5 and drone.hold_seconds(120) == 120
    for bad in (4, 121, 0, -1, 30.0, "30", True):
        with pytest.raises(ValueError):
            drone.hold_seconds(bad)
    assert (drone.HOLD_PERMISSION, drone.LAUNCH_PERMISSION) == ("drone:operate", "drone:mission:execute")
    assert drone.LAUNCHABLE == rec.DRONE_LAUNCHABLE, "one list of what 'ready to fly' means"


def test_whether_a_flight_could_hold_or_a_mission_could_start_is_a_first_answer_with_its_reason():
    flying = {"session_id": SESSION, "flight_status": "ACTIVE", "holding": False}
    assert drone.hold_state(flying) == (True, None)
    assert drone.hold_state({**flying, "holding": True}) == (False, "This flight is already holding for a look.")
    assert drone.hold_state({**flying, "flight_status": "RETURNING"}) == (
        False, "The flight is returning; only an active flight can hold to look again.")
    assert drone.hold_state({**flying, "flight_status": "COMPLETED"})[0] is False
    assert drone.hold_state({"session_id": None}) == (False, "This sighting did not come from a flight.")
    assert drone.hold_state({"session_id": SESSION, "flight_status": None}) == (False, "The flight no longer exists.")

    ready = {"drone_id": uuid.uuid4(), "drone_status": "READY", "communication_status": "OK", "drone_committed": False}
    assert drone.launch_state(ready) == (True, None)
    for status in ("STANDBY", "CHARGING"):
        assert drone.launch_state({**ready, "drone_status": status})[0] is True
    assert drone.launch_state({**ready, "drone_committed": True}) == (
        False, "Its drone is already committed to another flight.")
    assert drone.launch_state({**ready, "drone_status": "MISSION_ACTIVE"}) == (False, "Its drone is mission active.")
    assert drone.launch_state({**ready, "communication_status": "LOST"}) == (False, "Its drone is not in contact.")
    assert drone.launch_state({**ready, "drone_id": None}) == (False, "The mission has no drone.")


def test_why_pre_flight_stopped_a_flight_is_said_in_the_drone_modules_own_words():
    preflight = {"passed": False, "blocking": ["BATTERY", "HEARTBEAT"], "checks": [
        {"code": "BATTERY", "label": "Battery", "passed": False, "severity": "BLOCK", "detail": "Battery 12% is below 30%"},
        {"code": "WEATHER", "label": "Weather", "passed": False, "severity": "WARN", "detail": "Wind is high"},
        {"code": "GPS", "label": "GPS", "passed": True, "severity": "BLOCK", "detail": "GPS fix is good"},
        {"code": "HEARTBEAT", "label": "Heartbeat", "passed": False, "severity": "BLOCK", "detail": ""}]}
    assert drone.blocked_reason(preflight) == "Battery 12% is below 30%; Heartbeat"
    assert drone.blocked_reason({"blocking": ["BATTERY"]}) == "BATTERY"
    assert drone.blocked_reason(None) == "no reason was given"


def _look(added: int, at: datetime = NIGHT, hold: int | None = 30, **over) -> dict:
    return _ev(source_type="DRONE_PATROL", event_type="drone.verification", severity="info", occurred_at=at,
               method="DRONE_LOOK", confidence=None,
               attributes={"module_type": "intrusion", "detections_added": added, "hold_seconds": hold}, **over)


def test_a_second_look_is_worth_a_little_either_way_and_only_the_latest_counts():
    sighting = _ev(source_type="DRONE_PATROL", event_type="drone.intrusion")
    before = risk.assess(_sit(), [sighting], _context())
    assert "DRONE" not in _points(before), "no look, nothing said about one"

    more = risk.assess(_sit(), [sighting, _look(3)], _context())
    assert _points(more)["DRONE"] == risk.LOOK_POINTS == 5
    assert more.risk_score == before.risk_score + 5
    assert {"factor": "DRONE", "points": 5, "detail": "A drone held for 30 s and looked again, as a person asked: "
                                                      "3 more detection(s) of the same thing."} in more.risk_factors
    none = risk.assess(_sit(), [sighting, _look(0)], _context())
    assert _points(none)["DRONE"] == -5 and none.risk_score == before.risk_score - 5
    assert any(f["detail"] == "A drone held for 30 s and looked again, as a person asked: it saw nothing more."
               for f in none.risk_factors)
    # Two looks: what the later one saw is what stands.
    later = NIGHT + timedelta(minutes=3)
    assert _points(risk.assess(_sit(), [sighting, _look(3), _look(0, later)], _context()))["DRONE"] == -5
    assert _points(risk.assess(_sit(), [sighting, _look(0), _look(2, later)], _context()))["DRONE"] == 5
    untimed = risk.assess(_sit(), [sighting, _look(1, hold=None)], _context())
    assert any("A drone held and looked again, as a person asked" in f["detail"] for f in untimed.risk_factors)
    # A look is the same kind of source as the sighting: it does not make the
    # situation "reported by two kinds of source".
    assert "CORROBORATION" not in _points(more)
    # And a tenant weighs it with the weight it gives the drone's own judgement.
    assert _points(risk.assess(_sit(), [sighting, _look(3)], _context(), weights={"DRONE": 2}))["DRONE"] == 10
    assert more.same_answer_as({"risk_score": before.risk_score, "label": before.label,
                                "risk_factors": before.risk_factors}) is False, "so it is assessed again"


def test_once_a_drone_has_looked_looking_again_is_not_suggested():
    site = dict(drones_at_site=1, drones_ready=1)
    for level in ("MEDIUM", "HIGH", "CRITICAL"):
        assert "VERIFY_WITH_DRONE" in _steps(rec.recommend(_a(level=level), _can(**site)))
        after = _steps(rec.recommend(_a(level=level), _can(**site, drone_looked=True)))
        assert "VERIFY_WITH_DRONE" not in after and after, level
    assert rec.Availability().drone_looked is False
    assert risk.ENGINE_VERSION == rec.ENGINE_VERSION == "rules-2", "the rules changed, and the records say which made them"


def test_the_last_virtual_patrol_check_of_a_camera_is_said_as_what_was_recorded():
    def said(check, at=NIGHT):
        c = ctx.build(_ctx_event(at), _facts(last_patrol_check=check))
        return c, _texts(c, "operations")

    base = {"at": NIGHT - timedelta(minutes=40), "patrol_number": "VP-0042", "status": "COMPLETED", "answered": 3,
            "exceptions": 0, "noted": False, "snapshot": True}
    c, texts = said(base)
    assert texts == ["A virtual patrol checked this camera 40 min before (VP-0042): 3 question(s) answered, "
                     "nothing reported"]
    assert c["operations"]["last_patrol_check"] == {
        "at": base["at"].isoformat(), "patrol_number": "VP-0042", "status": "COMPLETED", "answered": 3,
        "exceptions": 0, "noted": False, "snapshot": True}
    assert c["statements"][-1]["source"] == "virtual_patrol_session_answers" or any(
        s["source"] == "virtual_patrol_session_answers" for s in c["statements"])
    assert "a virtual patrol" not in " ".join(c["expected"]).lower(), "a clean check is not a reason to expect anything"

    _, found = said({**base, "exceptions": 2, "at": NIGHT - timedelta(hours=5)})
    assert found == ["A virtual patrol checked this camera 5 h before (VP-0042) and reported 2 exception(s)"]
    _, dark = said({**base, "status": "CAMERA_UNAVAILABLE", "patrol_number": None, "at": NIGHT - timedelta(minutes=2)})
    assert dark == ["A virtual patrol could not see this camera 2 min before: the camera was unavailable"]
    none, texts = said(None)
    assert none["operations"]["last_patrol_check"] is None and texts == []


def test_a_patrol_exception_carries_what_the_officer_was_asked_answered_and_noted():
    taken = NIGHT - timedelta(seconds=30)
    row = {"id": uuid.uuid4(), "answered_at": NIGHT, "answered_by_user_id": uuid.uuid4(), "incident_id": None,
           "exception_reason": "Gate left open", "question_text": "Is the rear gate closed?", "answer_text": "NO",
           "failure_action": "CREATE_INCIDENT", "camera_id": uuid.uuid4(), "camera_name": "Rear gate",
           "session_id": uuid.uuid4(), "session_camera_id": uuid.uuid4(), "officer_notes": "Visibility poor, lamp out",
           "has_snapshot": True, "snapshot_taken_at": taken, "site_id": uuid.uuid4(), "patrol_number": "VP-0042",
           "camera_latitude": 1.31, "camera_longitude": 103.81}
    a = events.from_patrol_exception(row).attributes
    assert (a["question"], a["answer"], a["exception_reason"]) == ("Is the rear gate closed?", "NO", "Gate left open")
    assert a["observation"] == "Visibility poor, lamp out" and a["failure_action"] == "CREATE_INCIDENT"
    assert a["has_snapshot"] is True and a["snapshot_taken_at"] == taken.isoformat()
    assert a["session_camera_id"] == str(row["session_camera_id"]) and a["patrol_number"] == "VP-0042"
    # What the patrol did not record is left out, not filled in.
    bare = events.from_patrol_exception({**row, "answer_text": None, "officer_notes": None, "has_snapshot": False,
                                         "snapshot_taken_at": None, "session_camera_id": None}).attributes
    assert not {"answer", "observation", "has_snapshot", "snapshot_taken_at", "session_camera_id"} & set(bare)
    long = events.from_patrol_exception({**row, "officer_notes": "x" * 900}).attributes
    assert len(long["observation"]) == 500


def test_no_path_of_the_layer_is_one_the_drone_module_would_take_for_its_own():
    """Every path with "drone" in it is the drone module's: its own tests walk
    them and hold each to that module's permissions. The layer's paths are held
    to `intel:*`, so none of them may look like one of those."""
    from app.routers import security_decisions, security_intelligence

    paths = [r.path for router in (security_decisions.router, security_intelligence.router) for r in router.routes]
    assert len(paths) >= 30 and all(p.startswith("/api/v1/security-intelligence") for p in paths)
    assert [p for p in paths if "drone" in p] == []
    assert "/api/v1/security-intelligence/situations/{situation_id}/aerial" in paths


def test_the_part_of_the_layer_that_reads_about_drones_writes_nothing():
    code = without_docstrings(SERVICES / "intel_drone.py")
    assert "FROM drone_verification_requests" in code, "the statements are what is being looked at"
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE)\b", code), "intel_drone.py only reads"
    assert ".commit(" not in code
    # And the runner still cannot act: it imports neither the reader's caller nor the actor.
    for name in ("intel_runner", "intel_events", "intel_correlation", "intel_risk", "intel_recommend",
                 "intel_context"):
        text_ = (SERVICES / f"{name}.py").read_text(encoding="utf-8")
        assert "intel_actions" not in without_docstrings(SERVICES / f"{name}.py"), name
        assert "intel_drone" not in text_, name
        assert "drone_response" not in text_ and "drone_planning" not in text_ and "drone_operations" not in text_, name


# ─── B. A real flight, asked to look again ───────────────────────────────────

async def _user(w: dict, role: int, key: str) -> None:
    w[key] = uuid.uuid4()
    await _sql("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
               "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
               {"i": w[key], "t": w["tenant"], "r": role, "e": f"{key}-{w[key].hex[:8]}@look.test",
                "n": f"{key.title()} User"})
    w[f"h_{key}"] = {"Authorization": f"Bearer {create_access_token(str(w[key]), str(w['tenant']), role)}"}


async def _pass(w: dict) -> None:
    """Read, place, assess, suggest — on the real clock, as the runner does."""
    now = _now()
    await events.ingest_tenant(AsyncSessionLocal, w["tenant"], now=now)
    await corr.correlate_tenant(AsyncSessionLocal, w["tenant"], now)
    await risk.assess_tenant(AsyncSessionLocal, w["tenant"], now)
    await rec.recommend_tenant(AsyncSessionLocal, w["tenant"], now)


async def _tick(at: datetime) -> None:
    await runner.run_tick(AsyncSessionLocal, runner.ListPublisher(), now=at)


async def _fly(w: dict, sid: str) -> datetime:
    """Fly a launched session with the real simulator until the drone is
    active over the zone. Returns the simulated time it got there."""
    t = _now()
    for _ in range(25):
        t += timedelta(seconds=2)
        await _tick(t)
        s = await _session(sid)
        d = (await _sql("SELECT current_latitude FROM drones WHERE id = :d", {"d": w["drone"]}))[0]
        if s["status"] == "ACTIVE" and d["current_latitude"] and d["current_latitude"] > 1.3005:
            return t
    raise AssertionError("the drone never reached the zone")


async def _slow(w: dict) -> None:
    """Slow enough to hold, and freshly heard from, so pre-flight passes."""
    await _run([("UPDATE drone_provider_configs SET config = '{\"speed_factor\": 1}' WHERE id = :p", {"p": w["provider"]}),
                ("UPDATE drones SET last_heartbeat_at = now() WHERE id = :d", {"d": w["drone"]})])


async def _seen(w: dict, sid: str, t: datetime) -> dict:
    """The drone sees a person for long enough that its own module verifies it."""
    for s in (0, 2, 4):
        await _detect(w, t + timedelta(seconds=s), "intrusion")
    await _ai(t + timedelta(seconds=5))
    await _ai(t + timedelta(seconds=6))
    [e] = await _sightings(sid)
    assert e["verification_state"] == "VERIFIED", e["verification_state"]
    return e


async def _sighted() -> tuple[dict, str, datetime, dict, dict]:
    """A real flight that has seen something, read by the layer: the world,
    the flight, the simulated time, the drone's sighting, the situation."""
    w = await _drone_world()
    await _slow(w)
    async with _client() as c:
        r = await c.post(f"/api/v1/drone-missions/{w['mission']}/run", headers=w["h_admin"])
    sid = r.json()["session"]["id"]
    t = await _fly(w, sid)
    e = await _seen(w, sid, t)
    await _pass(w)
    return w, sid, t, e, await _situation_of(w, e["id"])


async def _situation_of(w: dict, source_id) -> dict:
    """The situation that holds the event made from this source record."""
    rows = await _sql("""
        SELECT s.* FROM security_situations s
          JOIN security_situation_events l ON l.situation_id = s.id
          JOIN security_events e ON e.id = l.event_id
         WHERE s.tenant_id = :t AND e.source_id = :i
    """, {"t": w["tenant"], "i": source_id})
    assert len(rows) == 1, f"{len(rows)} situations hold {source_id}"
    return dict(rows[0])


async def _recs(s: dict) -> list[dict]:
    return [dict(r) for r in await _sql(
        "SELECT * FROM security_recommendations WHERE assessment_id = :a ORDER BY rank", {"a": s["assessment_id"]})]


async def _looks(e: dict) -> list[dict]:
    return [dict(r) for r in await _sql(
        "SELECT * FROM drone_verification_requests WHERE event_id = :e ORDER BY created_at", {"e": e["id"]})]


async def _held(w: dict, t: datetime, *, sees: int = 3) -> datetime:
    """The flight pauses, holds, sees `sees` more detections, and the hold ends."""
    await _tick(t + timedelta(seconds=7))
    await _tick(t + timedelta(seconds=8))
    for i in range(sees):
        await _detect(w, t + timedelta(seconds=9 + 2 * i), "intrusion", 0.9)
    await _ai(t + timedelta(seconds=15))
    await _tick(t + timedelta(seconds=19))
    await _tick(t + timedelta(seconds=20))
    return t + timedelta(seconds=20)


def _decide(c, w: dict, s: dict, who: str, **body):
    return c.post(f"{BASE}/situations/{s['id']}/decisions", headers=w[f"h_{who}"],
                  json={"action": "VERIFY_WITH_DRONE", **body})


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


@pytest.mark.asyncio
async def test_an_officer_asks_the_flight_to_look_again_and_what_it_sees_comes_back_to_be_assessed():
    w, sid, t, e, s = await _sighted()
    first = await _recs(s)
    offered = next(r for r in first if r["action"] == "VERIFY_WITH_DRONE")
    assert offered["available"] and _j(offered["supporting"])["facts"] == ["A drone is in the air at the site."]

    async with _client() as c:
        picture = (await c.get(f"{BASE}/situations/{s['id']}/aerial", headers=w["h_op"])).json()
        r = await _decide(c, w, s, "op", drone_event_id=str(e["id"]), hold_seconds=10, note="confirm the person")
    assert r.status_code == 201, r.text
    d = r.json()
    assert (d["action"], d["basis"], d["authority"]) == ("VERIFY_WITH_DRONE", "FOLLOWED", "ALONE")
    assert d["params"] == {"drone_event_id": str(e["id"]), "hold_seconds": 10}
    [step] = d["actions"]
    assert (step["action"], step["result"], step["through"], step["target_type"]) == (
        "DRONE_HOLD", "OK", "app.routers.drone_operations.verify_with_drone", "drone_look")
    assert step["detail"].startswith("The flight was asked to hold for 10 s and look again.")

    # Before: the screen said this sighting's flight could be asked.
    [seen] = picture["sightings"]
    assert seen["drone_event_id"] == str(e["id"]) and seen["can_hold"] is True and seen["flight_status"] == "ACTIVE"
    assert picture["may"] == {"hold": True, "launch": True, "see_missions": True} and picture["asked"] == []

    # It went through the drone module, as that module's own button does: its
    # request, its queued pause, its audit entry — under the officer's name.
    [look] = await _looks(e)
    assert str(look["id"]) == step["target_id"] and look["requested_by_user_id"] == w["operator"]
    assert (look["status"], look["hold_seconds"], look["reason"]) == ("REQUESTED", 10, "confirm the person")
    cmds = await _sql("SELECT command, requested_by_user_id FROM drone_session_commands WHERE session_id = :s",
                      {"s": uuid.UUID(sid)})
    assert [(c_["command"], c_["requested_by_user_id"]) for c_ in cmds] == [("PAUSE", w["operator"])]
    audits = {a["action"]: a for a in await _sql(
        "SELECT action, user_id FROM audit_logs WHERE tenant_id = :t", {"t": w["tenant"]})}
    assert audits["drone.event.verify_with_drone"]["user_id"] == w["operator"]
    assert audits["intel.action.drone_hold"]["user_id"] == w["operator"]
    assert audits["intel.decision.record"]["user_id"] == w["operator"]

    await _held(w, t)
    [look] = await _looks(e)
    assert look["status"] == "COMPLETED" and _j(look["result"])["detections_added"] == 3
    assert (await _session(sid))["status"] in ("ACTIVE", "RETURNING", "COMPLETED"), "the flight never resumed"

    await _pass(w)
    after = await _situation_of(w, look["id"])
    assert after["id"] == s["id"], "what the drone saw came back to the situation it was asked about"
    assert after["event_count"] == s["event_count"] + 1 and after["duplicate_count"] == s["duplicate_count"]
    link = dict((await _sql("""
        SELECT l.method, l.reason, l.confidence, l.is_duplicate, m.source_id AS matched
          FROM security_situation_events l JOIN security_events e ON e.id = l.event_id
          LEFT JOIN security_events m ON m.id = l.matched_event_id
         WHERE e.source_id = :i""", {"i": look["id"]}))[0])
    assert (link["method"], float(link["confidence"]), link["is_duplicate"], link["matched"]) == (
        "DRONE_LOOK", 0.95, False, e["id"])
    assert link["reason"].startswith("A person asked the drone to hold for 10 s and look again at this sighting: "
                                     "it saw 3 more detection(s).")

    # Assessed again, on what came back — and looking again is no longer suggested.
    assert after["assessment_id"] != s["assessment_id"]
    a2 = dict((await _sql("SELECT * FROM security_assessments WHERE id = :a", {"a": after["assessment_id"]}))[0])
    assert a2["sequence"] == 2 and a2["event_count"] == after["event_count"] and a2["engine_version"] == "rules-2"
    assert any(f["factor"] == "DRONE" and f["points"] == 5 and "3 more detection(s) of the same thing" in f["detail"]
               for f in _j(a2["risk_factors"]))
    second = await _recs(after)
    assert second and "VERIFY_WITH_DRONE" not in [r["action"] for r in second]

    async with _client() as c:
        listed = (await c.get(f"{BASE}/situations?open=true", headers=w["h_op"])).json()["items"]
        detail = (await c.get(f"{BASE}/situations/{s['id']}", headers=w["h_op"])).json()
        picture = (await c.get(f"{BASE}/situations/{s['id']}/aerial", headers=w["h_op"])).json()
    mine = next(x for x in listed if x["id"] == str(s["id"]))
    assert mine["reassessed_since_decision"] is True and mine["decision_status"] == "IN_HAND"
    assert detail["reassessed_since_decision"] is True
    came_back = next(x for x in detail["events"] if x["event_type"] == "drone.verification")
    assert came_back["method"] == "DRONE_LOOK" and came_back["attributes"]["detections_added"] == 3
    [asked] = picture["asked"]
    assert (asked["action"], asked["result"], asked["decision_id"]) == ("DRONE_HOLD", "OK", d["id"])
    assert asked["look"]["status"] == "COMPLETED" and asked["look"]["result"]["detections_added"] == 3
    assert asked["flight"] is None and picture["other_looks"] == []

    # And the timeline tells it as it was: a person decided, the platform asked
    # the flight, the drone's look came back as a source's report, and the layer
    # assessed again. (The simulated flight's clock runs ahead of the real one
    # the decision was stamped by, so only what must be adjacent is held to be.)
    async with _client() as c:
        told = (await c.get(f"{BASE}/situations/{s['id']}/timeline", headers=w["h_op"])).json()["entries"]
    i = next(n for n, x in enumerate(told) if x["kind"] == "DECISION")
    assert (told[i]["actor"], told[i]["title"]) == ("PERSON", "Decided: verify with a drone")
    assert (told[i + 1]["actor"], told[i + 1]["title"]) == ("PLATFORM", "Flight asked to hold and look again")
    assert told[i + 1]["through"] == "app.routers.drone_operations.verify_with_drone"
    looked = [x for x in told if x["kind"] == "EVENT" and x["title"].startswith("Drone looked again: 3 more")]
    assert len(looked) == 1 and looked[0]["actor"] == "SOURCE" and looked[0]["source_type"] == "DRONE_PATROL"
    assert looked[0]["detail"].startswith("A person asked the drone to hold for 10 s and look again")
    assert [x["sequence"] for x in told if x["kind"] == "ASSESSMENT"] == [1, 2]
    assert len([x for x in told if x["kind"] == "RECOMMENDATION"]) == 2 and all(
        x["is_decision"] is False for x in told if x["kind"] == "RECOMMENDATION")


@pytest.mark.asyncio
async def test_the_camera_of_a_drone_that_saw_it_is_one_to_open_only_while_that_drone_is_in_the_air():
    w, sid, t, e, s = await _sighted()
    first = await _recs(s)
    looking = next(r for r in first if r["action"] == "VIEW_CAMERA")
    assert _j(looking["supporting"])["cameras"] == [
        {"id": str(w["camera"]), "name": "Drone One camera", "relation": "drone", "state": "not_known"}]
    assert "VERIFY" not in [r["action"] for r in first], "there is a camera that shows the place: the drone's own"

    async def cameras() -> tuple:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            events = [dict(r) for r in (await db.execute(text(
                "SELECT ev.id, ev.occurred_at, ev.camera_id, ev.alert_id, ev.incident_id, ev.event_type, ev.drone_id "
                "  FROM security_situation_events l JOIN security_events ev ON ev.id = l.event_id "
                " WHERE l.situation_id = :s"), {"s": s["id"]})).mappings().all()]
            return (await rec.availability(db, s, events, _now())).cameras

    assert [c["relation"] for c in await cameras()] == ["drone"]
    # Landed: its camera now shows a dock, and is not offered as a look at the place.
    await _sql("UPDATE drones SET status = 'READY' WHERE id = :d", {"d": w["drone"]})
    assert await cameras() == ()


@pytest.mark.asyncio
async def test_a_look_that_sees_nothing_more_comes_back_too_and_lowers_the_risk_a_little():
    w, sid, t, e, s = await _sighted()
    async with _client() as c:
        r = await _decide(c, w, s, "op", drone_event_id=str(e["id"]), hold_seconds=10)
    assert r.status_code == 201, r.text
    await _held(w, t, sees=0)
    [look] = await _looks(e)
    assert look["status"] == "COMPLETED" and _j(look["result"])["detections_added"] == 0
    await _pass(w)
    after = await _situation_of(w, look["id"])
    assert after["id"] == s["id"] and after["severity"] == s["severity"], "a look that saw nothing renames nothing"
    assert after["title"] == s["title"]
    assert after["risk_score"] == s["risk_score"] - 5
    a2 = dict((await _sql("SELECT * FROM security_assessments WHERE id = :a", {"a": after["assessment_id"]}))[0])
    assert any(f["factor"] == "DRONE" and f["points"] == -5 and f["detail"].endswith("it saw nothing more.")
               for f in _j(a2["risk_factors"]))


@pytest.mark.asyncio
async def test_when_the_drone_module_refuses_its_reason_is_the_steps_record_and_the_decision_stands():
    w, sid, t, e, s = await _sighted()
    # The drone has since moved on: it is no longer near where it saw this.
    await _sql("UPDATE drone_events SET drone_latitude = drone_latitude + 0.01 WHERE id = :e", {"e": e["id"]})
    async with _client() as c:
        r = await _decide(c, w, s, "op", drone_event_id=str(e["id"]))
    assert r.status_code == 201, r.text
    [step] = r.json()["actions"]
    assert (step["action"], step["result"], step["target_type"], step["target_id"]) == (
        "DRONE_HOLD", "FAILED", "drone_event", str(e["id"]))
    assert step["detail"].startswith("409: The drone is ") and "m from where it saw this" in step["detail"]
    assert await _looks(e) == []
    assert await _sql("SELECT 1 FROM drone_session_commands WHERE session_id = :s", {"s": uuid.UUID(sid)}) == []
    after = dict((await _sql("SELECT * FROM security_situations WHERE id = :s", {"s": s["id"]}))[0])
    assert after["decision_status"] == "IN_HAND", "the person decided; that the drone could not is on the record"
    failed = (await _sql("SELECT detail FROM audit_logs WHERE tenant_id = :t AND action = 'intel.action.drone_hold'",
                         {"t": w["tenant"]}))[0]
    assert _j(failed["detail"])["result"] == "failed"


@pytest.mark.asyncio
async def test_a_choice_that_is_not_this_situations_to_make_is_refused_and_nothing_is_recorded():
    w, sid, t, e, s = await _sighted()
    other = await _drone_world()
    refused = []
    async with _client() as c:
        for body in (
            {"drone_event_id": str(e["id"]), "drone_mission_id": str(w["mission"])},   # both
            {"drone_event_id": str(uuid.uuid4())},                                      # not a sighting here
            {"drone_mission_id": str(other["mission"])},                                # another organisation's
            {"drone_mission_id": str(w["mission"]), "hold_seconds": 20},                # a hold is a flight's
            {"hold_seconds": 20},                                                       # how long, of what?
            {"drone_event_id": str(e["id"]), "hold_seconds": 4},                        # outside the module's range
            {"drone_event_id": str(e["id"]), "hold_seconds": 121},
        ):
            refused.append(await _decide(c, w, s, "op", **body))
        elsewhere = await c.post(f"{BASE}/situations/{s['id']}/decisions", headers=w["h_op"],
                                 json={"action": "MONITOR", "drone_event_id": str(e["id"]),
                                       "reason_code": "OTHER", "note": "x"})
    assert [r.status_code for r in refused] == [422] * 7, [r.text for r in refused]
    assert "Choose one" in refused[0].json()["detail"]
    assert refused[1].json()["detail"] == ("drone_event_id must be a drone sighting that is one of this situation's "
                                           "events.")
    assert refused[2].json()["detail"] == ("drone_mission_id must be a mission that is switched on at this "
                                           "situation's site.")
    assert elsewhere.status_code == 422 and "only with VERIFY_WITH_DRONE" in elsewhere.json()["detail"]
    assert await _sql("SELECT 1 FROM security_decisions WHERE tenant_id = :t", {"t": w["tenant"]}) == []
    assert await _looks(e) == []


@pytest.mark.asyncio
async def test_someone_who_may_decide_but_may_not_operate_a_drone_cannot_ask_one():
    w, sid, t, e, s = await _sighted()
    await _user(w, GUARD, "guard")
    await _run([
        ("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, status) "
         "VALUES (:t,:s,:g,:a,:b,:a,'active')", {"t": w["tenant"], "s": w["site"], "g": w["guard"],
                                                 "a": _now() - timedelta(hours=1), "b": _now() + timedelta(hours=6)}),
        ("INSERT INTO security_decision_policies (tenant_id, roles) VALUES (:t, CAST(:r AS jsonb))",
         {"t": w["tenant"], "r": json.dumps({"5": {"alone": "CRITICAL"}})}),
    ])
    async with _client() as c:
        asking = await _decide(c, w, s, "guard", drone_event_id=str(e["id"]))
        picture = (await c.get(f"{BASE}/situations/{s['id']}/aerial", headers=w["h_guard"])).json()
        recording = await _decide(c, w, s, "guard")
    assert asking.status_code == 403 and asking.json()["detail"] == (
        "Carrying this out needs the permission drone:operate.")
    assert picture["may"] == {"hold": False, "launch": False, "see_missions": False} and picture["missions"] == []
    assert len(picture["sightings"]) == 1, "the sighting is part of the situation they can already see"
    # Recording that a drone should look asks nothing of one.
    assert recording.status_code == 201, recording.text
    [step] = recording.json()["actions"]
    assert (step["action"], step["result"], step["through"]) == ("NONE", "RECORDED", None)
    assert step["detail"].startswith("Recorded. No flight or mission was chosen with the decision")
    assert await _looks(e) == []


@pytest.mark.asyncio
async def test_a_decision_that_needs_approval_asks_nothing_of_the_drone_until_a_second_person_approves():
    w, sid, t, e, s = await _sighted()
    await _sql("INSERT INTO security_decision_policies (tenant_id, roles) VALUES (:t, CAST(:r AS jsonb))",
               {"t": w["tenant"], "r": json.dumps({"4": {"alone": "INFO", "with_approval": "CRITICAL"}})})
    async with _client() as c:
        r = await _decide(c, w, s, "op", drone_event_id=str(e["id"]), hold_seconds=10)
        d = r.json()
        assert r.status_code == 201 and (d["authority"], d["state"]) == ("WITH_APPROVAL", "PENDING_APPROVAL"), r.text
        assert d["actions"] == [] and await _looks(e) == [], "proposed is not asked"
        approved = await c.post(f"{BASE}/decisions/{d['id']}/approve", headers=w["h_admin"])
    assert approved.status_code == 200, approved.text
    [step] = approved.json()["actions"]
    assert (step["action"], step["result"]) == ("DRONE_HOLD", "OK")
    [look] = await _looks(e)
    assert look["requested_by_user_id"] == w["admin"], "it runs under the authority of the one who approved"
    assert look["hold_seconds"] == 10


@pytest.mark.asyncio
async def test_a_look_that_comes_back_after_a_person_closed_the_matter_is_a_new_matter_not_hidden_in_it():
    w, sid, t, e, s = await _sighted()
    async with _client() as c:
        asked = await _decide(c, w, s, "op", drone_event_id=str(e["id"]), hold_seconds=10)
        closed = await c.post(f"{BASE}/situations/{s['id']}/decisions", headers=w["h_admin"],
                              json={"action": "RESOLVE", "reason_code": "ALREADY_HANDLED"})
    assert asked.status_code == 201 and closed.status_code == 201, (asked.text, closed.text)
    await _held(w, t)
    [look] = await _looks(e)
    assert look["status"] == "COMPLETED"
    await _pass(w)
    new = await _situation_of(w, look["id"])
    was = dict((await _sql("SELECT * FROM security_situations WHERE id = :s", {"s": s["id"]}))[0])
    assert new["id"] != s["id"], "something seen after a matter was ended must be seen"
    assert new["closed_at"] is None and new["decision_status"] == "AWAITING" and new["status"] == "ACTIVE"
    assert new["title"].startswith("Drone looked again: 3 more detection(s)")
    assert (was["event_count"], was["status"], was["decision_status"]) == (s["event_count"], "SETTLED", "RESOLVED")


@pytest.mark.asyncio
async def test_a_look_wakes_a_situation_that_had_gone_quiet_and_a_look_asked_elsewhere_joins_it_too():
    w, sid, t, e, s = await _sighted()
    # Asked from the drone module's own screen, not by a decision here.
    async with _client() as c:
        r = await c.post(f"/api/v1/drone-events/{e['id']}/verify-with-drone", headers=w["h_op"],
                         json={"hold_seconds": 10})
    assert r.status_code == 202, r.text
    # The situation goes quiet, as the runner would find it half an hour on.
    await corr.correlate_tenant(AsyncSessionLocal, w["tenant"], _now() + corr.QUIET + timedelta(minutes=2))
    quiet = dict((await _sql("SELECT * FROM security_situations WHERE id = :s", {"s": s["id"]}))[0])
    assert quiet["status"] == "SETTLED" and quiet["closed_at"] is None
    await _held(w, t)
    [look] = await _looks(e)
    await _pass(w)
    after = await _situation_of(w, look["id"])
    assert after["id"] == s["id"] and after["status"] == "ACTIVE" and after["settled_at"] is None
    async with _client() as c:
        picture = (await c.get(f"{BASE}/situations/{s['id']}/aerial", headers=w["h_op"])).json()
    assert picture["asked"] == [], "no decision here asked for it"
    [elsewhere] = picture["other_looks"]
    assert elsewhere["id"] == str(look["id"]) and elsewhere["status"] == "COMPLETED"
    assert elsewhere["result"]["detections_added"] == 3


@pytest.mark.asyncio
async def test_the_runner_itself_brings_the_look_back_says_so_on_the_wire_and_asks_nothing_of_any_drone():
    """The same loop, by the ticks `python -m app.intelligence_main` runs — for
    a tenant that switched the layer on, and with the runner's own publisher."""
    w, sid, t, e, s = await _sighted()
    await _sql("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
               "VALUES (:t,'intel.enabled','true'::jsonb,:u)", {"t": w["tenant"], "u": w["admin"]})
    async with _client() as c:
        r = await _decide(c, w, s, "op", drone_event_id=str(e["id"]), hold_seconds=10)
    assert r.status_code == 201, r.text
    await _held(w, t)

    watched = ("drone_session_commands", "drone_verification_requests", "drone_patrol_sessions", "drone_events",
               "alerts", "incidents")

    async def counts() -> dict:
        return {name: (await _sql(f"SELECT count(*) AS n, max(xmin::text::bigint) AS x FROM {name} "
                                  "WHERE tenant_id = :t", {"t": w["tenant"]}))[0] for name in watched}

    before = await counts()
    pub = intel_runner.ListPublisher()
    await intel_runner.run_ingest_tick(AsyncSessionLocal)
    await intel_runner.run_correlate_tick(AsyncSessionLocal, pub)
    await intel_runner.run_assess_tick(AsyncSessionLocal, pub)
    await intel_runner.run_recommend_tick(AsyncSessionLocal, pub)
    assert await counts() == before, "the runner changed a row that is not the layer's own"

    mine = [(kind, p) for tenant, kind, p in pub.events if str(tenant) == str(w["tenant"])]
    joined = [p for kind, p in mine if kind == "intel_situation_updated" and p["situation_id"] == str(s["id"])]
    assert [p["link"]["method"] for p in joined] == ["DRONE_LOOK"]
    assert joined[0]["link"]["reason"].startswith("A person asked the drone to hold for 10 s and look again")
    assessed = [p for kind, p in mine if kind == "intel_assessment_ready" and p["situation_id"] == str(s["id"])]
    suggested = [p for kind, p in mine if kind == "intel_recommendation_ready" and p["situation_id"] == str(s["id"])]
    after = dict((await _sql("SELECT * FROM security_situations WHERE id = :s", {"s": s["id"]}))[0])
    # Higher by more than the look's own five points: the drone module re-scored
    # its sighting on what it saw, and that is carried as the event's severity.
    assert len(assessed) == 1 and assessed[0]["risk_score"] == after["risk_score"] > s["risk_score"]
    assert len(suggested) == 1 and suggested[0]["is_decision"] is False
    assert suggested[0]["suggested"]["action"] != "VERIFY_WITH_DRONE"
    assert after["decision_status"] == "IN_HAND" and after["last_decision_id"] == uuid.UUID(r.json()["id"]), \
        "the runner assessed it again; it decided nothing"


# ─── C. Starting a mission the site already has ──────────────────────────────

async def _camera_matter() -> tuple[dict, dict]:
    """A site with a drone on the ground and its mission, and one situation
    from a camera's alert there."""
    w = await _drone_world()
    await _slow(w)
    w["alert"] = await _alert(w, "intrusion", camera="camera", site="site", code="intrusion.zone_breach",
                              severity="critical", title="Person at the loading bay", at=_now() - timedelta(seconds=40))
    await _pass(w)
    return w, await _situation_of(w, w["alert"])


@pytest.mark.asyncio
async def test_an_officer_starts_a_mission_the_site_has_and_what_the_flight_sees_joins_the_situation():
    w, s = await _camera_matter()
    offered = next(r for r in await _recs(s) if r["action"] == "VERIFY_WITH_DRONE")
    assert offered["available"] and _j(offered["supporting"])["facts"] == ["1 drone(s) at the site ready to fly."]
    async with _client() as c:
        picture = (await c.get(f"{BASE}/situations/{s['id']}/aerial", headers=w["h_op"])).json()
        r = await _decide(c, w, s, "op", drone_mission_id=str(w["mission"]))
    assert r.status_code == 201, r.text
    [mission] = picture["missions"]
    assert (mission["mission_id"], mission["name"], mission["can_launch"], mission["why_not"]) == (
        str(w["mission"]), "Night Watch", True, None)
    assert picture["sightings"] == [] and picture["licence"] == {"ok": True, "problem": None}

    d = r.json()
    [step] = d["actions"]
    assert (step["action"], step["result"], step["through"], step["target_type"]) == (
        "DRONE_LAUNCH", "OK", "app.routers.drone_planning.run_mission", "drone_flight")
    sid = step["target_id"]
    flight = await _session(sid)
    assert step["detail"].startswith(f"Flight {flight['session_number']} of mission “Night Watch” passed pre-flight")
    assert (flight["status"], flight["triggered_by"], flight["triggered_by_user_id"]) == ("READY", "MANUAL",
                                                                                         w["operator"])
    assert flight["mission_id"] == w["mission"]
    ran = (await _sql("SELECT user_id FROM audit_logs WHERE tenant_id = :t AND action = 'drone.mission.run'",
                      {"t": w["tenant"]}))[0]
    assert ran["user_id"] == w["operator"], "the drone module's own audit entry, under the officer's name"

    # The flight is flown by the drone runner and sees someone.
    t = await _fly(w, sid)
    e = await _seen(w, sid, t)
    await _pass(w)
    after = await _situation_of(w, e["id"])
    assert after["id"] == s["id"]
    link = dict((await _sql("""
        SELECT l.method, l.reason, l.confidence FROM security_situation_events l
          JOIN security_events ev ON ev.id = l.event_id WHERE ev.source_id = :i""", {"i": e["id"]}))[0])
    assert (link["method"], float(link["confidence"])) == ("DRONE_LOOK", 0.6)
    assert link["reason"].startswith("Seen by the flight a person started to look at this situation.")
    assert sorted(after["source_types"]) == ["CCTV_AI", "DRONE_PATROL"]
    assert after["assessment_id"] != s["assessment_id"], "assessed again on what the flight saw"
    async with _client() as c:
        picture = (await c.get(f"{BASE}/situations/{s['id']}/aerial", headers=w["h_op"])).json()
        detail = (await c.get(f"{BASE}/situations/{s['id']}", headers=w["h_op"])).json()
    [asked] = picture["asked"]
    assert asked["action"] == "DRONE_LAUNCH" and asked["flight"]["id"] == sid
    assert asked["flight"]["session_number"] == flight["session_number"] and asked["look"] is None
    assert detail["reassessed_since_decision"] is True
    [mission] = picture["missions"]
    assert mission["can_launch"] is False and mission["why_not"] == "Its drone is already committed to another flight."


@pytest.mark.asyncio
async def test_pre_flight_can_still_stop_it_and_that_is_what_the_record_says():
    w, s = await _camera_matter()
    await _sql("UPDATE drones SET battery_level = 5 WHERE id = :d", {"d": w["drone"]})
    async with _client() as c:
        r = await _decide(c, w, s, "op", drone_mission_id=str(w["mission"]))
    assert r.status_code == 201, r.text
    [step] = r.json()["actions"]
    assert (step["action"], step["result"], step["target_type"]) == ("DRONE_LAUNCH", "FAILED", "drone_flight")
    flight = await _session(step["target_id"])
    assert flight["status"] == "BLOCKED", "the drone module recorded the attempt as its own screens would"
    assert step["detail"].startswith(f"Pre-flight stopped flight {flight['session_number']}: ")
    assert "no reason was given" not in step["detail"]
    after = dict((await _sql("SELECT * FROM security_situations WHERE id = :s", {"s": s["id"]}))[0])
    assert after["decision_status"] == "IN_HAND"


@pytest.mark.asyncio
async def test_an_organisation_without_the_drone_licence_cannot_start_a_flight_from_a_decision():
    w, s = await _camera_matter()
    await _sql("UPDATE drone_module_licenses SET is_enabled = FALSE WHERE tenant_id = :t", {"t": w["tenant"]})
    async with _client() as c:
        r = await _decide(c, w, s, "op", drone_mission_id=str(w["mission"]))
        picture = (await c.get(f"{BASE}/situations/{s['id']}/aerial", headers=w["h_op"])).json()
    assert r.status_code == 403 and r.json()["detail"].startswith("Drone Patrol is not licensed")
    assert await _sql("SELECT 1 FROM security_decisions WHERE tenant_id = :t", {"t": w["tenant"]}) == []
    assert await _sql("SELECT 1 FROM drone_patrol_sessions WHERE tenant_id = :t", {"t": w["tenant"]}) == []
    assert picture["licence"]["ok"] is False and picture["may"]["launch"] is False
    assert picture["may"]["hold"] is True, "a flight already in the air can still be asked to hold"


@pytest.mark.asyncio
async def test_a_mission_switched_off_or_at_another_site_is_not_this_situations_to_start():
    w, s = await _camera_matter()
    elsewhere = uuid.uuid4()
    await _run([
        ("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Annex')", {"i": elsewhere, "t": w["tenant"]}),
        ("UPDATE drone_missions SET enabled = FALSE WHERE id = :m", {"m": w["mission"]}),
    ])
    async with _client() as c:
        off = await _decide(c, w, s, "op", drone_mission_id=str(w["mission"]))
        await _run([("UPDATE drone_missions SET enabled = TRUE, site_id = :s WHERE id = :m",
                     {"s": elsewhere, "m": w["mission"]})])
        away = await _decide(c, w, s, "op", drone_mission_id=str(w["mission"]))
        picture = (await c.get(f"{BASE}/situations/{s['id']}/aerial", headers=w["h_op"])).json()
    assert off.status_code == away.status_code == 422
    assert picture["missions"] == []
    assert await _sql("SELECT 1 FROM drone_patrol_sessions WHERE tenant_id = :t", {"t": w["tenant"]}) == []


# ─── D. What a virtual patrol recorded ───────────────────────────────────────

async def _patrol_check(w: dict, *, minutes_ago: int, answers: list[tuple[str, bool]], status: str = "COMPLETED",
                        notes: str | None = None, snapshot: bool = False, number: str = "VP-0042") -> dict:
    ids = {k: uuid.uuid4() for k in ("session", "camera")}
    at = _now() - timedelta(minutes=minutes_ago)
    stmts = [
        ("INSERT INTO virtual_patrol_sessions (id, tenant_id, site_id, patrol_number, schedule_name, scheduled_for, "
         "    status, started_at) VALUES (:i,:t,:s,:n,'Night round',:at,'COMPLETED',:at)",
         {"i": ids["session"], "t": w["tenant"], "s": w["site_a"], "n": number, "at": at}),
        ("INSERT INTO virtual_patrol_session_cameras (id, tenant_id, session_id, camera_id, sequence_no, camera_name, "
         "    status, started_at, completed_at, officer_notes, snapshot_path, snapshot_taken_at) "
         "VALUES (:i,:t,:s,:c,1,'Gate 1',:st,:at,:at,:notes,:path,:taken)",
         {"i": ids["camera"], "t": w["tenant"], "s": ids["session"], "c": w["cam_a"], "st": status, "at": at,
          "notes": notes, "path": "vp/snap.jpg" if snapshot else None, "taken": at if snapshot else None}),
    ]
    ids["answers"] = []
    for n, (answer, exception) in enumerate(answers, 1):
        q, a = uuid.uuid4(), uuid.uuid4()
        ids["answers"].append(a)
        stmts += [
            ("INSERT INTO virtual_patrol_session_questions (id, tenant_id, session_camera_id, question_text, "
             "    question_type, is_required, sequence_no, failure_action) "
             "VALUES (:i,:t,:c,'Is the gate closed?','YES_NO',TRUE,:n,'CREATE_INCIDENT')",
             {"i": q, "t": w["tenant"], "c": ids["camera"], "n": n}),
            ("INSERT INTO virtual_patrol_session_answers (id, tenant_id, session_question_id, answered_by_user_id, "
             "    answer_text, answered_at, is_exception, exception_reason) VALUES (:i,:t,:q,:u,:a,:at,:x,:why)",
             {"i": a, "t": w["tenant"], "q": q, "u": w["users"][OPERATOR], "a": answer, "at": at,
              "x": exception, "why": "Gate left open" if exception else None}),
        ]
    await _run(stmts)
    return ids


async def _context_at(w: dict, camera: str = "cam_a", site: str = "site_a") -> dict:
    event = {"occurred_at": _now(), "site_id": w[site], "camera_id": w[camera], "subject_kind": "PERSON",
             "subject_verdict": None, "location_label": "Gate 1", "attributes": {"module_type": "intrusion"}}
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        return await ctx.context_for(db, event)


@pytest.mark.asyncio
async def test_the_context_of_an_event_says_when_a_patrol_last_looked_at_its_camera_and_what_it_reported():
    w = await _intel_world()
    assert (await _context_at(w))["operations"]["last_patrol_check"] is None, "never checked: nothing is said"

    await _patrol_check(w, minutes_ago=300, answers=[("NO", True)], number="VP-0001")
    await _patrol_check(w, minutes_ago=40, answers=[("YES", False), ("YES", False)], notes="All quiet", snapshot=True)
    c = await _context_at(w)
    last = c["operations"]["last_patrol_check"]
    assert (last["patrol_number"], last["answered"], last["exceptions"], last["noted"], last["snapshot"]) == (
        "VP-0042", 2, 0, True, True), "the latest check, not the one before it"
    assert "A virtual patrol checked this camera 40 min before (VP-0042): 2 question(s) answered, nothing reported" \
        in _texts(c, "operations")
    # Another camera's check says nothing about this one; and a day-old check is not said at all.
    assert (await _context_at(w, "cam_b", "site_b"))["operations"]["last_patrol_check"] is None
    await _sql("UPDATE virtual_patrol_session_cameras SET completed_at = completed_at - interval '2 days', "
               "started_at = started_at - interval '2 days' WHERE tenant_id = :t", {"t": w["tenant"]})
    assert (await _context_at(w))["operations"]["last_patrol_check"] is None

    dark = await _intel_world()
    await _patrol_check(dark, minutes_ago=10, answers=[], status="CAMERA_UNAVAILABLE")
    assert "A virtual patrol could not see this camera 10 min before (VP-0042): the camera was unavailable" in _texts(
        await _context_at(dark), "operations")


@pytest.mark.asyncio
async def test_a_patrols_exception_is_read_with_the_officers_answer_note_and_snapshot_and_shown_with_the_situation():
    w = await _intel_world()
    ids = await _patrol_check(w, minutes_ago=3, answers=[("NO", True)], notes="Rear gate visibility abnormal",
                              snapshot=True)
    await _pass(w)
    s = await _situation_of(w, ids["answers"][0])
    async with _client() as c:
        detail = (await c.get(f"{BASE}/situations/{s['id']}", headers=w["h"][OPERATOR])).json()
    [event] = detail["events"]
    a = event["attributes"]
    assert event["source_type"] == "VIRTUAL_PATROL"
    assert (a["question"], a["answer"], a["exception_reason"]) == ("Is the gate closed?", "NO", "Gate left open")
    assert a["observation"] == "Rear gate visibility abnormal" and a["has_snapshot"] is True
    assert a["session_camera_id"] == str(ids["camera"]) and a["patrol_number"] == "VP-0042"
    assert "officer_user_id" in a and "name" not in " ".join(a).lower(), "who answered is an id, never a name"


# ─── E. The schema, and who may see what ─────────────────────────────────────

@pytest.mark.asyncio
async def test_the_database_takes_the_new_steps_and_reasons_and_still_refuses_what_it_does_not_know():
    w, s = await _camera_matter()
    async with _client() as c:
        d = (await c.post(f"{BASE}/situations/{s['id']}/decisions", headers=w["h_op"],
                          json={"action": "ACKNOWLEDGE"})).json()
    event = (await _sql("SELECT id FROM security_events WHERE tenant_id = :t LIMIT 1", {"t": w["tenant"]}))[0]["id"]

    def action(seq: int, name: str, target: str | None) -> tuple[str, dict]:
        return ("INSERT INTO security_actions (tenant_id, decision_id, situation_id, sequence, action, through, "
                "    target_type, target_id, result) VALUES (:t,:d,:s,:q,:a,'x',:tt,:ti,'OK')",
                {"t": w["tenant"], "d": d["id"], "s": s["id"], "q": seq, "a": name, "tt": target,
                 "ti": uuid.uuid4() if target else None})

    await _run([action(10, "DRONE_HOLD", "drone_look"), action(11, "DRONE_HOLD", "drone_event"),
                action(12, "DRONE_LAUNCH", "drone_flight"), action(13, "DRONE_LAUNCH", "drone_mission")])
    for bad in (action(20, "DRONE_STEER", "drone_flight"), action(21, "DRONE_LAUNCH", "drone")):
        with pytest.raises(Exception, match="ck_secaction_(action|target)"):
            await _run([bad])
    with pytest.raises(Exception, match="ck_secsitev_method"):
        await _sql("UPDATE security_situation_events SET method = 'DRONE_GUESS' WHERE event_id = :e", {"e": event})
    await _sql("UPDATE security_situation_events SET method = 'DRONE_LOOK' WHERE event_id = :e", {"e": event})


@pytest.mark.asyncio
async def test_what_a_drone_could_be_asked_is_shown_only_within_the_tenant_and_missions_only_to_who_may_see_them():
    w, s = await _camera_matter()
    other = await _drone_world()
    await _user(w, VIEWER, "viewer")
    async with _client() as c:
        outsider = await c.get(f"{BASE}/situations/{s['id']}/aerial", headers=other["h_op"])
        viewer = await c.get(f"{BASE}/situations/{s['id']}/aerial", headers=w["h_viewer"])
        nobody = await c.get(f"{BASE}/situations/{s['id']}/aerial")
        deciding = await _decide(c, w, s, "viewer", drone_mission_id=str(w["mission"]))
    assert outsider.status_code == 404 and nobody.status_code in (401, 403)
    assert viewer.status_code == 200
    v = viewer.json()
    assert v["may"]["hold"] is False and v["may"]["launch"] is False
    assert v["hold_seconds"] == {"min": 5, "max": 120, "default": 30}
    assert "checks distance, battery and the provider" in v["notes"]["hold"]
    assert deciding.status_code == 403, "seeing is not deciding"
    assert await _sql("SELECT 1 FROM drone_patrol_sessions WHERE tenant_id = :t", {"t": w["tenant"]}) == []
