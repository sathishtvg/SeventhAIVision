"""The AI module, phase 7: the assistant prepares a change, and only the person's Confirm sends it.

Sections:
  A — Prepared, then confirmed or cancelled (4 tests)
  B — What stops a Confirm (3 tests)
  C — Passwords and roles (2 tests)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.main import app  # noqa: F401
from tests.test_ai_assistant import _assistant_world, _chat, _op, scripted  # noqa: F401  (the fixture)
from tests.test_ai_learning import ADMIN, SUPERVISOR, _client, _run, _sql


def _confirm_card(events):
    return next(d["action"] for k, d in events if k == "confirm")


# ─── A. Prepared, then confirmed or cancelled ────────────────────────────────

@pytest.mark.asyncio
async def test_a_change_waits_on_a_confirm_card_and_is_sent_as_the_person_when_confirmed(scripted):
    w = await _assistant_world()
    model = scripted([{"tool": "use_function", "args": {"operation_id": _op("POST", "/api/v1/sites"),
                                                        "body": {"name": "Changi Depot"}}},
                      {"text": "I have prepared the new site Changi Depot; confirm it on the card."}])
    async with _client() as c:
        r, events = await _chat(c, w, ADMIN, "Add a site called Changi Depot")
        card = _confirm_card(events)
        before = await _sql("SELECT count(*) AS n FROM sites WHERE tenant_id = :t", {"t": w["tenant"]})
        ok = await c.post(f"/api/v1/ai/assistant/actions/{card['id']}/confirm", headers=w["h"][ADMIN])
        again = await c.post(f"/api/v1/ai/assistant/actions/{card['id']}/confirm", headers=w["h"][ADMIN])
    assert r.status_code == 200 and json.loads(model.results[0][0])["prepared"] is True
    assert card["title"].endswith(": Changi Depot") and card["status"] == "PENDING"
    assert {"name": "name", "value": "Changi Depot"} in card["fields"]
    assert before[0]["n"] == 2, "the site was created before the Confirm"
    assert ok.status_code == 200 and ok.json()["status"] == "CONFIRMED" and ok.json()["result"]["status"] in (200, 201)
    made = await _sql("SELECT name FROM sites WHERE tenant_id = :t AND name = 'Changi Depot'", {"t": w["tenant"]})
    assert len(made) == 1
    assert again.status_code == 409
    audit = await _sql("SELECT user_id, detail FROM audit_logs WHERE tenant_id = :t AND action = 'ai.assistant.action'",
                       {"t": w["tenant"]})
    assert len(audit) == 1 and audit[0]["user_id"] == w["users"][ADMIN]
    assert audit[0]["detail"]["outcome"] == "CONFIRMED" and audit[0]["detail"]["method"] == "POST"
    notes = await _sql("SELECT text FROM ai_messages WHERE conversation_id = :c AND role = 'user' ORDER BY created_at",
                       {"c": uuid.UUID(card["conversation_id"])})
    assert notes[-1]["text"].startswith('the person confirmed "') and "the app accepted it" in notes[-1]["text"]


@pytest.mark.asyncio
async def test_missing_details_are_asked_for_rather_than_guessed(scripted):
    w = await _assistant_world()
    model = scripted([{"tool": "use_function", "args": {"operation_id": _op("POST", "/api/v1/sites"), "body": {}}},
                      {"text": "What should the site be called?"}])
    async with _client() as c:
        _, events = await _chat(c, w, ADMIN, "Add a site")
    assert model.results[0][1] and "Ask the person for: name" in model.results[0][0]
    assert not [k for k, _ in events if k == "confirm"]


@pytest.mark.asyncio
async def test_cancelled_nothing_changes_and_the_model_is_told(scripted):
    w = await _assistant_world()
    scripted([{"tool": "use_function", "args": {"operation_id": _op("POST", "/api/v1/sites"), "body": {"name": "X"}}},
              {"text": "Prepared."}])
    async with _client() as c:
        _, events = await _chat(c, w, ADMIN, "Add site X")
        card = _confirm_card(events)
        cancel = await c.post(f"/api/v1/ai/assistant/actions/{card['id']}/cancel", headers=w["h"][ADMIN])
        listed = (await c.get("/api/v1/ai/assistant/actions", headers=w["h"][ADMIN],
                              params={"conversation_id": card["conversation_id"]})).json()
        follow = scripted([{"text": "Understood."}])
        await _chat(c, w, ADMIN, "Never mind", card["conversation_id"])
    assert cancel.status_code == 200 and cancel.json()["status"] == "CANCELLED"
    assert [x["status"] for x in listed["items"]] == ["CANCELLED"]
    assert await _sql("SELECT 1 FROM sites WHERE tenant_id = :t AND name = 'X'", {"t": w["tenant"]}) == []
    told = [m for m in follow.seen[0]["history"] if "cancelled" in str(m.get("content"))]
    assert told and told[0]["role"] == "user" and "Note from the app, not from the person" in told[0]["content"]


@pytest.mark.asyncio
async def test_only_the_person_who_asked_can_confirm_their_card(scripted):
    w = await _assistant_world()
    scripted([{"tool": "use_function", "args": {"operation_id": _op("POST", "/api/v1/sites"), "body": {"name": "Y"}}},
              {"text": "Prepared."}])
    async with _client() as c:
        _, events = await _chat(c, w, ADMIN, "Add site Y")
        card = _confirm_card(events)
        other = await c.post(f"/api/v1/ai/assistant/actions/{card['id']}/confirm", headers=w["h"][SUPERVISOR])
    assert other.status_code == 404
    assert await _sql("SELECT 1 FROM sites WHERE tenant_id = :t AND name = 'Y'", {"t": w["tenant"]}) == []


# ─── B. What stops a Confirm ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_card_left_for_more_than_30_minutes_expires(scripted):
    w = await _assistant_world()
    scripted([{"tool": "use_function", "args": {"operation_id": _op("POST", "/api/v1/sites"), "body": {"name": "Z"}}},
              {"text": "Prepared."}])
    async with _client() as c:
        _, events = await _chat(c, w, ADMIN, "Add site Z")
        card = _confirm_card(events)
        await _sql("UPDATE ai_pending_actions SET expires_at = now() - interval '1 minute' WHERE id = :a",
                   {"a": uuid.UUID(card["id"])})
        late = await c.post(f"/api/v1/ai/assistant/actions/{card['id']}/confirm", headers=w["h"][ADMIN])
    assert late.status_code == 409 and "expired" in late.json()["detail"]
    row = await _sql("SELECT status FROM ai_pending_actions WHERE id = :a", {"a": uuid.UUID(card["id"])})
    assert row[0]["status"] == "EXPIRED"


@pytest.mark.asyncio
async def test_a_card_cannot_be_confirmed_before_the_reply_that_made_it_is_stored(scripted):
    w = await _assistant_world()
    scripted([{"text": "Hello."}])
    async with _client() as c:
        _, events = await _chat(c, w, ADMIN, "Hello")
        cid = uuid.UUID(events[0][1]["conversation_id"])
        aid = uuid.uuid4()
        await _sql("INSERT INTO ai_pending_actions (id, tenant_id, conversation_id, user_id, operation_id, method, path, "
                   "  body, title, expires_at) VALUES (:a,:t,:c,:u,'x','POST','/api/v1/sites',:b,'Add site',:e)",
                   {"a": aid, "t": w["tenant"], "c": cid, "u": w["users"][ADMIN], "b": json.dumps({"name": "Q"}),
                    "e": datetime.now(timezone.utc) + timedelta(minutes=10)})
        early = await c.post(f"/api/v1/ai/assistant/actions/{aid}/confirm", headers=w["h"][ADMIN])
    assert early.status_code == 409 and "finish its reply" in early.json()["detail"]


@pytest.mark.asyncio
async def test_what_is_never_offered_is_refused_at_confirm_too(scripted):
    """A card is checked again when confirmed, in case it was stored some other way."""
    w = await _assistant_world()
    scripted([{"text": "Hello."}])
    async with _client() as c:
        _, events = await _chat(c, w, ADMIN, "Hello")
        cid = uuid.UUID(events[0][1]["conversation_id"])
        aid = uuid.uuid4()
        await _sql("INSERT INTO ai_pending_actions (id, tenant_id, conversation_id, user_id, operation_id, method, path, "
                   "  body, title, expires_at, created_at) VALUES (:a,:t,:c,:u,:op,'PUT',:p,'{}','Finalise',:e, "
                   "  now() - interval '1 hour')",
                   {"a": aid, "t": w["tenant"], "c": cid, "u": w["users"][ADMIN], "op": "finalize_run",
                    "p": "/api/v1/payroll/runs/abc/finalize", "e": datetime.now(timezone.utc) + timedelta(minutes=10)})
        r = await c.post(f"/api/v1/ai/assistant/actions/{aid}/confirm", headers=w["h"][ADMIN])
    assert r.status_code == 200 and r.json()["status"] == "FAILED"
    assert "Not sent" in r.json()["result"]["answer"]


# ─── C. Passwords and roles ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_new_staff_members_password_is_made_by_the_app_and_shown_once_to_the_person(scripted):
    w = await _assistant_world()
    email = f"new-{uuid.uuid4().hex[:8]}@ai.test"
    model = scripted([{"tool": "use_function", "args": {"operation_id": _op("POST", "/api/v1/users"), "body": {
        "email": email, "role_id": 5, "full_name": "Ravi Kumar", "password": "model-invented-1"}}},
        {"text": "Prepared."}])
    async with _client() as c:
        _, events = await _chat(c, w, ADMIN, "Add Ravi Kumar as a guard")
        card = _confirm_card(events)
        ok = await c.post(f"/api/v1/ai/assistant/actions/{card['id']}/confirm", headers=w["h"][ADMIN])
    note = json.loads(model.results[0][0])["note"]
    assert "never mention or invent a password" in note
    shown = {f["name"]: f["value"] for f in card["fields"]}
    assert shown["password"] == "a temporary password, shown to you once after Confirm"
    assert ok.status_code == 200 and ok.json()["status"] == "CONFIRMED"
    temp = ok.json()["temporary_password"]
    assert temp and temp != "model-invented-1" and len(temp) >= 16
    stored = await _sql("SELECT body FROM ai_pending_actions WHERE id = :a", {"a": uuid.UUID(card["id"])})
    assert stored[0]["body"]["password"] == "[given once to the person]"
    user = await _sql("SELECT role_id, full_name FROM users WHERE email = :e", {"e": email})
    assert user and user[0]["role_id"] == 5


@pytest.mark.asyncio
async def test_changing_someones_role_stays_manual(scripted):
    w = await _assistant_world()
    model = scripted([{"tool": "use_function", "args": {
        "operation_id": _op("PUT", "/api/v1/users/{user_id}"),
        "path_params": {"user_id": str(w["users"][SUPERVISOR])}, "body": {"role_id": 2}}}, {"text": "No."}])
    async with _client() as c:
        _, events = await _chat(c, w, ADMIN, "Make the supervisor an admin")
    assert model.results[0][1] and "changing someone's role" in model.results[0][0]
    assert not [k for k, _ in events if k == "confirm"]
