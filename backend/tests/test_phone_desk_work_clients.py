"""The phone's newer screens call an API that exists, and read what it sends.

The phone is not compiled against the server. A path renamed on one side is a
button that does nothing on the other, and a field the phone reads that the
server never sent is a blank line on somebody's screen. Four clients brought
the newer work to the phone — a person's own reading, the published briefings,
the work orders given to them and their tasks on a case — and this holds each
to the server as it is.

  A — Every call is an operation the API serves, with that method; and the
      phone makes these calls and no other: none of the desk's steps
  B — The filters and the bodies are ones the API takes
  C — Each row of the phone's menu asks for the permission its list asks for,
      and each screen is registered
  D — Every field the phone reads of a record is in what the server sends,
      asked of the API as the person the screen is for

Read from the working tree, so it runs with the repository-inspection suites.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.services import daily_briefing, workforce_readings
from app.services import maintenance as maintenance_work
from tests._repo import REPO_ROOT, requires_repo_tree
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, _client, _world
from tests.test_drone_clients import _served, client_calls
from tests.test_expansion_hardening import routes

pytestmark = requires_repo_tree

MOBILE = REPO_ROOT / "mobile" / "src"
CLIENTS = {name: MOBILE / "api" / f"{name}.ts" for name in ("workforce", "briefings", "maintenance", "cases")}
MENU = MOBILE / "screens" / "MoreMenuScreen.tsx"
NAVIGATION = MOBILE / "navigation" / "index.tsx"

READING = "/api/v1/workforce/me"
BRIEFINGS = "/api/v1/daily-briefings"
ORDERS = "/api/v1/maintenance/work-orders"
CASES = "/api/v1/cases"
#: Everything the phone does with the newer work. What is not here is done at a desk.
CALLS = {
    "workforce": {("GET", READING)},
    "briefings": {("GET", BRIEFINGS), ("GET", f"{BRIEFINGS}/{{}}")},
    "maintenance": {("GET", ORDERS), ("POST", f"{ORDERS}/{{}}/start"), ("POST", f"{ORDERS}/{{}}/complete")},
    "cases": {("GET", CASES), ("GET", f"{CASES}/{{}}"), ("POST", f"{CASES}/{{}}/tasks/{{}}/{{}}")},
}
#: Each row of the menu: its screen, the file it is in, and the list it opens on.
ROWS = {
    "MyReading": ("MyReadingScreen", READING),
    "Briefings": ("BriefingsScreen", BRIEFINGS),
    "MyWorkOrders": ("MyWorkOrdersScreen", ORDERS),
    "MyCaseTasks": ("MyCaseTasksScreen", CASES),
}


@pytest.fixture(scope="module")
def spec() -> dict:
    return app.openapi()


def _source(client: str) -> str:
    """A client file without its comments, which say things in words that look like fields."""
    return re.sub(r"/\*.*?\*/", "", CLIENTS[client].read_text(encoding="utf-8"), flags=re.S)


def _fields(client: str, name: str) -> set[str]:
    """What the phone must be sent of a record: the fields of an exported
    interface that are not optional. What is nested inside a field is not read."""
    src = _source(client)
    i = re.search(rf"export interface {name}\b[^{{]*\{{", src).end()
    depth, top = 1, []
    while depth:
        depth += (src[i] == "{") - (src[i] == "}")
        top.append(src[i] if depth == 1 else " ")
        i += 1
    return set(re.findall(r"(?:^|[;\n])\s*(\w+):", "".join(top)))


def _union(client: str, name: str) -> set[str]:
    """The values of an exported union of string literals."""
    return set(re.findall(r"'(\w+)'", re.search(rf"export type {name} = (.*)", _source(client)).group(1)))


async def _ok(response, *codes: int) -> dict:
    assert response.status_code in (codes or (200, 201)), response.text
    return response.json()


# ─── A. Every call is served, and there is no other ──────────────────────────

@pytest.mark.parametrize("client", sorted(CLIENTS))
def test_the_phone_makes_these_calls_and_no_other(client, spec):
    calls = set(client_calls(CLIENTS[client]))
    assert calls == CALLS[client], calls ^ CALLS[client]
    missing = sorted(f"{m} {p}" for m, p in calls if not _served(m, p, spec))
    assert not missing, f"{client}.ts calls operations the API does not serve: {missing}"


def test_a_task_is_finished_one_of_the_two_ways_the_api_has(spec):
    ways = set(re.findall(r"'(\w+)'", re.search(r"how: ([^,]+),", _source("cases")).group(1)))
    assert ways == {"done", "drop"}
    for way in ways:
        assert f"{CASES}/{{case_id}}/tasks/{{task_id}}/{way}" in spec["paths"], way


def test_the_phone_takes_none_of_the_desks_steps():
    # Raising, accepting, assigning and cancelling work; drafting and publishing a briefing; opening, closing and
    # staffing a case; answering what is recommended for a guard. None of them is a call the phone makes.
    said = " ".join(_source(c) for c in CLIENTS)
    for step in ("/accept", "/dismiss", "/cancel", "/publish", "/recount", "/discard", "/request-close", "/approve-close",
                 "/decline-close", "/reopen", "/investigators", "/lead", "/links", "/parties", "/notes", "/advice",
                 "/schedules", "/settings", "/guards"):
        assert step not in said, step
    assert not re.search(r"apiClient\s*\.\s*(put|patch|delete)\b", said)
    # A reading is one's own: there is no call for anybody else's.
    assert [p for _, p in client_calls(CLIENTS["workforce"])] == [READING]


# ─── B. Filters and bodies ───────────────────────────────────────────────────

def test_the_filters_the_phone_sends_are_ones_the_api_takes(spec):
    def taken(path: str) -> set[str]:
        return {p["name"] for p in spec["paths"][path]["get"]["parameters"]}

    for client, path, sent in (("workforce", READING, "days"), ("briefings", BRIEFINGS, "state"),
                               ("maintenance", ORDERS, "mine"), ("cases", CASES, "mine")):
        assert re.search(rf"params: \{{ {sent}\b", _source(client)), (client, sent)
        assert sent in taken(path), (path, sent)
    assert len(re.findall(r"params:", " ".join(_source(c) for c in CLIENTS))) == 4, "a filter this does not check"
    # The periods the phone offers are the ones a reading is counted over, and briefings are asked for as published.
    offered = re.search(r"READING_DAYS = \[([\d, ]+)\]", _source("workforce")).group(1)
    assert tuple(int(d) for d in offered.split(",")) == tuple(workforce_readings.PERIOD_DAYS)
    assert "state: 'PUBLISHED'" in _source("briefings") and "PUBLISHED" in daily_briefing.STATES


def test_the_bodies_the_phone_sends_have_only_fields_the_api_accepts(spec):
    def accepted(path: str) -> dict:
        ref = spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        return spec["components"]["schemas"][ref.rsplit("/", 1)[1]]

    done = accepted(f"{ORDERS}/{{order_id}}/complete")
    sent = set(re.findall(r"body\.(\w+) =", _source("maintenance"))) | set(
        re.findall(r"body: Record<string, unknown> = \{ (\w+):", _source("maintenance")))
    assert sent == {"completion_note", "parts_used", "downtime_minutes"}
    assert sent <= set(done["properties"]) and done["additionalProperties"] is False
    assert done["required"] == ["completion_note"]
    for way in ("done", "drop"):
        end = accepted(f"{CASES}/{{case_id}}/tasks/{{task_id}}/{way}")
        assert set(end["properties"]) == {"note"} and end["additionalProperties"] is False
    assert "{ note: note.trim() }" in _source("cases")
    # Saying that work has started takes no body, and the phone sends none.
    assert "requestBody" not in spec["paths"][f"{ORDERS}/{{order_id}}/start"]["post"]
    assert re.search(r"apiClient\.post<WorkOrder>\(`\$\{BASE\}/\$\{id\}/start`\)", _source("maintenance"))


# ─── C. The menu and the screens ─────────────────────────────────────────────

def test_each_row_asks_for_what_its_list_asks_for_and_each_screen_is_registered():
    needs = {r["path"]: r["needs"] for r in routes() if r["method"] == "GET"}
    menu, navigation = MENU.read_text(encoding="utf-8"), NAVIGATION.read_text(encoding="utf-8")
    for screen, (component, path) in ROWS.items():
        row = re.search(rf"\{{ screen: '{screen}',.*?\}}", menu).group(0)
        (permission,) = re.findall(r"permission: '([^']+)'", row)
        assert needs[path] == {permission}, (screen, permission, needs[path])
        # None of them is oversight: each is the work of whoever is signed in.
        assert "audience" not in row, screen
        assert (MOBILE / "screens" / f"{component}.tsx").exists()
        assert re.search(rf'<MoreStack\.Screen name="{screen}"\s+component=\{{{component}\}}', navigation), screen
        assert re.search(rf"^\s+{screen}:\s+undefined\s*$", navigation, re.M), screen


# ─── D. What the phone reads is what the server sends ────────────────────────

async def test_a_persons_own_reading_carries_every_figure_the_phone_puts_into_words():
    w = await _world()
    async with _client() as c:
        for days in workforce_readings.PERIOD_DAYS:
            reading = await _ok(await c.get(READING, headers=w["h"][GUARD], params={"days": days}))
        assert (await c.get(READING, headers=w["h"][GUARD], params={"days": 30})).status_code == 422
    assert _fields("workforce", "MyReading") <= set(reading), _fields("workforce", "MyReading") - set(reading)
    assert _fields("workforce", "Section") <= set(reading["sections"][0])
    assert {s["key"] for s in reading["sections"]} == _union("workforce", "SectionKey") == set(workforce_readings.SECTIONS)
    assert _fields("workforce", "MyReading") >= {"note", "recommendations_note"}, "the two notes are read, and shown"
    # Each section's figures, by the names the phone reads them under.
    body = re.search(r"export interface Figures \{(.*?)\n\}", _source("workforce"), re.S).group(1)
    named = {key: set(re.findall(r"(\w+):", inner)) for key, inner in re.findall(r"(\w+)\?: \{(.*?)\}", body, re.S)}
    assert set(named) == set(reading["figures"]) == _union("workforce", "SectionKey")
    for key, fields in named.items():
        assert fields <= set(reading["figures"][key]), (key, fields - set(reading["figures"][key]))
    assert _union("workforce", "ViolationType") <= set(reading["figures"]["VIOLATIONS"]["by_type"])
    # What is recommended, as it is given to the person it is about.
    advice = (REPO_ROOT / "backend" / "app" / "services" / "workforce_advice.py").read_text(encoding="utf-8")
    given = set(re.findall(r'"(\w+)":', re.search(r'return \{"key": key.*?\}', advice, re.S).group(0))) | {"answer"}
    assert _fields("workforce", "Recommendation") <= given, _fields("workforce", "Recommendation") - given


async def test_a_published_briefing_is_what_the_phone_reads():
    w = await _world()
    day = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    async with _client() as c:
        draft = await _ok(await c.post(BRIEFINGS, headers=w["h"][MANAGER], json={
            "site_id": str(w["site_a"]), "briefing_date": day}), 201)
        await _ok(await c.post(f"{BRIEFINGS}/{draft['id']}/publish", headers=w["h"][MANAGER]), 200)
        (listed,) = (await _ok(await c.get(BRIEFINGS, headers=w["h"][OPERATOR], params={"state": "PUBLISHED"})))["items"]
        one = await _ok(await c.get(f"{BRIEFINGS}/{draft['id']}", headers=w["h"][OPERATOR]))
    summary = _fields("briefings", "BriefingSummary")
    assert summary <= set(listed), summary - set(listed)
    assert "sections" not in listed, "the list is not sent the sections; the phone reads one briefing for them"
    assert summary | {"sections"} <= set(one)
    assert _fields("briefings", "BriefingSection") <= set(one["sections"][0])
    lines = [line for s in one["sections"] for line in s["lines"]]
    assert lines and all(set(line) == {"text", "as_at"} for line in lines)
    assert {line["as_at"] for line in lines} <= _union("briefings", "AsAt")
    assert listed["state"] == "PUBLISHED" and listed["site"] == {"id": str(w["site_a"]), "name": "Factory A"}


async def test_a_work_order_given_to_somebody_is_theirs_to_start_and_to_finish_from_the_phone():
    w = await _world()
    adm, op = w["h"][ADMIN], w["h"][OPERATOR]
    due = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    async with _client() as c:
        raised = await _ok(await c.post(ORDERS, headers=adm, json={
            "title": "Loading bay camera: no picture", "site_id": str(w["site_a"]), "due_at": due,
            "assigned_to_user_id": str(w["users"][OPERATOR])}), 201)
        await _ok(await c.post(ORDERS, headers=adm, json={"title": "Somebody else's", "site_id": str(w["site_a"])}), 201)
        (mine,) = (await _ok(await c.get(ORDERS, headers=op, params={"mine": True})))["items"]
        assert mine["id"] == raised["id"] and mine["assigned_to_me"] is True
        assert _fields("maintenance", "WorkOrder") <= set(mine), _fields("maintenance", "WorkOrder") - set(mine)
        assert {"start", "complete"} <= set(mine["may"]) and mine["may"]["start"] and mine["may"]["complete"]
        url = f"{ORDERS}/{raised['id']}"
        # As the phone sends them: nothing to say it has started; then what was done, in words.
        started = await _ok(await c.post(f"{url}/start", headers=op), 200)
        assert started["state"] == "IN_PROGRESS" and not started["may"]["start"] and started["may"]["complete"]
        finished = await _ok(await c.post(f"{url}/complete", headers=op, json={
            "completion_note": "Power supply replaced.", "parts_used": "12 V supply", "downtime_minutes": 0}), 200)
        assert finished["state"] == "DONE"
        # What is over is no longer in the list the phone asks for.
        assert (await _ok(await c.get(ORDERS, headers=op, params={"mine": True})))["items"] == []
    assert _union("maintenance", "OrderState") == set(maintenance_work.STATES)


async def test_a_task_on_a_case_is_read_and_finished_as_the_phone_does_it():
    w = await _world()
    adm, op = w["h"][ADMIN], w["h"][OPERATOR]
    async with _client() as c:
        case = await _ok(await c.post(CASES, headers=adm, json={
            "title": "Forced gate", "summary": "The north gate was forced.", "site_id": str(w["site_a"])}), 201)
        url = f"{CASES}/{case['id']}"
        for title in ("Ask the haulier for the driver register", "Pull the gate log"):
            await _ok(await c.post(f"{url}/tasks", headers=adm, json={
                "title": title, "assigned_to_user_id": str(w["users"][OPERATOR])}), 201)
        await _ok(await c.post(f"{url}/tasks", headers=adm, json={"title": "The lead's own"}), 201)
        (listed,) = (await _ok(await c.get(CASES, headers=op, params={"mine": True})))["items"]
        assert _fields("cases", "CaseSummary") <= set(listed), _fields("cases", "CaseSummary") - set(listed)
        assert listed["tasks_open"] == 3
        one = await _ok(await c.get(url, headers=op))
        assert _fields("cases", "CaseDetail") == {"site", "tasks"}
        assert (_fields("cases", "CaseSummary") - {"site_name"}) | {"site", "tasks"} <= set(one)
        # The list names the site; one case gives it as an object. The phone's two types say so.
        assert "site_name" not in one and one["site"] == {"id": str(w["site_a"]), "name": "Factory A"}
        assert "Omit<CaseSummary, 'site_name'>" in _source("cases")
        assert all(_fields("cases", "CaseTask") <= set(t) for t in one["tasks"])
        # Theirs to finish are the two given to them: not the one given to nobody.
        mine = [t for t in one["tasks"] if t["may_finish"]]
        assert sorted(t["title"] for t in mine) == ["Ask the haulier for the driver register", "Pull the gate log"]
        first, second = (t["id"] for t in mine)
        # Done may be said without a word; dropped is said with why — as the phone asks before it sends.
        after = await _ok(await c.post(f"{url}/tasks/{first}/done", headers=op, json={"note": ""}), 200)
        assert next(t for t in after["tasks"] if t["id"] == first)["state"] == "DONE"
        assert (await c.post(f"{url}/tasks/{second}/drop", headers=op, json={"note": ""})).status_code == 422
        after = await _ok(await c.post(f"{url}/tasks/{second}/drop", headers=op, json={"note": "The client declined."}), 200)
        assert next(t for t in after["tasks"] if t["id"] == second)["state"] == "DROPPED"
        assert not [t for t in after["tasks"] if t["may_finish"]]
        # With nothing left for them on it, it is no longer theirs.
        assert (await _ok(await c.get(CASES, headers=op, params={"mine": True})))["items"] == []
