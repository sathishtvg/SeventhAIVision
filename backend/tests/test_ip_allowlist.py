"""Gap 75 — IP Allowlist Router

Isolated-tenant tests for backend/app/routers/ip_allowlist.py (113 lines).
Per-tenant CIDR-based access control rules.

Endpoints:
  GET    /api/v1/ip-allowlist              — iplist:manage; list rules + created_by_email
  POST   /api/v1/ip-allowlist              — iplist:manage; add rule; bare IP normalised to /32
  DELETE /api/v1/ip-allowlist/{id}         — iplist:manage; hard delete; returns {id, deleted}
  PATCH  /api/v1/ip-allowlist/{id}/toggle  — iplist:manage; flip is_active

Permission: iplist:manage granted to roles 1 (super_admin) and 2 (admin) only.
Viewer (role 6) and Supervisor (role 3) → 403.

Sections:
  A — CRUD (5 tests)
  B — Validation (3 tests)
  C — 404 cases (2 tests)
  D — Permissions + RLS (2 tests)
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
    slug = f"ipl-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"IPList Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'IPList Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"ipl-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _add_rule(c: AsyncClient, cidr: str = "10.0.0.0/8",
                   description: str | None = "Test rule") -> dict:
    r = await c.post("/api/v1/ip-allowlist", json={"cidr": cidr, "description": description})
    assert r.status_code == 200, f"add_rule failed: {r.text}"
    return r.json()


# ─── A. CRUD ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ipl_list_empty_fresh_tenant():
    """GET /ip-allowlist on fresh tenant returns 200 + []."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/ip-allowlist")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_ipl_create_returns_required_fields():
    """POST /ip-allowlist returns {id, cidr, description, is_active, created_at}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/ip-allowlist", json={
            "cidr": "192.168.1.0/24",
            "description": "Office network",
        })
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["cidr"] == "192.168.1.0/24"
    assert body["description"] == "Office network"
    assert body["is_active"] is True
    assert "created_at" in body


@pytest.mark.asyncio
async def test_ipl_created_rule_appears_in_list():
    """After POST, rule appears in GET list with created_by_email field."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        rule = await _add_rule(c, "172.16.0.0/12")
        r = await c.get("/api/v1/ip-allowlist")
    rows = r.json()
    ids = [row["id"] for row in rows]
    assert rule["id"] in ids
    row = next(rw for rw in rows if rw["id"] == rule["id"])
    assert row["cidr"] == "172.16.0.0/12"
    assert "created_by_email" in row


@pytest.mark.asyncio
async def test_ipl_delete_removes_rule():
    """DELETE /ip-allowlist/{id} returns {id, deleted: True} and removes the rule."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        rule = await _add_rule(c, "10.1.0.0/16")
        r = await c.delete(f"/api/v1/ip-allowlist/{rule['id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == rule["id"]
        assert body["deleted"] is True
        r2 = await c.get("/api/v1/ip-allowlist")
    ids = [row["id"] for row in r2.json()]
    assert rule["id"] not in ids


@pytest.mark.asyncio
async def test_ipl_toggle_flips_is_active():
    """PATCH /ip-allowlist/{id}/toggle toggles is_active; second call restores it."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        rule = await _add_rule(c, "10.2.0.0/16")
        r1 = await c.patch(f"/api/v1/ip-allowlist/{rule['id']}/toggle")
        assert r1.status_code == 200
        assert r1.json()["is_active"] is False
        r2 = await c.patch(f"/api/v1/ip-allowlist/{rule['id']}/toggle")
        assert r2.json()["is_active"] is True


# ─── B. Validation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ipl_invalid_cidr_returns_422():
    """POST with an invalid CIDR string returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/ip-allowlist", json={"cidr": "not-a-cidr"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_ipl_bare_ip_normalised_to_cidr():
    """POST with a bare IPv4 address stores it as /32 CIDR."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/ip-allowlist", json={"cidr": "203.0.113.5"})
    assert r.status_code == 200
    assert r.json()["cidr"] == "203.0.113.5/32"


@pytest.mark.asyncio
async def test_ipl_duplicate_cidr_returns_409():
    """POST the same CIDR twice returns 409 Conflict."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await _add_rule(c, "10.99.0.0/24")
        r = await c.post("/api/v1/ip-allowlist", json={"cidr": "10.99.0.0/24"})
    assert r.status_code == 409


# ─── C. 404 cases ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ipl_delete_unknown_rule_returns_404():
    """DELETE /ip-allowlist/{random_uuid} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/ip-allowlist/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ipl_toggle_unknown_rule_returns_404():
    """PATCH /ip-allowlist/{random_uuid}/toggle returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.patch(f"/api/v1/ip-allowlist/{uuid.uuid4()}/toggle")
    assert r.status_code == 404


# ─── D. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ipl_supervisor_cannot_manage_403():
    """Supervisor (role 3) does not have iplist:manage → 403."""
    _, _, token = await _seed_tenant_and_token(role_id=3)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/ip-allowlist")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ipl_rls_isolation():
    """Tenant B cannot see Tenant A's IP allowlist rules."""
    _, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_a) as c:
        await _add_rule(c, "10.50.0.0/16")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/ip-allowlist")
    assert r.json() == []
