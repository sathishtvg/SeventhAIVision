"""Gap 54 — Sites Router

First dedicated isolated-tenant test file for backend/app/routers/sites.py
(158 lines, previously only ~5 incidental tests spread across 3 files).

Endpoints (prefix /api/v1/sites):
  GET /                    — list sites (camera:read, any role)
  POST /                   — create site (site:manage, roles 1-3)
  GET /{id}                — get site detail (site:manage)
  PUT /{id}                — partial update (site:manage; 400 no fields; 404 not found)
  DELETE /{id}             — soft-deactivate (site:manage; 404 not found)
  GET /{id}/cameras        — list cameras in site (camera:read)

Sections:
  A — List sites: empty list, created site visible with camera_count=0,
      is_active filter, camera_count increments, RLS isolation
  B — Create site: 201 + id+name, requires site:manage (viewer 403), visible in list
  C — Get site: all fields + camera_count, unknown 404, cross-tenant 404 (RLS)
  D — Update site: name change, no-fields 400, unknown 404, is_active=false
  E — Deactivate (DELETE): 200 + is_active=False, unknown 404, soft delete (site still exists)
  F — List site cameras: empty, with camera, unknown site 404, cross-tenant 404 (RLS)
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
    """Create isolated tenant + user; return (tenant_id, user_id, jwt_token)."""
    from app.core.security import create_access_token, hash_password

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"sit-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Sit Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                "VALUES (:id, :tid, :role, :email, :pw)"
            ),
            {
                "id": user_id,
                "tid": tenant_id,
                "role": role_id,
                "email": f"sit-{user_id.hex[:8]}@test.local",
                "pw": "hashed",
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera(tenant_id: uuid.UUID, site_id: str | None = None) -> str:
    """Insert a camera with optional site_id; return its UUID string."""
    cam_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO cameras (id, tenant_id, name, site_id) "
                "VALUES (:id, :tid, :name, CAST(:sid AS uuid))"
            ),
            {"id": cam_id, "tid": tenant_id, "name": f"Cam {cam_id.hex[:6]}", "sid": site_id},
        )
        await s.commit()
    await engine.dispose()
    return str(cam_id)


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. List sites ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_sites_empty_returns_200():
    """GET /sites on a fresh tenant returns 200 with an empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/sites")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_sites_shows_created_site_with_camera_count_zero():
    """A newly created site appears in the list with camera_count=0."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await c.post("/api/v1/sites", json={"name": "Site Alpha"})
        r = await c.get("/api/v1/sites")
    sites = r.json()
    assert len(sites) == 1
    assert sites[0]["name"] == "Site Alpha"
    assert sites[0]["camera_count"] == 0


@pytest.mark.asyncio
async def test_list_sites_is_active_true_filter():
    """?is_active=true excludes deactivated sites."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "To Deactivate"})
        site_id = create_r.json()["id"]
        await c.delete(f"/api/v1/sites/{site_id}")
        r = await c.get("/api/v1/sites", params={"is_active": "true"})
    assert r.status_code == 200
    assert len(r.json()) == 0


@pytest.mark.asyncio
async def test_list_sites_is_active_false_filter():
    """?is_active=false shows only deactivated sites."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "Deactivated Site"})
        site_id = create_r.json()["id"]
        await c.delete(f"/api/v1/sites/{site_id}")
        r = await c.get("/api/v1/sites", params={"is_active": "false"})
    sites = r.json()
    assert r.status_code == 200
    assert len(sites) == 1
    assert sites[0]["is_active"] is False


@pytest.mark.asyncio
async def test_list_sites_camera_count_reflects_linked_cameras():
    """A site with one active camera shows camera_count=1."""
    tenant_id, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "Counted Site"})
        site_id = create_r.json()["id"]
    await _seed_camera(tenant_id, site_id)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/sites")
    sites = r.json()
    assert sites[0]["camera_count"] == 1


@pytest.mark.asyncio
async def test_list_sites_rls_isolation():
    """Tenant A cannot see tenant B's sites."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()
    async with await _authed(token_b) as c:
        await c.post("/api/v1/sites", json={"name": "Tenant B Site"})
    async with await _authed(token_a) as c:
        r = await c.get("/api/v1/sites")
    assert r.json() == []


# ─── B. Create site ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_site_returns_201():
    """POST /sites returns 201 with id and name."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/sites", json={"name": "Beta Site"})
    assert r.status_code == 201
    body = r.json()
    assert "id" in body
    assert body["name"] == "Beta Site"


@pytest.mark.asyncio
async def test_create_site_with_all_fields():
    """POST /sites with all optional fields returns 201."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/sites",
            json={
                "name": "Full Site",
                "address": "123 Test Rd",
                "description": "Test site desc",
                "latitude": 1.3521,
                "longitude": 103.8198,
            },
        )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_create_site_requires_site_manage():
    """viewer (role 6, no site:manage) receives 403 on POST /sites."""
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.post("/api/v1/sites", json={"name": "Forbidden Site"})
    assert r.status_code == 403


# ─── C. Get site ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_site_returns_all_fields():
    """GET /sites/{id} returns all expected fields including camera_count."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "Detail Site"})
        site_id = create_r.json()["id"]
        r = await c.get(f"/api/v1/sites/{site_id}")
    assert r.status_code == 200
    body = r.json()
    required = {"id", "name", "address", "description", "latitude", "longitude",
                "is_active", "created_at", "updated_at", "camera_count"}
    assert required.issubset(body.keys())
    assert body["is_active"] is True
    assert body["camera_count"] == 0


@pytest.mark.asyncio
async def test_get_site_unknown_returns_404():
    """GET /sites/{unknown} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/sites/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_site_cross_tenant_returns_404():
    """GET /sites/{id} for a site in another tenant returns 404 (RLS)."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()
    async with await _authed(token_b) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "Tenant B Only"})
        site_b_id = create_r.json()["id"]
    async with await _authed(token_a) as c:
        r = await c.get(f"/api/v1/sites/{site_b_id}")
    assert r.status_code == 404


# ─── D. Update site ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_site_name():
    """PUT /sites/{id} with {name: ...} updates the name."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "Old Name"})
        site_id = create_r.json()["id"]
        r = await c.put(f"/api/v1/sites/{site_id}", json={"name": "New Name"})
        assert r.status_code == 200
        get_r = await c.get(f"/api/v1/sites/{site_id}")
    assert get_r.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_update_site_no_fields_returns_400():
    """PUT /sites/{id} with an empty body returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "No Update Site"})
        site_id = create_r.json()["id"]
        r = await c.put(f"/api/v1/sites/{site_id}", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_site_unknown_returns_404():
    """PUT /sites/{unknown} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put(f"/api/v1/sites/{uuid.uuid4()}", json={"name": "Ghost"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_site_is_active_false():
    """PUT /sites/{id} with {is_active: false} deactivates the site."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "Will Deactivate"})
        site_id = create_r.json()["id"]
        await c.put(f"/api/v1/sites/{site_id}", json={"is_active": False})
        get_r = await c.get(f"/api/v1/sites/{site_id}")
    assert get_r.json()["is_active"] is False


# ─── E. Deactivate (DELETE) ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deactivate_site_returns_200_with_is_active_false():
    """DELETE /sites/{id} returns 200 with {is_active: False}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "Delete Me"})
        site_id = create_r.json()["id"]
        r = await c.delete(f"/api/v1/sites/{site_id}")
    assert r.status_code == 200
    assert r.json()["is_active"] is False


@pytest.mark.asyncio
async def test_deactivate_site_unknown_returns_404():
    """DELETE /sites/{unknown} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/sites/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_deactivate_site_is_soft_delete():
    """DELETE marks is_active=False but the site still exists and is gettable."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "Soft Deleted"})
        site_id = create_r.json()["id"]
        await c.delete(f"/api/v1/sites/{site_id}")
        get_r = await c.get(f"/api/v1/sites/{site_id}")
    assert get_r.status_code == 200
    assert get_r.json()["is_active"] is False


# ─── F. List site cameras ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_site_cameras_empty():
    """GET /sites/{id}/cameras on a site with no cameras returns 200 + []."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "No Cameras"})
        site_id = create_r.json()["id"]
        r = await c.get(f"/api/v1/sites/{site_id}/cameras")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_site_cameras_with_camera():
    """GET /sites/{id}/cameras returns cameras linked to that site."""
    tenant_id, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "Has Camera"})
        site_id = create_r.json()["id"]
    cam_id = await _seed_camera(tenant_id, site_id)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/sites/{site_id}/cameras")
    assert r.status_code == 200
    cameras = r.json()
    assert len(cameras) == 1
    assert cameras[0]["id"] == cam_id


@pytest.mark.asyncio
async def test_list_site_cameras_unknown_site_returns_404():
    """GET /sites/{unknown}/cameras returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/sites/{uuid.uuid4()}/cameras")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_site_cameras_cross_tenant_returns_404():
    """GET /sites/{id}/cameras for a site in another tenant returns 404 (RLS)."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()
    async with await _authed(token_b) as c:
        create_r = await c.post("/api/v1/sites", json={"name": "B Cameras Site"})
        site_b_id = create_r.json()["id"]
    async with await _authed(token_a) as c:
        r = await c.get(f"/api/v1/sites/{site_b_id}/cameras")
    assert r.status_code == 404
