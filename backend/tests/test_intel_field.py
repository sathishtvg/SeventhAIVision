"""AI security intelligence, phase 9: the person on the ground.

  A — A guard's reach: their own shift's site, or what they were dispatched to
  B — What a guard is shown, and what everyone else is
  C — Reporting from the ground: accepted, arrived, what I see — and that it
      changes nothing else
  D — The schema

The claims this phase makes, each with tests: a guard decides and reports only
where they are on shift or were sent; where the command centre controls
incidents a guard's phone shows what the command centre sent them and nothing
more; and an observation is a statement, not a decision — it touches no alert,
no incident and no dispatch.
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
from app.services import intel_decisions as dec
from app.services import intel_field as field
from tests.test_drone_api import ADMIN, GUARD, OPERATOR, SUPERVISOR, VIEWER, _client, _sql
from tests.test_intel_decisions import (
    BASE, EVERYTHING, POLICY_A, POLICY_B, _decide, _facts, _on_shift, _pass, _ready, _rows, _situation, _url,
)
from tests.test_intel_events import _alert, _world

NOW = datetime.now(timezone.utc)


def _ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


async def _report(c, w: dict, s: dict, role: int, kind: str, **body):
    return await c.post(_url(s, "observations"), headers=w["h"][role], json={"kind": kind, **body})


async def _dispatch(c, w: dict, s: dict, who: int = GUARD, note: str | None = None):
    """The command centre sends a guard, through a decision."""
    r = await _decide(c, w, s, OPERATOR, "DISPATCH_GUARD", guard_user_id=str(w["users"][who]),
                      reason_code="EMERGENCY", note=note)
    assert r.status_code == 201, r.text
    return r.json()


# ─── A. A guard's reach ──────────────────────────────────────────────────────

def test_out_of_reach_is_refused_before_the_policy_is_even_asked():
    open_ = {"id": uuid.uuid4(), "closed_at": None, "incident_confirmed_at": None}
    for action in ("VIEW_CAMERA", "ACKNOWLEDGE", "REQUEST_ASSISTANCE", "RESOLVE"):
        c = dec.check(action, situation=open_, f=_facts("LOW"), mine=set(EVERYTHING), roles=POLICY_B, role_id=GUARD,
                      in_reach=False)
        assert c.refusal == (403, dec.OUT_OF_REACH), action
    inside = dec.check("VIEW_CAMERA", situation=open_, f=_facts("LOW"), mine=set(EVERYTHING), roles=POLICY_B,
                       role_id=GUARD, in_reach=True)
    assert inside.refusal is None
    # Lacking the permission is still said first: reach is not a way to learn more.
    none = dec.check("VIEW_CAMERA", situation=open_, f=_facts("LOW"), mine=set(), roles=POLICY_B, role_id=GUARD,
                     in_reach=False)
    assert none.refusal == (403, "Deciding needs the permission intel:decide.")


@pytest.mark.asyncio
async def test_a_guard_decides_and_reports_only_at_their_own_shifts_site():
    w, s = await _ready(policy=POLICY_B)                       # the policy would let a guard decide here…
    async with _client() as c:
        reads = await c.get(f"{BASE}/situations/{s['id']}", headers=w["h"][GUARD])
        nowhere = await _decide(c, w, s, GUARD, "VIEW_CAMERA")     # …but this guard is on no shift at all
        asks = await _decide(c, w, s, GUARD, "REQUEST_ASSISTANCE")
        reports = await _report(c, w, s, GUARD, "OBSERVATION", note="Gate is shut.")
        can = (await c.get(_url(s, "authority"), headers=w["h"][GUARD])).json()
        await _on_shift(w, GUARD, "site_b")                        # on shift, at the other site
        elsewhere = await _decide(c, w, s, GUARD, "VIEW_CAMERA")
        await _on_shift(w, GUARD, "site_a")
        here = await _decide(c, w, s, GUARD, "VIEW_CAMERA")
        operator = await _report(c, w, s, OPERATOR, "OBSERVATION", note="Watching camera 2.")
    assert reads.status_code == 200, "reading is by site, as for everyone"
    for refused in (nowhere, asks, reports, elsewhere):
        assert refused.status_code == 403 and refused.json()["detail"] == dec.OUT_OF_REACH
    assert can["in_reach"] is False and {a["why_not"] for a in can["actions"]} == {dec.OUT_OF_REACH}
    assert here.status_code == 201, here.text
    assert operator.status_code == 201, "reach is a guard's limit; an operator's sites were already checked"
    assert len(await _rows(w, "security_decisions", "decided_at")) == 1


@pytest.mark.asyncio
async def test_a_guard_who_was_dispatched_is_within_reach_wherever_their_shift_is():
    w, s = await _ready(severity="critical")                   # default policy; the guard is on no shift
    async with _client() as c:
        before = await _report(c, w, s, GUARD, "ACCEPTED")
        await _dispatch(c, w, s, note="Rear gate, take the torch.")
        accepted = await _report(c, w, s, GUARD, "ACCEPTED", via="mobile")
        decides = await _decide(c, w, s, GUARD, "VIEW_CAMERA")
        asks = await _decide(c, w, s, GUARD, "REQUEST_ASSISTANCE", note="Two people here.")
        can = (await c.get(_url(s, "authority"), headers=w["h"][GUARD])).json()
    assert before.status_code == 403
    assert accepted.status_code == 201 and accepted.json()["kind"] == "ACCEPTED"
    # Within reach is not authority: the policy still says a guard does not decide here.
    assert decides.status_code == 403
    assert decides.json()["detail"] == "The decision policy does not let a guard decide here."
    assert asks.status_code == 201 and can["in_reach"] is True


# ─── B. What each person is shown ────────────────────────────────────────────

async def _mine(c, w: dict, role: int) -> list[dict]:
    r = await c.get(f"{BASE}/my-situations", headers=w["h"][role])
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_where_the_command_centre_decides_a_guards_phone_shows_what_they_were_sent():
    w, s = await _ready(severity="critical", on_shift=True, policy=POLICY_A)
    async with _client() as c:
        nothing = await _mine(c, w, GUARD)
        await _dispatch(c, w, s, note="Rear gate, take the torch.")
        sent = await _mine(c, w, GUARD)
        await _report(c, w, s, GUARD, "ACCEPTED")
        accepted = await _mine(c, w, GUARD)
        await _report(c, w, s, GUARD, "OBSERVATION", note="Nobody at the gate.")
        await _report(c, w, s, GUARD, "ARRIVED", latitude=1.3001, longitude=103.8001)
        arrived = await _mine(c, w, GUARD)
    assert nothing == [], "on shift here, but the policy leaves incidents to the command centre"
    (row,) = sent
    assert row["id"] == str(s["id"]) and row["assigned_to_me"] is True
    assert row["dispatch_notes"] == "Rear gate, take the torch." and row["dispatched_at"] and row["my_last"] is None
    assert (row["title"], row["site_name"], row["primary_camera_name"]) == ("Person at Gate 1", "Factory A", "Gate 1")
    assert (row["risk_level"], row["kind"], row["label"]) == ("HIGH", "ACTIVITY", "Activity that requires review")
    assert accepted[0]["my_last"] == "ACCEPTED"
    assert arrived[0]["my_last"] == "ARRIVED", "the last of accepted and arrived; what was seen is not a stage"


@pytest.mark.asyncio
async def test_where_a_guard_may_decide_they_are_shown_their_sites_open_situations():
    w, s = await _ready(policy=POLICY_B, on_shift=True)
    await _alert(w, "weapon", camera="cam_b", site="site_b", code="weapon.detected", severity="critical",
                 title="Weapon at Dock 4", at=_ago(seconds=50))
    await _pass(w)
    other = await _world()
    async with _client() as c:
        guard = await _mine(c, w, GUARD)
        operator = await _mine(c, w, OPERATOR)
        supervisor = await _mine(c, w, SUPERVISOR)
        viewer = await _mine(c, w, VIEWER)
        stranger = await _mine(c, other, ADMIN)
        nobody = await c.get(f"{BASE}/my-situations")
        await _decide(c, w, s, OPERATOR, "FALSE_POSITIVE", reason_code="FALSE_DETECTION")
        after = await _mine(c, w, GUARD)
    assert [x["title"] for x in guard] == ["Person at Gate 1"] and guard[0]["assigned_to_me"] is False
    assert [x["title"] for x in operator] == ["Weapon at Dock 4", "Person at Gate 1"], "every site, highest risk first"
    assert [x["title"] for x in supervisor] == ["Person at Gate 1"], "a supervisor of site A"
    assert len(viewer) == 2 and stranger == [] and nobody.status_code == 401
    assert after == [], "a closed situation is nobody's any longer"


# ─── C. Reporting from the ground ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_observation_is_recorded_as_that_persons_and_changes_nothing_else():
    w, s = await _ready(severity="critical", on_shift=True)
    async with _client() as c:
        await _dispatch(c, w, s)
        incident_before = (await _rows(w, "incidents"))[0]
        alerts_before = await _rows(w, "alerts")
        status_before = (await _situation(w, s))["decision_status"]
        arrived = await _report(c, w, s, GUARD, "ARRIVED", latitude=1.3001, longitude=103.8001)
        seen = await _report(c, w, s, GUARD, "OBSERVATION", note="  Night cleaner, badge checked.  ", via="mobile")
        listed = await c.get(_url(s, "observations"), headers=w["h"][VIEWER])
        hidden = await c.get(_url(s, "observations"), headers=(await _world())["h"][ADMIN])
    assert arrived.status_code == 201 and seen.status_code == 201
    rows = listed.json()
    assert [(r["kind"], r["note"], r["role_id"], r["name"]) for r in rows] == [
        ("ARRIVED", None, GUARD, "Role 5 User"), ("OBSERVATION", "Night cleaner, badge checked.", GUARD, "Role 5 User")]
    assert (rows[0]["latitude"], rows[0]["longitude"]) == (1.3001, 103.8001) and rows[1]["latitude"] is None
    assert hidden.status_code == 404
    # The incident's own arrival time is the command centre's to set, and was not set from here.
    incident_after = (await _rows(w, "incidents"))[0]
    assert incident_after == incident_before and incident_after["guard_arrived_at"] is None
    assert await _rows(w, "alerts") == alerts_before
    assert (await _situation(w, s))["decision_status"] == status_before, "reporting is not deciding"
    assert len(await _rows(w, "security_decisions", "decided_at")) == 1 and \
        len(await _rows(w, "security_actions", "executed_at")) == 2, "only the dispatch itself"

    audited = await _sql("SELECT user_id, detail FROM audit_logs WHERE tenant_id = :t "
                         "   AND action = 'intel.observation.record' ORDER BY created_at, id", {"t": w["tenant"]})
    details = [x["detail"] if isinstance(x["detail"], dict) else json.loads(x["detail"]) for x in audited]
    assert [(d["kind"], d["with_position"], d["via"]) for d in details] == [("ARRIVED", True, "mobile"),
                                                                           ("OBSERVATION", False, "mobile")]
    assert all(x["user_id"] == w["users"][GUARD] for x in audited)
    assert "badge" not in json.dumps(details), "that something was reported, not what was said"


@pytest.mark.asyncio
async def test_what_a_report_must_say_and_who_may_not_make_one():
    w, s = await _ready(on_shift=True)
    ref = str(uuid.uuid4())
    async with _client() as c:
        bad = [await _report(c, w, s, OPERATOR, kind, **body) for kind, body in (
            ("OBSERVATION", {}), ("OBSERVATION", {"note": "   "}), ("ARRIVED", {"latitude": 1.3}),
            ("ARRIVED", {"latitude": 91, "longitude": 103.8}), ("LEFT", {}), ("ARRIVED", {"decides": True}))]
        viewer = await _report(c, w, s, VIEWER, "OBSERVATION", note="x")
        missing = await c.post(f"{BASE}/situations/{uuid.uuid4()}/observations", headers=w["h"][ADMIN],
                               json={"kind": "ARRIVED"})
        assert await _rows(w, "security_observations", "observed_at") == []
        first = await _report(c, w, s, GUARD, "ACCEPTED", client_ref=ref)
        retry = await _report(c, w, s, GUARD, "ACCEPTED", client_ref=ref)
        someone_else = await _report(c, w, s, OPERATOR, "ACCEPTED", client_ref=ref)
        await _decide(c, w, s, OPERATOR, "RESOLVE", reason_code="ALREADY_HANDLED")
        late = await _report(c, w, s, GUARD, "OBSERVATION", note="Still here.")
    assert [r.status_code for r in bad] == [422] * 6
    assert "a note is needed" in bad[0].json()["detail"] and "together, or neither" in bad[2].json()["detail"]
    assert (viewer.status_code, missing.status_code) == (403, 404)
    assert (first.status_code, retry.status_code, someone_else.status_code) == (201, 200, 409)
    assert retry.json() == {"id": first.json()["id"], "kind": "ACCEPTED", "replayed": True}
    assert late.status_code == 409 and "closed" in late.json()["detail"]
    assert len(await _rows(w, "security_observations", "observed_at")) == 1


def test_a_report_is_a_persons_not_a_keys():
    from fastapi import HTTPException

    from app.dependencies.auth import TokenPayload
    from app.routers import security_decisions as api

    with pytest.raises(HTTPException) as refused:
        api._person(TokenPayload(user_id="k", tenant_id="t", role_id=4, via_api_key=True))
    assert refused.value.status_code == 403
    # The same gate stands in front of reporting as in front of deciding.
    import inspect
    assert "_person(token)" in inspect.getsource(api.record_observation)
    assert "_person(token)" in inspect.getsource(api.record_decision)


# ─── D. The schema ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_observations_are_tenant_isolated_and_can_only_be_added_and_read():
    w, s = await _ready(on_shift=True)
    other, other_s = await _ready(on_shift=True)
    async with _client() as c:
        await _report(c, w, s, GUARD, "ACCEPTED")
        await _report(c, other, other_s, GUARD, "ACCEPTED")
    row = (await _sql(
        "SELECT c.relrowsecurity AND c.relforcerowsecurity AS forced, "
        "       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies, "
        "       has_table_privilege('svc_app', c.oid, 'INSERT') AS can_add, "
        "       has_table_privilege('svc_app', c.oid, 'UPDATE') AS can_change, "
        "       has_table_privilege('svc_app', c.oid, 'DELETE') AS can_remove "
        "  FROM pg_class c WHERE c.relname = 'security_observations'"))[0]
    assert row["forced"] and row["policies"] == 1 and row["can_add"]
    assert not row["can_change"] and not row["can_remove"]
    async with AsyncSessionLocal() as db:
        who = (await db.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))).first()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        mine = (await db.execute(text("SELECT tenant_id FROM security_observations"))).scalars().all()
        for stmt in ("UPDATE security_observations SET note = 'x'", "DELETE FROM security_observations"):
            with pytest.raises(Exception, match="permission denied"):
                await db.execute(text(stmt))
            await db.rollback()
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        with pytest.raises(Exception):
            await db.execute(text(
                "INSERT INTO security_observations (tenant_id, situation_id, kind, actor_role) "
                "VALUES (:t,:s,'ACCEPTED',5)"), {"t": other["tenant"], "s": other_s["id"]})
        await db.rollback()
    assert who.rolsuper is False and who.rolbypassrls is False and set(mine) == {w["tenant"]}


@pytest.mark.asyncio
async def test_the_database_refuses_an_observation_that_says_nothing_or_stands_nowhere():
    w, s = await _ready()
    insert = ("INSERT INTO security_observations (tenant_id, situation_id, kind, note, actor_role, latitude, longitude, "
              "    via, client_ref) VALUES (:t,:s,:kind,:note,5,:lat,:lon,:via,:ref)")
    ok = {"t": w["tenant"], "s": s["id"], "kind": "ARRIVED", "note": None, "lat": None, "lon": None, "via": "mobile",
          "ref": None}
    for bad in ({"kind": "OBSERVATION"}, {"kind": "OBSERVATION", "note": "  "}, {"kind": "LEFT"}, {"via": "radio"},
                {"lat": 1.3}, {"lon": 103.8}, {"lat": 95.0, "lon": 103.8}, {"lat": 1.3, "lon": 190.0}):
        with pytest.raises(Exception):
            await _sql(insert, {**ok, **bad})
    ref = uuid.uuid4()
    await _sql(insert, {**ok, "ref": ref})
    await _sql(insert, {**ok, "kind": "OBSERVATION", "note": "Door shut.", "lat": 1.3, "lon": 103.8})
    with pytest.raises(Exception):
        await _sql(insert, {**ok, "ref": ref})              # the same report twice
    await _sql("DELETE FROM security_situations WHERE id = :s", {"s": s["id"]})
    assert await _rows(w, "security_observations", "observed_at") == [], "observations go with their situation"


def test_the_runner_cannot_reach_the_ground_either():
    """Reporting and reach belong to the API. Nothing the runner imports gets there."""
    import ast
    from pathlib import Path

    app_dir = Path(field.__file__).resolve().parents[1]
    reached, queue = set(), [app_dir / "intelligence_main.py"]
    while queue:
        path = queue.pop()
        if path in reached or not path.exists():
            continue
        reached.add(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.services"):
                names = [node.module.split(".")[-1]] if node.module != "app.services" else [a.name for a in node.names]
                queue.extend(app_dir / "services" / f"{n}.py" for n in names if n.startswith("intel_"))
    assert len(reached) >= 6, "the walk did not follow the runner's imports"
    assert not {"intel_field.py", "intel_actions.py", "intel_decisions.py"} & {p.name for p in reached}
