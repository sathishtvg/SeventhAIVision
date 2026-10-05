"""AI security intelligence, phase 11: one situation, in the order it happened.

  A — The rules, with nothing running: the order, whose each entry is, the
      words, what is folded and what is not said twice
  B — Through the API, from real records: a situation decided, carried out,
      reported on and closed — read back as one sequence
  C — Who sees what, and that it only reads

The claims this phase makes, each with tests: the timeline is made only of
records that already exist and points back at each; every entry says whether a
source, the layer, a person or the platform is behind it; a suggestion is never
worded or marked as a decision; and someone who may not see suggestions sees
the same timeline without them.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.services import intel_actions
from app.services import intel_decisions as dec
from app.services import intel_timeline as tl
from tests.test_drone_api import ADMIN, GUARD, OPERATOR, SUPERVISOR, VIEWER, _client, _sql
from tests.test_intel_decisions import BASE, POLICY_C, _ago, _decide, _pass, _ready, _url
from tests.test_intel_events import _alert

T0 = datetime(2026, 10, 5, 2, 17, 4, tzinfo=timezone.utc)
SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"
OFFICER = {"user_id": uuid.uuid4(), "name": "Priya", "role_id": OPERATOR}
GUARD_P = {"user_id": uuid.uuid4(), "name": "Tan Wei Ming", "role_id": GUARD}
SENIOR = {"user_id": uuid.uuid4(), "name": "Kumar", "role_id": SUPERVISOR}


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _event(seconds: float, title: str, *, method: str = "FIRST_EVENT", reason: str = "The first event of the situation.",
           source: str = "CCTV_AI", duplicate: bool = False, **over) -> dict:
    return {"id": uuid.uuid4(), "source_type": source, "source_table": "alerts", "source_id": uuid.uuid4(),
            "event_type": "intrusion.zone_breach", "occurred_at": _at(seconds), "title": title, "camera_id": None,
            "camera_name": None, "location_label": "Gate 1", "alert_id": None, "incident_id": None, "method": method,
            "reason": reason, "is_duplicate": duplicate, **over}


def _assessment(seconds: float, sequence: int, level: str, score: int, count: int = 1) -> dict:
    return {"id": uuid.uuid4(), "sequence": sequence, "assessed_at": _at(seconds),
            "label": "Access refused, with activity seen nearby", "risk_level": level, "risk_score": score,
            "event_count": count}


def _rec(seconds: float, assessment: dict, rank: int, action: str, reason: str, available: bool = True,
         why: str | None = None) -> dict:
    return {"id": uuid.uuid4(), "assessment_id": assessment["id"], "rank": rank, "action": action, "reason": reason,
            "available": available, "unavailable_reason": why, "created_at": _at(seconds)}


def _action(seconds: float, sequence: int, action: str, result: str = "OK", *, target=None, detail=None) -> dict:
    return {"sequence": sequence, "action": action, "result": result, "executed_at": _at(seconds), "detail": detail,
            "through": None if result == "RECORDED" else intel_actions.THROUGH.get(action),
            "target_type": "incident" if target else None, "target_id": target}


def _decision(seconds: float, action: str, *, by: dict = OFFICER, basis: str = "FOLLOWED", reason: str | None = None,
              note: str | None = None, authority: str = "ALONE", approval: dict | None = None,
              actions: list | None = None, suggested: str | None = None, earlier: bool = False) -> dict:
    return {"id": uuid.uuid4(), "decided_at": _at(seconds), "action": action, "basis": basis, "reason": reason,
            "note": note, "decided_by": by, "via": "web", "authority": authority, "approval": approval,
            "suggested_action": suggested, "decided_on_an_earlier_assessment": earlier, "actions": actions or []}


def _built(**over) -> list[dict]:
    rows = dict(events=[], assessments=[], recommendations=[], reviews=[], decisions_=[], observations=[],
                incidents=[])
    return tl.build(**{**rows, **over})


# ─── A. The rules ────────────────────────────────────────────────────────────

def test_the_specifications_own_timeline_is_told_in_order_and_each_line_says_whose_it_is():
    incident = uuid.uuid4()
    first = _assessment(58, 1, "HIGH", 65, 3)
    entries = _built(
        events=[_event(0, "Person at Gate 1"),
                _event(4, "Access denied at the rear door", method="ACCESS_AT_CAMERA", source="ACCESS_CONTROL",
                       reason="An access event at the door this camera watches, 4 s apart."),
                _event(39, "Possible unauthorised person", method="NEAR_POSITION", source="DRONE_PATROL",
                       reason="The drone's sighting was 20 m from Gate 1, 39 s apart.")],
        assessments=[first],
        recommendations=[_rec(62, first, 1, "DISPATCH_GUARD", "More than one kind of source reported this: send a guard."),
                         _rec(62, first, 2, "VIEW_CAMERA", "Follow it on camera while the guard is on the way.")],
        reviews=[{"user_id": OFFICER["user_id"], "name": "Priya", "role_id": OPERATOR, "assessment_id": first["id"],
                  "via": "web", "viewed_at": _at(66)}],
        decisions_=[
            _decision(71, "DISPATCH_GUARD", actions=[_action(72, 1, "INCIDENT_CREATE", target=incident),
                                                     _action(73, 2, "INCIDENT_DISPATCH", target=incident)]),
            _decision(656, "RESOLVE", by=SENIOR, basis="CLOSING", reason="Authorised activity",
                      actions=[_action(656, 1, "ALERT_DISMISS"), _action(656.5, 2, "INCIDENT_RESOLVE", target=incident)])],
        observations=[{"id": uuid.uuid4(), "kind": "ARRIVED", "note": None, **GUARD_P, "latitude": 1.3, "longitude": 103.8,
                       "via": "mobile", "observed_at": _at(447)},
                      {"id": uuid.uuid4(), "kind": "OBSERVATION", "note": "Authorised maintenance worker, badge checked.",
                       **GUARD_P, "latitude": None, "longitude": None, "via": "mobile", "observed_at": _at(606)}],
        incidents=[{"id": incident, "status": "resolved", "is_auto_created": False, "created_at": _at(72),
                    "dispatched_at": _at(73), "guard_arrived_at": None, "escalated_at": None, "resolved_at": _at(656.5)}])

    assert [(e["actor"], e["kind"], e["title"]) for e in entries] == [
        ("SOURCE", "EVENT", "Person at Gate 1"),
        ("SOURCE", "EVENT", "Access denied at the rear door"),
        ("SOURCE", "EVENT", "Possible unauthorised person"),
        ("AI", "ASSESSMENT", "AI-assisted assessment: Access refused, with activity seen nearby. Risk HIGH (65)."),
        ("AI", "RECOMMENDATION", "AI suggests: dispatch a guard"),
        ("PERSON", "REVIEW", "Looked at what was suggested"),
        ("PERSON", "DECISION", "Decided: dispatch a guard"),
        ("PLATFORM", "ACTION", "Incident opened"),
        ("PLATFORM", "ACTION", "Guard dispatched"),
        ("PERSON", "OBSERVATION", "Arrived"),
        ("PERSON", "OBSERVATION", "Reported from the ground: Authorised maintenance worker, badge checked."),
        ("PERSON", "DECISION", "Decided: resolve it"),
        ("PLATFORM", "ACTION", "Alerts closed"),
        ("PLATFORM", "ACTION", "Incident resolved"),
    ]
    assert [e["at"] for e in entries] == sorted(e["at"] for e in entries)
    assert tl.counts(entries) == {"SOURCE": 3, "AI": 2, "PERSON": 5, "PLATFORM": 4}
    by = {e["title"]: e for e in entries}
    assert by["Person at Gate 1"]["detail"] is None and by["Person at Gate 1"]["first"] is True
    assert by["Access denied at the rear door"]["detail"] == "An access event at the door this camera watches, 4 s apart."
    assert by["AI suggests: dispatch a guard"]["detail"] == (
        "More than one kind of source reported this: send a guard. 1 other step(s) were also listed.")
    assert by["Decided: dispatch a guard"]["who"] == OFFICER
    assert by["Decided: resolve it"]["detail"] == "Closed the situation — Authorised activity."
    assert by["Arrived"]["who"] == GUARD_P and by["Arrived"]["with_position"] is True
    assert by["Guard dispatched"]["through"] == "app.routers.dispatch.dispatch_guard"
    # The incident's own times add nothing the steps did not already say.
    assert not [e for e in entries if e["kind"] == "INCIDENT"]


def test_every_entry_points_back_at_the_record_it_was_read_from_and_names_one_of_four_actors():
    a = _assessment(10, 1, "MEDIUM", 45)
    entries = _built(events=[_event(0, "Person at Gate 1")], assessments=[a],
                     recommendations=[_rec(11, a, 1, "MONITOR", "Keep watching.")],
                     decisions_=[_decision(20, "MONITOR", actions=[_action(20, 1, "NONE", "RECORDED")])])
    assert [e["ref"]["type"] for e in entries] == ["event", "assessment", "recommendation", "decision", "decision"]
    assert entries[1]["ref"]["id"] == a["id"]
    for e in entries:
        assert e["actor"] in tl.ACTORS and e["kind"] in tl.KINDS and e["ref"]["id"] is not None
        assert set(e) >= {"at", "kind", "actor", "title", "detail", "who", "ref"} and "_order" not in e
    assert set(tl.ACTOR_OF) == set(tl.KINDS) and set(tl.ACTOR_OF.values()) == set(tl.ACTORS)


def test_what_the_layer_said_is_never_worded_or_marked_as_what_a_person_decided():
    a = _assessment(10, 1, "HIGH", 60)
    entries = _built(events=[_event(0, "Person at Gate 1")], assessments=[a],
                     recommendations=[_rec(11, a, 1, "DISPATCH_GUARD", "Send a guard.")],
                     decisions_=[_decision(20, "DISPATCH_GUARD")])
    ai = [e for e in entries if e["actor"] == "AI"]
    assert all(e["title"].startswith("AI") for e in ai), "every line of the layer's says so in its first word"
    suggestion = next(e for e in entries if e["kind"] == "RECOMMENDATION")
    assert suggestion["is_decision"] is False and "who" in suggestion and suggestion["who"] is None
    assert not any(word in suggestion["title"].lower() for word in ("decid", "approv", "dispatched"))
    decided = next(e for e in entries if e["kind"] == "DECISION")
    assert decided["actor"] == "PERSON" and decided["who"] == OFFICER and "AI" not in decided["title"]
    assert decided["detail"] == "Followed what the layer suggested."


def test_entries_at_the_same_instant_are_put_as_cause_before_effect():
    a = _assessment(5, 1, "HIGH", 60)
    entries = _built(
        events=[_event(5, "Person at Gate 1")], assessments=[a],
        recommendations=[_rec(5, a, 1, "MONITOR", "Keep watching.")],
        reviews=[{"user_id": None, "name": None, "role_id": OPERATOR, "assessment_id": a["id"], "via": "web",
                  "viewed_at": _at(5)}],
        decisions_=[_decision(5, "ACKNOWLEDGE", basis="INDEPENDENT",
                              actions=[_action(5, 2, "ALERT_ASSIGN"), _action(5, 1, "ALERT_ACKNOWLEDGE")])],
        observations=[{"id": uuid.uuid4(), "kind": "ACCEPTED", "note": None, **GUARD_P, "latitude": None,
                       "longitude": None, "via": "mobile", "observed_at": _at(5)}])
    assert [e["kind"] for e in entries] == ["EVENT", "ASSESSMENT", "RECOMMENDATION", "REVIEW", "DECISION", "ACTION",
                                            "ACTION", "OBSERVATION"]
    assert [e["title"] for e in entries if e["kind"] == "ACTION"] == ["Alerts acknowledged", "Alerts assigned"], \
        "a decision's steps in the order they were taken"


def test_repeats_of_one_alert_are_one_line_and_say_how_many():
    cam = uuid.uuid4()
    again = dict(method="SAME_SOURCE_REPEAT", duplicate=True, camera_id=cam, camera_name="Gate 1",
                 reason="The same alert from Gate 1 again.")
    entries = _built(events=[_event(0, "Person at Gate 1", camera_id=cam, camera_name="Gate 1"),
                             _event(40, "Person at Gate 1", **again), _event(95, "Person at Gate 1", **again),
                             _event(130, "Person at Gate 1", **again),
                             _event(60, "Smoke in the plant room", **{**again, "camera_id": uuid.uuid4(),
                                                                     "event_type": "fire_smoke.detected"})])
    assert [(e["kind"], e["title"]) for e in entries] == [
        ("EVENT", "Person at Gate 1"),
        ("REPEATS", "The same alert again, 3 time(s): Person at Gate 1"),
        ("REPEATS", "The same alert again, 1 time(s): Smoke in the plant room")]
    three, one = entries[1], entries[2]
    assert (three["at"], three["until"], three["count"], three["where"]) == (_at(40), _at(130), 3, "Gate 1")
    assert three["detail"] == one["detail"] == "Folded as repeats. Each is still an alert of its own."
    assert (one["at"], one["until"], one["count"], one["actor"]) == (_at(60), _at(60), 1, "SOURCE")


def test_an_assessment_made_again_says_what_it_was_before():
    entries = _built(assessments=[_assessment(120, 2, "CRITICAL", 85, 3), _assessment(60, 1, "HIGH", 65, 1)])
    assert [e["sequence"] for e in entries] == [1, 2]
    assert entries[0]["detail"] == "On 1 event(s)."
    assert entries[1]["detail"] == "Assessed again. On 3 event(s). Before: HIGH (65)."
    assert (entries[1]["risk_level"], entries[1]["risk_score"]) == ("CRITICAL", 85)


def test_a_set_of_suggestions_is_one_line_and_says_so_when_nothing_could_be_done():
    a, b = _assessment(10, 1, "HIGH", 60), _assessment(90, 2, "HIGH", 70)
    entries = _built(recommendations=[
        _rec(12, a, 2, "MONITOR", "If it looks ordinary, keep watching."),
        _rec(12, a, 1, "DISPATCH_GUARD", "Send a guard to check.", False, "No guard is on shift at this site."),
        _rec(92, b, 1, "DISPATCH_GUARD", "Send a guard to check.", False, "No guard is on shift at this site.")])
    assert [(e["title"], e["action"]) for e in entries] == [
        ("AI suggests: keep watching", "MONITOR"), ("AI suggests nothing that can be done right now", "DISPATCH_GUARD")]
    assert entries[0]["detail"] == "If it looks ordinary, keep watching. 1 other step(s) were also listed."
    assert entries[1]["detail"] == "Dispatch a guard was listed as not possible: No guard is on shift at this site."
    assert all(e["is_decision"] is False for e in entries)


def test_someone_who_may_not_see_suggestions_gets_the_same_timeline_without_them():
    a = _assessment(10, 1, "HIGH", 60)
    rows = dict(events=[_event(0, "Person at Gate 1")], assessments=[a],
                decisions_=[_decision(20, "MONITOR", basis="OVERRIDE", reason="Guard already responding",
                                      suggested="DISPATCH_GUARD")])
    full = _built(**rows, recommendations=[_rec(11, a, 1, "DISPATCH_GUARD", "Send a guard.")])
    without = _built(**rows, recommendations=None)
    assert [e["kind"] for e in full] == ["EVENT", "ASSESSMENT", "RECOMMENDATION", "DECISION"]
    assert [e["kind"] for e in without] == ["EVENT", "ASSESSMENT", "DECISION"]
    assert [e for e in full if e["kind"] != "RECOMMENDATION"] == without


def test_a_decision_says_whether_it_followed_overrode_closed_or_was_the_persons_own():
    def detail(**kw) -> str:
        return _built(decisions_=[_decision(1, "MONITOR", **kw)])[0]["detail"]

    assert detail(basis="FOLLOWED") == "Followed what the layer suggested."
    assert detail(basis="OVERRIDE", reason="Guard already responding", suggested="DISPATCH_GUARD") == (
        "An override — Guard already responding. The layer had put “dispatch a guard” first.")
    assert detail(basis="OVERRIDE", reason="Other", note="  Sending the patrol car.  ") == (
        "An override — Other. Note: Sending the patrol car.")
    assert detail(basis="CLOSING", reason="False detection") == "Closed the situation — False detection."
    assert detail(basis="INDEPENDENT") == "Their own decision."
    assert detail(basis="FOLLOWED", earlier=True).endswith("Made on an earlier assessment than the latest at the time.")
    assert set(tl.STEP_WORDS) == set(dec.DECISIONS), "a word for every decision, and for nothing else"


def test_a_proposal_its_verdict_and_what_was_then_done_are_three_lines_by_two_people():
    def lines(verdict: str, note: str | None, actions: list) -> list[tuple]:
        approval = {"verdict": verdict, "note": note, "at": _at(60), "by": SENIOR}
        entries = _built(decisions_=[_decision(10, "CREATE_INCIDENT", by=GUARD_P, authority="WITH_APPROVAL",
                                               approval=approval, actions=actions)])
        return [(e["kind"], e["title"], (e["who"] or {}).get("name"), e["detail"]) for e in entries]

    assert lines("APPROVED", "Go ahead.", [_action(61, 1, "INCIDENT_CREATE", target=uuid.uuid4())]) == [
        ("DECISION", "Proposed, to wait for approval: open an incident", "Tan Wei Ming",
         "Followed what the layer suggested."),
        ("APPROVAL", "Approved: open an incident", "Kumar", "Go ahead."),
        ("ACTION", "Incident opened", None, None)]
    assert lines("REJECTED", "Wait for the camera.", []) == [
        ("DECISION", "Proposed, to wait for approval: open an incident", "Tan Wei Ming",
         "Followed what the layer suggested."),
        ("APPROVAL", "Rejected: open an incident", "Kumar", "Wait for the camera.")]
    waiting = _built(decisions_=[_decision(10, "CREATE_INCIDENT", by=GUARD_P, authority="WITH_APPROVAL")])
    assert [e["kind"] for e in waiting] == ["DECISION"], "nothing is said to be done while it waits"


def test_a_step_says_how_it_ended_and_a_failure_is_not_worded_as_done():
    def title(action: str, result: str) -> str:
        return _built(decisions_=[_decision(1, "RESOLVE", actions=[_action(2, 1, action, result)])])[1]["title"]

    assert title("INCIDENT_DISPATCH", "OK") == "Guard dispatched"
    assert title("INCIDENT_DISPATCH", "FAILED") == "Could not dispatch the guard"
    assert title("INCIDENT_RESOLVE", "SKIPPED") == "Did not need to resolve the incident"
    assert title("NONE", "RECORDED") == "Recorded — nothing was carried out by the platform"
    assert title("INCIDENT_CONFIRM", "RECORDED") == "Incident confirmed by a person"
    assert title("DRONE_HOLD", "OK") == "Flight asked to hold and look again"
    assert title("DRONE_LAUNCH", "FAILED") == "Could not start the mission"
    failed = _built(decisions_=[_decision(1, "VERIFY_WITH_DRONE", actions=[_action(
        2, 1, "DRONE_HOLD", "FAILED", detail="409: The drone is 212 m from where it saw this (limit 75 m).")])])[1]
    assert failed["detail"].startswith("409: The drone is 212 m") and failed["result"] == "FAILED"
    # A word for every step the platform can take, done and not done.
    steps = set(intel_actions.THROUGH) | {"INCIDENT_CONFIRM"}
    assert set(tl.DONE) == set(tl.TO_DO) == steps
    assert set(tl.SAME_AS) <= steps


def test_the_incidents_own_times_are_told_only_where_no_step_from_here_already_tells_them():
    mine, theirs, sos = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    entries = _built(
        events=[_event(0, "Guard SOS", source="GUARD", source_table="incidents", source_id=sos)],
        decisions_=[_decision(30, "DISPATCH_GUARD", actions=[_action(31, 1, "INCIDENT_CREATE", target=mine),
                                                             _action(32, 2, "INCIDENT_DISPATCH", target=mine)])],
        incidents=[
            # Opened and dispatched from the decision above; the arrival was recorded on the existing screen.
            {"id": mine, "is_auto_created": False, "created_at": _at(31), "dispatched_at": _at(32),
             "guard_arrived_at": _at(400), "escalated_at": None, "resolved_at": _at(900)},
            # Opened by the platform for an alert, and escalated by its own timer.
            {"id": theirs, "is_auto_created": True, "created_at": _at(2), "dispatched_at": None,
             "guard_arrived_at": None, "escalated_at": _at(300), "resolved_at": None},
            # The SOS incident is itself the situation's first event.
            {"id": sos, "is_auto_created": True, "created_at": _at(0), "dispatched_at": None,
             "guard_arrived_at": None, "escalated_at": None, "resolved_at": None}])
    told = [(e["milestone"], e["title"], e["ref"]["id"]) for e in entries if e["kind"] == "INCIDENT"]
    assert told == [
        ("OPENED", "Incident opened by the platform itself — not confirmed by a person", theirs),
        ("ESCALATED", "Incident escalated", theirs),
        ("ARRIVED", "Guard's arrival recorded on the incident", mine),
        ("RESOLVED", "Incident resolved", mine)]
    assert all(e["actor"] == "PLATFORM" for e in entries if e["kind"] == "INCIDENT")
    # A step that failed did not do it: the incident's own record still speaks.
    failed = _built(decisions_=[_decision(30, "RESOLVE", actions=[_action(31, 1, "INCIDENT_RESOLVE", "FAILED",
                                                                          target=mine)])],
                    incidents=[{"id": mine, "is_auto_created": False, "created_at": _at(1), "dispatched_at": None,
                                "guard_arrived_at": None, "escalated_at": None, "resolved_at": _at(500)}])
    assert [e["title"] for e in failed if e["kind"] in ("ACTION", "INCIDENT")] == [
        "Incident opened", "Could not resolve the incident", "Incident resolved"]


def test_the_timeline_only_reads():
    source = (SERVICES / "intel_timeline.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", source, flags=re.S)
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE)\b", code) and ".commit(" not in code
    assert "intel_actions" not in code, "it describes what was done; it can do nothing"
    for name in ("intel_runner", "intel_events", "intel_correlation", "intel_risk", "intel_recommend"):
        assert "intel_timeline" not in (SERVICES / f"{name}.py").read_text(encoding="utf-8"), name


# ─── B. Through the API ──────────────────────────────────────────────────────

async def _timeline(c, w: dict, s: dict, role: int):
    return await c.get(_url(s, "timeline"), headers=w["h"][role])


@pytest.mark.asyncio
async def test_a_situation_decided_carried_out_reported_on_and_closed_reads_back_as_one_sequence():
    w, s = await _ready(severity="critical", on_shift=True)
    guard = str(w["users"][GUARD])
    async with _client() as c:
        await c.post(_url(s, "reviews"), headers=w["h"][OPERATOR], json={})
        sent = await _decide(c, w, s, SUPERVISOR, "DISPATCH_GUARD", guard_user_id=guard)
        assert sent.status_code == 201, sent.text
        arrived = await c.post(_url(s, "observations"), headers=w["h"][GUARD],
                               json={"kind": "ARRIVED", "latitude": 1.3001, "longitude": 103.8001})
        seen = await c.post(_url(s, "observations"), headers=w["h"][GUARD],
                            json={"kind": "OBSERVATION", "note": "Night cleaner, badge checked."})
        assert arrived.status_code == 201 and seen.status_code == 201, (arrived.text, seen.text)
        closed = await _decide(c, w, s, SUPERVISOR, "RESOLVE", reason_code="AUTHORISED_ACTIVITY")
        assert closed.status_code == 201, closed.text
        r = await _timeline(c, w, s, OPERATOR)
    assert r.status_code == 200, r.text
    body = r.json()
    entries = body["entries"]
    kinds = [e["kind"] for e in entries]
    assert kinds == ["EVENT", "ASSESSMENT", "RECOMMENDATION", "REVIEW", "DECISION", "ACTION", "ACTION", "OBSERVATION",
                     "OBSERVATION", "DECISION", "ACTION", "ACTION"], [(e["kind"], e["title"]) for e in entries]
    assert [e["at"] for e in entries] == sorted(e["at"] for e in entries)
    titles = [e["title"] for e in entries]
    assert titles[0] == "Person at Gate 1" and titles[1].startswith("AI-assisted assessment: ")
    assert titles[2].startswith("AI suggests: ") and entries[2]["is_decision"] is False
    assert titles[4:7] == ["Decided: dispatch a guard", "Incident opened", "Guard dispatched"]
    assert titles[7:9] == ["Arrived", "Reported from the ground: Night cleaner, badge checked."]
    assert titles[9:] == ["Decided: resolve it", "Alerts closed", "Incident resolved"]
    assert entries[4]["who"] == {"user_id": str(w["users"][SUPERVISOR]), "name": "Role 3 User", "role_id": SUPERVISOR}
    assert entries[7]["who"]["role_id"] == GUARD and entries[7]["with_position"] is True
    assert entries[3]["who"]["role_id"] == OPERATOR and entries[3]["title"] == "Looked at what was suggested"
    assert entries[5]["through"] == "app.routers.incidents.create_incident" and entries[5]["result"] == "OK"
    assert entries[9]["detail"] == "Closed the situation — Authorised activity."
    assert body["counts"] == {"SOURCE": 1, "AI": 2, "PERSON": 5, "PLATFORM": 4}
    assert body["situation_id"] == str(s["id"]) and body["suggestions_shown"] is True
    assert body["closed_at"] is not None and body["decision_status"] == "RESOLVED"
    # Every reference is to a record that exists.
    decision_ids = {str(x["id"]) for x in await _sql("SELECT id FROM security_decisions WHERE situation_id = :s",
                                                     {"s": s["id"]})}
    assert {e["ref"]["id"] for e in entries if e["ref"]["type"] == "decision"} == decision_ids


@pytest.mark.asyncio
async def test_a_proposal_that_waits_and_is_approved_is_told_as_it_happened():
    w, s = await _ready(severity="critical", policy=POLICY_C, on_shift=True)
    async with _client() as c:
        proposed = await _decide(c, w, s, GUARD, "CREATE_INCIDENT", via="mobile")
        assert proposed.status_code == 201, proposed.text
        waiting = (await _timeline(c, w, s, OPERATOR)).json()["entries"]
        approved = await c.post(f"{BASE}/decisions/{proposed.json()['id']}/approve", headers=w["h"][SUPERVISOR],
                                json={"note": "Go ahead."})
        assert approved.status_code == 200, approved.text
        after = (await _timeline(c, w, s, OPERATOR)).json()["entries"]
    assert [e["kind"] for e in waiting][-1] == "DECISION"
    assert waiting[-1]["title"] == "Proposed, to wait for approval: open an incident" and waiting[-1]["via"] == "mobile"
    tail = [(e["kind"], e["title"], e["who"]["role_id"] if e["who"] else None) for e in after[-3:]]
    assert tail == [("DECISION", "Proposed, to wait for approval: open an incident", GUARD),
                    ("APPROVAL", "Approved: open an incident", SUPERVISOR),
                    ("ACTION", "Incident opened", None)]
    assert after[-2]["detail"] == "Go ahead."


# ─── C. Who sees what ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_viewer_reads_the_timeline_without_the_suggestions_and_nobody_outside_reads_it_at_all():
    w, s = await _ready(severity="critical")
    other, _ = await _ready()
    async with _client() as c:
        await _decide(c, w, s, OPERATOR, "MONITOR", reason_code="OTHER", note="Watching.")
        full = (await _timeline(c, w, s, OPERATOR)).json()
        viewer = await _timeline(c, w, s, VIEWER)
        outsider = await c.get(_url(s, "timeline"), headers=other["h"][ADMIN])
        nobody = await c.get(_url(s, "timeline"))
        missing = await c.get(f"{BASE}/situations/{uuid.uuid4()}/timeline", headers=w["h"][ADMIN])
    assert viewer.status_code == 200
    seen = viewer.json()
    assert seen["suggestions_shown"] is False and "RECOMMENDATION" not in [e["kind"] for e in seen["entries"]]
    assert [e for e in full["entries"] if e["kind"] != "RECOMMENDATION"] == seen["entries"]
    assert seen["counts"]["AI"] == full["counts"]["AI"] - 1, "the assessment is theirs to read; the suggestion is not"
    assert outsider.status_code == 404 and missing.status_code == 404 and nobody.status_code in (401, 403)


@pytest.mark.asyncio
async def test_reading_the_timeline_changes_nothing_and_a_restricted_user_sees_only_their_sites():
    w, s = await _ready(severity="critical")
    # A second matter, at the site this world's supervisor is not given.
    elsewhere = await _alert(w, "weapon", camera="cam_b", site="site_b", code="weapon.detected", severity="critical",
                             at=_ago(seconds=50))
    await _pass(w)
    other = dict((await _sql("""
        SELECT s.* FROM security_situations s JOIN security_situation_events l ON l.situation_id = s.id
          JOIN security_events e ON e.id = l.event_id WHERE e.source_id = :a""", {"a": elsewhere}))[0])

    async def marks() -> list:
        return [(await _sql(f"SELECT count(*) AS n, max(xmin::text::bigint) AS x FROM {name} WHERE tenant_id = :t",
                            {"t": w["tenant"]}))[0] for name in (
            "security_situations", "security_events", "security_assessments", "security_recommendations",
            "security_decisions", "security_actions", "security_reviews", "alerts", "incidents", "audit_logs")]

    before = await marks()
    async with _client() as c:
        for _ in range(2):
            assert (await _timeline(c, w, s, OPERATOR)).status_code == 200
        theirs = await _timeline(c, w, s, SUPERVISOR)
        not_theirs = await _timeline(c, w, other, SUPERVISOR)
        admin = await _timeline(c, w, other, ADMIN)
    assert await marks() == before, "reading wrote something"
    assert theirs.status_code == 200 and admin.status_code == 200
    assert not_theirs.status_code == 404, "that situation is at site B; this supervisor is kept to site A"
