"""Gap 39 — Patrol Routes, Checkpoints, Sessions, Scan Verification + SOS + Permissions + RLS

Sections:
  A (2)  — DB schema: patrol_routes + checkpoint_scans tables with key columns
  B (4)  — Route CRUD: create route, list with checkpoint_count, get with checkpoints, 404
  C (3)  — Checkpoints: add checkpoint, appears in route GET, correct sequence/name returned
  D (4)  — Session lifecycle: start session, manual scan (verified=True), QR scan match/mismatch, complete session
  E (2)  — Session get: returns route_name + scans list, 404 for unknown session
  F (2)  — SOS panic: creates incident, sos_acknowledged=True (tenant has a camera)
  G (2)  — Permissions: viewer read=200 manage=403, unauth 401
  H (2)  — RLS isolation: tenant B cannot list or get tenant A's routes
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql+asyncpg://svc_app:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


async def _seed_tenant_user_site(role_id: int = 2):
    """Create isolated tenant + user + site; return (tenant_id, user_id, site_id, jwt_token)."""
    from app.core.security import create_access_token, hash_password

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    site_id = uuid.uuid4()
    slug = f"pat-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Patrol Tenant {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                "VALUES (:id, :tid, :rid, :email, :pw)"
            ),
            {"id": user_id, "tid": tenant_id, "rid": role_id,
             "email": f"u-{user_id.hex[:8]}@test.local", "pw": hash_password("x")},
        )
        await s.execute(
            text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :name)"),
            {"id": site_id, "tid": tenant_id, "name": "Test Patrol Site"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, site_id, token


async def _seed_camera(tenant_id: uuid.UUID) -> uuid.UUID:
    """Insert a minimal camera row for the tenant (needed for patrol SOS)."""
    camera_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) "
                "VALUES (:id, :tid, 'SOS Cam', '[]')"
            ),
            {"id": camera_id, "tid": tenant_id},
        )
        await s.commit()
    await engine.dispose()
    return camera_id


def _app():
    from app.main import app
    return app


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_route(client: AsyncClient, site_id: str, **kwargs) -> dict:
    """POST /api/v1/patrols/routes and assert 200; return the JSON body."""
    payload = {"site_id": site_id, "name": "Test Route", **kwargs}
    r = await client.post("/api/v1/patrols/routes", json=payload)
    assert r.status_code == 200, f"create_route failed: {r.text}"
    return r.json()


async def _add_checkpoint(client: AsyncClient, route_id: str, **kwargs) -> dict:
    """POST /api/v1/patrols/routes/{route_id}/checkpoints; return JSON body."""
    payload = {"sequence": 1, "name": "Checkpoint Alpha", **kwargs}
    r = await client.post(f"/api/v1/patrols/routes/{route_id}/checkpoints", json=payload)
    assert r.status_code == 200, f"add_checkpoint failed: {r.text}"
    return r.json()


# ── Section A — DB Schema ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patrol_routes_table_has_key_columns():
    """patrol_routes table has operational and relationship columns."""
    expected = {
        "id", "tenant_id", "site_id", "name", "description", "is_active",
        "created_at", "updated_at",
    }
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'patrol_routes'"
        ))
        cols = {r[0] for r in rows}
    await engine.dispose()
    assert expected.issubset(cols), f"Missing columns: {expected - cols}"


@pytest.mark.asyncio
async def test_checkpoint_scans_table_has_verification_columns():
    """checkpoint_scans table has scan_method and verified columns for QR/NFC verification."""
    expected = {
        "id", "tenant_id", "session_id", "checkpoint_id",
        "scan_method", "scanned_at", "guard_user_id", "verified",
        "latitude", "longitude",
    }
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'checkpoint_scans'"
        ))
        cols = {r[0] for r in rows}
    await engine.dispose()
    assert expected.issubset(cols), f"Missing columns: {expected - cols}"


# ── Section B — Route CRUD ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_patrol_route_returns_200():
    """POST /patrols/routes creates a route and returns id + name."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/patrols/routes", json={
            "site_id": str(site_id),
            "name": "Evening Perimeter",
            "description": "Full perimeter check",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "id" in body
    assert body["name"] == "Evening Perimeter"


@pytest.mark.asyncio
async def test_list_patrol_routes_includes_created():
    """GET /patrols/routes returns the created route with checkpoint_count field."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        created = await _create_route(c, str(site_id))
        r = await c.get("/api/v1/patrols/routes")
    assert r.status_code == 200, r.text
    routes = r.json()
    ids = [rt["id"] for rt in routes]
    assert created["id"] in ids
    matched = next(rt for rt in routes if rt["id"] == created["id"])
    assert "checkpoint_count" in matched


@pytest.mark.asyncio
async def test_get_patrol_route_by_id_includes_checkpoints():
    """GET /patrols/routes/{id} returns route with a 'checkpoints' list."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        created = await _create_route(c, str(site_id))
        r = await c.get(f"/api/v1/patrols/routes/{created['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == created["id"]
    assert "checkpoints" in body
    assert isinstance(body["checkpoints"], list)


@pytest.mark.asyncio
async def test_get_patrol_route_not_found_404():
    """GET /patrols/routes/{unknown} returns 404."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/patrols/routes/{uuid.uuid4()}")
    assert r.status_code == 404


# ── Section C — Checkpoints ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_checkpoint_to_route_returns_200():
    """POST /patrols/routes/{id}/checkpoints adds a checkpoint; returns id + sequence."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        route = await _create_route(c, str(site_id))
        r = await c.post(f"/api/v1/patrols/routes/{route['id']}/checkpoints", json={
            "sequence": 1,
            "name": "Main Gate",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "id" in body
    assert body["sequence"] == 1
    assert body["name"] == "Main Gate"


@pytest.mark.asyncio
async def test_added_checkpoint_appears_in_route_get():
    """A checkpoint added to a route is returned in GET /routes/{id}['checkpoints']."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        route = await _create_route(c, str(site_id))
        cp = await _add_checkpoint(c, route["id"], sequence=1, name="Lobby")
        r = await c.get(f"/api/v1/patrols/routes/{route['id']}")
    assert r.status_code == 200, r.text
    cp_ids = [c_["id"] for c_ in r.json()["checkpoints"]]
    assert cp["id"] in cp_ids


@pytest.mark.asyncio
async def test_checkpoint_with_qr_code_stored():
    """Checkpoint with qr_code is persisted; returned in route detail."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        route = await _create_route(c, str(site_id))
        await c.post(f"/api/v1/patrols/routes/{route['id']}/checkpoints", json={
            "sequence": 1,
            "name": "Server Room",
            "qr_code": "QR-SERVER-ROOM-001",
        })
        r = await c.get(f"/api/v1/patrols/routes/{route['id']}")
    checkpoints = r.json()["checkpoints"]
    assert any(cp.get("qr_code") == "QR-SERVER-ROOM-001" for cp in checkpoints)


# ── Section D — Session Lifecycle ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_patrol_session_returns_in_progress():
    """POST /patrols/routes/{id}/sessions starts a session with status 'in_progress'."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        route = await _create_route(c, str(site_id))
        r = await c.post(f"/api/v1/patrols/routes/{route['id']}/sessions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "in_progress"
    assert "id" in body


@pytest.mark.asyncio
async def test_manual_scan_is_always_verified():
    """POST /sessions/{id}/scan with scan_method='manual' returns verified=True."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        route = await _create_route(c, str(site_id))
        cp = await _add_checkpoint(c, route["id"])
        session_r = await c.post(f"/api/v1/patrols/routes/{route['id']}/sessions")
        session_id = session_r.json()["id"]
        r = await c.post(f"/api/v1/patrols/sessions/{session_id}/scan", json={
            "checkpoint_id": cp["id"],
            "scan_method": "manual",
        })
    assert r.status_code == 200, r.text
    assert r.json()["verified"] is True


@pytest.mark.asyncio
async def test_qr_scan_correct_code_verified_true():
    """POST .../scan with qr_code matching checkpoint.qr_code returns verified=True."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        route = await _create_route(c, str(site_id))
        cp_r = await c.post(f"/api/v1/patrols/routes/{route['id']}/checkpoints", json={
            "sequence": 1,
            "name": "QR Gate",
            "qr_code": "QR-GATE-9999",
        })
        cp_id = cp_r.json()["id"]
        session_r = await c.post(f"/api/v1/patrols/routes/{route['id']}/sessions")
        session_id = session_r.json()["id"]
        r = await c.post(f"/api/v1/patrols/sessions/{session_id}/scan", json={
            "checkpoint_id": cp_id,
            "scan_method": "qr",
            "scanned_code": "QR-GATE-9999",
        })
    assert r.status_code == 200, r.text
    assert r.json()["verified"] is True


@pytest.mark.asyncio
async def test_qr_scan_wrong_code_verified_false():
    """POST .../scan with incorrect scanned_code returns verified=False."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        route = await _create_route(c, str(site_id))
        cp_r = await c.post(f"/api/v1/patrols/routes/{route['id']}/checkpoints", json={
            "sequence": 1,
            "name": "Secure Zone",
            "qr_code": "CORRECT-QR",
        })
        cp_id = cp_r.json()["id"]
        session_r = await c.post(f"/api/v1/patrols/routes/{route['id']}/sessions")
        session_id = session_r.json()["id"]
        r = await c.post(f"/api/v1/patrols/sessions/{session_id}/scan", json={
            "checkpoint_id": cp_id,
            "scan_method": "qr",
            "scanned_code": "WRONG-QR",
        })
    assert r.status_code == 200, r.text
    assert r.json()["verified"] is False


@pytest.mark.asyncio
async def test_complete_session_sets_completed_status():
    """POST /patrols/sessions/{id}/complete transitions status to 'completed'."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        route = await _create_route(c, str(site_id))
        session_r = await c.post(f"/api/v1/patrols/routes/{route['id']}/sessions")
        session_id = session_r.json()["id"]
        r = await c.post(f"/api/v1/patrols/sessions/{session_id}/complete")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"


# ── Section E — Session Get ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_session_returns_route_name_and_scans():
    """GET /patrols/sessions/{id} returns session with route_name and scans list."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        route = await _create_route(c, str(site_id), name="Night Patrol")
        cp = await _add_checkpoint(c, route["id"])
        session_r = await c.post(f"/api/v1/patrols/routes/{route['id']}/sessions")
        session_id = session_r.json()["id"]
        await c.post(f"/api/v1/patrols/sessions/{session_id}/scan", json={
            "checkpoint_id": cp["id"],
            "scan_method": "manual",
        })
        r = await c.get(f"/api/v1/patrols/sessions/{session_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route_name"] == "Night Patrol"
    assert "scans" in body
    assert len(body["scans"]) == 1
    assert body["scans"][0]["verified"] is True


@pytest.mark.asyncio
async def test_get_session_not_found_404():
    """GET /patrols/sessions/{unknown} returns 404."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/patrols/sessions/{uuid.uuid4()}")
    assert r.status_code == 404


# ── Section F — SOS Panic ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patrol_sos_returns_acknowledged():
    """POST /patrols/sos returns sos_acknowledged=True with incident_id (tenant has camera)."""
    tid, _, _, token = await _seed_tenant_user_site(role_id=5)  # security_guard has guard:sos
    await _seed_camera(tid)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/patrols/sos", json={
            "latitude": 1.3521,
            "longitude": 103.8198,
            "message": "Guard down — immediate assistance needed",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sos_acknowledged"] is True
    assert "incident_id" in body
    assert body["incident_id"] is not None


@pytest.mark.asyncio
async def test_patrol_sos_creates_critical_incident():
    """POST /patrols/sos creates an incident with severity='critical' and alert_code='guard.sos'."""
    tid, _, _, token = await _seed_tenant_user_site(role_id=5)
    await _seed_camera(tid)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/patrols/sos", json={"message": "SOS test"})
    assert r.status_code == 200
    incident_id = r.json()["incident_id"]

    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        row = (await s.execute(
            text(
                "SELECT severity, alert_code FROM incidents "
                "WHERE id = CAST(:iid AS uuid)"
            ),
            {"iid": incident_id},
        )).first()
    await engine.dispose()
    assert row is not None, "Incident row not found after SOS"
    assert row.severity == "critical"
    assert row.alert_code == "guard.sos"


# ── Section G — Permissions ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_can_read_routes_not_create():
    """Role 6 (viewer) can list routes (patrol:read=200) but cannot create (patrol:manage=403)."""
    _, _, site_id, token = await _seed_tenant_user_site(role_id=6)
    async with await _authed(token) as c:
        get_r = await c.get("/api/v1/patrols/routes")
        post_r = await c.post("/api/v1/patrols/routes", json={
            "site_id": str(site_id),
            "name": "Blocked Route",
        })
    assert get_r.status_code == 200
    assert post_r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_patrols_returns_401():
    """Requests to /patrols/routes without a token return 401."""
    async with AsyncClient(
        transport=ASGITransport(_app()), base_url="http://test"
    ) as c:
        r = await c.get("/api/v1/patrols/routes")
    assert r.status_code == 401


# ── Section H — RLS Isolation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_tenant_b_cannot_list_tenant_a_routes():
    """Tenant B's route list does not contain Tenant A's routes."""
    _, _, site_a, token_a = await _seed_tenant_user_site(role_id=2)
    _, _, site_b, token_b = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token_a) as c:
        route_a = await _create_route(c, str(site_a))
    async with await _authed(token_b) as c:
        r = await c.get("/api/v1/patrols/routes")
    assert r.status_code == 200, r.text
    ids = [rt["id"] for rt in r.json()]
    assert route_a["id"] not in ids


@pytest.mark.asyncio
async def test_rls_tenant_b_get_tenant_a_route_returns_404():
    """Tenant B cannot fetch Tenant A's route by ID — RLS makes it appear non-existent."""
    _, _, site_a, token_a = await _seed_tenant_user_site(role_id=2)
    _, _, _, token_b = await _seed_tenant_user_site(role_id=2)
    async with await _authed(token_a) as c:
        route_a = await _create_route(c, str(site_a))
    async with await _authed(token_b) as c:
        r = await c.get(f"/api/v1/patrols/routes/{route_a['id']}")
    assert r.status_code == 404
