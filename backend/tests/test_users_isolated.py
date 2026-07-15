"""Gap 69 — Users Router (isolated-tenant)

Isolated-tenant tests for backend/app/routers/users.py (158 lines).
Separate from existing shared-fixture user tests.

Endpoints (prefix /api/v1/users):
  GET    /                  — user:read; list tenant users (no hashed_password)
  POST   /                  — user:create; 201; duplicate email → 409
  GET    /{id}              — user:read; 404 unknown
  PUT    /{id}              — user:update; partial; no fields → 422; 404 unknown
  DELETE /{id}              — user:delete; soft-deactivate is_active=False; 404 unknown
  POST   /me/push-token     — any auth; registers Expo token in Redis
  DELETE /me/push-token     — any auth; removes token

Sections:
  A — User CRUD (6 tests)
  B — Validation (3 tests)
  C — Permissions + RLS (3 tests)
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
    slug = f"usr-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Usr Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Usr Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"usr-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_user(c: AsyncClient) -> dict:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    r = await c.post("/api/v1/users", json={
        "email": email,
        "password": "Secret123!",
        "role_id": 4,
        "full_name": "Test Operator",
    })
    assert r.status_code == 201, f"create_user failed: {r.text}"
    return r.json()


# ─── A. User CRUD ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_usr_list_users_has_seeded_user():
    """GET /users returns list containing the seeded admin user; no hashed_password exposed."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/users")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    ids = [u["id"] for u in body]
    assert str(user_id) in ids
    # Ensure hashed_password is never returned
    for u in body:
        assert "hashed_password" not in u


@pytest.mark.asyncio
async def test_usr_create_user_returns_201_and_fields():
    """POST /users returns 201 + {id, email, role_id, full_name, is_active, created_at}."""
    _, _, token = await _seed_tenant_and_token()
    email = f"newuser-{uuid.uuid4().hex[:8]}@example.com"
    async with await _authed(token) as c:
        r = await c.post("/api/v1/users", json={
            "email": email,
            "password": "Pass123!",
            "role_id": 5,
            "full_name": "Guard Smith",
        })
    assert r.status_code == 201
    body = r.json()
    assert "id" in body
    assert body["email"] == email
    assert body["role_id"] == 5
    assert body["full_name"] == "Guard Smith"
    assert body["is_active"] is True
    assert "hashed_password" not in body


@pytest.mark.asyncio
async def test_usr_created_user_appears_in_list():
    """After POST /users, the new user is visible via GET /users."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        user = await _create_user(c)
        r = await c.get("/api/v1/users")
    ids = [u["id"] for u in r.json()]
    assert user["id"] in ids


@pytest.mark.asyncio
async def test_usr_get_user_by_id():
    """GET /users/{id} returns the user with expected fields."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        user = await _create_user(c)
        r = await c.get(f"/api/v1/users/{user['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == user["id"]
    assert body["email"] == user["email"]
    assert "hashed_password" not in body


@pytest.mark.asyncio
async def test_usr_update_user_full_name():
    """PUT /users/{id} with full_name updates it; returns {id, role_id, full_name, is_active}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        user = await _create_user(c)
        r = await c.put(f"/api/v1/users/{user['id']}", json={"full_name": "Updated Name"})
    assert r.status_code == 200
    assert r.json()["full_name"] == "Updated Name"


@pytest.mark.asyncio
async def test_usr_deactivate_user():
    """DELETE /users/{id} sets is_active=False; returns {id, is_active: False}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        user = await _create_user(c)
        r = await c.delete(f"/api/v1/users/{user['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == user["id"]
    assert body["is_active"] is False


# ─── B. Validation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_usr_create_duplicate_email_returns_409():
    """POST /users with an already-used email in the same tenant returns 409."""
    _, _, token = await _seed_tenant_and_token()
    email = f"dup-{uuid.uuid4().hex[:8]}@example.com"
    async with await _authed(token) as c:
        await c.post("/api/v1/users", json={"email": email, "password": "P", "role_id": 4})
        r = await c.post("/api/v1/users", json={"email": email, "password": "P", "role_id": 4})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_usr_get_unknown_user_returns_404():
    """GET /users/{random_uuid} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/users/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_usr_update_no_fields_returns_422():
    """PUT /users/{id} with empty body returns 422 (no fields to update)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        user = await _create_user(c)
        r = await c.put(f"/api/v1/users/{user['id']}", json={})
    assert r.status_code == 422


# ─── C. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_usr_viewer_cannot_list_users_403():
    """Viewer (role 6) does not have user:read → GET /users returns 403."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/users")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_usr_rls_isolation_list():
    """Tenant B cannot see Tenant A's users via GET /users."""
    tenant_a, user_a, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/users")
    ids = [u["id"] for u in r.json()]
    assert str(user_a) not in ids


@pytest.mark.asyncio
async def test_usr_rls_isolation_get_by_id():
    """Tenant B cannot GET Tenant A's user by ID — returns 404 (RLS hides it)."""
    _, user_a, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_b) as c:
        r = await c.get(f"/api/v1/users/{user_a}")
    assert r.status_code == 404
