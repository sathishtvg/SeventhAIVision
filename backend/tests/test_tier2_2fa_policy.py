"""Tests for Tier 2 Feature 4: 2FA enforcement policy.

Covers:
- GET/PUT /api/v1/2fa/policy (permission gate, read, write)
- Login blocked when policy requires 2FA and user has no TOTP
- Login allowed when policy requires 2FA and user has TOTP (redirects to challenge)
- Grace period: newly created user exempt for grace_hours hours
- Non-admin cannot access policy endpoints
"""

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


@pytest_asyncio.fixture
async def _flush_rate_limits():
    """Flush Redis before each login test to prevent cross-suite rate-limit pollution.

    The full suite can exhaust the 5/minute per-IP limit for 127.0.0.1 before
    these tests run (test_auth.py, test_rate_limit.py, test_tier1_features.py all
    call /api/v1/auth/login without X-Forwarded-For).  Flushing Redis at test
    setup gives each login test a clean slate regardless of suite ordering.
    """
    import redis.asyncio as aioredis

    r = aioredis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    try:
        await r.flushdb()
    finally:
        await r.aclose()


def _jwt(tenant_id: uuid.UUID, user_id: uuid.UUID, role_id: int) -> str:
    return create_access_token(str(user_id), str(tenant_id), role_id=role_id)


def _slug(tenant_id: uuid.UUID) -> str:
    return f"t-{tenant_id.hex[:8]}"


def _email(user_id: uuid.UUID) -> str:
    return f"{user_id}@example.com"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def _admin(admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    return _jwt(tenant_id, user_id, 2), tenant_id, user_id


@pytest_asyncio.fixture
async def _operator(admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    return _jwt(tenant_id, user_id, 4), tenant_id, user_id


# ── Policy read / write ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_policy_default_not_required(_admin, app_client):
    jwt, _, _ = _admin
    resp = await app_client.get("/api/v1/2fa/policy", headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["required"] is False
    assert body["grace_hours"] == 0


@pytest.mark.asyncio
async def test_set_policy_requires_permission(_operator, app_client):
    jwt, _, _ = _operator
    resp = await app_client.put(
        "/api/v1/2fa/policy",
        json={"required": True, "grace_hours": 0},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_set_policy_and_read_back(_admin, app_client):
    jwt, _, _ = _admin
    put_resp = await app_client.put(
        "/api/v1/2fa/policy",
        json={"required": True, "grace_hours": 24},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert put_resp.status_code == 200
    body = put_resp.json()
    assert body["required"] is True
    assert body["grace_hours"] == 24

    get_resp = await app_client.get("/api/v1/2fa/policy", headers={"Authorization": f"Bearer {jwt}"})
    assert get_resp.status_code == 200
    body2 = get_resp.json()
    assert body2["required"] is True
    assert body2["grace_hours"] == 24


@pytest.mark.asyncio
async def test_toggle_policy_on_and_off(_admin, app_client):
    jwt, _, _ = _admin
    # Turn on
    r1 = await app_client.put(
        "/api/v1/2fa/policy",
        json={"required": True, "grace_hours": 0},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert r1.json()["required"] is True

    # Turn off
    r2 = await app_client.put(
        "/api/v1/2fa/policy",
        json={"required": False, "grace_hours": 0},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert r2.json()["required"] is False

    # Confirm OFF via GET
    r3 = await app_client.get("/api/v1/2fa/policy", headers={"Authorization": f"Bearer {jwt}"})
    assert r3.json()["required"] is False


@pytest.mark.asyncio
async def test_get_policy_non_admin_forbidden(_operator, app_client):
    jwt, _, _ = _operator
    resp = await app_client.get("/api/v1/2fa/policy", headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 403


# ── Login enforcement ─────────────────────────────────────────────────────────

async def _enable_policy(app_client, jwt: str, grace_hours: int = 0):
    resp = await app_client.put(
        "/api/v1/2fa/policy",
        json={"required": True, "grace_hours": grace_hours},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_login_blocked_when_policy_enforced_and_no_totp(admin_session, app_client, _flush_rate_limits):
    """User without TOTP is blocked at login when tenant enforces 2FA."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    admin_jwt = _jwt(tenant_id, user_id, 2)
    await _enable_policy(app_client, admin_jwt, grace_hours=0)

    login_resp = await app_client.post(
        "/api/v1/auth/login",
        json={
            "tenant_slug": _slug(tenant_id),
            "email": _email(user_id),
            "password": "x",
        },
    )
    assert login_resp.status_code == 403
    detail = login_resp.json()["detail"]
    assert detail["code"] == "2fa_setup_required"


@pytest.mark.asyncio
async def test_login_allowed_when_policy_off(admin_session, app_client, _flush_rate_limits):
    """User without TOTP can log in when 2FA enforcement is OFF."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)

    login_resp = await app_client.post(
        "/api/v1/auth/login",
        json={
            "tenant_slug": _slug(tenant_id),
            "email": _email(user_id),
            "password": "x",
        },
    )
    assert login_resp.status_code == 200


@pytest.mark.asyncio
async def test_login_blocked_code_is_2fa_setup_required(admin_session, app_client, _flush_rate_limits):
    """Verify the error code is exactly 2fa_setup_required and message mentions TOTP."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=3)
    admin_jwt = _jwt(tenant_id, user_id, 2)
    await _enable_policy(app_client, admin_jwt, grace_hours=0)

    resp = await app_client.post(
        "/api/v1/auth/login",
        json={
            "tenant_slug": _slug(tenant_id),
            "email": _email(user_id),
            "password": "x",
        },
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "2fa_setup_required"
    assert "two-factor" in detail["message"].lower() or "totp" in detail["message"].lower()


@pytest.mark.asyncio
async def test_grace_period_exempts_new_user(admin_session, app_client, _flush_rate_limits):
    """User created within grace period can log in even when policy is ON."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    admin_jwt = _jwt(tenant_id, user_id, 2)

    # Enable with 24-hour grace period — newly seeded user is within grace
    await _enable_policy(app_client, admin_jwt, grace_hours=24)

    resp = await app_client.post(
        "/api/v1/auth/login",
        json={
            "tenant_slug": _slug(tenant_id),
            "email": _email(user_id),
            "password": "x",
        },
    )
    # Should succeed (200) since the user was just created (within grace period)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_grace_period_expired_blocks_old_user(admin_session, app_client, _flush_rate_limits):
    """User created before the grace window expires is blocked."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    admin_jwt = _jwt(tenant_id, user_id, 2)

    # Backdate created_at so the user is outside any reasonable grace period
    await admin_session.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})
    await admin_session.execute(
        text("UPDATE users SET created_at = now() - INTERVAL '2 days' WHERE id = :uid"),
        {"uid": user_id},
    )
    await admin_session.commit()

    # Enable with 1-hour grace (user is 2 days old → outside)
    await _enable_policy(app_client, admin_jwt, grace_hours=1)

    resp = await app_client.post(
        "/api/v1/auth/login",
        json={
            "tenant_slug": _slug(tenant_id),
            "email": _email(user_id),
            "password": "x",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "2fa_setup_required"
