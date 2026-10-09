"""A visitor's badge used outside its authorisation, handed to the intelligence layer (services/visitor_movement_events.py).

A. Off unless the organisation asks: the layer is exactly as it was.
B. On: what is still to be looked at is handed over once, as an event that names nobody.
C. The layer places it beside what a camera saw, and decides nothing.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.main import app  # noqa: F401
from app.core.config_keys import SETTING_VALIDATORS
from app.db.session import AsyncSessionLocal
from app.services import intel_runner
from app.services import visitor_movement_events as handed
from tests.test_drone_api import ADMIN, MANAGER, SUPERVISOR, _client, _sql
from tests.test_intel_decisions import _ago, _pass, _rows
from tests.test_intel_events import _alert, _events
from tests.test_intel_events import _world as _intel_world
from tests.test_visitor_authorizations import _approved, _card, _check_in, _period, _site_with_doors, _swipe, _visit

VISITS = "/api/v1/visitor-authorizations"


async def _switch(c, w: dict, on: bool) -> None:
    r = await c.put(f"/api/v1/settings/{handed.SETTING}", headers=w["h"][ADMIN], json={"setting_value": on})
    assert r.status_code == 200, r.text


async def _a_visit(c, w: dict) -> dict:
    """A visitor authorised for Block A, whose badge opens a door of Block A and then one of Block B."""
    doors = await _site_with_doors(w)
    visit = await _visit(w, "Lim Mei Ling")
    card = await _card(w, "V-17")
    yes = await _approved(c, w, visit, place_ids=[str(doors["block_a"])])
    await _period(yes["id"], 180, -60)
    await _check_in(c, w, visit, "V-17", minutes_ago=150)
    return {"doors": doors, "card": card, "id": yes["id"],
            "inside": await _swipe(w, doors["d_a2"], card, 30), "outside": await _swipe(w, doors["d_b"], card, 20)}


def test_the_event_names_nobody_and_says_it_is_something_to_look_at():
    movement = {"authorization_id": "a1", "access_event_id": "e1", "occurred_at": _ago(minutes=5), "event_type": "granted",
                "door_id": "d1", "door_name": "B lobby", "badge": "V-17", "place_id": "p1", "place_name": "B lobby door",
                "within": False, "in_period": True, "to_look_at": True, "review": None}
    n = handed.normalise(movement, "s1", {"latitude": 1.3, "longitude": 103.8})
    assert (n.source_type, n.source_table, n.source_id, n.event_type) == ("ACCESS_CONTROL", "access_events", "e1", handed.EVENT_TYPE)
    assert n.severity == "low" and n.subject_kind == "NONE" and n.subject_ref is None
    assert n.title == "A visitor's badge was used outside the places the visit is authorised for: B lobby"
    assert (n.latitude, n.longitude, n.location_label, n.site_id) == (1.3, 103.8, "B lobby door", "s1")
    assert n.attributes == {"authorization_id": "a1", "door_id": "d1", "place_id": "p1", "door_event": "granted",
                            "within_places": False, "within_period": True, "note": "Something to look at, not a finding."}
    # Nothing of who the visitor is, and not the number on their badge.
    assert "V-17" not in json.dumps(n.attributes) + n.title
    late = handed.normalise({**movement, "within": True, "in_period": False}, "s1", None)
    assert "outside the period the visit" in late.title and late.latitude is None
    both = handed.normalise({**movement, "in_period": False}, "s1", None)
    assert "outside the places and the period the visit" in both.title
    for words in (n.title, late.title, both.title):
        assert not any(w in words.lower() for w in ("unauthorised", "unauthorized", "intruder", "suspect", "breach"))
    SETTING_VALIDATORS[handed.SETTING](True)
    with pytest.raises(ValueError):
        SETTING_VALIDATORS[handed.SETTING]("yes")
    assert not handed.SETTING.startswith("intel."), "it is the visitor module's to hand over, and its setting"


def test_the_reader_the_runner_imports_reads_and_can_take_no_action():
    """The intelligence runner may import only what cannot act (tests/test_intel_events.py). This is the one
    module of another part of the platform it imports, so the same is held of it here."""
    import ast
    import re
    from pathlib import Path

    services = Path(handed.__file__).parent
    source = Path(handed.__file__).read_text(encoding="utf-8")
    imported = {node.module if node.module != "app.services" else f"app.services.{alias.name}"
                for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app")
                for alias in node.names}
    assert imported == {"app.services.intel_events", "app.services.visitor_authorization"}
    # It writes through the layer's own insert, and has no write of its own.
    assert "events._INSERT" in source and not re.search(r"INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM", source)
    # What it reads with reads only, and imports nothing of the application's.
    reader = (services / "visitor_authorization.py").read_text(encoding="utf-8")
    assert not re.search(r"INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM", reader)
    assert not [n for n in ast.walk(ast.parse(reader)) if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("app")]
    for acts in ("dispatch", "incidents", "alerts", "notify", "push"):
        assert acts not in imported and f"import {acts}" not in source


async def test_nothing_is_handed_over_until_the_organisation_asks_and_then_each_event_once():
    w, other = await _intel_world(), await _intel_world()
    async with _client() as c:
        v = await _a_visit(c, w)
        theirs = await _a_visit(c, other)
        # Off: nothing is read, and the layer's own tick is as it was.
        assert await handed.ingest_tenant(AsyncSessionLocal, w["tenant"]) == 0
        tick = await intel_runner.run_ingest_tick(AsyncSessionLocal)
        assert handed.SOURCE not in tick["by_source"] and await _events(w) == []

        await _switch(c, w, True)
        assert await handed.ingest_tenant(AsyncSessionLocal, w["tenant"]) == 1
        (event,) = await _events(w)
        assert (event["source_type"], event["source_table"], event["source_id"]) == ("ACCESS_CONTROL", "access_events", v["outside"])
        assert event["event_type"] == handed.EVENT_TYPE and event["severity"] == "low" and event["site_id"] == w["site_a"]
        assert event["subject_kind"] == "NONE" and event["subject_ref"] is None and event["camera_id"] is None
        attributes = event["attributes"] if isinstance(event["attributes"], dict) else json.loads(event["attributes"])
        assert attributes["authorization_id"] == v["id"] and attributes["within_places"] is False
        assert "Lim Mei Ling" not in str(event) and "V-17" not in str(event), "the event names nobody"
        # The door inside what was authorised is not an event at all.
        assert v["inside"] not in {e["source_id"] for e in await _events(w)}
        # Read again, nothing is added; through the runner's own tick, the same.
        assert await handed.ingest_tenant(AsyncSessionLocal, w["tenant"]) == 0
        later = await _swipe(w, v["doors"]["d_b"], v["card"], 5)
        tick = await intel_runner.run_ingest_tick(AsyncSessionLocal)
        assert tick["by_source"].get(handed.SOURCE) == 1 and tick["failed"] == 0
        assert {e["source_id"] for e in await _events(w)} == {v["outside"], later}

        # What a person has already looked at is not handed over.
        third = await _swipe(w, v["doors"]["d_b"], v["card"], 2)
        done = await c.post(f"{VISITS}/{v['id']}/movements/{third}/review", headers=w["h"][SUPERVISOR],
                            json={"outcome": "IN_ORDER", "note": "Escorted to the canteen."})
        assert done.status_code == 201, done.text
        assert await handed.ingest_tenant(AsyncSessionLocal, w["tenant"]) == 0
        # The other organisation did not ask: none of its door events is an event.
        assert await handed.ingest_tenant(AsyncSessionLocal, other["tenant"]) == 0 and await _events(other) == []
        assert theirs["outside"] not in {e["source_id"] for e in await _events(w)}
        # Switched off again, nothing more is read. What was read stays.
        await _switch(c, w, False)
        await _swipe(w, v["doors"]["d_b"], v["card"], 1)
        assert await handed.ingest_tenant(AsyncSessionLocal, w["tenant"]) == 0 and len(await _events(w)) == 2
    # It is read as the application's role, under row level security.
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
    # Handing an event over raised no alert, opened no incident and reviewed nothing for anybody.
    assert await _sql("SELECT 1 FROM alerts WHERE tenant_id = :t", {"t": w["tenant"]}) == []
    assert await _sql("SELECT 1 FROM incidents WHERE tenant_id = :t", {"t": w["tenant"]}) == []
    assert len(await _sql("SELECT 1 FROM visitor_movement_reviews WHERE tenant_id = :t", {"t": w["tenant"]})) == 1


async def test_the_layer_places_it_beside_what_a_camera_saw_and_decides_nothing():
    w = await _intel_world()
    async with _client() as c:
        v = await _a_visit(c, w)
        await _switch(c, w, True)
        # A camera sees somebody a moment after the badge opened the door of Block B.
        seen = await _alert(w, "intrusion", code="intrusion.zone_breach", severity="high", title="Person at Gate 1",
                            at=_ago(seconds=30))
        assert await handed.ingest_tenant(AsyncSessionLocal, w["tenant"]) == 1
        await _pass(w)
        events = await _events(w)
        assert {(e["source_table"], e["source_id"]) for e in events} == {("access_events", v["outside"]), ("alerts", seen)}
        members = await _sql("SELECT situation_id, event_id FROM security_situation_events WHERE tenant_id = :t", {"t": w["tenant"]})
        assert {m["event_id"] for m in members} == {e["id"] for e in events}, "both were placed"
        situations = await _rows(w, "security_situations")
        assert 1 <= len(situations) <= 2
        # Whatever it was placed with, nothing was decided and nothing was opened: a person reads it first.
        assert await _rows(w, "security_decisions", "decided_at") == []
        assert await _sql("SELECT 1 FROM incidents WHERE tenant_id = :t", {"t": w["tenant"]}) == []
        # Whoever manages visits still has it on their list to look at: the layer took nothing off it.
        todo = (await c.get(f"{VISITS}/to-review", headers=w["h"][MANAGER])).json()
        assert [i["access_event_id"] for i in todo["items"]] == [str(v["outside"])]
        # And the situation, read by an operator, shows the event without a name.
        for s in situations:
            shown = await c.get(f"/api/v1/security-intelligence/situations/{s['id']}", headers=w["h"][MANAGER])
            assert shown.status_code == 200 and "Lim Mei Ling" not in shown.text and "V-17" not in shown.text
