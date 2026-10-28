"""The AI module: the search that finds a function, and how the small free model is helped to use one.

Tried on 28 October 2026 with a 1.5B model, the assistant's two-step tools failed: the
model skipped the search, or searched and then invented the answer. These hold what
was built so a small model can do the job: a search that finds everyday things in
English, Malay, Tamil and Chinese; the few functions that fit handed over as tools
of their own; the read made for it when it only says "I will check"; and nothing it
never read passed off as an answer. A fake Ollama plays the model.

Sections:
  A — The search (4 tests)
  B — The free model's tools and words (6 tests)
  C — A turn of the free model (5 tests)
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.main import app  # noqa: F401
from app.services import ai_chat, ai_functions
from tests.test_ai_learning import _sql


async def _held(role: int) -> set[str]:
    rows = await _sql("SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
                      "WHERE rp.role_id = :r", {"r": role})
    return {r["code"] for r in rows}


def _paths(found, n=3) -> list[tuple[str, str]]:
    return [(f.method, f.path) for f in found[:n]]


# ─── A. The search ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_everyday_questions_find_the_function_that_answers_them():
    guard, admin = await _held(5), await _held(2)
    assert ("GET", "/api/v1/shifts") in _paths(ai_functions.ranked("When is my next shift?", guard))
    assert _paths(ai_functions.ranked("How many leave days do I have left?", guard), 1) == [
        ("GET", "/api/v1/leave/balances")]
    assert _paths(ai_functions.ranked("Which patrols do I have today?", guard), 1) == [
        ("GET", "/api/v1/virtual-patrol/my-patrols")]
    assert _paths(ai_functions.ranked("I want to apply for leave on Friday", guard), 1) == [
        ("POST", "/api/v1/leave/requests")]
    assert _paths(ai_functions.ranked("Who is late today?", admin), 1) == [("GET", "/api/v1/attendance/live")]
    assert _paths(ai_functions.ranked("Create a site called Harbour Gate", admin), 1) == [("POST", "/api/v1/sites")]
    assert ai_functions.ranked("What is the weather tomorrow?", guard) == []


@pytest.mark.asyncio
async def test_malay_tamil_and_chinese_questions_find_it_too():
    guard = await _held(5)
    assert _paths(ai_functions.ranked("Berapa baki cuti saya?", guard), 1) == [("GET", "/api/v1/leave/balances")]
    assert ("GET", "/api/v1/shifts") in _paths(ai_functions.ranked("Bila shift saya seterusnya?", guard))
    assert ("GET", "/api/v1/shifts") in _paths(ai_functions.ranked("என் அடுத்த ஷிப்ட் எப்போது?", guard))
    assert ("GET", "/api/v1/shifts") in _paths(ai_functions.ranked("我的下一个班是什么时候？", guard))


@pytest.mark.asyncio
async def test_a_request_finds_a_change_and_my_finds_my_own():
    guard = await _held(5)
    report = ai_chat.local_picks("Report an incident: gate 2 was left open, low severity", guard)
    assert _paths(report, 1) == [("POST", "/api/v1/incidents")]
    assert all(not f.path_params for f in report), "no function needing an id nobody gave"
    with_id = ai_chat.local_picks("Add a note to incident 3f2b8a1c-1d2e-4f50-9a6b-7c8d9e0f1a2b: gate shut", guard, 8)
    assert any(f.path_params for f in with_id), "given an id, a function on that record may be offered"
    face_first = _paths(ai_functions.ranked("When is my next shift?", guard), 1)
    assert face_first != [("GET", "/api/v1/faces/me/checks")], "'my' prefers what is about shifts"


def test_the_live_attendance_board_is_offered_and_a_camera_stream_is_not():
    paths = {(f.method, f.path) for f in ai_functions.catalogue().values()}
    assert ("GET", "/api/v1/attendance/live") in paths
    assert not [p for _, p in paths if p.endswith("/streams/{stream_id}/live")]


# ─── B. The free model's tools and words ─────────────────────────────────────

def test_the_reply_language_is_told_from_the_message():
    assert ai_chat.language_of("என் அடுத்த ஷிப்ட் எப்போது?") == "Tamil"
    assert ai_chat.language_of("我的下一个班是什么时候？") == "Chinese (Simplified)"
    assert ai_chat.language_of("Bila shift saya seterusnya?") == "Malay"
    assert ai_chat.language_of("When is my next shift?") == "English"
    assert ai_chat.language_of("12345 ?") is None


def test_the_free_model_gets_a_short_prompt_in_the_language_of_the_message():
    from datetime import datetime

    full = ai_chat.system_prompt(name="Ravi", role="Security Guard", organisation="Demo", locale="en",
                                 tz="Asia/Singapore", now=datetime(2026, 10, 28), can_change=True)
    short = ai_chat.local_system(full, "Berapa baki cuti saya?")
    assert short.startswith(ai_chat.LOCAL_RULES) and "find_functions" not in short
    assert "You are talking to Ravi (Security Guard) of Demo." in short and "Asia/Singapore" in short
    assert short.endswith("Reply in Malay.") and "when that is unclear" not in short
    assert ai_chat.local_system(full, "12345").endswith("Reply in English."), "the account language, when unclear"


def test_a_function_becomes_a_tool_with_flat_parameters_and_no_guessable_filters():
    cat = ai_functions.catalogue()
    shifts = next(f for f in cat.values() if (f.method, f.path) == ("GET", "/api/v1/shifts"))
    tool = ai_chat.local_tool(shifts, "list_shifts")["function"]
    assert tool["name"] == "list_shifts" and "guard_user_id" not in tool["parameters"]["properties"]
    create = next(f for f in cat.values() if (f.method, f.path) == ("POST", "/api/v1/incidents"))
    tool = ai_chat.local_tool(create, "create_incident")["function"]
    assert "title" in tool["parameters"]["required"] and "confirms it on a card" in tool["description"]
    user = next(f for f in cat.values() if (f.method, f.path) == ("POST", "/api/v1/users"))
    assert not [n for n in ai_chat.local_tool(user, "create_user")["function"]["parameters"]["properties"]
                if "password" in n]


def test_flat_arguments_are_put_where_the_function_takes_them():
    cat = ai_functions.catalogue()
    note = next(f for f in cat.values() if (f.method, f.path) == ("POST", "/api/v1/incidents/{incident_id}/notes"))
    assert ai_chat.local_call(note, {"incident_id": "i1", "note": "Shut", "made_up": 1}) == {
        "operation_id": note.operation_id, "path_params": {"incident_id": "i1"}, "query": {}, "body": {"note": "Shut"}}
    shifts = next(f for f in cat.values() if (f.method, f.path) == ("GET", "/api/v1/shifts"))
    assert ai_chat.local_call(shifts, {"guard_user_id": "Ravi Kumar", "shift_status": "confirmed"})["query"] == {}
    create = next(f for f in cat.values() if (f.method, f.path) == ("POST", "/api/v1/incidents"))
    made = ai_chat.local_call(create, {"description": "Gate 2 was left open. Found at 22:10.", "severity": "low",
                                       "camera_id": ""})
    assert made["body"] == {"description": "Gate 2 was left open. Found at 22:10.", "severity": "low",
                            "title": "Gate 2 was left open."}, "the title from the person's own words; no empties"
    assert ai_chat.local_call(create, {"title": "Gate", "description": "x"})["body"]["title"] == "Gate"
    site = next(f for f in cat.values() if (f.method, f.path) == ("POST", "/api/v1/sites"))
    assert ai_chat.local_call(site, {}, "Create a site called Harbour Gate")["body"]["name"] == "Harbour Gate"
    assert ai_chat.local_call(site, {"name": "Changi"}, "a site called Harbour Gate")["body"]["name"] == "Changi"
    assert "name" not in ai_chat.local_call(site, {}, "Create a site")["body"], "no name said, none made up"


def test_times_are_written_out_in_the_organisations_zone_before_the_model_sees_them():
    text = '{"start": "2026-10-28T22:00:00+08:00", "end": "2026-10-28T23:00:00Z", "day": "2026-10-28", "x": "a"}'
    out = ai_chat.readable_times(text, "Asia/Singapore")
    assert '"start": "Wed 28 Oct 2026, 22:00"' in out and '"end": "Thu 29 Oct 2026, 07:00"' in out
    assert '"day": "2026-10-28"' in out, "a date alone is left as it is"
    assert ai_chat.readable_times("2026-10-28T14:00:00", None) == "Wed 28 Oct 2026, 22:00", "no zone given: UTC"


def test_a_tool_call_written_out_as_words_is_found():
    text = '好的。\n{"name": "list_shifts", "arguments": {"site_id": "x"}} and {"name": "other", "arguments": {}}'
    assert ai_chat.calls_in_text(text, {"list_shifts": None}) == [
        {"function": {"name": "list_shifts", "arguments": {"site_id": "x"}}}]
    assert ai_chat.calls_in_text("no call {here", {"list_shifts": None}) == []


# ─── C. A turn of the free model ─────────────────────────────────────────────

SHIFTS = {"items": [{"site_name": "Jurong Logistics Hub", "start_time": "2026-10-28T22:00:00+08:00"}]}


class FakeOllama:
    """Answers /api/chat from a script, one reply per request, and keeps what it was sent."""

    def __init__(self, replies: list[dict]):
        self.replies, self.sent = list(replies), []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(json.loads(request.content))
        r = self.replies.pop(0)
        msg = {"role": "assistant", "content": r.get("text", ""), **({"tool_calls": r["calls"]} if "calls" in r else {})}
        lines = [{"message": msg, "done": False}, {"message": {"content": ""}, "done": True,
                                                   "prompt_eval_count": 5, "eval_count": 3}]
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())


@pytest.fixture
def ollama(monkeypatch):
    def use(replies):
        fake = FakeOllama(replies)
        real = httpx.AsyncClient
        monkeypatch.setattr(ai_chat.httpx, "AsyncClient",
                            lambda **kw: real(transport=httpx.MockTransport(fake.handler), **kw))
        return fake
    return use


@pytest.fixture
def app_calls(monkeypatch):
    calls = []

    async def fake_app(method, path, *, token, client_ip, query=None, body=None):
        calls.append((method, path, query))
        return 200, SHIFTS if path == "/api/v1/shifts" else {"items": []}
    monkeypatch.setattr(ai_chat, "call_app", fake_app)
    return calls


async def _turn(question: str, held: set[str], cards: list | None = None):
    async def on_change(f, path, path_values, query, change):
        cards.append((f.method, path, change))
        return json.dumps({"prepared": True}), False

    async def emit(kind, data):
        pass

    tools = ai_chat.ToolBox(token="t", held=held, client_ip="127.0.0.1", mask_results=False,
                            on_change=on_change if cards is not None else None)
    system = "RULES\n\nYou are talking to Ravi (Security Guard) of Demo. Reply in the language the person writes " \
             "in; when that is unclear, in English. The organisation's time zone is Asia/Singapore; today is x."
    return await ai_chat.LocalOllama(url="http://ollama:11434", model="small").turn(
        system=system, history=[], user_text=question, tools=tools, emit=emit)


@pytest.mark.asyncio
async def test_a_question_is_read_first_and_the_model_writes_the_answer_from_it(ollama, app_calls):
    fake = ollama([{"text": "Your next shift is at Jurong Logistics Hub at 22:00."}])
    r = await _turn("When is my next shift?", await _held(5))
    assert [c[:2] for c in app_calls] == [("GET", "/api/v1/shifts")], "read before the model was asked"
    assert r.text == "Your next shift is at Jurong Logistics Hub at 22:00."
    first = fake.sent[0]
    assert first["options"] == {"temperature": 0, "num_predict": 600, "repeat_penalty": 1.2}
    assert first["messages"][0]["content"].endswith("Reply in English.")
    assert [t["function"]["name"] for t in first["tools"]][0] == "list_shifts"
    assert first["messages"][-1]["role"] == "tool" and "Jurong" in first["messages"][-1]["content"]
    assert first["messages"][-2]["tool_calls"][0]["function"]["name"] == "list_shifts"


@pytest.mark.asyncio
async def test_a_request_it_did_nothing_about_gets_one_reminder(ollama, app_calls):
    cards: list = []
    fake = ollama([{"text": "Sure, I will report it. Please wait."},
                   {"calls": [{"function": {"name": "create_incident", "arguments": {"title": "Gate 2 left open"}}}]},
                   {"text": "It is prepared: confirm it on the card."}])
    r = await _turn("Report an incident: gate 2 was left open", await _held(5), cards)
    assert app_calls == [], "a request is not read first"
    assert [(m, p) for m, p, _ in cards] == [("POST", "/api/v1/incidents")]
    assert fake.sent[1]["messages"][-1] == {"role": "user", "content": "Use the tool create_incident now, with the "
                                                                       "details I gave. Do not ask me to wait."}
    assert r.text == "It is prepared: confirm it on the card."


@pytest.mark.asyncio
async def test_a_call_written_as_words_is_made(ollama, app_calls):
    ollama([{"text": '好的，让我查一下。{"name": "list_shifts", "arguments": {"guard_user_id": "Ravi Kumar"}}'},
            {"text": "你的下一个班在 Jurong Logistics Hub。"}])
    r = await _turn("我的下一个班是什么时候？", await _held(5))
    assert app_calls == [("GET", "/api/v1/shifts", {})] * 2, "read first, then as it asked - its filter not sent"
    assert r.text == "你的下一个班在 Jurong Logistics Hub。"


@pytest.mark.asyncio
async def test_records_it_never_read_are_not_passed_off_as_an_answer(ollama, app_calls):
    ollama([{"text": "Here are your incidents: [IncidentID1] [IncidentDescription1]"}])
    r = await _turn("What is the weather tomorrow?", await _held(5))
    assert app_calls == [] and r.text == "I could not read that from the app. Please ask me again in other words."


@pytest.mark.asyncio
async def test_a_change_is_prepared_and_an_empty_answer_says_so(ollama, app_calls):
    cards: list = []
    ollama([{"calls": [{"function": {"name": "create_incident", "arguments": {
        "title": "Gate 2 left open", "severity": "low", "invented_field": "x"}}}]}, {"text": ""}])
    r = await _turn("Report an incident: gate 2 was left open, low severity", await _held(5), cards)
    assert [(m, p) for m, p, _ in cards] == [("POST", "/api/v1/incidents")]
    assert cards[0][2] == {"title": "Gate 2 left open", "severity": "low"}
    assert r.text == "I have prepared that for you: check the card and press Confirm."
