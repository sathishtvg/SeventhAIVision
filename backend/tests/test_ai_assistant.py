"""The AI module, phase 6: the assistant reads the app as the person, streams, and keeps its own record.

A scripted model stands in for Claude and the built-in model: what is tested is
everything around the model - the gates, the tools reaching the app as the
person, what is never offered, the masking, the limit, the record.

Sections:
  A — What it may reach, without a database (4 tests)
  B — Who may talk to it (1 test)
  C — A conversation (5 tests)
  D — The record (2 tests)
"""
from __future__ import annotations

import json
import re
import uuid

import pytest

from app.main import app
from app.services import ai_chat, ai_functions
from tests.test_ai_learning import ADMIN, GUARD, MANAGER, SUPERVISOR, _client, _run, _sql, _world


class Scripted:
    """Plays a fixed script: tool calls through the real tool box, then words."""

    def __init__(self, steps: list[dict], model: str = "scripted-1"):
        self.steps, self.model, self.seen, self.results = steps, model, [], []

    async def turn(self, *, system, history, user_text, tools, emit):
        self.seen.append({"system": system, "history": history, "user": user_text})
        messages, used, words = [{"role": "user", "content": user_text}], [], []
        for step in self.steps:
            if "tool" in step:
                await emit("status", {"text": tools.describe(step["tool"], step["args"])})
                content, error = await tools.call(step["tool"], step["args"])
                self.results.append((content, error))
                messages.append({"role": "tool", "content": content})
                used.append({"tool": step["tool"], "what": "x"})
            else:
                await emit("delta", {"text": step["text"]})
                words.append(step["text"])
                messages.append({"role": "assistant", "content": step["text"]})
        return ai_chat.TurnResult(text="".join(words), messages=messages, used=used, tokens_in=11, tokens_out=7)


@pytest.fixture
def scripted(monkeypatch):
    def use(steps, model="scripted-1"):
        model_ = Scripted(steps, model)
        monkeypatch.setattr(ai_chat, "OVERRIDE", model_)
        return model_
    return use


def _op(method: str, path: str) -> str:
    return next(f.operation_id for f in ai_functions.catalogue().values() if f.method == method and f.path == path)


def _events(body: str) -> list[tuple[str, dict]]:
    return [(m.group(1), json.loads(m.group(2))) for m in re.finditer(r"event: (\w+)\ndata: (.*)\n\n", body)]


async def _assistant_world(*, provider="LOCAL", limit=None, roles=(2, 3, 8)):
    w = await _world(features=("assistant",), enable=())
    await _run([
        ("UPDATE ai_module_licenses SET assistant_provider = :p, monthly_reply_limit = :l WHERE tenant_id = :t",
         {"p": provider, "l": limit, "t": w["tenant"]}),
        ("INSERT INTO ai_feature_settings (tenant_id, feature_key, enabled, role_ids) "
         "VALUES (:t, 'assistant', TRUE, CAST(:r AS smallint[]))", {"t": w["tenant"], "r": list(roles)}),
    ])
    return w


async def _chat(c, w, role, message, conversation_id=None):
    r = await c.post("/api/v1/ai/assistant/chat", headers=w["h"][role],
                     json={"message": message, **({"conversation_id": conversation_id} if conversation_id else {})})
    return r, (_events(r.text) if r.status_code == 200 else [])


# ─── A. What it may reach ────────────────────────────────────────────────────

def test_every_never_offered_rule_still_matches_a_real_route():
    """A rule that matches nothing has rotted: the route was renamed and the rule protects nothing."""
    spec = app.openapi()["paths"]
    routes = [(m.upper(), p) for p, item in spec.items() for m in item if m in ("get", "post", "put", "patch", "delete")]
    for methods, pattern, why in ai_functions.NEVER_OFFERED:
        assert any(("*" in methods or m in methods) and pattern.search(p) for m, p in routes), why


def test_the_catalogue_holds_none_of_the_never_offered_functions():
    cat = ai_functions.catalogue()
    assert len(cat) > 800, "the whole app should be reachable"
    paths = {(f.method, f.path) for f in cat.values()}
    for gone in [("PUT", "/api/v1/payroll/runs/{run_id}/finalize"), ("DELETE", "/api/v1/2fa"),
                 ("DELETE", "/api/v1/tenants/{tenant_id}"), ("PUT", "/api/v1/roles/{role_id}"),
                 ("POST", "/api/v1/auth/login"), ("GET", "/api/v1/platform/tenants"),
                 ("PUT", "/api/v1/ai/settings/{feature_key}"), ("POST", "/api/v1/pdpa/dsar/{dsar_id}/execute-erasure")]:
        assert gone not in paths, gone
    assert ("GET", "/api/v1/payroll/runs") in paths or any(p.startswith("/api/v1/payroll") for _, p in paths)
    assert ai_functions.never_offered("PUT", "/api/v1/users/x", {"role_id": 2}) == "changing someone's role"
    assert ai_functions.never_offered("PUT", "/api/v1/users/x", {"phone": "9"}) is None
    assert ai_functions.never_offered("DELETE", "/api/v1/evidence-packages/a/items/b") is not None


def test_a_search_offers_only_what_the_role_holds():
    held = {"site:read", "shift:read"}
    found = ai_functions.find("list sites", held)
    assert found and all(not ai_functions.catalogue()[f["operation_id"]].permissions - held for f in found)
    assert not [f for f in ai_functions.find("payroll runs", held) if "/payroll" in f["path"]]


def test_identity_and_bank_numbers_are_shortened_for_the_hosted_model():
    data = {"full_name": "Tan Ah Kow", "nric_fin": "S1234567D", "bank_account_number": "123-456789-0",
            "notes": "Guard S7654321A covered", "items": [{"fin_number": "G1234567X"}]}
    m = ai_functions.mask(data)
    assert m["nric_fin"] == "*****567D" and m["bank_account_number"] == "********89-0"
    assert m["notes"] == "Guard *****321A covered" and m["items"][0]["fin_number"] == "*****567X"
    assert m["full_name"] == "Tan Ah Kow"


# ─── B. Who may talk to it ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_only_the_roles_chosen_may_talk_to_it_and_never_without_the_licence(scripted):
    scripted([{"text": "Hello."}])
    w = await _assistant_world(roles=(2, 3))
    bare = await _world()
    async with _client() as c:
        admin, _ = await _chat(c, w, ADMIN, "Hi")
        guard, _ = await _chat(c, w, GUARD, "Hi")
        manager, _ = await _chat(c, w, MANAGER, "Hi")
        unlicensed, _ = await _chat(c, bare, ADMIN, "Hi")
    assert admin.status_code == 200
    assert guard.status_code == 403 and "not switched on for your role" in guard.json()["detail"]
    assert manager.status_code == 403
    assert unlicensed.status_code == 403


# ─── C. A conversation ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_it_reads_the_app_as_the_person_and_the_reply_streams(scripted):
    other = await _world()  # another organisation's sites must never appear
    w = await _assistant_world()
    model = scripted([{"tool": "find_functions", "args": {"query": "list sites"}},
                      {"tool": "use_function", "args": {"operation_id": _op("GET", "/api/v1/sites")}},
                      {"text": "You have two sites: "}, {"text": "Jurong and Tuas."}])
    async with _client() as c:
        r, events = await _chat(c, w, ADMIN, "Which sites do we have?")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    kinds = [k for k, _ in events]
    assert kinds[0] == "conversation" and "status" in kinds and kinds.count("delta") == 2 and kinds[-1] == "done"
    found, read = model.results
    assert not found[1] and any(f["path"] == "/api/v1/sites" for f in json.loads(found[0])["found"])
    assert not read[1] and "Jurong" in read[0] and "Tuas" in read[0]
    # The other organisation has two sites of its own with the same names: only these two are returned.
    sites = json.loads(read[0])
    assert {s["id"] for s in sites} == {str(w["site_a"]), str(w["site_b"])}, "another organisation's sites came back"
    assert other["site_a"] not in {s["id"] for s in sites}
    done = events[-1][1]
    assert done["text"] == "You have two sites: Jurong and Tuas."
    assert "Reply in English" in model.seen[0]["system"] and "Role 2" in model.seen[0]["system"]


@pytest.mark.asyncio
async def test_what_stays_manual_is_refused_and_changes_wait_for_the_next_release(scripted):
    w = await _assistant_world()
    model = scripted([
        {"tool": "use_function", "args": {"operation_id": "payroll_finalize_anything"}},
        {"tool": "use_function", "args": {"operation_id": _op("POST", "/api/v1/sites"), "body": {"name": "New"}}},
        {"tool": "use_function", "args": {"operation_id": _op("GET", "/api/v1/sites/{site_id}")}},
        {"text": "Done."}])
    async with _client() as c:
        await _chat(c, w, ADMIN, "Finalise payroll and add a site")
    missing, write, no_param = model.results
    assert missing[1] and "No such function" in missing[0]
    assert write[1] and "Nothing was changed" in write[0]
    assert no_param[1] and "path parameters: site_id" in no_param[0]
    sites = await _sql("SELECT count(*) AS n FROM sites WHERE tenant_id = :t", {"t": w["tenant"]})
    assert sites[0]["n"] == 2, "a site was created"


@pytest.mark.asyncio
async def test_the_role_s_own_limits_hold_inside_the_assistant(scripted):
    w = await _assistant_world(roles=(2, 3, 5))
    payroll = next(f for f in ai_functions.catalogue().values()
                   if f.method == "GET" and "{" not in f.path and "payroll:manage" in f.permissions)
    model = scripted([{"tool": "use_function", "args": {"operation_id": payroll.operation_id}}, {"text": "No."}])
    async with _client() as c:
        await _chat(c, w, GUARD, "Show me payroll")
    assert model.results[0][1] and "role cannot use" in model.results[0][0]


@pytest.mark.asyncio
async def test_the_hosted_model_is_sent_shortened_identity_numbers(scripted):
    w = await _assistant_world(provider="HOSTED", limit=100)
    await _sql("UPDATE users SET nric_fin = 'S1234567D' WHERE id = :u", {"u": w["users"][SUPERVISOR]})
    model = scripted([{"tool": "use_function", "args": {"operation_id": _op("GET", "/api/v1/users/{user_id}"),
                                                        "path_params": {"user_id": str(w["users"][SUPERVISOR])}}},
                      {"text": "Found."}])
    async with _client() as c:
        r, _ = await _chat(c, w, ADMIN, "Who is the supervisor?")
    assert r.status_code == 200
    content, error = model.results[0]
    assert not error and "S1234567D" not in content and "*****567D" in content


@pytest.mark.asyncio
async def test_at_the_monthly_limit_it_says_so(scripted):
    scripted([{"text": "One."}])
    w = await _assistant_world(provider="HOSTED", limit=1)
    async with _client() as c:
        first, _ = await _chat(c, w, ADMIN, "One")
        second, _ = await _chat(c, w, ADMIN, "Two")
        usage = (await c.get("/api/v1/ai/assistant/usage", headers=w["h"][ADMIN])).json()
    assert first.status_code == 200
    assert second.status_code == 429 and "1 assistant replies have been used" in second.json()["detail"]
    assert usage["used"] == 1 and usage["limit"] == 1 and usage["left"] == 0


# ─── D. The record ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_next_message_carries_the_conversation_and_only_the_person_reads_it(scripted):
    w = await _assistant_world()
    model = scripted([{"text": "First answer."}])
    async with _client() as c:
        _, events = await _chat(c, w, ADMIN, "First question")
        cid = events[0][1]["conversation_id"]
        _, second = await _chat(c, w, ADMIN, "Second question", cid)
        mine = (await c.get("/api/v1/ai/assistant/conversations", headers=w["h"][ADMIN])).json()
        read = (await c.get(f"/api/v1/ai/assistant/conversations/{cid}", headers=w["h"][ADMIN])).json()
        theirs = await c.get(f"/api/v1/ai/assistant/conversations/{cid}", headers=w["h"][SUPERVISOR])
        sneaky, _ = await _chat(c, w, SUPERVISOR, "Continue", cid)
    assert model.seen[1]["history"] == [{"role": "user", "content": "First question"},
                                        {"role": "assistant", "content": "First answer."}]
    assert [x["id"] for x in mine["items"]] == [cid] and mine["items"][0]["title"] == "First question"
    assert [(m["role"], m["text"]) for m in read["messages"]] == [
        ("user", "First question"), ("assistant", "First answer."),
        ("user", "Second question"), ("assistant", "First answer.")]
    assert theirs.status_code == 404 and sneaky.status_code == 404
    stored = await _sql("SELECT role, tokens_in, tokens_out FROM ai_messages WHERE conversation_id = :c "
                        "ORDER BY created_at", {"c": uuid.UUID(cid)})
    assert [s["role"] for s in stored] == ["user", "assistant", "user", "assistant"]
    assert stored[1]["tokens_in"] == 11 and stored[1]["tokens_out"] == 7


@pytest.mark.asyncio
async def test_after_the_model_is_switched_the_conversation_starts_its_context_again(scripted):
    w = await _assistant_world()
    scripted([{"text": "On the free model."}], model="free-1")
    async with _client() as c:
        _, events = await _chat(c, w, ADMIN, "Hello")
        cid = events[0][1]["conversation_id"]
        await _sql("UPDATE ai_module_licenses SET assistant_provider = 'HOSTED', monthly_reply_limit = 50 "
                   "WHERE tenant_id = :t", {"t": w["tenant"]})
        hosted = scripted([{"text": "On the hosted model."}], model="hosted-1")
        _, after = await _chat(c, w, ADMIN, "Hello again", cid)
    assert ("status", {"text": "The chat model was changed; earlier messages are not carried over."}) in after
    assert hosted.seen[0]["history"] == []
