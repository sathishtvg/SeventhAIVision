"""Gap 55 — Two-Factor Authentication Router

Dedicated isolated-tenant test file for backend/app/routers/two_fa.py
(170 lines, existing tests only cover policy endpoint via test_p5_2fa_policy.py).

Endpoints (prefix /api/v1/2fa):
  GET  /policy         — 2fa:policy; returns {required, grace_hours}
  PUT  /policy         — 2fa:policy; upserts tenant_settings rows
  GET  /status         — 2fa:manage; returns {enabled: bool} for caller
  GET  /setup          — 2fa:manage; generates TOTP secret + QR; 409 if already enabled
  POST /enable         — 2fa:manage; verifies TOTP + enables; 400/409 on error states
  DELETE /             — 2fa:manage; verifies TOTP + disables; 400 if not enabled / bad code

Sections:
  A — Status: new user disabled, unauthenticated 401, after-enable reflects True
  B — Setup: required fields, QR is PNG data URI, secret valid for pyotp, 409 if already enabled
  C — Enable: valid code succeeds, no-setup 400, wrong code 400, already-enabled 409
  D — Disable: valid code succeeds, not-enabled 400, wrong code 400, status reflects after disable
  E — Policy (additional coverage): default values, set+get round-trip, unauth 401
"""
from __future__ import annotations

import json
import os
import re
import uuid

import pyotp
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
    slug = f"tfa-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"2FA Test {slug}", "slug": slug},
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
                "email": f"tfa-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _wrong_totp_code() -> str:
    """Generate a valid-format TOTP code from a *different* random secret (will never match)."""
    return pyotp.TOTP(pyotp.random_base32()).now()


# ─── A. Status ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_new_user_is_disabled():
    """GET /2fa/status on a fresh user returns {enabled: False}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/2fa/status")
    assert r.status_code == 200
    assert r.json()["enabled"] is False


@pytest.mark.asyncio
async def test_status_unauthenticated_returns_401():
    """GET /2fa/status without JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/2fa/status")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_status_after_enable_returns_true():
    """GET /2fa/status after a successful enable returns {enabled: True}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        setup_r = await c.get("/api/v1/2fa/setup")
        secret = setup_r.json()["secret"]
        code = pyotp.TOTP(secret).now()
        await c.post("/api/v1/2fa/enable", json={"totp_code": code})
        r = await c.get("/api/v1/2fa/status")
    assert r.json()["enabled"] is True


# ─── B. Setup ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_setup_returns_required_fields():
    """GET /2fa/setup returns secret, qr_code_uri, manual_entry_key, instructions."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/2fa/setup")
    assert r.status_code == 200
    body = r.json()
    assert {"secret", "qr_code_uri", "manual_entry_key", "instructions"}.issubset(body.keys())
    assert body["secret"] == body["manual_entry_key"]


@pytest.mark.asyncio
async def test_setup_qr_code_uri_is_png_data_uri():
    """GET /2fa/setup qr_code_uri is a base64 PNG data URI."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/2fa/setup")
    qr = r.json()["qr_code_uri"]
    assert qr.startswith("data:image/png;base64,"), f"Expected PNG data URI, got: {qr[:60]}"


@pytest.mark.asyncio
async def test_setup_secret_is_valid_base32_for_pyotp():
    """The secret returned by /2fa/setup is a valid TOTP key (pyotp.TOTP(secret).now() produces 6 digits)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/2fa/setup")
    secret = r.json()["secret"]
    code = pyotp.TOTP(secret).now()
    assert len(code) == 6
    assert code.isdigit()


@pytest.mark.asyncio
async def test_setup_when_already_enabled_returns_409():
    """GET /2fa/setup when 2FA is already enabled returns 409 Conflict."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        setup_r = await c.get("/api/v1/2fa/setup")
        secret = setup_r.json()["secret"]
        code = pyotp.TOTP(secret).now()
        await c.post("/api/v1/2fa/enable", json={"totp_code": code})
        r = await c.get("/api/v1/2fa/setup")
    assert r.status_code == 409


# ─── C. Enable ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enable_with_valid_totp_code_returns_200():
    """POST /2fa/enable with a valid TOTP code returns 200 {enabled: True}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        setup_r = await c.get("/api/v1/2fa/setup")
        secret = setup_r.json()["secret"]
        code = pyotp.TOTP(secret).now()
        r = await c.post("/api/v1/2fa/enable", json={"totp_code": code})
    assert r.status_code == 200
    assert r.json()["enabled"] is True


@pytest.mark.asyncio
async def test_enable_without_prior_setup_returns_400():
    """POST /2fa/enable without calling /2fa/setup first returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/2fa/enable", json={"totp_code": "123456"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_enable_with_wrong_code_returns_400():
    """POST /2fa/enable with an invalid TOTP code returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await c.get("/api/v1/2fa/setup")
        r = await c.post("/api/v1/2fa/enable", json={"totp_code": _wrong_totp_code()})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_enable_when_already_enabled_returns_409():
    """POST /2fa/enable when 2FA is already active returns 409 Conflict."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        setup_r = await c.get("/api/v1/2fa/setup")
        secret = setup_r.json()["secret"]
        code = pyotp.TOTP(secret).now()
        await c.post("/api/v1/2fa/enable", json={"totp_code": code})
        code2 = pyotp.TOTP(secret).now()
        r = await c.post("/api/v1/2fa/enable", json={"totp_code": code2})
    assert r.status_code == 409


# ─── D. Disable ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disable_with_valid_code_returns_enabled_false():
    """DELETE /2fa with a valid TOTP code returns 200 {enabled: False}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        setup_r = await c.get("/api/v1/2fa/setup")
        secret = setup_r.json()["secret"]
        code = pyotp.TOTP(secret).now()
        await c.post("/api/v1/2fa/enable", json={"totp_code": code})
        code2 = pyotp.TOTP(secret).now()
        r = await c.request("DELETE", "/api/v1/2fa", content=json.dumps({"totp_code": code2}),
                            headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


@pytest.mark.asyncio
async def test_disable_when_not_enabled_returns_400():
    """DELETE /2fa when 2FA is not active returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.request("DELETE", "/api/v1/2fa", content=json.dumps({"totp_code": "123456"}),
                            headers={"Content-Type": "application/json"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_disable_with_wrong_code_returns_400():
    """DELETE /2fa with an invalid TOTP code returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        setup_r = await c.get("/api/v1/2fa/setup")
        secret = setup_r.json()["secret"]
        code = pyotp.TOTP(secret).now()
        await c.post("/api/v1/2fa/enable", json={"totp_code": code})
        r = await c.request("DELETE", "/api/v1/2fa", content=json.dumps({"totp_code": _wrong_totp_code()}),
                            headers={"Content-Type": "application/json"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_status_after_disable_returns_false():
    """GET /2fa/status after disable returns {enabled: False}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        setup_r = await c.get("/api/v1/2fa/setup")
        secret = setup_r.json()["secret"]
        code = pyotp.TOTP(secret).now()
        await c.post("/api/v1/2fa/enable", json={"totp_code": code})
        code2 = pyotp.TOTP(secret).now()
        await c.request("DELETE", "/api/v1/2fa", content=json.dumps({"totp_code": code2}),
                        headers={"Content-Type": "application/json"})
        r = await c.get("/api/v1/2fa/status")
    assert r.json()["enabled"] is False


# ─── E. Policy (additional coverage, complements test_p5_2fa_policy.py) ─────

@pytest.mark.asyncio
async def test_get_policy_fresh_tenant_returns_defaults():
    """GET /2fa/policy on a fresh tenant returns {required: False, grace_hours: 0}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/2fa/policy")
    assert r.status_code == 200
    body = r.json()
    assert body["required"] is False
    assert body["grace_hours"] == 0


@pytest.mark.asyncio
async def test_set_policy_updates_values():
    """PUT /2fa/policy returns the new values."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put("/api/v1/2fa/policy", json={"required": True, "grace_hours": 48})
    assert r.status_code == 200
    body = r.json()
    assert body["required"] is True
    assert body["grace_hours"] == 48


@pytest.mark.asyncio
async def test_get_policy_reflects_set_values():
    """After PUT /2fa/policy, GET returns the updated values."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await c.put("/api/v1/2fa/policy", json={"required": True, "grace_hours": 12})
        r = await c.get("/api/v1/2fa/policy")
    body = r.json()
    assert body["required"] is True
    assert body["grace_hours"] == 12


@pytest.mark.asyncio
async def test_policy_unauthenticated_returns_401():
    """GET/PUT /2fa/policy without JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        get_r = await c.get("/api/v1/2fa/policy")
        put_r = await c.put("/api/v1/2fa/policy", json={"required": True})
    assert get_r.status_code == 401
    assert put_r.status_code == 401
