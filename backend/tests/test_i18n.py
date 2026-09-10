"""Gap 71 — i18n Router

Isolated-tenant tests for backend/app/routers/i18n.py (141 lines).

Endpoints:
  GET  /api/v1/i18n/locales                     — public; no auth
  GET  /api/v1/i18n/{locale}/alerts             — public; falls back to FALLBACK_LOCALE
  GET  /api/v1/i18n/{locale}/alerts/{code}      — auth; 404 on unknown code
  PUT  /api/v1/i18n/me/locale                   — auth; 422 on unsupported locale
  GET  /api/v1/i18n/tenant/locale               — any auth
  PUT  /api/v1/i18n/tenant/locale               — requires i18n:manage (admin only)

Sections:
  A — Public endpoints (3 tests)
  B — Alert code translation (4 tests)
  C — User locale preference (2 tests)
  D — Tenant locale (3 tests)
"""
from __future__ import annotations

import json
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
    slug = f"i18n-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"I18n Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'I18n Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"i18n-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _anon() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(_app()), base_url="http://test")


# ─── A. Public endpoints ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_i18n_list_locales_no_auth():
    """GET /i18n/locales returns {locales, fallback} without any auth token."""
    async with _anon() as c:
        r = await c.get("/api/v1/i18n/locales")
    assert r.status_code == 200
    body = r.json()
    assert "locales" in body
    assert "fallback" in body
    assert isinstance(body["locales"], list)
    assert len(body["locales"]) >= 1
    assert body["fallback"] in body["locales"]


@pytest.mark.asyncio
async def test_i18n_get_templates_known_locale():
    """GET /i18n/en/alerts returns {locale, templates} with non-empty dict."""
    async with _anon() as c:
        r = await c.get("/api/v1/i18n/en/alerts")
    assert r.status_code == 200
    body = r.json()
    assert body["locale"] == "en"
    assert isinstance(body["templates"], dict)
    assert len(body["templates"]) > 0
    assert "lpr.blocklist_hit" in body["templates"]


@pytest.mark.asyncio
async def test_i18n_get_templates_unknown_locale_fallback():
    """GET /i18n/xx/alerts with unknown locale falls back to en — never 404."""
    async with _anon() as c:
        r = await c.get("/api/v1/i18n/xx/alerts")
    assert r.status_code == 200
    body = r.json()
    assert body["locale"] == "en"
    assert isinstance(body["templates"], dict)
    assert len(body["templates"]) > 0


# ─── B. Alert code translation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_i18n_translate_known_code_returns_message():
    """GET /i18n/en/alerts/face.unrecognized returns {locale, alert_code, message}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/i18n/en/alerts/face.unrecognized")
    assert r.status_code == 200
    body = r.json()
    assert body["locale"] == "en"
    assert body["alert_code"] == "face.unrecognized"
    assert isinstance(body["message"], str)
    assert len(body["message"]) > 0


@pytest.mark.asyncio
async def test_i18n_translate_unknown_locale_falls_back():
    """GET /i18n/xx/alerts/camera.offline with unknown locale falls back to en message."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/i18n/xx/alerts/camera.offline")
    assert r.status_code == 200
    body = r.json()
    assert body["locale"] == "en"
    assert body["alert_code"] == "camera.offline"


@pytest.mark.asyncio
async def test_i18n_translate_unknown_code_returns_404():
    """GET /i18n/en/alerts/nonexistent.code returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/i18n/en/alerts/nonexistent.code.xyz")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_i18n_translate_with_params_renders_template():
    """GET with ?params=JSON renders template placeholders in the message."""
    _, _, token = await _seed_tenant_and_token()
    params_json = json.dumps({"zone_name": "Server Room"})
    async with await _authed(token) as c:
        r = await c.get(
            "/api/v1/i18n/en/alerts/intrusion.zone_breach",
            params={"params": params_json},
        )
    assert r.status_code == 200
    body = r.json()
    assert "Server Room" in body["message"]


# ─── C. User locale preference ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_i18n_set_my_locale_returns_user_and_locale():
    """PUT /i18n/me/locale with a supported locale returns {user_id, locale}."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/i18n/locales")
        locales = r.json()["locales"]
        target = locales[0]
        r2 = await c.put("/api/v1/i18n/me/locale", json={"locale": target})
    assert r2.status_code == 200
    body = r2.json()
    assert body["locale"] == target
    assert "user_id" in body


@pytest.mark.asyncio
async def test_i18n_set_my_locale_unsupported_returns_422():
    """PUT /i18n/me/locale with an unsupported locale returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put("/api/v1/i18n/me/locale", json={"locale": "klingon"})
    assert r.status_code == 422


# ─── D. Tenant locale ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_i18n_get_tenant_locale_returns_locale_key():
    """GET /i18n/tenant/locale returns {locale} for any authenticated user."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/i18n/tenant/locale")
    assert r.status_code == 200
    assert "locale" in r.json()


@pytest.mark.asyncio
async def test_i18n_set_tenant_locale_admin_succeeds():
    """PUT /i18n/tenant/locale by admin (role 2) with a valid locale returns {tenant_id, locale}."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        locales = (await c.get("/api/v1/i18n/locales")).json()["locales"]
        target = locales[0]
        r = await c.put("/api/v1/i18n/tenant/locale", json={"locale": target})
    assert r.status_code == 200
    body = r.json()
    assert body["locale"] == target
    assert "tenant_id" in body


@pytest.mark.asyncio
async def test_i18n_set_tenant_locale_viewer_returns_403():
    """PUT /i18n/tenant/locale by viewer (role 6) returns 403 (no i18n:manage)."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.put("/api/v1/i18n/tenant/locale", json={"locale": "en"})
    assert r.status_code == 403
