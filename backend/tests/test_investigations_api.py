"""Smart investigation: a file of what somebody found, and why they were looking.

  A — Opening: a reason, a number, and what it was opened from
  B — What is in it: references read where the record lives, as the reader may see it
  C — Notes, setting aside, closing and reopening
  D — Who may, and at which sites
  E — What the application's role can and cannot do to a file
  F — The record of it: audit, the route table, the migration

Every request goes through the real app over ASGI, as svc_app with RLS
enforced.

The claims, each with tests: an investigation is never a way into a record the
reader could not open; an item is a reference and never a copy; nothing filed
is removed, only set aside with a reason; opening, closing and reopening each
need a reason and each is audited; and the application's role can neither
delete a file nor rewrite one.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.db.session import AsyncSessionLocal
from app.routers import investigations as api
from app.services import investigation_sources as sources
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _auth, _client, _sql, _world
from tests.test_investigation_search import BASE, _audit, _iso, _seed

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0143_investigations.py").read_text(encoding="utf-8")


def _ref(seed: dict, kind: str) -> dict:
    return {"kind": kind, "id": str(seed["ids"][kind]), "occurred_at": _iso(seed["at"][kind])}


async def _open(c, w: dict, who: int = ADMIN, expect: int = 201, **body) -> dict:
    body = {"title": "Lorry at the back fence", "reason": "Reported by the night supervisor.", **body}
    r = await c.post(BASE, headers=w["h"][who], json=body)
    assert r.status_code == expect, r.text
    return r.json()


async def _get(c, w: dict, file_id: str, who: int = ADMIN) -> dict:
    r = await c.get(f"{BASE}/{file_id}", headers=w["h"][who])
    assert r.status_code == 200, r.text
    return r.json()


async def _post(c, w: dict, path: str, body: dict, who: int = ADMIN):
    return await c.post(f"{BASE}/{path}", headers=w["h"][who], json=body)


# ─── A. Opening ──────────────────────────────────────────────────────────────

async def test_an_investigation_is_opened_with_a_reason_and_numbered_by_the_day():
    w = await _world()
    today = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%Y%m%d")
    async with _client() as c:
        first = await _open(c, w, site_id=str(w["site_a"]))
        second = await _open(c, w, MANAGER, title="  Missing pallets  ")
        assert (first["investigation_number"], second["investigation_number"]) == (
            f"INV-{today}-0001", f"INV-{today}-0002")
        assert (first["status"], first["records"], second["title"]) == ("OPEN", 0, "Missing pallets")
        for bad in ({"reason": ""}, {"reason": "why"}, {"title": "ab"}, {"reason": None}, {"colour": "red"}):
            assert (await c.post(BASE, headers=w["h"][ADMIN], json={
                "title": "Lorry at the back fence", "reason": "Reported by the night supervisor.",
                **bad})).status_code == 422, bad
        assert (await _open(c, w, expect=404, site_id=str(uuid.uuid4())))["detail"] == "Site not found"
        file = await _get(c, w, first["id"])
    assert (file["reason"], file["site_name"], file["opened_by_name"]) == (
        "Reported by the night supervisor.", "Factory A", f"Role {ADMIN} User")
    assert file["items"] == [] and file["counts"] == {"records": 0, "notes": 0, "set_aside": 0, "not_shown": 0}
    assert file["can_manage"] is True and file["closed_at"] is None


async def test_numbers_are_each_organisations_own():
    mine, theirs = await _world(), await _world()
    async with _client() as c:
        a = await _open(c, mine)
        b = await _open(c, theirs)
        assert a["investigation_number"] == b["investigation_number"], "the count is per organisation"
        assert (await c.get(f"{BASE}/{b['id']}", headers=mine["h"][ADMIN])).status_code == 404
        listed = (await c.get(BASE, headers=mine["h"][ADMIN])).json()
    assert [i["id"] for i in listed["items"]] == [a["id"]]


async def test_opened_from_an_incident_the_incident_and_its_alert_are_the_first_things_in_it():
    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        opened = await _open(c, w, OPERATOR, incident_id=str(seed["ids"]["INCIDENT"]))
        file = await _get(c, w, opened["id"])
        from_situation = await _open(c, w, situation_id=str(seed["ids"]["SITUATION"]))
        assert (await _open(c, w, expect=404, incident_id=str(uuid.uuid4())))["detail"] == "Incident not found"
        assert (await _open(c, w, expect=404, situation_id=str(uuid.uuid4())))["detail"] == "Situation not found"
    assert opened["records"] == 2 and file["incident_id"] == str(seed["ids"]["INCIDENT"])
    assert file["site_id"] == str(w["site_a"]), "the incident's site, since none was given"
    assert [(i["kind"], i["state"], i["note"]) for i in file["items"]] == [
        ("ALERT", "SHOWN", "What this investigation was opened from."),
        ("INCIDENT", "SHOWN", "What this investigation was opened from.")]
    assert file["items"][1]["record"]["title"] == "Incident A"
    assert from_situation["records"] == 1


async def test_an_investigation_is_not_a_way_into_an_incident_at_another_site():
    w = await _world()
    seed_b = await _seed(w, site="site_b", tag="B")
    async with _client() as c:
        refused = await _open(c, w, SUPERVISOR, expect=404, incident_id=str(seed_b["ids"]["INCIDENT"]))
        assert refused["detail"] == "Incident not found"
        assert (await _open(c, w, SUPERVISOR, expect=404, site_id=str(w["site_b"])))["detail"] == "Site not found"
        spanning = await _open(c, w, SUPERVISOR, expect=422)
        assert "Choose the site" in spanning["detail"]
        mine = await _open(c, w, SUPERVISOR, site_id=str(w["site_a"]))
        everywhere = await _open(c, w, ADMIN)
        other = await _open(c, w, ADMIN, site_id=str(w["site_b"]))
        listed = (await c.get(BASE, headers=w["h"][SUPERVISOR])).json()
        assert [i["id"] for i in listed["items"]] == [mine["id"]]
        for hidden in (everywhere, other):
            assert (await c.get(f"{BASE}/{hidden['id']}", headers=w["h"][SUPERVISOR])).status_code == 404
            r = await _post(c, w, f"{hidden['id']}/notes", {"note": "Let me in."}, SUPERVISOR)
            assert r.status_code == 404
        assert (await c.get(BASE, headers=w["h"][ADMIN])).json()["total"] == 3


# ─── B. What is in it ────────────────────────────────────────────────────────

async def test_records_are_filed_all_or_none_and_once():
    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        file = await _open(c, w)
        r = await _post(c, w, f"{file['id']}/items", {
            "records": [_ref(seed, "PLATE_READ"), _ref(seed, "VISITOR"), _ref(seed, "OCCURRENCE")],
            "note": "  The lorry, and who signed it in.  "})
        assert r.status_code == 201 and len(r.json()["added"]) == 3 and r.json()["already_filed"] == []

        ghost = {"kind": "ALERT", "id": str(uuid.uuid4()), "occurred_at": _iso(seed["at"]["ALERT"])}
        r = await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "ALERT"), ghost]})
        assert r.status_code == 404 and r.json()["detail"]["not_found"] == [{"kind": "ALERT", "id": ghost["id"]}]
        assert "Nothing was added" in r.json()["detail"]["message"]

        again = await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "VISITOR"), _ref(seed, "ALERT")]})
        assert again.json() == {"added": [{"kind": "ALERT", "id": str(seed["ids"]["ALERT"])}],
                                "already_filed": [{"kind": "VISITOR", "id": str(seed["ids"]["VISITOR"])}]}

        for bad in ({"records": []}, {"records": [{"kind": "ALERT", "id": "x", "occurred_at": _iso(seed["since"])}]},
                    {"records": [_ref(seed, "ALERT")] * 51}, {}):
            assert (await _post(c, w, f"{file['id']}/items", bad)).status_code == 422
        unknown = await _post(c, w, f"{file['id']}/items", {"records": [{**_ref(seed, "ALERT"), "kind": "NOTE"}]})
        assert unknown.status_code == 422 and "Unknown kind of record" in unknown.json()["detail"]

        got = await _get(c, w, file["id"])
    assert [i["kind"] for i in got["items"]] == ["ALERT", "PLATE_READ", "VISITOR", "OCCURRENCE"], "in the order it happened"
    assert got["counts"] == {"records": 4, "notes": 0, "set_aside": 0, "not_shown": 0}
    plate = got["items"][1]
    assert (plate["state"], plate["label"], plate["note"]) == ("SHOWN", "Number plate reads",
                                                               "The lorry, and who signed it in.")
    assert plate["record"]["title"] == "SGA1234B" and plate["record"]["camera_name"] == "Gate Camera A"
    assert plate["added_by_name"] == f"Role {ADMIN} User" and got["items"][0]["note"] is None
    listed = await _sql("SELECT count(*) AS n FROM investigation_items WHERE investigation_id = :i", {"i": file["id"]})
    assert listed[0]["n"] == 4


async def test_an_item_is_a_reference_and_holds_nothing_of_the_record():
    columns = {r["column_name"] for r in await _sql(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'investigation_items'")}
    assert columns == {"id", "tenant_id", "investigation_id", "kind", "ref_id", "occurred_at", "site_id", "note",
                       "added_by_user_id", "added_at", "set_aside_at", "set_aside_by_user_id", "set_aside_reason"}
    for copied in ("title", "summary", "subject", "label", "plate", "name", "snapshot", "path"):
        assert not any(copied in c for c in columns - {"set_aside_by_user_id"}), f"an item would copy the {copied}"

    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        file = await _open(c, w)
        await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "VISITOR"), _ref(seed, "OCCURRENCE")]})
        stored = await _sql("SELECT * FROM investigation_items WHERE investigation_id = :i", {"i": file["id"]})
        assert "Tan Wei Ming" not in str(stored) and "lorry" not in str(stored)

        # The visitor is erased — a data subject's request, say. The file does not keep them.
        await _sql("DELETE FROM visitor_logs WHERE id = :i", {"i": seed["ids"]["VISITOR"]})
        await _sql("UPDATE occurrence_book_entries SET body = 'Corrected: a white van.' WHERE id = :i",
                   {"i": seed["ids"]["OCCURRENCE"]})
        got = await _get(c, w, file["id"])
    visitor, entry = got["items"]
    assert (visitor["kind"], visitor["state"], visitor["record"]) == ("VISITOR", "NOT_AVAILABLE", None)
    assert "Tan Wei Ming" not in str(got)
    assert entry["record"]["summary"] == "Corrected: a white van.", "read where it lives, now"
    assert got["counts"]["not_shown"] == 1 and got["counts"]["records"] == 2


async def test_a_reader_sees_in_a_file_only_what_they_could_open_elsewhere():
    w = await _world()
    seed_a = await _seed(w, site="site_a", tag="A")
    async with _client() as c:
        file = await _open(c, w, site_id=str(w["site_a"]))
        await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed_a, "FACE_MATCH"), _ref(seed_a, "ALERT")]})
        as_admin = await _get(c, w, file["id"], ADMIN)
        as_viewer = await _get(c, w, file["id"], VIEWER)
    face = lambda got: next(i for i in got["items"] if i["kind"] == "FACE_MATCH")  # noqa: E731
    assert face(as_admin)["record"]["subject_label"] == "Lim Ah Kow A"
    assert face(as_viewer)["record"]["subject_label"] is None and "Lim" not in str(as_viewer), \
        "the watchlist name is its keeper's, in a file as in a search"
    assert as_viewer["can_manage"] is False and as_admin["can_manage"] is True

    held = frozenset(sources.PERMISSIONS)
    item = {"kind": "VISITOR"}
    assert api._state(item, {"id": "x"}, held) == "SHOWN"
    assert api._state(item, None, held) == "NOT_AVAILABLE"
    assert api._state(item, None, held - {"visitor:read"}) == "NOT_PERMITTED"
    assert api._state({"kind": "NOTE"}, None, frozenset()) == "NOTE"
    resolved = await _resolve_without(w, seed_a, "visitor:read")
    assert resolved == {}, "a record of a kind the reader may not read is not fetched at all"


async def _resolve_without(w: dict, seed: dict, permission: str) -> dict:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        return await sources.resolve(db, [("VISITOR", seed["ids"]["VISITOR"], seed["at"]["VISITOR"])],
                                     frozenset(sources.PERMISSIONS) - {permission}, None)


async def test_a_file_holds_so_many_entries_and_no_more(monkeypatch):
    w = await _world()
    seed = await _seed(w)
    monkeypatch.setattr(api, "MAX_ITEMS", 3)
    async with _client() as c:
        file = await _open(c, w)
        ok = await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "ALERT"), _ref(seed, "INCIDENT")]})
        full = await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "VISITOR"), _ref(seed, "DRONE")]})
    assert ok.status_code == 201 and full.status_code == 409 and "at most 3 entries" in full.json()["detail"]


# ─── C. Notes, setting aside, closing and reopening ──────────────────────────

async def test_a_note_is_part_of_the_file_and_sits_at_the_moment_it_is_about():
    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        file = await _open(c, w)
        await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "ALERT"), _ref(seed, "SENSOR")]})
        between = seed["at"]["ALERT"] + timedelta(seconds=30)
        r = await _post(c, w, f"{file['id']}/notes", {"note": "  The gate was already open here. ",
                                                      "occurred_at": _iso(between)}, OPERATOR)
        assert r.status_code == 201
        assert (await _post(c, w, f"{file['id']}/notes", {"note": "Spoke to the driver."})).status_code == 201
        for bad, code in (({"note": " "}, 422), ({"note": ""}, 422), ({"note": "Tomorrow", "occurred_at": _iso(
                datetime.now(timezone.utc) + timedelta(days=1))}, 422), ({"note": "x", "occurred_at": "2026-10-05T01:00:00"}, 422)):
            assert (await _post(c, w, f"{file['id']}/notes", bad)).status_code == code, bad
        got = await _get(c, w, file["id"])
    assert [(i["kind"], i["note"]) for i in got["items"]] == [
        ("ALERT", None), ("NOTE", "The gate was already open here."), ("SENSOR", None),
        ("NOTE", "Spoke to the driver.")]
    note = got["items"][1]
    assert (note["state"], note["label"], note["record"], note["added_by_name"]) == (
        "NOTE", "Note", None, f"Role {OPERATOR} User")
    assert got["counts"] == {"records": 2, "notes": 2, "set_aside": 0, "not_shown": 0}


async def test_an_entry_is_set_aside_with_a_reason_and_stays_in_the_file():
    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        file = await _open(c, w, site_id=str(w["site_a"]))
        await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "ALERT"), _ref(seed, "DRONE")]})
        drone = next(i for i in (await _get(c, w, file["id"]))["items"] if i["kind"] == "DRONE")
        path = f"{file['id']}/items/{drone['id']}/set-aside"
        for bad in ({}, {"reason": ""}, {"reason": "  "}, {"reason": "no"}):
            assert (await _post(c, w, path, bad)).status_code == 422, bad
        r = await _post(c, w, path, {"reason": "A different vehicle, on the other side of the site."}, SUPERVISOR)
        assert r.status_code == 200 and r.json() == {"set_aside": True}
        assert (await _post(c, w, path, {"reason": "Again."})).status_code == 409
        missing = await _post(c, w, f"{file['id']}/items/{uuid.uuid4()}/set-aside", {"reason": "Not there."})
        assert missing.status_code == 404
        other = await _open(c, w)
        wrong_file = await _post(c, w, f"{other['id']}/items/{drone['id']}/set-aside", {"reason": "Not this file."})
        assert wrong_file.status_code == 404, "an entry is set aside in the file it is in"

        got = await _get(c, w, file["id"])
        again = await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "DRONE")]})
        listed = (await c.get(BASE, headers=w["h"][ADMIN])).json()["items"]
    kept = next(i for i in got["items"] if i["kind"] == "DRONE")
    assert kept["set_aside_reason"] == "A different vehicle, on the other side of the site."
    assert kept["set_aside_by_name"] == f"Role {SUPERVISOR} User" and kept["set_aside_at"] and kept["record"]
    assert got["counts"] == {"records": 1, "notes": 0, "set_aside": 1, "not_shown": 0}
    assert again.json()["already_filed"] == [{"kind": "DRONE", "id": str(seed["ids"]["DRONE"])}], \
        "set aside is not gone: it is not filed a second time"
    assert next(i for i in listed if i["id"] == file["id"])["records"] == 1


async def test_closing_says_what_was_found_and_a_closed_file_is_not_changed():
    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        file = await _open(c, w)
        await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "ALERT")]})
        alert = (await _get(c, w, file["id"]))["items"][0]
        for bad in ({}, {"note": ""}, {"note": "ok"}, {"note": "     "}):
            assert (await _post(c, w, f"{file['id']}/close", bad)).status_code == 422, bad
        assert (await _post(c, w, f"{file['id']}/reopen", {"reason": "Not closed."})).status_code == 409

        closed = await _post(c, w, f"{file['id']}/close", {"note": " A contractor's lorry, booked in late. "}, MANAGER)
        assert closed.status_code == 200 and closed.json() == {"status": "CLOSED"}
        got = await _get(c, w, file["id"])
        assert (got["status"], got["closing_note"], got["closed_by_name"]) == (
            "CLOSED", "A contractor's lorry, booked in late.", f"Role {MANAGER} User")
        assert got["items"][-1]["note"] == "Closed: A contractor's lorry, booked in late."

        for path, body in ((f"{file['id']}/items", {"records": [_ref(seed, "DRONE")]}),
                           (f"{file['id']}/notes", {"note": "One more thing."}),
                           (f"{file['id']}/items/{alert['id']}/set-aside", {"reason": "Changed my mind."}),
                           (f"{file['id']}/close", {"note": "Closing it again."})):
            r = await _post(c, w, path, body)
            assert r.status_code == 409 and "closed" in r.json()["detail"], path

        assert (await _post(c, w, f"{file['id']}/reopen", {"reason": "  "})).status_code == 422
        reopened = await _post(c, w, f"{file['id']}/reopen", {"reason": "The driver's account does not match."})
        assert reopened.json() == {"status": "OPEN"}
        got = await _get(c, w, file["id"])
        assert (got["status"], got["closed_at"], got["closing_note"], got["closed_by_name"]) == (
            "OPEN", None, None, None)
        assert [i["note"] for i in got["items"] if i["kind"] == "NOTE"] == [
            "Closed: A contractor's lorry, booked in late.", "Reopened: The driver's account does not match."], \
            "how it was closed stays in the file"
        assert (await _post(c, w, f"{file['id']}/notes", {"note": "Asked for the delivery note."})).status_code == 201

        by_status = {s: (await c.get(BASE, headers=w["h"][ADMIN], params={"status": s})).json()["total"]
                     for s in ("OPEN", "CLOSED")}
    assert by_status == {"OPEN": 1, "CLOSED": 0}


async def test_the_list_is_filtered_by_status_site_owner_and_words():
    w = await _world()
    async with _client() as c:
        a = await _open(c, w, ADMIN, title="Lorry at the back fence", site_id=str(w["site_a"]))
        b = await _open(c, w, OPERATOR, title="Missing pallets", site_id=str(w["site_b"]))
        await _post(c, w, f"{b['id']}/close", {"note": "Found in bay four."})

        async def ids(**params) -> list[str]:
            r = await c.get(BASE, headers=w["h"][ADMIN], params=params)
            assert r.status_code == 200, r.text
            return [i["id"] for i in r.json()["items"]]

        assert await ids() == [b["id"], a["id"]], "most recently opened first"
        assert await ids(status="CLOSED") == [b["id"]] and await ids(status="OPEN") == [a["id"]]
        assert await ids(site_id=str(w["site_a"])) == [a["id"]]
        assert await ids(mine="true") == [a["id"]]
        assert await ids(q="PALLET") == [b["id"]]
        assert await ids(q=a["investigation_number"].lower()) == [a["id"]]
        assert (await c.get(BASE, headers=w["h"][ADMIN], params={"status": "PENDING"})).status_code == 422
        page = (await c.get(BASE, headers=w["h"][ADMIN], params={"limit": 1})).json()
    assert (page["total"], page["has_more"], len(page["items"])) == (2, True, 1)


# ─── D. Who may ──────────────────────────────────────────────────────────────

async def test_a_viewer_reads_and_files_nothing_and_a_guard_does_neither():
    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        file = await _open(c, w)
        await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "ALERT")]})
        item = (await _get(c, w, file["id"]))["items"][0]
        writes = (("", {"title": "Mine", "reason": "Because I want one."}),
                  (f"{file['id']}/items", {"records": [_ref(seed, "DRONE")]}),
                  (f"{file['id']}/notes", {"note": "A viewer's note."}),
                  (f"{file['id']}/items/{item['id']}/set-aside", {"reason": "A viewer's view."}),
                  (f"{file['id']}/close", {"note": "A viewer closing it."}),
                  (f"{file['id']}/reopen", {"reason": "A viewer reopening it."}))
        for path, body in writes:
            r = await c.post(f"{BASE}/{path}".rstrip("/"), headers=w["h"][VIEWER], json=body)
            assert r.status_code == 403 and r.json()["detail"] == "Missing permission: investigation:manage", path
            r = await c.post(f"{BASE}/{path}".rstrip("/"), headers=w["h"][GUARD], json=body)
            assert r.status_code == 403 and r.json()["detail"] == "Missing permission: investigation:read", path
        assert (await c.get(f"{BASE}/{file['id']}", headers=w["h"][VIEWER])).status_code == 200
        assert (await c.get(BASE, headers=w["h"][VIEWER])).json()["total"] == 1
        assert (await c.get(f"{BASE}/{file['id']}", headers=w["h"][GUARD])).status_code == 403
        for role in (1, 7):
            headers = _auth(uuid.uuid4(), w["tenant"], role)
            assert (await c.get(f"{BASE}/{file['id']}", headers=headers)).status_code == 403
            assert (await c.post(BASE, headers=headers, json=writes[0][1])).status_code == 403
        for role in (SUPERVISOR, OPERATOR, MANAGER):
            r = await _post(c, w, f"{file['id']}/notes", {"note": f"Seen by role {role}."}, role)
            assert r.status_code == (404 if role == SUPERVISOR else 201), "the supervisor is held to site A"
        after = await _get(c, w, file["id"])
    assert after["counts"] == {"records": 1, "notes": 2, "set_aside": 0, "not_shown": 0}, "nothing a viewer tried took"


def _dependencies(route) -> set[str]:
    found: set[str] = set()

    def walk(dep):
        name = getattr(dep.call, "__qualname__", "")
        if "require_permission" in name:
            found.update(c.cell_contents for c in (dep.call.__closure__ or ()) if isinstance(c.cell_contents, str))
        if name == "_a_person":
            found.add("a person")
        for sub in dep.dependencies:
            walk(sub)

    for d in route.dependant.dependencies:
        walk(d)
    return found


def test_every_route_needs_the_permission_and_a_person_and_every_change_needs_more():
    routes = []
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            if getattr(route, "path", "").startswith(BASE) and getattr(route, "endpoint", None):
                routes.append(route)
    seen = {(m, r.path) for r in routes for m in r.methods - {"HEAD"}}
    assert seen == {
        ("GET", f"{BASE}/sources"), ("POST", f"{BASE}/search"), ("GET", f"{BASE}/trail"), ("GET", BASE),
        ("POST", BASE), ("GET", f"{BASE}/{{investigation_id:uuid}}"),
        ("POST", f"{BASE}/{{investigation_id:uuid}}/items"), ("POST", f"{BASE}/{{investigation_id:uuid}}/notes"),
        ("POST", f"{BASE}/{{investigation_id:uuid}}/items/{{item_id:uuid}}/set-aside"),
        ("POST", f"{BASE}/{{investigation_id:uuid}}/close"), ("POST", f"{BASE}/{{investigation_id:uuid}}/reopen"),
    }
    reads = {f"{BASE}/sources", f"{BASE}/search", f"{BASE}/trail"}
    for route in routes:
        needs = _dependencies(route)
        assert {"investigation:read", "a person"} <= needs, route.path
        changes = "POST" in route.methods and route.path not in reads
        assert ("investigation:manage" in needs) == changes, f"{route.methods} {route.path}"
        assert "drone" not in route.path, "a path with that word in it is the drone licence's to gate"
        assert "DELETE" not in route.methods and "PUT" not in route.methods and "PATCH" not in route.methods, \
            "nothing in a file is removed or rewritten"


# ─── E. What the application's role can and cannot do ────────────────────────

async def test_the_application_role_can_neither_delete_a_file_nor_rewrite_one():
    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        file = await _open(c, w)
        await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "ALERT")]})
    refused = (
        "DELETE FROM investigations WHERE id = :f",
        "DELETE FROM investigation_items WHERE investigation_id = :f",
        "UPDATE investigations SET title = 'Rewritten' WHERE id = :f",
        "UPDATE investigations SET reason = 'A better reason' WHERE id = :f",
        "UPDATE investigations SET opened_by_user_id = NULL WHERE id = :f",
        "UPDATE investigations SET investigation_number = 'INV-1' WHERE id = :f",
        "UPDATE investigations SET site_id = NULL WHERE id = :f",
        "UPDATE investigation_items SET note = 'Rewritten' WHERE investigation_id = :f",
        "UPDATE investigation_items SET ref_id = gen_random_uuid() WHERE investigation_id = :f",
        "UPDATE investigation_items SET kind = 'DRONE' WHERE investigation_id = :f",
        "UPDATE investigation_items SET added_by_user_id = NULL WHERE investigation_id = :f",
        "UPDATE investigation_items SET occurred_at = now() WHERE investigation_id = :f",
        "TRUNCATE investigation_items",
        "TRUNCATE investigations CASCADE",
    )
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        for statement in refused:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(statement), {"f": file["id"]} if ":f" in statement else {})
            await db.rollback()
        # What it may change: that a file was closed, and that an entry was set aside.
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        await db.execute(text("UPDATE investigation_items SET set_aside_at = now(), set_aside_reason = 'Unrelated.' "
                              " WHERE investigation_id = :f"), {"f": file["id"]})
        await db.execute(text("UPDATE investigations SET status = 'CLOSED', closed_at = now(), "
                              "closing_note = 'Nothing in it.' WHERE id = :f"), {"f": file["id"]})
        await db.rollback()
    assert (await _sql("SELECT status FROM investigations WHERE id = :f", {"f": file["id"]}))[0]["status"] == "OPEN"


async def test_the_database_itself_refuses_a_file_without_its_reasons():
    w = await _world()
    file, item = uuid.uuid4(), uuid.uuid4()
    opened = ("INSERT INTO investigations (id, tenant_id, investigation_number, title, reason{more}) "
              "VALUES (:i, :t, :n, :title, :reason{values})")

    async def insert(more: str = "", values: str = "", **params):
        await _sql(opened.format(more=more, values=values), {
            "i": params.pop("i", uuid.uuid4()), "t": w["tenant"], "n": f"INV-X-{uuid.uuid4().hex[:8]}",
            "title": "A title", "reason": "A reason.", **params})

    await insert(i=file)
    for more, values, params, constraint in (
        ("", "", {"reason": "  "}, "ck_investigation_reason"),
        ("", "", {"title": " "}, "ck_investigation_title"),
        (", status", ", 'CLOSED'", {}, "ck_investigation_closed"),
        (", status, closed_at, closing_note", ", 'CLOSED', now(), ' '", {}, "ck_investigation_closed"),
        (", status, closed_at", ", 'OPEN', now()", {}, "ck_investigation_closed"),
        (", status", ", 'PENDING'", {}, "ck_investigation_(status|closed)"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await insert(more, values, **params)
    add = ("INSERT INTO investigation_items (id, tenant_id, investigation_id, kind, ref_id, occurred_at, note"
           "{more}) VALUES (:i, :t, :f, :k, :r, now(), :n{values})")
    for kind, ref, note, more, values, constraint in (
        ("ALERT", None, None, "", "", "ck_invitem_ref"),
        ("NOTE", uuid.uuid4(), "A note.", "", "", "ck_invitem_ref"),
        ("NOTE", None, " ", "", "", "ck_invitem_ref"),
        ("GOSSIP", uuid.uuid4(), None, "", "", "ck_invitem_kind"),
        ("ALERT", uuid.uuid4(), None, ", set_aside_at", ", now()", "ck_invitem_set_aside"),
        ("ALERT", uuid.uuid4(), None, ", set_aside_at, set_aside_reason", ", now(), ' '", "ck_invitem_set_aside"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(add.format(more=more, values=values), {"i": uuid.uuid4(), "t": w["tenant"], "f": file,
                                                              "k": kind, "r": ref, "n": note})
    record = uuid.uuid4()
    await _sql(add.format(more="", values=""), {"i": item, "t": w["tenant"], "f": file, "k": "ALERT", "r": record,
                                                "n": None})
    with pytest.raises(DBAPIError, match="uq_invitem_record"):
        await _sql(add.format(more="", values=""), {"i": uuid.uuid4(), "t": w["tenant"], "f": file, "k": "ALERT",
                                                    "r": record, "n": None})
    with pytest.raises(DBAPIError, match="uq_investigation_number"):
        await _sql("INSERT INTO investigations (tenant_id, investigation_number, title, reason) "
                   "SELECT tenant_id, investigation_number, 'Twice', 'The same number.' FROM investigations "
                   " WHERE id = :f", {"f": file})


async def test_a_file_is_its_organisations_own_at_the_database():
    mine, theirs = await _world(), await _world()
    async with _client() as c:
        file = await _open(c, theirs)
        await _post(c, theirs, f"{file['id']}/notes", {"note": "Theirs."})
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(mine["tenant"])})
        assert (await db.execute(text("SELECT count(*) FROM investigations"))).scalar() == 0
        assert (await db.execute(text("SELECT count(*) FROM investigation_items"))).scalar() == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(text(
                "INSERT INTO investigations (tenant_id, investigation_number, title, reason) "
                "VALUES (:t, 'INV-X-1', 'Planted', 'In somebody else''s organisation.')"), {"t": theirs["tenant"]})
        await db.rollback()
    for table in ("investigations", "investigation_items"):
        row = (await _sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :n",
                          {"n": table}))[0]
        assert row["relrowsecurity"] and row["relforcerowsecurity"], table


# ─── F. The record of it ─────────────────────────────────────────────────────

async def test_everything_done_to_a_file_is_audited_with_who_and_why():
    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        file = await _open(c, w, OPERATOR, incident_id=str(seed["ids"]["INCIDENT"]))
        await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "DRONE")]}, OPERATOR)
        await _post(c, w, f"{file['id']}/items", {"records": [_ref(seed, "DRONE")]}, OPERATOR)
        await _post(c, w, f"{file['id']}/notes", {"note": "The driver said he was lost."}, OPERATOR)
        drone = next(i for i in (await _get(c, w, file["id"]))["items"] if i["kind"] == "DRONE")
        await _post(c, w, f"{file['id']}/items/{drone['id']}/set-aside", {"reason": "Another vehicle."}, ADMIN)
        await _post(c, w, f"{file['id']}/close", {"note": "A wrong turn."}, ADMIN)
        await _post(c, w, f"{file['id']}/reopen", {"reason": "Seen again tonight."}, MANAGER)
        await _post(c, w, f"{file['id']}/close", {"note": ""}, ADMIN)
    expected = {
        "investigation.open": (OPERATOR, {"number": file["investigation_number"], "records": 2,
                                          "incident_id": str(seed["ids"]["INCIDENT"])}),
        "investigation.item.add": (OPERATOR, {"added": [{"kind": "DRONE", "id": str(seed["ids"]["DRONE"])}]}),
        "investigation.note.add": (OPERATOR, {}),
        "investigation.item.set_aside": (ADMIN, {"kind": "DRONE", "reason": "Another vehicle.",
                                                 "record_id": str(seed["ids"]["DRONE"])}),
        "investigation.close": (ADMIN, {"note": "A wrong turn."}),
        "investigation.reopen": (MANAGER, {"reason": "Seen again tonight."}),
    }
    for action, (role, detail) in expected.items():
        rows = await _audit(w, action)
        assert len(rows) == 1, f"{action}: once, and not for the request that changed nothing or was refused"
        row = rows[0]
        assert row["user_id"] == w["users"][role] and row["row_hash"], action
        assert (row["resource_type"], str(row["resource_id"])) == ("investigation", file["id"]), action
        assert row["detail"]["actor_role"] == role and row["detail"]["site_id"] == str(w["site_a"])
        assert row["detail"]["source"] == "user" and row["detail"]["result"] == "ok"
        assert detail.items() <= row["detail"].items(), (action, row["detail"])
    assert "lost" not in str(await _audit(w, "investigation.note.add")), "that a note was written, not what it said"


def test_the_code_and_the_database_agree_on_what_can_be_filed_and_what_can_change():
    listed = set(re.findall(r"'([A-Z_]+)'", re.search(r"^KINDS = \((.*?)\)$", MIGRATION, re.M | re.S).group(1)))
    assert listed == set(sources.KINDS) | {"NOTE"}, "the database and the code disagree on the kinds"
    assert "NOTE" not in sources.BY_KIND, "a note is not a source"
    changes = re.search(r'^INVESTIGATION_CHANGES = "(.*)"$', MIGRATION, re.M).group(1)
    assert set(changes.split(", ")) == {"status", "closed_at", "closed_by_user_id", "closing_note", "updated_at"}
    item_changes = re.search(r'^ITEM_CHANGES = "(.*)"$', MIGRATION, re.M).group(1)
    assert set(item_changes.split(", ")) == {"set_aside_at", "set_aside_by_user_id", "set_aside_reason"}
    assert MIGRATION.count("REVOKE ALL ON {table} FROM svc_app") == 1 and "GRANT ALL" not in MIGRATION
    assert 'down_revision = "0142"' in MIGRATION
    code = Path(api.__file__).read_text(encoding="utf-8")
    assert "DELETE FROM" not in code, "the API removes nothing"
    for column in re.findall(r"UPDATE investigations\s+SET (.*?)\s+WHERE", code, re.S):
        assert {c.split("=")[0].strip() for c in re.split(r",\s*(?![^()]*\))", column)} <= set(changes.split(", "))


async def test_the_permissions_are_there_and_held_by_the_roles_that_investigate():
    rows = await _sql("SELECT code, description, category FROM permissions WHERE code LIKE 'investigation:%' "
                      " ORDER BY code")
    assert [(r["code"], r["category"]) for r in rows] == [("investigation:manage", "investigation"),
                                                          ("investigation:read", "investigation")]
    held = await _sql("SELECT rp.role_id FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
                      " WHERE p.code LIKE 'investigation:%' AND rp.role_id IN (1, 5, 7)")
    assert held == [], "not the platform owner, not a guard, not a customer's client"
