"""AI security intelligence, phase 5: how unusual, and how much it matters.

  A — The rules, with nothing running: normality, each risk factor, the label,
      the three confidences, a tenant's weights
  B — Against the database: a camera's habit counted from its alerts; an
      assessment written once and never changed; re-assessed only when the
      answer changes
  C — The runner's pass and the API
  D — The schema

The claims this phase makes, each with tests: risk is explained factor by
factor; what is not known adds nothing and lowers confidence instead; the same
detection is a different risk in a different place and hour; and the wording
never says who somebody is or what they intend.
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
from app.services import intel_context as ctx
from app.services import intel_correlation as corr
from app.services import intel_risk as risk
from app.services import intel_runner as runner
from tests.test_drone_api import ADMIN, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql
from tests.test_intel_context import MORNING, NIGHT, WEEKDAY_HOURS, ZONE, _event as _ctx_event, _facts
from tests.test_intel_events import _alert, _camera, _events, _read, _world

NOW = datetime.now(timezone.utc)
BASE = "/api/v1/security-intelligence"


def _ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def _situation(**over) -> dict:
    return {"id": uuid.uuid4(), "situation_number": "SIT-20261005-0001", "severity": "high",
            "title": "Restricted zone breach", "correlation_confidence": None, **over}


def _ev(**over) -> dict:
    """One event of a situation, with its link, as assess() receives it."""
    return {"source_type": "CCTV_AI", "event_type": "intrusion.zone_breach", "severity": "high",
            "subject_kind": "PERSON", "subject_verdict": None, "confidence": 0.8, "occurred_at": NIGHT,
            "attributes": {"module_type": "intrusion"}, "method": "FIRST_EVENT", "is_duplicate": False, **over}


def _context(at: datetime = NIGHT, event: dict | None = None, **facts) -> dict:
    return ctx.build(event or _ctx_event(at), _facts(**facts))


def _points(a: risk.Assessment) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in a.risk_factors:
        out[f["factor"]] = out.get(f["factor"], 0) + f["points"]
    return out


# ─── A. The rules ────────────────────────────────────────────────────────────

def test_the_bands_are_the_drone_engines_own_so_high_means_one_thing():
    from app.services import drone_ai
    assert risk.THRESHOLDS == drone_ai.THRESHOLDS and risk.LEVELS == drone_ai.LEVELS
    assert [risk.level_of(s) for s in (0, 14, 15, 34, 35, 54, 55, 79, 80, 100)] == [
        "INFO", "INFO", "LOW", "LOW", "MEDIUM", "MEDIUM", "HIGH", "HIGH", "CRITICAL", "CRITICAL"]


def test_with_too_little_history_there_is_no_normality_score_at_all():
    for baseline in (None, risk.Baseline(), risk.Baseline(weeks_observed=3, weeks_with_alerts=3, alerts=9)):
        n = risk.normality(_context(), baseline)
        assert n["normality_score"] is None and n["anomaly_score"] is None and n["factors"] == []
        assert n["basis"].startswith("Insufficient history")


def test_normality_is_the_cameras_own_habit_adjusted_by_what_was_expected():
    often = risk.Baseline(weeks_observed=8, weeks_with_alerts=6, alerts=14)
    never = risk.Baseline(weeks_observed=8, weeks_with_alerts=0, alerts=0)
    usual = risk.normality(_context(MORNING, visitors_on_site=2), often)
    assert usual["normality_score"] == 95 and usual["anomaly_score"] == 5, \
        "75 habit + 10 each for the site being open and visitors signed in; open hours are not counted twice"
    assert [f["factor"] for f in usual["factors"]] == ["HABIT", "EXPECTED", "EXPECTED"]
    assert usual["factors"][0] == {"factor": "HABIT", "points": 75, "detail":
                                   "This camera raised this kind of alert in this hour of the week in 6 of the "
                                   "last 8 weeks (14 alert(s))."}
    rare_at_night = risk.normality(_context(NIGHT, zones=[ZONE]), never)
    assert rare_at_night["normality_score"] == 0 and rare_at_night["anomaly_score"] == 100
    assert [f["factor"] for f in rare_at_night["factors"]] == ["HABIT", "HOURS", "ZONE"]
    no_hours = risk.normality(_context(NIGHT, profile=None), often)
    assert [f["factor"] for f in no_hours["factors"]] == ["HABIT"], "hours not defined: nothing taken from the hour"
    assert no_hours["normality_score"] == 75


def test_the_same_detection_is_a_different_risk_in_a_different_place_and_hour():
    """The specification's own example."""
    entrance = risk.assess(
        _situation(severity="low", title="Person detected"),
        [_ev(severity="low", subject_verdict="ALLOW", event_type="face.match",
             attributes={"module_type": "face"})],
        _context(MORNING, event=_ctx_event(MORNING, subject_verdict="ALLOW"), guards_on_shift=2,
                 profile={"timezone": None, "business_hours": WEEKDAY_HOURS, "closed_on_public_holidays": True,
                          "criticality": "low"}))
    warehouse = risk.assess(
        _situation(severity="high", title="Person in the warehouse", correlation_confidence=0.85),
        [_ev(subject_verdict="UNKNOWN", confidence=0.94),
         _ev(source_type="ACCESS_CONTROL", event_type="access.denied", subject_kind="NONE", confidence=None,
             method="ACCESS_AT_CAMERA"),
         _ev(source_type="DRONE_PATROL", event_type="drone.intrusion", confidence=0.91, method="NEAR_POSITION",
             attributes={"module_type": "intrusion", "drone_risk_level": "HIGH", "drone_risk_score": 82})],
        _context(NIGHT, event=_ctx_event(NIGHT, subject_verdict="UNKNOWN", attributes={
            "module_type": "intrusion", "zone_id": str(ZONE["id"])}), zones=[ZONE],
                 access={"granted": 0, "denied": 1, "forced": 0, "last_denial_reason": "card expired"},
                 camera_profile={"area_label": "Warehouse B", "criticality": "critical", "is_restricted_area": True}))
    assert entrance.risk_level == "INFO" and entrance.risk_score < 15
    assert warehouse.risk_level == "CRITICAL" and warehouse.risk_score >= 80
    assert _points(entrance)["IDENTITY"] == -25
    assert _points(warehouse) == {"SEVERITY": 45, "ZONE": 20, "CRITICALITY": 15, "TIME": 15, "ACCESS": 15,
                                  "CORROBORATION": 20, "DRONE": 10, "CONFIDENCE": 5}
    assert (warehouse.kind, warehouse.label) == ("ACCESS_REFUSED", "Access refused, with activity seen nearby")
    assert warehouse.summary.startswith("Access refused, with activity seen nearby. Risk CRITICAL (100). Raised by:")


def test_every_factor_says_why_and_the_same_situation_always_scores_the_same():
    args = (_situation(), [_ev(), _ev(is_duplicate=True, method="SAME_SOURCE_REPEAT")],
            _context(NIGHT, zones=[ZONE], history={"same_kind": 30, "decided": 10, "false_positive": 7,
                                                   "incidents": 2}))
    a, b = risk.assess(*args), risk.assess(*args)
    assert a.risk_factors == b.risk_factors and a.risk_score == b.risk_score and a.summary == b.summary
    assert all(f["detail"].strip().endswith(".") and isinstance(f["points"], int) and f["factor"] in risk.FACTORS
               for f in a.risk_factors), a.risk_factors
    assert a.risk_score == max(0, min(100, sum(f["points"] for f in a.risk_factors)))
    assert json.loads(json.dumps(a.context, default=str)) is not None


def test_what_is_not_known_adds_no_risk_and_lowers_confidence_instead():
    known = risk.assess(_situation(), [_ev()], _context(NIGHT))
    unknown_site = risk.assess(_situation(), [_ev()], _context(NIGHT, profile=None))
    assert "TIME" in _points(known) and "CRITICALITY" in _points(known)
    assert "TIME" not in _points(unknown_site) and "CRITICALITY" not in _points(unknown_site), \
        "no hours and no criticality set: neither is assumed"
    assert unknown_site.risk_score < known.risk_score
    assert unknown_site.risk_confidence < known.risk_confidence
    # One unknown each for hours, criticality and history, at 0.12 apiece.
    assert known.risk_confidence == 0.88 and unknown_site.risk_confidence == 0.64
    assert any("Insufficient history" in u for u in known.context["unknowns"])
    many = {**_context(NIGHT), "unknowns": [f"unknown {i}" for i in range(20)]}
    assert risk.assess(_situation(), [_ev()], many).risk_confidence == 0.3, "never below 0.3"


def test_a_person_nobody_identified_gets_no_points_for_being_unidentified():
    unidentified = risk.assess(_situation(), [_ev(subject_verdict="UNKNOWN")], _context(NIGHT))
    silent = risk.assess(_situation(), [_ev(subject_verdict=None)], _context(NIGHT))
    blocked = risk.assess(_situation(), [_ev(subject_verdict="BLOCK")], _context(NIGHT))
    allowed = risk.assess(_situation(), [_ev(subject_verdict="ALLOW")], _context(NIGHT))
    mixed = risk.assess(_situation(), [_ev(subject_verdict="ALLOW"), _ev(subject_verdict="UNKNOWN")], _context(NIGHT))
    assert "IDENTITY" not in _points(unidentified) and unidentified.risk_score == silent.risk_score
    assert _points(blocked)["IDENTITY"] == 25 and _points(allowed)["IDENTITY"] == -25
    assert "IDENTITY" not in _points(mixed), "one known person does not vouch for an unknown one beside them"


def test_doors_corroboration_repetition_and_history_each_move_the_score():
    def points(events=None, **facts):
        return _points(risk.assess(_situation(), events or [_ev()], _context(NIGHT, **facts)))

    assert points(access={"granted": 0, "denied": 0, "forced": 1, "last_denial_reason": None})["ACCESS"] == 25
    assert points(access={"granted": 1, "denied": 0, "forced": 0, "last_denial_reason": None})["ACCESS"] == -10
    assert points([_ev(), _ev(source_type="ALARM", subject_kind="NONE")])["CORROBORATION"] == 10
    assert points([_ev(), _ev(method="ADJACENT_CAMERA")])["CORROBORATION"] == 5
    assert "CORROBORATION" not in points([_ev(), _ev(source_type="ALARM", is_duplicate=True)]), \
        "a duplicate is not a second witness"
    repeats = [_ev()] + [_ev(is_duplicate=True) for _ in range(4)]
    assert points(repeats)["PERSISTENCE"] == 5
    assert points([_ev()] + [_ev(is_duplicate=True) for _ in range(12)])["PERSISTENCE"] == 10
    assert points(history={"same_kind": 9, "decided": 10, "false_positive": 9, "incidents": 0})["HISTORY"] == -25
    assert points(history={"same_kind": 9, "decided": 10, "false_positive": 6, "incidents": 1})["HISTORY"] == -10
    assert "HISTORY" not in points(history={"same_kind": 3, "decided": 3, "false_positive": 3, "incidents": 0}), \
        "three of three is too few to call a camera unreliable"
    assert points(permits=[{"work_type": "electrical", "workers_count": 2}])["EXPECTED"] == -10


def test_usual_and_unusual_move_the_score_and_no_history_moves_nothing():
    rare = risk.Baseline(weeks_observed=8, weeks_with_alerts=0, alerts=0)
    common = risk.Baseline(weeks_observed=8, weeks_with_alerts=8, alerts=40)
    at_night = risk.assess(_situation(), [_ev()], _context(NIGHT), rare)
    by_day = risk.assess(_situation(), [_ev()], _context(MORNING), common)
    none = risk.assess(_situation(), [_ev()], _context(NIGHT), None)
    assert _points(at_night)["ANOMALY"] == 10 and at_night.anomaly_score == 100
    assert _points(by_day)["ANOMALY"] == -10 and by_day.normality_score == 100
    assert "ANOMALY" not in _points(none) and none.anomaly_score is None and none.normality_score is None
    assert at_night.normality_factors and none.normality_factors == []


def test_three_confidences_are_kept_apart():
    a = risk.assess(_situation(correlation_confidence=0.6),
                    [_ev(confidence=0.71), _ev(confidence=0.96), _ev(source_type="ALARM", confidence=None)],
                    _context(NIGHT))
    assert a.detection_confidence == 0.96, "the model's own, the highest it gave"
    assert a.correlation_confidence == 0.6 and a.risk_confidence == 0.88
    assert _points(a)["CONFIDENCE"] == 5
    unsure = risk.assess(_situation(), [_ev(confidence=0.41)], _context(NIGHT))
    assert _points(unsure)["CONFIDENCE"] == -10 and unsure.detection_confidence == 0.41
    no_model = risk.assess(_situation(), [_ev(source_type="ALARM", confidence=None)], _context(NIGHT))
    assert no_model.detection_confidence is None and "CONFIDENCE" not in _points(no_model)
    assert no_model.correlation_confidence is None, "a situation of one event: nothing was correlated"


def test_a_guards_sos_and_the_drones_own_assessment_are_carried_in():
    sos = risk.assess(_situation(severity="critical", title="Guard SOS"),
                      [_ev(source_type="GUARD", event_type="guard.sos", severity="critical", confidence=None)],
                      _context(NIGHT))
    assert (sos.kind, sos.label) == ("GUARD_EMERGENCY", "Guard emergency")
    assert _points(sos)["GUARD"] == 20 and sos.risk_level == "CRITICAL"
    drone = risk.assess(_situation(), [_ev(source_type="DRONE_PATROL", attributes={
        "module_type": "intrusion", "drone_risk_level": "CRITICAL", "drone_risk_score": 91, "zone_type": "NO_ENTRY"})],
                        _context(NIGHT))
    assert _points(drone)["DRONE"] == 15 and _points(drone)["ZONE"] == 20
    detail = next(f["detail"] for f in drone.risk_factors if f["factor"] == "DRONE")
    assert detail == "The drone module assessed its own sighting as CRITICAL (91)."
    low = risk.assess(_situation(), [_ev(source_type="DRONE_PATROL", attributes={
        "module_type": "intrusion", "drone_risk_level": "LOW", "drone_risk_score": 20})], _context(NIGHT))
    assert "DRONE" not in _points(low)


LABEL_CASES = [
    ([_ev(attributes={"module_type": "weapon"}, event_type="weapon.detected")], {},
     "WEAPON", "Possible weapon — requires review"),
    ([_ev(attributes={"module_type": "fire_smoke"})], {}, "FIRE_SMOKE", "Possible fire or smoke — requires review"),
    ([_ev(source_type="ACCESS_CONTROL", event_type="access.forced"), _ev()], {},
     "DOOR_FORCED", "Door forced — with activity seen nearby"),
    ([_ev(source_type="ACCESS_CONTROL", event_type="access.denied")], {}, "ACCESS_REFUSED", "Access refused"),
    ([_ev(source_type="ALARM", event_type="alarm.zone_alarm", attributes={"module_type": "alarm"})], {},
     "ALARM", "Alarm"),
    ([_ev(attributes={"module_type": "fall"})], {}, "FALL", "Possible fall — requires review"),
    ([_ev(subject_verdict="BLOCK", subject_kind="VEHICLE", attributes={"module_type": "lpr"})], {},
     "BLOCK_LISTED", "Block-listed vehicle"),
    ([_ev(subject_verdict="BLOCK", attributes={"module_type": "face"})], {}, "BLOCK_LISTED", "Block-listed person"),
    ([_ev()], {"zones": [ZONE]}, "RESTRICTED_ZONE", "Suspicious activity in a restricted zone, out of hours"),
    ([_ev()], {"zones": [ZONE], "profile": None}, "RESTRICTED_ZONE", "Activity in a restricted zone"),
    ([_ev(attributes={"module_type": "face"})], {}, "ACTIVITY", "Unusual activity out of hours"),
    ([_ev(attributes={"module_type": "ppe"})], {"profile": None}, "ACTIVITY", "Activity that requires review"),
    ([_ev(source_type="VIRTUAL_PATROL", event_type="vpatrol.exception", attributes={})], {"profile": None},
     "PATROL_FINDING", "Virtual patrol finding"),
    # An intrusion alert is activity, whatever a patrol reported beside it.
    ([_ev(), _ev(source_type="VIRTUAL_PATROL", event_type="vpatrol.exception", attributes={})], {"profile": None},
     "ACTIVITY", "Activity that requires review"),
    ([_ev(source_type="SYSTEM", event_type="camera.stream_disconnected", attributes={})], {"profile": None},
     "CAMERA_OFFLINE", "Camera stopped sending"),
]


@pytest.mark.parametrize("events, facts, kind, expected", LABEL_CASES)
def test_what_a_situation_appears_to_be_is_said_in_chosen_words(events, facts, kind, expected):
    a = risk.assess(_situation(), events, _context(NIGHT, **facts))
    assert (a.kind, a.label) == (kind, expected) and a.kind in risk.KINDS
    said = (a.label + " " + a.summary + " " + json.dumps(a.risk_factors)).lower()
    for word in ("intruder", "unauthorised", "unauthorized", "criminal", "thief", "trespass"):
        assert word not in said, f"“{word}” states who somebody is or what they intend"


def test_a_tenant_can_weigh_a_factor_more_or_less_or_not_at_all():
    args = (_situation(), [_ev()], _context(NIGHT))
    shipped = _points(risk.assess(*args))
    assert shipped["TIME"] == 15
    assert _points(risk.assess(*args, weights={"TIME": 2}))["TIME"] == 30
    assert _points(risk.assess(*args, weights={"TIME": 0}))["TIME"] == 0
    assert _points(risk.assess(*args, weights={"TIME": 0.5}))["TIME"] == 8
    risk.validate_weights({"TIME": 2, "ZONE": 0, "HISTORY": 1.5})
    for bad in ("TIME=2", ["TIME"], {"WEATHER": 1}, {"TIME": 4}, {"TIME": -1}, {"TIME": "2"}, {"TIME": True}):
        with pytest.raises(ValueError):
            risk.validate_weights(bad)


def test_an_assessment_that_says_the_same_is_not_news():
    a = risk.assess(_situation(), [_ev()], _context(NIGHT))
    previous = {"risk_score": a.risk_score, "label": a.label, "risk_factors": json.dumps(a.risk_factors)}
    assert a.same_answer_as(previous) is True and a.same_answer_as(None) is False
    assert a.same_answer_as({**previous, "risk_score": a.risk_score - 5}) is False
    more = risk.assess(_situation(), [_ev(), _ev(source_type="ALARM")], _context(NIGHT))
    assert more.same_answer_as(previous) is False


# ─── B. Against the database ─────────────────────────────────────────────────

async def _pass(w: dict, now: datetime | None = None) -> dict:
    """Read, place, assess."""
    now = now or NOW
    await _read(w, now=now)
    await corr.correlate_tenant(AsyncSessionLocal, w["tenant"], now)
    return await risk.assess_tenant(AsyncSessionLocal, w["tenant"], now)


async def _assessments(w: dict) -> list[dict]:
    rows = await _sql("SELECT a.* FROM security_assessments a WHERE a.tenant_id = :t ORDER BY a.assessed_at, a.sequence",
                      {"t": w["tenant"]})
    return [dict(r) for r in rows]


async def _situation_rows(w: dict) -> list[dict]:
    rows = await _sql("SELECT * FROM security_situations WHERE tenant_id = :t ORDER BY started_at", {"t": w["tenant"]})
    return [dict(r) for r in rows]


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


@pytest.mark.asyncio
async def test_a_cameras_habit_is_counted_from_its_own_alerts_hour_by_hour():
    w = await _world()
    at = _ago(minutes=5)
    # The same hour of the week in five of the last eight weeks; and noise at
    # another hour, on another camera, and of another kind, which must not count.
    for week in (1, 2, 4, 5, 8):
        await _alert(w, "intrusion", at=at - timedelta(weeks=week, minutes=10))
    await _alert(w, "intrusion", at=at - timedelta(weeks=3, hours=5))
    await _alert(w, "weapon", at=at - timedelta(weeks=3))
    await _alert(w, "intrusion", camera="cam_b", site="site_b", at=at - timedelta(weeks=3))
    await _alert(w, "intrusion", at=at - timedelta(weeks=10))             # how far back the camera goes

    async def read(camera, module="intrusion"):
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            b = await risk.baseline(db, {"camera_id": camera, "occurred_at": at, "attributes": {"module_type": module}})
            await db.rollback()
        return b

    assert await read(w["cam_a"]) == risk.Baseline(weeks_observed=8, weeks_with_alerts=5, alerts=5)
    assert await read(w["cam_b"]) == risk.Baseline(weeks_observed=3, weeks_with_alerts=1, alerts=1)
    assert await read(None) is None and await read(w["cam_a"], module=None) is None
    new_camera = await _camera(w, "site_a", "Brand new")
    assert await read(new_camera) == risk.Baseline(), "no history is zero weeks, not an error"


@pytest.mark.asyncio
async def test_a_situation_is_assessed_once_and_again_only_when_the_answer_changes():
    w = await _world()
    await _alert(w, "intrusion", code="intrusion.zone_breach", title="Person at Gate 1", at=_ago(seconds=100))
    first = await _pass(w)
    assert (first["assessed"], first["changed"], first["failed"]) == (1, 1, 0)
    (a1,) = await _assessments(w)
    (s,) = await _situation_rows(w)
    assert a1["sequence"] == 1 and a1["event_count"] == 1 and a1["engine_version"] == risk.ENGINE_VERSION
    assert (s["risk_score"], s["risk_level"], s["assessment_id"]) == (a1["risk_score"], a1["risk_level"], a1["id"])
    assert _j(a1["risk_factors"])[0]["factor"] == "SEVERITY" and a1["correlation_confidence"] is None
    assert a1["normality_score"] is None and any("Insufficient history" in u for u in _j(a1["context"])["unknowns"])

    again = await _pass(w)
    assert (again["assessed"], again["changed"]) == (0, 0), "nothing changed, so nothing is assessed"
    assert len(await _assessments(w)) == 1

    # A repeat of the same alert changes nothing worth a new row…
    await _alert(w, "intrusion", code="intrusion.zone_breach", title="Person at Gate 1", at=_ago(seconds=60))
    repeat = await _pass(w)
    assert (repeat["assessed"], repeat["changed"]) == (1, 0) and len(await _assessments(w)) == 1
    # …and a door refused at the same camera does.
    await _alert(w, "access", code="access.denied", title="Access denied at Rear door", at=_ago(seconds=30))
    third = await _pass(w)
    assert (third["assessed"], third["changed"]) == (1, 1)
    a1_again, a2 = await _assessments(w)
    assert a1_again == a1, "the first assessment is exactly as it was written"
    assert a2["sequence"] == 2 and a2["risk_score"] > a1["risk_score"] and a2["event_count"] == 3
    assert a2["label"] == "Access refused, with activity seen nearby"
    assert float(a2["correlation_confidence"]) == 0.85
    assert {f["factor"] for f in _j(a2["risk_factors"])} >= {"SEVERITY", "ACCESS", "CORROBORATION"}
    (s,) = await _situation_rows(w)
    assert s["assessment_id"] == a2["id"] and s["risk_score"] == a2["risk_score"]


@pytest.mark.asyncio
async def test_an_assessment_keeps_what_was_known_then_and_cannot_be_changed_by_the_application():
    w = await _world()
    await _alert(w, "intrusion", code="intrusion.zone_breach", at=_ago(seconds=60))
    await _pass(w)
    (a,) = await _assessments(w)
    assert any("Business hours are not defined" in u for u in _j(a["context"])["unknowns"])

    # The site is described afterwards. What was written is what was known then.
    await _sql("INSERT INTO security_site_profiles (tenant_id, site_id, business_hours, criticality) "
               "VALUES (:t,:s,CAST(:h AS jsonb),'critical')",
               {"t": w["tenant"], "s": w["site_a"], "h": json.dumps({})})
    (still,) = await _assessments(w)
    assert still == a

    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        for stmt in ("UPDATE security_assessments SET risk_score = 0", "DELETE FROM security_assessments"):
            with pytest.raises(Exception, match="permission denied"):
                await db.execute(text(stmt))
            await db.rollback()
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        await db.rollback()
    assert (await _assessments(w))[0] == a


@pytest.mark.asyncio
async def test_a_site_once_described_is_scored_with_what_is_now_known():
    w = await _world()
    await _run([
        ("INSERT INTO security_site_profiles (tenant_id, site_id, business_hours, criticality) "
         "VALUES (:t,:s,CAST(:h AS jsonb),'critical')", {"t": w["tenant"], "s": w["site_a"], "h": json.dumps({})}),
        ("INSERT INTO restricted_zones (tenant_id, camera_id, name, polygon, severity) "
         "VALUES (:t,:c,'Fuel store','[]','critical')", {"t": w["tenant"], "c": w["cam_a"]}),
        ("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
         "VALUES (:t,'intel.risk_weights',CAST(:v AS jsonb),:u)",
         {"t": w["tenant"], "v": json.dumps({"TIME": 2}), "u": w["users"][ADMIN]}),
    ])
    await _alert(w, "intrusion", code="intrusion.zone_breach", severity="high", at=_ago(seconds=60))
    await _pass(w)
    (a,) = await _assessments(w)
    points = {f["factor"]: f["points"] for f in _j(a["risk_factors"])}
    assert points == {"SEVERITY": 45, "ZONE": 20, "CRITICALITY": 15, "TIME": 30}, "closed every day; TIME doubled"
    assert (a["risk_score"], a["risk_level"]) == (100, "CRITICAL")
    assert (a["kind"], a["label"]) == ("RESTRICTED_ZONE", "Suspicious activity in a restricted zone, out of hours")
    unknowns = _j(a["context"])["unknowns"]
    assert len(unknowns) == 2, unknowns
    assert "How reliable this camera is" in unknowns[0] and "Insufficient history" in unknowns[1]
    assert float(a["risk_confidence"]) == 0.76, "two things not known, at 0.12 apiece"


@pytest.mark.asyncio
async def test_two_tenants_are_assessed_separately_and_see_only_their_own():
    a, b = await _world(), await _world()
    await _alert(a, "intrusion", code="intrusion.zone_breach", at=_ago(seconds=60))
    await _alert(b, "weapon", code="weapon.detected", severity="critical", title="Weapon", at=_ago(seconds=60))
    for w in (a, b):
        await _pass(w)
    assert [x["label"] for x in await _assessments(a)] != [x["label"] for x in await _assessments(b)]
    async with AsyncSessionLocal() as db:
        row = (await db.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))).first()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(a["tenant"])})
        mine = (await db.execute(text("SELECT label FROM security_assessments"))).scalars().all()
        await db.rollback()
    assert row.rolsuper is False and row.rolbypassrls is False
    assert mine == [x["label"] for x in await _assessments(a)] and len(mine) == 1


# ─── C. The runner's pass and the API ────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_runner_assesses_for_tenants_that_asked_and_announces_only_what_changed():
    on, off = await _world(enabled=True), await _world(enabled=False)
    for w in (on, off):
        await _alert(w, "intrusion", code="intrusion.zone_breach", title="Person at the gate", at=_ago(seconds=60))
    pub = runner.ListPublisher()
    await runner.run_ingest_tick(AsyncSessionLocal, now=NOW)
    await runner.run_correlate_tick(AsyncSessionLocal, pub, now=NOW)
    first = await runner.run_assess_tick(AsyncSessionLocal, pub, now=NOW)
    second = await runner.run_assess_tick(AsyncSessionLocal, pub, now=NOW)
    assert first["changed"] >= 1 and first["failed"] == 0 and second["changed"] == 0
    assert len(await _assessments(on)) == 1 and await _assessments(off) == []

    ready = [p for tenant, kind, p in pub.events if tenant == str(on["tenant"]) and kind == "intel_assessment_ready"]
    assert len(ready) == 1 and not [1 for tenant, _, _ in pub.events if tenant == str(off["tenant"])]
    (a,) = await _assessments(on)
    p = ready[0]
    assert (p["risk_score"], p["risk_level"], p["label"]) == (a["risk_score"], a["risk_level"], a["label"])
    assert p["kind"] == a["kind"] == "ACTIVITY"
    assert set(p) >= {"situation_id", "situation_number", "risk_confidence", "detection_confidence",
                      "correlation_confidence", "anomaly_score", "top_factors"}
    assert "confidence" not in p, "three confidences on the wire, not one"
    assert json.loads(json.dumps(p)) == p


@pytest.mark.asyncio
async def test_a_situation_is_served_with_its_assessment_and_the_reasons_behind_it():
    w = await _world()
    await _alert(w, "intrusion", camera="cam_a", code="intrusion.zone_breach", title="Person at Gate 1",
                 at=_ago(seconds=100))
    await _alert(w, "access", camera="cam_a", code="access.forced", title="Door forced open", severity="critical",
                 at=_ago(seconds=60))
    await _alert(w, "ppe", camera="cam_b", site="site_b", code="ppe.missing", title="No hard hat", severity="low",
                 at=_ago(seconds=40))
    await _pass(w)
    rows = await _situation_rows(w)
    at_a = next(s for s in rows if s["site_id"] == w["site_a"])
    async with _client() as c:
        one = await c.get(f"{BASE}/situations/{at_a['id']}", headers=w["h"][OPERATOR])
        history = await c.get(f"{BASE}/situations/{at_a['id']}/assessments", headers=w["h"][VIEWER])
        by_risk = await c.get(BASE + "/situations", params={"sort": "risk"}, headers=w["h"][ADMIN])
        by_time = await c.get(BASE + "/situations", headers=w["h"][ADMIN])
        critical = await c.get(BASE + "/situations", params={"risk_level": at_a["risk_level"]}, headers=w["h"][ADMIN])
        bad = [await c.get(BASE + "/situations", params=p, headers=w["h"][ADMIN])
               for p in ({"risk_level": "SEVERE"}, {"sort": "loudest"})]
        hidden = await c.get(f"{BASE}/situations/{at_a['id']}/assessments", headers=(await _world())["h"][ADMIN])
        missing = await c.get(f"{BASE}/situations/{uuid.uuid4()}/assessments", headers=w["h"][ADMIN])
    body = one.json()
    a = body["assessment"]
    assert (body["risk_score"], body["risk_level"]) == (a["risk_score"], a["risk_level"])
    assert (a["kind"], a["label"]) == ("DOOR_FORCED", "Door forced — with activity seen nearby")
    assert a["sequence"] == 1
    assert set(a["confidence"]) == {"detection", "correlation", "risk"}, "kept apart, never one number"
    assert a["confidence"]["correlation"] == 0.85 and a["confidence"]["detection"] is None
    assert all({"factor", "points", "detail"} == set(f) for f in a["risk_factors"])
    assert a["unknowns"] and all(s["source"] for s in a["statements"])
    assert a["normality_score"] is None and a["normality_basis"].startswith("Insufficient history")

    assert history.status_code == 200 and [h["sequence"] for h in history.json()] == [1]
    assert "statements" not in history.json()[0] and history.json()[0]["risk_factors"] == a["risk_factors"]
    assert [s["title"] for s in by_time.json()["items"]] == ["No hard hat", "Door forced open"], "latest first"
    assert [s["title"] for s in by_risk.json()["items"]] == ["Door forced open", "No hard hat"], "highest risk first"
    assert [s["title"] for s in critical.json()["items"]] == ["Door forced open"]
    assert [r.status_code for r in bad] == [422, 422]
    assert hidden.status_code == 404 and missing.status_code == 404


@pytest.mark.asyncio
async def test_a_restricted_supervisor_cannot_read_another_sites_assessments():
    w = await _world()
    await _alert(w, "weapon", camera="cam_b", site="site_b", code="weapon.detected", severity="critical",
                 at=_ago(seconds=60))
    await _pass(w)
    (s,) = await _situation_rows(w)
    async with _client() as c:
        r = await c.get(f"{BASE}/situations/{s['id']}/assessments", headers=w["h"][SUPERVISOR])
        listed = await c.get(BASE + "/situations", params={"sort": "risk"}, headers=w["h"][SUPERVISOR])
    assert r.status_code == 404 and listed.json()["total"] == 0


@pytest.mark.asyncio
async def test_a_tenants_weights_are_a_setting_that_refuses_nonsense():
    w = await _world()
    url = "/api/v1/settings/intel.risk_weights"
    async with _client() as c:
        ok = await c.put(url, json={"setting_value": {"TIME": 2, "HISTORY": 0}}, headers=w["h"][ADMIN])
        bad = [await c.put(url, json={"setting_value": v}, headers=w["h"][ADMIN])
               for v in ({"WEATHER": 1}, {"TIME": 9}, "double everything")]
        refused = await c.put(url, json={"setting_value": {"TIME": 2}}, headers=w["h"][OPERATOR])
    assert ok.status_code == 200 and [r.status_code for r in bad] == [422, 422, 422]
    assert "WEATHER" in bad[0].json()["detail"] and refused.status_code == 403
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        assert await risk.weights(db) == {"TIME": 2, "HISTORY": 0}
        await db.rollback()


# ─── D. The schema ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assessments_are_tenant_isolated_and_the_application_may_only_add_and_read():
    row = (await _sql(
        "SELECT c.relrowsecurity, c.relforcerowsecurity, "
        "       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies, "
        "       has_table_privilege('svc_app', c.oid, 'SELECT') AS can_read, "
        "       has_table_privilege('svc_app', c.oid, 'INSERT') AS can_add, "
        "       has_table_privilege('svc_app', c.oid, 'UPDATE') AS can_change, "
        "       has_table_privilege('svc_app', c.oid, 'DELETE') AS can_remove "
        "  FROM pg_class c WHERE c.relname = 'security_assessments'"))[0]
    assert row["relrowsecurity"] and row["relforcerowsecurity"] and row["policies"] == 1
    assert row["can_read"] and row["can_add"] and not row["can_change"] and not row["can_remove"]


@pytest.mark.asyncio
async def test_an_assessment_without_a_reason_or_with_scores_that_disagree_is_refused():
    w = await _world()
    await _alert(w, "intrusion", code="intrusion.zone_breach", at=_ago(seconds=60))
    await _pass(w)
    (s,) = await _situation_rows(w)
    insert = ("INSERT INTO security_assessments (tenant_id, situation_id, sequence, assessed_at, kind, label, "
              "    summary, risk_score, risk_level, risk_factors, normality_score, anomaly_score, risk_confidence, "
              "    context, event_count, engine_version) VALUES (:t,:s,:seq,now(),:kind,'x','y',:score,:level,"
              "    CAST(:f AS jsonb),:norm,:anom,:conf,'{}'::jsonb,1,'test')")
    ok = {"t": w["tenant"], "s": s["id"], "seq": 2, "kind": "ACTIVITY", "score": 50, "level": "MEDIUM",
          "f": json.dumps([{"factor": "SEVERITY", "points": 50, "detail": "x."}]), "norm": 30, "anom": 70, "conf": 0.8}
    for bad in ({"seq": 1}, {"f": "[]"}, {"f": "{}"}, {"score": 140}, {"level": "SEVERE"}, {"anom": 60},
                {"norm": None}, {"conf": 1.5}, {"seq": 0}, {"kind": "MYSTERY"}):
        with pytest.raises(Exception):
            await _sql(insert, {**ok, **bad})
    await _sql(insert, ok)
    await _sql(insert, {**ok, "seq": 3, "norm": None, "anom": None})
    await _sql("DELETE FROM security_situations WHERE id = :s", {"s": s["id"]})
    assert await _assessments(w) == [], "assessments go with their situation"
