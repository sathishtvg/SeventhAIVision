"""Drone patrol, phase 3: the API does what it promises, for the right people.

  A — The licence gates changes, not history
  B — Only an enrolled Super Admin can grant the licence
  C — Roles
  D — Site scoping and tenant isolation, through the API
  E — Fleet: secrets, credentials, history, maintenance, telemetry
  F — Planning: zones, routes, missions, schedules
  G — Events: a decision is recorded once, and stays made
  H — Audit and replay

Every request goes through the real app over ASGI, as svc_app with RLS enforced.
Fixtures are written directly with the admin connection so each test exercises
an endpoint rather than re-testing the endpoints that would have created them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose: app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app
from app.core.security import create_access_token
from app.services import drone_provider_registry as registry

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

SUPER_ADMIN, ADMIN, SUPERVISOR, OPERATOR, GUARD, VIEWER, MANAGER = 1, 2, 3, 4, 5, 6, 8


async def _run(statements: list[tuple[str, dict]]) -> list:
    """Several statements on one connection, committed together."""
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    out = []
    try:
        async with factory() as s:
            for stmt, params in statements:
                r = await s.execute(text(stmt), params)
                out.append(r.mappings().all() if r.returns_rows else [])
            await s.commit()
        return out
    finally:
        await engine.dispose()


async def _sql(stmt: str, params: dict | None = None) -> list:
    return (await _run([(stmt, params or {})]))[0]


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app), base_url="http://test")


def _auth(uid, tid, role) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(uid), str(tid), role)}"}


async def _world(*, licensed: bool = True, expires_at: datetime | None = None,
                 max_drones: int | None = None, max_missions: int | None = None,
                 max_sites: int | None = None) -> dict:
    """A tenant with two fenced sites 7 km apart, one user per role (the
    supervisor restricted to site A), and optionally the drone licence."""
    w: dict = {k: uuid.uuid4() for k in ("tenant", "site_a", "site_b")}
    stmts = [
        ("INSERT INTO tenants (id, name, slug) VALUES (:t,'Drone API Co',:s)",
         {"t": w["tenant"], "s": f"dapi-{w['tenant'].hex[:10]}"}),
        ("INSERT INTO sites (id, tenant_id, name, latitude, longitude, geofence_radius_meters) "
         "VALUES (:i,:t,'Factory A',1.3000,103.8000,300)", {"i": w["site_a"], "t": w["tenant"]}),
        ("INSERT INTO sites (id, tenant_id, name, latitude, longitude, geofence_radius_meters) "
         "VALUES (:i,:t,'Factory B',1.3500,103.8500,300)", {"i": w["site_b"], "t": w["tenant"]}),
    ]
    w["users"], w["h"] = {}, {}
    for role in (ADMIN, SUPERVISOR, OPERATOR, GUARD, VIEWER, MANAGER):
        uid = uuid.uuid4()
        w["users"][role] = uid
        w["h"][role] = _auth(uid, w["tenant"], role)
        stmts.append((
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
            "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
            {"i": uid, "t": w["tenant"], "r": role, "e": f"r{role}-{uid.hex[:8]}@drone.test",
             "n": f"Role {role} User"}))
    stmts.append(("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:u,:s,:t)",
                  {"u": w["users"][SUPERVISOR], "s": w["site_a"], "t": w["tenant"]}))
    if licensed:
        stmts.append((
            "INSERT INTO drone_module_licenses (tenant_id, is_enabled, licensed_at, expires_at, "
            "    max_drones, max_missions, max_sites) VALUES (:t,TRUE,now(),:e,:d,:m,:s)",
            {"t": w["tenant"], "e": expires_at, "d": max_drones, "m": max_missions, "s": max_sites}))
    await _run(stmts)
    return w


async def _drone(c: AsyncClient, w: dict, site: str = "site_a", code: str | None = None,
                 who: int = ADMIN, **extra) -> dict:
    r = await c.post("/api/v1/drones", headers=w["h"][who], json={
        "name": f"Drone {code or 'X'}", "code": code or f"D-{uuid.uuid4().hex[:6]}",
        "site_id": str(w[site]), **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def _event(w: dict, *, site: str = "site_a", status: str = "NEW") -> uuid.UUID:
    eid = uuid.uuid4()
    await _sql("INSERT INTO drone_events (id, tenant_id, site_id, module_type, detected_at, "
               "    risk_level, ai_confidence, risk_score, status) "
               "VALUES (:i,:t,:s,'intrusion',now(),'HIGH',0.94,82,:st)",
               {"i": eid, "t": w["tenant"], "s": w[site], "st": status})
    return eid


# ─── A. The licence gates changes, not history ───────────────────────────────

@pytest.mark.asyncio
async def test_an_unlicensed_tenant_cannot_register_a_drone():
    w = await _world(licensed=False)
    async with _client() as c:
        r = await c.post("/api/v1/drones", headers=w["h"][ADMIN],
                         json={"name": "D1", "code": "D-1", "site_id": str(w["site_a"])})
        ent = await c.get("/api/v1/drones/entitlement", headers=w["h"][ADMIN])
    assert r.status_code == 403 and "not licensed" in r.json()["detail"], r.text
    assert ent.status_code == 200 and ent.json()["licensed"] is False


@pytest.mark.asyncio
async def test_an_expired_licence_blocks_changes_but_history_stays_readable():
    w = await _world(expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    await _sql("INSERT INTO drones (tenant_id, site_id, name, code) VALUES (:t,:s,'Old','D-OLD')",
               {"t": w["tenant"], "s": w["site_a"]})
    async with _client() as c:
        write = await c.post("/api/v1/drones", headers=w["h"][ADMIN],
                             json={"name": "D2", "code": "D-2", "site_id": str(w["site_a"])})
        read = await c.get("/api/v1/drones", headers=w["h"][ADMIN])
        ent = (await c.get("/api/v1/drones/entitlement", headers=w["h"][ADMIN])).json()
    assert write.status_code == 403 and "expired" in write.json()["detail"], write.text
    assert read.status_code == 200 and read.json()["total"] == 1
    assert ent["licensed"] is False and "expired" in ent["reason"]


@pytest.mark.asyncio
async def test_the_drone_limit_counts_active_drones_and_disabling_frees_a_slot():
    w = await _world(max_drones=1)
    async with _client() as c:
        first = await _drone(c, w, code="D-1")
        second = await c.post("/api/v1/drones", headers=w["h"][ADMIN],
                              json={"name": "D2", "code": "D-2", "site_id": str(w["site_a"])})
        assert second.status_code == 403 and "allows 1" in second.json()["detail"], second.text
        assert (await c.post(f"/api/v1/drones/{first['id']}/disable", headers=w["h"][ADMIN])).status_code == 200
        await _drone(c, w, code="D-2")
        again = await c.post(f"/api/v1/drones/{first['id']}/enable", headers=w["h"][ADMIN])
    assert again.status_code == 403, again.text


@pytest.mark.asyncio
async def test_the_site_limit_counts_sites_in_use():
    w = await _world(max_sites=1)
    async with _client() as c:
        await _drone(c, w, "site_a", "D-A")
        other_site = await c.post("/api/v1/drones", headers=w["h"][ADMIN],
                                  json={"name": "B", "code": "D-B", "site_id": str(w["site_b"])})
        same_site = await c.post("/api/v1/drone-missions", headers=w["h"][ADMIN],
                                 json={"site_id": str(w["site_a"]), "name": "Night A"})
    assert other_site.status_code == 403 and "covers 1 site" in other_site.json()["detail"]
    assert same_site.status_code == 201, same_site.text


@pytest.mark.asyncio
async def test_the_mission_limit():
    w = await _world(max_missions=1)
    async with _client() as c:
        ok = await c.post("/api/v1/drone-missions", headers=w["h"][ADMIN],
                          json={"site_id": str(w["site_a"]), "name": "One"})
        over = await c.post("/api/v1/drone-missions", headers=w["h"][ADMIN],
                            json={"site_id": str(w["site_a"]), "name": "Two"})
    assert ok.status_code == 201 and over.status_code == 403, over.text


# ─── B. Only an enrolled Super Admin can grant the licence ───────────────────

async def _super_admin(*, enrolled: bool = True) -> dict:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    await _run([
        ("INSERT INTO tenants (id, name, slug) VALUES (:t,'Platform Ops',:s)",
         {"t": tid, "s": f"sa-{tid.hex[:10]}"}),
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name, totp_enabled) "
         "VALUES (:i,:t,1,:e,'x','Platform Owner',:mfa)",
         {"i": uid, "t": tid, "e": f"sa-{uid.hex[:8]}@platform.test", "mfa": enrolled}),
    ])
    return {"tenant": tid, "user": uid, "h": _auth(uid, tid, SUPER_ADMIN)}


@pytest.mark.asyncio
async def test_a_tenant_admin_cannot_license_the_module():
    w = await _world(licensed=False)
    async with _client() as c:
        r = await c.put(f"/api/v1/platform/tenants/{w['tenant']}/drone-license",
                        headers=w["h"][ADMIN], json={"is_enabled": True})
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_the_super_admin_grants_the_licence_and_it_takes_effect():
    w = await _world(licensed=False)
    sa = await _super_admin()
    async with _client() as c:
        put = await c.put(f"/api/v1/platform/tenants/{w['tenant']}/drone-license", headers=sa["h"],
                          json={"is_enabled": True, "max_drones": 5, "notes": "Pilot"})
        created = await c.post("/api/v1/drones", headers=w["h"][ADMIN],
                               json={"name": "D1", "code": "D-1", "site_id": str(w["site_a"])})
    assert put.status_code == 200, put.text
    assert put.json()["licensed"] is True and put.json()["max_drones"] == 5
    assert created.status_code == 201, created.text
    logged = await _sql("SELECT count(*) AS n FROM audit_logs WHERE tenant_id = :t "
                        " AND action = 'drone.license.set'", {"t": w["tenant"]})
    assert logged[0]["n"] == 1, "the grant is not in the customer's own audit log"


@pytest.mark.asyncio
async def test_a_super_admin_without_2fa_cannot_grant_it():
    w = await _world(licensed=False)
    sa = await _super_admin(enrolled=False)
    async with _client() as c:
        r = await c.put(f"/api/v1/platform/tenants/{w['tenant']}/drone-license", headers=sa["h"],
                        json={"is_enabled": True})
    assert r.status_code == 403 and "Two-factor" in r.json()["detail"], r.text


@pytest.mark.asyncio
async def test_the_platform_tenant_does_not_run_drone_patrols():
    sa = await _super_admin()
    plat = uuid.uuid4()
    await _sql("INSERT INTO tenants (id, name, slug, is_platform) VALUES (:t,'Platform',:s,TRUE)",
               {"t": plat, "s": f"plat-{plat.hex[:10]}"})
    async with _client() as c:
        r = await c.put(f"/api/v1/platform/tenants/{plat}/drone-license", headers=sa["h"],
                        json={"is_enabled": True})
    assert r.status_code == 422 and "platform tenant" in r.json()["detail"], r.text


# ─── C. Roles ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_who_may_read_and_who_may_change_the_fleet():
    w = await _world()
    async with _client() as c:
        await _drone(c, w)
        viewer_read = await c.get("/api/v1/drones", headers=w["h"][VIEWER])
        viewer_write = await c.post("/api/v1/drones", headers=w["h"][VIEWER],
                                    json={"name": "V", "code": "D-V", "site_id": str(w["site_a"])})
        operator_write = await c.post("/api/v1/drones", headers=w["h"][OPERATOR],
                                      json={"name": "O", "code": "D-O", "site_id": str(w["site_a"])})
        guard_fleet = await c.get("/api/v1/drones", headers=w["h"][GUARD])
        guard_events = await c.get("/api/v1/drone-events", headers=w["h"][GUARD])
        operator_route = await c.post("/api/v1/drone-routes", headers=w["h"][OPERATOR],
                                      json={"site_id": str(w["site_a"]), "name": "R"})
    assert viewer_read.status_code == 200
    assert viewer_write.status_code == 403 and operator_write.status_code == 403
    assert guard_fleet.status_code == 403, "a guard does not hold drone:read"
    assert guard_events.status_code == 200, "a guard may see drone events on incidents they attend"
    assert operator_route.status_code == 403, "an operator flies routes; they do not redraw them"


@pytest.mark.asyncio
async def test_an_operator_can_acknowledge_but_a_viewer_cannot():
    w = await _world()
    eid = await _event(w)
    async with _client() as c:
        viewer = await c.post(f"/api/v1/drone-events/{eid}/acknowledge", headers=w["h"][VIEWER], json={})
        operator = await c.post(f"/api/v1/drone-events/{eid}/acknowledge", headers=w["h"][OPERATOR], json={})
    assert viewer.status_code == 403 and operator.status_code == 200, operator.text


# ─── D. Site scoping and tenant isolation, through the API ───────────────────

@pytest.mark.asyncio
async def test_a_supervisor_sees_only_their_own_sites():
    w = await _world()
    async with _client() as c:
        a = await _drone(c, w, "site_a", "D-A")
        b = await _drone(c, w, "site_b", "D-B")
        route_b = (await c.post("/api/v1/drone-routes", headers=w["h"][ADMIN],
                                json={"site_id": str(w["site_b"]), "name": "B route"})).json()
        listing = (await c.get("/api/v1/drones", headers=w["h"][SUPERVISOR])).json()
        own = await c.get(f"/api/v1/drones/{a['id']}", headers=w["h"][SUPERVISOR])
        other = await c.get(f"/api/v1/drones/{b['id']}", headers=w["h"][SUPERVISOR])
        other_route = await c.get(f"/api/v1/drone-routes/{route_b['id']}", headers=w["h"][SUPERVISOR])
        plant_on_b = await c.post("/api/v1/drone-missions", headers=w["h"][SUPERVISOR],
                                  json={"site_id": str(w["site_b"]), "name": "Sneaky"})
    assert [d["code"] for d in listing["items"]] == ["D-A"]
    assert own.status_code == 200
    assert other.status_code == 404, "another site's drone must read as absent, not forbidden"
    assert other_route.status_code == 404
    assert plant_on_b.status_code == 404


@pytest.mark.asyncio
async def test_another_tenants_drone_does_not_exist():
    a, b = await _world(), await _world()
    async with _client() as c:
        theirs = await _drone(c, b, code="D-THEIRS")
        read = await c.get(f"/api/v1/drones/{theirs['id']}", headers=a["h"][ADMIN])
        write = await c.put(f"/api/v1/drones/{theirs['id']}", headers=a["h"][ADMIN], json={"name": "Mine"})
        listing = (await c.get("/api/v1/drones", headers=a["h"][ADMIN])).json()
    assert read.status_code == 404 and write.status_code == 404
    assert listing["total"] == 0


# ─── E. Fleet ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_duplicate_code_is_a_clear_409():
    w = await _world()
    async with _client() as c:
        await _drone(c, w, code="D-01")
        dup = await c.post("/api/v1/drones", headers=w["h"][ADMIN],
                           json={"name": "Again", "code": "D-01", "site_id": str(w["site_a"])})
    assert dup.status_code == 409 and "code" in dup.json()["detail"], dup.text


@pytest.mark.asyncio
async def test_a_provider_secret_is_write_only(monkeypatch):
    """The simulator has no secrets, so a provider that does is registered for
    this test only — the handling is the registry's, not the simulator's."""
    monkeypatch.setitem(registry.PROVIDERS, "test_secure", registry.ProviderSpec(
        key="test_secure", name="Test secure", description="test", simulated=True,
        fields=(registry.ProviderField("endpoint", "Endpoint", required=True),
                registry.ProviderField("api_key", "API key", required=True, secret=True))))
    w = await _world()
    async with _client() as c:
        made = await c.post("/api/v1/drones/providers", headers=w["h"][ADMIN], json={
            "name": "Secure", "provider_key": "test_secure",
            "settings": {"endpoint": "https://fleet.example", "api_key": "sk-live-abc123"}})
        assert made.status_code == 201, made.text
        pid = made.json()["id"]
        # Change the endpoint without resending the key: the key must survive.
        kept = await c.put(f"/api/v1/drones/providers/{pid}", headers=w["h"][ADMIN],
                           json={"settings": {"endpoint": "https://fleet2.example"}})
        listing = await c.get("/api/v1/drones/providers", headers=w["h"][ADMIN])
    for body in (made.text, kept.text, listing.text):
        assert "sk-live-abc123" not in body
    assert made.json()["has_secret"] is True and made.json()["config"] == {"endpoint": "https://fleet.example"}
    assert kept.status_code == 200 and kept.json()["has_secret"] is True
    stored = await _sql("SELECT secret_encrypted FROM drone_provider_configs WHERE id = :i", {"i": uuid.UUID(pid)})
    assert "sk-live-abc123" not in stored[0]["secret_encrypted"], "the secret is stored in plaintext"


@pytest.mark.asyncio
async def test_a_gateway_credential_is_shown_once_and_stored_as_a_hash():
    w = await _world()
    async with _client() as c:
        made = await c.post("/api/v1/drones/edge-gateways", headers=w["h"][ADMIN],
                            json={"site_id": str(w["site_a"]), "name": "Gate A", "code": "EDGE-A"})
        assert made.status_code == 201, made.text
        cred = made.json()["credential"]
        listing = await c.get("/api/v1/drones/edge-gateways", headers=w["h"][ADMIN])
        rotated = await c.post(f"/api/v1/drones/edge-gateways/{made.json()['id']}/rotate-credential",
                               headers=w["h"][ADMIN])
    assert cred.startswith(f"deg.{w['tenant']}.")
    assert cred not in listing.text
    assert all("credential" not in g for g in listing.json()), "the listing re-exposed a credential"
    row = await _sql("SELECT credential_hash FROM drone_edge_gateways WHERE id = :i",
                     {"i": uuid.UUID(made.json()["id"])})
    new = rotated.json()["credential"]
    assert new != cred
    assert row[0]["credential_hash"] == hashlib.sha256(new.encode()).hexdigest(), \
        "rotation must replace the old credential, not add to it"


@pytest.mark.asyncio
async def test_a_drone_reports_through_its_own_sites_gateway():
    w = await _world()
    async with _client() as c:
        gw = (await c.post("/api/v1/drones/edge-gateways", headers=w["h"][ADMIN],
                           json={"site_id": str(w["site_a"]), "name": "Gate A", "code": "EDGE-A"})).json()
        wrong = await c.post("/api/v1/drones", headers=w["h"][ADMIN], json={
            "name": "B", "code": "D-B", "site_id": str(w["site_b"]), "edge_gateway_id": gw["id"]})
    assert wrong.status_code == 422 and "gateway" in wrong.json()["detail"], wrong.text


@pytest.mark.asyncio
async def test_a_drone_that_has_flown_is_disabled_not_deleted():
    w = await _world()
    async with _client() as c:
        d = await _drone(c, w)
        await _sql("INSERT INTO drone_patrol_sessions (tenant_id, session_number, drone_id, site_id, status) "
                   "VALUES (:t,'DP-1',:d,:s,'COMPLETED')",
                   {"t": w["tenant"], "d": uuid.UUID(d["id"]), "s": w["site_a"]})
        delete = await c.delete(f"/api/v1/drones/{d['id']}", headers=w["h"][ADMIN])
        disable = await c.post(f"/api/v1/drones/{d['id']}/disable", headers=w["h"][ADMIN])
    assert delete.status_code == 409 and "Disable it instead" in delete.json()["detail"]
    assert disable.status_code == 200 and disable.json()["status"] == "DISABLED"


@pytest.mark.asyncio
async def test_a_drone_on_a_mission_cannot_be_disabled():
    w = await _world()
    async with _client() as c:
        d = await _drone(c, w)
        await _sql("INSERT INTO drone_patrol_sessions (tenant_id, session_number, drone_id, site_id, status) "
                   "VALUES (:t,'DP-2',:d,:s,'ACTIVE')",
                   {"t": w["tenant"], "d": uuid.UUID(d["id"]), "s": w["site_a"]})
        r = await c.post(f"/api/v1/drones/{d['id']}/disable", headers=w["h"][ADMIN])
    assert r.status_code == 409 and "on a mission" in r.json()["detail"]


@pytest.mark.asyncio
async def test_maintenance_moves_the_drones_dates_forward_only():
    w = await _world()
    now = datetime.now(timezone.utc)
    async with _client() as c:
        d = await _drone(c, w)
        first = await c.post(f"/api/v1/drones/{d['id']}/maintenance", headers=w["h"][ADMIN], json={
            "maintenance_type": "FIRMWARE_UPDATE", "performed_at": now.isoformat(),
            "firmware_version_after": "v2.1.0", "next_due_at": (now + timedelta(days=90)).isoformat()})
        late_entry = await c.post(f"/api/v1/drones/{d['id']}/maintenance", headers=w["h"][ADMIN], json={
            "maintenance_type": "INSPECTION", "performed_at": (now - timedelta(days=30)).isoformat()})
        after = (await c.get(f"/api/v1/drones/{d['id']}", headers=w["h"][ADMIN])).json()
        guard = await c.post(f"/api/v1/drones/{d['id']}/maintenance", headers=w["h"][OPERATOR],
                             json={"maintenance_type": "INSPECTION"})
    assert first.status_code == 201 and late_entry.status_code == 201
    assert after["firmware_version"] == "v2.1.0"
    assert datetime.fromisoformat(after["last_maintenance_at"]) >= now - timedelta(seconds=1), \
        "entering an older job late moved last_maintenance_at backwards"
    assert len(after["recent_maintenance"]) == 2
    assert guard.status_code == 403


@pytest.mark.asyncio
async def test_telemetry_comes_back_in_order_and_the_window_is_capped():
    w = await _world()
    now = datetime.now(timezone.utc)
    async with _client() as c:
        d = await _drone(c, w)
        await _run([("INSERT INTO drone_telemetry (tenant_id, drone_id, recorded_at, latitude, longitude, battery_pct) "
                     "VALUES (:t,:d,:a,1.3,103.8,:b)",
                     {"t": w["tenant"], "d": uuid.UUID(d["id"]), "a": now - timedelta(minutes=m), "b": 90 - m})
                    for m in (30, 10, 20)])
        ok = await c.get(f"/api/v1/drones/{d['id']}/telemetry", headers=w["h"][ADMIN])
        too_wide = await c.get(f"/api/v1/drones/{d['id']}/telemetry", headers=w["h"][ADMIN], params={
            "start": (now - timedelta(hours=25)).isoformat(), "end": now.isoformat()})
    assert ok.status_code == 200
    assert [p["battery_pct"] for p in ok.json()["points"]] == [60, 70, 80]
    assert too_wide.status_code == 422


@pytest.mark.asyncio
async def test_the_dashboard_counts_the_fleet():
    w = await _world()
    async with _client() as c:
        a = await _drone(c, w, code="D-A")
        await _drone(c, w, code="D-B")
        await _sql("UPDATE drones SET status = 'READY', battery_level = 20 WHERE id = :i",
                   {"i": uuid.UUID(a["id"])})
        await _event(w)
        dash = (await c.get("/api/v1/drones/dashboard", headers=w["h"][ADMIN])).json()
    assert dash["fleet"]["total"] == 2 and dash["fleet"]["online"] == 1
    assert dash["fleet"]["battery_warnings"] == 1
    assert dash["open_events_by_risk"]["HIGH"] == 1


# ─── F. Planning ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_rectangle_is_stored_as_four_corners_and_a_bad_circle_is_refused():
    w = await _world()
    async with _client() as c:
        rect = await c.post("/api/v1/drone-zones", headers=w["h"][ADMIN], json={
            "site_id": str(w["site_a"]), "name": "Warehouse", "zone_type": "RESTRICTED",
            "shape": "RECTANGLE", "polygon": [[1.301, 103.801], [1.299, 103.799]],
            "allowed_vehicle_plates": ["sgx 1234 a"]})
        bad = await c.post("/api/v1/drone-zones", headers=w["h"][ADMIN], json={
            "site_id": str(w["site_a"]), "name": "Pad", "shape": "CIRCLE",
            "center_latitude": 1.3, "center_longitude": 103.8})
    assert rect.status_code == 201, rect.text
    assert len(rect.json()["polygon"]) == 4
    assert rect.json()["allowed_vehicle_plates"] == ["SGX1234A"], "plates are stored as a reader reports them"
    assert bad.status_code == 422 and "centre and a radius" in bad.json()["detail"]


@pytest.mark.asyncio
async def test_waypoints_are_stored_in_the_order_given_and_off_site_ones_are_flagged():
    w = await _world()
    async with _client() as c:
        made = await c.post("/api/v1/drone-routes", headers=w["h"][ADMIN], json={
            "site_id": str(w["site_a"]), "name": "Perimeter",
            "waypoints": [{"name": "Gate", "latitude": 1.3005, "longitude": 103.8},
                          {"name": "Far", "latitude": 1.3100, "longitude": 103.8}]})
        assert made.status_code == 201, made.text
        rid = made.json()["id"]
        reordered = await c.put(f"/api/v1/drone-routes/{rid}/waypoints", headers=w["h"][ADMIN], json={
            "waypoints": [{"name": "Far", "latitude": 1.3100, "longitude": 103.8},
                          {"name": "Gate", "latitude": 1.3005, "longitude": 103.8},
                          {"name": "Yard", "latitude": 1.2995, "longitude": 103.8}]})
    body = made.json()
    assert (body["base_latitude"], body["base_longitude"]) == (1.3, 103.8), "base defaults to the site"
    assert body["summary"]["outside_site_geofence"] == [2]
    assert body["summary"]["length_m"] > 2000
    names = [(p["sequence"], p["name"]) for p in reordered.json()["waypoints"]]
    assert names == [(1, "Far"), (2, "Gate"), (3, "Yard")]
    assert reordered.json()["summary"]["outside_site_geofence"] == [1]


@pytest.mark.asyncio
async def test_the_parts_of_a_mission_must_share_its_site():
    w = await _world()
    async with _client() as c:
        zone_b = (await c.post("/api/v1/drone-zones", headers=w["h"][ADMIN], json={
            "site_id": str(w["site_b"]), "name": "B zone", "shape": "CIRCLE",
            "center_latitude": 1.35, "center_longitude": 103.85, "radius_m": 30})).json()
        wp_zone = await c.post("/api/v1/drone-routes", headers=w["h"][ADMIN], json={
            "site_id": str(w["site_a"]), "name": "A",
            "waypoints": [{"latitude": 1.3, "longitude": 103.8, "security_zone_id": zone_b["id"]}]})
        route_b = (await c.post("/api/v1/drone-routes", headers=w["h"][ADMIN],
                                json={"site_id": str(w["site_b"]), "name": "B"})).json()
        drone_b = await _drone(c, w, "site_b")
        wrong_route = await c.post("/api/v1/drone-missions", headers=w["h"][ADMIN], json={
            "site_id": str(w["site_a"]), "name": "M1", "route_id": route_b["id"]})
        wrong_drone = await c.post("/api/v1/drone-missions", headers=w["h"][ADMIN], json={
            "site_id": str(w["site_a"]), "name": "M2", "drone_id": drone_b["id"]})
    assert wp_zone.status_code == 422 and "another site" in wp_zone.json()["detail"]
    assert wrong_route.status_code == 422 and "another site" in wrong_route.json()["detail"]
    assert wrong_drone.status_code == 422 and "another site" in wrong_drone.json()["detail"]


@pytest.mark.asyncio
async def test_a_route_that_is_being_flown_cannot_be_deleted():
    w = await _world()
    async with _client() as c:
        route = (await c.post("/api/v1/drone-routes", headers=w["h"][ADMIN],
                              json={"site_id": str(w["site_a"]), "name": "R"})).json()
        await c.post("/api/v1/drone-missions", headers=w["h"][ADMIN],
                     json={"site_id": str(w["site_a"]), "name": "Uses R", "route_id": route["id"]})
        r = await c.delete(f"/api/v1/drone-routes/{route['id']}", headers=w["h"][ADMIN])
    assert r.status_code == 409 and "enabled mission" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_schedule_runs_in_its_own_timezone_and_the_mission_shows_its_next_run():
    w = await _world()
    async with _client() as c:
        m = (await c.post("/api/v1/drone-missions", headers=w["h"][ADMIN],
                          json={"site_id": str(w["site_a"]), "name": "Night"})).json()
        s = await c.post(f"/api/v1/drone-missions/{m['id']}/schedules", headers=w["h"][ADMIN], json={
            "schedule_type": "DAILY", "timezone": "Asia/Singapore",
            "start_date": datetime.now(timezone.utc).date().isoformat(), "launch_time": "23:00:00"})
        preview = await c.get(f"/api/v1/drone-schedules/{s.json()['id']}/preview",
                              headers=w["h"][VIEWER], params={"count": 3})
        listed = (await c.get("/api/v1/drone-missions", headers=w["h"][ADMIN])).json()
    assert s.status_code == 201, s.text
    runs = preview.json()["next_runs"]
    assert len(runs) == 3
    assert all(r["local"].endswith("T23:00:00+08:00") for r in runs), runs
    assert all(datetime.fromisoformat(r["utc"]).hour == 15 for r in runs)
    assert listed["items"][0]["next_run"]["local"] == runs[0]["local"]


@pytest.mark.asyncio
async def test_a_bad_schedule_says_everything_wrong_with_it():
    w = await _world()
    async with _client() as c:
        m = (await c.post("/api/v1/drone-missions", headers=w["h"][ADMIN],
                          json={"site_id": str(w["site_a"]), "name": "Days"})).json()
        r = await c.post(f"/api/v1/drone-missions/{m['id']}/schedules", headers=w["h"][ADMIN], json={
            "schedule_type": "SELECTED_DAYS", "timezone": "Nowhere/Special",
            "start_date": "2026-10-01", "launch_time": "22:00:00"})
    assert r.status_code == 422
    assert "Unknown timezone" in r.json()["detail"] and "at least one day" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_profile_lists_each_ai_module_once():
    w = await _world()
    async with _client() as c:
        ok = await c.post("/api/v1/drone-security-profiles", headers=w["h"][ADMIN], json={
            "name": "Night High Security",
            "rules": [{"module_type": "intrusion", "base_severity": "HIGH"},
                      {"module_type": "fire_smoke", "incident_risk_level": "CRITICAL"}]})
        dupe = await c.post("/api/v1/drone-security-profiles", headers=w["h"][ADMIN], json={
            "name": "Dupe", "rules": [{"module_type": "lpr"}, {"module_type": "lpr"}]})
        unknown = await c.post("/api/v1/drone-security-profiles", headers=w["h"][ADMIN], json={
            "name": "Made up", "rules": [{"module_type": "wrong_way"}]})
    assert ok.status_code == 201 and len(ok.json()["rules"]) == 2
    assert dupe.status_code == 422 and "once" in dupe.json()["detail"]
    assert unknown.status_code == 422, "only AI modules that exist may be configured"


# ─── G. Events: a decision is recorded once, and stays made ──────────────────

@pytest.mark.asyncio
async def test_an_event_moves_forward_and_a_closed_one_stays_closed():
    w = await _world()
    eid = await _event(w)
    async with _client() as c:
        ack = await c.post(f"/api/v1/drone-events/{eid}/acknowledge", headers=w["h"][OPERATOR], json={})
        ack_again = await c.post(f"/api/v1/drone-events/{eid}/acknowledge", headers=w["h"][ADMIN], json={})
        inv = await c.post(f"/api/v1/drone-events/{eid}/investigate", headers=w["h"][ADMIN], json={})
        res = await c.post(f"/api/v1/drone-events/{eid}/resolve", headers=w["h"][ADMIN],
                           json={"note": "Guard checked; contractor with a permit"})
        res_again = await c.post(f"/api/v1/drone-events/{eid}/resolve", headers=w["h"][OPERATOR], json={})
        fp_after = await c.post(f"/api/v1/drone-events/{eid}/false-positive", headers=w["h"][OPERATOR],
                                json={"reason": "late change of mind"})
    assert ack.status_code == 200 and ack.json()["acknowledged_by_name"] == "Role 4 User"
    assert ack_again.status_code == 409 and "Role 4 User" in ack_again.json()["detail"]
    assert inv.json()["status"] == "INVESTIGATING"
    assert res.json()["status"] == "RESOLVED" and res.json()["resolved_by_name"] == "Role 2 User"
    assert res_again.status_code == 409 and "Role 2 User" in res_again.json()["detail"]
    assert fp_after.status_code == 409


@pytest.mark.asyncio
async def test_a_false_positive_needs_a_reason():
    w = await _world()
    eid = await _event(w)
    async with _client() as c:
        bare = await c.post(f"/api/v1/drone-events/{eid}/false-positive", headers=w["h"][ADMIN], json={})
        ok = await c.post(f"/api/v1/drone-events/{eid}/false-positive", headers=w["h"][ADMIN],
                          json={"reason": "Reflection on the warehouse roof"})
    assert bare.status_code == 422
    assert ok.json()["status"] == "FALSE_POSITIVE"
    assert ok.json()["false_positive_reason"] == "Reflection on the warehouse roof"


@pytest.mark.asyncio
async def test_events_can_be_handled_after_the_licence_lapses():
    w = await _world(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    eid = await _event(w)
    async with _client() as c:
        r = await c.post(f"/api/v1/drone-events/{eid}/acknowledge", headers=w["h"][OPERATOR], json={})
    assert r.status_code == 200, "a lapsed licence locked an operator out of an open event"


@pytest.mark.asyncio
async def test_an_event_shows_its_media_and_correlated_cameras():
    w = await _world()
    eid = await _event(w)
    cam = uuid.uuid4()
    await _run([
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'CAM-27')",
         {"i": cam, "t": w["tenant"], "s": w["site_a"]}),
        ("INSERT INTO drone_event_media (tenant_id, event_id, media_kind, storage_path, captured_at) "
         "VALUES (:t,:e,'SNAPSHOT','drone/e.jpg',now())", {"t": w["tenant"], "e": eid}),
        ("INSERT INTO drone_event_cameras (tenant_id, event_id, camera_id, camera_name, distance_m) "
         "VALUES (:t,:e,:c,'CAM-27',42.5)", {"t": w["tenant"], "e": eid, "c": cam}),
    ])
    async with _client() as c:
        detail = (await c.get(f"/api/v1/drone-events/{eid}", headers=w["h"][OPERATOR])).json()
        other_site = await c.get(f"/api/v1/drone-events/{await _event(w, site='site_b')}",
                                 headers=w["h"][SUPERVISOR])
    assert [m["media_kind"] for m in detail["media"]] == ["SNAPSHOT"]
    assert detail["cameras"][0]["camera_name"] == "CAM-27"
    assert "storage_path" not in json.dumps(detail["media"]), "the storage path is served, not exposed"
    assert other_site.status_code == 404


# ─── H. Audit and replay ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_changes_are_written_to_the_audit_log():
    w = await _world()
    async with _client() as c:
        d = await _drone(c, w, code="D-AUD")
        await c.put(f"/api/v1/drones/{d['id']}", headers=w["h"][ADMIN], json={"model": "Mk2"})
    rows = await _sql("SELECT action, user_id FROM audit_logs WHERE tenant_id = :t AND resource_id = :r "
                      " ORDER BY created_at", {"t": w["tenant"], "r": d["id"]})
    assert [r["action"] for r in rows] == ["drone.create", "drone.update"]
    assert all(str(r["user_id"]) == str(w["users"][ADMIN]) for r in rows)


@pytest.mark.asyncio
async def test_a_long_flight_track_is_thinned_but_keeps_its_ends():
    w = await _world()
    async with _client() as c:
        d = await _drone(c, w)
        sid = uuid.uuid4()
        await _run([
            ("INSERT INTO drone_patrol_sessions (id, tenant_id, session_number, drone_id, site_id, status) "
             "VALUES (:i,:t,'DP-T',:d,:s,'COMPLETED')",
             {"i": sid, "t": w["tenant"], "d": uuid.UUID(d["id"]), "s": w["site_a"]}),
            ("INSERT INTO drone_telemetry (tenant_id, drone_id, session_id, recorded_at, waypoint_sequence) "
             "SELECT :t, :d, :s, now() - make_interval(secs => 5000 - g), g FROM generate_series(1, 5000) g",
             {"t": w["tenant"], "d": uuid.UUID(d["id"]), "s": sid}),
        ])
        track = (await c.get(f"/api/v1/drone-patrols/{sid}/track", headers=w["h"][ADMIN])).json()
    assert track["total_samples"] == 5000
    assert track["returned"] <= 2000 + 1
    assert track["points"][0]["waypoint_sequence"] == 1
    assert track["points"][-1]["waypoint_sequence"] == 5000
