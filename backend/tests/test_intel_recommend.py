"""AI security intelligence, phase 6: what the layer suggests doing.

  A — The rules, with nothing running: which step for which kind at which
      risk, the fourth confidence, what cannot be done
  B — What can be done right now, read from the database
  C — Written once per assessment; the runner's pass; and that suggesting
      changes nothing
  D — The API
  E — The schema

The claims this phase makes, each with tests: a suggestion is a record and
nothing else; a step that cannot be taken says why; a step that sends someone is
no surer than what it rests on, so an incomplete picture puts looking first;
and a guard's SOS is not held back by any of that.
"""
from __future__ import annotations

import itertools
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.db.session import AsyncSessionLocal
from app.services import intel_correlation as corr
from app.services import intel_recommend as rec
from app.services import intel_risk as risk
from app.services import intel_runner as runner
from tests.test_drone_api import ADMIN, GUARD, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql
from tests.test_intel_events import _alert, _camera, _events, _read, _world

NOW = datetime.now(timezone.utc)
BASE = "/api/v1/security-intelligence"
SEVERITY = {"factor": "SEVERITY", "points": 45, "detail": "The most serious event is “Person at Gate 1” (high)."}
TWO_SOURCES = {"factor": "CORROBORATION", "points": 10, "detail": "Reported by two kinds of source: ALARM, CCTV_AI."}
CAM = {"id": str(uuid.uuid4()), "name": "Gate 1", "state": "online", "relation": "reported"}
ACTING = set(rec.ACTIONS) - rec.LOOKING


def _ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def _a(kind: str = "ACTIVITY", level: str = "HIGH", *, factors=None, det=0.9, corr_=None, risk_=0.88,
       score: int = 60) -> dict:
    """An assessment, as the recommender receives it."""
    return {"id": uuid.uuid4(), "kind": kind, "label": "Activity that requires review", "risk_level": level,
            "risk_score": score, "risk_factors": factors if factors is not None else [SEVERITY],
            "detection_confidence": det, "correlation_confidence": corr_, "risk_confidence": risk_}


def _can(**over) -> rec.Availability:
    """A site where everything ordinary is possible: a camera, guards, a contact."""
    return rec.Availability(**{**dict(has_site=True, cameras=(CAM,), guards_on_shift=2, site_contact=True), **over})


def _steps(recs, available: bool | None = None) -> list[str]:
    return [r.action for r in recs if available is None or r.available is available]


def _step(recs, action: str) -> rec.Recommendation:
    return next(r for r in recs if r.action == action)


# ─── A. The rules ────────────────────────────────────────────────────────────

def test_low_risk_is_watched_and_nobody_is_sent():
    info, low = rec.recommend(_a(level="INFO"), _can()), rec.recommend(_a(level="LOW"), _can())
    assert _steps(info) == ["MONITOR"] and _steps(low) == ["MONITOR", "VIEW_CAMERA"]
    assert {r.priority for r in info + low} == {"LOW"}
    assert not ACTING & set(_steps(info + low))


def test_medium_risk_is_confirmed_before_anything_else():
    with_camera = rec.recommend(_a(level="MEDIUM"), _can())
    with_drone = rec.recommend(_a(level="MEDIUM"), _can(drones_at_site=1, drones_ready=1))
    blind = rec.recommend(_a(level="MEDIUM"), _can(cameras=()))
    assert _steps(with_camera) == ["VIEW_CAMERA", "MONITOR"]
    assert _steps(with_drone) == ["VIEW_CAMERA", "VERIFY_WITH_DRONE", "MONITOR"]
    assert _steps(blind) == ["VERIFY", "MONITOR"], "no camera: confirm another way, and say so"
    assert "No camera shows this place" in _step(blind, "VERIFY").reason
    assert not ACTING & set(_steps(with_camera + with_drone + blind))
    assert _step(with_camera, "VIEW_CAMERA").priority == "MEDIUM" and _step(with_camera, "MONITOR").priority == "LOW"


def test_at_high_risk_one_kind_of_source_looks_first_and_two_send_first():
    alone = rec.recommend(_a(level="HIGH"), _can())
    agreed = rec.recommend(_a(level="HIGH", factors=[SEVERITY, TWO_SOURCES], corr_=0.85), _can())
    assert _steps(alone) == ["VIEW_CAMERA", "DISPATCH_GUARD", "CREATE_INCIDENT"]
    assert "look before sending anyone" in _step(alone, "VIEW_CAMERA").reason
    assert _steps(agreed) == ["DISPATCH_GUARD", "VIEW_CAMERA", "CREATE_INCIDENT"]
    assert _step(agreed, "DISPATCH_GUARD").confidence == 0.85 and _step(alone, "DISPATCH_GUARD").confidence == 0.7
    assert {r.priority for r in alone} == {"HIGH"}


def test_two_cameras_of_one_kind_are_not_a_second_kind_of_source():
    """A person seen on two neighbouring cameras is one kind of source seen
    twice. The assessment counts that for a little; the rule about sources
    means kinds — a door and a camera, an alarm and a drone. Until rules-3 the
    two cameras were taken for it: at high risk a guard was suggested first,
    and the reason given was "more than one kind of source reported this",
    which no record said. Found on the first situation the layer read on a
    running stack."""
    from app.services import intel_risk

    two_cameras = {"factor": "CORROBORATION", "points": 5, "detail": intel_risk.SEEN_BY_CAMERAS}
    alone = rec.recommend(_a(level="HIGH"), _can())
    seen_twice = rec.recommend(_a(level="HIGH", factors=[SEVERITY, two_cameras], corr_=0.7), _can())
    assert _steps(seen_twice) == _steps(alone) == ["VIEW_CAMERA", "DISPATCH_GUARD", "CREATE_INCIDENT"]
    assert _step(seen_twice, "VIEW_CAMERA").reason == "Only one kind of source reported this: look before sending anyone."

    critical = rec.recommend(_a(level="CRITICAL", factors=[SEVERITY, two_cameras], score=95), _can())
    send = _step(critical, "DISPATCH_GUARD")
    assert send.reason == "Send a guard now." and send.confidence == 0.75
    assert not any("More than one kind of source" in r.reason for r in seen_twice + critical)
    # Two kinds still send first, and say so.
    agreed = rec.recommend(_a(level="CRITICAL", factors=[SEVERITY, TWO_SOURCES], score=95), _can())
    assert _step(agreed, "DISPATCH_GUARD").reason == "More than one kind of source reported this: send a guard now."

    # And the sentence the recommender tells them apart by is the one the risk engine writes.
    from tests.test_intel_risk import NIGHT, _context, _ev, _situation

    assessed = intel_risk.assess(_situation(), [_ev(), _ev(method="ADJACENT_CAMERA")], _context(NIGHT))
    said = [f["detail"] for f in assessed.risk_factors if f["factor"] == "CORROBORATION"]
    assert said == [intel_risk.SEEN_BY_CAMERAS]


def test_critical_risk_sends_escalates_and_opens_an_incident():
    recs = rec.recommend(_a(level="CRITICAL", factors=[SEVERITY, TWO_SOURCES], det=0.94, corr_=0.85, score=95),
                         _can(drones_at_site=1, drones_ready=1))
    assert _steps(recs) == ["DISPATCH_GUARD", "ESCALATE", "CREATE_INCIDENT", "VIEW_CAMERA", "VERIFY_WITH_DRONE"]
    assert {r.priority for r in recs} == {"URGENT"}
    send = _step(recs, "DISPATCH_GUARD")
    assert (send.confidence, send.limited_by) == (0.85, "CORRELATION"), "the rule says 0.9; the weakest link is 0.85"


def test_an_incomplete_picture_puts_looking_first_by_arithmetic():
    """Many things not known: the steps that act fall to the risk confidence,
    the steps that look do not, so looking comes first without a special case."""
    full = rec.recommend(_a(level="CRITICAL", risk_=0.88), _can())
    thin = rec.recommend(_a(level="CRITICAL", risk_=0.52), _can())
    unsure = rec.recommend(_a(level="CRITICAL", det=0.41), _can())
    assert _steps(full)[0] == "ESCALATE" and _steps(thin)[0] == "VIEW_CAMERA" == _steps(unsure)[0]
    for r in thin:
        if r.action in rec.LOOKING:
            assert r.limited_by == "RULE" and r.confidence == _step(full, r.action).confidence
        else:
            assert (r.confidence, r.limited_by) == (0.52, "RISK")
    assert {(r.confidence, r.limited_by) for r in unsure if r.action in ACTING} == {(0.41, "DETECTION")}
    assert set(_steps(thin)) == set(_steps(full)), "nothing is withheld; only the order and the confidence change"


def test_a_guards_sos_is_not_held_back_by_an_incomplete_picture():
    sos = _a("GUARD_EMERGENCY", "CRITICAL", det=None, risk_=0.4)
    recs = rec.recommend(sos, _can())
    assert _steps(recs) == ["DISPATCH_GUARD", "ESCALATE", "VIEW_CAMERA", "CONTACT_SITE"]
    assert [(r.confidence, r.limited_by) for r in recs[:2]] == [(0.95, "RULE"), (0.95, "RULE")]
    assert "CREATE_INCIDENT" not in _steps(recs), "an SOS has already opened its incident"
    # The guard who raised it is the only one on shift: there is nobody else to send.
    alone = rec.recommend(sos, _can(guards_on_shift=1))
    assert _steps(alone, available=True)[0] == "ESCALATE"
    assert _step(alone, "DISPATCH_GUARD").unavailable_reason == "No other guard is on shift at this site."


def test_nobody_is_sent_towards_a_possible_weapon():
    for level in ("MEDIUM", "HIGH", "CRITICAL"):
        recs = rec.recommend(_a("WEAPON", level), _can())
        assert _steps(recs) == ["VIEW_CAMERA", "ESCALATE", "CREATE_INCIDENT", "CONTACT_SITE"]
        assert "DISPATCH_GUARD" not in _steps(recs)
    assert "before anyone is sent" in _step(recs, "VIEW_CAMERA").reason
    faint = rec.recommend(_a("WEAPON", "HIGH", det=0.45), _can())
    assert (_step(faint, "ESCALATE").confidence, _step(faint, "ESCALATE").limited_by) == (0.45, "DETECTION")


def test_fire_a_fall_a_dead_camera_and_a_patrol_finding_each_have_their_own_first_step():
    def steps(kind, level):
        return _steps(rec.recommend(_a(kind, level, det=None, risk_=1.0), _can()))

    assert steps("FIRE_SMOKE", "MEDIUM") == ["VIEW_CAMERA", "ESCALATE", "CONTACT_SITE", "CREATE_INCIDENT"]
    assert steps("FIRE_SMOKE", "HIGH") == ["VIEW_CAMERA", "ESCALATE", "CONTACT_SITE", "CREATE_INCIDENT",
                                           "DISPATCH_GUARD"]
    assert steps("FALL", "MEDIUM") == ["VIEW_CAMERA", "DISPATCH_GUARD"]
    assert steps("FALL", "CRITICAL") == ["VIEW_CAMERA", "DISPATCH_GUARD", "ESCALATE", "CREATE_INCIDENT"]
    assert steps("CAMERA_OFFLINE", "LOW") == ["INVESTIGATE", "MONITOR"]
    assert steps("CAMERA_OFFLINE", "HIGH") == ["INVESTIGATE", "DISPATCH_GUARD", "MONITOR"]
    assert steps("PATROL_FINDING", "MEDIUM") == ["INVESTIGATE", "VIEW_CAMERA"]
    assert steps("PATROL_FINDING", "HIGH") == ["INVESTIGATE", "VIEW_CAMERA", "DISPATCH_GUARD", "CREATE_INCIDENT"]
    blocked = rec.recommend(_a("BLOCK_LISTED", "HIGH"), _can())
    assert "ESCALATE" in _steps(blocked) and "ESCALATE" not in steps("ACTIVITY", "HIGH")


def test_what_cannot_be_done_is_listed_last_and_says_why():
    offline = {**CAM, "state": "offline"}
    recs = rec.recommend(
        _a(level="HIGH", factors=[SEVERITY, TWO_SOURCES], corr_=0.85),
        _can(cameras=(offline,), guards_on_shift=0, site_contact=False, drones_at_site=1,
             incident={"id": uuid.uuid4(), "status": "open"}))
    why = {r.action: r.unavailable_reason for r in recs if not r.available}
    assert why == {
        "DISPATCH_GUARD": "No guard is on shift at this site.",
        "VIEW_CAMERA": "The camera is not sending.",
        "VERIFY_WITH_DRONE": "No drone at this site is ready to fly.",
        "CREATE_INCIDENT": "An incident is already open for this.",
        "CONTACT_SITE": "No contact is recorded for this site.",
    }
    # Nothing usual can be done, so the list is never only greyed-out steps.
    assert _steps(recs, available=True) == ["ESCALATE"]
    assert "None of the usual steps" in recs[0].reason and recs[0].limited_by == "RULE"
    flags = [r.available for r in recs]
    assert flags == sorted(flags, reverse=True), "what can be done comes before what cannot"
    assert all((r.unavailable_reason is None) == r.available for r in recs)
    assert _step(recs, "CREATE_INCIDENT").supporting["incident"]["status"] == "open"

    def reason(action, **avail):
        return _step(rec.recommend(_a(level="HIGH"), _can(**avail)), action).unavailable_reason

    assert reason("DISPATCH_GUARD", has_site=False) == \
        "The situation has no site, so there is no shift to send a guard from."
    assert reason("DISPATCH_GUARD", guard_dispatched=True) == "A guard has already been dispatched to this."
    assert reason("VIEW_CAMERA", cameras=(offline, {**offline, "id": "b"})) == "None of the cameras is sending."
    assert reason("VIEW_CAMERA", cameras=({**CAM, "state": "disabled"},)) == "The camera is not sending."
    assert reason("VIEW_CAMERA", cameras=({**CAM, "state": "not_known"},)) is None, \
        "a camera nobody has heard from either way is not said to be down"
    quiet = rec.recommend(_a(level="LOW"), _can(cameras=(offline,)))
    assert _steps(quiet, available=True) == ["MONITOR"]


def test_with_nobody_on_shift_the_site_itself_is_the_next_call():
    recs = rec.recommend(_a(level="HIGH"), _can(guards_on_shift=0))
    call = _step(recs, "CONTACT_SITE")
    assert call.available and "Nobody is on shift" in call.reason
    assert "CONTACT_SITE" not in _steps(rec.recommend(_a(level="HIGH"), _can()))
    assert "CONTACT_SITE" not in _steps(rec.recommend(_a(level="MEDIUM"), _can(guards_on_shift=0)))


def test_a_camera_that_keeps_being_wrong_and_an_alert_that_keeps_coming_are_looked_into():
    wrong = {"factor": "HISTORY", "points": -25, "detail": "90% of decided alerts of this kind were false."}
    again = {"factor": "PERSISTENCE", "points": 10, "detail": "The same alert has repeated 12 times."}
    for level in ("LOW", "HIGH"):
        unreliable = _step(rec.recommend(_a(level=level, factors=[SEVERITY, wrong]), _can()), "INVESTIGATE")
        repeating = _step(rec.recommend(_a(level=level, factors=[SEVERITY, again]), _can()), "INVESTIGATE")
        both = _step(rec.recommend(_a(level=level, factors=[SEVERITY, again, wrong]), _can()), "INVESTIGATE")
        assert "marked false" in unreliable.reason and "keeps repeating" in repeating.reason
        assert both.reason == unreliable.reason and unreliable.priority == ("LOW" if level == "LOW" else "MEDIUM")
    assert "INVESTIGATE" not in _steps(rec.recommend(_a(level="HIGH"), _can()))
    a_few = {"factor": "PERSISTENCE", "points": 5, "detail": "The same alert has repeated 4 times."}
    assert "INVESTIGATE" not in _steps(rec.recommend(_a(level="HIGH", factors=[SEVERITY, a_few]), _can()))


def test_the_cameras_to_look_at_are_named_those_that_reported_first():
    cams = (CAM, {"id": "n1", "name": "Gate 2", "state": "not_known", "relation": "neighbour"},
            {"id": "n2", "name": "Yard", "state": "offline", "relation": "neighbour"})
    view = _step(rec.recommend(_a(level="MEDIUM"), _can(cameras=cams)), "VIEW_CAMERA")
    assert view.available and view.supporting["cameras"] == [dict(c) for c in cams]
    send = _step(rec.recommend(_a(level="HIGH"), _can(cameras=cams, guards_on_shift=3)), "DISPATCH_GUARD")
    assert send.supporting["facts"] == ["3 guard(s) on shift at the site."] and "cameras" not in send.supporting
    flying = _step(rec.recommend(_a(level="HIGH"), _can(drones_at_site=1, drone_in_flight=True)), "VERIFY_WITH_DRONE")
    assert flying.available and flying.supporting["facts"] == ["A drone is in the air at the site."]
    assert "VERIFY_WITH_DRONE" not in _steps(rec.recommend(_a(level="HIGH"), _can())), \
        "a site with no drone is not told that it has no drone"


KINDS_LEVELS = list(itertools.product(risk.KINDS, risk.LEVELS))


@pytest.mark.parametrize("kind, level", KINDS_LEVELS)
def test_every_suggestion_says_why_what_it_rests_on_and_never_who_somebody_is(kind, level):
    factors = [SEVERITY, TWO_SOURCES, {"factor": "HISTORY", "points": -15, "detail": "60% were false."}]
    for avail in (_can(drones_at_site=1, drones_ready=1), rec.Availability(), _can(cameras=(), guards_on_shift=0)):
        a = _a(kind, level, factors=factors, corr_=0.7)
        recs = rec.recommend(a, avail)
        assert recs and any(r.available for r in recs), "never nothing, and never only what cannot be done"
        assert [r.rank for r in recs] == list(range(1, len(recs) + 1))
        assert len({r.action for r in recs}) == len(recs) and {r.action for r in recs} <= set(rec.ACTIONS)
        for r in recs:
            assert r.reason.strip().endswith(".") and r.priority in rec.PRIORITIES and 0 <= r.confidence <= 1
            assert r.limited_by in ("RULE", "DETECTION", "CORRELATION", "RISK")
            assert r.limited_by == "RULE" or r.action in ACTING, "a step that only looks is as sure as its rule"
            assert r.supporting["risk"] == {"level": level, "score": 60}
            assert r.supporting["rests_on"] == [SEVERITY["detail"], TWO_SOURCES["detail"]]
            said = (r.reason + " " + (r.unavailable_reason or "")).lower()
            for word in ("intruder", "unauthorised", "unauthorized", "criminal", "thief", "trespass", "suspect"):
                assert word not in said
        again = rec.recommend(a, avail)
        assert [(r.action, r.confidence, r.rank, r.reason) for r in again] == \
               [(r.action, r.confidence, r.rank, r.reason) for r in recs]
        assert json.loads(json.dumps([r.supporting for r in recs])) is not None


def test_every_kind_of_step_can_be_suggested_by_something():
    seen = set()
    for kind, level in KINDS_LEVELS:
        for avail in (_can(drones_at_site=1, drones_ready=1), _can(cameras=(), guards_on_shift=0)):
            seen |= set(_steps(rec.recommend(_a(kind, level, factors=[SEVERITY, {
                "factor": "HISTORY", "points": -25, "detail": "x."}]), avail)))
    assert seen == set(rec.ACTIONS)


# ─── B. What can be done right now ───────────────────────────────────────────

async def _pass(w: dict, now: datetime | None = None) -> dict:
    """Read, place, assess, suggest."""
    now = now or NOW
    await _read(w, now=now)
    await corr.correlate_tenant(AsyncSessionLocal, w["tenant"], now)
    await risk.assess_tenant(AsyncSessionLocal, w["tenant"], now)
    return await rec.recommend_tenant(AsyncSessionLocal, w["tenant"], now)


async def _rows(w: dict, table: str, order: str = "created_at") -> list[dict]:
    return [dict(r) for r in await _sql(f"SELECT * FROM {table} WHERE tenant_id = :t ORDER BY {order}",
                                        {"t": w["tenant"]})]


async def _recs(w: dict) -> list[dict]:
    return [dict(r) for r in await _sql(
        "SELECT r.*, a.sequence FROM security_recommendations r JOIN security_assessments a ON a.id = r.assessment_id "
        " WHERE r.tenant_id = :t ORDER BY a.sequence, r.rank", {"t": w["tenant"]})]


async def _available(w: dict, now: datetime | None = None) -> rec.Availability:
    (situation,) = await _rows(w, "security_situations")
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        out = await rec.availability(db, situation, await _events(w), now or NOW)
        await db.rollback()
    return out


def _j(value):
    return json.loads(value) if isinstance(value, str) else value


@pytest.mark.asyncio
async def test_what_can_be_done_is_read_from_what_the_platform_has_recorded():
    w = await _world()
    alert = await _alert(w, "intrusion", code="intrusion.zone_breach", title="Person at Gate 1", at=_ago(seconds=60))
    await _read(w)
    await corr.correlate_tenant(AsyncSessionLocal, w["tenant"], NOW)
    nothing = await _available(w)
    assert nothing == rec.Availability(has_site=True, cameras=(
        {"id": str(w["cam_a"]), "name": "Gate 1", "relation": "reported", "state": "not_known"},))

    gate2, yard = await _camera(w, "site_a", "Gate 2"), await _camera(w, "site_a", "Yard")
    a, b = sorted([w["cam_a"], gate2], key=str)
    ready, no_mission, in_workshop, route, incident = (uuid.uuid4() for _ in range(5))
    drone = ("INSERT INTO drones (id, tenant_id, site_id, name, code, status, communication_status) "
             "VALUES (:i,:t,:s,:n,:c,:st,'OK')")
    await _run([
        ("INSERT INTO security_camera_links (tenant_id, camera_a, camera_b, walk_seconds) VALUES (:t,:a,:b,40)",
         {"t": w["tenant"], "a": a, "b": b}),
        # Gate 1 dropped and came back; Gate 2 dropped and has not.
        ("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, occurred_at) "
         "VALUES (:t,:c,'stream_disconnected',:at)", {"t": w["tenant"], "c": w["cam_a"], "at": _ago(minutes=9)}),
        ("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, occurred_at) "
         "VALUES (:t,:c,'stream_reconnected',:at)", {"t": w["tenant"], "c": w["cam_a"], "at": _ago(minutes=8)}),
        ("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, occurred_at) "
         "VALUES (:t,:c,'stream_disconnected',:at)", {"t": w["tenant"], "c": gate2, "at": _ago(minutes=3)}),
        # On shift now; a shift that ended; and one at the other site.
        ("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, status) "
         "VALUES (:t,:s,:g,:a,:b,:a,'active')",
         {"t": w["tenant"], "s": w["site_a"], "g": w["users"][GUARD], "a": _ago(hours=2), "b": NOW + timedelta(hours=6)}),
        ("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, "
         "    actual_end, status) VALUES (:t,:s,:g,:a,:b,:a,:b,'completed')",
         {"t": w["tenant"], "s": w["site_a"], "g": w["users"][OPERATOR], "a": _ago(hours=9), "b": _ago(hours=1)}),
        ("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, status) "
         "VALUES (:t,:s,:g,:a,:b,:a,'active')",
         {"t": w["tenant"], "s": w["site_b"], "g": w["users"][SUPERVISOR], "a": _ago(hours=2),
          "b": NOW + timedelta(hours=6)}),
        # A drone that can fly; one with no mission to fly; one in the workshop.
        (drone, {"i": ready, "t": w["tenant"], "s": w["site_a"], "n": "Ready", "c": "D-1", "st": "READY"}),
        (drone, {"i": no_mission, "t": w["tenant"], "s": w["site_a"], "n": "Idle", "c": "D-2", "st": "READY"}),
        (drone, {"i": in_workshop, "t": w["tenant"], "s": w["site_a"], "n": "Bench", "c": "D-3", "st": "MAINTENANCE"}),
        ("INSERT INTO drone_routes (id, tenant_id, site_id, name, base_latitude, base_longitude, default_speed_mps) "
         "VALUES (:i,:t,:s,'Perimeter',1.3,103.8,8)", {"i": route, "t": w["tenant"], "s": w["site_a"]}),
        ("INSERT INTO drone_missions (tenant_id, site_id, drone_id, route_id, name) VALUES (:t,:s,:d,:r,'Night')",
         {"t": w["tenant"], "s": w["site_a"], "d": ready, "r": route}),
        ("UPDATE sites SET site_contact_name = 'Tan Wei Ming', site_contact_phone = '+65 6000 0000' WHERE id = :s",
         {"s": w["site_a"]}),
        ("INSERT INTO incidents (id, tenant_id, alert_id, camera_id, title, severity) "
         "VALUES (:i,:t,:a,:c,'Person at Gate 1','high')",
         {"i": incident, "t": w["tenant"], "a": alert, "c": w["cam_a"]}),
    ])
    now = await _available(w)
    assert now.cameras == (
        {"id": str(w["cam_a"]), "name": "Gate 1", "relation": "reported", "state": "online"},
        {"id": str(gate2), "name": "Gate 2", "relation": "neighbour", "state": "offline"},
    ), "the camera that reported, then its linked neighbour; not a camera nobody linked"
    assert str(yard) not in json.dumps(now.cameras)
    assert (now.guards_on_shift, now.guard_dispatched) == (1, False)
    assert (now.drones_at_site, now.drones_ready, now.drone_in_flight) == (2, 1, False)
    assert now.site_contact is True and now.incident["id"] == incident and now.incident["status"] == "open"
    said = json.dumps([now.cameras, now.incident], default=str)
    assert "Tan Wei Ming" not in said and "6000" not in said, "that a contact exists, never who or the number"

    await _run([
        ("UPDATE incidents SET dispatched_guard_id = :g, dispatched_at = now() WHERE id = :i",
         {"g": w["users"][GUARD], "i": incident}),
        ("UPDATE drones SET status = 'MISSION_ACTIVE' WHERE id = :d", {"d": ready}),
        ("UPDATE cameras SET is_active = FALSE WHERE id = :c", {"c": w["cam_a"]}),
    ])
    later = await _available(w)
    assert later.guard_dispatched is True and (later.drones_ready, later.drone_in_flight) == (0, True)
    assert later.cameras[0]["state"] == "disabled"
    await _sql("UPDATE incidents SET status = 'resolved', resolved_at = now() WHERE id = :i", {"i": incident})
    closed = await _available(w)
    assert closed.incident is None and closed.guard_dispatched is False, "a resolved incident is not an open one"


# ─── C. Written once; the runner; nothing acted on ───────────────────────────

@pytest.mark.asyncio
async def test_each_assessment_gets_its_suggestions_once_and_the_earlier_set_is_kept():
    w = await _world()
    await _alert(w, "intrusion", code="intrusion.zone_breach", severity="critical", title="Person at Gate 1",
                 at=_ago(seconds=100))
    first = await _pass(w)
    assert (first["recommended"], first["failed"]) == (1, 0)
    set1 = await _recs(w)
    assert [r["rank"] for r in set1] == list(range(1, len(set1) + 1)) and {r["sequence"] for r in set1} == {1}
    assert set1[0]["action"] == "VIEW_CAMERA" and set1[0]["available"] is True
    assert {r["engine_version"] for r in set1} == {rec.ENGINE_VERSION}
    send = next(r for r in set1 if r["action"] == "DISPATCH_GUARD")
    assert send["available"] is False and send["unavailable_reason"] == "No guard is on shift at this site."
    assert _j(set1[0]["supporting"])["cameras"][0]["name"] == "Gate 1"

    again = await _pass(w)
    assert again["recommended"] == 0 and await _recs(w) == set1, "nothing new to suggest about"

    # A door refused at the same camera: a new assessment, and a new set beside the old one.
    await _alert(w, "access", code="access.denied", title="Access denied at Rear door", at=_ago(seconds=30))
    second = await _pass(w)
    assert second["recommended"] == 1
    both = await _recs(w)
    assert [r for r in both if r["sequence"] == 1] == set1, "the first set is exactly as it was written"
    set2 = [r for r in both if r["sequence"] == 2]
    assert set2 and {r["assessment_id"] for r in set2}.isdisjoint({r["assessment_id"] for r in set1})

    async with AsyncSessionLocal() as db:
        for stmt in ("UPDATE security_recommendations SET available = TRUE", "DELETE FROM security_recommendations"):
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            with pytest.raises(Exception, match="permission denied"):
                await db.execute(text(stmt))
            await db.rollback()
    assert await _recs(w) == both


WATCHED = ("alerts", "incidents", "escalation_events", "drone_patrol_sessions", "drone_verification_requests",
           "notification_log", "audit_logs")


async def _everything_else(w: dict) -> dict:
    """Every row a suggestion could have touched if suggesting did anything."""
    out = {}
    for table in WATCHED:
        exists = (await _sql("SELECT to_regclass(CAST(:n AS text)) IS NOT NULL AS there", {"n": table}))[0]["there"]
        if exists:
            rows = await _sql(f"SELECT md5(CAST(array_agg(t.* ORDER BY t.id) AS text)) AS h, count(*) AS n "
                              f"  FROM {table} t WHERE t.tenant_id = :t", {"t": w["tenant"]})
            out[table] = (rows[0]["n"], rows[0]["h"])
    return out


@pytest.mark.asyncio
async def test_suggesting_to_send_a_guard_sends_nobody_and_changes_nothing():
    w = await _world()
    await _sql("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, "
               "    status) VALUES (:t,:s,:g,:a,:b,:a,'active')",
               {"t": w["tenant"], "s": w["site_a"], "g": w["users"][GUARD], "a": _ago(hours=2),
                "b": NOW + timedelta(hours=6)})
    await _alert(w, "intrusion", code="intrusion.zone_breach", severity="critical", title="Person in the vault",
                 at=_ago(seconds=100))
    await _alert(w, "access", code="access.forced", severity="critical", title="Vault door forced", at=_ago(seconds=60))
    await _read(w)
    await corr.correlate_tenant(AsyncSessionLocal, w["tenant"], NOW)
    await risk.assess_tenant(AsyncSessionLocal, w["tenant"], NOW)
    before = await _everything_else(w)
    assert set(before) >= {"alerts", "incidents", "escalation_events"}, "the tables to watch were not found"

    out = await rec.recommend_tenant(AsyncSessionLocal, w["tenant"], NOW)
    assert out["recommended"] == 1
    steps = {r["action"]: r for r in await _recs(w)}
    assert {"DISPATCH_GUARD", "ESCALATE", "CREATE_INCIDENT"} <= set(steps)
    assert steps["DISPATCH_GUARD"]["available"] and steps["CREATE_INCIDENT"]["available"]
    assert await _everything_else(w) == before, "a suggestion is a record: nothing was sent, opened or told"
    alerts = await _rows(w, "alerts")
    assert {a["status"] for a in alerts} == {"open"} and all(a["escalated_at"] is None for a in alerts)
    assert await _rows(w, "incidents") == []


@pytest.mark.asyncio
async def test_two_tenants_get_their_own_suggestions_and_see_only_their_own():
    a, b = await _world(), await _world()
    await _alert(a, "intrusion", code="intrusion.zone_breach", at=_ago(seconds=60))
    await _alert(b, "weapon", code="weapon.detected", severity="critical", title="Weapon", at=_ago(seconds=60))
    for w in (a, b):
        await _pass(w)
    mine, theirs = await _recs(a), await _recs(b)
    assert mine and theirs and "ESCALATE" in {r["action"] for r in theirs}
    async with AsyncSessionLocal() as db:
        who = (await db.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))).first()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(a["tenant"])})
        seen = (await db.execute(text("SELECT id FROM security_recommendations"))).scalars().all()
        with pytest.raises(Exception):
            await db.execute(text(
                "INSERT INTO security_recommendations (tenant_id, situation_id, assessment_id, rank, action, "
                "    priority, reason, confidence, confidence_limited_by, engine_version) "
                "VALUES (:b,:s,:a,9,'VERIFY','LOW','forged.',0.5,'RULE','x')"),
                {"b": b["tenant"], "s": theirs[0]["situation_id"], "a": theirs[0]["assessment_id"]})
        await db.rollback()
    assert who.rolsuper is False and who.rolbypassrls is False
    assert set(seen) == {r["id"] for r in mine}


@pytest.mark.asyncio
async def test_the_runner_suggests_for_tenants_that_asked_and_says_it_is_not_a_decision():
    on, off = await _world(enabled=True), await _world(enabled=False)
    for w in (on, off):
        await _alert(w, "intrusion", code="intrusion.zone_breach", title="Person at the gate", at=_ago(seconds=60))
    pub = runner.ListPublisher()
    await runner.run_ingest_tick(AsyncSessionLocal, now=NOW)
    await runner.run_correlate_tick(AsyncSessionLocal, pub, now=NOW)
    await runner.run_assess_tick(AsyncSessionLocal, pub, now=NOW)
    first = await runner.run_recommend_tick(AsyncSessionLocal, pub, now=NOW)
    second = await runner.run_recommend_tick(AsyncSessionLocal, pub, now=NOW)
    assert first["recommended"] >= 1 and first["failed"] == 0 and second["recommended"] == 0
    assert await _recs(on) and await _recs(off) == []

    ready = [p for tenant, kind, p in pub.events if tenant == str(on["tenant"]) and kind == "intel_recommendation_ready"]
    assert len(ready) == 1
    p, rows = ready[0], await _recs(on)
    assert p["is_decision"] is False
    assert p["suggested"]["action"] == rows[0]["action"] == "VIEW_CAMERA" and p["count"] == len(rows)
    assert p["not_available"] == sum(1 for r in rows if not r["available"])
    assert set(p["suggested"]) == {"action", "priority", "reason", "recommendation_confidence", "limited_by"}
    assert "confidence" not in p, "the suggestion's confidence is named for what it is"
    assert json.loads(json.dumps(p)) == p
    kinds = [kind for tenant, kind, _ in pub.events if tenant == str(on["tenant"])]
    assert kinds.index("intel_assessment_ready") < kinds.index("intel_recommendation_ready")


# ─── D. The API ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_suggestions_are_served_as_suggestions_to_those_who_may_read_them():
    w = await _world()
    await _alert(w, "intrusion", code="intrusion.zone_breach", title="Person at Gate 1", at=_ago(seconds=100))
    await _pass(w)
    (s,) = await _rows(w, "security_situations")
    (a1,) = await _rows(w, "security_assessments", "sequence")
    url = f"{BASE}/situations/{s['id']}/recommendations"
    async with _client() as c:
        ok = await c.get(url, headers=w["h"][OPERATOR])
        viewer = await c.get(url, headers=w["h"][VIEWER])
        detail = await c.get(f"{BASE}/situations/{s['id']}", headers=w["h"][VIEWER])
        stranger = await c.get(url, headers=(await _world())["h"][ADMIN])
        missing = await c.get(f"{BASE}/situations/{uuid.uuid4()}/recommendations", headers=w["h"][ADMIN])
    assert viewer.status_code == 403, "a viewer may read situations, not what the layer suggests doing about them"
    assert detail.status_code == 200 and "recommendations" not in detail.json()
    assert stranger.status_code == 404 and missing.status_code == 404
    body = ok.json()
    assert ok.status_code == 200 and body["is_decision"] is False and body["current"] is True
    assert body["assessment"]["id"] == str(a1["id"]) and body["assessment"]["sequence"] == 1
    assert set(body["assessment"]["confidence"]) == {"detection", "correlation", "risk"}
    rows = body["recommendations"]
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1)) and rows[0]["action"] == "VIEW_CAMERA"
    for r in rows:
        assert set(r) == {"id", "rank", "action", "priority", "reason", "recommendation_confidence", "limited_by",
                          "available", "unavailable_reason", "supporting", "created_at"}
        assert "confidence" not in r, "four confidences, each under its own name"

    # A second assessment: the current set changes, and the first can still be read.
    await _alert(w, "access", code="access.denied", title="Access denied at Rear door", at=_ago(seconds=30))
    await _pass(w)
    async with _client() as c:
        now = await c.get(url, headers=w["h"][ADMIN])
        then = await c.get(url, params={"assessment_id": str(a1["id"])}, headers=w["h"][ADMIN])
        other = await c.get(url, params={"assessment_id": str(uuid.uuid4())}, headers=w["h"][ADMIN])
    assert now.json()["assessment"]["sequence"] == 2 and now.json()["current"] is True
    assert then.json()["current"] is False and then.json()["recommendations"] == rows
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_a_restricted_supervisor_cannot_read_another_sites_suggestions_and_none_yet_is_an_empty_list():
    w = await _world()
    await _alert(w, "weapon", camera="cam_b", site="site_b", code="weapon.detected", severity="critical",
                 at=_ago(seconds=60))
    await _read(w)
    await corr.correlate_tenant(AsyncSessionLocal, w["tenant"], NOW)
    (s,) = await _rows(w, "security_situations")
    url = f"{BASE}/situations/{s['id']}/recommendations"
    async with _client() as c:
        early = await c.get(url, headers=w["h"][ADMIN])
        hidden = await c.get(url, headers=w["h"][SUPERVISOR])
    assert hidden.status_code == 404
    assert early.status_code == 200 and early.json() == {
        "situation_id": str(s["id"]), "is_decision": False, "current": True, "assessment": None,
        "recommendations": []}


# ─── E. The schema ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_suggestions_are_tenant_isolated_and_the_application_may_only_add_and_read():
    row = (await _sql(
        "SELECT c.relrowsecurity, c.relforcerowsecurity, "
        "       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies, "
        "       has_table_privilege('svc_app', c.oid, 'SELECT') AS can_read, "
        "       has_table_privilege('svc_app', c.oid, 'INSERT') AS can_add, "
        "       has_table_privilege('svc_app', c.oid, 'UPDATE') AS can_change, "
        "       has_table_privilege('svc_app', c.oid, 'DELETE') AS can_remove "
        "  FROM pg_class c WHERE c.relname = 'security_recommendations'"))[0]
    assert row["relrowsecurity"] and row["relforcerowsecurity"] and row["policies"] == 1
    assert row["can_read"] and row["can_add"] and not row["can_change"] and not row["can_remove"]


@pytest.mark.asyncio
async def test_a_suggestion_without_a_reason_or_unavailable_without_saying_why_is_refused():
    w = await _world()
    await _alert(w, "intrusion", code="intrusion.zone_breach", at=_ago(seconds=60))
    await _pass(w)
    (s,) = await _rows(w, "security_situations")
    taken = {r["action"] for r in await _recs(w)}
    free = next(a for a in rec.ACTIONS if a not in taken)
    insert = ("INSERT INTO security_recommendations (tenant_id, situation_id, assessment_id, rank, action, priority, "
              "    reason, confidence, confidence_limited_by, available, unavailable_reason, supporting, "
              "    engine_version) VALUES (:t,:s,:a,:rank,:action,:priority,:reason,:conf,:by,:avail,:why,"
              "    CAST(:sup AS jsonb),'test')")
    ok = {"t": w["tenant"], "s": s["id"], "a": s["assessment_id"], "rank": 50, "action": free, "priority": "LOW",
          "reason": "Because.", "conf": 0.5, "by": "RULE", "avail": True, "why": None, "sup": "{}"}
    for bad in ({"rank": 1}, {"rank": 0}, {"action": next(iter(taken))}, {"action": "ARREST"},
                {"priority": "PANIC"}, {"reason": "  "}, {"conf": 1.2}, {"by": "MOOD"},
                {"avail": False}, {"avail": False, "why": " "}, {"why": "but it is available"}, {"sup": "[]"}):
        with pytest.raises(Exception):
            await _sql(insert, {**ok, **bad})
    await _sql(insert, {**ok, "avail": False, "why": "No guard is on shift at this site."})
    await _sql("DELETE FROM security_situations WHERE id = :s", {"s": s["id"]})
    assert await _recs(w) == [], "suggestions go with their situation"
