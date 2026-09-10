"""Gap 76 — API Keys Router

Isolated-tenant tests for backend/app/routers/api_keys.py (105 lines).
Machine-to-machine authentication keys for external integrations.

Endpoints:
  GET    /api/v1/api-keys      — apikey:manage; list keys (never returns hash/full key)
  POST   /api/v1/api-keys      — apikey:manage; create key; full key returned ONCE
  DELETE /api/v1/api-keys/{id} — apikey:manage; soft-revoke (is_active=False); 404 if gone/revoked

Permission: apikey:manage granted to roles 1 (super_admin) and 2 (admin) only.

Sections:
  A — CRUD (5 tests)
  B — 404 cases (2 tests)
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
    slug = f"key-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"APIKey Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'APIKey Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"key-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_key(c: AsyncClient, name: str = "Test Key",
                      expires_at: str | None = None) -> dict:
    payload: dict = {"name": name}
    if expires_at:
        payload["expires_at"] = expires_at
    r = await c.post("/api/v1/api-keys", json=payload)
    assert r.status_code == 200, f"create_key failed: {r.text}"
    return r.json()


# ─── A. CRUD ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_key_list_empty_fresh_tenant():
    """GET /api-keys on fresh tenant returns 200 + []."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/api-keys")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_key_create_returns_full_key_once():
    """POST /api-keys returns full key (sav1_ prefix) + key_shown_once=True."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/api-keys", json={"name": "My Integration Key"})
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["name"] == "My Integration Key"
    assert "key" in body
    assert body["key"].startswith("sav1_")
    assert len(body["key"]) > 10
    assert body["key_shown_once"] is True
    assert "key_prefix" in body
    assert "created_at" in body


@pytest.mark.asyncio
async def test_key_created_key_appears_in_list():
    """After POST, key appears in GET list with created_by_email; full key NOT returned."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = await _create_key(c, "Integration Alpha")
        r = await c.get("/api/v1/api-keys")
    rows = r.json()
    ids = [row["id"] for row in rows]
    assert created["id"] in ids
    row = next(rw for rw in rows if rw["id"] == created["id"])
    assert row["name"] == "Integration Alpha"
    assert row["is_active"] is True
    assert "created_by_email" in row
    assert "key" not in row        # full key never returned in list
    assert "key_hash" not in row   # hash never exposed


@pytest.mark.asyncio
async def test_key_revoke_returns_revoked_true():
    """DELETE /api-keys/{id} returns {id, revoked: True} (soft delete)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = await _create_key(c, "Temp Key")
        r = await c.delete(f"/api/v1/api-keys/{created['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == created["id"]
    assert body["revoked"] is True


@pytest.mark.asyncio
async def test_key_create_with_expiry():
    """POST /api-keys with expires_at stores and returns the expiry."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/api-keys", json={
            "name": "Short-lived Key",
            "expires_at": "2030-12-31T23:59:59Z",
        })
    assert r.status_code == 200
    body = r.json()
    assert body["expires_at"] is not None
    assert "2030" in body["expires_at"]


# ─── B. 404 cases ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_key_revoke_already_revoked_returns_404():
    """DELETE /api-keys/{id} a second time returns 404 (already revoked)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = await _create_key(c, "One-time Key")
        await c.delete(f"/api/v1/api-keys/{created['id']}")   # first revoke
        r = await c.delete(f"/api/v1/api-keys/{created['id']}")  # second → 404
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_key_revoke_unknown_returns_404():
    """DELETE /api-keys/{random_uuid} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/api-keys/{uuid.uuid4()}")
    assert r.status_code == 404


# ─── C. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_key_supervisor_cannot_manage_403():
    """Supervisor (role 3) does not have apikey:manage → GET /api-keys returns 403."""
    _, _, token = await _seed_tenant_and_token(role_id=3)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/api-keys")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_key_rls_isolation():
    """Tenant B cannot see Tenant A's API keys."""
    _, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_a) as c:
        await _create_key(c, "Tenant A Key")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/api-keys")
    assert r.json() == []
