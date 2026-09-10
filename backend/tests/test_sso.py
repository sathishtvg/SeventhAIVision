"""Gap 59 — SSO / SAML / LDAP Router

Isolated-tenant tests for backend/app/routers/sso.py (461 lines).
Two separate routers registered in main.py:
  auth_sso_router  — prefix /api/v1/auth/sso  (no authentication required)
  sso_config_router — prefix /api/v1/sso-configs (requires sso:manage)

Endpoints:
  GET  /api/v1/auth/sso/{slug}/metadata   — no auth; SAML SP metadata XML; 404 if no SAML config
  GET  /api/v1/auth/sso/{slug}/initiate   — no auth; redirect to IdP; 404 if no config
  POST /api/v1/auth/sso/{slug}/callback   — no auth; validate SAML assertion; 404 if no config
  GET  /api/v1/sso-configs                — sso:manage; list (cert masked as ***)
  POST /api/v1/sso-configs                — sso:manage; 422 invalid protocol; returns {id,...}
  GET  /api/v1/sso-configs/{id}           — sso:manage; 404 not found; cert+password masked
  PUT  /api/v1/sso-configs/{id}           — sso:manage; 422 no-fields; 404 not found
  DELETE /api/v1/sso-configs/{id}         — sso:manage; 404 not found; returns {deleted: True}
  POST /api/v1/sso-configs/{id}/test-ldap — sso:manage; 404 if config not found or not ldap
  POST /api/v1/sso-configs/{id}/sync-ldap — sso:manage; 404 if config not found or not ldap

Sections:
  A — Config CRUD (9 tests)
  B — SAML unauthenticated 404 paths (3 tests)
  C — LDAP test + sync 404 paths (4 tests)
  D — Permissions + RLS (3 tests)
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

_SAML_BODY = {
    "protocol": "saml",
    "idp_entity_id": "https://idp.example.com/saml",
    "idp_sso_url": "https://idp.example.com/sso",
    "idp_certificate": "MIIC... (dummy cert)",
    "sp_entity_id": "https://sp.example.com",
    "sp_acs_url": "https://sp.example.com/acs",
}

_LDAP_BODY = {
    "protocol": "ldap",
    "ldap_host": "ldap.example.com",
    "ldap_port": 636,
    "ldap_bind_dn": "cn=admin,dc=example,dc=com",
    "ldap_bind_password": "s3cr3t",
    "ldap_base_dn": "dc=example,dc=com",
}


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 2):
    """Create isolated tenant + user; return (tenant_id, user_id, token, slug)."""
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"sso-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"SSO Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'SSO Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"sso-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token, slug


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_saml_config(c: AsyncClient, extra: dict | None = None) -> str:
    body = {**_SAML_BODY, **(extra or {})}
    r = await c.post("/api/v1/sso-configs", json=body)
    assert r.status_code == 200, f"create_saml_config failed: {r.text}"
    return r.json()["id"]


# ─── A. Config CRUD ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_sso_configs_empty_fresh_tenant():
    """GET /sso-configs on a fresh tenant returns 200 + empty list."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/sso-configs")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_saml_config_returns_200():
    """POST /sso-configs with protocol=saml returns 200 + {id, protocol, is_enabled, created_at}."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/sso-configs", json=_SAML_BODY)
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["protocol"] == "saml"
    assert "is_enabled" in body
    assert "created_at" in body


@pytest.mark.asyncio
async def test_create_ldap_config_returns_200():
    """POST /sso-configs with protocol=ldap returns 200."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/sso-configs", json=_LDAP_BODY)
    assert r.status_code == 200
    assert r.json()["protocol"] == "ldap"


@pytest.mark.asyncio
async def test_create_sso_config_appears_in_list():
    """After POST /sso-configs, the config is visible via GET /sso-configs."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        config_id = await _create_saml_config(c)
        r = await c.get("/api/v1/sso-configs")
    ids = [item["id"] for item in r.json()]
    assert config_id in ids


@pytest.mark.asyncio
async def test_create_sso_config_invalid_protocol_returns_422():
    """POST /sso-configs with an invalid protocol returns 422."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/sso-configs", json={"protocol": "oauth1"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_sso_config_masks_sensitive_fields():
    """GET /sso-configs/{id} returns *** for idp_certificate and ldap_bind_password."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        # Create LDAP config with both sensitive fields
        r = await c.post("/api/v1/sso-configs", json={
            **_LDAP_BODY,
            "idp_certificate": "real-cert-data",
        })
        config_id = r.json()["id"]
        r = await c.get(f"/api/v1/sso-configs/{config_id}")
    body = r.json()
    assert r.status_code == 200
    assert body["ldap_bind_password"] == "***"
    # idp_certificate was set, so it should also be masked
    assert body.get("idp_certificate") in ("***", None)


@pytest.mark.asyncio
async def test_get_sso_config_unknown_id_returns_404():
    """GET /sso-configs/{random_uuid} returns 404."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/sso-configs/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_sso_config_is_enabled():
    """PUT /sso-configs/{id} enables the config."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        config_id = await _create_saml_config(c)
        r = await c.put(f"/api/v1/sso-configs/{config_id}", json={"is_enabled": True})
    assert r.status_code == 200
    body = r.json()
    assert body["is_enabled"] is True
    assert body["id"] == config_id


@pytest.mark.asyncio
async def test_update_sso_config_no_fields_returns_422():
    """PUT /sso-configs/{id} with empty body returns 422."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        config_id = await _create_saml_config(c)
        r = await c.put(f"/api/v1/sso-configs/{config_id}", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_sso_config_returns_deleted_true():
    """DELETE /sso-configs/{id} returns {deleted: True} and removes the config."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        config_id = await _create_saml_config(c)
        r = await c.delete(f"/api/v1/sso-configs/{config_id}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        # Confirm it's gone
        r2 = await c.get(f"/api/v1/sso-configs/{config_id}")
    assert r2.status_code == 404


# ─── B. SAML unauthenticated endpoints (404 paths) ───────────────────────────

@pytest.mark.asyncio
async def test_saml_metadata_unknown_slug_returns_404():
    """GET /auth/sso/{unknown}/metadata returns 404 when no SAML config exists."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get(f"/api/v1/auth/sso/no-such-tenant-{uuid.uuid4().hex[:8]}/metadata")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_saml_initiate_unknown_slug_returns_404():
    """GET /auth/sso/{unknown}/initiate returns 404 when no SAML config exists."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get(f"/api/v1/auth/sso/no-such-tenant-{uuid.uuid4().hex[:8]}/initiate")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_saml_callback_unknown_slug_returns_404():
    """POST /auth/sso/{unknown}/callback returns 404 when no SAML config exists."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.post(
            f"/api/v1/auth/sso/no-such-tenant-{uuid.uuid4().hex[:8]}/callback",
            data={"SAMLResponse": "dummy"},
        )
    assert r.status_code == 404


# ─── C. LDAP test + sync 404 paths ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_test_ldap_unknown_id_returns_404():
    """POST /sso-configs/{random_uuid}/test-ldap returns 404."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/sso-configs/{uuid.uuid4()}/test-ldap")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_test_ldap_with_saml_config_returns_404():
    """POST /sso-configs/{saml_id}/test-ldap returns 404 (config is not protocol=ldap)."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        saml_id = await _create_saml_config(c)
        r = await c.post(f"/api/v1/sso-configs/{saml_id}/test-ldap")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_sync_ldap_unknown_id_returns_404():
    """POST /sso-configs/{random_uuid}/sync-ldap returns 404."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/sso-configs/{uuid.uuid4()}/sync-ldap")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_sync_ldap_with_saml_config_returns_404():
    """POST /sso-configs/{saml_id}/sync-ldap returns 404 (not an ldap config)."""
    _, _, token, _ = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        saml_id = await _create_saml_config(c)
        r = await c.post(f"/api/v1/sso-configs/{saml_id}/sync-ldap")
    assert r.status_code == 404


# ─── D. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_list_sso_configs_403():
    """Viewer (role 6) cannot list SSO configs — sso:manage not granted."""
    _, _, token, _ = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/sso-configs")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_create_sso_config_403():
    """Viewer (role 6) cannot create SSO configs — sso:manage not granted."""
    _, _, token, _ = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/sso-configs", json=_SAML_BODY)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_sso_configs_rls_isolation():
    """Tenant B cannot see Tenant A's SSO configs."""
    _, _, tok_a, _ = await _seed_tenant_and_token()
    _, _, tok_b, _ = await _seed_tenant_and_token()
    async with await _authed(tok_a) as c:
        config_id = await _create_saml_config(c)
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/sso-configs")
    ids = [item["id"] for item in r.json()]
    assert config_id not in ids
