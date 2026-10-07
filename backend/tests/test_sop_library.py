"""The SOP library: procedures in versions, approved before they are in force, and found by their own words.

  A — A procedure's text, cut into the passages it is found by
  B — Writing a procedure, and who sees it before it is approved
  C — Approval: by somebody else; and an approved version is not changed
  D — Which version is in force: the next one, a date to come, one that has run out, a retired procedure
  E — Asking: passages as approved, and nothing composed
  F — The procedure beside an incident and a situation
  G — The document as issued
  H — What the application role and the database refuse

Every request goes through the real app over ASGI, as svc_app with RLS enforced.

The claims, each with tests: a version is in force only if somebody other than
its author approved it; an approved version cannot be changed; somebody who only
reads sees only what is in force; and `ask` returns the words of procedures in
force and never an answer of its own.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import sop as api
from app.services import sop_library as library
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world
from tests.test_incident_responses import _guard
from tests.test_investigation_search import _audit

BASE = "/api/v1/sop"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0148_sop_library.py").read_text(encoding="utf-8")
TABLES = ("sop_documents", "sop_versions", "sop_passages", "sop_incident_types")

FIRE = """# Fire alarm

## On hearing the alarm
1. Stop what you are doing and go to the fire panel in the guardhouse.
2. Read which zone is in alarm and radio it to the supervisor.

## Evacuation
Open Gate 1 and Gate 3 for the fire engines. Direct everybody to the assembly point at the north car park.
Do not let anybody back into the building until the fire service says so.

CALLING THE FIRE SERVICE
Dial 995. Give the address, the zone in alarm and your name.
"""

INTRUDER = """Somebody inside the fence:

Do not approach alone. Radio the supervisor, keep the person in sight from a distance and note what they are wearing.

If the person runs, do not chase. Note the direction and call the police on 999.
"""


async def _write(c, w: dict, title: str = "Fire alarm", body: str = FIRE, *, who: int = SUPERVISOR,
                 site: str | None = "site_a", category: str = "fire", types: list[str] | None = None) -> dict:
    r = await c.post(f"{BASE}/documents", headers=w["h"][who], json={
        "title": title, "category": category, "site_id": str(w[site]) if site else None, "body": body,
        "incident_types": types if types is not None else ["fire_smoke"]})
    assert r.status_code == 201, r.text
    return r.json()


async def _in_force(c, w: dict, title: str = "Fire alarm", body: str = FIRE, *, site: str | None = "site_a",
                    category: str = "fire", types: list[str] | None = None, author: int | None = None) -> dict:
    """A procedure written, submitted and approved by somebody else: in force now."""
    author = author if author is not None else (SUPERVISOR if site == "site_a" else ADMIN)
    made = await _write(c, w, title, body, who=author, site=site, category=category, types=types)
    version = made["versions"][0]["id"]
    assert (await c.post(f"{BASE}/versions/{version}/submit", headers=w["h"][author])).status_code == 200
    done = await c.post(f"{BASE}/versions/{version}/approve", headers=w["h"][MANAGER])
    assert done.status_code == 200, done.text
    return done.json()


# ─── A. A procedure's text, cut into passages ────────────────────────────────

def test_a_procedure_is_cut_into_its_paragraphs_each_under_its_heading():
    cut = library.passages(FIRE)
    assert [(p["heading"], p["body"].split("\n")[0][:28]) for p in cut] == [
        ("On hearing the alarm", "1. Stop what you are doing a"),
        ("Evacuation", "Open Gate 1 and Gate 3 for t"),
        ("CALLING THE FIRE SERVICE", "Dial 995. Give the address, "),
    ]
    # The steps of a list stay together, and the words are the procedure's own.
    assert cut[0]["body"] == ("1. Stop what you are doing and go to the fire panel in the guardhouse.\n"
                              "2. Read which zone is in alarm and radio it to the supervisor.")
    assert cut[1]["body"].endswith("until the fire service says so.")
    assert "".join(p["body"] for p in cut).replace("\n", "") in FIRE.replace("\n", "") or all(
        line in FIRE for p in cut for line in p["body"].split("\n"))


def test_what_counts_as_a_heading_and_what_does_not():
    cut = library.passages(INTRUDER)
    assert [p["heading"] for p in cut] == ["Somebody inside the fence", "Somebody inside the fence"]
    assert library.passages("One paragraph.\n\nAnother.") == [
        {"heading": None, "body": "One paragraph."}, {"heading": None, "body": "Another."}]
    assert library.passages("# Only a heading\n\n## And another\n") == [], "a heading with nothing under it is not a passage"
    assert library.passages("") == [] and library.passages("\n\n  \n") == []
    # A sentence that happens to be short, and a line that ends in a colon inside a paragraph, are not headings.
    assert library.passages("Call 995.\n\nThen wait.") == [
        {"heading": None, "body": "Call 995."}, {"heading": None, "body": "Then wait."}]
    assert library.passages("Do these:\n1. One\n2. Two") == [{"heading": None, "body": "Do these:\n1. One\n2. Two"}]
    assert library.passages("STEP ONE\n\nGo.\r\n\r\n## Two\nStay.") == [
        {"heading": "STEP ONE", "body": "Go."}, {"heading": "Two", "body": "Stay."}]
    assert len(library.passages("\n\n".join(f"Paragraph {n}." for n in range(500)))) == library.MAX_PASSAGES_PER_VERSION


# ─── B. Writing a procedure ──────────────────────────────────────────────────

async def test_a_procedure_is_written_as_a_draft_and_is_the_writers_until_it_is_approved():
    w, other = await _world(), await _world()
    async with _client() as c:
        made = await _write(c, w)
        second = await _write(c, w, "Intruder", INTRUDER, who=ADMIN, site=None, category="incident_response",
                              types=["Intrusion", " weapon "])
        theirs = await _write(c, other)
        listed = (await c.get(f"{BASE}/documents", headers=w["h"][SUPERVISOR])).json()
        for reader in (GUARD, VIEWER, OPERATOR):
            seen = await c.get(f"{BASE}/documents", headers=w["h"][reader])
            assert seen.status_code == 200 and seen.json()["items"] == [], "nothing is in force yet"
            assert (await c.get(f"{BASE}/documents/{made['id']}", headers=w["h"][reader])).status_code == 404
            assert (await c.get(f"{BASE}/versions/{made['versions'][0]['id']}", headers=w["h"][reader])).status_code == 404
        assert (await c.get(f"{BASE}/documents/{made['id']}", headers=other["h"][ADMIN])).status_code == 404
        assert (await c.get(f"{BASE}/documents")).status_code == 401
    assert (made["code"], second["code"], theirs["code"]) == ("SOP-0001", "SOP-0002", "SOP-0001"), "numbered per organisation"
    assert made["state"] == "NOT_YET_APPROVED" and made["site_name"] == "Factory A" and made["category"] == "fire"
    assert made["incident_types"] == ["fire_smoke"] and second["incident_types"] == ["intrusion", "weapon"]
    (v,) = made["versions"]
    assert (v["version_no"], v["state"], v["in_force"], v["body"]) == (1, "DRAFT", False, FIRE.strip())
    assert v["drafted_by_name"] == f"Role {SUPERVISOR} User"
    assert v["may"] == {"edit": True, "submit": True, "withdraw": False, "decide": False}
    assert made["open_version"] == {"id": v["id"], "version_no": 1, "state": "DRAFT"}
    # Held to site A: sees site A's and the one for every site.
    assert [d["code"] for d in listed["items"]] == ["SOP-0001", "SOP-0002"]
    assert listed["can_write"] and not listed["can_approve"] and listed["categories"] == list(library.CATEGORIES)
    (entry,) = [a for a in await _audit(w, "sop.create") if a["detail"]["code"] == "SOP-0001"]
    assert entry["detail"]["title"] == "Fire alarm" and entry["detail"]["site_id"] == str(w["site_a"])


async def test_what_a_procedure_has_to_be_and_who_may_write_one():
    w, other = await _world(), await _world()
    good = {"title": "Fire alarm", "category": "fire", "site_id": str(w["site_a"]), "body": FIRE}
    async with _client() as c:
        for change, status, why in (
            ({"category": "gossip"}, 422, "Unknown category"),
            ({"title": "   "}, 422, "has a title"),
            ({"body": "  \n "}, 422, "says something"),
            ({"incident_types": ["Fire Smoke!"]}, 422, "is not a kind of incident"),
            ({"site_id": str(other["site_a"])}, 404, "Site not found"),
            ({"site_id": str(uuid.uuid4())}, 404, "Site not found"),
        ):
            r = await c.post(f"{BASE}/documents", headers=w["h"][ADMIN], json={**good, **change})
            assert r.status_code == status and why in str(r.json()["detail"]), (change, r.text)
        assert (await c.post(f"{BASE}/documents", headers=w["h"][ADMIN], json={**good, "version": 3})).status_code == 422
        for who in (OPERATOR, GUARD, VIEWER):
            r = await c.post(f"{BASE}/documents", headers=w["h"][who], json=good)
            assert r.status_code == 403 and r.json()["detail"] == "Missing permission: sop:write", who
        # Held to site A: writes for site A, and for nowhere else.
        everywhere = await c.post(f"{BASE}/documents", headers=w["h"][SUPERVISOR], json={**good, "site_id": None})
        assert everywhere.status_code == 422 and "names one of them" in everywhere.json()["detail"]
        assert (await c.post(f"{BASE}/documents", headers=w["h"][SUPERVISOR],
                             json={**good, "site_id": str(w["site_b"])})).status_code == 404
    key = TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key
    try:
        async with _client() as c:
            r = await c.post(f"{BASE}/documents", json=good)
            assert r.status_code == 403 and "not by an API key" in r.json()["detail"]
            assert (await c.get(f"{BASE}/documents")).status_code == 200, "an integration may read the library"
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    assert await _sql("SELECT 1 FROM sop_documents WHERE tenant_id = :t", {"t": w["tenant"]}) == []


async def test_a_procedures_title_site_and_incident_kinds_are_changed_and_its_text_is_not():
    w = await _world()
    async with _client() as c:
        made = await _write(c, w)
        wide = await _write(c, w, "Intruder", INTRUDER, who=ADMIN, site=None, types=[])
        url = f"{BASE}/documents/{made['id']}"
        changed = await c.patch(url, headers=w["h"][SUPERVISOR], json={"title": " Fire alarm and evacuation ",
                                                                       "category": "evacuation"})
        assert changed.status_code == 200, changed.text
        assert (await c.patch(url, headers=w["h"][SUPERVISOR], json={})).status_code == 422
        assert (await c.patch(url, headers=w["h"][SUPERVISOR], json={"body": "x"})).status_code == 422, \
            "what it says is changed by drafting its next version"
        assert (await c.patch(url, headers=w["h"][SUPERVISOR], json={"code": "SOP-9999"})).status_code == 422
        assert (await c.patch(url, headers=w["h"][SUPERVISOR], json={"site_id": None})).status_code == 422
        assert (await c.patch(url, headers=w["h"][OPERATOR], json={"title": "x"})).status_code == 403
        tagged = await c.put(f"{url}/incident-types", headers=w["h"][SUPERVISOR],
                             json={"incident_types": ["fire_smoke", "alarm", "alarm"]})
        emptied = await c.put(f"{BASE}/documents/{wide['id']}/incident-types", headers=w["h"][ADMIN],
                              json={"incident_types": []})
        # A procedure for every site is settled by somebody who answers for every site.
        for refused in (await c.patch(f"{BASE}/documents/{wide['id']}", headers=w["h"][SUPERVISOR], json={"title": "x"}),
                        await c.put(f"{BASE}/documents/{wide['id']}/incident-types", headers=w["h"][SUPERVISOR],
                                    json={"incident_types": ["intrusion"]})):
            assert refused.status_code == 403 and "every site" in refused.json()["detail"]
        kinds = (await c.get(f"{BASE}/incident-types", headers=w["h"][GUARD])).json()["items"]
    assert changed.json()["title"] == "Fire alarm and evacuation" and changed.json()["category"] == "evacuation"
    assert changed.json()["code"] == "SOP-0001"
    assert tagged.json()["incident_types"] == ["alarm", "fire_smoke"] and emptied.json()["incident_types"] == []
    assert set(library.KNOWN_TYPES) <= set(kinds) and kinds == sorted(kinds)
    (tag, _) = await _audit(w, "sop.tag")
    assert tag["detail"]["incident_types"] == ["alarm", "fire_smoke"] and tag["detail"]["were"] == ["fire_smoke"]
    assert len(await _audit(w, "sop.update")) == 1


# ─── C. Approval ─────────────────────────────────────────────────────────────

async def test_a_draft_is_submitted_and_approved_by_somebody_else_and_is_then_in_force():
    w = await _world()
    async with _client() as c:
        made = await _write(c, w, who=ADMIN)
        v = made["versions"][0]["id"]
        url = f"{BASE}/versions/{v}"
        edited = await c.patch(url, headers=w["h"][SUPERVISOR], json={"body": FIRE + "\n\nTell the duty manager."})
        assert edited.status_code == 200 and edited.json()["body"].endswith("Tell the duty manager.")
        assert (await c.post(f"{url}/approve", headers=w["h"][MANAGER])).status_code == 409, "not yet submitted"
        submitted = await c.post(f"{url}/submit", headers=w["h"][ADMIN])
        assert submitted.status_code == 200 and submitted.json()["state"] == "SUBMITTED" and submitted.json()["submitted_at"]
        assert (await c.patch(url, headers=w["h"][ADMIN], json={"body": "x"})).status_code == 409
        awaiting = (await c.get(f"{BASE}/documents", headers=w["h"][MANAGER], params={"state": "AWAITING"})).json()["items"]
        # Back to a draft, corrected, and forward again.
        assert (await c.post(f"{url}/withdraw", headers=w["h"][ADMIN])).json()["state"] == "DRAFT"
        assert (await c.patch(url, headers=w["h"][ADMIN], json={"body": FIRE})).status_code == 200
        assert (await c.post(f"{url}/submit", headers=w["h"][SUPERVISOR])).status_code == 200
        assert (await c.post(f"{url}/submit", headers=w["h"][ADMIN])).status_code == 409

        own = await c.post(f"{url}/approve", headers=w["h"][ADMIN])
        assert own.status_code == 403 and own.json()["detail"] == "A procedure is approved by somebody other than who wrote it."
        for who in (SUPERVISOR, OPERATOR, GUARD):
            r = await c.post(f"{url}/approve", headers=w["h"][who])
            assert r.status_code == 403, who
        for bad in ({"effective_from": "2030-01-01T00:00:00"}, {"effective_until": "2020-01-01T00:00:00Z"},
                    {"effective_from": "2030-06-01T00:00:00Z", "effective_until": "2030-05-01T00:00:00Z"}):
            assert (await c.post(f"{url}/approve", headers=w["h"][MANAGER], json=bad)).status_code == 422, bad
        approved = await c.post(f"{url}/approve", headers=w["h"][MANAGER], json={"note": "Reviewed with the fire warden."})
        assert approved.status_code == 200, approved.text
        assert (await c.post(f"{url}/approve", headers=w["h"][MANAGER])).status_code == 409
        for reader in (GUARD, VIEWER, OPERATOR):
            seen = (await c.get(f"{BASE}/documents/{made['id']}", headers=w["h"][reader])).json()
            assert seen["state"] == "IN_FORCE" and [x["version_no"] for x in seen["versions"]] == [1]
            assert seen["open_version"] is None and not any(seen["versions"][0]["may"].values())
        one = (await c.get(url, headers=w["h"][GUARD])).json()
    assert [d["code"] for d in awaiting] == ["SOP-0001"]
    done = approved.json()
    assert done["state"] == "IN_FORCE" and done["in_force_version_no"] == 1 and done["effective_from"]
    (v1,) = done["versions"]
    assert (v1["state"], v1["in_force"], v1["decided_by_name"]) == ("APPROVED", True, f"Role {MANAGER} User")
    assert v1["decision_note"] == "Reviewed with the fire warden." and not any(v1["may"].values())
    assert [p["heading"] for p in one["passages"]] == ["On hearing the alarm", "Evacuation", "CALLING THE FIRE SERVICE"]
    stored = await _sql("SELECT ordinal, heading, body FROM sop_passages WHERE version_id = :v ORDER BY ordinal", {"v": v})
    assert [(p["ordinal"], p["heading"]) for p in stored] == [(1, "On hearing the alarm"), (2, "Evacuation"),
                                                             (3, "CALLING THE FIRE SERVICE")]
    assert [p["body"] for p in stored] == [p["body"] for p in library.passages(FIRE)], "cut once, as approved"
    (entry,) = await _audit(w, "sop.version.approve")
    assert entry["detail"]["passages"] == 3 and entry["detail"]["version_no"] == 1 and entry["user_id"] == w["users"][MANAGER]
    assert len(await _audit(w, "sop.version.submit")) == 2 and len(await _audit(w, "sop.version.withdraw")) == 1


async def test_a_rejected_version_says_why_and_the_next_is_drafted_from_it():
    w = await _world()
    async with _client() as c:
        made = await _write(c, w)
        v = made["versions"][0]["id"]
        await c.post(f"{BASE}/versions/{v}/submit", headers=w["h"][SUPERVISOR])
        for no_reason in ({}, {"reason": "  "}):
            assert (await c.post(f"{BASE}/versions/{v}/reject", headers=w["h"][MANAGER], json=no_reason)).status_code == 422
        rejected = await c.post(f"{BASE}/versions/{v}/reject", headers=w["h"][MANAGER],
                                json={"reason": "The assembly point moved to the south car park."})
        assert rejected.status_code == 200, rejected.text
        assert (await c.patch(f"{BASE}/versions/{v}", headers=w["h"][SUPERVISOR], json={"body": "x"})).status_code == 409
        assert (await c.get(f"{BASE}/documents/{made['id']}", headers=w["h"][GUARD])).status_code == 404
        nxt = await c.post(f"{BASE}/documents/{made['id']}/versions", headers=w["h"][SUPERVISOR])
        assert nxt.status_code == 201, nxt.text
        twice = await c.post(f"{BASE}/documents/{made['id']}/versions", headers=w["h"][ADMIN])
        assert twice.status_code == 409 and "Version 2 is already being written" in twice.json()["detail"]
        v2 = nxt.json()["versions"][0]
        unsaid = await c.post(f"{BASE}/versions/{v2['id']}/submit", headers=w["h"][SUPERVISOR])
        assert unsaid.status_code == 422 and "what is different" in unsaid.json()["detail"]
        await c.patch(f"{BASE}/versions/{v2['id']}", headers=w["h"][SUPERVISOR],
                      json={"body": FIRE.replace("north", "south"), "change_note": "Assembly point is the south car park."})
        assert (await c.post(f"{BASE}/versions/{v2['id']}/submit", headers=w["h"][SUPERVISOR])).status_code == 200
    doc = rejected.json()
    assert doc["state"] == "NOT_YET_APPROVED" and doc["versions"][0]["state"] == "REJECTED"
    assert doc["versions"][0]["decision_note"] == "The assembly point moved to the south car park."
    assert (v2["version_no"], v2["state"], v2["body"]) == (2, "DRAFT", FIRE.strip()), "it starts from what was rejected"
    assert [x["version_no"] for x in nxt.json()["versions"]] == [2, 1]
    assert await _sql("SELECT 1 FROM sop_passages WHERE tenant_id = :t", {"t": w["tenant"]}) == [], \
        "nothing rejected is ever found"


# ─── D. Which version is in force ────────────────────────────────────────────

async def test_the_version_in_force_stays_in_force_until_the_next_is_approved_and_its_date_comes():
    w = await _world()
    async with _client() as c:
        doc = await _in_force(c, w)
        nxt = (await c.post(f"{BASE}/documents/{doc['id']}/versions", headers=w["h"][SUPERVISOR], json={
            "body": FIRE.replace("north car park", "south car park"), "change_note": "The assembly point moved."})).json()
        v2 = nxt["versions"][0]["id"]

        async def reads(who=GUARD) -> dict:
            return (await c.get(f"{BASE}/documents/{doc['id']}", headers=w["h"][who])).json()

        assert (await reads())["versions"][0]["version_no"] == 1, "a draft changes nothing for anybody who reads"
        await c.post(f"{BASE}/versions/{v2}/submit", headers=w["h"][SUPERVISOR])
        soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        approved = await c.post(f"{BASE}/versions/{v2}/approve", headers=w["h"][MANAGER], json={"effective_from": soon})
        assert approved.status_code == 200, approved.text
        waiting = await reads()
        assert waiting["in_force_version_no"] == 1 and "north car park" in waiting["versions"][0]["body"], \
            "approved, and not yet in force"
        assert [(x["version_no"], x["in_force"]) for x in approved.json()["versions"]] == [(2, False), (1, True)]
        # Its date comes.
        await _run([("ALTER TABLE sop_versions DISABLE TRIGGER sop_version_decided", {}),
                    ("UPDATE sop_versions SET effective_from = now() - interval '1 minute' WHERE id = :v", {"v": v2}),
                    ("ALTER TABLE sop_versions ENABLE TRIGGER sop_version_decided", {})])
        now = await reads()
        writer = await reads(SUPERVISOR)
        found = (await c.post(f"{BASE}/ask", headers=w["h"][GUARD], json={"question": "assembly point"})).json()
    assert now["in_force_version_no"] == 2 and [x["version_no"] for x in now["versions"]] == [2]
    assert "south car park" in now["versions"][0]["body"]
    assert [(x["version_no"], x["in_force"]) for x in writer["versions"]] == [(2, True), (1, False)], \
        "whoever writes them still sees the version it replaced"
    assert {p["version"]["version_no"] for p in found["passages"]} == {2}, "the version it replaced is not found"
    assert all("south car park" in p["text"] for p in found["passages"])


async def test_a_version_that_has_run_out_leaves_nothing_in_force_and_a_retired_procedure_is_not_found():
    w = await _world()
    async with _client() as c:
        old = await _in_force(c, w)
        nxt = (await c.post(f"{BASE}/documents/{old['id']}/versions", headers=w["h"][SUPERVISOR],
                            json={"change_note": "Reviewed."})).json()
        v2 = nxt["versions"][0]["id"]
        await c.post(f"{BASE}/versions/{v2}/submit", headers=w["h"][SUPERVISOR])
        later = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        assert (await c.post(f"{BASE}/versions/{v2}/approve", headers=w["h"][MANAGER],
                             json={"effective_until": later})).status_code == 200
        other = await _in_force(c, w, "Intruder", INTRUDER, category="incident_response", types=["intrusion"])
        # A year passes: version 2 has run out. Version 1 does not come back.
        await _run([("ALTER TABLE sop_versions DISABLE TRIGGER sop_version_decided", {}),
                    ("UPDATE sop_versions SET effective_from = now() - interval '400 days', "
                     "effective_until = now() - interval '35 days' WHERE id = :v", {"v": v2}),
                    ("ALTER TABLE sop_versions ENABLE TRIGGER sop_version_decided", {})])
        writer = (await c.get(f"{BASE}/documents/{old['id']}", headers=w["h"][SUPERVISOR])).json()
        assert (await c.get(f"{BASE}/documents/{old['id']}", headers=w["h"][GUARD])).status_code == 404
        expired = (await c.get(f"{BASE}/documents", headers=w["h"][ADMIN], params={"state": "EXPIRED"})).json()["items"]
        fire = (await c.post(f"{BASE}/ask", headers=w["h"][GUARD], json={"question": "fire alarm zone"})).json()

        assert (await c.post(f"{BASE}/documents/{other['id']}/retire", headers=w["h"][SUPERVISOR])).status_code == 403
        retired = await c.post(f"{BASE}/documents/{other['id']}/retire", headers=w["h"][MANAGER])
        assert retired.status_code == 200 and retired.json()["state"] == "RETIRED"
        assert (await c.post(f"{BASE}/documents/{other['id']}/retire", headers=w["h"][MANAGER])).status_code == 409
        assert (await c.post(f"{BASE}/documents/{other['id']}/versions", headers=w["h"][SUPERVISOR])).status_code == 409
        gone = (await c.post(f"{BASE}/ask", headers=w["h"][GUARD], json={"question": "police chase"})).json()
        assert (await c.get(f"{BASE}/documents", headers=w["h"][GUARD])).json()["items"] == []
        restored = await c.post(f"{BASE}/documents/{other['id']}/restore", headers=w["h"][ADMIN])
        back = (await c.post(f"{BASE}/ask", headers=w["h"][GUARD], json={"question": "police chase"})).json()
    assert writer["state"] == "EXPIRED" and writer["in_force_version_no"] == 2
    assert [d["code"] for d in expired] == ["SOP-0001"]
    assert fire["passages"] == [] and fire["nothing"] == library.NOTHING, "nor is the version it replaced found instead"
    assert gone["passages"] == [] and restored.json()["state"] == "IN_FORCE"
    assert [p["procedure"]["code"] for p in back["passages"]] == ["SOP-0002"]
    assert len(await _audit(w, "sop.retire")) == 1 and len(await _audit(w, "sop.restore")) == 1


# ─── E. Asking ───────────────────────────────────────────────────────────────

async def test_asking_returns_the_passages_as_approved_with_where_each_is_from_and_composes_nothing():
    w, other = await _world(), await _world()
    async with _client() as c:
        fire = await _in_force(c, w)
        await _in_force(c, w, "Intruder", INTRUDER, site=None, category="incident_response", types=["intrusion"])
        await _in_force(c, w, "Site B gate", "Gate 9 at site B is opened for the fire engines only by the duty manager.",
                        site="site_b", category="access", types=[])
        await _write(c, w, "A draft about fire", "Fire doors are checked every evacuation drill.", who=ADMIN)
        await _in_force(c, other, "Their fire plan", "Evacuate by the west stairs when the fire alarm sounds.")
        r = await c.post(f"{BASE}/ask", headers=w["h"][GUARD], json={"question": "What do I do when evacuating for a fire?"})
        assert r.status_code == 200, r.text
        at_b = (await c.post(f"{BASE}/ask", headers=w["h"][ADMIN],
                             json={"question": "fire engines gate", "site_id": str(w["site_b"])})).json()
        held = (await c.post(f"{BASE}/ask", headers=w["h"][SUPERVISOR], json={"question": "fire engines gate"})).json()
        assert (await c.post(f"{BASE}/ask", headers=w["h"][SUPERVISOR],
                             json={"question": "gate", "site_id": str(w["site_b"])})).status_code == 404
        nothing = (await c.post(f"{BASE}/ask", headers=w["h"][GUARD], json={"question": "helicopter landing"})).json()
        empty = (await c.post(f"{BASE}/ask", headers=w["h"][GUARD], json={"question": "what do I do if the"})).json()
        assert (await c.post(f"{BASE}/ask", headers=w["h"][GUARD], json={"question": "x"})).status_code == 422
        assert (await c.post(f"{BASE}/ask", headers=w["h"][GUARD], json={"question": "fire", "tone": "brief"})).status_code == 422
    answer = r.json()
    assert answer["is_an_answer"] is False and answer["note"] == library.ASK_NOTE and "nothing" not in answer
    assert answer["words"] == ["evacuating", "fire"], "the words it was looked for by; not 'what', 'do', 'I', 'when'"
    top = answer["passages"][0]
    # "evacuating" finds "Evacuation": the same word in another form. Both words, so it comes first.
    assert top["heading"] == "Evacuation" and top["matched"] == 2 and top["of"] == 2
    assert top["matched_words"] == ["evacuating", "fire"]
    assert top["text"] == library.passages(FIRE)[1]["body"], "word for word as it was approved"
    assert top["procedure"] == {"id": fire["id"], "code": "SOP-0001", "title": "Fire alarm", "category": "fire",
                                "site_id": str(w["site_a"]), "site_name": "Factory A"}
    assert top["version"]["version_no"] == 1 and top["version"]["approved_by_name"] == f"Role {MANAGER} User"
    assert top["version"]["approved_at"] and top["version"]["effective_from"]
    codes = [p["procedure"]["code"] for p in answer["passages"]]
    assert set(codes) == {"SOP-0001", "SOP-0003"}, "the guard may see both sites; the draft and the other tenant's are not found"
    assert [p["matched"] for p in answer["passages"]] == sorted((p["matched"] for p in answer["passages"]), reverse=True)
    every_text = " ".join(p["text"] for p in answer["passages"])
    assert "checked every evacuation drill" not in every_text and "west stairs" not in every_text
    assert {p["procedure"]["code"] for p in at_b["passages"]} == {"SOP-0003"}, "that site's, and the ones for every site"
    assert "SOP-0003" not in {p["procedure"]["code"] for p in held["passages"]}, "held to site A"
    assert nothing["passages"] == [] and nothing["nothing"] == library.NOTHING and nothing["words"] == ["helicopter", "landing"]
    assert empty["passages"] == [] and empty["words"] == [] and "words you would look for" in empty["nothing"]


def test_the_search_composes_nothing():
    source = Path(library.__file__).read_text(encoding="utf-8")
    for name in ("anthropic", "openai", "httpx", "requests", "llm"):
        assert f"import {name}" not in source and f"from {name}" not in source, name
    assert "INSERT INTO" not in source and "UPDATE " not in source and "DELETE FROM" not in source, "it reads"
    assert '"text": r["body"]' in source, "a passage is given as it is stored"
    router = Path(api.__file__).read_text(encoding="utf-8")
    assert "UPDATE sop_passages" not in router and "DELETE FROM sop_passages" not in router


# ─── F. The procedure beside an incident and a situation ─────────────────────

async def test_the_procedure_for_an_incident_is_the_one_for_its_kind_at_its_site():
    w, other = await _world(), await _world()
    t, now = w["tenant"], datetime.now(timezone.utc)
    ids = {k: uuid.uuid4() for k in ("cam_a", "cam_b", "fire", "by_hand", "at_b", "odd", "situation", "event")}
    await _run([
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Hall')", {"i": ids["cam_a"], "t": t, "s": w["site_a"]}),
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Yard')", {"i": ids["cam_b"], "t": t, "s": w["site_b"]}),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, alert_code) "
         "VALUES (:i,:t,:c,'Smoke in the hall','high','open','fire_smoke.detected')", {"i": ids["fire"], "t": t, "c": ids["cam_a"]}),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status) "
         "VALUES (:i,:t,:c,'Reported by a tenant','low','open')", {"i": ids["by_hand"], "t": t, "c": ids["cam_a"]}),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, alert_code) "
         "VALUES (:i,:t,:c,'Smoke in the yard','high','open','fire_smoke.detected')", {"i": ids["at_b"], "t": t, "c": ids["cam_b"]}),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, alert_code) "
         "VALUES (:i,:t,:c,'Crowd at the gate','low','open','crowd.density')", {"i": ids["odd"], "t": t, "c": ids["cam_a"]}),
        ("INSERT INTO security_situations (id, tenant_id, site_id, situation_number, title, severity, started_at, last_event_at) "
         "VALUES (:i,:t,:s,:n,'Activity at the fence','high',:at,:at)",
         {"i": ids["situation"], "t": t, "s": w["site_a"], "n": f"SIT-S-{uuid.uuid4().hex[:8]}", "at": now}),
        ("INSERT INTO security_events (id, tenant_id, site_id, source_type, source_table, source_id, event_type, "
         "occurred_at, severity, title) VALUES (:i,:t,:s,'CCTV_AI','alerts',:i,'intrusion.alert',:at,'high','Zone breach')",
         {"i": ids["event"], "t": t, "s": w["site_a"], "at": now}),
        ("INSERT INTO security_situation_events (tenant_id, situation_id, event_id, method, reason, confidence) "
         "VALUES (:t,:s,:e,'NEAR_POSITION','The same fence.',0.9)", {"t": t, "s": ids["situation"], "e": ids["event"]}),
    ])
    async with _client() as c:
        await _in_force(c, w, "Fire alarm, every site", "Call 995 and meet the fire engines at the main gate.",
                        site=None, types=["fire_smoke"])
        await _in_force(c, w)                                                          # site A's own
        await _in_force(c, w, "Fire at site B", "Use the yard hydrant.", site="site_b", types=["fire_smoke"])
        await _in_force(c, w, "Intruder", INTRUDER, site=None, category="incident_response", types=["intrusion"])
        await _write(c, w, "Fire, a draft", "Not approved.", who=ADMIN, types=["fire_smoke"])
        r = await c.get(f"{BASE}/for-incident/{ids['fire']}", headers=w["h"][GUARD])
        assert r.status_code == 200, r.text
        by_hand = (await c.get(f"{BASE}/for-incident/{ids['by_hand']}", headers=w["h"][GUARD])).json()
        untagged = (await c.get(f"{BASE}/for-incident/{ids['odd']}", headers=w["h"][GUARD])).json()
        at_b = (await c.get(f"{BASE}/for-incident/{ids['at_b']}", headers=w["h"][ADMIN])).json()
        assert (await c.get(f"{BASE}/for-incident/{ids['at_b']}", headers=w["h"][SUPERVISOR])).status_code == 404
        assert (await c.get(f"{BASE}/for-incident/{ids['fire']}", headers=other["h"][ADMIN])).status_code == 404
        assert (await c.get(f"{BASE}/for-incident/{uuid.uuid4()}", headers=w["h"][ADMIN])).status_code == 404
        situation = await c.get(f"{BASE}/for-situation/{ids['situation']}", headers=w["h"][OPERATOR])
        assert (await c.get(f"{BASE}/for-situation/{ids['situation']}", headers=other["h"][ADMIN])).status_code == 404
    found = r.json()
    assert found["incident_types"] == ["fire_smoke"] and found["why_none"] is None
    assert [p["title"] for p in found["procedures"]] == ["Fire alarm", "Fire alarm, every site"], \
        "the site's own first; not site B's, not the draft"
    first = found["procedures"][0]
    assert first["text"] == FIRE.strip() and first["for_types"] == ["fire_smoke"] and first["version"]["version_no"] == 1
    assert [p["heading"] for p in first["passages"]] == ["On hearing the alarm", "Evacuation", "CALLING THE FIRE SERVICE"]
    assert by_hand["procedures"] == [] and "raised by hand" in by_hand["why_none"]
    assert untagged["procedures"] == [] and "of the kind 'crowd'" in untagged["why_none"]
    assert [p["title"] for p in at_b["procedures"]] == ["Fire at site B", "Fire alarm, every site"]
    assert situation.status_code == 200 and situation.json()["incident_types"] == ["intrusion"]
    assert [p["title"] for p in situation.json()["procedures"]] == ["Intruder"]


# ─── G. The document as issued ───────────────────────────────────────────────

async def test_the_document_as_issued_is_attached_to_a_draft_and_read_once_the_version_is_in_force(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EMPLOYEE_DOCS_ROOT", str(tmp_path))
    w, other = await _world(), await _world()
    pdf = b"%PDF-1.4\n% the procedure as issued\n" + b"x" * 512
    async with _client() as c:
        made = await _write(c, w)
        v = made["versions"][0]["id"]
        url = f"{BASE}/versions/{v}/attachment"
        for name, data, why in (("plan.exe", pdf, "kind of file is not taken"), ("plan.pdf", b"", "is empty")):
            r = await c.post(url, headers=w["h"][SUPERVISOR], files={"file": (name, data, "application/octet-stream")})
            assert r.status_code == 422 and why in r.json()["detail"], name
        assert (await c.post(url, headers=w["h"][OPERATOR], files={"file": ("plan.pdf", pdf)})).status_code == 403
        assert (await c.get(url, headers=w["h"][SUPERVISOR])).status_code == 404, "nothing attached yet"
        up = await c.post(url, headers=w["h"][SUPERVISOR], files={"file": ("../../Fire plan 2026.pdf", pdf, "application/pdf")})
        assert up.status_code == 200, up.text
        assert (await c.get(url, headers=w["h"][GUARD])).status_code == 404, "a draft's document is the writers'"
        await c.post(f"{BASE}/versions/{v}/submit", headers=w["h"][SUPERVISOR])
        late = await c.post(url, headers=w["h"][SUPERVISOR], files={"file": ("other.pdf", pdf, "application/pdf")})
        assert late.status_code == 409 and "attached to a draft" in late.json()["detail"]
        await c.post(f"{BASE}/versions/{v}/approve", headers=w["h"][MANAGER])
        down = await c.get(url, headers=w["h"][GUARD])
        assert (await c.get(url, headers=other["h"][ADMIN])).status_code == 404
        one = (await c.get(f"{BASE}/versions/{v}", headers=w["h"][GUARD])).json()
    digest = hashlib.sha256(pdf).hexdigest()
    assert up.json() == {"attachment_name": "Fire plan 2026.pdf", "attachment_sha256": digest, "bytes": len(pdf)}
    assert down.status_code == 200 and down.content == pdf and down.headers["content-type"] == "application/pdf"
    assert "Fire%20plan%202026.pdf" in down.headers["content-disposition"]
    assert one["has_attachment"] and one["attachment_sha256"] == digest
    kept = list(tmp_path.rglob("*.pdf"))
    assert len(kept) == 1 and kept[0].parent.parent.name == "sop" and str(w["tenant"]) in str(kept[0]), \
        "under the organisation's own folder, whatever the file was called"
    (entry,) = await _audit(w, "sop.attach")
    assert entry["detail"]["sha256"] == digest and entry["detail"]["bytes"] == len(pdf)


# ─── H. What the application role and the database refuse ────────────────────

async def test_an_approved_version_and_its_passages_cannot_be_changed_by_the_application():
    w, other = await _world(), await _world()
    async with _client() as c:
        doc = await _in_force(c, w)
        draft = await _write(c, w, "Intruder", INTRUDER, types=["intrusion"])
    approved, drafting = doc["versions"][0]["id"], draft["versions"][0]["id"]
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        for statement, params, refusal in (
            ("UPDATE sop_versions SET body = 'Rewritten after approval' WHERE id = :v", {"v": approved}, "approved is not changed"),
            ("UPDATE sop_versions SET state = 'DRAFT' WHERE id = :v", {"v": approved}, "approved is not changed"),
            ("UPDATE sop_versions SET effective_until = now() + interval '1 day' WHERE id = :v", {"v": approved},
             "approved is not changed"),
            ("UPDATE sop_versions SET version_no = 9 WHERE id = :v", {"v": drafting}, "permission denied"),
            ("UPDATE sop_versions SET document_id = gen_random_uuid() WHERE id = :v", {"v": drafting}, "permission denied"),
            ("UPDATE sop_versions SET drafted_by_user_id = NULL WHERE id = :v", {"v": drafting}, "permission denied"),
            ("DELETE FROM sop_versions WHERE id = :v", {"v": drafting}, "permission denied"),
            ("UPDATE sop_passages SET body = 'Rewritten' WHERE version_id = :v", {"v": approved}, "permission denied"),
            ("DELETE FROM sop_passages WHERE version_id = :v", {"v": approved}, "permission denied"),
            ("UPDATE sop_documents SET code = 'SOP-9999' WHERE id = :d", {"d": doc["id"]}, "permission denied"),
            ("DELETE FROM sop_documents WHERE id = :d", {"d": doc["id"]}, "permission denied"),
            ("UPDATE sop_incident_types SET incident_type = 'weapon' WHERE document_id = :d", {"d": doc["id"]},
             "permission denied"),
        ):
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            with pytest.raises(DBAPIError, match=refusal):
                await db.execute(text(statement), params)
            await db.rollback()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(other["tenant"])})
        for table in TABLES:
            assert (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 0, table
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(text("INSERT INTO sop_documents (tenant_id, code, title) VALUES (:t, 'SOP-0099', 'Planted')"),
                             {"t": w["tenant"]})
        await db.rollback()
    for table in TABLES:
        row = (await _sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :n", {"n": table}))[0]
        assert row["relrowsecurity"] and row["relforcerowsecurity"], table
        can = (await _sql("SELECT has_table_privilege('svc_app', :n, 'DELETE') AS d, "
                          "has_table_privilege('svc_app', :n, 'UPDATE') AS u", {"n": table}))[0]
        assert can["d"] == (table == "sop_incident_types") and not can["u"], table
    assert "GRANT ALL" not in MIGRATION and MIGRATION.count("REVOKE ALL ON {table} FROM svc_app") == 1
    (still,) = await _sql("SELECT body FROM sop_versions WHERE id = :v", {"v": approved})
    assert still["body"] == FIRE.strip()


async def test_what_the_database_refuses_of_a_procedure():
    w = await _world()
    t = w["tenant"]
    (doc,) = await _sql("INSERT INTO sop_documents (tenant_id, code, title) VALUES (:t,'SOP-0001','Fire') RETURNING id", {"t": t})
    d = doc["id"]
    document = "INSERT INTO sop_documents (tenant_id, code, title{more}) VALUES (:t, :code, :title{values})"
    for code, title, more, values, constraint in (
        ("SOP-0001", "Again", "", "", "uq_sop_code"),
        ("SOP-0002", "  ", "", "", "ck_sop_title"),
        ("SOP-0002", "X", ", category", ", 'gossip'", "ck_sop_category"),
        ("SOP-0002", "X", ", is_retired", ", TRUE", "ck_sop_retired"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(document.format(more=more, values=values), {"t": t, "code": code, "title": title})
    version = "INSERT INTO sop_versions (tenant_id, document_id, version_no, body{more}) VALUES (:t, :d, :n, :b{values})"
    for n, body, more, values, constraint in (
        (0, "x", "", "", "ck_sopv_no"),
        (1, "  ", "", "", "ck_sopv_body"),
        (1, "x", ", state", ", 'PUBLISHED'", "ck_sopv_state"),
        (1, "x", ", state", ", 'APPROVED'", "ck_sopv_approved"),
        (1, "x", ", state, decided_at", ", 'REJECTED', now()", "ck_sopv_rejected"),
        (1, "x", ", effective_from, effective_until", ", now(), now() - interval '1 day'", "ck_sopv_period"),
        (1, "x", ", attachment_path", ", 'sop/x.pdf'", "ck_sopv_attachment"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(version.format(more=more, values=values), {"t": t, "d": d, "n": n, "b": body})
    (v1,) = await _sql(version.format(more="", values="") + " RETURNING id", {"t": t, "d": d, "n": 1, "b": "First"})
    with pytest.raises(DBAPIError, match="uq_sop_version"):
        await _sql(version.format(more="", values=""), {"t": t, "d": d, "n": 1, "b": "Again"})
    with pytest.raises(DBAPIError, match="uq_sop_version_open"):
        await _sql(version.format(more="", values=""), {"t": t, "d": d, "n": 2, "b": "A second draft at once"})
    passage = "INSERT INTO sop_passages (tenant_id, version_id, document_id, ordinal, body) VALUES (:t, :v, :d, :n, :b)"
    with pytest.raises(DBAPIError, match="ck_sop_passage"):
        await _sql(passage, {"t": t, "v": v1["id"], "d": d, "n": 1, "b": " "})
    await _sql(passage, {"t": t, "v": v1["id"], "d": d, "n": 1, "b": "Evacuate the building."})
    with pytest.raises(DBAPIError, match="uq_sop_passage"):
        await _sql(passage, {"t": t, "v": v1["id"], "d": d, "n": 1, "b": "Twice"})
    found = await _sql("SELECT 1 FROM sop_passages WHERE tsv @@ to_tsquery('english', 'evacuation')")
    assert len(found) >= 1, "a passage is found by its own words, in whatever form"
    with pytest.raises(DBAPIError, match="ck_sop_type"):
        await _sql("INSERT INTO sop_incident_types (tenant_id, document_id, incident_type) VALUES (:t, :d, 'Fire Smoke')",
                   {"t": t, "d": d})


async def test_a_person_who_leaves_does_not_take_an_approved_procedure_with_them():
    w = await _world()
    author, author_h = await _guard(w, "Leaving Supervisor", role=SUPERVISOR)
    async with _client() as c:
        made = (await c.post(f"{BASE}/documents", headers=author_h, json={
            "title": "Fire alarm", "category": "fire", "body": FIRE, "incident_types": ["fire_smoke"]})).json()
        v = made["versions"][0]["id"]
        await c.post(f"{BASE}/versions/{v}/submit", headers=author_h)
        assert (await c.post(f"{BASE}/versions/{v}/approve", headers=w["h"][MANAGER])).status_code == 200
    await _run([("DELETE FROM audit_logs WHERE user_id = :u", {"u": author}), ("DELETE FROM users WHERE id = :u", {"u": author})])
    (kept,) = await _sql("SELECT state, drafted_by_user_id, body FROM sop_versions WHERE id = :v", {"v": v})
    assert kept["state"] == "APPROVED" and kept["drafted_by_user_id"] is None and kept["body"] == FIRE.strip()
    async with _client() as c:
        still = (await c.post(f"{BASE}/ask", headers=w["h"][GUARD], json={"question": "fire panel"})).json()
    assert still["passages"] and still["passages"][0]["procedure"]["code"] == "SOP-0001"


def _needs(route) -> set[str]:
    found: set[str] = set()

    def walk(dep):
        if "require_permission" in getattr(dep.call, "__qualname__", ""):
            found.update(c.cell_contents for c in (dep.call.__closure__ or ()) if isinstance(c.cell_contents, str))
        for sub in dep.dependencies:
            walk(sub)

    for d in route.dependant.dependencies:
        walk(d)
    return found


def test_every_route_asks_for_what_it_should():
    served = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if path.startswith(BASE + "/") and getattr(route, "endpoint", None):
                for method in route.methods - {"HEAD"}:
                    served[(method, path.replace(":uuid", "").removeprefix(BASE))] = _needs(route)
    read, write, approve = {"sop:read"}, {"sop:read", "sop:write"}, {"sop:read", "sop:write", "sop:approve"}
    assert served == {
        ("GET", "/documents"): read, ("POST", "/documents"): write, ("GET", "/documents/{document_id}"): read,
        ("PATCH", "/documents/{document_id}"): write, ("PUT", "/documents/{document_id}/incident-types"): write,
        ("POST", "/documents/{document_id}/versions"): write,
        ("POST", "/documents/{document_id}/retire"): approve, ("POST", "/documents/{document_id}/restore"): approve,
        ("GET", "/versions/{version_id}"): read, ("PATCH", "/versions/{version_id}"): write,
        ("POST", "/versions/{version_id}/submit"): write, ("POST", "/versions/{version_id}/withdraw"): write,
        ("POST", "/versions/{version_id}/approve"): approve, ("POST", "/versions/{version_id}/reject"): approve,
        ("POST", "/versions/{version_id}/attachment"): write, ("GET", "/versions/{version_id}/attachment"): read,
        ("POST", "/ask"): read, ("GET", "/incident-types"): read,
        ("GET", "/for-incident/{incident_id}"): read, ("GET", "/for-situation/{situation_id}"): read,
    }
    assert not [m for m, _ in served if m == "DELETE"], "a procedure is retired, never removed"


async def test_who_holds_the_three_permissions_and_post_orders_are_as_they_were():
    rows = await _sql("SELECT p.code, array_agg(rp.role_id ORDER BY rp.role_id) AS roles FROM permissions p "
                      "JOIN role_permissions rp ON rp.permission_id = p.id WHERE p.code LIKE 'sop:%' GROUP BY p.code")
    assert {r["code"]: list(r["roles"]) for r in rows} == {
        "sop:read": [2, 3, 4, 5, 6, 8], "sop:write": [2, 3, 8], "sop:approve": [2, 8]}
    assert "post_orders" not in MIGRATION.split("def upgrade")[1], "this phase alters nothing of post orders"
    router = Path(api.__file__).read_text(encoding="utf-8")
    assert "post_orders" not in router.split('"""', 2)[2]
