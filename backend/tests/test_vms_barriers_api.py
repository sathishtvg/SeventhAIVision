"""Vehicle registry, barriers and VMS — HTTP + database level (isolated tenant).

Covers backend/app/routers/{watchlist,barriers,vms}.py and the migration-0076
trigger that derives watchlist_entries.list_type from category.

Complements test_decision_engine.py, which covers the pure decision logic and
the driver contract with no DB. This file is about the seams: does the trigger
actually fire, does the command log actually record, does RLS actually isolate,
and does the API refuse to hand back a stored device password.

Sections:
  A — Registry: categories, the derived list_type trigger, validation (7)
  B — Barriers: CRUD, credential safety, commands, audit log (8)
  C — VMS: form fields, custom-field validation, parking clock (6)
  D — Tenant isolation across all three new tables (3)
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant(role_id: int = 2):
    """Fresh tenant + user + token. Each test gets its own so RLS assertions
    mean something and tests can't contaminate each other."""
    from app.core.security import create_access_token

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    slug = f"vms-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"),
            {"id": tenant_id, "n": f"VMS Test {slug}", "s": slug},
        )
        await s.execute(
            text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                 "VALUES (:id, :t, :r, :e, 'hashed', 'VMS Tester')"),
            {"id": user_id, "t": tenant_id, "r": role_id,
             "e": f"vms-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return tenant_id, user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_site(tenant_id: uuid.UUID, name: str = "Test Site") -> uuid.UUID:
    site_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :t, :n)"),
            {"id": site_id, "t": tenant_id, "n": name},
        )
        await s.commit()
    await engine.dispose()
    return site_id


def _client(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


async def _list_type_of(tenant_id: uuid.UUID, plate: str) -> str:
    """Read list_type straight from the database, bypassing the API — the API
    could be echoing back what was sent rather than what was stored."""
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        v = (await s.execute(
            text("SELECT list_type FROM watchlist_entries "
                 "WHERE tenant_id = :t AND plate_number = :p"),
            {"t": tenant_id, "p": plate},
        )).scalar()
    await engine.dispose()
    return v


# ── A. Vehicle registry ───────────────────────────────────────────────────

async def test_create_vehicle_with_full_registry_fields():
    tid, _, token = await _seed_tenant()
    async with _client(token) as c:
        r = await c.post("/api/v1/watchlist/plates", json={
            "plate_number": "sgb1234x", "category": "vip",
            "owner_name": "Ms Lim", "company": "Aurora", "vehicle_type": "sedan",
            "vehicle_color": "black", "valid_to": "2027-12-31", "remarks": "Director",
        })
    assert r.status_code == 201
    body = r.json()
    assert body["plate_number"] == "SGB1234X", "plate must be normalised to upper case"
    assert body["category"] == "vip"
    assert body["owner_name"] == "Ms Lim"


@pytest.mark.parametrize("category,expected", [
    ("blacklist", "block"),
    ("vip", "allow"),
    ("staff", "allow"),
    ("watchlist", "allow"),
    ("unknown", "allow"),
])
async def test_trigger_derives_list_type_from_category(category, expected):
    """The LPR worker reads list_type on a hot path. It must always agree with
    the category an admin actually chose."""
    tid, _, token = await _seed_tenant()
    plate = f"TRG{uuid.uuid4().hex[:5].upper()}"
    async with _client(token) as c:
        r = await c.post("/api/v1/watchlist/plates",
                         json={"plate_number": plate, "category": category})
    assert r.status_code == 201
    assert await _list_type_of(tid, plate) == expected


async def test_trigger_overrules_a_contradictory_list_type():
    """A caller sending list_type='allow' alongside category='blacklist' must
    not be able to sneak a barred vehicle past the worker's coarse check."""
    tid, _, token = await _seed_tenant()
    async with _client(token) as c:
        r = await c.post("/api/v1/watchlist/plates", json={
            "plate_number": "SNEAK01", "category": "blacklist", "list_type": "allow",
        })
    assert r.status_code == 201
    assert await _list_type_of(tid, "SNEAK01") == "block"


async def test_trigger_refires_when_category_is_edited():
    tid, _, token = await _seed_tenant()
    async with _client(token) as c:
        eid = (await c.post("/api/v1/watchlist/plates",
                            json={"plate_number": "FLIP01", "category": "vip"})).json()["id"]
        assert await _list_type_of(tid, "FLIP01") == "allow"
        r = await c.put(f"/api/v1/watchlist/plates/{eid}", json={"category": "blacklist"})
    assert r.status_code == 200
    assert await _list_type_of(tid, "FLIP01") == "block"


async def test_unknown_category_rejected():
    _, _, token = await _seed_tenant()
    async with _client(token) as c:
        r = await c.post("/api/v1/watchlist/plates",
                         json={"plate_number": "BAD01", "category": "nonsense"})
    assert r.status_code == 422
    assert "unknown category" in r.json()["detail"]


async def test_inverted_validity_window_rejected():
    _, _, token = await _seed_tenant()
    async with _client(token) as c:
        r = await c.post("/api/v1/watchlist/plates", json={
            "plate_number": "BAD02", "category": "staff",
            "valid_from": "2027-01-01", "valid_to": "2026-01-01",
        })
    assert r.status_code == 422


async def test_registry_search_matches_owner_and_company():
    _, _, token = await _seed_tenant()
    async with _client(token) as c:
        await c.post("/api/v1/watchlist/plates", json={
            "plate_number": "SRCH01", "category": "contractor",
            "owner_name": "Kumar S", "company": "BuildRight Pte"})
        await c.post("/api/v1/watchlist/plates",
                     json={"plate_number": "SRCH02", "category": "staff"})
        by_company = (await c.get("/api/v1/watchlist/plates?search=buildright")).json()
        by_owner = (await c.get("/api/v1/watchlist/plates?search=kumar")).json()
        by_cat = (await c.get("/api/v1/watchlist/plates?category=staff")).json()
    assert [e["plate_number"] for e in by_company] == ["SRCH01"]
    assert [e["plate_number"] for e in by_owner] == ["SRCH01"]
    assert [e["plate_number"] for e in by_cat] == ["SRCH02"]


# ── B. Barriers ───────────────────────────────────────────────────────────

async def _make_barrier(c: AsyncClient, name="North Gate", vendor="simulator", **kw):
    r = await c.post("/api/v1/barriers", json={"name": name, "vendor": vendor, **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_barrier_and_reject_unknown_vendor():
    _, _, token = await _seed_tenant()
    async with _client(token) as c:
        b = await _make_barrier(c)
        assert b["vendor"] == "simulator"
        r = await c.post("/api/v1/barriers", json={"name": "X", "vendor": "acme9000"})
    assert r.status_code == 422
    assert "unsupported vendor" in r.json()["detail"]


async def test_device_password_is_never_returned():
    """The API must not become a credential-exfiltration route. Callers get a
    boolean saying whether one is configured, never the value."""
    _, _, token = await _seed_tenant()
    async with _client(token) as c:
        b = await _make_barrier(c, vendor="hikvision", host="10.0.0.9",
                                username="admin", password="s3cr3t-value")
        detail = (await c.get(f"/api/v1/barriers/{b['id']}")).json()
        listed = (await c.get("/api/v1/barriers")).json()
    assert b["has_credentials"] is True
    for payload in (b, detail, listed):
        assert "s3cr3t-value" not in str(payload)
        assert "password" not in str(payload)


async def test_open_command_succeeds_and_is_logged():
    _, uid, token = await _seed_tenant()
    async with _client(token) as c:
        b = await _make_barrier(c)
        r = await c.post(f"/api/v1/barriers/{b['id']}/command", json={"command": "open"})
        assert r.status_code == 200
        assert r.json()["succeeded"] is True
        assert r.json()["source"] == "operator"
        log = (await c.get(f"/api/v1/barriers/{b['id']}/commands")).json()
    assert len(log) == 1
    assert log[0]["command"] == "open"
    assert log[0]["issued_by_name"] == "VMS Tester"


async def test_status_reflects_the_last_command():
    _, _, token = await _seed_tenant()
    async with _client(token) as c:
        b = await _make_barrier(c)
        await c.post(f"/api/v1/barriers/{b['id']}/command", json={"command": "open"})
        assert (await c.get(f"/api/v1/barriers/{b['id']}/status")).json()["status"] == "open"
        await c.post(f"/api/v1/barriers/{b['id']}/command", json={"command": "close"})
        assert (await c.get(f"/api/v1/barriers/{b['id']}/status")).json()["status"] == "closed"


async def test_emergency_override_requires_a_reason():
    """An override bypasses every access rule; the stated reason is what makes
    it reviewable afterwards."""
    _, _, token = await _seed_tenant()
    async with _client(token) as c:
        b = await _make_barrier(c)
        bad = await c.post(f"/api/v1/barriers/{b['id']}/command",
                           json={"command": "emergency_override"})
        good = await c.post(f"/api/v1/barriers/{b['id']}/command",
                            json={"command": "emergency_override", "reason": "fire drill"})
        log = (await c.get(f"/api/v1/barriers/{b['id']}/commands")).json()
    assert bad.status_code == 422
    assert "requires a reason" in bad.json()["detail"]
    assert good.status_code == 200
    # Only the successful one is logged — the rejected call never reached a device.
    assert [x["command"] for x in log] == ["emergency_override"]
    assert log[0]["reason"] == "fire drill"


async def test_failed_command_is_still_recorded():
    """A device that refuses must leave a trace. Logging only successes would
    hide exactly the events an investigation cares about."""
    from app.services.barrier.simulator import force_failure, reset_simulator
    reset_simulator()
    _, _, token = await _seed_tenant()
    async with _client(token) as c:
        b = await _make_barrier(c)
        force_failure(b["id"], "boom motor jam")
        r = await c.post(f"/api/v1/barriers/{b['id']}/command", json={"command": "open"})
        log = (await c.get(f"/api/v1/barriers/{b['id']}/commands")).json()
    reset_simulator()
    assert r.status_code == 502, "operator must not be told a gate opened when it did not"
    assert len(log) == 1
    assert log[0]["succeeded"] is False
    assert log[0]["error"] == "boom motor jam"


async def test_unknown_command_rejected():
    _, _, token = await _seed_tenant()
    async with _client(token) as c:
        b = await _make_barrier(c)
        r = await c.post(f"/api/v1/barriers/{b['id']}/command", json={"command": "explode"})
    assert r.status_code == 422


async def test_deactivated_barrier_refuses_commands():
    _, _, token = await _seed_tenant()
    async with _client(token) as c:
        b = await _make_barrier(c)
        await c.delete(f"/api/v1/barriers/{b['id']}")
        r = await c.post(f"/api/v1/barriers/{b['id']}/command", json={"command": "open"})
    assert r.status_code == 409


# ── C. VMS: form fields + parking clock ───────────────────────────────────

async def test_dropdown_field_requires_choices():
    """A dropdown with nothing to pick is a dead control on the operator's
    screen — rejected at authoring time, not discovered at the gate."""
    _, _, token = await _seed_tenant()
    async with _client(token) as c:
        bad = await c.post("/api/v1/vms/form-fields", json={
            "field_key": "empty_dd", "label": "Bad", "field_type": "select", "options": []})
        good = await c.post("/api/v1/vms/form-fields", json={
            "field_key": "reason", "label": "Reason", "field_type": "select",
            "options": ["Delivery", "Meeting"], "is_required": True})
    assert bad.status_code == 422
    assert good.status_code == 201


async def test_duplicate_field_key_conflicts():
    """field_key is the JSON key answers are stored under; a duplicate would
    silently overwrite another field's answers."""
    _, _, token = await _seed_tenant()
    async with _client(token) as c:
        await c.post("/api/v1/vms/form-fields",
                     json={"field_key": "dup", "label": "One", "field_type": "text"})
        r = await c.post("/api/v1/vms/form-fields",
                         json={"field_key": "dup", "label": "Two", "field_type": "text"})
    assert r.status_code == 409


async def test_manual_entry_enforces_required_custom_field():
    tid, _, token = await _seed_tenant()
    site_id = await _seed_site(tid)
    async with _client(token) as c:
        await c.post("/api/v1/vms/form-fields", json={
            "field_key": "visit_reason", "label": "Reason for Visit",
            "field_type": "select", "options": ["Delivery", "Meeting"], "is_required": True})
        missing = await c.post("/api/v1/vms/entries/manual", json={
            "site_id": str(site_id), "full_name": "No Reason", "custom_fields": {}})
        invalid = await c.post("/api/v1/vms/entries/manual", json={
            "site_id": str(site_id), "full_name": "Bad Opt",
            "custom_fields": {"visit_reason": "Party"}})
        ok = await c.post("/api/v1/vms/entries/manual", json={
            "site_id": str(site_id), "full_name": "Jane Doe",
            "custom_fields": {"visit_reason": "Delivery"}})
    assert missing.status_code == 422 and "required" in missing.json()["detail"]
    assert invalid.status_code == 422 and "must be one of" in invalid.json()["detail"]
    assert ok.status_code == 201
    assert ok.json()["custom_fields"] == {"visit_reason": "Delivery"}


async def test_parking_clock_starts_only_with_a_vehicle():
    """A walk-in visitor has no parking to meter, so the clock must stay null —
    otherwise they would eventually be flagged for clamping a car they never
    brought."""
    tid, _, token = await _seed_tenant()
    site_id = await _seed_site(tid)
    async with _client(token) as c:
        with_car = await c.post("/api/v1/vms/entries/manual", json={
            "site_id": str(site_id), "full_name": "Driver", "vehicle_plate": "sjk9911z"})
        walk_in = await c.post("/api/v1/vms/entries/manual", json={
            "site_id": str(site_id), "full_name": "Walker"})
    assert with_car.json()["vehicle_entry_at"] is not None
    assert with_car.json()["vehicle_plate"] == "SJK9911Z"
    assert walk_in.json()["vehicle_entry_at"] is None


async def test_overstay_requires_an_allowance_to_be_set():
    """NULL allowance means this site does not meter parking. It must never be
    read as 'zero minutes free' — that would flag every arrival instantly."""
    tid, _, token = await _seed_tenant()
    site_id = await _seed_site(tid)
    async with _client(token) as c:
        await c.post("/api/v1/vms/entries/manual", json={
            "site_id": str(site_id), "full_name": "Driver", "vehicle_plate": "NOMETER1"})
        unmetered = (await c.get(f"/api/v1/vms/onsite?site_id={site_id}")).json()
        assert unmetered[0]["allowance_minutes"] is None
        assert unmetered[0]["is_overstayed"] is False
        assert (await c.get(
            f"/api/v1/vms/onsite?site_id={site_id}&overstayed_only=true")).json() == []

        await c.put(f"/api/v1/sites/{site_id}", json={"free_parking_minutes": 0})
        metered = (await c.get(f"/api/v1/vms/onsite?site_id={site_id}")).json()
    assert metered[0]["allowance_minutes"] == 0
    assert metered[0]["is_overstayed"] is True


async def test_site_vms_camera_binding_can_be_cleared():
    """Sending an explicit null is the only way to unbind a lane. The
    `is not None` pattern used by older site fields silently ignores it."""
    tid, _, token = await _seed_tenant()
    site_id = await _seed_site(tid)
    cam_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO cameras (id, tenant_id, site_id, name) "
                 "VALUES (:id, :t, :s, 'Gate Cam')"),
            {"id": cam_id, "t": tid, "s": site_id},
        )
        await s.commit()
    await engine.dispose()

    async with _client(token) as c:
        await c.put(f"/api/v1/sites/{site_id}",
                    json={"vms_enabled": True, "entry_lpr_camera_id": str(cam_id)})
        bound = [s for s in (await c.get("/api/v1/sites")).json() if s["id"] == str(site_id)][0]
        assert bound["entry_lpr_camera_id"] == str(cam_id)

        await c.put(f"/api/v1/sites/{site_id}", json={"entry_lpr_camera_id": None})
        cleared = [s for s in (await c.get("/api/v1/sites")).json() if s["id"] == str(site_id)][0]
    assert cleared["entry_lpr_camera_id"] is None


# ── D. Tenant isolation ───────────────────────────────────────────────────

async def test_barriers_are_tenant_isolated():
    _, _, token_a = await _seed_tenant()
    _, _, token_b = await _seed_tenant()
    async with _client(token_a) as a:
        b = await _make_barrier(a, name="Tenant A Gate")
    async with _client(token_b) as bc:
        assert (await bc.get("/api/v1/barriers")).json() == []
        # 404 not 403 — a scoped-out row must be indistinguishable from absent.
        assert (await bc.get(f"/api/v1/barriers/{b['id']}")).status_code == 404
        assert (await bc.post(f"/api/v1/barriers/{b['id']}/command",
                              json={"command": "open"})).status_code == 404


async def test_registry_is_tenant_isolated():
    _, _, token_a = await _seed_tenant()
    _, _, token_b = await _seed_tenant()
    async with _client(token_a) as a:
        await a.post("/api/v1/watchlist/plates",
                     json={"plate_number": "ISO001", "category": "vip"})
    async with _client(token_b) as bc:
        plates = [e["plate_number"] for e in (await bc.get("/api/v1/watchlist/plates")).json()]
    assert "ISO001" not in plates


async def test_visitor_form_fields_are_tenant_isolated():
    _, _, token_a = await _seed_tenant()
    _, _, token_b = await _seed_tenant()
    async with _client(token_a) as a:
        await a.post("/api/v1/vms/form-fields",
                     json={"field_key": "secret_field", "label": "A only", "field_type": "text"})
    async with _client(token_b) as bc:
        keys = [f["field_key"] for f in (await bc.get("/api/v1/vms/form-fields")).json()]
    assert "secret_field" not in keys
