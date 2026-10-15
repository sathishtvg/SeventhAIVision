"""Fleet vehicles, phase 1 of the GPS fleet upgrade (docs/plans/2026-10-14-gps-fleet-tracking-design.md).

  A — What a vehicle record takes, with nothing running
  B — Registering, finding and changing a vehicle
  C — Archive in place of delete, and restoring
  D — What the application role and the database refuse
  E — Sites and organisations
  F — Custom fields
  G — Groups
  H — Who may do what

Every request goes through the real app over ASGI, as svc_app with RLS enforced.

The claims: an administrator can archive a vehicle - from the fleet API and from
the older GPS delete - with a reason, an audit line and a 404 for no vehicle;
nothing the application does deletes a vehicle or its history, and the database
refuses a removal that would take history with it; a registration number is one
vehicle's per organisation, however it is spaced; somebody limited to sites sees
and changes only their sites' vehicles; one organisation never sees another's.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.db.session import AsyncSessionLocal
from app.services import fleet_vehicles as fv
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _auth, _client, _sql, _world
from tests.test_investigation_search import _audit

BASE = "/api/v1/fleet"


async def _vehicle(c, w, plate: str, *, site: str = "site_a", who: int = ADMIN, **extra) -> dict:
    r = await c.post(f"{BASE}/vehicles", headers=w["h"][who],
                     json={"name": f"Car {plate}", "plate_number": plate, "site_id": str(w[site]), **extra})
    assert r.status_code == 201, r.text
    return r.json()


# ─── A. What a vehicle record takes ──────────────────────────────────────────

def test_a_plate_is_compared_without_spaces_dashes_or_case():
    assert fv.plate_key("sba 1234-k") == "SBA1234K"
    assert fv.plate_key(" - ") is None and fv.plate_key(None) is None
    with pytest.raises(HTTPException) as no:
        fv.clean({"name": "Van", "plate_number": "--"}, creating=True, plate_required=True)
    assert no.value.status_code == 422


def test_a_record_refuses_what_it_does_not_have_and_needs_what_it_must():
    with pytest.raises(HTTPException) as unknown:
        fv.clean({"name": "Van", "colour": "red"}, creating=True, plate_required=False)
    assert "colour" in unknown.value.detail, "a misspelt field is refused, not silently lost"
    with pytest.raises(HTTPException):
        fv.clean({"name": "Van"}, creating=True, plate_required=True)
    with pytest.raises(HTTPException):
        fv.clean({"name": "Van", "plate_number": "SBA1", "service_status": "parked"}, creating=True, plate_required=True)
    assert fv.clean({"name": " Van ", "plate_number": "SBA1"}, creating=True, plate_required=True) == {
        "name": "Van", "plate_number": "SBA1", "vehicle_type": "patrol_car"}


def test_custom_values_are_checked_against_their_field():
    defs = [
        {"field_key": "fuel", "label": "Fuel", "field_type": "select", "options": ["Petrol", "Diesel"],
         "is_required": True, "is_active": True},
        {"field_key": "seats", "label": "Seats", "field_type": "number", "options": [], "is_required": False,
         "is_active": True},
        {"field_key": "insured", "label": "Insured", "field_type": "boolean", "options": [], "is_required": False,
         "is_active": True},
        {"field_key": "permit", "label": "Permit", "field_type": "date", "options": [], "is_required": False,
         "is_active": True},
        {"field_key": "old", "label": "Old tag", "field_type": "text", "options": [], "is_required": False,
         "is_active": False},
    ]
    assert fv.check_custom(defs, {"fuel": "Diesel", "seats": 5, "insured": True, "permit": "2026-12-31"}, None) == {
        "fuel": "Diesel", "seats": 5, "insured": True, "permit": "2026-12-31"}
    for bad in ({"fuel": "Hydrogen"}, {"fuel": "Diesel", "seats": "five"}, {"fuel": "Diesel", "insured": "yes"},
                {"fuel": "Diesel", "permit": "31/12/2026"}, {"fuel": "Diesel", "nickname": "x"}, {"seats": 4}):
        with pytest.raises(HTTPException):
            fv.check_custom(defs, bad, None)
    # A retired field keeps what it had and takes nothing new; null removes a value; the rest is kept.
    current = {"fuel": "Petrol", "old": "A-7", "seats": 4}
    assert fv.check_custom(defs, {"seats": None}, current) == {"fuel": "Petrol", "old": "A-7"}
    with pytest.raises(HTTPException):
        fv.check_custom(defs, {"old": "B-9"}, current)
    with pytest.raises(HTTPException):
        fv.check_custom(defs, {"fuel": None}, current)


# ─── B. Registering, finding and changing ────────────────────────────────────

async def test_a_vehicle_is_registered_found_and_changed_with_an_audit_line():
    w = await _world(licensed=False)
    async with _client() as c:
        v = await _vehicle(c, w, "SBA 1234 K", make="Toyota", model="Hilux", department="Patrol",
                           driver_name="K. Tan", driver_phone="+65 8123 4567", service_status="in_service",
                           notes="Spare key at gatehouse")
        assert (v["plate_number"], v["plate_key"], v["archived"]) == ("SBA 1234 K", "SBA1234K", False)
        # The same plate, spaced and cased otherwise, is the same plate.
        again = await c.post(f"{BASE}/vehicles", headers=w["h"][ADMIN],
                             json={"name": "Twin", "plate_number": "sba-1234k", "site_id": str(w["site_a"])})
        assert again.status_code == 409
        # A registration number is required on the fleet API.
        assert (await c.post(f"{BASE}/vehicles", headers=w["h"][ADMIN],
                             json={"name": "Nameless", "site_id": str(w["site_a"])})).status_code == 422
        await _vehicle(c, w, "SKZ 9", make="Honda")
        found = (await c.get(f"{BASE}/vehicles", headers=w["h"][ADMIN], params={"q": "sba1234"})).json()
        assert [x["id"] for x in found["items"]] == [v["id"]] and found["total"] == 1
        by_make = (await c.get(f"{BASE}/vehicles", headers=w["h"][ADMIN], params={"q": "hond"})).json()
        assert [x["plate_number"] for x in by_make["items"]] == ["SKZ 9"]
        page = (await c.get(f"{BASE}/vehicles", headers=w["h"][ADMIN],
                            params={"sort": "plate_number", "order": "desc", "limit": 1})).json()
        assert page["total"] == 2 and [x["plate_number"] for x in page["items"]] == ["SKZ 9"]
        assert (await c.get(f"{BASE}/vehicles", headers=w["h"][ADMIN], params={"sort": "id; DROP"})).status_code == 422

        r = await c.patch(f"{BASE}/vehicles/{v['id']}", headers=w["h"][SUPERVISOR],
                          json={"color": "White", "service_status": "maintenance"})
        assert r.status_code == 200 and r.json()["service_status"] == "maintenance"
        assert (await c.patch(f"{BASE}/vehicles/{v['id']}", headers=w["h"][ADMIN],
                              json={"plate_number": "SKZ-9"})).status_code == 409
        detail = (await c.get(f"{BASE}/vehicles/{v['id']}", headers=w["h"][VIEWER])).json()
        assert detail["driver_display"] == "K. Tan" and detail["may"] == {
            "edit": False, "archive": False, "manage_fields": False, "manage_devices": False, "manage_limits": False}
    created = await _audit(w, "fleet.vehicle.created")
    changed = await _audit(w, "fleet.vehicle.changed")
    assert len(created) == 2 and changed[0]["detail"] == {"changed": ["color", "service_status"]}
    assert str(changed[0]["user_id"]) == str(w["users"][SUPERVISOR])


# ─── C. Archive in place of delete ───────────────────────────────────────────

async def test_an_administrator_archives_a_vehicle_with_a_reason_and_can_restore_it():
    w = await _world(licensed=False)
    async with _client() as c:
        v = await _vehicle(c, w, "SBA 1 A")
        url = f"{BASE}/vehicles/{v['id']}"
        # Whoever may change a vehicle may not archive it; an administrator may, and must say why.
        assert (await c.post(f"{url}/archive", headers=w["h"][SUPERVISOR], json={"reason": "Sold"})).status_code == 403
        assert (await c.post(f"{url}/archive", headers=w["h"][ADMIN], json={"reason": " "})).status_code == 422
        r = await c.post(f"{url}/archive", headers=w["h"][ADMIN], json={"reason": "Sold at auction"})
        assert r.status_code == 200 and r.json()["archived"] and r.json()["archive_reason"] == "Sold at auction"
        assert (await c.post(f"{url}/archive", headers=w["h"][MANAGER], json={"reason": "Again"})).status_code == 409
        # Off the lists, not gone: the archived view has it, and a change to it is refused until it is restored.
        listed = (await c.get(f"{BASE}/vehicles", headers=w["h"][ADMIN])).json()["items"]
        assert v["id"] not in [x["id"] for x in listed]
        archived = (await c.get(f"{BASE}/vehicles", headers=w["h"][ADMIN], params={"archived": "archived"})).json()
        assert [x["id"] for x in archived["items"]] == [v["id"]]
        assert (await c.patch(url, headers=w["h"][ADMIN], json={"color": "Red"})).status_code == 409
        # Its plate is free while it is archived; restoring it while another holds the plate is refused.
        other = await _vehicle(c, w, "sba1a")
        assert (await c.post(f"{url}/restore", headers=w["h"][ADMIN])).status_code == 409
        await c.patch(f"{BASE}/vehicles/{other['id']}", headers=w["h"][ADMIN], json={"plate_number": "SBA 2 B"})
        r = await c.post(f"{url}/restore", headers=w["h"][MANAGER])
        assert r.status_code == 200 and not r.json()["archived"] and r.json()["archive_reason"] is None
        history = (await c.get(f"{url}/history", headers=w["h"][VIEWER])).json()["items"]
        assert [h["action"] for h in history] == ["fleet.vehicle.restored", "fleet.vehicle.archived",
                                                  "fleet.vehicle.created"]
        assert (await c.post(f"{BASE}/vehicles/{uuid.uuid4()}/archive", headers=w["h"][ADMIN],
                             json={"reason": "Nothing"})).status_code == 404
    archived_line = (await _audit(w, "fleet.vehicle.archived"))[0]
    # It had no device to release (phase 2 records which device archiving released).
    assert archived_line["detail"] == {"plate_number": "SBA 1 A", "reason": "Sold at auction", "device_released": None}
    assert str(archived_line["user_id"]) == str(w["users"][ADMIN])


async def test_the_older_gps_delete_archives_and_says_when_there_is_nothing():
    w = await _world(licensed=False)
    async with _client() as c:
        v = await _vehicle(c, w, "SGP 7")
        gps = f"/api/v1/gps/vehicles/{v['id']}"
        assert (await c.delete(gps, headers=w["h"][SUPERVISOR])).status_code == 403
        assert (await c.delete(f"/api/v1/gps/vehicles/{uuid.uuid4()}", headers=w["h"][ADMIN])).status_code == 404
        assert (await c.put(gps, headers=w["h"][ADMIN], json={"is_active": False})).status_code == 409
        r = await c.delete(gps, headers=w["h"][ADMIN], params={"reason": "Written off"})
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert (await c.get(gps, headers=w["h"][ADMIN])).status_code == 404
    row = (await _sql("SELECT is_active, archived_at IS NOT NULL AS archived, archive_reason FROM vehicles WHERE id = :i",
                      {"i": v["id"]}))[0]
    assert (row["is_active"], row["archived"], row["archive_reason"]) == (False, True, "Written off")
    assert len(await _audit(w, "fleet.vehicle.archived")) == 1


# ─── D. What the application role and the database refuse ────────────────────

async def test_nothing_the_application_does_deletes_a_vehicle_or_its_history():
    w = await _world(licensed=False)
    async with _client() as c:
        v = await _vehicle(c, w, "SDEL 1")
    await _sql("INSERT INTO vehicle_journeys (tenant_id, vehicle_id) VALUES (:t, :v)", {"t": w["tenant"], "v": v["id"]})

    for statement in ("DELETE FROM vehicles WHERE id = :v", "DELETE FROM vehicle_journeys WHERE vehicle_id = :v",
                      "DELETE FROM geofence_events WHERE vehicle_id = :v", "DELETE FROM vehicle_positions WHERE vehicle_id = :v"):
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            assert not (await db.execute(text(
                "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(statement), {"v": v["id"]})
            await db.rollback()

    # Even a superuser removing the vehicle by hand is refused while it has history; it used to take it along.
    with pytest.raises(DBAPIError, match="foreign key"):
        await _sql("DELETE FROM vehicles WHERE id = :v", {"v": v["id"]})
    assert (await _sql("SELECT count(*) AS n FROM vehicle_journeys WHERE vehicle_id = :v", {"v": v["id"]}))[0]["n"] == 1
    # Removing a whole organisation still removes its vehicles and their history together.
    await _sql("DELETE FROM tenants WHERE id = :t", {"t": w["tenant"]})
    assert (await _sql("SELECT count(*) AS n FROM vehicles WHERE id = :v", {"v": v["id"]}))[0]["n"] == 0


async def test_history_links_no_longer_cascade():
    rows = await _sql("""
        SELECT conrelid::regclass::text AS tab, confdeltype::text AS confdeltype FROM pg_constraint
         WHERE confrelid = 'vehicles'::regclass AND contype = 'f'
           AND conrelid::regclass::text IN ('vehicle_journeys', 'geofence_events')
    """)
    assert sorted((r["tab"], r["confdeltype"]) for r in rows) == [("geofence_events", "a"), ("vehicle_journeys", "a")]


# ─── E. Sites and organisations ──────────────────────────────────────────────

async def test_somebody_limited_to_a_site_sees_and_changes_only_that_sites_vehicles():
    w = await _world(licensed=False)
    async with _client() as c:
        a = await _vehicle(c, w, "SITE A1", site="site_a")
        b = await _vehicle(c, w, "SITE B1", site="site_b")
        sup = w["h"][SUPERVISOR]
        listed = (await c.get(f"{BASE}/vehicles", headers=sup)).json()["items"]
        assert [x["id"] for x in listed] == [a["id"]]
        old = (await c.get("/api/v1/gps/vehicles", headers=sup)).json()
        assert [x["id"] for x in old] == [a["id"]], "the older GPS list keeps to the person's sites as well"
        assert {x["id"] for x in (await c.get("/api/v1/gps/dashboard", headers=sup)).json()["vehicles"]} == {a["id"]}
        for url in (f"{BASE}/vehicles/{b['id']}", f"/api/v1/gps/vehicles/{b['id']}",
                    f"/api/v1/gps/vehicles/{b['id']}/positions", f"{BASE}/vehicles/{b['id']}/history"):
            assert (await c.get(url, headers=sup)).status_code == 404, url
        assert (await c.patch(f"{BASE}/vehicles/{b['id']}", headers=sup, json={"color": "Blue"})).status_code == 404
        assert (await c.post(f"/api/v1/gps/vehicles/{b['id']}/positions", headers=w["h"][OPERATOR],
                             json={"lat": 1.3, "lon": 103.8})).status_code == 200, "the operator has no site limit"
        # A vehicle they register is at one of their sites - or they could not see it again.
        assert (await c.post(f"{BASE}/vehicles", headers=sup, json={"name": "X", "plate_number": "SUP 1"})).status_code == 422
        assert (await c.post(f"{BASE}/vehicles", headers=sup, json={
            "name": "X", "plate_number": "SUP 1", "site_id": str(w["site_b"])})).status_code == 422
        assert (await c.patch(f"{BASE}/vehicles/{a['id']}", headers=sup,
                              json={"site_id": str(w["site_b"])})).status_code == 422
        opts = (await c.get(f"{BASE}/options", headers=sup)).json()
        assert [s["id"] for s in opts["sites"]] == [str(w["site_a"])] and opts["site_required"] is True


async def test_one_organisation_never_sees_another_ones_vehicles():
    w, other = await _world(licensed=False), await _world(licensed=False)
    async with _client() as c:
        mine = await _vehicle(c, w, "SAME 1")
        theirs = await _vehicle(c, other, "SAME 1")  # the same plate in another organisation is another vehicle
        assert (await c.get(f"{BASE}/vehicles/{mine['id']}", headers=other["h"][ADMIN])).status_code == 404
        assert (await c.post(f"{BASE}/vehicles/{mine['id']}/archive", headers=other["h"][ADMIN],
                             json={"reason": "Not yours"})).status_code == 404
        assert [x["id"] for x in (await c.get(f"{BASE}/vehicles", headers=other["h"][ADMIN])).json()["items"]] == [
            theirs["id"]]
        # Another organisation's site is not one to put a vehicle at.
        assert (await c.post(f"{BASE}/vehicles", headers=other["h"][ADMIN], json={
            "name": "Y", "plate_number": "SITE X", "site_id": str(w["site_a"])})).status_code == 422


# ─── F. Custom fields ────────────────────────────────────────────────────────

async def test_custom_fields_are_the_organisations_and_a_retired_one_keeps_its_values():
    w = await _world(licensed=False)
    async with _client() as c:
        fields = f"{BASE}/vehicle-fields"
        assert (await c.post(fields, headers=w["h"][SUPERVISOR], json={
            "field_key": "fuel", "label": "Fuel", "field_type": "text"})).status_code == 403
        assert (await c.post(fields, headers=w["h"][ADMIN], json={
            "field_key": "fuel", "label": "Fuel", "field_type": "select"})).status_code == 422, "a choice needs options"
        fuel = (await c.post(fields, headers=w["h"][ADMIN], json={
            "field_key": "fuel", "label": "Fuel", "field_type": "select", "options": ["Petrol", "Diesel"],
            "is_required": True})).json()
        assert (await c.post(fields, headers=w["h"][MANAGER], json={
            "field_key": "fuel", "label": "Fuel again", "field_type": "text"})).status_code == 409
        assert (await c.post(f"{BASE}/vehicles", headers=w["h"][ADMIN], json={
            "name": "No fuel", "plate_number": "CF 1", "site_id": str(w["site_a"])})).status_code == 422
        assert (await c.post(f"{BASE}/vehicles", headers=w["h"][ADMIN], json={
            "name": "Bad fuel", "plate_number": "CF 1", "site_id": str(w["site_a"]),
            "custom_fields": {"fuel": "Coal"}})).status_code == 422
        v = await _vehicle(c, w, "CF 1", custom_fields={"fuel": "Diesel"})
        assert v["custom_fields"] == {"fuel": "Diesel"}
        assert (await c.post(f"{fields}/{fuel['id']}/retire", headers=w["h"][ADMIN])).status_code == 200
        # Retired: no longer asked for, its value kept and shown, no new value taken.
        later = await _vehicle(c, w, "CF 2")
        assert later["custom_fields"] == {}
        assert (await c.get(f"{BASE}/vehicles/{v['id']}", headers=w["h"][ADMIN])).json()["custom_fields"] == {"fuel": "Diesel"}
        assert (await c.patch(f"{BASE}/vehicles/{v['id']}", headers=w["h"][ADMIN],
                              json={"custom_fields": {"fuel": "Petrol"}})).status_code == 422
        assert (await c.patch(f"{fields}/{fuel['id']}", headers=w["h"][ADMIN],
                              json={"field_type": "text"})).status_code == 422, "a field's type stays"
        assert (await c.post(f"{fields}/{fuel['id']}/reinstate", headers=w["h"][ADMIN])).status_code == 200
    assert [r["detail"] for r in await _audit(w, "fleet.field.retired")] == [{"field_key": "fuel"}]


# ─── G. Groups ───────────────────────────────────────────────────────────────

async def test_a_group_takes_only_vehicles_its_owner_may_see_and_its_site_holds():
    w = await _world(licensed=False)
    async with _client() as c:
        a1, a2 = await _vehicle(c, w, "GA 1"), await _vehicle(c, w, "GA 2")
        b1 = await _vehicle(c, w, "GB 1", site="site_b")
        sup = w["h"][SUPERVISOR]
        group = (await c.post(f"{BASE}/groups", headers=sup,
                              json={"name": "Night patrol", "site_id": str(w["site_a"])})).json()
        url = f"{BASE}/groups/{group['id']}"
        assert (await c.post(f"{BASE}/groups", headers=w["h"][ADMIN], json={"name": "night PATROL"})).status_code == 409
        assert (await c.put(f"{url}/vehicles", headers=w["h"][ADMIN],
                            json={"vehicle_ids": [a1["id"], b1["id"]]})).status_code == 422, "site B's car at site A's group"
        assert (await c.put(f"{url}/vehicles", headers=sup, json={"vehicle_ids": [b1["id"]]})).status_code == 404
        r = await c.put(f"{url}/vehicles", headers=sup, json={"vehicle_ids": [a1["id"], a2["id"]]})
        assert r.json() == {"added": 2, "removed": 0, "vehicles": 2}
        assert (await c.put(f"{url}/vehicles", headers=sup, json={"vehicle_ids": [a1["id"]]})).json()["removed"] == 1
        filtered = (await c.get(f"{BASE}/vehicles", headers=sup, params={"group_id": group["id"]})).json()["items"]
        assert [x["id"] for x in filtered] == [a1["id"]]
        org_group = (await c.post(f"{BASE}/groups", headers=w["h"][ADMIN], json={"name": "Whole fleet"})).json()
        assert [g["name"] for g in (await c.get(f"{BASE}/groups", headers=sup)).json()["items"]] == ["Night patrol"]
        assert {g["name"] for g in (await c.get(f"{BASE}/groups", headers=w["h"][ADMIN])).json()["items"]} == {
            "Night patrol", "Whole fleet"}
        await c.post(f"{BASE}/vehicles/{a2['id']}/archive", headers=w["h"][ADMIN], json={"reason": "Crashed"})
        assert (await c.put(f"{BASE}/groups/{org_group['id']}/vehicles", headers=w["h"][ADMIN],
                            json={"vehicle_ids": [a2["id"]]})).status_code == 409
        assert (await c.post(f"{url}/retire", headers=sup)).status_code == 200
        assert (await c.patch(url, headers=sup, json={"name": "Day"})).status_code == 409


# ─── H. Who may do what ──────────────────────────────────────────────────────

async def test_who_may_read_change_archive_and_define():
    w = await _world(licensed=False)
    async with _client() as c:
        v = await _vehicle(c, w, "ROLE 1")
        for role in (GUARD, VIEWER, OPERATOR):
            assert (await c.get(f"{BASE}/vehicles", headers=w["h"][role])).status_code == 200
            assert (await c.post(f"{BASE}/vehicles", headers=w["h"][role], json={
                "name": "N", "plate_number": f"R{role}", "site_id": str(w["site_a"])})).status_code == 403
            assert (await c.post(f"{BASE}/vehicles/{v['id']}/archive", headers=w["h"][role],
                                 json={"reason": "No"})).status_code == 403
        for role in (1, 7):  # the platform owner and a customer's client see no organisation's fleet
            h = _auth(uuid.uuid4(), w["tenant"], role)
            assert (await c.get(f"{BASE}/vehicles", headers=h)).status_code == 403
        assert (await c.get(f"{BASE}/vehicles")).status_code == 401
        mine = (await c.get(f"{BASE}/vehicles", headers=w["h"][MANAGER])).json()["may"]
        assert mine == {"edit": True, "archive": True, "manage_fields": True, "manage_devices": True, "manage_limits": True}
