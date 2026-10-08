"""Work orders: raised by a person, or put forward by the platform for a person to accept.

  A — What may follow what, and how a suggestion is worded, with nothing running
  B — Raising an order by hand, and for a facility defect
  C — Doing the work: given to somebody, started, completed, cancelled
  D — What a schedule puts forward, and what the order's end does to the schedule
  E — What device health puts forward, once an organisation has asked for it
  F — The lists, the settings, and who reads what
  G — What the application role and the database refuse

Every request goes through the real app over ASGI, as svc_app with RLS
enforced. The scheduler's pass is run as the application's role.

The claims, each with tests: the platform suggests and a person raises the
work; a suggestion is made once, assigns nobody and tells nobody; suggestions
from health are off until asked for; an order that is over is not changed; a
facility defect is not changed by an order raised for it.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import maintenance as api
from app.services import maintenance as work
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world
from tests.test_incident_responses import _guard
from tests.test_investigation_search import _audit
from tests.test_security_assets import BASE as ASSETS, _camera, _hit, _look, _sensor

BASE = "/api/v1/maintenance"
ORDERS = f"{BASE}/work-orders"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0150_security_assets.py").read_text(encoding="utf-8")
CLIENT = 7
SGT = ZoneInfo("Asia/Singapore")


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _today() -> date:
    """Today where the organisation is: the tenants these tests make keep the default zone."""
    return datetime.now(SGT).date()


async def _asset(c, w: dict, name: str = "UPS rack 2", *, kind: str = "UPS", site: str | None = "site_a", **more) -> dict:
    r = await c.post(ASSETS, headers=w["h"][MANAGER], json={
        "kind": kind, "name": name, **({"site_id": str(w[site])} if site else {}), **more})
    assert r.status_code == 201, r.text
    return r.json()


async def _raise(c, w: dict, *, who: int = MANAGER, **body):
    return await c.post(ORDERS, headers=w["h"][who], json={"title": "Replace the battery", **body})


async def _raised(c, w: dict, **body) -> dict:
    r = await _raise(c, w, **body)
    assert r.status_code == 201, r.text
    return r.json()


async def _defect(w: dict, *, site: str = "site_a") -> uuid.UUID:
    did = uuid.uuid4()
    await _sql("INSERT INTO facility_defects (id, tenant_id, site_id, category, location, description, severity, status, "
               "reported_by_user_id) VALUES (:i,:t,:s,'cctv','Gate 1','Camera housing cracked','medium','open',:u)",
               {"i": did, "t": w["tenant"], "s": w[site], "u": w["users"][GUARD]})
    return did


async def _schedule(c, w: dict, *, due_in_days: int = 3, every: int = 90, lead: int = 7, **body) -> dict:
    r = await c.post(f"{BASE}/schedules", headers=w["h"][MANAGER], json={
        "title": "Quarterly battery test", "every_days": every, "lead_days": lead,
        "next_due_on": (_today() + timedelta(days=due_in_days)).isoformat(), **body})
    assert r.status_code == 201, r.text
    return r.json()


async def _orders(w: dict, **where) -> list[dict]:
    rows = await _sql("SELECT id, number, state, origin, title, suggestion_reason, schedule_id, asset_id, priority, kind, "
                      "due_at, assigned_to_user_id, accepted_by_user_id FROM maintenance_work_orders "
                      "WHERE tenant_id = :t ORDER BY number", {"t": w["tenant"]})
    return [r for r in rows if all(r[k] == v for k, v in where.items())]


async def _ask_for_suggestions(c, w: dict, hours: int = 4) -> None:
    r = await c.put(f"{BASE}/settings", headers=w["h"][ADMIN], json={"suggest_from_health": True,
                                                                      "suggest_after_hours": hours})
    assert r.status_code == 200, r.text


# ─── A. What may follow what, and how a suggestion is worded ─────────────────

def test_which_state_a_work_order_may_go_to_from_which():
    assert work.MOVES == {"SUGGESTED": ("OPEN", "DISMISSED"), "OPEN": ("IN_PROGRESS", "CANCELLED"),
                          "IN_PROGRESS": ("DONE", "CANCELLED"), "DONE": (), "CANCELLED": (), "DISMISSED": ()}
    assert set(work.MOVES) == set(work.STATES) and work.OVER == ("DONE", "CANCELLED", "DISMISSED")
    assert all(not work.MOVES[state] for state in work.OVER), "nothing follows an order that is over"
    # Only what the platform put forward can be dismissed; only what a person accepted can be done.
    assert "DONE" not in work.MOVES["SUGGESTED"] and "IN_PROGRESS" not in work.MOVES["SUGGESTED"]
    assert "assigns and tells nobody" in work.SUGGESTION_NOTE and work.DEFAULT_AFTER_HOURS == 4


def test_a_suggestion_from_health_says_what_was_read_and_nothing_of_its_cause():
    now = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)
    reading = {"kind": "CAMERA", "kind_label": "Camera", "device_id": "c1", "name": "Gate 2",
               "since": now - timedelta(hours=5, minutes=20), "since_is_when_first_read": False,
               "reasons": ["Every stream is offline."]}
    assert work.health_reason(reading, now, SGT) == (
        "Camera “Gate 2” has been down since 7 Oct 06:40 (5 hours). Every stream is offline.")
    first_read = {**reading, "since_is_when_first_read": True, "since": now - timedelta(hours=1, minutes=5)}
    # When the state began is not known: only since when the platform has been reading it.
    assert work.health_reason(first_read, now, SGT) == (
        "Camera “Gate 2” has been read as down since at least 7 Oct 10:55 (1 hour). Every stream is offline.")
    assert work.health_key(reading) == "health:CAMERA:c1:2026-10-06T22:40:00+00:00"
    assert work.health_key({**reading, "since": reading["since"].astimezone(SGT)}) == work.health_key(reading), \
        "one outage is one key, whatever zone its start is written in"
    code = Path(work.__file__).read_text(encoding="utf-8").split('"""', 2)[2].lower()
    for guess in ("faulty", "broken", "failed hardware", "power loss", "cable", "vandal", "tamper"):
        assert guess not in code, f"nothing here knows a device's {guess}"


# ─── B. Raising an order ─────────────────────────────────────────────────────

async def test_a_work_order_is_raised_by_hand_on_an_asset_and_is_open_at_once():
    w = await _world()
    tech, tech_h = await _guard(w, "Lee Technician", role=OPERATOR)
    due = datetime.now(timezone.utc) + timedelta(days=2)
    async with _client() as c:
        asset = await _asset(c, w)
        r = await _raise(c, w, asset_id=asset["id"], description=" Runtime is under five minutes. ", priority="HIGH",
                         due_at=_iso(due), assigned_to_user_id=str(tech))
        assert r.status_code == 201, r.text
        made = r.json()
        assert (made["number"], made["state"], made["origin"], made["kind"], made["priority"]) == (
            "WO-0001", "OPEN", "PERSON", "CORRECTIVE", "HIGH")
        assert made["site_id"] == str(w["site_a"]) and made["site_name"] == "Factory A", "the site is the asset's"
        assert (made["asset_code"], made["asset_name"]) == ("AST-0001", "UPS rack 2")
        assert made["description"] == "Runtime is under five minutes." and made["raised_by_name"] == "Role 8 User"
        assert made["assigned_to_user_name"] == "Lee Technician" and made["assigned_at"] and made["overdue"] is False
        assert made["suggestion_reason"] is None and made["note"] is None and "origin_key" not in made
        assert made["may"] == {"accept": False, "dismiss": False, "change": True, "start": True, "complete": True,
                               "cancel": True}
        mine = (await c.get(f"{ORDERS}/{made['id']}", headers=tech_h)).json()
        assert mine["assigned_to_me"] is True
        assert mine["may"] == {"accept": False, "dismiss": False, "change": False, "start": True, "complete": True,
                               "cancel": False}, "whoever has it does the work; they do not re-plan it"
        # A vendor who is not one of the organisation's people is named in words.
        second = await _raised(c, w, title="Annual service", kind="PREVENTIVE", site_id=str(w["site_b"]),
                               assigned_to_name=" PowerCo Services ")
        assert (second["number"], second["assigned_to_name"], second["assigned_to_user_name"]) == (
            "WO-0002", "PowerCo Services", None)
        assert second["assigned_at"] and second["asset_id"] is None
        bare = await _raised(c, w, title="Check the comms room")
        assert bare["site_id"] is None and bare["assigned_at"] is None and bare["number"] == "WO-0003"
    (entry,) = [e for e in await _audit(w, "maintenance.order.raise") if e["detail"]["number"] == "WO-0001"]
    assert entry["user_id"] == w["users"][MANAGER] and entry["detail"]["origin"] == "PERSON"


async def test_what_a_work_order_has_to_be_and_who_may_raise_one():
    w, other = await _world(), await _world()
    guard_user = w["users"][GUARD]
    client, _ = await _guard(w, "Building Owner", role=CLIENT)
    async with _client() as c:
        here, there = await _asset(c, w), await _asset(c, w, "B switch", kind="NETWORK", site="site_b")
        gone = await _asset(c, w, "Old UPS")
        await c.post(f"{ASSETS}/{gone['id']}/retire", headers=w["h"][MANAGER], json={"reason": "Replaced."})
        for body, status, words in (
            ({"title": "  "}, 422, "says what is to be done"), ({"title": None}, 422, None),
            ({"kind": "DESTRUCTIVE"}, 422, None), ({"priority": "WHENEVER"}, 422, None),
            ({"state": "DONE"}, 422, None), ({"origin": "HEALTH"}, 422, None), ({"number": "WO-0099"}, 422, None),
            ({"asset_id": str(uuid.uuid4())}, 422, "No asset of that id"),
            ({"asset_id": gone["id"]}, 422, "is retired"),
            ({"asset_id": here["id"], "site_id": str(w["site_b"])}, 422, "another site"),
            ({"defect_id": str(uuid.uuid4())}, 422, "No facility defect of that id"),
            ({"site_id": str(uuid.uuid4())}, 404, "Site not found"),
            ({"due_at": "2026-12-01T10:00:00"}, 422, "time zone"),
            ({"assigned_to_user_id": str(guard_user)}, 422, "cannot read work orders"),
            ({"assigned_to_user_id": str(client)}, 422, "cannot read work orders"),
            ({"assigned_to_user_id": str(other["users"][OPERATOR])}, 422, "not one of the organisation's people"),
        ):
            r = await _raise(c, w, **body)
            assert r.status_code == status, (body, r.text)
            if words:
                assert words in str(r.json()["detail"]), (body, r.text)
        for role in (OPERATOR, VIEWER, GUARD):
            assert (await _raise(c, w, who=role)).status_code == 403, role
        # Held to site A: says where, and it is one of theirs.
        assert (await _raise(c, w, who=SUPERVISOR)).status_code == 422
        assert (await _raise(c, w, who=SUPERVISOR, site_id=str(w["site_b"]))).status_code == 404
        assert (await _raise(c, w, who=SUPERVISOR, asset_id=there["id"])).status_code == 422
        assert (await _raise(c, w, who=SUPERVISOR, asset_id=here["id"])).status_code == 201
    key = TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key
    try:
        async with _client() as c:
            r = await c.post(ORDERS, json={"title": "By a key"})
            assert r.status_code == 403 and "not by an API key" in r.json()["detail"]
            assert (await c.get(ORDERS)).status_code == 200
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    assert len(await _orders(w)) == 1, "only the one that was raised properly"


async def test_an_order_raised_for_a_facility_defect_leaves_the_defect_as_it_was():
    w = await _world()
    defect, far = await _defect(w), await _defect(w, site="site_b")
    async with _client() as c:
        made = await _raised(c, w, title="Replace the camera housing", defect_id=str(defect))
        assert (made["origin"], made["defect_id"], made["site_id"]) == ("DEFECT", str(defect), str(w["site_a"]))
        again = await _raised(c, w, title="Order the part", defect_id=str(defect))
        assert again["number"] == "WO-0002", "more than one piece of work can be for one defect"
        assert (await _raise(c, w, who=SUPERVISOR, defect_id=str(far))).status_code == 422
        options = (await c.get(f"{BASE}/options", headers=w["h"][SUPERVISOR])).json()
        assert [d["id"] for d in options["defects"]] == [str(defect)], "held to site A"
        await c.post(f"{ORDERS}/{made['id']}/complete", headers=w["h"][MANAGER], json={"completion_note": "Housing replaced."})
    (same,) = await _sql("SELECT status, resolved_at, resolution_notes, referred_to FROM facility_defects WHERE id = :d",
                         {"d": defect})
    assert (same["status"], same["resolved_at"], same["resolution_notes"], same["referred_to"]) == ("open", None, None, None), \
        "the defect is resolved where it always was, by a person"


# ─── C. Doing the work ───────────────────────────────────────────────────────

async def test_whoever_has_an_order_starts_and_completes_it_and_says_what_was_done():
    w = await _world()
    tech, tech_h = await _guard(w, "Lee Technician", role=OPERATOR)
    async with _client() as c:
        asset = await _asset(c, w)
        made = await _raised(c, w, asset_id=asset["id"], assigned_to_user_id=str(tech))
        url = f"{ORDERS}/{made['id']}"
        # Somebody else who reads orders does not do this one's work.
        for step, body in (("start", None), ("complete", {"completion_note": "Done."})):
            r = await c.post(f"{url}/{step}", headers=w["h"][OPERATOR], json=body)
            assert r.status_code == 403 and "whoever has the order" in r.json()["detail"], step
        assert (await c.post(f"{url}/start", headers=w["h"][VIEWER])).status_code == 403
        going = await c.post(f"{url}/start", headers=tech_h)
        assert going.status_code == 200 and going.json()["state"] == "IN_PROGRESS"
        assert going.json()["started_by_name"] == "Lee Technician" and going.json()["started_at"]
        again = await c.post(f"{url}/start", headers=tech_h)
        assert again.status_code == 409 and again.json()["detail"] == "This work has already started."
        for body in ({}, {"completion_note": "  "}, {"completion_note": "Done.", "downtime_minutes": -5},
                     {"completion_note": "Done.", "cost": 120}):
            assert (await c.post(f"{url}/complete", headers=tech_h, json=body)).status_code == 422, body
        done = await c.post(f"{url}/complete", headers=tech_h, json={
            "completion_note": " Battery pack replaced; runtime now 22 minutes. ", "parts_used": "1 x RBC7",
            "downtime_minutes": 35})
        assert done.status_code == 200, done.text
        end = done.json()
        assert (end["state"], end["completion_note"], end["parts_used"], end["downtime_minutes"]) == (
            "DONE", "Battery pack replaced; runtime now 22 minutes.", "1 x RBC7", 35)
        assert end["completed_by_name"] == "Lee Technician" and end["completed_at"]
        assert not any(end["may"].values()), "nothing more is done to an order that is over"

        # Over: every change is refused, by whoever asks.
        for method, path, body in (("post", "start", None), ("post", "complete", {"completion_note": "Again."}),
                                   ("post", "cancel", {"reason": "No."}), ("post", "accept", None),
                                   ("post", "dismiss", {"reason": "No."}), ("patch", "", {"title": "Renamed"})):
            r = await getattr(c, method)(f"{url}/{path}".rstrip("/"), headers=w["h"][MANAGER], json=body)
            assert r.status_code == 409 and "It stands as it is" in r.json()["detail"], path

        # Done in one go: started and completed at the same moment, by the same person.
        quick = await _raised(c, w, title="Tighten the bracket", site_id=str(w["site_a"]))
        once = (await c.post(f"{ORDERS}/{quick['id']}/complete", headers=w["h"][SUPERVISOR],
                             json={"completion_note": "Tightened."})).json()
        assert once["state"] == "DONE" and once["started_at"] == once["completed_at"]
        assert once["started_by_name"] == once["completed_by_name"] == "Role 3 User"
    entries = await _audit(w, "maintenance.order.complete")
    assert (entries[0]["detail"]["downtime_minutes"], entries[0]["detail"]["was"]) == (35, "IN_PROGRESS")
    assert entries[1]["detail"]["was"] == "OPEN" and entries[1]["detail"]["from_schedule"] is False
    assert len(await _audit(w, "maintenance.order.start")) == 1
    # The asset's own record is a person's to change: completing work did not put it back in service or move it.
    (same,) = await _sql("SELECT status FROM asset_register WHERE id = :a", {"a": asset["id"]})
    assert same["status"] == "IN_SERVICE"


async def test_an_order_is_changed_given_to_somebody_and_cancelled_with_a_reason():
    w = await _world()
    tech, _ = await _guard(w, "Lee Technician", role=OPERATOR)
    due = datetime.now(timezone.utc) - timedelta(hours=3)
    async with _client() as c:
        asset, far = await _asset(c, w), await _asset(c, w, "B switch", kind="NETWORK", site="site_b")
        made = await _raised(c, w, asset_id=asset["id"])
        url = f"{ORDERS}/{made['id']}"
        r = await c.patch(url, headers=w["h"][SUPERVISOR], json={
            "title": " Replace both batteries ", "priority": "URGENT", "due_at": _iso(due),
            "assigned_to_user_id": str(tech), "description": None})
        assert r.status_code == 200, r.text
        now = r.json()
        assert (now["title"], now["priority"], now["assigned_to_user_name"], now["description"]) == (
            "Replace both batteries", "URGENT", "Lee Technician", None)
        assert now["overdue"] is True and now["assigned_at"]
        # Taken back from them: nobody has it, and nothing says when it was given.
        freed = (await c.patch(url, headers=w["h"][MANAGER], json={"assigned_to_user_id": None})).json()
        assert freed["assigned_to_user_id"] is None and freed["assigned_at"] is None
        for body, status, words in (
            ({}, 422, "Nothing to change."), ({"title": None}, 422, "has a title"), ({"title": " "}, 422, "says what is to be done"),
            ({"priority": None}, 422, "has a priority"), ({"state": "DONE"}, 422, None), ({"kind": "INSPECTION"}, 422, None),
            ({"asset_id": far["id"]}, 422, "another site"), ({"due_at": "2026-12-01T10:00:00"}, 422, "time zone"),
            ({"assigned_to_user_id": str(w["users"][GUARD])}, 422, "cannot read work orders"),
        ):
            r = await c.patch(url, headers=w["h"][MANAGER], json=body)
            assert r.status_code == status, (body, r.text)
            if words:
                assert words in str(r.json()["detail"]), (body, r.text)
        for role in (OPERATOR, VIEWER):
            assert (await c.patch(url, headers=w["h"][role], json={"title": "Theirs"})).status_code == 403
            assert (await c.post(f"{url}/cancel", headers=w["h"][role], json={"reason": "No."})).status_code == 403
        for body in ({}, {"reason": " "}):
            assert (await c.post(f"{url}/cancel", headers=w["h"][MANAGER], json=body)).status_code == 422
        gone = await c.post(f"{url}/cancel", headers=w["h"][MANAGER], json={"reason": "The UPS is being replaced whole."})
        assert gone.status_code == 200 and gone.json()["state"] == "CANCELLED"
        assert gone.json()["closed_reason"] == "The UPS is being replaced whole." and gone.json()["closed_by_name"] == "Role 8 User"
        assert (await c.delete(url, headers=w["h"][ADMIN])).status_code == 405, "there is no removing an order"
        listed = (await c.get(ORDERS, headers=w["h"][ADMIN])).json()
        assert listed["items"] == [] and listed["counts"] == {"suggested": 0, "open": 0, "in_progress": 0, "overdue": 0}
        assert [i["id"] for i in (await c.get(ORDERS, headers=w["h"][ADMIN], params={"state": "CANCELLED"})).json()["items"]] == [made["id"]]
    entry = (await _audit(w, "maintenance.order.update"))[0]
    assert entry["detail"]["changed"] == ["assigned_to_user_id", "description", "due_at", "priority", "title"]
    assert len(await _audit(w, "maintenance.order.cancel")) == 1


# ─── D. What a schedule puts forward ─────────────────────────────────────────

async def test_a_schedule_falling_due_puts_an_order_forward_once_for_a_person_to_accept():
    w = await _world()
    tech, _ = await _guard(w, "Lee Technician", role=OPERATOR)
    async with _client() as c:
        asset = await _asset(c, w)
        soon = await _schedule(c, w, asset_id=asset["id"], instructions="Run on battery for ten minutes.")
        later = await _schedule(c, w, title="Annual service", due_in_days=40, site_id=str(w["site_a"]))
        assert soon["days_until_due"] == 3 and soon["asset_code"] == "AST-0001" and soon["site_name"] == "Factory A"
        counts = await _look()
        assert counts["from_schedules"] >= 1
        (put,) = await _orders(w)
        assert (put["state"], put["origin"], put["kind"], put["title"]) == (
            "SUGGESTED", "SCHEDULE", "PREVENTIVE", "Quarterly battery test")
        assert put["schedule_id"] == uuid.UUID(soon["id"]) and put["asset_id"] == uuid.UUID(asset["id"])
        due = _today() + timedelta(days=3)
        assert put["suggestion_reason"] == (f"“Quarterly battery test” of AST-0001 UPS rack 2 is due on {due.day} "
                                            f"{due.strftime('%b %Y')}: it is scheduled every 90 days.")
        # Due by the end of that day, where the organisation is.
        assert put["due_at"].astimezone(SGT) == datetime.combine(due + timedelta(days=1), datetime.min.time(), SGT)
        # Nobody was given it, and nobody accepted it: it is a row in a list.
        assert put["assigned_to_user_id"] is None and put["accepted_by_user_id"] is None
        await _look()
        assert len(await _orders(w)) == 1, "the same due date is put forward once"

        url = f"{ORDERS}/{put['id']}"
        shown = (await c.get(url, headers=w["h"][VIEWER])).json()
        assert shown["note"] == work.SUGGESTION_NOTE and shown["description"] == "Run on battery for ten minutes."
        # Until a person accepts it, it is not work: nobody starts it, completes it, changes it or cancels it.
        for method, path, body, words in (
            ("post", "start", None, "Accept it first."), ("post", "complete", {"completion_note": "Done."}, "Accept it first."),
            ("patch", "", {"title": "Mine now"}, "Accept it first"), ("post", "cancel", {"reason": "No."}, "Dismiss it instead"),
        ):
            r = await getattr(c, method)(f"{url}/{path}".rstrip("/"), headers=w["h"][MANAGER], json=body)
            assert r.status_code == 409 and words in r.json()["detail"], path
        for role in (OPERATOR, VIEWER):
            assert (await c.post(f"{url}/accept", headers=w["h"][role])).status_code == 403
            assert (await c.post(f"{url}/dismiss", headers=w["h"][role], json={"reason": "No."})).status_code == 403
        yes = await c.post(f"{url}/accept", headers=w["h"][SUPERVISOR], json={"assigned_to_user_id": str(tech), "priority": "LOW"})
        assert yes.status_code == 200, yes.text
        assert (yes.json()["state"], yes.json()["accepted_by_name"], yes.json()["priority"]) == ("OPEN", "Role 3 User", "LOW")
        assert yes.json()["assigned_to_user_name"] == "Lee Technician" and yes.json()["note"] is None
        assert yes.json()["suggestion_reason"] == put["suggestion_reason"], "why it was put forward is kept as it was"
        assert (await c.post(f"{url}/accept", headers=w["h"][MANAGER])).status_code == 409

        # Done: the schedule runs again from the day the work was done.
        assert (await c.post(f"{url}/complete", headers=w["h"][MANAGER], json={"completion_note": "Held for 14 minutes."})).status_code == 200
        (after,) = [s for s in (await c.get(f"{BASE}/schedules", headers=w["h"][VIEWER])).json()["items"] if s["id"] == soon["id"]]
        assert after["last_done_on"] == _today().isoformat()
        assert after["next_due_on"] == (_today() + timedelta(days=90)).isoformat()
        await _look()
        assert len(await _orders(w)) == 1, "and nothing more is due yet"
        assert later["days_until_due"] == 40
    (entry,) = await _audit(w, "maintenance.order.accept")
    assert entry["user_id"] == w["users"][SUPERVISOR] and entry["detail"]["origin"] == "SCHEDULE"
    assert (await _audit(w, "maintenance.order.complete"))[0]["detail"]["from_schedule"] is True


async def test_setting_a_schedules_work_aside_moves_the_schedule_on_and_says_why():
    w = await _world()
    async with _client() as c:
        asset = await _asset(c, w)
        dismissed = await _schedule(c, w, asset_id=asset["id"], every=30)
        cancelled = await _schedule(c, w, title="Monthly lamp test", site_id=str(w["site_a"]), every=30, due_in_days=1)
        off = await _schedule(c, w, title="Switched off", site_id=str(w["site_a"]))
        retired_asset = await _asset(c, w, "Old UPS")
        of_retired = await _schedule(c, w, title="Of a retired asset", asset_id=retired_asset["id"])
        await c.patch(f"{BASE}/schedules/{off['id']}", headers=w["h"][MANAGER], json={"is_active": False})
        await c.post(f"{ASSETS}/{retired_asset['id']}/retire", headers=w["h"][MANAGER], json={"reason": "Replaced."})
        await _look()
        put = {o["title"]: o for o in await _orders(w)}
        assert set(put) == {"Quarterly battery test", "Monthly lamp test"}, "not one that is switched off, or of a retired asset"

        url = f"{ORDERS}/{put['Quarterly battery test']['id']}"
        for body in ({}, {"reason": "  "}):
            assert (await c.post(f"{url}/dismiss", headers=w["h"][MANAGER], json=body)).status_code == 422
        no = await c.post(f"{url}/dismiss", headers=w["h"][MANAGER], json={"reason": "The UPS is replaced next week."})
        assert no.status_code == 200 and no.json()["state"] == "DISMISSED"
        assert no.json()["closed_reason"] == "The UPS is replaced next week." and not any(no.json()["may"].values())
        assert (await c.post(f"{url}/accept", headers=w["h"][MANAGER])).status_code == 409, "what was dismissed stays dismissed"

        url = f"{ORDERS}/{put['Monthly lamp test']['id']}"
        await c.post(f"{url}/accept", headers=w["h"][MANAGER])
        late = await c.post(f"{url}/dismiss", headers=w["h"][MANAGER], json={"reason": "Too late."})
        assert late.status_code == 409 and "Cancel it instead" in late.json()["detail"]
        assert (await c.post(f"{url}/cancel", headers=w["h"][MANAGER], json={"reason": "Site closed that week."})).status_code == 200

        now = {s["id"]: s for s in (await c.get(f"{BASE}/schedules", headers=w["h"][VIEWER])).json()["items"]}
        # Each moved on a cycle from the date it was due; neither is recorded as done.
        assert now[dismissed["id"]]["next_due_on"] == (_today() + timedelta(days=33)).isoformat()
        assert now[cancelled["id"]]["next_due_on"] == (_today() + timedelta(days=31)).isoformat()
        assert now[dismissed["id"]]["last_done_on"] is None and now[cancelled["id"]]["last_done_on"] is None
        await _look()
        assert len(await _orders(w)) == 2, "and the dates that were set aside are not put forward again"
        assert of_retired["id"] in now and now[off["id"]]["is_active"] is False

        # A schedule moved by hand since is left where the person put it.
        moved = await _schedule(c, w, title="Moved by hand", site_id=str(w["site_a"]), every=30, due_in_days=2)
        await _look()
        (order,) = await _orders(w, title="Moved by hand")
        chosen = (_today() + timedelta(days=10)).isoformat()
        await c.patch(f"{BASE}/schedules/{moved['id']}", headers=w["h"][MANAGER], json={"next_due_on": chosen})
        await c.post(f"{ORDERS}/{order['id']}/dismiss", headers=w["h"][MANAGER], json={"reason": "Rescheduled."})
        (kept,) = [s for s in (await c.get(f"{BASE}/schedules", headers=w["h"][VIEWER])).json()["items"] if s["id"] == moved["id"]]
        assert kept["next_due_on"] == chosen


async def test_a_schedule_is_kept_by_whoever_manages_maintenance_and_switched_off_not_removed():
    w = await _world()
    async with _client() as c:
        here, there = await _asset(c, w), await _asset(c, w, "B switch", kind="NETWORK", site="site_b")
        url = f"{BASE}/schedules"
        good = {"title": "Quarterly battery test", "every_days": 90, "next_due_on": (_today() + timedelta(days=30)).isoformat()}
        for over, status, words in (
            ({"title": " "}, 422, "says what is to be done"), ({"every_days": 0}, 422, None), ({"every_days": 4000}, 422, None),
            ({"lead_days": 91}, 422, None), ({"next_due_on": "soon"}, 422, None), ({"is_active": False}, 422, None),
            ({"asset_id": str(uuid.uuid4())}, 422, "No asset of that id"),
            ({"asset_id": here["id"], "site_id": str(w["site_b"])}, 422, "another site"),
            ({"site_id": str(uuid.uuid4())}, 404, "Site not found"),
        ):
            r = await c.post(url, headers=w["h"][MANAGER], json={**good, **over})
            assert r.status_code == status, (over, r.text)
            if words:
                assert words in str(r.json()["detail"])
        for role in (OPERATOR, VIEWER, GUARD):
            assert (await c.post(url, headers=w["h"][role], json=good)).status_code == 403
        assert (await c.post(url, headers=w["h"][SUPERVISOR], json=good)).status_code == 422, "held to sites: says where"
        assert (await c.post(url, headers=w["h"][SUPERVISOR], json={**good, "asset_id": there["id"]})).status_code == 422
        mine = (await c.post(url, headers=w["h"][SUPERVISOR], json={**good, "asset_id": here["id"]})).json()
        far = (await c.post(url, headers=w["h"][MANAGER], json={**good, "title": "B switch firmware", "asset_id": there["id"]})).json()
        whole = (await c.post(url, headers=w["h"][MANAGER], json={**good, "title": "Review the register"})).json()
        assert (mine["lead_days"], mine["is_active"], mine["created_by_name"]) == (7, True, "Role 3 User")
        assert whole["site_id"] is None and far["site_name"] == "Factory B", "a schedule is at its asset's site"

        seen = [s["id"] for s in (await c.get(url, headers=w["h"][SUPERVISOR])).json()["items"]]
        assert seen == [mine["id"]], "held to site A: not site B's, and not the organisation's own"
        assert (await c.patch(f"{url}/{far['id']}", headers=w["h"][SUPERVISOR], json={"every_days": 1})).status_code == 404
        changed = await c.patch(f"{url}/{mine['id']}", headers=w["h"][SUPERVISOR], json={
            "every_days": 30, "lead_days": 3, "instructions": " Ten minutes on battery. ", "is_active": False})
        assert changed.status_code == 200, changed.text
        assert (changed.json()["every_days"], changed.json()["instructions"], changed.json()["is_active"]) == (
            30, "Ten minutes on battery.", False)
        for body in ({}, {"title": None}, {"every_days": None}, {"is_active": "no"}, {"asset_id": there["id"]}):
            assert (await c.patch(f"{url}/{mine['id']}", headers=w["h"][MANAGER], json=body)).status_code == 422, body
        assert (await c.patch(f"{url}/{mine['id']}", headers=w["h"][OPERATOR], json={"every_days": 1})).status_code == 403
        assert (await c.delete(f"{url}/{mine['id']}", headers=w["h"][ADMIN])).status_code == 405
        active = (await c.get(url, headers=w["h"][ADMIN], params={"active": "true"})).json()
        assert {s["id"] for s in active["items"]} == {far["id"], whole["id"]} and active["can_manage"] is True
        assert (await c.get(url, headers=w["h"][VIEWER])).json()["can_manage"] is False
    assert len(await _audit(w, "maintenance.schedule.create")) == 3
    assert (await _audit(w, "maintenance.schedule.update"))[0]["detail"]["changed"] == [
        "every_days", "instructions", "is_active", "lead_days"]


# ─── E. What device health puts forward ──────────────────────────────────────

async def test_health_puts_nothing_forward_until_an_organisation_asks_and_then_each_outage_once():
    w = await _world()
    dark = await _camera(w, "Loading bay", streams=("offline",))
    await _hit(w, dark, "stream_disconnected", minutes_ago=300)
    recent = await _camera(w, "Car park", streams=("offline",))
    await _hit(w, recent, "stream_disconnected", minutes_ago=30)
    await _camera(w, "Gate 1")
    async with _client() as c:
        asset = await _asset(c, w, "Loading bay camera", kind="CAMERA", site=None, device_id=str(dark))
        read = (await c.get(f"{BASE}/settings", headers=w["h"][VIEWER])).json()
        assert read == {"suggest_from_health": False, "suggest_after_hours": 4, "default_after_hours": 4,
                        "can_manage": False, "note": work.SUGGESTION_NOTE}
        counts = await _look()
        assert counts["from_health"] == 0 and await _orders(w) == [], "five hours down, and nobody has asked"

        await _ask_for_suggestions(c, w)
        await _look()
        (put,) = await _orders(w)
        assert (put["state"], put["origin"], put["kind"], put["priority"], put["title"]) == (
            "SUGGESTED", "HEALTH", "CORRECTIVE", "HIGH", "Loading bay: down")
        assert put["asset_id"] == uuid.UUID(asset["id"]) and put["assigned_to_user_id"] is None
        assert re.fullmatch(r"Camera “Loading bay” has been down since \d{1,2} \w{3} \d\d:\d\d \(5 hours\)\. "
                            r"Every stream is offline\.", put["suggestion_reason"]), put["suggestion_reason"]
        await _look()
        assert len(await _orders(w)) == 1, "the same outage is put forward once; half an hour down is not put forward"

        # Dismissed: the same outage is not put forward again, however long it lasts.
        url = f"{ORDERS}/{put['id']}"
        await c.post(f"{url}/dismiss", headers=w["h"][MANAGER], json={"reason": "The bay is closed for repainting."})
        await _look(datetime.now(timezone.utc) + timedelta(hours=30))
        still = await _orders(w)
        assert [o["state"] for o in still if o["title"] == "Loading bay: down"] == ["DISMISSED"]
        # By then the other camera has been down long enough, and is put forward in its turn.
        assert [o["state"] for o in still if o["title"] == "Car park: down"] == ["SUGGESTED"]

        # It comes back, and goes again: that is another outage.
        await _hit(w, dark, "stream_reconnected", minutes_ago=20)
        await _hit(w, dark, "stream_disconnected", minutes_ago=10)
        await _look(datetime.now(timezone.utc) + timedelta(hours=6))
        assert [o["state"] for o in await _orders(w) if o["title"] == "Loading bay: down"] == ["DISMISSED", "SUGGESTED"]

        # Switched off again, nothing more is put forward; what was, stays.
        off = await c.put(f"{BASE}/settings", headers=w["h"][ADMIN], json={"suggest_from_health": False})
        assert off.json()["suggest_from_health"] is False and off.json()["changed"] is True
        await _hit(w, recent, "stream_reconnected", minutes_ago=8)
        await _hit(w, recent, "stream_disconnected", minutes_ago=7)
        before = len(await _orders(w))
        await _look(datetime.now(timezone.utc) + timedelta(days=3))
        assert len(await _orders(w)) == before
    # Putting an order forward told nobody and raised nothing.
    (raised,) = await _sql("SELECT (SELECT count(*) FROM alerts WHERE tenant_id = :t) + "
                           "(SELECT count(*) FROM incidents WHERE tenant_id = :t) AS n", {"t": w["tenant"]})
    assert raised["n"] == 0
    for source in (Path(work.__file__), Path(api.__file__)):
        code = source.read_text(encoding="utf-8")
        assert "redis" not in code.lower() and "response_notify" not in code and "push" not in code.lower(), source.name


async def test_a_device_with_no_record_of_going_down_is_counted_from_when_it_was_first_read_so():
    w = await _world()
    silent = await _sensor(w, "Flood sensor", read_seconds_ago=7200)
    t0 = datetime.now(timezone.utc)
    async with _client() as c:
        await _ask_for_suggestions(c, w, hours=2)
        await _look(t0)
        assert await _orders(w) == [], "read as down for the first time: for how long is not known, so it is not assumed"
        await _look(t0 + timedelta(hours=1))
        assert await _orders(w) == []
        await _look(t0 + timedelta(hours=2, minutes=1))
        (put,) = await _orders(w)
        assert put["title"] == "Flood sensor: down" and put["asset_id"] is None
        assert put["suggestion_reason"].startswith("Sensor “Flood sensor” has been read as down since at least ")
        assert put["suggestion_reason"].endswith("(2 hours). No reading has arrived for more than twice the time one is expected in.")
        shown = (await c.get(f"{ORDERS}/{put['id']}", headers=w["h"][OPERATOR])).json()
        assert shown["site_name"] == "Factory A" and shown["may"]["accept"] is False and shown["note"] == work.SUGGESTION_NOTE
    assert silent


async def test_who_asks_for_suggestions_from_health_and_what_may_be_asked():
    w = await _world()
    url = f"{BASE}/settings"
    async with _client() as c:
        for body in ({}, {"suggest_from_health": "yes"}, {"suggest_from_health": True, "suggest_after_hours": 0},
                     {"suggest_from_health": True, "suggest_after_hours": 169}, {"suggest_from_health": True, "auto_accept": True}):
            assert (await c.put(url, headers=w["h"][ADMIN], json=body)).status_code == 422, body
        for role in (OPERATOR, VIEWER, GUARD):
            assert (await c.put(url, headers=w["h"][role], json={"suggest_from_health": True})).status_code == 403
        held = await c.put(url, headers=w["h"][SUPERVISOR], json={"suggest_from_health": True})
        assert held.status_code == 403 and "for the whole organisation" in held.json()["detail"]
        on = await c.put(url, headers=w["h"][MANAGER], json={"suggest_from_health": True, "suggest_after_hours": 12})
        assert on.json() == {"suggest_from_health": True, "suggest_after_hours": 12, "changed": True}
        same = await c.put(url, headers=w["h"][MANAGER], json={"suggest_from_health": True, "suggest_after_hours": 12})
        assert same.json()["changed"] is False
        assert (await c.get(url, headers=w["h"][SUPERVISOR])).json()["suggest_after_hours"] == 12
        assert (await c.get(url, headers=w["h"][GUARD])).status_code == 403
    first = (await _audit(w, "maintenance.settings"))[0]
    assert first["detail"]["was"] == {"suggest_from_health": False, "suggest_after_hours": 4}
    assert first["detail"]["now"] == {"suggest_from_health": True, "suggest_after_hours": 12}
    from app.core.config_keys import SETTING_VALIDATORS
    assert work.SUGGEST_KEY in SETTING_VALIDATORS and work.AFTER_KEY in SETTING_VALIDATORS
    # A value somebody put there another way is not trusted further than it can be read.
    await _sql("UPDATE tenant_settings SET setting_value = CAST(:v AS jsonb) WHERE tenant_id = :t AND setting_key = :k",
               {"v": '"often"', "t": w["tenant"], "k": work.AFTER_KEY})
    async with _client() as c:
        assert (await c.get(url, headers=w["h"][VIEWER])).json()["suggest_after_hours"] == work.DEFAULT_AFTER_HOURS


# ─── F. The lists, and who reads what ────────────────────────────────────────

async def test_the_list_puts_what_is_suggested_first_and_is_kept_to_the_sites_and_people_it_concerns():
    w, other = await _world(), await _world()
    tech, tech_h = await _guard(w, "Lee Technician", role=OPERATOR)
    await _sql("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:u,:s,:t)",
               {"u": tech, "s": w["site_a"], "t": w["tenant"]})
    past, ahead = datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc) + timedelta(days=5)
    async with _client() as c:
        asset = await _asset(c, w, "Percent % UPS")
        waits = await _raised(c, w, title="Replace the battery", asset_id=asset["id"], due_at=_iso(ahead))
        late = await _raised(c, w, title="Fix the gate lamp", site_id=str(w["site_a"]), due_at=_iso(past), kind="INSPECTION")
        doing = await _raised(c, w, title="Service the B switch", site_id=str(w["site_b"]), assigned_to_user_id=str(tech))
        await c.post(f"{ORDERS}/{doing['id']}/start", headers=tech_h)
        whole = await _raised(c, w, title="Review the register")
        done = await _raised(c, w, title="Tighten the bracket", site_id=str(w["site_a"]))
        await c.post(f"{ORDERS}/{done['id']}/complete", headers=w["h"][MANAGER], json={"completion_note": "Done."})
        await _schedule(c, w, site_id=str(w["site_a"]))
        await _look()
        (put,) = await _orders(w, state="SUGGESTED")

        async def titles(who=ADMIN, headers=None, **params) -> list:
            r = await c.get(ORDERS, headers=headers or w["h"][who], params=params)
            assert r.status_code == 200, r.text
            return [i["title"] for i in r.json()["items"]]

        everything = await c.get(ORDERS, headers=w["h"][ADMIN])
        assert [i["title"] for i in everything.json()["items"]] == [
            "Quarterly battery test", "Service the B switch", "Fix the gate lamp", "Replace the battery", "Review the register"], \
            "what is put forward, then what is being done, then what waits by when it is due; what is over is left out"
        assert everything.json()["counts"] == {"suggested": 1, "open": 3, "in_progress": 1, "overdue": 1}
        assert everything.json()["can_manage"] is True
        assert await titles(state="DONE") == ["Tighten the bracket"]
        assert await titles(state=["SUGGESTED", "IN_PROGRESS"]) == ["Quarterly battery test", "Service the B switch"]
        assert await titles(overdue="true") == ["Fix the gate lamp"] and await titles(kind="INSPECTION") == ["Fix the gate lamp"]
        assert await titles(origin="SCHEDULE") == ["Quarterly battery test"]
        assert await titles(asset_id=asset["id"]) == ["Replace the battery"]
        assert await titles(site_id=str(w["site_b"])) == ["Service the B switch"]
        assert await titles(q="WO-0002") == ["Fix the gate lamp"] and await titles(q="gate lamp") == ["Fix the gate lamp"]
        # The words as typed: a percent sign is looked for, not treated as "anything".
        assert await titles(q="%") == ["Replace the battery"] and await titles(q="_") == []
        assert (await c.get(ORDERS, headers=w["h"][ADMIN], params={"state": "BROKEN"})).status_code == 422
        page = (await c.get(ORDERS, headers=w["h"][VIEWER], params={"limit": 2})).json()
        assert len(page["items"]) == 2 and page["has_more"] is True and page["can_manage"] is False

        # Held to site A — and still reads the order at site B that they were given.
        assert await titles(headers=tech_h) == ["Quarterly battery test", "Service the B switch", "Fix the gate lamp",
                                                 "Replace the battery"]
        assert await titles(headers=tech_h, mine="true") == ["Service the B switch"]
        assert (await c.get(ORDERS, headers=tech_h)).json()["counts"] == {"suggested": 1, "open": 2, "in_progress": 1, "overdue": 1}
        assert (await c.get(f"{ORDERS}/{whole['id']}", headers=tech_h)).status_code == 404
        assert (await c.get(f"{ORDERS}/{doing['id']}", headers=tech_h)).json()["assigned_to_me"] is True
        assert await titles(who=SUPERVISOR) == ["Quarterly battery test", "Fix the gate lamp", "Replace the battery"]
        assert (await c.get(ORDERS, headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404
        assert (await c.get(f"{ORDERS}/{doing['id']}", headers=w["h"][SUPERVISOR])).status_code == 404

        options = (await c.get(f"{BASE}/options", headers=w["h"][MANAGER])).json()
        assert "Lee Technician" in [p["name"] for p in options["people"]]
        assert "Role 5 User" not in [p["name"] for p in options["people"]], "a guard does not read work orders"
        assert [a["asset_code"] for a in options["assets"]] == ["AST-0001"] and options["kinds"] == list(work.KINDS)
        assert (await c.get(f"{BASE}/options", headers=w["h"][OPERATOR])).status_code == 403
        assert (await c.get(f"{BASE}/options", headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404

        assert await titles(headers=other["h"][ADMIN]) == []
        assert (await c.get(f"{ORDERS}/{waits['id']}", headers=other["h"][ADMIN])).status_code == 404
        for path in (ORDERS, f"{BASE}/schedules", f"{BASE}/settings", f"{ORDERS}/{waits['id']}"):
            assert (await c.get(path, headers=w["h"][GUARD])).status_code == 403, path
            assert (await c.get(path)).status_code == 401, path
        assert put["id"] and late["id"]
    _, client_h = await _guard(w, "Building Owner", role=CLIENT)
    async with _client() as c:
        assert (await c.get(ORDERS, headers=client_h)).status_code == 403


async def test_the_pass_looks_at_every_organisation_and_one_that_fails_does_not_stop_the_rest():
    first, second = await _world(), await _world()
    async with _client() as c:
        for w in (first, second):
            await _schedule(c, w, site_id=str(w["site_a"]))
            await _camera(w)
    counts = await _look()
    assert counts["from_schedules"] >= 2 and counts["changes"] >= 2
    for w in (first, second):
        (put,) = await _orders(w)
        assert put["number"] == "WO-0001", "each organisation numbers its own"
    # Each organisation was a transaction of its own: nothing after the first was lost to the commit.
    for w in (first, second):
        (kept,) = await _sql("SELECT count(*) AS n FROM device_health_changes WHERE tenant_id = :t", {"t": w["tenant"]})
        assert kept["n"] == 1
    scheduler = (Path(work.__file__).resolve().parents[1] / "scheduler_main.py").read_text(encoding="utf-8")
    assert "counts = await maintenance.run(AsyncSessionLocal)" in scheduler
    assert 'DEVICE_HEALTH_INTERVAL = int(os.environ.get("DEVICE_HEALTH_INTERVAL_SECONDS", "300"))' in scheduler


# ─── G. What the application role and the database refuse ────────────────────

async def test_an_order_that_is_over_is_held_still_by_the_database_and_not_only_by_the_code():
    w = await _world()
    async with _client() as c:
        asset = await _asset(c, w)
        done = await _raised(c, w, asset_id=asset["id"])
        await c.post(f"{ORDERS}/{done['id']}/complete", headers=w["h"][MANAGER], json={"completion_note": "Replaced."})
        gone = await _raised(c, w, title="Cancelled one", site_id=str(w["site_a"]))
        await c.post(f"{ORDERS}/{gone['id']}/cancel", headers=w["h"][MANAGER], json={"reason": "Not needed."})
        live = await _raised(c, w, title="Still open", site_id=str(w["site_a"]))
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        for order in (done, gone):
            for statement in ("UPDATE maintenance_work_orders SET completion_note = 'Rewritten' WHERE id = :i",
                              "UPDATE maintenance_work_orders SET state = 'OPEN' WHERE id = :i",
                              "UPDATE maintenance_work_orders SET closed_reason = 'Rewritten', updated_at = now() WHERE id = :i"):
                await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
                with pytest.raises(DBAPIError, match="a work order that is over is not changed"):
                    await db.execute(text(statement), {"i": order["id"]})
                await db.rollback()
        for statement in (
            # Where it came from, why it was put forward, its number and who raised it are not rewritten.
            "UPDATE maintenance_work_orders SET number = 'WO-9999' WHERE id = :i",
            "UPDATE maintenance_work_orders SET origin = 'HEALTH', origin_key = 'x', suggestion_reason = 'x' WHERE id = :i",
            "UPDATE maintenance_work_orders SET raised_by_user_id = NULL, raised_at = now() WHERE id = :i",
            "UPDATE maintenance_work_orders SET kind = 'INSPECTION' WHERE id = :i",
            "UPDATE maintenance_work_orders SET site_id = NULL WHERE id = :i",
            "DELETE FROM maintenance_work_orders WHERE id = :i",
        ):
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(statement), {"i": live["id"]})
            await db.rollback()
    # Somebody who leaves is forgotten by name, and the order that is over stays whole.
    await _run([("DELETE FROM audit_logs WHERE user_id = :u", {"u": w["users"][MANAGER]}),
                ("DELETE FROM users WHERE id = :u", {"u": w["users"][MANAGER]})])
    (kept,) = await _sql("SELECT state, completion_note, completed_by_user_id FROM maintenance_work_orders WHERE id = :i",
                         {"i": done["id"]})
    assert (kept["state"], kept["completion_note"], kept["completed_by_user_id"]) == ("DONE", "Replaced.", None)
    assert "OLD.state IN ('DONE', 'CANCELLED', 'DISMISSED') AND pg_trigger_depth() = 1" in MIGRATION


async def test_what_the_database_refuses_of_a_work_order_and_a_schedule():
    w = await _world()
    t, s, now = w["tenant"], w["site_a"], datetime.now(timezone.utc)
    make = ("INSERT INTO maintenance_work_orders (tenant_id, number, site_id, title, kind, priority, state, origin, "
            "origin_key, suggestion_reason, accepted_at, started_at, completed_at, completion_note, closed_at, closed_reason, "
            "downtime_minutes) VALUES (:t, :number, :s, :title, :kind, :priority, :state, :origin, :key, :reason, :accepted, "
            ":started, :completed, :note, :closed, :why, :downtime)")
    base = {"t": t, "s": s, "number": "WO-0001", "title": "Replace the battery", "kind": "CORRECTIVE", "priority": "NORMAL",
            "state": "OPEN", "origin": "PERSON", "key": None, "reason": None, "accepted": None, "started": None,
            "completed": None, "note": None, "closed": None, "why": None, "downtime": None}
    suggested = {"origin": "HEALTH", "key": "health:CAMERA:x:1", "reason": "Camera down."}
    for over, constraint in (
        ({"title": " "}, "ck_wo_title"), ({"kind": "DESTRUCTIVE"}, "ck_wo_kind"), ({"priority": "WHENEVER"}, "ck_wo_priority"),
        ({"state": "PAUSED"}, "ck_wo_state"), ({"origin": "ROBOT"}, "ck_wo_origin"), ({"downtime": -1}, "ck_wo_downtime"),
        # Only the platform suggests, and what it suggests says what from.
        ({"origin": "HEALTH", "state": "SUGGESTED"}, "ck_wo_suggestion"), ({"key": "k", "reason": "Why."}, "ck_wo_suggestion"),
        ({"state": "SUGGESTED"}, "ck_wo_suggested"),
        ({"state": "DISMISSED", "closed": now, "why": "No."}, "ck_wo_suggested"),
        # What the platform suggested is work only once a person has accepted it.
        ({**suggested, "state": "OPEN"}, "ck_wo_accepted"),
        ({**suggested, "state": "DONE", "started": now, "completed": now, "note": "Done."}, "ck_wo_accepted"),
        ({"state": "IN_PROGRESS"}, "ck_wo_started"),
        ({"state": "DONE", "started": now, "completed": now}, "ck_wo_done"),
        ({"state": "DONE", "started": now, "completed": now, "note": "  "}, "ck_wo_done"),
        ({"state": "CANCELLED", "closed": now}, "ck_wo_closed"), ({"state": "CANCELLED", "why": "No."}, "ck_wo_closed"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(make, {**base, **over})
    await _sql(make, base)
    with pytest.raises(DBAPIError, match="uq_wo_number"):
        await _sql(make, base)
    await _sql(make, {**base, **suggested, "number": "WO-0002", "state": "SUGGESTED"})
    with pytest.raises(DBAPIError, match="uq_wo_origin"):
        await _sql(make, {**base, **suggested, "number": "WO-0003", "state": "SUGGESTED"})
    await _sql(make, {**base, **suggested, "key": "health:CAMERA:x:2", "number": "WO-0003", "state": "OPEN", "accepted": now})
    schedule = ("INSERT INTO maintenance_schedules (tenant_id, site_id, title, every_days, lead_days, next_due_on) "
                "VALUES (:t, :s, :title, :every, :lead, CURRENT_DATE)")
    for over, constraint in (({"title": " "}, "ck_msched_title"), ({"every": 0}, "ck_msched_every"),
                             ({"every": 3651}, "ck_msched_every"), ({"lead": 91}, "ck_msched_lead")):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(schedule, {"t": t, "s": s, "title": "Quarterly test", "every": 90, "lead": 7, **over})


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
    read, manage = {"maintenance:read"}, {"maintenance:read", "maintenance:manage"}
    one = "/work-orders/{order_id}"
    assert served == {
        ("GET", "/work-orders"): read, ("POST", "/work-orders"): manage, ("GET", "/options"): manage, ("GET", one): read,
        ("PATCH", one): manage, ("POST", f"{one}/accept"): manage, ("POST", f"{one}/dismiss"): manage,
        # Whoever has an order does its work, and they are somebody who reads: who it is, is judged in the handler.
        ("POST", f"{one}/start"): read, ("POST", f"{one}/complete"): read, ("POST", f"{one}/cancel"): manage,
        ("GET", "/schedules"): read, ("POST", "/schedules"): manage, ("PATCH", "/schedules/{schedule_id}"): manage,
        ("GET", "/settings"): read, ("PUT", "/settings"): manage,
    }
    assert not [m for m, _ in served if m == "DELETE"], "an order is cancelled and a schedule switched off; neither is removed"
    code = Path(api.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert not re.search(r"(INSERT INTO|UPDATE|DELETE FROM)\s+facility_defects\b", code), "a defect is not changed from here"
