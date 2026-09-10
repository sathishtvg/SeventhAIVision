"""Gap 79 — Branding Router (isolated-tenant)

Isolated-tenant tests for backend/app/routers/branding.py (58 lines).
Tenant-scoped white-label appearance config.

Endpoints:
  GET /api/v1/branding   — any authenticated user; returns name, branding, timezone
  PUT /api/v1/branding   — settings:write; updates branding JSONB column only

Permission:
  GET: no permission code required — any valid token
  PUT: settings:write → roles 1 (super_admin) and 2 (admin) only

Sections:
  A — GET behavior (4 tests)
  B — PUT behavior (2 tests)
  C — Permissions (2 tests)
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
    slug = f"brd-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Branding Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'Branding Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"brd-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. GET behavior ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_brd_get_returns_required_shape():
    """GET /branding returns 200 with name, branding (dict), and timezone keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/branding")
    assert r.status_code == 200
    body = r.json()
    for key in ("name", "branding", "timezone"):
        assert key in body, f"Missing key: {key}"
    assert isinstance(body["branding"], dict)


@pytest.mark.asyncio
async def test_brd_get_branding_empty_by_default():
    """Fresh tenant has no branding configured — branding field is {}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/branding")
    assert r.status_code == 200
    assert r.json()["branding"] == {}


@pytest.mark.asyncio
async def test_brd_get_name_matches_seeded_tenant():
    """GET /branding returns the tenant's name (not empty string)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/branding")
    assert r.status_code == 200
    assert r.json()["name"] != ""


@pytest.mark.asyncio
async def test_brd_get_reflects_put_branding():
    """After PUT, GET returns the updated branding dict."""
    _, _, token = await _seed_tenant_and_token()
    branding_data = {"primary_color": "#6C63FF", "logo_url": "https://example.com/logo.png"}
    async with await _authed(token) as c:
        pr = await c.put("/api/v1/branding", json={"branding": branding_data})
        assert pr.status_code == 200
        r = await c.get("/api/v1/branding")
    assert r.status_code == 200
    assert r.json()["branding"] == branding_data


# ─── B. PUT behavior ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_brd_put_returns_branding_dict():
    """PUT /branding returns {branding: ...} with the submitted values."""
    _, _, token = await _seed_tenant_and_token()
    branding_data = {"theme": "dark", "accent_color": "#00D9C0"}
    async with await _authed(token) as c:
        r = await c.put("/api/v1/branding", json={"branding": branding_data})
    assert r.status_code == 200
    body = r.json()
    assert "branding" in body
    assert body["branding"] == branding_data


@pytest.mark.asyncio
async def test_brd_put_replaces_previous_branding():
    """Second PUT fully replaces the branding dict (no merge — only touches branding column)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await c.put("/api/v1/branding", json={"branding": {"old_key": "old_val"}})
        new_data = {"new_key": "new_val"}
        await c.put("/api/v1/branding", json={"branding": new_data})
        r = await c.get("/api/v1/branding")
    body = r.json()["branding"]
    assert body.get("new_key") == "new_val"
    assert "old_key" not in body, "PUT should fully replace branding, not merge"


# ─── C. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_brd_viewer_can_get():
    """Viewer (role 6) has no permission requirement for GET /branding → 200."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/branding")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_brd_supervisor_cannot_put_403():
    """Supervisor (role 3) lacks settings:write → PUT /branding returns 403."""
    _, _, token = await _seed_tenant_and_token(role_id=3)
    async with await _authed(token) as c:
        r = await c.put("/api/v1/branding", json={"branding": {"color": "#fff"}})
    assert r.status_code == 403
