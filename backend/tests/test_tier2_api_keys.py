"""Tier 2 Feature 1: API key management tests.

Tests cover:
- Creating an API key (returns full key once)
- Listing keys (prefix shown, hash never exposed)
- Authenticating with X-Api-Key header (no JWT needed)
- Revoking a key (subsequent requests fail 401)
- Expired key is rejected
- Wrong/garbage key is rejected
- Non-admin cannot manage keys
"""

import hashlib
import secrets
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


# ── Helpers ───────────────────────────────────────────────────────────────────

def _jwt(tenant_id: uuid.UUID, user_id: uuid.UUID, role_id: int) -> str:
    return create_access_token(str(user_id), str(tenant_id), role_id=role_id)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def _admin_jwt_and_tenant(admin_session):
    """Seed a tenant+admin user, return (jwt, tenant_id)."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    return _jwt(tenant_id, user_id, 2), tenant_id


@pytest_asyncio.fixture
async def _created_key(admin_session, app_client, _admin_jwt_and_tenant):
    """Create one API key via HTTP, return its details + JWT + tenant_id."""
    jwt, tenant_id = _admin_jwt_and_tenant
    resp = await app_client.post(
        "/api/v1/api-keys",
        json={"name": "CI integration key"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return {"jwt": jwt, "key": data["key"], "key_id": data["id"], "tenant_id": tenant_id}


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_key_returns_full_key_once(admin_session, app_client):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    jwt = _jwt(tenant_id, user_id, 2)

    resp = await app_client.post(
        "/api/v1/api-keys",
        json={"name": "my integration"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"].startswith("sav1_")
    assert len(body["key"]) == 5 + 64      # "sav1_" + 64 hex chars
    assert body["key_shown_once"] is True
    assert body["key_prefix"] == body["key"][5:13]   # first 8 hex chars of random part


@pytest.mark.asyncio
async def test_list_keys_hides_hash(_created_key, app_client):
    jwt = _created_key["jwt"]
    resp = await app_client.get(
        "/api/v1/api-keys",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    keys = resp.json()
    assert len(keys) >= 1
    for k in keys:
        assert "key_hash" not in k
        assert "key" not in k
        assert "key_prefix" in k


@pytest.mark.asyncio
async def test_api_key_authenticates_without_jwt(_created_key, app_client):
    """A valid X-Api-Key should let the caller hit a protected endpoint."""
    full_key = _created_key["key"]
    resp = await app_client.get(
        "/api/v1/alerts",
        headers={"X-Api-Key": full_key},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_wrong_api_key_returns_401(app_client):
    resp = await app_client.get(
        "/api/v1/alerts",
        headers={"X-Api-Key": "sav1_" + "0" * 64},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_garbage_api_key_returns_401(app_client):
    resp = await app_client.get(
        "/api/v1/alerts",
        headers={"X-Api-Key": "not-a-valid-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_revoke_key_blocks_subsequent_requests(_created_key, app_client):
    full_key = _created_key["key"]
    key_id = _created_key["key_id"]
    jwt = _created_key["jwt"]

    # Key works before revocation
    r = await app_client.get("/api/v1/alerts", headers={"X-Api-Key": full_key})
    assert r.status_code == 200

    # Revoke
    del_resp = await app_client.delete(
        f"/api/v1/api-keys/{key_id}",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["revoked"] is True

    # Key no longer works
    r2 = await app_client.get("/api/v1/alerts", headers={"X-Api-Key": full_key})
    assert r2.status_code == 401


@pytest.mark.asyncio
async def test_revoke_nonexistent_key_404(admin_session, app_client):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    jwt = _jwt(tenant_id, user_id, 2)
    resp = await app_client.delete(
        f"/api/v1/api-keys/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_expired_key_returns_401(admin_session, app_client):
    """Insert an expired key directly (bypassing RLS via superuser) and verify rejection."""
    tenant_id, _ = await _seed_user_with_role(admin_session, role_id=2)

    raw = "sav1_" + secrets.token_hex(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()

    # postgres superuser bypasses RLS — no need to set tenant GUC
    await admin_session.execute(
        text("""
            INSERT INTO api_keys (tenant_id, name, key_prefix, key_hash, expires_at)
            VALUES (:tid, 'expired key', :pfx, :hash, now() - INTERVAL '1 hour')
        """),
        {"tid": tenant_id, "pfx": raw[5:13], "hash": key_hash},
    )
    await admin_session.commit()

    resp = await app_client.get("/api/v1/alerts", headers={"X-Api-Key": raw})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_non_admin_cannot_manage_keys(admin_session, app_client):
    """Viewer role (role_id=6) cannot create or list API keys."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)
    jwt = _jwt(tenant_id, user_id, 6)

    r_list = await app_client.get("/api/v1/api-keys", headers={"Authorization": f"Bearer {jwt}"})
    assert r_list.status_code == 403

    r_create = await app_client.post(
        "/api/v1/api-keys",
        json={"name": "should fail"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert r_create.status_code == 403
