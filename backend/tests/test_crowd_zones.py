"""Gap 73 — Crowd Zones Router

Isolated-tenant tests for backend/app/routers/crowd_zones.py (120 lines).
Permission: zone:manage (admin/supervisor, roles 1-3). Viewer (role 6) → 403.

Endpoints:
  GET    /api/v1/crowd-zones         — list all
  POST   /api/v1/crowd-zones         — create (201); camera_id + name + polygon required
  PUT    /api/v1/crowd-zones/{id}    — update (200); no fields → 422; unknown → 404
  DELETE /api/v1/crowd-zones/{id}    — deactivate (200) → {id, is_active: False}

max_capacity ≥ 1, severity in {low, medium, high, critical}.

Sections:
  A — CRUD (6 tests)
  B — Validation (3 tests)
  C — Permissions + RLS (2 tests)
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
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"czn-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"CZone Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'CZone Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"czn-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera(tenant_id: uuid.UUID) -> uuid.UUID:
    cam_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO cameras (id, tenant_id, name, location) VALUES (:id, :tid, :n, :l)"),
            {"id": cam_id, "tid": tenant_id, "n": "CZone Cam", "l": "Lobby"},
        )
        await s.commit()
    await engine.dispose()
    return cam_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


_POLYGON = [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}, {"x": 0.9, "y": 0.9}]


async def _create_zone(c: AsyncClient, camera_id: str, name: str = "Lobby Zone") -> dict:
    r = await c.post("/api/v1/crowd-zones", json={
        "camera_id": camera_id,
        "name": name,
        "polygon": _POLYGON,
        "max_capacity": 20,
        "severity": "medium",
    })
    assert r.status_code == 201, f"create_zone failed: {r.text}"
    return r.json()


# ─── A. CRUD ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_czn_list_empty_fresh_tenant():
    """GET /crowd-zones on fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/crowd-zones")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_czn_create_returns_id():
    """POST /crowd-zones returns 201 + {id}."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/crowd-zones", json={
            "camera_id": str(cam_id),
            "name": "Entrance Zone",
            "polygon": _POLYGON,
            "max_capacity": 50,
            "severity": "high",
        })
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_czn_created_zone_appears_in_list():
    """After POST, the zone appears in GET /crowd-zones with expected fields."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        zone = await _create_zone(c, str(cam_id), "Hall Zone")
        r = await c.get("/api/v1/crowd-zones")
    rows = r.json()
    ids = [row["id"] for row in rows]
    assert zone["id"] in ids
    row = next(z for z in rows if z["id"] == zone["id"])
    assert row["name"] == "Hall Zone"
    assert row["max_capacity"] == 20
    assert row["is_active"] is True


@pytest.mark.asyncio
async def test_czn_update_max_capacity():
    """PUT /crowd-zones/{id} with max_capacity=100 updates it."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        zone = await _create_zone(c, str(cam_id))
        r = await c.put(f"/api/v1/crowd-zones/{zone['id']}", json={"max_capacity": 100})
    assert r.status_code == 200
    assert r.json()["max_capacity"] == 100


@pytest.mark.asyncio
async def test_czn_deactivate_sets_is_active_false():
    """DELETE /crowd-zones/{id} returns {id, is_active: False}."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        zone = await _create_zone(c, str(cam_id))
        r = await c.delete(f"/api/v1/crowd-zones/{zone['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == zone["id"]
    assert body["is_active"] is False


@pytest.mark.asyncio
async def test_czn_deactivated_zone_still_in_list():
    """After DELETE, the deactivated zone still appears in GET /crowd-zones (soft delete)."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        zone = await _create_zone(c, str(cam_id))
        await c.delete(f"/api/v1/crowd-zones/{zone['id']}")
        r = await c.get("/api/v1/crowd-zones")
    ids = [z["id"] for z in r.json()]
    assert zone["id"] in ids


# ─── B. Validation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_czn_invalid_severity_returns_422():
    """POST with severity='deadly' returns 422."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/crowd-zones", json={
            "camera_id": str(cam_id),
            "name": "Bad Zone",
            "polygon": _POLYGON,
            "severity": "deadly",
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_czn_update_no_fields_returns_422():
    """PUT /crowd-zones/{id} with empty body returns 422."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        zone = await _create_zone(c, str(cam_id))
        r = await c.put(f"/api/v1/crowd-zones/{zone['id']}", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_czn_update_unknown_zone_returns_404():
    """PUT /crowd-zones/{random_uuid} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put(f"/api/v1/crowd-zones/{uuid.uuid4()}", json={"name": "Ghost"})
    assert r.status_code == 404


# ─── C. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_czn_viewer_cannot_list_zones_403():
    """Viewer (role 6) does not have zone:manage → GET /crowd-zones returns 403."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/crowd-zones")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_czn_rls_isolation():
    """Tenant B cannot see Tenant A's crowd zones."""
    tenant_a, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_a)
    async with await _authed(tok_a) as c:
        await _create_zone(c, str(cam_id), "Tenant A Zone")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/crowd-zones")
    assert r.json() == []
