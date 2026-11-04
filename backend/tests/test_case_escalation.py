"""Phase 3 of the known limits (4 November 2026): people told when put on a case, and cases that escalate
(services/case_notify.py, services/case_escalation.py, routers/case_escalation.py, migration 0177).

A. Put on a case: the lead (opened by somebody else, or changed), an investigator, a task given - told; never the
   person who did it, nor somebody who cannot see the case's site; a telling that fails leaves the change saved.
B. Escalations: an overdue task, a quiet case, a close request waiting - each at its time, first and second step,
   once; a note starts the quiet count again; a finished task stops; never backdated; as the application's role,
   one organisation's pass touches nothing of another's.
C. The timings: read by whoever reads cases, changed by whoever may change the organisation's settings, and used.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.main import app  # noqa: F401 - imported first, so the first test does not absorb the import
from app.db.session import AsyncSessionLocal
from app.services import case_escalation, case_notify, response_notify
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world
from tests.test_investigation_search import _audit

CASES = "/api/v1/cases"
SETTINGS = "/api/v1/case-escalations/settings"


def _ago(**kw) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kw)


@pytest.fixture
def told(monkeypatch):
    """Every telling, as (event, who, words, payload) - and nothing sent anywhere."""
    calls: list[tuple[str, list[str], str, dict]] = []

    async def fake(redis, session_factory, tenant_id, event_type, user_ids, message, payload, now=None):
        calls.append((event_type, sorted(str(u) for u in user_ids), message, dict(payload)))
        return True

    monkeypatch.setattr(case_notify, "tell", fake)
    return calls


# ─── A. Put on a case ────────────────────────────────────────────────────────

async def test_somebody_put_on_a_case_is_told_and_never_the_person_who_did_it(told):
    w = await _world()
    u = {r: str(w["users"][r]) for r in (ADMIN, MANAGER, OPERATOR, SUPERVISOR)}
    async with _client() as c:
        mine = await c.post(CASES, headers=w["h"][OPERATOR], json={"title": "Gate", "summary": "Forced.",
                                                                    "site_id": str(w["site_a"])})
        assert mine.status_code == 201 and told == [], "who opens and leads it is not told of it"
        opened = await c.post(CASES, headers=w["h"][MANAGER], json={
            "title": "East gate forced", "summary": "Forced at night.", "site_id": str(w["site_a"]),
            "lead_user_id": u[OPERATOR]})
        assert opened.status_code == 201, opened.text
        case = opened.json()
        assert told[-1][:2] == ("case_assigned", [u[OPERATOR]])
        assert told[-1][2] == f"You are the lead on {case['case_number']}: East gate forced"
        assert told[-1][3]["as"] == "LEAD" and told[-1][3]["case_id"] == case["id"]

        cid = case["id"]
        assert (await c.post(f"{CASES}/{cid}/investigators", headers=w["h"][MANAGER],
                             json={"user_id": u[SUPERVISOR]})).status_code == 201
        assert told[-1][:2] == ("case_assigned", [u[SUPERVISOR]]) and told[-1][3]["as"] == "INVESTIGATOR"
        due = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
        task = await c.post(f"{CASES}/{cid}/tasks", headers=w["h"][MANAGER], json={
            "title": "Get the gatehouse CCTV", "assigned_to_user_id": u[OPERATOR], "due_at": due.isoformat()})
        assert task.status_code == 201, task.text
        assert told[-1][:2] == ("case_assigned", [u[OPERATOR]]) and told[-1][3]["as"] == "TASK"
        assert told[-1][2].startswith(f"Task for you on {case['case_number']}: Get the gatehouse CCTV, due ")
        # A task given to oneself, or to nobody, tells nobody.
        before = len(told)
        assert (await c.post(f"{CASES}/{cid}/tasks", headers=w["h"][MANAGER], json={
            "title": "Call the client", "assigned_to_user_id": u[MANAGER]})).status_code == 201
        assert (await c.post(f"{CASES}/{cid}/tasks", headers=w["h"][MANAGER], json={"title": "Check the fence"})).status_code == 201
        assert len(told) == before
        assert (await c.put(f"{CASES}/{cid}/lead", headers=w["h"][MANAGER], json={"user_id": u[ADMIN]})).status_code == 200
        assert told[-1][:2] == ("case_assigned", [u[ADMIN]]) and told[-1][3]["as"] == "LEAD"


async def test_nobody_is_told_of_a_case_they_cannot_see(told):
    w = await _world()
    async with _client() as c:
        # The supervisor is held to site A: a case at site B is not theirs to see, so they are not told of it.
        opened = await c.post(CASES, headers=w["h"][MANAGER], json={"title": "Fence", "summary": "Cut.",
                                                                     "site_id": str(w["site_b"])})
        cid = opened.json()["id"]
        before = len(told)
        r = await c.post(f"{CASES}/{cid}/investigators", headers=w["h"][MANAGER],
                         json={"user_id": str(w["users"][SUPERVISOR])})
        assert r.status_code == 201 and len(told) == before


async def test_a_telling_that_fails_leaves_the_change_saved(monkeypatch):
    w = await _world()

    async def broken(*args, **kwargs):
        raise RuntimeError("the push service is down")

    monkeypatch.setattr(response_notify, "push", broken)
    monkeypatch.setattr(response_notify, "announce", broken)
    async with _client() as c:
        opened = await c.post(CASES, headers=w["h"][MANAGER], json={
            "title": "Gate", "summary": "Forced.", "site_id": str(w["site_a"]),
            "lead_user_id": str(w["users"][OPERATOR])})
    assert opened.status_code == 201, opened.text
    lead = await _sql("SELECT lead_user_id FROM case_files WHERE id = :c", {"c": opened.json()["id"]})
    assert lead[0]["lead_user_id"] == w["users"][OPERATOR]


# ─── B. Escalations ──────────────────────────────────────────────────────────

async def _watched_since(w: dict, ago: timedelta) -> None:
    """The organisation's escalation began that long ago (as if it had been watching)."""
    await _sql("INSERT INTO case_escalation_settings (tenant_id, since) VALUES (:t, :s)",
               {"t": w["tenant"], "s": datetime.now(timezone.utc) - ago})


async def _case(w: dict, *, quiet: timedelta, priority: str = "NORMAL", site: str = "site_a",
                status: str = "OPEN", close_asked: timedelta | None = None) -> uuid.UUID:
    """A case, led by the operator, last added to `quiet` ago."""
    cid, at = uuid.uuid4(), datetime.now(timezone.utc) - quiet
    await _run([
        ("INSERT INTO case_files (id, tenant_id, site_id, case_number, title, summary, priority, lead_user_id, "
         "opened_by_user_id, opened_at, updated_at) VALUES (:i,:t,:s,:n,'East gate','Forced.',:p,:lead,:by,:at,:at)",
         {"i": cid, "t": w["tenant"], "s": w[site], "n": f"CASE-{uuid.uuid4().hex[:6]}", "p": priority,
          "lead": w["users"][OPERATOR], "by": w["users"][MANAGER], "at": at}),
        ("INSERT INTO case_entries (tenant_id, case_id, kind, actor_user_id, occurred_at) VALUES (:t,:c,'OPENED',:u,:at)",
         {"t": w["tenant"], "c": cid, "u": w["users"][MANAGER], "at": at}),
    ])
    if status == "AWAITING_APPROVAL":
        asked = datetime.now(timezone.utc) - close_asked
        await _sql("UPDATE case_files SET status = 'AWAITING_APPROVAL', outcome = 'Gate repaired.', "
                   "close_requested_at = :at, close_requested_by_user_id = :u, updated_at = :at WHERE id = :c",
                   {"at": asked, "u": w["users"][MANAGER], "c": cid})
    return cid


async def _task(w: dict, case_id, *, due_ago: timedelta, to: int = GUARD) -> uuid.UUID:
    tid = uuid.uuid4()
    made = datetime.now(timezone.utc) - due_ago - timedelta(days=1)
    await _sql("INSERT INTO case_tasks (id, tenant_id, case_id, title, assigned_to_user_id, due_at, created_by_user_id, "
               "created_at, updated_at) VALUES (:i,:t,:c,'Get the CCTV',:to,:due,:by,:m,:m)",
               {"i": tid, "t": w["tenant"], "c": case_id, "to": w["users"][to], "by": w["users"][MANAGER],
                "due": datetime.now(timezone.utc) - due_ago, "m": made})
    return tid


async def _pass(told) -> list:
    before = len(told)
    counts = await case_escalation.run(AsyncSessionLocal, None)
    assert counts["failed"] == 0
    return told[before:]


def _for(told_now, case_id) -> list:
    return [(t[3]["kind"], t[3]["step"], t[1]) for t in told_now if t[3]["case_id"] == str(case_id)]


async def test_an_overdue_task_tells_the_people_on_it_then_managers_and_admins_once(told):
    w = await _world()
    await _watched_since(w, timedelta(days=60))
    # The operator can do cases work but the guard cannot read cases: a task given to a guard tells the lead only.
    late = await _case(w, quiet=timedelta(hours=1))
    await _task(w, late, due_ago=timedelta(hours=2), to=OPERATOR)
    later = await _case(w, quiet=timedelta(hours=1))
    await _task(w, later, due_ago=timedelta(hours=30), to=OPERATOR)
    u = {r: str(w["users"][r]) for r in (ADMIN, MANAGER, OPERATOR, SUPERVISOR)}
    now = await _pass(told)
    assert _for(now, late) == [("TASK_OVERDUE", 1, [u[OPERATOR]])]
    above = sorted([u[ADMIN], u[MANAGER]])
    assert sorted(_for(now, later)) == [("TASK_OVERDUE", 1, [u[OPERATOR]]), ("TASK_OVERDUE", 2, above)]
    assert any("is still overdue after 24 hours" in t[2] for t in now)
    # Again: nothing more. Each is recorded once, with how many it was addressed to, and audited.
    assert await _pass(told) == []
    rows = await _sql("SELECT kind, step, addressed FROM case_escalations WHERE case_id = :c ORDER BY step",
                      {"c": later})
    assert [(r["kind"], r["step"], r["addressed"]) for r in rows] == [("TASK_OVERDUE", 1, 1), ("TASK_OVERDUE", 2, 2)]
    assert len(await _audit(w, "case.escalate")) == 3
    # A finished task escalates no more.
    await _sql("UPDATE case_tasks SET state = 'DONE', done_at = now(), done_note = 'Got it.' WHERE case_id = :c",
               {"c": late})
    assert _for(await _pass(told), late) == []


async def test_a_quiet_case_tells_its_lead_then_managers_and_a_note_starts_the_count_again(told):
    w = await _world()
    await _watched_since(w, timedelta(days=60))
    u = {r: str(w["users"][r]) for r in (ADMIN, MANAGER, OPERATOR)}
    quiet = await _case(w, quiet=timedelta(days=8))
    quieter = await _case(w, quiet=timedelta(days=10))
    high = await _case(w, quiet=timedelta(days=4), priority="HIGH")
    low = await _case(w, quiet=timedelta(days=8), priority="LOW")
    now = await _pass(told)
    assert _for(now, quiet) == [("QUIET", 1, [u[OPERATOR]])]
    assert sorted(_for(now, quieter)) == [("QUIET", 1, [u[OPERATOR]]), ("QUIET", 2, sorted([u[ADMIN], u[MANAGER]]))]
    assert _for(now, high) == [("QUIET", 1, [u[OPERATOR]])] and _for(now, low) == []
    assert any("has had nothing added for 7 days (normal priority)" in t[2] for t in now)
    # A note: the case is not quiet now. Nothing more until it has been quiet again.
    await _sql("INSERT INTO case_entries (tenant_id, case_id, kind, body, actor_user_id) VALUES (:t,:c,'NOTE','Spoke to the guard.',:u)",
               {"t": w["tenant"], "c": quiet, "u": w["users"][OPERATOR]})
    assert _for(await _pass(told), quiet) == []
    # Quiet again for eight days from that note: told afresh.
    await _sql("UPDATE case_entries SET occurred_at = now() - interval '8 days' WHERE case_id = :c AND kind = 'NOTE'",
               {"c": quiet})
    assert _for(await _pass(told), quiet) == [("QUIET", 1, [u[OPERATOR]])]


async def test_a_close_request_waiting_tells_who_may_approve_and_never_who_asked(told):
    w = await _world()
    await _watched_since(w, timedelta(days=60))
    waiting = await _case(w, quiet=timedelta(days=5), status="AWAITING_APPROVAL", close_asked=timedelta(days=3))
    fresh = await _case(w, quiet=timedelta(days=5), status="AWAITING_APPROVAL", close_asked=timedelta(hours=6))
    now = await _pass(told)
    # Who holds case:manage and sees site A: the admin and the supervisor. The manager asked, so is not told.
    assert _for(now, waiting) == [("CLOSE_WAITING", 1, sorted([str(w["users"][ADMIN]), str(w["users"][SUPERVISOR])]))]
    assert _for(now, fresh) == [] and not [t for t in now if t[3]["kind"] == "QUIET"], "a case waiting is not quiet"


async def test_nothing_is_backdated_and_one_organisation_sees_nothing_of_another(told):
    w, other = await _world(), await _world()
    # No settings row: the first pass makes it, and escalation begins then. A year-quiet case is not told of as
    # quiet, nor are Managers told of a task as if it had been overdue all along: its assignee is told it is overdue
    # now, as it is - the later of when it fell due and when escalation began.
    old = await _case(w, quiet=timedelta(days=365))
    await _task(w, old, due_ago=timedelta(days=100), to=OPERATOR)
    await _watched_since(other, timedelta(days=60))
    theirs = await _case(other, quiet=timedelta(days=8))
    now = await _pass(told)
    assert _for(now, old) == [("TASK_OVERDUE", 1, [str(w["users"][OPERATOR])])]
    assert _for(now, theirs) == [("QUIET", 1, [str(other["users"][OPERATOR])])]
    made = await _sql("SELECT since FROM case_escalation_settings WHERE tenant_id = :t", {"t": w["tenant"]})
    assert len(made) == 1 and made[0]["since"] > _ago(minutes=5)
    # As the application's role in one organisation's scope: its own escalations only.
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        seen = (await db.execute(text("SELECT case_id FROM case_escalations"))).scalars().all()
        await db.rollback()
    assert seen == [old], "its own escalation, and none of the other organisation's"
    # Once it has watched for long enough, the old case is told of like any other.
    await _sql("UPDATE case_escalation_settings SET since = now() - interval '8 days' WHERE tenant_id = :t",
               {"t": w["tenant"]})
    assert sorted((k, n) for k, n, _ in _for(await _pass(told), old)) == [("QUIET", 1), ("TASK_OVERDUE", 2)]


# ─── C. The timings ──────────────────────────────────────────────────────────

async def test_the_timings_are_read_by_case_readers_changed_by_settings_writers_and_used(told):
    w = await _world()
    await _watched_since(w, timedelta(days=60))
    body = {"quiet_high_days": 2, "quiet_normal_days": 3, "quiet_low_days": 30, "overdue_hours": 4,
            "close_wait_days": 1}
    async with _client() as c:
        shown = await c.get(SETTINGS, headers=w["h"][VIEWER])
        assert shown.status_code == 200 and shown.json()["quiet_normal_days"] == 7
        assert shown.json()["may_set"] is False and shown.json()["defaults"]["overdue_hours"] == 24
        assert (await c.get(SETTINGS, headers=w["h"][MANAGER])).json()["may_set"] is True
        for role in (SUPERVISOR, OPERATOR, VIEWER):
            assert (await c.put(SETTINGS, headers=w["h"][role], json=body)).status_code == 403, role
        for bad in ({**body, "quiet_normal_days": 0}, {**body, "overdue_hours": 169}, {**body, "extra": 1},
                    {k: v for k, v in body.items() if k != "close_wait_days"}):
            assert (await c.put(SETTINGS, headers=w["h"][MANAGER], json=bad)).status_code == 422, bad
        done = await c.put(SETTINGS, headers=w["h"][MANAGER], json=body)
        assert done.status_code == 200 and done.json()["quiet_normal_days"] == 3
        assert done.json()["updated_by_name"] == "Role 8 User"
    (entry,) = await _audit(w, "case.escalation.settings")
    assert entry["detail"]["from"]["quiet_normal_days"] == 7 and entry["detail"]["to"] == body
    # Three quiet days is now enough for a normal case.
    cid = await _case(w, quiet=timedelta(days=4))
    assert _for(await _pass(told), cid) == [("QUIET", 1, [str(w["users"][OPERATOR])])]
    async with _client() as c:
        listed = await c.get(f"/api/v1/case-escalations/cases/{cid}", headers=w["h"][VIEWER])
        assert listed.status_code == 200
        (one,) = listed.json()["escalations"]
        assert one["kind"] == "QUIET" and one["kind_label"] == "Nothing has been added" and one["addressed"] == 1
        assert "has had nothing added for 3 days" in one["message"]
        assert (await c.get(f"/api/v1/case-escalations/cases/{uuid.uuid4()}", headers=w["h"][VIEWER])).status_code == 404
