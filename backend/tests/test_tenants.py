"""Gap 56 — Tenants Router

First dedicated isolated-tenant test file for backend/app/routers/tenants.py
(199 lines, previously only ~10 incidental tests embedded in shared-fixture files).

All endpoints require tenant:manage (super_admin, role_id=1 only).
The router uses get_raw_db (no automatic RLS) — it operates globally across tenants.

Endpoints (prefix /api/v1/tenants):
  GET  /               — list ALL tenants (no RLS isolation, raw DB)
  POST /               — create tenant; seeds 11 license rows; optional admin user; 409 duplicate slug
  GET  /{id}           — get tenant detail; 404 not found
  PUT  /{id}           — partial update; 422 no fields; 404 not found
  DELETE /{id}         — soft-deactivate; 404 not found
  POST /{id}/users     — create user in target tenant; 201; 404 inactive/not-found; 409 dup email

Sections:
  A — List: 200, new tenants visible, requires tenant:manage (admin 403)
  B — Create: 201 + all fields, seeds 11 license rows, with admin user, dup slug 409, admin 403
  C — Get: all 10 fields, unknown 404, created tenant gettable
  D — Update: name change, no-fields 422, unknown 404, is_active=false deactivation
  E — Deactivate (DELETE): 200 + is_active False, unknown 404, soft delete
  F — Create tenant user: 201 + user fields, inactive tenant 404, duplicate email 409
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


async def _seed_super_admin_and_token():
    """Create a home tenant + super_admin user; return (home_tenant_id, user_id, token)."""
    from app.core.security import create_access_token, hash_password

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"tnt-home-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Super Home {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                "VALUES (:id, :tid, 1, :email, :pw)"
            ),
            {
                "id": user_id,
                "tid": tenant_id,
                "email": f"super-{user_id.hex[:8]}@test.local",
                "pw": "hashed",
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), 1)
    return tenant_id, user_id, token


async def _seed_admin_and_token():
    """Create a home tenant + admin user (role 2, no tenant:manage); return token."""
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"tnt-adm-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Admin Home {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                "VALUES (:id, :tid, 2, :email, 'hashed')"
            ),
            {"id": user_id, "tid": tenant_id, "email": f"adm-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return create_access_token(str(user_id), str(tenant_id), 2)


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _unique_slug() -> str:
    return f"tnt-{uuid.uuid4().hex[:12]}"


# ─── A. List tenants ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_tenants_returns_200():
    """GET /tenants returns 200 for super_admin."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/tenants")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_list_tenants_shows_newly_created_tenants():
    """Tenants created via POST /tenants appear in GET /tenants."""
    _, _, token = await _seed_super_admin_and_token()
    slug_a = _unique_slug()
    async with await _authed(token) as c:
        await c.post("/api/v1/tenants", json={"name": "List Test Tenant", "slug": slug_a})
        r = await c.get("/api/v1/tenants")
    slugs = [t["slug"] for t in r.json()]
    assert slug_a in slugs


@pytest.mark.asyncio
async def test_list_tenants_admin_role_returns_403():
    """admin (role 2) without tenant:manage receives 403."""
    admin_token = await _seed_admin_and_token()
    async with await _authed(admin_token) as c:
        r = await c.get("/api/v1/tenants")
    assert r.status_code == 403


# ─── B. Create tenant ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_tenant_returns_201():
    """POST /tenants returns 201 with id and slug."""
    _, _, token = await _seed_super_admin_and_token()
    slug = _unique_slug()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/tenants", json={"name": "New Tenant", "slug": slug})
    assert r.status_code == 201
    body = r.json()
    assert "id" in body
    assert body["slug"] == slug
    assert body["name"] == "New Tenant"


@pytest.mark.asyncio
async def test_create_tenant_response_has_all_fields():
    """POST /tenants response includes all 10 _TENANT_COLS fields."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/tenants", json={"name": "Field Test", "slug": _unique_slug()})
    body = r.json()
    required = {"id", "name", "slug", "subdomain", "custom_domain", "timezone",
                "branding", "is_active", "created_at", "updated_at"}
    assert required.issubset(body.keys())
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_create_tenant_seeds_11_license_rows():
    """POST /tenants auto-seeds 11 disabled license rows (all AI modules)."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/tenants", json={"name": "License Test", "slug": _unique_slug()})
        tenant_id = create_r.json()["id"]
        r = await c.get(f"/api/v1/licenses/{tenant_id}")
    assert r.status_code == 200
    assert len(r.json()) == 11
    assert all(not entry["is_enabled"] for entry in r.json())


@pytest.mark.asyncio
async def test_create_tenant_with_admin_user():
    """POST /tenants with admin_email+admin_password creates a seed admin user."""
    _, _, token = await _seed_super_admin_and_token()
    slug = _unique_slug()
    email = f"seedadmin-{slug}@test.local"
    async with await _authed(token) as c:
        create_r = await c.post(
            "/api/v1/tenants",
            json={"name": "With Admin", "slug": slug,
                  "admin_email": email, "admin_password": "pass123",
                  "admin_full_name": "Seed Admin"},
        )
        tenant_id = create_r.json()["id"]
        # Verify by trying to create same email again → 409 conflict
        r = await c.post(
            f"/api/v1/tenants/{tenant_id}/users",
            json={"email": email, "password": "pass456", "role_id": 2},
        )
    assert r.status_code == 409, "Duplicate email should return 409 — confirms seed admin was created"


@pytest.mark.asyncio
async def test_create_tenant_duplicate_slug_returns_409():
    """POST /tenants with an existing slug returns 409."""
    _, _, token = await _seed_super_admin_and_token()
    slug = _unique_slug()
    async with await _authed(token) as c:
        await c.post("/api/v1/tenants", json={"name": "First", "slug": slug})
        r = await c.post("/api/v1/tenants", json={"name": "Second", "slug": slug})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_tenant_requires_tenant_manage():
    """admin (role 2) receives 403 on POST /tenants."""
    admin_token = await _seed_admin_and_token()
    async with await _authed(admin_token) as c:
        r = await c.post("/api/v1/tenants", json={"name": "Forbidden", "slug": _unique_slug()})
    assert r.status_code == 403


# ─── C. Get tenant ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_tenant_returns_all_fields():
    """GET /tenants/{id} returns all 10 _TENANT_COLS fields."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/tenants", json={"name": "Get Test", "slug": _unique_slug()})
        tenant_id = create_r.json()["id"]
        r = await c.get(f"/api/v1/tenants/{tenant_id}")
    assert r.status_code == 200
    body = r.json()
    required = {"id", "name", "slug", "subdomain", "custom_domain", "timezone",
                "branding", "is_active", "created_at", "updated_at"}
    assert required.issubset(body.keys())


@pytest.mark.asyncio
async def test_get_tenant_unknown_returns_404():
    """GET /tenants/{unknown} returns 404."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/tenants/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_tenant_returns_correct_slug():
    """GET /tenants/{id} returns the same slug as was used in create."""
    _, _, token = await _seed_super_admin_and_token()
    slug = _unique_slug()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/tenants", json={"name": "Slug Check", "slug": slug})
        tenant_id = create_r.json()["id"]
        r = await c.get(f"/api/v1/tenants/{tenant_id}")
    assert r.json()["slug"] == slug


# ─── D. Update tenant ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_tenant_name():
    """PUT /tenants/{id} with {name: ...} updates the name."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/tenants", json={"name": "Old Name", "slug": _unique_slug()})
        tenant_id = create_r.json()["id"]
        r = await c.put(f"/api/v1/tenants/{tenant_id}", json={"name": "New Name"})
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_update_tenant_no_fields_returns_422():
    """PUT /tenants/{id} with empty body returns 422."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/tenants", json={"name": "No Update", "slug": _unique_slug()})
        tenant_id = create_r.json()["id"]
        r = await c.put(f"/api/v1/tenants/{tenant_id}", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_tenant_unknown_returns_404():
    """PUT /tenants/{unknown} returns 404."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        r = await c.put(f"/api/v1/tenants/{uuid.uuid4()}", json={"name": "Ghost"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_tenant_deactivate_via_is_active():
    """PUT /tenants/{id} with {is_active: false} deactivates the tenant."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/tenants", json={"name": "Will Deactivate", "slug": _unique_slug()})
        tenant_id = create_r.json()["id"]
        r = await c.put(f"/api/v1/tenants/{tenant_id}", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] is False


# ─── E. Deactivate tenant (DELETE) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deactivate_tenant_returns_200():
    """DELETE /tenants/{id} returns 200 with {id, is_active: False}."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/tenants", json={"name": "Delete Me", "slug": _unique_slug()})
        tenant_id = create_r.json()["id"]
        r = await c.delete(f"/api/v1/tenants/{tenant_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["is_active"] is False


@pytest.mark.asyncio
async def test_deactivate_tenant_unknown_returns_404():
    """DELETE /tenants/{unknown} returns 404."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/tenants/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_deactivate_tenant_is_soft_delete():
    """DELETE marks is_active=False but the tenant still exists and is gettable."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/tenants", json={"name": "Soft Deleted", "slug": _unique_slug()})
        tenant_id = create_r.json()["id"]
        await c.delete(f"/api/v1/tenants/{tenant_id}")
        get_r = await c.get(f"/api/v1/tenants/{tenant_id}")
    assert get_r.status_code == 200
    assert get_r.json()["is_active"] is False


# ─── F. Create user in tenant ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_tenant_user_returns_201():
    """POST /tenants/{id}/users returns 201 with user fields."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/tenants", json={"name": "User Target", "slug": _unique_slug()})
        tenant_id = create_r.json()["id"]
        r = await c.post(
            f"/api/v1/tenants/{tenant_id}/users",
            json={"email": "newuser@target.local", "password": "pass123", "role_id": 3,
                  "full_name": "New User"},
        )
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "newuser@target.local"
    assert body["role_id"] == 3
    assert "id" in body


@pytest.mark.asyncio
async def test_create_tenant_user_inactive_tenant_returns_404():
    """POST /tenants/{id}/users on an inactive tenant returns 404."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/tenants", json={"name": "Inactive Target", "slug": _unique_slug()})
        tenant_id = create_r.json()["id"]
        await c.delete(f"/api/v1/tenants/{tenant_id}")
        r = await c.post(
            f"/api/v1/tenants/{tenant_id}/users",
            json={"email": "blocked@target.local", "password": "pass123"},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_tenant_user_duplicate_email_returns_409():
    """POST /tenants/{id}/users with an existing email returns 409."""
    _, _, token = await _seed_super_admin_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/tenants", json={"name": "Dup Email Test", "slug": _unique_slug()})
        tenant_id = create_r.json()["id"]
        await c.post(f"/api/v1/tenants/{tenant_id}/users",
                     json={"email": "dup@target.local", "password": "pass1"})
        r = await c.post(f"/api/v1/tenants/{tenant_id}/users",
                         json={"email": "dup@target.local", "password": "pass2"})
    assert r.status_code == 409
