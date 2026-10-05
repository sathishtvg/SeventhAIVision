"""AI security intelligence, phase 7: what a person decided, and what was then done.

  A — The rules, with nothing running: the decision policy, what kind of
      decision a choice is, what carrying it out takes, who is refused
  B — Deciding, through the API: followed, overridden, closed; each step
      carried out by the platform's own function, and recorded
  C — Who may not decide, and deciding with a second person's approval
  D — The trail, the audit log, the policy API
  E — The schema

The claims this phase makes, each with tests: only a signed-in person decides;
an override is never blocked and always says why; a decision the policy lets a
role take only with approval carries nothing out until a second person
approves; what was suggested, what was decided and what was done are three
records; and nothing is carried out except through a function the platform
already had.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload
from app.routers import security_decisions as api
from app.services import intel_actions
from app.services import intel_correlation as corr
from app.services import intel_decisions as dec
from app.services import intel_recommend as rec
from app.services import intel_risk as risk
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql
from tests.test_intel_events import _alert, _read, _world

NOW = datetime.now(timezone.utc)
BASE = "/api/v1/security-intelligence"
POLICY_A = {"5": {}}                                              # the command centre decides
POLICY_B = {"5": {"alone": "MEDIUM"}}                             # a guard handles low and medium
POLICY_C = {"5": {"alone": "MEDIUM", "with_approval": "CRITICAL"}}  # high risk needs approval
EVERYTHING = {"intel:decide", "intel:override", "intel:approve", "alert:acknowledge", "incident:create",
              "incident:dispatch", "incident:assign", "incident:resolve"}


def _ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


# ─── A. The rules ────────────────────────────────────────────────────────────

def test_by_default_the_command_centre_decides_and_a_guard_does_not():
    for role in (ADMIN, MANAGER, SUPERVISOR, OPERATOR):
        for level in risk.LEVELS:
            assert dec.authority(None, role, level, "DISPATCH_GUARD")[0] == "ALONE"
    for level in risk.LEVELS:
        how, why = dec.authority(None, GUARD, level, "MONITOR")
        assert how is None and why == "The decision policy does not let a guard decide here."
    assert dec.authority(None, VIEWER, "LOW", "MONITOR")[0] is None, "a role no policy speaks of decides nothing"
    assert dec.authority(None, 7, "LOW", "MONITOR")[0] is None
    # Asking the command centre for help is handing the decision up, not taking it.
    assert dec.authority(POLICY_A, GUARD, "CRITICAL", "REQUEST_ASSISTANCE")[0] == "ALONE"


def test_the_three_policies_of_the_specification_are_each_a_setting():
    def guard(policy, level, action="INVESTIGATE"):
        return dec.authority(policy, GUARD, level, action)[0]

    assert [guard(POLICY_A, lvl) for lvl in risk.LEVELS] == [None] * 5
    assert [guard(POLICY_B, lvl) for lvl in risk.LEVELS] == ["ALONE", "ALONE", "ALONE", None, None]
    assert [guard(POLICY_C, lvl) for lvl in risk.LEVELS] == ["ALONE", "ALONE", "ALONE", "WITH_APPROVAL",
                                                             "WITH_APPROVAL"]
    assert dec.authority(POLICY_C, GUARD, "HIGH", "RESOLVE")[1] == \
        "Guard may decide up to CRITICAL with approval; this is HIGH."
    assert dec.authority(POLICY_B, GUARD, "HIGH", "RESOLVE")[1] == \
        "Guard may decide up to MEDIUM under the decision policy; this is HIGH."
    # A policy that speaks only of guards leaves everyone else as they were…
    assert dec.authority(POLICY_A, OPERATOR, "CRITICAL", "ESCALATE")[0] == "ALONE"
    # …and one can hold the command centre's own roles back too.
    held = {"4": {"alone": "HIGH"}, "3": {"alone": "MEDIUM", "with_approval": "CRITICAL"}}
    assert dec.authority(held, OPERATOR, "HIGH", "MONITOR")[0] == "ALONE"
    assert dec.authority(held, OPERATOR, "CRITICAL", "MONITOR")[0] is None
    assert dec.authority(held, SUPERVISOR, "HIGH", "MONITOR")[0] == "WITH_APPROVAL"


def test_a_situation_not_yet_assessed_asks_for_the_most_authority_not_the_least():
    assert dec.authority(POLICY_B, GUARD, None, "MONITOR")[0] is None
    how, why = dec.authority(POLICY_C, GUARD, None, "MONITOR")
    assert how == "WITH_APPROVAL" and "CRITICAL (not yet assessed)" in why
    assert dec.authority(None, OPERATOR, None, "MONITOR")[0] == "ALONE"


def test_a_policy_that_makes_no_sense_is_refused():
    for good in (POLICY_A, POLICY_B, POLICY_C, {}, {"3": {"alone": None, "with_approval": "LOW"}}):
        dec.validate_policy(good)
    for bad in ("guards decide", ["5"], {"6": {"alone": "LOW"}}, {"1": {"alone": "CRITICAL"}}, {"7": {}},
                {"5": "MEDIUM"}, {"5": {"alone": "SEVERE"}}, {"5": {"up_to": "LOW"}},
                {"5": {"alone": "HIGH", "with_approval": "MEDIUM"}}, {"5": {"alone": "HIGH", "with_approval": "HIGH"}}):
        with pytest.raises(ValueError):
            dec.validate_policy(bad)


def _suggested(*steps) -> list[dict]:
    return [{"id": uuid.uuid4(), "rank": i, "action": a, "available": ok} for i, (a, ok) in enumerate(steps, 1)]


def test_a_choice_follows_what_was_suggested_overrides_it_closes_the_matter_or_does_neither():
    current = _suggested(("VIEW_CAMERA", True), ("CREATE_INCIDENT", True), ("DISPATCH_GUARD", False))
    assert dec.classify("VIEW_CAMERA", current) == ("FOLLOWED", current[0])
    assert dec.classify("CREATE_INCIDENT", current) == ("FOLLOWED", current[1]), "any suggestion, not only the first"
    assert dec.classify("MONITOR", current) == ("OVERRIDE", None), "not suggested"
    assert dec.classify("DISPATCH_GUARD", current) == ("OVERRIDE", current[2]), "suggested, but listed as not possible"
    for closing in dec.CLOSING:
        assert dec.classify(closing, current) == ("CLOSING", None)
    for own in ("ACKNOWLEDGE", "REQUEST_ASSISTANCE", "CONFIRM_INCIDENT"):
        assert dec.classify(own, current) == ("INDEPENDENT", None)
    assert dec.classify("MONITOR", []) == ("INDEPENDENT", None), "nothing had been suggested to go against"
    assert dec.first_suggestion(current) == "VIEW_CAMERA"
    assert dec.first_suggestion(_suggested(("DISPATCH_GUARD", False), ("ESCALATE", True))) == "ESCALATE"
    assert dec.first_suggestion([]) is None
    assert set(dec.DECISIONS) == set(rec.ACTIONS) | set(dec.OWN) and len(dec.DECISIONS) == 14


A1, A2, A3, INC = (uuid.uuid4() for _ in range(4))
ALERTS = [{"id": A1, "status": "open"}, {"id": A2, "status": "acknowledged"}, {"id": A3, "status": "dismissed"}]
OPEN_INCIDENT = {"id": INC, "status": "open", "is_auto_created": True, "dispatched_guard_id": None}


def test_what_carrying_out_takes_is_planned_from_what_the_situation_has():
    def steps(action, alerts=ALERTS, incident=None):
        return [(s.action, s.permission) for s in dec.plan(action, alerts=alerts, incident=incident)]

    assert steps("ACKNOWLEDGE") == [("ALERT_ACKNOWLEDGE", "alert:acknowledge")]
    assert dec.plan("ACKNOWLEDGE", alerts=ALERTS, incident=None)[0].ids == (str(A1),), "only the alert still open"
    assert dec.plan("FALSE_POSITIVE", alerts=ALERTS, incident=None)[0].ids == (str(A1), str(A2))
    assert steps("RESOLVE") == [("ALERT_DISMISS", "alert:acknowledge")]
    assert steps("RESOLVE", incident=OPEN_INCIDENT) == [("ALERT_DISMISS", "alert:acknowledge"),
                                                        ("INCIDENT_RESOLVE", "incident:resolve")]
    assert steps("RESOLVE", incident={**OPEN_INCIDENT, "status": "resolved"}) == [("ALERT_DISMISS", "alert:acknowledge")]
    assert steps("ESCALATE", incident=OPEN_INCIDENT) == [("ALERT_ASSIGN", "alert:acknowledge"),
                                                         ("INCIDENT_ASSIGN", "incident:assign")]
    assert steps("CREATE_INCIDENT") == [("INCIDENT_CREATE", "incident:create")]
    assert steps("CONFIRM_INCIDENT", incident=OPEN_INCIDENT) == [("INCIDENT_CONFIRM", None)]
    assert steps("DISPATCH_GUARD") == [("INCIDENT_CREATE", "incident:create"), ("INCIDENT_DISPATCH", "incident:dispatch")]
    assert steps("DISPATCH_GUARD", incident=OPEN_INCIDENT) == [("INCIDENT_DISPATCH", "incident:dispatch")]
    assert dec.plan("DISPATCH_GUARD", alerts=[], incident=OPEN_INCIDENT)[0].target_id == INC
    # A record and nothing more.
    for action in ("MONITOR", "VIEW_CAMERA", "VERIFY", "VERIFY_WITH_DRONE", "INVESTIGATE", "CONTACT_SITE",
                   "REQUEST_ASSISTANCE"):
        assert steps(action, incident=OPEN_INCIDENT) == []
    assert steps("ACKNOWLEDGE", alerts=[]) == [] and steps("RESOLVE", alerts=[]) == []
    assert dec.permissions_needed(dec.plan("RESOLVE", alerts=ALERTS, incident=OPEN_INCIDENT)) == \
        ["alert:acknowledge", "incident:resolve"]


def test_where_a_situation_stands_after_each_decision():
    assert dec.status_after("MONITOR", "ALONE", "AWAITING") == "IN_HAND"
    assert dec.status_after("ACKNOWLEDGE", "ALONE", "AWAITING") == "ACKNOWLEDGED"
    assert dec.status_after("ACKNOWLEDGE", "ALONE", "IN_HAND") == "IN_HAND", "seen is not a step back from acting"
    assert dec.status_after("REQUEST_ASSISTANCE", "ALONE", "AWAITING") == "ASSISTANCE_REQUESTED"
    assert dec.status_after("RESOLVE", "ALONE", "IN_HAND") == "RESOLVED"
    assert dec.status_after("FALSE_POSITIVE", "ALONE", "AWAITING") == "FALSE_POSITIVE"
    for action in ("RESOLVE", "DISPATCH_GUARD", "ACKNOWLEDGE"):
        assert dec.status_after(action, "WITH_APPROVAL", "AWAITING") == "PENDING_APPROVAL", \
            "waiting for a second person, nothing has happened yet"
    assert {dec.status_after(a, h, "AWAITING") for a in dec.DECISIONS for h in ("ALONE", "WITH_APPROVAL")} \
        <= set(dec.STATUSES)


def _facts(level="HIGH", current=None, alerts=ALERTS, incident=None) -> dec.Facts:
    return dec.Facts(assessment={"id": uuid.uuid4(), "risk_level": level, "risk_score": 60},
                     current=current if current is not None else _suggested(("VIEW_CAMERA", True)),
                     alerts=alerts, incident=incident)


def test_each_reason_a_decision_is_refused_and_in_what_order():
    open_ = {"id": uuid.uuid4(), "closed_at": None, "incident_confirmed_at": None}

    def refused(action, *, situation=open_, f=None, mine=EVERYTHING, roles=None, role=OPERATOR):
        c = dec.check(action, situation=situation, f=f or _facts(), mine=set(mine), roles=roles, role_id=role)
        return c.refusal

    assert refused("VIEW_CAMERA") is None
    assert refused("VIEW_CAMERA", situation={**open_, "closed_at": NOW})[0] == 409
    assert refused("VIEW_CAMERA", mine=EVERYTHING - {"intel:decide"}) == (403, "Deciding needs the permission intel:decide.")
    assert refused("VIEW_CAMERA", role=GUARD) == (403, "The decision policy does not let a guard decide here.")
    status, why = refused("MONITOR", mine=EVERYTHING - {"intel:override"})
    assert status == 403 and "intel:override" in why
    assert refused("MONITOR") is None, "someone who may override is not held back"
    assert refused("CREATE_INCIDENT", f=_facts(incident=OPEN_INCIDENT)) == \
        (409, "An incident is already open for this situation. Confirm it instead.")
    assert refused("CONFIRM_INCIDENT") == (409, "There is no open incident to confirm.")
    assert refused("CONFIRM_INCIDENT", f=_facts(incident=OPEN_INCIDENT)) is None
    assert refused("CONFIRM_INCIDENT", f=_facts(incident={**OPEN_INCIDENT, "is_auto_created": False}))[0] == 409
    assert refused("CONFIRM_INCIDENT", situation={**open_, "incident_confirmed_at": NOW},
                   f=_facts(incident=OPEN_INCIDENT))[0] == 409
    # A decision still needs the platform's own permission for what it sets off.
    assert refused("ACKNOWLEDGE", mine=EVERYTHING - {"alert:acknowledge"}) == \
        (403, "Carrying this out needs the permission alert:acknowledge.")
    assert refused("RESOLVE", f=_facts(incident=OPEN_INCIDENT), mine=EVERYTHING - {"incident:resolve"})[0] == 403
    assert refused("RESOLVE", mine=EVERYTHING - {"incident:resolve"}) is None, "no incident, so nothing to resolve"
    # Proposed for approval, it is the approver who must hold them.
    c = dec.check("ACKNOWLEDGE", situation=open_, f=_facts(), mine={"intel:decide"}, roles=POLICY_C, role_id=GUARD)
    assert c.how == "WITH_APPROVAL" and c.refusal is None
    # intel:decide alone carries nothing out.
    alone = dec.check("ACKNOWLEDGE", situation=open_, f=_facts("LOW"), mine={"intel:decide"}, roles=POLICY_C,
                      role_id=GUARD)
    assert alone.how == "ALONE" and alone.refusal[0] == 403


def test_an_incident_software_opened_is_never_shown_as_one_a_person_stands_behind():
    s = {"incident_confirmed_at": None}
    assert dec.incident_state(s, None) == "NONE"
    assert dec.incident_state(s, OPEN_INCIDENT) == "PRELIMINARY"
    assert dec.incident_state(s, {**OPEN_INCIDENT, "is_auto_created": False}) == "CONFIRMED"
    assert dec.incident_state({"incident_confirmed_at": NOW}, OPEN_INCIDENT) == "CONFIRMED"


def test_a_decision_is_a_persons_not_a_keys_and_not_the_vendors():
    someone = TokenPayload(user_id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4()), role_id=ADMIN)
    api._person(someone)
    for not_a_person in (TokenPayload(user_id="k", tenant_id="t", role_id=ADMIN, via_api_key=True),
                         TokenPayload(user_id="v", tenant_id="t", role_id=ADMIN, support_session_id=str(uuid.uuid4()))):
        with pytest.raises(HTTPException) as refused:
            api._person(not_a_person)
        assert refused.value.status_code == 403


def test_every_step_goes_through_a_function_the_platform_already_had():
    import importlib

    assert set(intel_actions.THROUGH) | {"INCIDENT_CONFIRM", "NONE"} == {
        "ALERT_ACKNOWLEDGE", "ALERT_FALSE_POSITIVE", "ALERT_DISMISS", "ALERT_ASSIGN", "INCIDENT_CREATE",
        "INCIDENT_CONFIRM", "INCIDENT_DISPATCH", "INCIDENT_ASSIGN", "INCIDENT_RESOLVE", "NONE"}
    for path in intel_actions.THROUGH.values():
        module, name = path.rsplit(".", 1)
        assert module.startswith("app.routers.") and "intel" not in module and "security" not in module
        assert callable(getattr(importlib.import_module(module), name)), f"{path} does not exist"


# ─── B. Deciding ─────────────────────────────────────────────────────────────

async def _pass(w: dict) -> None:
    """Read, place, assess, suggest."""
    await _read(w, now=NOW)
    await corr.correlate_tenant(AsyncSessionLocal, w["tenant"], NOW)
    await risk.assess_tenant(AsyncSessionLocal, w["tenant"], NOW)
    await rec.recommend_tenant(AsyncSessionLocal, w["tenant"], NOW)


async def _rows(w: dict, table: str, order: str = "created_at") -> list[dict]:
    return [dict(r) for r in await _sql(f"SELECT * FROM {table} WHERE tenant_id = :t ORDER BY {order}",
                                        {"t": w["tenant"]})]


async def _on_shift(w: dict, role: int = GUARD, site: str = "site_a") -> None:
    await _sql("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, "
               "    status) VALUES (:t,:s,:g,:a,:b,:a,'active')",
               {"t": w["tenant"], "s": w[site], "g": w["users"][role], "a": _ago(hours=2),
                "b": NOW + timedelta(hours=6)})


async def _ready(*, severity: str = "high", on_shift: bool = False, policy: dict | None = None,
                 assessed: bool = True) -> tuple[dict, dict]:
    """A tenant with one situation: high is MEDIUM risk, critical is HIGH."""
    w = await _world()
    if on_shift:
        await _on_shift(w)
    if policy is not None:
        await _sql("INSERT INTO security_decision_policies (tenant_id, roles) VALUES (:t, CAST(:r AS jsonb))",
                   {"t": w["tenant"], "r": json.dumps(policy)})
    w["alert"] = await _alert(w, "intrusion", code="intrusion.zone_breach", severity=severity,
                              title="Person at Gate 1", at=_ago(seconds=60))
    if assessed:
        await _pass(w)
    else:
        await _read(w, now=NOW)
        await corr.correlate_tenant(AsyncSessionLocal, w["tenant"], NOW)
    (s,) = await _rows(w, "security_situations")
    return w, s


def _url(s: dict, tail: str = "decisions") -> str:
    return f"{BASE}/situations/{s['id']}/{tail}"


async def _decide(c, w: dict, s: dict, role: int, action: str, **body):
    return await c.post(_url(s), headers=w["h"][role], json={"action": action, **body})


async def _situation(w: dict, s: dict) -> dict:
    return dict((await _sql("SELECT * FROM security_situations WHERE id = :s", {"s": s["id"]}))[0])


@pytest.mark.asyncio
async def test_following_a_suggestion_is_recorded_as_followed_and_changes_nothing_it_need_not():
    w, s = await _ready()
    suggested = await _rows(w, "security_recommendations", "rank")
    assert [r["action"] for r in suggested] == ["VIEW_CAMERA", "MONITOR"]
    async with _client() as c:
        r = await _decide(c, w, s, OPERATOR, "VIEW_CAMERA", seen_assessment_id=str(s["assessment_id"]))
    d = r.json()
    assert r.status_code == 201, r.text
    assert (d["action"], d["basis"], d["is_override"]) == ("VIEW_CAMERA", "FOLLOWED", False)
    assert d["recommendation_id"] == str(suggested[0]["id"]) and d["suggested_action"] == "VIEW_CAMERA"
    assert d["decided_by"] == {"user_id": str(w["users"][OPERATOR]), "name": "Role 4 User", "role_id": OPERATOR}
    assert (d["authority"], d["state"], d["risk_level"], d["via"]) == ("ALONE", "EFFECTIVE", "MEDIUM", "web")
    assert d["assessment_id"] == str(s["assessment_id"]) and d["decided_on_an_earlier_assessment"] is False
    assert d["policy"]["source"] == "default" and d["approval"] is None
    assert [(a["action"], a["result"], a["through"]) for a in d["actions"]] == [("NONE", "RECORDED", None)]
    assert d["actions"][0]["detail"] == "Recorded. There was nothing for the platform to carry out."
    after = await _situation(w, s)
    assert (after["decision_status"], after["last_decision_id"], after["closed_at"]) == ("IN_HAND", uuid.UUID(d["id"]), None)
    (alert,) = await _rows(w, "alerts")
    assert alert["status"] == "open" and await _rows(w, "incidents") == [], "looking at a camera touches nothing"


@pytest.mark.asyncio
async def test_acknowledging_acknowledges_the_alerts_through_the_platforms_own_function():
    w, s = await _ready()
    async with _client() as c:
        r = await _decide(c, w, s, OPERATOR, "ACKNOWLEDGE")
        again = await _decide(c, w, s, OPERATOR, "ACKNOWLEDGE")
    d = r.json()
    assert r.status_code == 201 and (d["basis"], d["reason_code"]) == ("INDEPENDENT", None)
    assert [(a["action"], a["result"], a["through"], a["detail"]) for a in d["actions"]] == [
        ("ALERT_ACKNOWLEDGE", "OK", "app.routers.alerts.bulk_acknowledge_alerts", "1 alert(s) acknowledged.")]
    (alert,) = await _rows(w, "alerts")
    assert alert["status"] == "acknowledged" and alert["acknowledged_by_user_id"] == w["users"][OPERATOR]
    assert (await _situation(w, s))["decision_status"] == "ACKNOWLEDGED"
    # Nothing is left to acknowledge the second time, and the record says so.
    assert again.status_code == 201 and [a["action"] for a in again.json()["actions"]] == ["NONE"]


@pytest.mark.asyncio
async def test_an_override_needs_a_reason_and_is_then_not_blocked():
    w, s = await _ready(severity="critical")
    assert [(r["action"], r["available"]) for r in await _rows(w, "security_recommendations", "rank")] == [
        ("VIEW_CAMERA", True), ("CREATE_INCIDENT", True), ("DISPATCH_GUARD", False), ("CONTACT_SITE", False)]
    async with _client() as c:
        no_reason = await _decide(c, w, s, OPERATOR, "MONITOR")
        unknown = await _decide(c, w, s, OPERATOR, "MONITOR", reason_code="BORED")
        other = await _decide(c, w, s, OPERATOR, "MONITOR", reason_code="OTHER", note="   ")
        assert await _rows(w, "security_decisions", "decided_at") == [], "a refused decision records nothing"
        ok = await _decide(c, w, s, OPERATOR, "MONITOR", reason_code="GUARD_RESPONDING")
        # The layer said nobody was on shift to send. The officer knows better, and is not stopped.
        sent = await _decide(c, w, s, OPERATOR, "DISPATCH_GUARD", reason_code="OTHER",
                             note="Sending the patrol car from Factory B.", guard_user_id=str(w["users"][GUARD]))
    assert [r.status_code for r in (no_reason, unknown, other)] == [422, 422, 422]
    assert "needs a reason" in no_reason.json()["detail"] and "OTHER" in other.json()["detail"]
    d = ok.json()
    assert ok.status_code == 201 and (d["basis"], d["is_override"]) == ("OVERRIDE", True)
    assert (d["reason_code"], d["reason"], d["suggested_action"]) == ("GUARD_RESPONDING", "Guard already responding",
                                                                     "VIEW_CAMERA")
    assert d["recommendation_id"] is None
    o = sent.json()
    assert sent.status_code == 201 and o["basis"] == "OVERRIDE" and o["note"] == "Sending the patrol car from Factory B."
    assert o["recommendation_id"] is not None, "it names the suggestion that was listed as not possible"
    assert [(a["action"], a["result"]) for a in o["actions"]] == [("INCIDENT_CREATE", "OK"), ("INCIDENT_DISPATCH", "OK")]
    (incident,) = await _rows(w, "incidents")
    assert incident["dispatched_guard_id"] == w["users"][GUARD]


@pytest.mark.asyncio
async def test_dispatching_opens_an_incident_and_dispatches_through_the_existing_functions():
    w, s = await _ready(severity="critical", on_shift=True)
    assert "DISPATCH_GUARD" in [r["action"] for r in await _rows(w, "security_recommendations", "rank") if r["available"]]
    async with _client() as c:
        nobody = await _decide(c, w, s, OPERATOR, "DISPATCH_GUARD")
        stranger = await _decide(c, w, s, OPERATOR, "DISPATCH_GUARD", guard_user_id=str(uuid.uuid4()))
        r = await _decide(c, w, s, OPERATOR, "DISPATCH_GUARD", guard_user_id=str(w["users"][GUARD]),
                          note="North gate, approach from the car park.")
        seen = await c.get(f"{BASE}/situations/{s['id']}", headers=w["h"][OPERATOR])
    assert [x.status_code for x in (nobody, stranger)] == [422, 422] and "guard_user_id" in nobody.json()["detail"]
    d = r.json()
    assert r.status_code == 201 and d["basis"] == "FOLLOWED" and d["params"] == {"guard_user_id": str(w["users"][GUARD])}
    assert [(a["action"], a["result"], a["through"]) for a in d["actions"]] == [
        ("INCIDENT_CREATE", "OK", "app.routers.incidents.create_incident"),
        ("INCIDENT_DISPATCH", "OK", "app.routers.dispatch.dispatch_guard")]
    (incident,) = await _rows(w, "incidents")
    assert incident["is_auto_created"] is False and incident["title"] == "Person at Gate 1"
    assert incident["severity"] == "critical" and incident["camera_id"] == w["cam_a"]
    assert incident["dispatched_guard_id"] == w["users"][GUARD] and incident["dispatched_at"] is not None
    assert incident["dispatch_notes"] == "North gate, approach from the car park."
    assert s["situation_number"] in incident["description"] and "a person's decision" in incident["description"]
    assert {a["target_id"] for a in d["actions"]} == {str(incident["id"])}
    after = await _situation(w, s)
    assert after["incident_id"] == incident["id"] and after["incident_confirmed_at"] is not None
    assert seen.json()["incident"] == {"state": "CONFIRMED", "id": str(incident["id"]), "opened_by_the_platform": False}
    assert seen.json()["decision_status"] == "IN_HAND"


@pytest.mark.asyncio
async def test_escalating_hands_the_alerts_to_a_supervisor_and_only_to_one():
    w, s = await _ready()
    async with _client() as c:
        to_nobody = await _decide(c, w, s, OPERATOR, "ESCALATE", reason_code="EMERGENCY")
        to_myself = await _decide(c, w, s, OPERATOR, "ESCALATE", reason_code="EMERGENCY",
                                  escalate_to_user_id=str(w["users"][OPERATOR]))
        to_a_guard = await _decide(c, w, s, OPERATOR, "ESCALATE", reason_code="EMERGENCY",
                                   escalate_to_user_id=str(w["users"][GUARD]))
        r = await _decide(c, w, s, OPERATOR, "ESCALATE", reason_code="EMERGENCY",
                          escalate_to_user_id=str(w["users"][SUPERVISOR]))
    assert [x.status_code for x in (to_nobody, to_myself, to_a_guard)] == [422, 422, 422]
    d = r.json()
    assert r.status_code == 201 and d["basis"] == "OVERRIDE", "escalating was not among the suggestions at this risk"
    assert [(a["action"], a["result"], a["through"]) for a in d["actions"]] == [
        ("ALERT_ASSIGN", "OK", "app.routers.alerts.bulk_assign_alerts")]
    (alert,) = await _rows(w, "alerts")
    assert alert["assigned_to_user_id"] == w["users"][SUPERVISOR] and alert["status"] == "open"


@pytest.mark.asyncio
async def test_a_false_positive_closes_the_situation_with_a_reason_and_nothing_more_joins_it():
    w, s = await _ready()
    async with _client() as c:
        no_reason = await _decide(c, w, s, OPERATOR, "FALSE_POSITIVE")
        r = await _decide(c, w, s, OPERATOR, "FALSE_POSITIVE", reason_code="CAMERA_ISSUE", note="Spider on the lens.")
        more = await _decide(c, w, s, OPERATOR, "MONITOR", reason_code="OTHER", note="x")
        can = await c.get(_url(s, "authority"), headers=w["h"][OPERATOR])
    assert no_reason.status_code == 422 and "Closing a situation needs a reason" in no_reason.json()["detail"]
    d = r.json()
    assert r.status_code == 201 and (d["basis"], d["reason"], d["is_override"]) == ("CLOSING", "Camera issue", False)
    assert [(a["action"], a["result"], a["through"]) for a in d["actions"]] == [
        ("ALERT_FALSE_POSITIVE", "OK", "app.routers.alerts.mark_false_positive")]
    (alert,) = await _rows(w, "alerts")
    assert (alert["status"], alert["fp_reason"], alert["fp_marked_by_user_id"]) == (
        "false_positive", "Camera issue", w["users"][OPERATOR])
    closed = await _situation(w, s)
    assert (closed["decision_status"], closed["status"]) == ("FALSE_POSITIVE", "SETTLED") and closed["closed_at"]
    assert more.status_code == 409 and can.json()["closed"] is True
    assert {a["why_not"] for a in can.json()["actions"]} == {"This situation is closed. Nothing more can be decided on it."}

    # The same camera reports again: a new matter, not the one a person closed.
    await _alert(w, "intrusion", code="intrusion.zone_breach", title="Person at Gate 1", at=_ago(seconds=10))
    await _pass(w)
    both = await _rows(w, "security_situations", "started_at")
    assert len(both) == 2 and both[0]["event_count"] == 1 and both[1]["decision_status"] == "AWAITING"


@pytest.mark.asyncio
async def test_resolving_closes_the_alerts_and_the_incident_and_needs_the_permission_for_each():
    w, s = await _ready(severity="critical", on_shift=True)
    async with _client() as c:
        await _decide(c, w, s, OPERATOR, "DISPATCH_GUARD", guard_user_id=str(w["users"][GUARD]))
        # An operator may not resolve an incident anywhere on the platform, so not from here either.
        operator = await _decide(c, w, s, OPERATOR, "RESOLVE", reason_code="ALREADY_HANDLED")
        r = await _decide(c, w, s, SUPERVISOR, "RESOLVE", reason_code="AUTHORISED_ACTIVITY",
                          note="Night cleaner, badge checked by the guard.")
    assert operator.status_code == 403
    assert operator.json()["detail"] == "Carrying this out needs the permission incident:resolve."
    d = r.json()
    assert r.status_code == 201 and (d["basis"], d["reason"]) == ("CLOSING", "Authorised activity")
    assert [(a["action"], a["result"], a["through"]) for a in d["actions"]] == [
        ("ALERT_DISMISS", "OK", "app.routers.alerts.bulk_dismiss_alerts"),
        ("INCIDENT_RESOLVE", "OK", "app.routers.incidents.resolve_incident")]
    (alert,), (incident,) = await _rows(w, "alerts"), await _rows(w, "incidents")
    assert alert["status"] == "dismissed" and incident["status"] == "resolved" and incident["resolved_at"]
    assert (await _situation(w, s))["decision_status"] == "RESOLVED"
    trail = await _rows(w, "security_decisions", "decided_at")
    assert [(t["action"], t["actor_role"]) for t in trail] == [("DISPATCH_GUARD", OPERATOR), ("RESOLVE", SUPERVISOR)]
    async with _client() as c:
        ended = (await c.get(_url(s), headers=w["h"][VIEWER])).json()
    assert ended["decision_status"] == "RESOLVED" and ended["closed_at"] is not None
    # How it ended, beside how it was decided.
    assert ended["incident"] == {"state": "CONFIRMED", "id": str(incident["id"]), "status": "resolved"}


@pytest.mark.asyncio
async def test_an_incident_the_platform_opened_is_preliminary_until_a_person_confirms_it():
    w, s = await _ready(severity="critical")
    incident = uuid.uuid4()
    await _sql("INSERT INTO incidents (id, tenant_id, alert_id, camera_id, title, severity, is_auto_created) "
               "VALUES (:i,:t,:a,:c,'Person at Gate 1','critical',TRUE)",
               {"i": incident, "t": w["tenant"], "a": w["alert"], "c": w["cam_a"]})
    before = (await _rows(w, "incidents"))[0]
    async with _client() as c:
        seen = await c.get(f"{BASE}/situations/{s['id']}", headers=w["h"][VIEWER])
        another = await _decide(c, w, s, OPERATOR, "CREATE_INCIDENT", reason_code="OTHER", note="x")
        r = await _decide(c, w, s, OPERATOR, "CONFIRM_INCIDENT")
        again = await _decide(c, w, s, OPERATOR, "CONFIRM_INCIDENT")
        now = await c.get(f"{BASE}/situations/{s['id']}", headers=w["h"][VIEWER])
    assert seen.json()["incident"] == {"state": "PRELIMINARY", "id": str(incident), "opened_by_the_platform": True}
    assert another.status_code == 409 and "Confirm it instead" in another.json()["detail"]
    d = r.json()
    assert r.status_code == 201 and d["basis"] == "INDEPENDENT"
    assert [(a["action"], a["result"], a["through"], a["target_id"]) for a in d["actions"]] == [
        ("INCIDENT_CONFIRM", "RECORDED", None, str(incident))]
    assert again.status_code == 409 and "a person stands behind" in again.json()["detail"]
    assert now.json()["incident"] == {"state": "CONFIRMED", "id": str(incident), "opened_by_the_platform": True}
    assert (await _rows(w, "incidents"))[0] == before, "confirming is this layer's record; the incident is untouched"
    assert len(await _rows(w, "incidents")) == 1


@pytest.mark.asyncio
async def test_deciding_before_anything_was_suggested_is_neither_for_nor_against():
    w, s = await _ready(assessed=False)
    assert s["assessment_id"] is None
    async with _client() as c:
        r = await _decide(c, w, s, OPERATOR, "INVESTIGATE")
        looked = await c.post(_url(s, "reviews"), headers=w["h"][OPERATOR])
    d = r.json()
    assert r.status_code == 201 and (d["basis"], d["risk_level"], d["assessment_id"]) == ("INDEPENDENT", None, None)
    assert d["suggested_action"] is None and "not yet assessed" in d["policy"]["said"]
    assert looked.json() == {"recorded": False, "assessment_id": None}, "nothing had been suggested to look at"


@pytest.mark.asyncio
async def test_a_decision_made_on_an_earlier_assessment_says_so():
    w, s = await _ready()
    first = s["assessment_id"]
    await _alert(w, "access", code="access.denied", title="Access denied at Rear door", at=_ago(seconds=20))
    await _pass(w)
    latest = (await _situation(w, s))["assessment_id"]
    assert latest != first
    async with _client() as c:
        r = await _decide(c, w, s, OPERATOR, "ACKNOWLEDGE", seen_assessment_id=str(first))
    d = r.json()
    assert d["assessment_id"] == str(latest) and d["decided_on_an_earlier_assessment"] is True
    assert d["actions"][0]["detail"] == "2 alert(s) acknowledged."


@pytest.mark.asyncio
async def test_a_retried_request_records_one_decision_and_carries_it_out_once():
    w, s = await _ready()
    ref = str(uuid.uuid4())
    async with _client() as c:
        first = await _decide(c, w, s, OPERATOR, "ACKNOWLEDGE", client_ref=ref)
        retry = await _decide(c, w, s, OPERATOR, "ACKNOWLEDGE", client_ref=ref)
        someone_else = await _decide(c, w, s, SUPERVISOR, "ACKNOWLEDGE", client_ref=ref)
    assert (first.status_code, retry.status_code, someone_else.status_code) == (201, 200, 409)
    assert retry.json()["id"] == first.json()["id"] and retry.json()["replayed"] is True
    assert len(await _rows(w, "security_decisions", "decided_at")) == 1
    assert len(await _rows(w, "security_actions", "executed_at")) == 1


@pytest.mark.asyncio
async def test_a_step_that_fails_is_recorded_as_failed_and_the_decision_stands(monkeypatch):
    w, s = await _ready()

    async def broken(**_):
        raise RuntimeError("the database went away: SELECT secret FROM ...")

    async def gone(**_):
        raise HTTPException(404, "Alert not found or already closed")

    async with _client() as c:
        monkeypatch.setattr(intel_actions.alerts_api, "bulk_acknowledge_alerts", broken)
        failed = await _decide(c, w, s, OPERATOR, "ACKNOWLEDGE")
        monkeypatch.setattr(intel_actions.alerts_api, "bulk_dismiss_alerts", gone)
        skipped = await _decide(c, w, s, OPERATOR, "RESOLVE", reason_code="ALREADY_HANDLED")
    assert failed.status_code == 201, "the person's decision is on record whatever became of the step"
    (step,) = failed.json()["actions"]
    assert (step["action"], step["result"], step["detail"]) == ("ALERT_ACKNOWLEDGE", "FAILED", "RuntimeError"), \
        "the kind of error, never its text"
    assert [(a["result"], a["detail"]) for a in skipped.json()["actions"]] == [
        ("SKIPPED", "404: Alert not found or already closed")]
    assert (await _rows(w, "alerts"))[0]["status"] == "open"


# ─── C. Who may not decide; deciding with approval ───────────────────────────

@pytest.mark.asyncio
async def test_who_may_not_decide_and_that_a_refusal_records_nothing():
    w, s = await _ready(on_shift=True)          # the guard is on shift here: what stops them is the policy
    await _alert(w, "weapon", camera="cam_b", site="site_b", code="weapon.detected", severity="critical",
                 title="Weapon at Dock 4", at=_ago(seconds=50))
    await _pass(w)
    elsewhere = next(x for x in await _rows(w, "security_situations") if x["site_id"] == w["site_b"])
    other = await _world()
    async with _client() as c:
        viewer = await _decide(c, w, s, VIEWER, "ACKNOWLEDGE")
        guard = await _decide(c, w, s, GUARD, "ACKNOWLEDGE")
        guard_follows = await _decide(c, w, s, GUARD, "VIEW_CAMERA")
        other_site = await _decide(c, w, elsewhere, SUPERVISOR, "ACKNOWLEDGE")
        other_tenant = await _decide(c, other, s, ADMIN, "ACKNOWLEDGE")
        nonsense = await _decide(c, w, s, OPERATOR, "ARREST")
        extra = await c.post(_url(s), headers=w["h"][OPERATOR], json={"action": "MONITOR", "auto_execute": True})
        missing = await c.post(f"{BASE}/situations/{uuid.uuid4()}/decisions", headers=w["h"][ADMIN],
                               json={"action": "ACKNOWLEDGE"})
        nobody = await c.post(_url(s), json={"action": "ACKNOWLEDGE"})
        assert await _rows(w, "security_decisions", "decided_at") == []
        assert await _rows(w, "security_actions", "executed_at") == []
        # Asking the command centre for help is always open to a guard.
        help_ = await _decide(c, w, s, GUARD, "REQUEST_ASSISTANCE", note="Two people, I am alone.")
    assert viewer.status_code == 403 and viewer.json()["detail"] == "Missing permission: intel:decide"
    assert guard.status_code == 403 and guard.json()["detail"] == "The decision policy does not let a guard decide here."
    assert guard_follows.status_code == 403, "following a suggestion is still deciding"
    assert (other_site.status_code, other_tenant.status_code, missing.status_code) == (404, 404, 404)
    assert (nonsense.status_code, extra.status_code, nobody.status_code) == (422, 422, 401)
    assert help_.status_code == 201 and help_.json()["basis"] == "INDEPENDENT"
    assert [a["action"] for a in help_.json()["actions"]] == ["NONE"]
    assert (await _situation(w, s))["decision_status"] == "ASSISTANCE_REQUESTED"
    assert (await _rows(w, "alerts", "created_at"))[0]["status"] == "open"


@pytest.mark.asyncio
async def test_where_the_policy_lets_a_guard_decide_the_guard_decides_within_it():
    w, s = await _ready(policy=POLICY_B, on_shift=True)
    async with _client() as c:
        follows = await _decide(c, w, s, GUARD, "VIEW_CAMERA", via="mobile")
        # A guard has not been given the right to go against a suggestion.
        overrides = await _decide(c, w, s, GUARD, "INVESTIGATE", reason_code="OTHER", note="x")
        acknowledges = await _decide(c, w, s, GUARD, "ACKNOWLEDGE", via="mobile")
    assert follows.status_code == 201 and follows.json()["authority"] == "ALONE" and follows.json()["via"] == "mobile"
    assert follows.json()["policy"] == {"source": "tenant", "rule": {"alone": "MEDIUM"},
                                        "said": "Guard may decide alone up to MEDIUM; this is MEDIUM."}
    assert overrides.status_code == 403 and "intel:override" in overrides.json()["detail"]
    assert acknowledges.status_code == 201 and acknowledges.json()["actions"][0]["result"] == "OK"
    high_w, high = await _ready(severity="critical", policy=POLICY_B, on_shift=True)
    async with _client() as c:
        too_high = await _decide(c, high_w, high, GUARD, "VIEW_CAMERA")
    assert too_high.status_code == 403
    assert too_high.json()["detail"] == "Guard may decide up to MEDIUM under the decision policy; this is HIGH."


@pytest.mark.asyncio
async def test_a_decision_that_needs_approval_carries_nothing_out_until_a_second_person_approves():
    w, s = await _ready(severity="critical", policy=POLICY_C, on_shift=True)
    async with _client() as c:
        r = await _decide(c, w, s, GUARD, "CREATE_INCIDENT", via="mobile")
        d = r.json()
        assert r.status_code == 201, r.text
        assert (d["authority"], d["state"], d["basis"], d["actions"]) == ("WITH_APPROVAL", "PENDING_APPROVAL",
                                                                          "FOLLOWED", [])
        assert await _rows(w, "incidents") == [] and await _rows(w, "security_actions", "executed_at") == []
        assert (await _situation(w, s))["decision_status"] == "PENDING_APPROVAL"
        queue = await c.get(f"{BASE}/decisions", params={"state": "pending_approval"}, headers=w["h"][SUPERVISOR])
        own = await c.post(f"{BASE}/decisions/{d['id']}/approve", headers=w["h"][GUARD])
        operator = await c.post(f"{BASE}/decisions/{d['id']}/approve", headers=w["h"][OPERATOR])
        approved = await c.post(f"{BASE}/decisions/{d['id']}/approve", headers=w["h"][SUPERVISOR],
                                json={"note": "Go ahead."})
        twice = await c.post(f"{BASE}/decisions/{d['id']}/reject", headers=w["h"][ADMIN], json={"note": "No."})
        empty = await c.get(f"{BASE}/decisions", params={"state": "pending_approval"}, headers=w["h"][SUPERVISOR])
    assert [x["id"] for x in queue.json()["items"]] == [d["id"]] and queue.json()["total"] == 1
    assert own.status_code == 403 and operator.status_code == 403, "neither holds intel:approve"
    a = approved.json()
    assert approved.status_code == 200 and a["state"] == "APPROVED"
    assert a["approval"]["verdict"] == "APPROVED" and a["approval"]["note"] == "Go ahead."
    assert a["approval"]["by"] == {"user_id": str(w["users"][SUPERVISOR]), "name": "Role 3 User", "role_id": SUPERVISOR}
    assert a["decided_by"]["role_id"] == GUARD, "it is still the guard's decision; the approval is a second record"
    (step,) = a["actions"]
    assert (step["action"], step["result"]) == ("INCIDENT_CREATE", "OK")
    assert step["executed_by_user_id"] == str(w["users"][SUPERVISOR]), "carried out under the approver's authority"
    assert len(await _rows(w, "incidents")) == 1
    assert twice.status_code == 409 and empty.json()["items"] == []
    assert (await _situation(w, s))["decision_status"] == "IN_HAND"


@pytest.mark.asyncio
async def test_a_rejected_decision_carries_nothing_out_and_says_why():
    w, s = await _ready(severity="critical", policy=POLICY_C, on_shift=True)
    async with _client() as c:
        d = (await _decide(c, w, s, GUARD, "FALSE_POSITIVE", reason_code="FALSE_DETECTION")).json()
        assert d["state"] == "PENDING_APPROVAL" and (await _situation(w, s))["closed_at"] is None
        silent = await c.post(f"{BASE}/decisions/{d['id']}/reject", headers=w["h"][SUPERVISOR])
        rejected = await c.post(f"{BASE}/decisions/{d['id']}/reject", headers=w["h"][SUPERVISOR],
                                json={"note": "There is a person on camera 2."})
        late = await c.post(f"{BASE}/decisions/{d['id']}/approve", headers=w["h"][ADMIN])
        hidden = await c.post(f"{BASE}/decisions/{d['id']}/approve", headers=(await _world())["h"][ADMIN])
    assert silent.status_code == 422 and "needs a note" in silent.json()["detail"]
    r = rejected.json()
    assert rejected.status_code == 200 and (r["state"], r["actions"]) == ("REJECTED", [])
    assert r["approval"]["note"] == "There is a person on camera 2."
    assert (late.status_code, hidden.status_code) == (409, 404)
    after = await _situation(w, s)
    assert (after["decision_status"], after["closed_at"]) == ("AWAITING", None), "it wants a decision again"
    assert (await _rows(w, "alerts"))[0]["status"] == "open"


@pytest.mark.asyncio
async def test_approval_is_by_a_second_person_who_could_have_decided_it_alone():
    held = {"3": {"alone": "LOW", "with_approval": "CRITICAL"}, "8": {"alone": "LOW"}}
    w, s = await _ready(severity="critical", policy=held)
    async with _client() as c:
        d = (await _decide(c, w, s, SUPERVISOR, "CREATE_INCIDENT")).json()
        assert d["state"] == "PENDING_APPROVAL"
        myself = await c.post(f"{BASE}/decisions/{d['id']}/approve", headers=w["h"][SUPERVISOR])
        no_authority = await c.post(f"{BASE}/decisions/{d['id']}/approve", headers=w["h"][MANAGER])
        # Someone with the authority decides it themselves: the waiting one is overtaken.
        direct = await _decide(c, w, s, ADMIN, "VIEW_CAMERA")
        overtaken = await c.post(f"{BASE}/decisions/{d['id']}/approve", headers=w["h"][ADMIN])
    assert myself.status_code == 403
    assert myself.json()["detail"] == "A decision is approved by a second person, not by the one who made it."
    assert no_authority.status_code == 403 and "authority to take this decision alone" in no_authority.json()["detail"]
    assert direct.status_code == 201 and overtaken.status_code == 409
    assert "A later decision has been made" in overtaken.json()["detail"]
    assert await _rows(w, "incidents") == []


# ─── D. The trail, the audit log, the policy ─────────────────────────────────

@pytest.mark.asyncio
async def test_looking_is_recorded_once_per_person_per_assessment_and_decides_nothing():
    w, s = await _ready()
    async with _client() as c:
        first = await c.post(_url(s, "reviews"), headers=w["h"][OPERATOR])
        again = await c.post(_url(s, "reviews"), headers=w["h"][OPERATOR], json={"via": "web"})
        second_person = await c.post(_url(s, "reviews"), headers=w["h"][SUPERVISOR], json={"via": "mobile"})
        viewer = await c.post(_url(s, "reviews"), headers=w["h"][VIEWER])
        trail = await c.get(_url(s), headers=w["h"][VIEWER])
    assert first.json() == {"recorded": True, "assessment_id": str(s["assessment_id"])}
    assert again.json()["recorded"] is False and second_person.json()["recorded"] is True
    assert viewer.status_code == 403, "a viewer cannot read the suggestions, so has not looked at them"
    t = trail.json()
    assert [(r["role_id"], r["via"]) for r in t["reviews"]] == [(OPERATOR, "web"), (SUPERVISOR, "mobile")]
    assert t["decisions"] == [] and t["decision_status"] == "AWAITING", "looking decides nothing"
    views = await _sql("SELECT count(*) AS n FROM audit_logs WHERE tenant_id = :t AND action = 'intel.recommendation.view'",
                       {"t": w["tenant"]})
    assert views[0]["n"] == 2


@pytest.mark.asyncio
async def test_the_trail_keeps_what_was_suggested_what_was_decided_and_what_was_done_apart():
    w, s = await _ready(severity="critical", on_shift=True)
    async with _client() as c:
        await _decide(c, w, s, OPERATOR, "ACKNOWLEDGE")
        sent = (await _decide(c, w, s, OPERATOR, "DISPATCH_GUARD", guard_user_id=str(w["users"][GUARD]))).json()
        trail = await c.get(_url(s), headers=w["h"][VIEWER])
        one = await c.get(f"{BASE}/decisions/{sent['id']}", headers=w["h"][VIEWER])
        listed = await c.get(f"{BASE}/decisions", headers=w["h"][ADMIN])
        by_action = await c.get(f"{BASE}/decisions", params={"action": "DISPATCH_GUARD", "basis": "FOLLOWED"},
                                headers=w["h"][ADMIN])
        bad = [await c.get(f"{BASE}/decisions", params=p, headers=w["h"][ADMIN])
               for p in ({"action": "ARREST"}, {"basis": "WHIM"}, {"state": "done"})]
        stranger = await c.get(f"{BASE}/decisions/{sent['id']}", headers=(await _world())["h"][ADMIN])
        recs = await c.get(_url(s, "recommendations"), headers=w["h"][OPERATOR])
    t = trail.json()["decisions"]
    assert [d["action"] for d in t] == ["ACKNOWLEDGE", "DISPATCH_GUARD"], "oldest first"
    d = t[1]
    for part in ("suggested_action", "recommendation_id", "action", "basis", "decided_by", "actions", "approval"):
        assert part in d
    assert d["suggested_action"] == "VIEW_CAMERA" and d["action"] == "DISPATCH_GUARD", "suggested first; decided another"
    assert [a["result"] for a in d["actions"]] == ["OK", "OK"]
    assert one.status_code == 200 and one.json() == d and "site_id" not in one.json()
    assert [x["action"] for x in listed.json()["items"]] == ["DISPATCH_GUARD", "ACKNOWLEDGE"], "most recent first"
    assert [x["id"] for x in by_action.json()["items"]] == [sent["id"]]
    assert [r.status_code for r in bad] == [422, 422, 422] and stranger.status_code == 404
    # The suggestions are as they were written: deciding does not rewrite them.
    assert recs.json()["is_decision"] is False and all("decided" not in json.dumps(r) for r in recs.json()["recommendations"])


@pytest.mark.asyncio
async def test_every_decision_and_every_step_is_in_the_audit_log_with_who_where_and_how_it_ended():
    w, s = await _ready(severity="critical", on_shift=True)
    async with _client() as c:
        r = await _decide(c, w, s, OPERATOR, "DISPATCH_GUARD", guard_user_id=str(w["users"][GUARD]))
    rows = await _sql("SELECT action, user_id, resource_type, resource_id, detail, row_hash FROM audit_logs "
                      " WHERE tenant_id = :t AND action LIKE 'intel.%' ORDER BY created_at, id", {"t": w["tenant"]})
    assert sorted(x["action"] for x in rows) == ["intel.action.incident_create", "intel.action.incident_dispatch",
                                                 "intel.decision.record"]
    by = {x["action"]: (x["detail"] if isinstance(x["detail"], dict) else json.loads(x["detail"])) for x in rows}
    assert all(x["user_id"] == w["users"][OPERATOR] and x["row_hash"] and str(x["resource_id"]) == str(s["id"])
               for x in rows)
    decision = by["intel.decision.record"]
    assert decision["actor_role"] == OPERATOR and decision["site_id"] == str(w["site_a"])
    assert decision["source"] == "user" and decision["result"] == "ok"
    assert decision["request_id"] == r.headers["X-Request-Id"]
    assert (decision["decision"], decision["basis"], decision["override"]) == ("DISPATCH_GUARD", "FOLLOWED", False)
    assert (decision["suggested_action"], decision["risk_level"], decision["authority"]) == ("VIEW_CAMERA", "HIGH", "ALONE")
    assert decision["assessment_id"] == str(s["assessment_id"]) and decision["recommendation_id"]
    step = by["intel.action.incident_dispatch"]
    assert step["result"] == "ok" and step["through"] == "app.routers.dispatch.dispatch_guard"
    assert step["decision_id"] == r.json()["id"] and step["target_type"] == "incident"


@pytest.mark.asyncio
async def test_what_may_i_do_here_is_answered_by_the_same_judgement_as_deciding():
    w, s = await _ready(severity="critical", policy=POLICY_C, on_shift=True)
    async with _client() as c:
        operator = (await c.get(_url(s, "authority"), headers=w["h"][OPERATOR])).json()
        guard = (await c.get(_url(s, "authority"), headers=w["h"][GUARD])).json()
        viewer = (await c.get(_url(s, "authority"), headers=w["h"][VIEWER])).json()
        hidden = await c.get(_url(s, "authority"), headers=(await _world())["h"][ADMIN])

        def of(answer, action):
            return next(a for a in answer["actions"] if a["action"] == action)

        assert hidden.status_code == 404
        assert [a["action"] for a in operator["actions"]] == list(dec.DECISIONS)
        assert (operator["risk_level"], operator["suggested_action"]) == ("HIGH", "VIEW_CAMERA")
        assert operator["incident"] == {"state": "NONE", "id": None} and operator["may_override"] is True
        assert operator["may_approve"] is False and len(operator["reasons"]) == 8
        assert of(operator, "VIEW_CAMERA") == {
            "action": "VIEW_CAMERA", "allowed": True, "how": "ALONE", "basis": "FOLLOWED", "needs_reason": False,
            "needs": [], "why_not": None, "carries_out": []}
        assert of(operator, "DISPATCH_GUARD")["needs"] == ["guard_user_id"]
        assert of(operator, "DISPATCH_GUARD")["carries_out"] == ["INCIDENT_CREATE", "INCIDENT_DISPATCH"]
        assert (of(operator, "MONITOR")["basis"], of(operator, "MONITOR")["needs_reason"]) == ("OVERRIDE", True)
        assert of(operator, "CONFIRM_INCIDENT")["why_not"] == "There is no open incident to confirm."
        assert of(guard, "CREATE_INCIDENT")["how"] == "WITH_APPROVAL" and guard["policy"]["source"] == "tenant"
        assert of(guard, "MONITOR")["allowed"] is False and "intel:override" in of(guard, "MONITOR")["why_not"]
        assert {a["why_not"] for a in viewer["actions"]} == {"Deciding needs the permission intel:decide."}
        # Whatever it says may not be done is refused when it is tried…
        refusals = 0
        for role, answer in ((OPERATOR, operator), (GUARD, guard), (VIEWER, viewer)):
            for a in answer["actions"]:
                if a["allowed"]:
                    continue
                body = {"reason_code": "OTHER", "note": "x"} if a["needs_reason"] else {}
                tried = await _decide(c, w, s, role, a["action"], **body)
                assert tried.status_code in (403, 409), (role, a["action"], tried.text)
                refusals += 1
        assert refusals > 15 and await _rows(w, "security_decisions", "decided_at") == []
        # …and what it says may be done is accepted.
        assert of(guard, "REQUEST_ASSISTANCE")["how"] == "ALONE"
        assert (await _decide(c, w, s, GUARD, "REQUEST_ASSISTANCE")).status_code == 201
        assert of(operator, "VIEW_CAMERA")["allowed"]
        assert (await _decide(c, w, s, OPERATOR, "VIEW_CAMERA")).status_code == 201


@pytest.mark.asyncio
async def test_the_people_a_decision_can_name_are_listed_for_someone_who_may_decide():
    w, s = await _ready(severity="critical", on_shift=True)
    await _run([
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
         "VALUES (:i,:t,5,:e,'x','Aaron Spare')", {"i": uuid.uuid4(), "t": w["tenant"], "e": "spare@decide.test"}),
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name, is_active) "
         "VALUES (:i,:t,5,:e,'x','Left Last Year',FALSE)", {"i": uuid.uuid4(), "t": w["tenant"], "e": "left@decide.test"}),
    ])
    async with _client() as c:
        r = await c.get(_url(s, "responders"), headers=w["h"][OPERATOR])
        as_supervisor = await c.get(_url(s, "responders"), headers=w["h"][SUPERVISOR])
        viewer = await c.get(_url(s, "responders"), headers=w["h"][VIEWER])
        stranger = await c.get(_url(s, "responders"), headers=(await _world())["h"][ADMIN])
    body = r.json()
    assert r.status_code == 200
    assert [(g["name"], g["on_shift_here"], g["on_shift"]) for g in body["guards"]] == [
        ("Role 5 User", True, True), ("Aaron Spare", False, False)], "on shift here first; nobody who has left"
    assert sorted(e["role_id"] for e in body["escalation"]) == [ADMIN, SUPERVISOR, MANAGER]
    # Nobody escalates to themselves.
    assert str(w["users"][SUPERVISOR]) not in [e["user_id"] for e in as_supervisor.json()["escalation"]]
    assert set(body["guards"][0]) == {"user_id", "name", "on_shift_here", "on_shift"}, "a name to choose by, no more"
    assert viewer.status_code == 403 and stranger.status_code == 404


@pytest.mark.asyncio
async def test_the_decision_policy_is_set_by_an_administrator_and_a_site_can_have_its_own():
    w = await _world()
    url = f"{BASE}/decision-policy"
    async with _client() as c:
        default = (await c.get(url, headers=w["h"][VIEWER])).json()
        refused = await c.put(url, headers=w["h"][SUPERVISOR], json={"roles": POLICY_B})
        bad = [await c.put(url, headers=w["h"][ADMIN], json=b) for b in (
            {"roles": {"6": {"alone": "LOW"}}}, {"roles": {"5": {"alone": "SEVERE"}}}, {"roles": POLICY_B, "mode": "B"})]
        tenant = await c.put(url, headers=w["h"][ADMIN], json={"roles": POLICY_B, "note": "Guards handle the small things."})
        site = await c.put(f"{url}/sites/{w['site_b']}", headers=w["h"][MANAGER], json={"roles": POLICY_A})
        no_site = await c.put(f"{url}/sites/{uuid.uuid4()}", headers=w["h"][ADMIN], json={"roles": POLICY_A})
        restricted = (await c.get(url, headers=w["h"][SUPERVISOR])).json()
        stranger = (await c.get(url, headers=(await _world())["h"][ADMIN])).json()
    assert default["tenant"] is None and default["sites"] == [] and default["default"] == dec.DEFAULT_POLICY
    assert default["levels"] == list(risk.LEVELS) and default["always_allowed"] == ["REQUEST_ASSISTANCE"]
    assert refused.status_code == 403 and [r.status_code for r in bad] == [422, 422, 422]
    assert "is not a role that can decide" in bad[0].json()["detail"]
    t = tenant.json()["tenant"]
    assert tenant.status_code == 200 and t["roles"] == POLICY_B and t["note"] == "Guards handle the small things."
    assert t["effective"]["5"] == {"alone": "MEDIUM"} and t["effective"]["4"] == {"alone": "CRITICAL"}
    assert [x["site_id"] for x in site.json()["sites"]] == [str(w["site_b"])] and no_site.status_code == 404
    assert restricted["sites"] == [], "a supervisor of site A is not shown site B's policy"
    assert stranger["tenant"] is None and stranger["sites"] == []

    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        at_a, at_b, nowhere = (await dec.policy_for(db, w["site_a"]), await dec.policy_for(db, w["site_b"]),
                               await dec.policy_for(db, None))
        await db.rollback()
    assert (at_a["source"], at_a["roles"]["5"]) == ("tenant", {"alone": "MEDIUM"})
    assert (at_b["source"], at_b["roles"]["5"]) == ("site", {}), "the site's own policy stands in place of the tenant's"
    assert nowhere["source"] == "tenant"

    async with _client() as c:
        gone = await c.delete(f"{url}/sites/{w['site_b']}", headers=w["h"][ADMIN])
        twice = await c.delete(f"{url}/sites/{w['site_b']}", headers=w["h"][ADMIN])
    assert gone.status_code == 200 and gone.json()["sites"] == [] and twice.status_code == 404
    audited = await _sql("SELECT action FROM audit_logs WHERE tenant_id = :t AND action LIKE 'intel.decision_policy.%' "
                         " ORDER BY created_at, id", {"t": w["tenant"]})
    assert [x["action"] for x in audited] == ["intel.decision_policy.update", "intel.decision_policy.update",
                                              "intel.decision_policy.delete"]


@pytest.mark.asyncio
async def test_situations_can_be_listed_by_where_they_stand_and_closed_ones_left_out():
    w, s = await _ready()
    await _alert(w, "ppe", camera="cam_b", site="site_b", code="ppe.missing", severity="low", title="No hard hat",
                 at=_ago(seconds=40))
    await _pass(w)
    async with _client() as c:
        await _decide(c, w, s, OPERATOR, "FALSE_POSITIVE", reason_code="FALSE_DETECTION")
        everything = await c.get(f"{BASE}/situations", headers=w["h"][ADMIN])
        still_open = await c.get(f"{BASE}/situations", params={"open": "true"}, headers=w["h"][ADMIN])
        awaiting = await c.get(f"{BASE}/situations", params={"decision_status": "AWAITING"}, headers=w["h"][ADMIN])
        bad = await c.get(f"{BASE}/situations", params={"decision_status": "DONE"}, headers=w["h"][ADMIN])
    assert {x["title"]: x["decision_status"] for x in everything.json()["items"]} == {
        "Person at Gate 1": "FALSE_POSITIVE", "No hard hat": "AWAITING"}
    assert [x["title"] for x in still_open.json()["items"]] == ["No hard hat"] == \
           [x["title"] for x in awaiting.json()["items"]]
    assert bad.status_code == 422


def test_what_goes_on_the_wire_about_a_decision_names_nobody():
    decision = {"id": uuid.uuid4(), "situation_id": uuid.uuid4(), "situation_number": "SIT-20261005-0001",
                "action": "DISPATCH_GUARD", "basis": "FOLLOWED", "authority": "ALONE", "state": "EFFECTIVE",
                "note": "Tan Wei Ming is nearest", "decided_by": {"user_id": uuid.uuid4(), "name": "Sathish", "role_id": 4},
                "actions": [{"action": "INCIDENT_DISPATCH", "result": "OK", "detail": "x", "target_id": uuid.uuid4()}]}
    said = api._said(decision)
    assert set(said) == {"situation_id", "situation_number", "decision_id", "action", "basis", "authority", "state",
                         "decided_by_role", "actions"}
    assert "Sathish" not in json.dumps(said, default=str) and "Tan Wei Ming" not in json.dumps(said, default=str)
    assert said["actions"] == [{"action": "INCIDENT_DISPATCH", "result": "OK"}]


# ─── E. The schema ───────────────────────────────────────────────────────────

RECORDS = ("security_reviews", "security_decisions", "security_decision_approvals", "security_actions")


@pytest.mark.asyncio
async def test_the_records_are_tenant_isolated_and_the_application_may_only_add_and_read():
    rows = await _sql(
        "SELECT c.relname, c.relrowsecurity AND c.relforcerowsecurity AS forced, "
        "       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies, "
        "       has_table_privilege('svc_app', c.oid, 'INSERT') AS can_add, "
        "       has_table_privilege('svc_app', c.oid, 'UPDATE') AS can_change, "
        "       has_table_privilege('svc_app', c.oid, 'DELETE') AS can_remove "
        "  FROM pg_class c WHERE c.relname = ANY(CAST(:names AS text[]))",
        {"names": [*RECORDS, "security_decision_policies"]})
    by = {r["relname"]: r for r in rows}
    assert set(by) == {*RECORDS, "security_decision_policies"}
    for name, r in by.items():
        assert r["forced"] and r["policies"] == 1 and r["can_add"], name
        assert (r["can_change"], r["can_remove"]) == ((True, True) if name == "security_decision_policies"
                                                       else (False, False)), name


@pytest.mark.asyncio
async def test_a_decision_once_recorded_cannot_be_changed_and_another_tenants_cannot_be_seen():
    w, s = await _ready()
    other, other_s = await _ready()
    async with _client() as c:
        await _decide(c, w, s, OPERATOR, "ACKNOWLEDGE")
        await _decide(c, other, other_s, OPERATOR, "ACKNOWLEDGE")
    async with AsyncSessionLocal() as db:
        who = (await db.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))).first()
        for table in ("security_decisions", "security_actions"):
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            mine = (await db.execute(text(f"SELECT tenant_id FROM {table}"))).scalars().all()
            assert set(mine) == {w["tenant"]}, table
            for stmt in (f"UPDATE {table} SET tenant_id = tenant_id", f"DELETE FROM {table}"):
                with pytest.raises(Exception, match="permission denied"):
                    await db.execute(text(stmt))
                await db.rollback()
                await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        await db.rollback()
    assert who.rolsuper is False and who.rolbypassrls is False
    assert len(await _rows(w, "security_decisions", "decided_at")) == 1


@pytest.mark.asyncio
async def test_the_database_itself_refuses_a_decision_that_does_not_say_what_it_must():
    w, s = await _ready()
    (suggestion,) = [r for r in await _rows(w, "security_recommendations", "rank") if r["rank"] == 1]
    insert = ("INSERT INTO security_decisions (id, tenant_id, situation_id, recommendation_id, action, basis, "
              "    reason_code, note, actor_role, authority) "
              "VALUES (:i,:t,:s,:rec,:action,:basis,:reason,:note,4,:how)")
    ok = {"i": uuid.uuid4(), "t": w["tenant"], "s": s["id"], "rec": None, "action": "MONITOR", "basis": "OVERRIDE",
          "reason": "ALREADY_HANDLED", "note": None, "how": "ALONE"}
    for bad in ({"reason": None}, {"reason": "OTHER"}, {"reason": "OTHER", "note": "  "}, {"reason": "BORED"},
                {"action": "ARREST"}, {"basis": "WHIM"}, {"how": "BY_ITSELF"}, {"basis": "FOLLOWED", "reason": None},
                {"basis": "CLOSING"}, {"action": "RESOLVE"}, {"action": "RESOLVE", "basis": "CLOSING", "reason": None}):
        with pytest.raises(Exception):
            await _sql(insert, {**ok, **bad, "i": uuid.uuid4()})
    await _sql(insert, ok)
    await _sql(insert, {**ok, "i": uuid.uuid4(), "basis": "FOLLOWED", "reason": None, "rec": suggestion["id"],
                        "action": "VIEW_CAMERA"})
    held = uuid.uuid4()
    await _sql(insert, {**ok, "i": held, "action": "RESOLVE", "basis": "CLOSING", "how": "WITH_APPROVAL"})

    verdict = ("INSERT INTO security_decision_approvals (tenant_id, decision_id, verdict, note, approver_role) "
               "VALUES (:t,:d,:v,:n,3)")
    for bad in ({"v": "REJECTED", "n": None}, {"v": "REJECTED", "n": " "}, {"v": "MAYBE", "n": "x"}):
        with pytest.raises(Exception):
            await _sql(verdict, {"t": w["tenant"], "d": held, **bad})
    await _sql(verdict, {"t": w["tenant"], "d": held, "v": "APPROVED", "n": None})
    with pytest.raises(Exception):
        await _sql(verdict, {"t": w["tenant"], "d": held, "v": "REJECTED", "n": "A second verdict."})

    step = ("INSERT INTO security_actions (tenant_id, decision_id, situation_id, sequence, action, through, result) "
            "VALUES (:t,:d,:s,:seq,:action,:through,:result)")
    base = {"t": w["tenant"], "d": held, "s": s["id"], "seq": 1, "action": "ALERT_DISMISS",
            "through": "app.routers.alerts.bulk_dismiss_alerts", "result": "OK"}
    for bad in ({"action": "DOOR_UNLOCK"}, {"result": "MAYBE"}, {"through": None}, {"seq": 0},
                {"action": "NONE", "result": "RECORDED"}):       # recorded, yet naming a function it went through
        with pytest.raises(Exception):
            await _sql(step, {**base, **bad})
    await _sql(step, base)
    with pytest.raises(Exception):
        await _sql(step, base)                                    # the same step twice

    for stmt in ("UPDATE security_situations SET decision_status = 'RESOLVED' WHERE id = :s",
                 "UPDATE security_situations SET closed_at = now() WHERE id = :s",
                 "UPDATE security_situations SET decision_status = 'ABANDONED' WHERE id = :s"):
        with pytest.raises(Exception):
            await _sql(stmt, {"s": s["id"]})
    policy = "INSERT INTO security_decision_policies (tenant_id, site_id, roles) VALUES (:t, :s, CAST(:r AS jsonb))"
    await _sql(policy, {"t": w["tenant"], "s": None, "r": "{}"})
    await _sql(policy, {"t": w["tenant"], "s": w["site_a"], "r": "{}"})
    for again in ({"s": None, "r": "{}"}, {"s": w["site_a"], "r": "{}"}, {"s": w["site_b"], "r": "[]"}):
        with pytest.raises(Exception):      # one for the tenant, one for a site, and always an object
            await _sql(policy, {"t": w["tenant"], **again})
