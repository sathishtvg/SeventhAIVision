"""Gap 70 — Command Centre Router

Isolated-tenant tests for backend/app/routers/command_centre.py (163 lines).
Single endpoint: GET /api/v1/command-centre/overview  (permission: alert:read, all roles).

Response shape:
  {
    summary: {total_sites, cameras_online, cameras_offline, cameras_degraded,
              active_alerts, critical_alerts, high_alerts, guards_on_duty},
    sites:   [{id, name, address, cameras_online, cameras_offline, cameras_degraded,
               cameras_total, active_alerts, critical_alerts, high_alerts, medium_alerts, guards}],
    recent_alerts: [/* last 20 critical/high in 4 hours */],
    guards: [],
  }

Sections:
  A — Response structure (5 tests)
  B — Site card completeness (1 test)
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
    slug = f"cc-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"CC Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'CC Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"cc-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_site(tenant_id: uuid.UUID, name: str = "HQ") -> uuid.UUID:
    site_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO sites (id, tenant_id, name, address) VALUES (:id, :tid, :n, :a)"),
            {"id": site_id, "tid": tenant_id, "n": name, "a": "123 Test Street"},
        )
        await s.commit()
    await engine.dispose()
    return site_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


_SUMMARY_KEYS = {
    "total_sites", "cameras_online", "cameras_offline", "cameras_degraded",
    "active_alerts", "critical_alerts", "high_alerts", "guards_on_duty",
}

_SITE_CARD_KEYS = {
    "id", "name", "address",
    "cameras_online", "cameras_offline", "cameras_degraded", "cameras_total",
    "active_alerts", "critical_alerts", "high_alerts", "medium_alerts", "guards",
}


# ─── A. Response structure ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cc_overview_returns_200():
    """GET /command-centre/overview returns 200 on a fresh tenant."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/command-centre/overview")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_cc_overview_has_4_top_level_keys():
    """Response has exactly summary, sites, recent_alerts, guards top-level keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/command-centre/overview")
    keys = set(r.json().keys())
    assert {"summary", "sites", "recent_alerts", "guards"} <= keys


@pytest.mark.asyncio
async def test_cc_summary_has_all_8_keys():
    """summary dict contains all 8 required keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/command-centre/overview")
    assert _SUMMARY_KEYS <= set(r.json()["summary"].keys())


@pytest.mark.asyncio
async def test_cc_fresh_tenant_all_zeros():
    """On a fresh tenant all numeric summary fields are 0 and lists are empty."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/command-centre/overview")
    body = r.json()
    summary = body["summary"]
    assert summary["total_sites"] == 0
    assert summary["active_alerts"] == 0
    assert summary["guards_on_duty"] == 0
    assert body["sites"] == []
    assert body["recent_alerts"] == []
    assert body["guards"] == []


@pytest.mark.asyncio
async def test_cc_total_sites_increments_after_site_seed():
    """After seeding one site, summary.total_sites=1 and sites list has one entry."""
    tenant_id, _, token = await _seed_tenant_and_token()
    await _seed_site(tenant_id, "North Gate")
    async with await _authed(token) as c:
        r = await c.get("/api/v1/command-centre/overview")
    body = r.json()
    assert body["summary"]["total_sites"] == 1
    assert len(body["sites"]) == 1


# ─── B. Site card completeness ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cc_site_card_has_all_required_fields():
    """Site card in response has all 12 required fields."""
    tenant_id, _, token = await _seed_tenant_and_token()
    await _seed_site(tenant_id, "South Gate")
    async with await _authed(token) as c:
        r = await c.get("/api/v1/command-centre/overview")
    card = r.json()["sites"][0]
    assert _SITE_CARD_KEYS <= set(card.keys())
    assert card["name"] == "South Gate"
    assert card["cameras_total"] == 0


# ─── C. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cc_viewer_can_access_overview():
    """Viewer (role 6) has alert:read → GET /command-centre/overview returns 200."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/command-centre/overview")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_cc_rls_isolation():
    """Tenant B sees 0 sites even after Tenant A seeds a site."""
    tenant_a, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    await _seed_site(tenant_a, "Tenant A HQ")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/command-centre/overview")
    assert r.json()["summary"]["total_sites"] == 0
    assert r.json()["sites"] == []
