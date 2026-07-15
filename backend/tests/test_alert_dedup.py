"""Gap 68 — Alert Deduplication Rules Router

Isolated-tenant tests for backend/app/routers/alert_dedup.py (195 lines).
Permission: alert:dedup:manage (admin/supervisor, roles 1-3).

Endpoints (prefix /api/v1/alert-dedup-rules):
  GET    /           — list all rules with camera_name JOIN
  POST   /           — create rule; body: {module_type?, camera_id?, window_seconds, is_active}
  GET    /{id}       — get single rule + camera_name; 404 unknown
  PUT    /{id}       — update rule; 404 unknown
  DELETE /{id}       — delete rule; {deleted: id}; 404 unknown

Rule body: module_type (None=wildcard), camera_id (None=wildcard),
           window_seconds (1-86400), is_active bool.

Sections:
  A — Rule CRUD (6 tests)
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
    slug = f"ded-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Dedup Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Ded Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"ded-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_rule(c: AsyncClient, **kwargs) -> dict:
    body = {"window_seconds": 300, **kwargs}
    r = await c.post("/api/v1/alert-dedup-rules", json=body)
    assert r.status_code == 200, f"create rule failed: {r.text}"
    return r.json()


# ─── A. Rule CRUD ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ded_list_rules_empty_fresh_tenant():
    """GET /alert-dedup-rules on fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/alert-dedup-rules")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_ded_create_rule_returns_fields():
    """POST /alert-dedup-rules returns rule row with all key fields."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/alert-dedup-rules", json={
            "module_type": "lpr",
            "window_seconds": 600,
            "is_active": True,
        })
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["module_type"] == "lpr"
    assert body["window_seconds"] == 600
    assert body["is_active"] is True
    assert "created_at" in body


@pytest.mark.asyncio
async def test_ded_created_rule_appears_in_list():
    """After POST, the rule is visible in GET /alert-dedup-rules."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        rule = await _create_rule(c, module_type="face", window_seconds=300)
        r = await c.get("/api/v1/alert-dedup-rules")
    ids = [row["id"] for row in r.json()]
    assert rule["id"] in ids


@pytest.mark.asyncio
async def test_ded_get_rule_by_id_returns_camera_name():
    """GET /alert-dedup-rules/{id} returns the rule including camera_name field."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        rule = await _create_rule(c, module_type="intrusion", window_seconds=120)
        r = await c.get(f"/api/v1/alert-dedup-rules/{rule['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == rule["id"]
    assert "camera_name" in body
    assert body["module_type"] == "intrusion"


@pytest.mark.asyncio
async def test_ded_update_rule_changes_window():
    """PUT /alert-dedup-rules/{id} updates window_seconds."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        rule = await _create_rule(c, window_seconds=300)
        r = await c.put(f"/api/v1/alert-dedup-rules/{rule['id']}", json={
            "window_seconds": 900,
            "is_active": True,
        })
    assert r.status_code == 200
    assert r.json()["window_seconds"] == 900


@pytest.mark.asyncio
async def test_ded_delete_rule_removes_it():
    """DELETE /alert-dedup-rules/{id} returns {deleted: id}; subsequent GET returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        rule = await _create_rule(c, window_seconds=300)
        r_del = await c.delete(f"/api/v1/alert-dedup-rules/{rule['id']}")
        assert r_del.status_code == 200
        assert r_del.json()["deleted"] == rule["id"]
        r_get = await c.get(f"/api/v1/alert-dedup-rules/{rule['id']}")
    assert r_get.status_code == 404


# ─── B. Validation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ded_get_unknown_rule_returns_404():
    """GET /alert-dedup-rules/{random_uuid} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/alert-dedup-rules/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ded_delete_unknown_rule_returns_404():
    """DELETE /alert-dedup-rules/{random_uuid} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/alert-dedup-rules/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ded_window_seconds_too_large_returns_422():
    """POST /alert-dedup-rules with window_seconds=86401 returns 422 (le=86400)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/alert-dedup-rules", json={"window_seconds": 86401})
    assert r.status_code == 422


# ─── C. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ded_viewer_cannot_list_rules_403():
    """Viewer (role 6) does not have alert:dedup:manage → 403."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/alert-dedup-rules")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ded_rls_isolation_list():
    """Tenant B cannot see Tenant A's rules via GET /alert-dedup-rules."""
    _, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_a) as c:
        await _create_rule(c, module_type="lpr", window_seconds=300)
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/alert-dedup-rules")
    assert r.json() == []


@pytest.mark.asyncio
async def test_ded_rls_isolation_get_by_id():
    """Tenant B cannot GET Tenant A's rule by ID — returns 404 (RLS hides it)."""
    _, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_a) as c:
        rule = await _create_rule(c, module_type="lpr", window_seconds=300)
    async with await _authed(tok_b) as c:
        r = await c.get(f"/api/v1/alert-dedup-rules/{rule['id']}")
    assert r.status_code == 404
