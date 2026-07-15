"""Gap 64 — Cameras Router (isolated-tenant integration tests)

Isolated-tenant tests for backend/app/routers/cameras.py (236 lines).
Separate file from existing test_cameras.py (Phase 1 unit tests).

Endpoints (prefix /api/v1/cameras):
  POST   /validate-stream   — camera:create; SSRF guard via validate_rtsp_url; probes RTSP
  GET    /                  — camera:read; list cameras with site JOIN
  GET    /{id}              — camera:read; 404 if not found
  POST   /                  — 201; camera:create; returns {id, name}; license check (0 rows = open)
  PUT    /{id}              — camera:update; 422 no fields; optional ai_modules license check
  DELETE /{id}              — 204; camera:delete; soft-deactivate (is_active=FALSE)

Key facts:
  - Tenants seeded via admin engine have 0 license rows → ALL ai modules allowed (_check_module_licenses bypass)
  - Invalid module names in ai_modules_enabled are silently filtered out (not 422)
  - DELETE always 204 — no 404 check on unknown IDs
  - validate-stream: private IPs (127.0.0.1, RFC 1918) → 422; non-rtsp scheme → 422

Sections:
  A — Camera CRUD (6 tests)
  B — Camera update (3 tests)
  C — AI modules handling (2 tests)
  D — Validate stream SSRF guard (2 tests)
  E — Permissions + RLS (3 tests)
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


async def _seed_tenant_and_token(role_id: int = 2):
    """Create isolated tenant + user; return (tenant_id, user_id, token)."""
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"cam-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Cam Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Cam Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"cam-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_camera(c: AsyncClient, name: str = "Test Cam",
                          location: str = "Lobby") -> str:
    r = await c.post("/api/v1/cameras", json={"name": name, "location": location})
    assert r.status_code == 201, f"create_camera failed: {r.text}"
    return r.json()["id"]


# ─── A. Camera CRUD ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ci_list_cameras_empty_fresh_tenant():
    """GET /cameras on a fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/cameras")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_ci_create_camera_returns_201_and_id():
    """POST /cameras returns 201 + {id, name}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/cameras", json={
            "name": "Entrance Cam", "location": "Main Gate",
            "latitude": 1.3521, "longitude": 103.8198,
        })
    assert r.status_code == 201
    body = r.json()
    assert "id" in body
    assert body["name"] == "Entrance Cam"


@pytest.mark.asyncio
async def test_ci_create_camera_appears_in_list():
    """After POST /cameras, the camera is visible via GET /cameras with expected fields."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cam_id = await _create_camera(c, name="Lobby Cam")
        r = await c.get("/api/v1/cameras")
    items = r.json()
    ids = [item["id"] for item in items]
    assert cam_id in ids
    match = next(item for item in items if item["id"] == cam_id)
    assert match["name"] == "Lobby Cam"
    assert match["is_active"] is True
    # List response includes all expected fields
    for field in ("id", "name", "location", "is_active", "ai_modules_enabled", "site_id", "site_name"):
        assert field in match


@pytest.mark.asyncio
async def test_ci_get_camera_by_id_returns_200():
    """GET /cameras/{id} returns the camera with full field set."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cam_id = await _create_camera(c, name="Gate Cam")
        r = await c.get(f"/api/v1/cameras/{cam_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == cam_id
    assert body["name"] == "Gate Cam"


@pytest.mark.asyncio
async def test_ci_get_camera_unknown_id_returns_404():
    """GET /cameras/{random_uuid} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/cameras/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ci_deactivate_camera_returns_204():
    """DELETE /cameras/{id} returns 204 with no body."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cam_id = await _create_camera(c, name="To Delete")
        r = await c.delete(f"/api/v1/cameras/{cam_id}")
    assert r.status_code == 204


# ─── B. Camera Update ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ci_update_camera_name():
    """PUT /cameras/{id} with new name returns 200 + updated name."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cam_id = await _create_camera(c, name="Old Name")
        r = await c.put(f"/api/v1/cameras/{cam_id}", json={"name": "New Name"})
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_ci_update_camera_no_fields_returns_422():
    """PUT /cameras/{id} with empty body returns 422 (no fields to update)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cam_id = await _create_camera(c)
        r = await c.put(f"/api/v1/cameras/{cam_id}", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_ci_update_camera_deactivate_via_is_active():
    """PUT /cameras/{id} with is_active=false deactivates the camera."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cam_id = await _create_camera(c, name="Active Cam")
        r = await c.put(f"/api/v1/cameras/{cam_id}", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] is False


# ─── C. AI Modules ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ci_create_camera_with_ai_modules():
    """POST /cameras with valid ai_modules_enabled returns them in list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/cameras", json={
            "name": "AI Cam", "ai_modules_enabled": ["lpr", "face", "intrusion"],
        })
        assert r.status_code == 201
        cam_id = r.json()["id"]
        r2 = await c.get(f"/api/v1/cameras/{cam_id}")
    modules = r2.json()["ai_modules_enabled"]
    assert set(modules) == {"lpr", "face", "intrusion"}


@pytest.mark.asyncio
async def test_ci_create_camera_invalid_module_silently_filtered():
    """Invalid module names in ai_modules_enabled are dropped — not a 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/cameras", json={
            "name": "Filter Cam", "ai_modules_enabled": ["lpr", "nonexistent_module"],
        })
        assert r.status_code == 201
        cam_id = r.json()["id"]
        r2 = await c.get(f"/api/v1/cameras/{cam_id}")
    modules = r2.json()["ai_modules_enabled"]
    assert "lpr" in modules
    assert "nonexistent_module" not in modules


# ─── D. Validate Stream SSRF Guard ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_ci_validate_stream_localhost_blocked_422():
    """POST /cameras/validate-stream with localhost RTSP URL returns 422 (SSRF guard)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/cameras/validate-stream", json={
            "url": "rtsp://127.0.0.1:554/live",
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_ci_validate_stream_private_ip_blocked_422():
    """POST /cameras/validate-stream with RFC 1918 IP returns 422 (SSRF guard)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/cameras/validate-stream", json={
            "url": "rtsp://192.168.1.100:554/stream",
        })
    assert r.status_code == 422


# ─── E. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ci_viewer_can_list_cameras():
    """Viewer (role 6) has camera:read — GET /cameras returns 200."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/cameras")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_ci_viewer_cannot_create_camera_403():
    """Viewer (role 6) does not have camera:create — POST /cameras returns 403."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/cameras", json={"name": "Viewer Cam"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ci_cameras_rls_isolation():
    """Tenant B cannot see Tenant A's cameras via GET /cameras."""
    _, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_a) as c:
        cam_id = await _create_camera(c, name="A's Camera")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/cameras")
    ids = [item["id"] for item in r.json()]
    assert cam_id not in ids
