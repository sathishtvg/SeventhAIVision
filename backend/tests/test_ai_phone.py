"""The AI module, phase 9: the assistant on the phone, for guards.

The phone asks for the reply whole (`stream: false`): React Native cannot read a
stream as it arrives. Everything else is the web's: the gates, the Confirm cards,
the record.

Sections:
  A — The whole reply (3 tests)
  B — Guards (1 test)
"""
from __future__ import annotations

import pytest

from app.main import app  # noqa: F401
from app.services import ai_chat
from tests.test_ai_assistant import _assistant_world, _op, scripted  # noqa: F401  (the fixture)
from tests.test_ai_learning import ADMIN, GUARD, SUPERVISOR, _client, _sql


async def _ask(c, w, role, message, conversation_id=None):
    return await c.post("/api/v1/ai/assistant/chat", headers=w["h"][role],
                        json={"message": message, "channel": "PHONE", "stream": False,
                              **({"conversation_id": conversation_id} if conversation_id else {})})


# ─── A. The whole reply ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_phone_gets_the_reply_whole_and_the_conversation_is_kept_as_a_phone_one(scripted):
    w = await _assistant_world()
    model = scripted([{"tool": "find_functions", "args": {"query": "sites"}}, {"text": "You have no sites yet."}])
    async with _client() as c:
        r = await _ask(c, w, SUPERVISOR, "Which sites do I have?")
        again = await _ask(c, w, SUPERVISOR, "And tomorrow?", r.json()["conversation_id"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json"), r.text
    body = r.json()
    assert body["text"] == "You have no sites yet." and body["stop"] == "end" and body["actions"] == []
    assert len(body["statuses"]) == 1 and body["message_id"]
    assert again.status_code == 200 and again.json()["conversation_id"] == body["conversation_id"]
    assert len(model.seen[1]["history"]) > 0, "the second message carried the first"
    kept = await _sql("SELECT channel FROM ai_conversations WHERE id = CAST(:c AS uuid)", {"c": body["conversation_id"]})
    assert kept[0]["channel"] == "PHONE"


@pytest.mark.asyncio
async def test_a_change_asked_for_on_the_phone_comes_back_as_a_confirm_card(scripted):
    w = await _assistant_world()
    scripted([{"tool": "use_function", "args": {"operation_id": _op("POST", "/api/v1/sites"),
                                                "body": {"name": "Harbour Gate"}}},
              {"text": "Confirm it on the card."}])
    async with _client() as c:
        r = await _ask(c, w, ADMIN, "Add a site called Harbour Gate at 1 Harbour Rd")
        card = r.json()["actions"][0]
        ok = await c.post(f"/api/v1/ai/assistant/actions/{card['id']}/confirm", headers=w["h"][ADMIN])
    assert r.status_code == 200 and card["status"] == "PENDING" and card["title"].endswith("Harbour Gate")
    assert ok.status_code == 200 and ok.json()["status"] == "CONFIRMED", ok.text
    assert await _sql("SELECT 1 FROM sites WHERE tenant_id = :t AND name = 'Harbour Gate'", {"t": w["tenant"]})


@pytest.mark.asyncio
async def test_when_the_model_is_not_running_the_phone_is_told_in_words(monkeypatch):
    class Down:
        model = "down-1"

        async def turn(self, **_):
            raise ai_chat.AssistantUnavailable("The built-in assistant is not running on this server yet.")

    monkeypatch.setattr(ai_chat, "OVERRIDE", Down())
    w = await _assistant_world()
    async with _client() as c:
        r = await _ask(c, w, ADMIN, "Hello")
    assert r.status_code == 503 and r.json()["detail"] == "The built-in assistant is not running on this server yet."


# ─── B. Guards ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_guard_uses_it_when_the_admin_chose_guards_and_not_otherwise(scripted):
    scripted([{"text": "Your next shift starts at 22:00."}])
    chosen = await _assistant_world(roles=(2, 5))
    not_chosen = await _assistant_world(roles=(2, 3))
    async with _client() as c:
        yes = await _ask(c, chosen, GUARD, "Bila shift saya seterusnya?")
        no = await _ask(c, not_chosen, GUARD, "Bila shift saya seterusnya?")
    assert yes.status_code == 200 and yes.json()["text"] == "Your next shift starts at 22:00."
    assert no.status_code == 403 and "not switched on for your role" in no.json()["detail"]
