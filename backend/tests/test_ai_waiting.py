"""The corner card: what the AI waits on a person to decide, and the push after the hourly pass.

Design: docs/plans/2026-10-31-ai-corner-button-and-decision-popups-design.md.

The check is asked through the app, which connects as svc_app, so row-level
security applies to every read here (tests that connect as the superuser skip
it); the two-organisation test says so before it trusts anything.

Sections:
  A — What the check shows, and to whom (6 tests)
  B — The push after the pass (5 tests)
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app  # noqa: F401  (module level: the ML import must not land inside a test)
from app.services import ai_decision_push, ai_runner
from tests.test_ai_assistant import _chat, _op, scripted  # noqa: F401  (the fixture)
from tests.test_ai_learning import (
    ADMIN, GUARD, MANAGER, NOW, OPERATOR, SUPERVISOR, _admin_session_run, _client, _late_history, _run, _sql, _world,
)


async def _waiting_world(*, assistant_roles=(2, 3, 5, 8), insight_roles=(2, 3, 8)) -> dict:
    """An organisation with the assistant (guards included) and the late-and-no-show warnings switched on."""
    w = await _world(features=("assistant", "late_risk"), enable=("late_risk",), roles=insight_roles)
    await _sql("INSERT INTO ai_feature_settings (tenant_id, feature_key, enabled, role_ids) "
               "VALUES (:t, 'assistant', TRUE, CAST(:r AS smallint[]))", {"t": w["tenant"], "r": list(assistant_roles)})
    return w


async def _card(w, role, title, *, finished=True, expires_in=timedelta(minutes=20), status="PENDING") -> uuid.UUID:
    """A Confirm card in one of the person's own conversations, made two minutes ago."""
    conv, card = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    reply_at = now - (timedelta(minutes=1) if finished else timedelta(minutes=5))
    await _run([
        ("INSERT INTO ai_conversations (id, tenant_id, user_id, title, channel, provider, model) "
         "VALUES (:c,:t,:u,'Chat','PHONE','LOCAL','m')", {"c": conv, "t": w["tenant"], "u": w["users"][role]}),
        ("INSERT INTO ai_pending_actions (id, tenant_id, conversation_id, user_id, operation_id, method, path, body, "
         "  title, status, created_at, expires_at) VALUES (:a,:t,:c,:u,'x','POST','/api/v1/visitors',:b,:title,:st,:at,:e)",
         {"a": card, "t": w["tenant"], "c": conv, "u": w["users"][role], "b": json.dumps({"full_name": "Tan"}),
          "title": title, "st": status, "at": now - timedelta(minutes=2), "e": now + expires_in}),
        ("INSERT INTO ai_messages (tenant_id, conversation_id, role, text, provider, model, created_at) "
         "VALUES (:t,:c,'assistant','Prepared.','LOCAL','m',:at)", {"t": w["tenant"], "c": conv, "at": reply_at}),
    ])
    return card


async def _warning(w, title, *, score=0.8, site="site_a", valid_for=timedelta(hours=10), status="OPEN") -> uuid.UUID:
    wid = uuid.uuid4()
    await _sql("INSERT INTO ai_suggestions (id, tenant_id, feature_key, subject_type, subject_id, site_id, title, "
               "  score, status, valid_until, decided_at) VALUES (:i,:t,'late_risk','shift',:s,:site,:title,:score,:st,:u,:d)",
               {"i": wid, "t": w["tenant"], "s": uuid.uuid4(), "site": w[site] if site else None, "title": title,
                "score": score, "st": status, "u": datetime.now(timezone.utc) + valid_for,
                "d": datetime.now(timezone.utc) if status in ("ACCEPTED", "DISMISSED") else None})
    return wid


async def _waiting(c, w, role) -> dict:
    r = await c.get("/api/v1/ai/waiting", headers=w["h"][role])
    assert r.status_code == 200, r.text
    return r.json()


# ─── A. What the check shows, and to whom ────────────────────────────────────

@pytest.mark.asyncio
async def test_a_guard_sees_only_their_own_confirm_cards_and_never_warnings():
    w = await _waiting_world()
    mine = await _card(w, GUARD, "Register visitor: Tan Wei Ming")
    await _card(w, SUPERVISOR, "Add site: Changi")
    await _warning(w, "Late Lim may be late tonight")
    async with _client() as c:
        got = await _waiting(c, w, GUARD)
    assert got["assistant"] is True
    assert [a["id"] for a in got["actions"]] == [str(mine)]
    assert got["actions"][0]["title"] == "Register visitor: Tan Wei Ming"
    assert got["warnings"] == [] and got["counts"] == {"actions": 1, "warnings": 0}, "a guard may not decide warnings"


@pytest.mark.asyncio
async def test_a_supervisor_sees_the_warnings_of_their_sites_most_important_first_with_a_count():
    w = await _waiting_world()
    low = await _warning(w, "Prompt Tan may be late", score=0.55)
    high = await _warning(w, "Late Lim may be late tonight", score=0.92)
    other_site = await _warning(w, "Tuas guard may be late", score=0.99, site="site_b")
    async with _client() as c:
        everywhere = await _waiting(c, w, SUPERVISOR)
        await _sql("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:u,:s,:t)",
                   {"u": w["users"][SUPERVISOR], "s": w["site_a"], "t": w["tenant"]})
        jurong_only = await _waiting(c, w, SUPERVISOR)
        operator = await _waiting(c, w, OPERATOR)
    assert [x["id"] for x in everywhere["warnings"]] == [str(other_site), str(high), str(low)]
    assert everywhere["counts"]["warnings"] == 3
    assert [x["id"] for x in jurong_only["warnings"]] == [str(high), str(low)], "Tuas is not the supervisor's site"
    assert jurong_only["warnings"][0]["reasons"] == [] and jurong_only["warnings"][0]["site_name"] == "Jurong"
    assert operator["warnings"] == [], "an operator may read warnings but not decide them"


@pytest.mark.asyncio
async def test_expired_unfinished_decided_and_other_people_s_cards_are_not_offered():
    w = await _waiting_world()
    live = await _card(w, ADMIN, "Add site: Live")
    await _card(w, ADMIN, "Add site: Expired", expires_in=timedelta(minutes=-1))
    await _card(w, ADMIN, "Add site: Still being written", finished=False)
    await _card(w, ADMIN, "Add site: Already confirmed", status="CONFIRMED")
    await _card(w, MANAGER, "Add site: The manager's")
    await _warning(w, "Decided already", status="ACCEPTED")
    await _warning(w, "About a shift already over", valid_for=timedelta(hours=-1))
    async with _client() as c:
        got = await _waiting(c, w, ADMIN)
    assert [a["id"] for a in got["actions"]] == [str(live)]
    assert got["warnings"] == [] and got["counts"]["warnings"] == 0


@pytest.mark.asyncio
async def test_without_a_licence_or_the_assistant_it_answers_empty_and_never_refuses():
    w = await _waiting_world(assistant_roles=(2, 3, 8))
    await _card(w, GUARD, "Register visitor: before the switch")
    await _warning(w, "Late Lim may be late tonight")
    async with _client() as c:
        guard = await _waiting(c, w, GUARD)
        await _sql("UPDATE ai_module_licenses SET is_enabled = FALSE WHERE tenant_id = :t", {"t": w["tenant"]})
        admin = await _waiting(c, w, ADMIN)
    assert guard == {"assistant": False, "actions": [], "warnings": [], "counts": {"actions": 0, "warnings": 0}}
    assert admin == {"assistant": False, "actions": [], "warnings": [], "counts": {"actions": 0, "warnings": 0}}


@pytest.mark.asyncio
async def test_a_card_the_chat_prepared_is_offered_until_it_is_decided(scripted):
    """The card the real chat stores is the one the check offers, and deciding it takes it away."""
    w = await _waiting_world()
    scripted([{"tool": "use_function", "args": {"operation_id": _op("POST", "/api/v1/sites"),
                                                "body": {"name": "Changi Depot"}}},
              {"text": "I have prepared the new site; confirm it on the card."}])
    async with _client() as c:
        await _chat(c, w, ADMIN, "Add a site called Changi Depot")
        before = await _waiting(c, w, ADMIN)
        card = before["actions"][0]
        cancel = await c.post(f"/api/v1/ai/assistant/actions/{card['id']}/cancel", headers=w["h"][ADMIN])
        after = await _waiting(c, w, ADMIN)
    assert card["title"].endswith(": Changi Depot") and card["status"] == "PENDING"
    assert cancel.status_code == 200 and after["actions"] == []


@pytest.mark.asyncio
async def test_each_organisation_sees_only_its_own_as_the_app_role():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    try:
        async with engine.connect() as s:
            restricted = (await s.execute(text(
                "SELECT NOT (rolsuper OR rolbypassrls) FROM pg_roles WHERE rolname = current_user"))).scalar()
    finally:
        await engine.dispose()
    assert restricted, "the app connects as a role that bypasses RLS: this test would prove nothing"
    a, b = await _waiting_world(), await _waiting_world()
    a_card, b_card = await _card(a, ADMIN, "Add site: A"), await _card(b, ADMIN, "Add site: B")
    a_warn, b_warn = await _warning(a, "A's warning"), await _warning(b, "B's warning")
    async with _client() as c:
        got_a, got_b = await _waiting(c, a, ADMIN), await _waiting(c, b, ADMIN)
    assert [x["id"] for x in got_a["actions"]] == [str(a_card)] and [x["id"] for x in got_a["warnings"]] == [str(a_warn)]
    assert [x["id"] for x in got_b["actions"]] == [str(b_card)] and [x["id"] for x in got_b["warnings"]] == [str(b_warn)]


# ─── B. The push after the pass ──────────────────────────────────────────────

class _Redis:
    """The device registrations the phone made (routers/users.py), one phone each."""

    def __init__(self, w, roles):
        self.keys = {f"push_tokens:{w['tenant']}:{w['users'][r]}": {f"ExponentPushToken[{r}]"} for r in roles}

    async def smembers(self, key):
        return self.keys.get(key, set())


class _Sent:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def __call__(self, tokens, title, body, data):
        if self.fail:
            raise RuntimeError("the push gateway is down")
        self.calls.append((tuple(tokens), title, body, data))


def _factory():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _pass_and_push(w, redis, sent, since=None):
    since = since or (await _sql("SELECT now() AS n"))[0]["n"]
    await _admin_session_run(w)
    engine, factory = _factory()
    try:
        return await ai_decision_push.after_pass(factory, str(w["tenant"]), since=since, redis=redis, send=sent)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_after_a_pass_each_person_who_may_decide_is_pushed_once_with_their_count():
    w = await _waiting_world()
    await _late_history(w)
    sent = _Sent()
    pushed = await _pass_and_push(w, _Redis(w, (ADMIN, SUPERVISOR, MANAGER, OPERATOR, GUARD)), sent)
    assert pushed == {str(w["users"][r]): 1 for r in (ADMIN, SUPERVISOR, MANAGER)}
    assert sorted(c[0][0] for c in sent.calls) == sorted(f"ExponentPushToken[{r}]" for r in (ADMIN, SUPERVISOR, MANAGER))
    assert all(c[1:] == (ai_decision_push.TITLE, "1 new AI warning to decide. Open the app to accept or dismiss.",
                         {"type": "ai_waiting"}) for c in sent.calls)


@pytest.mark.asyncio
async def test_nobody_is_pushed_a_warning_they_may_not_decide():
    """The roles the admin chose, the permission, and the site, as the card itself."""
    w = await _waiting_world(insight_roles=(2, 3))
    await _late_history(w)
    await _sql("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:u,:s,:t)",
               {"u": w["users"][SUPERVISOR], "s": w["site_b"], "t": w["tenant"]})
    pushed = await _pass_and_push(w, _Redis(w, (ADMIN, SUPERVISOR, MANAGER, OPERATOR, GUARD)), _Sent())
    assert pushed == {str(w["users"][ADMIN]): 1}, "manager's role not chosen; supervisor only at Tuas"


@pytest.mark.asyncio
async def test_a_warning_from_an_earlier_pass_is_not_pushed_again():
    w = await _waiting_world()
    await _late_history(w)
    first = await _pass_and_push(w, _Redis(w, (ADMIN,)), _Sent())
    second = await _pass_and_push(w, _Redis(w, (ADMIN,)), _Sent())
    assert first == {str(w["users"][ADMIN]): 1} and second == {}


@pytest.mark.asyncio
async def test_a_push_that_fails_is_not_raised_and_changes_nothing():
    w = await _waiting_world()
    await _late_history(w)
    pushed = await _pass_and_push(w, _Redis(w, (ADMIN,)), _Sent(fail=True))
    rows = await _sql("SELECT status FROM ai_suggestions WHERE tenant_id = :t", {"t": w["tenant"]})
    assert pushed == {} and [r["status"] for r in rows] == ["OPEN"]


@pytest.mark.asyncio
async def test_the_hourly_pass_pushes_after_its_commit_with_its_own_clock(monkeypatch):
    w = await _waiting_world()
    await _late_history(w)
    told = []

    async def record(factory, tenant_id, *, since):
        rows = await _sql("SELECT created_at FROM ai_suggestions WHERE tenant_id = :t", {"t": w["tenant"]})
        told.append((tenant_id, since, [r["created_at"] for r in rows]))
        return {}

    monkeypatch.setattr(ai_decision_push, "after_pass", record)
    engine, factory = _factory()
    try:
        done = await ai_runner.run_all(factory, now=NOW)
    finally:
        await engine.dispose()
    mine = [t for t in told if t[0] == str(w["tenant"])]
    assert str(w["tenant"]) in done and len(mine) == 1
    _, since, created = mine[0]
    assert created and all(at == since for at in created), "the warning was not already committed, or not this pass's"
    assert {t[0] for t in told} <= set(done), "pushed for an organisation whose pass did nothing"
