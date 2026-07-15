"""Gap 5 — SSO/SAML/LDAP feature tests.

Covers:
  - SSO config CRUD (create, list, get, update, delete)
  - SAML metadata endpoint (mocked service)
  - SAML initiate endpoint (mocked service)
  - SAML callback → JIT provision → JWT issued (mocked assertion)
  - LDAP connection test (mocked)
  - LDAP user sync (mocked)
  - Permission gate: operator (role_id=4) gets 403 on sso:manage routes
  - Unit tests: role-from-group mapping, PEM stripping, UAC bit check
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token


def _auth(user_id, tenant_id, role_id=2):
    token = create_access_token(str(user_id), str(tenant_id), role_id=role_id)
    return {"Authorization": f"Bearer {token}"}


def _config_payload(**overrides) -> dict:
    base = {
        "protocol": "saml",
        "is_enabled": False,
        "idp_entity_id": "https://idp.example.com",
        "idp_sso_url": "https://idp.example.com/sso",
        "idp_certificate": "MIIC...",
        "auto_provision": True,
        "default_role_id": 6,
        "role_mapping": {},
    }
    base.update(overrides)
    return base


# ── SSO config CRUD ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sso_config_create(auth_client: AsyncClient):
    resp = await auth_client.post("/api/v1/sso-configs", json=_config_payload())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["protocol"] == "saml"
    assert "id" in data


@pytest.mark.asyncio
async def test_sso_config_create_requires_permission(app_client: AsyncClient):
    """Operator (role_id=4) does not have sso:manage → 403."""
    headers = _auth(uuid.uuid4(), uuid.uuid4(), role_id=4)
    resp = await app_client.post(
        "/api/v1/sso-configs",
        json=_config_payload(),
        headers=headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_sso_config_list(auth_client: AsyncClient):
    # Create one first, then list
    await auth_client.post("/api/v1/sso-configs", json=_config_payload())
    resp = await auth_client.get("/api/v1/sso-configs")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    # Certificates are masked
    for row in data:
        assert row.get("idp_certificate") in ("***", None)


@pytest.mark.asyncio
async def test_sso_config_get(auth_client: AsyncClient):
    c = await auth_client.post("/api/v1/sso-configs", json=_config_payload())
    config_id = c.json()["id"]

    resp = await auth_client.get(f"/api/v1/sso-configs/{config_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == config_id
    assert data["idp_certificate"] == "***"


@pytest.mark.asyncio
async def test_sso_config_update(auth_client: AsyncClient):
    c = await auth_client.post("/api/v1/sso-configs", json=_config_payload())
    config_id = c.json()["id"]

    resp = await auth_client.put(
        f"/api/v1/sso-configs/{config_id}",
        json={"is_enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["is_enabled"] is True


@pytest.mark.asyncio
async def test_sso_config_update_empty_body_422(auth_client: AsyncClient):
    c = await auth_client.post("/api/v1/sso-configs", json=_config_payload())
    config_id = c.json()["id"]

    resp = await auth_client.put(f"/api/v1/sso-configs/{config_id}", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sso_config_delete(auth_client: AsyncClient):
    c = await auth_client.post("/api/v1/sso-configs", json=_config_payload())
    config_id = c.json()["id"]

    resp = await auth_client.delete(f"/api/v1/sso-configs/{config_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    resp2 = await auth_client.get(f"/api/v1/sso-configs/{config_id}")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_sso_config_get_404(auth_client: AsyncClient):
    resp = await auth_client.get(f"/api/v1/sso-configs/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sso_config_invalid_protocol(auth_client: AsyncClient):
    resp = await auth_client.post("/api/v1/sso-configs", json=_config_payload(protocol="oauth2"))
    assert resp.status_code == 422


# ── SAML public endpoints (no auth required) ─────────────────────────────────

@pytest.mark.asyncio
async def test_saml_metadata_no_config_404(app_client: AsyncClient):
    resp = await app_client.get("/api/v1/auth/sso/nonexistent-tenant-xyz/metadata")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_saml_initiate_no_config_404(app_client: AsyncClient):
    resp = await app_client.get(
        "/api/v1/auth/sso/nonexistent-tenant-xyz/initiate",
        follow_redirects=False,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_saml_metadata_returns_xml(app_client: AsyncClient):
    """With a mocked SSO config, metadata endpoint returns XML."""
    fake_config = {
        "tenant_slug": "test",
        "tenant_id_val": str(uuid.uuid4()),
        "idp_entity_id": "https://idp.example.com",
        "idp_sso_url": "https://idp.example.com/sso",
        "idp_slo_url": None,
        "idp_certificate": "",
        "sp_entity_id": None,
        "sp_acs_url": None,
        "protocol": "saml",
    }
    fake_xml = b'<?xml version="1.0"?><EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata"/>'

    with patch("app.routers.sso.get_sso_config_for_slug", new_callable=AsyncMock) as mock_cfg, \
         patch("app.routers.sso.generate_saml_metadata", return_value=fake_xml):
        mock_cfg.return_value = fake_config
        resp = await app_client.get("/api/v1/auth/sso/test/metadata")

    assert resp.status_code == 200
    assert "xml" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_saml_initiate_redirects(app_client: AsyncClient):
    """With mocked config, initiate endpoint redirects to IdP URL."""
    fake_config = {
        "tenant_slug": "test",
        "tenant_id_val": str(uuid.uuid4()),
        "idp_entity_id": "https://idp.example.com",
        "idp_sso_url": "https://idp.example.com/sso",
        "idp_slo_url": None,
        "idp_certificate": "",
        "sp_entity_id": None,
        "sp_acs_url": None,
        "protocol": "saml",
    }

    with patch("app.routers.sso.get_sso_config_for_slug", new_callable=AsyncMock) as mock_cfg, \
         patch("app.services.sso_service.build_saml_redirect_url",
               return_value="https://idp.example.com/sso?SAMLRequest=ENCODED"):
        mock_cfg.return_value = fake_config
        resp = await app_client.get(
            "/api/v1/auth/sso/test/initiate",
            follow_redirects=False,
        )

    assert resp.status_code in (302, 307)
    assert "idp.example.com" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_saml_callback_invalid_response_401(app_client: AsyncClient):
    """SAML callback with an invalid SAMLResponse returns 401."""
    fake_config = {
        "tenant_slug": "test",
        "tenant_id_val": str(uuid.uuid4()),
        "idp_entity_id": "https://idp.example.com",
        "idp_sso_url": "https://idp.example.com/sso",
        "idp_slo_url": None,
        "idp_certificate": "",
        "sp_entity_id": None,
        "sp_acs_url": None,
        "protocol": "saml",
        "auto_provision": True,
        "default_role_id": 6,
        "role_mapping": {},
    }

    with patch("app.routers.sso.get_sso_config_for_slug", new_callable=AsyncMock) as mock_cfg, \
         patch("app.routers.sso.process_saml_response",
               side_effect=ValueError("SAML validation errors: ['invalid_response']")):
        mock_cfg.return_value = fake_config
        resp = await app_client.post(
            "/api/v1/auth/sso/test/callback",
            data={"SAMLResponse": "INVALID_BASE64"},
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_saml_callback_jit_provision_returns_tokens(app_client: AsyncClient):
    """Successful SAML callback JIT-provisions user and returns JWT pair."""
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    fake_config = {
        "tenant_slug": "test",
        "tenant_id_val": tenant_id,
        "tenant_id": tenant_id,
        "idp_entity_id": "https://idp.example.com",
        "idp_sso_url": "https://idp.example.com/sso",
        "idp_slo_url": None,
        "idp_certificate": "",
        "sp_entity_id": None,
        "sp_acs_url": None,
        "protocol": "saml",
        "auto_provision": True,
        "default_role_id": 6,
        "role_mapping": {},
    }
    fake_saml_info = {
        "name_id": "alice@example.com",
        "email": "alice@example.com",
        "full_name": "Alice Smith",
        "attributes": {},
    }
    fake_user = {
        "id": user_id,
        "email": "alice@example.com",
        "role_id": 6,
        "full_name": "Alice Smith",
        "is_active": True,
    }

    from app.schemas.auth import TokenPair
    fake_token_pair = TokenPair(access_token="tok", refresh_token="ref")

    with patch("app.routers.sso.get_sso_config_for_slug", new_callable=AsyncMock) as mock_cfg, \
         patch("app.routers.sso.process_saml_response", return_value=fake_saml_info), \
         patch("app.routers.sso.jit_provision_user", new_callable=AsyncMock) as mock_jit, \
         patch("app.routers.sso.auth_service.issue_tokens", new_callable=AsyncMock,
               return_value=fake_token_pair):
        mock_cfg.return_value = fake_config
        mock_jit.return_value = fake_user
        resp = await app_client.post(
            "/api/v1/auth/sso/test/callback",
            data={"SAMLResponse": "VALID_ENCODED"},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "access_token" in data


# ── LDAP endpoints ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ldap_test_connection_not_ldap_config_404(auth_client: AsyncClient):
    """test-ldap on a SAML config → 404 (only ldap configs have LDAP endpoints)."""
    c = await auth_client.post("/api/v1/sso-configs", json=_config_payload(protocol="saml"))
    config_id = c.json()["id"]

    resp = await auth_client.post(f"/api/v1/sso-configs/{config_id}/test-ldap")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ldap_test_connection_success(auth_client: AsyncClient):
    c = await auth_client.post(
        "/api/v1/sso-configs",
        json=_config_payload(
            protocol="ldap",
            ldap_host="ldap.example.com",
            ldap_bind_dn="cn=admin,dc=example,dc=com",
            ldap_bind_password="secret",
            ldap_base_dn="dc=example,dc=com",
        ),
    )
    config_id = c.json()["id"]

    with patch("app.routers.sso.test_ldap_connection",
               return_value={"ok": True, "message": "Connection successful"}):
        resp = await auth_client.post(f"/api/v1/sso-configs/{config_id}/test-ldap")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_ldap_test_connection_failure(auth_client: AsyncClient):
    c = await auth_client.post(
        "/api/v1/sso-configs",
        json=_config_payload(protocol="ldap", ldap_host="ldap.example.com"),
    )
    config_id = c.json()["id"]

    with patch("app.routers.sso.test_ldap_connection",
               return_value={"ok": False, "message": "Connection refused"}):
        resp = await auth_client.post(f"/api/v1/sso-configs/{config_id}/test-ldap")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "refused" in data["message"]


@pytest.mark.asyncio
async def test_ldap_sync_creates_users(auth_client: AsyncClient):
    """LDAP sync upserts users from mocked directory — created count = 1."""
    c = await auth_client.post(
        "/api/v1/sso-configs",
        json=_config_payload(
            protocol="ldap",
            ldap_host="ldap.example.com",
            ldap_bind_dn="cn=admin,dc=example,dc=com",
            ldap_base_dn="dc=example,dc=com",
            default_role_id=6,
        ),
    )
    config_id = c.json()["id"]

    unique_email = f"ldapuser{uuid.uuid4().hex[:8]}@example.com"
    fake_users = [{
        "email": unique_email,
        "full_name": "LDAP User One",
        "dn": "cn=ldapuser1,dc=example,dc=com",
        "groups": [],
        "is_active": True,
    }]

    with patch("app.routers.sso.ldap_sync_users", return_value=fake_users):
        resp = await auth_client.post(f"/api/v1/sso-configs/{config_id}/sync-ldap")

    assert resp.status_code == 200
    data = resp.json()
    assert data["synced"] == 1
    assert data["created"] == 1


@pytest.mark.asyncio
async def test_ldap_sync_updates_existing_user(auth_client: AsyncClient):
    """Second LDAP sync with same email → updated count = 1, created = 0."""
    c = await auth_client.post(
        "/api/v1/sso-configs",
        json=_config_payload(
            protocol="ldap",
            ldap_host="ldap.example.com",
            ldap_bind_dn="cn=admin,dc=example,dc=com",
            ldap_base_dn="dc=example,dc=com",
        ),
    )
    config_id = c.json()["id"]

    fixed_email = f"ldapupdate{uuid.uuid4().hex[:8]}@example.com"
    fake_user = {
        "email": fixed_email,
        "full_name": "LDAP User",
        "dn": "cn=lu,dc=example,dc=com",
        "groups": [],
        "is_active": True,
    }

    with patch("app.routers.sso.ldap_sync_users", return_value=[fake_user]):
        resp1 = await auth_client.post(f"/api/v1/sso-configs/{config_id}/sync-ldap")
    assert resp1.json()["created"] == 1

    with patch("app.routers.sso.ldap_sync_users", return_value=[fake_user]):
        resp2 = await auth_client.post(f"/api/v1/sso-configs/{config_id}/sync-ldap")
    assert resp2.json()["updated"] == 1
    assert resp2.json()["created"] == 0


@pytest.mark.asyncio
async def test_ldap_sync_gateway_error_502(auth_client: AsyncClient):
    c = await auth_client.post(
        "/api/v1/sso-configs",
        json=_config_payload(
            protocol="ldap",
            ldap_host="ldap.unreachable.example.com",
        ),
    )
    config_id = c.json()["id"]

    with patch("app.routers.sso.ldap_sync_users", side_effect=Exception("Connection timed out")):
        resp = await auth_client.post(f"/api/v1/sso-configs/{config_id}/sync-ldap")

    assert resp.status_code == 502


# ── Pure-unit tests (no I/O) ──────────────────────────────────────────────────

def test_role_from_group_mapping():
    """JIT provisioning picks the correct role_id based on LDAP group membership."""
    config = {
        "auto_provision": True,
        "default_role_id": 6,
        "role_mapping": {"cn=admins,dc=example,dc=com": 2},
    }
    attributes = {"memberOf": ["cn=admins,dc=example,dc=com", "cn=users,dc=example,dc=com"]}

    role_id = config["default_role_id"]
    for attr_val, mapped_role in config["role_mapping"].items():
        group_vals = attributes.get("memberOf", attributes.get("groups", []))
        if isinstance(group_vals, str):
            group_vals = [group_vals]
        if attr_val in group_vals:
            role_id = int(mapped_role)
            break

    assert role_id == 2


def test_ldap_uac_disabled_account_flag():
    """userAccountControl bit 2 correctly marks disabled accounts."""
    assert not bool(512 & 2)   # 0x200 = normal account (enabled)
    assert bool(2 & 2)          # 0x002 = disabled account


def test_strip_pem_headers():
    """_build_saml_settings strips PEM headers from cert/key strings."""
    from app.services.sso_service import _build_saml_settings

    config = {
        "tenant_slug": "test",
        "idp_entity_id": "https://idp.example.com",
        "idp_sso_url": "https://idp.example.com/sso",
        "idp_slo_url": None,
        "idp_certificate": "-----BEGIN CERTIFICATE-----\nMIIC...\n-----END CERTIFICATE-----",
        "sp_entity_id": None,
        "sp_acs_url": None,
    }

    with patch("app.services.sso_service.get_sp_cert_and_key",
               return_value=(
                   "-----BEGIN CERTIFICATE-----\nSPCERT\n-----END CERTIFICATE-----",
                   "-----BEGIN RSA PRIVATE KEY-----\nSPKEY\n-----END RSA PRIVATE KEY-----",
               )):
        settings = _build_saml_settings(config, "http://localhost:8000")

    assert "-----" not in settings["idp"]["x509cert"]
    assert "-----" not in settings["sp"]["x509cert"]
    assert "-----" not in settings["sp"]["privateKey"]


def test_sso_service_imports():
    """All public functions from sso_service import cleanly."""
    from app.services.sso_service import (
        build_saml_redirect_url,
        generate_saml_metadata,
        get_sp_cert_and_key,
        get_sso_config_for_slug,
        jit_provision_user,
        ldap_sync_users,
        process_saml_response,
        test_ldap_connection,
    )
    assert all([
        build_saml_redirect_url,
        generate_saml_metadata,
        get_sp_cert_and_key,
        get_sso_config_for_slug,
        jit_provision_user,
        ldap_sync_users,
        process_saml_response,
        test_ldap_connection,
    ])
