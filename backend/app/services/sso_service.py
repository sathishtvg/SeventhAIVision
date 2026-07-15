"""SSO service — SAML 2.0 SP helpers and LDAP/AD integration.

SAML flow:
  1. Admin stores IdP metadata in tenant_sso_configs (entity_id, sso_url, certificate).
  2. GET /auth/sso/{slug}/initiate  → builds AuthnRequest, redirects to IdP.
  3. POST /auth/sso/{slug}/callback → validates assertion, JIT-provisions user, issues JWT.

LDAP flow:
  1. Admin stores LDAP connection details in tenant_sso_configs.
  2. POST /sso-configs/{id}/test-ldap  → verifies bind credentials.
  3. POST /sso-configs/{id}/sync-ldap  → fetches users and upserts them into `users` table.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── SP certificate / key ─────────────────────────────────────────────────────
# In production these come from env vars or a secrets manager.
# For dev/test a self-signed cert is generated once on startup (see get_sp_cert).
_SP_CERT: str | None = None
_SP_KEY: str | None = None


def get_sp_cert_and_key() -> tuple[str, str]:
    """Return (cert_pem, key_pem) for the SAML SP, generating a self-signed
    pair on first call if SP_SAML_CERT / SP_SAML_KEY env vars are not set."""
    global _SP_CERT, _SP_KEY
    if _SP_CERT and _SP_KEY:
        return _SP_CERT, _SP_KEY

    import os
    cert_env = os.environ.get("SP_SAML_CERT", "")
    key_env  = os.environ.get("SP_SAML_KEY", "")
    if cert_env and key_env:
        _SP_CERT, _SP_KEY = cert_env, key_env
        return _SP_CERT, _SP_KEY

    # Generate a temporary self-signed cert (dev/test only)
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import datetime as dt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "7th-ai-vision-saml-sp"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.now(dt.timezone.utc))
        .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    _SP_KEY  = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    _SP_CERT = cert.public_bytes(serialization.Encoding.PEM).decode()
    return _SP_CERT, _SP_KEY


# ── SAML helpers ─────────────────────────────────────────────────────────────

def _build_saml_settings(config: dict, base_url: str) -> dict:
    """Build the python3-saml settings dict from a tenant_sso_configs row."""
    sp_cert, sp_key = get_sp_cert_and_key()

    tenant_slug = config.get("tenant_slug", "tenant")
    sp_entity  = config.get("sp_entity_id") or f"{base_url}/api/v1/auth/sso/{tenant_slug}/metadata"
    sp_acs_url = config.get("sp_acs_url")   or f"{base_url}/api/v1/auth/sso/{tenant_slug}/callback"

    # Strip PEM headers for python3-saml (it wants raw base64)
    def _strip(pem: str) -> str:
        lines = [l for l in pem.strip().splitlines()
                 if not l.startswith("-----")]
        return "".join(lines)

    return {
        "strict": True,
        "debug":  False,
        "sp": {
            "entityId":                 sp_entity,
            "assertionConsumerService": {"url": sp_acs_url, "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"},
            "singleLogoutService":      {"url": f"{base_url}/api/v1/auth/sso/{tenant_slug}/slo", "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"},
            "x509cert":   _strip(sp_cert),
            "privateKey": _strip(sp_key),
            "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        },
        "idp": {
            "entityId":           config.get("idp_entity_id", ""),
            "singleSignOnService": {"url": config.get("idp_sso_url", ""), "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"},
            "singleLogoutService": {"url": config.get("idp_slo_url") or config.get("idp_sso_url", ""), "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"},
            "x509cert": _strip(config.get("idp_certificate", "") or ""),
        },
        "security": {
            "nameIdEncrypted":       False,
            "authnRequestsSigned":   False,
            "logoutRequestSigned":   False,
            "logoutResponseSigned":  False,
            "signMetadata":          False,
            "wantMessagesSigned":    False,
            "wantAssertionsSigned":  True,
            "wantNameId":            True,
            "wantAttributeStatement": False,
            "requestedAuthnContext": False,
        },
    }


async def get_sso_config_for_slug(
    db: AsyncSession,
    tenant_slug: str,
    protocol: str = "saml",
) -> dict | None:
    """Return the SSO config + tenant info for the given slug, bypassing RLS
    (the SSO endpoints are called before a user is authenticated)."""
    result = await db.execute(
        text("""
            SELECT sc.*, t.slug AS tenant_slug, t.id AS tenant_id_val
            FROM tenant_sso_configs sc
            JOIN tenants t ON t.id = sc.tenant_id
            WHERE t.slug = :slug
              AND sc.protocol = :proto
              AND sc.is_enabled = TRUE
              AND t.is_active = TRUE
        """),
        {"slug": tenant_slug, "proto": protocol},
    )
    row = result.mappings().first()
    return dict(row) if row else None


def build_saml_redirect_url(config: dict, base_url: str) -> str:
    """Build the IdP redirect URL for a SAML AuthnRequest."""
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    settings = _build_saml_settings(config, base_url)
    # Build a minimal request dict (no real HTTP request needed for AuthnRequest)
    req = {
        "https": "on" if base_url.startswith("https") else "off",
        "http_host":     base_url.split("://", 1)[-1].split("/")[0],
        "script_name":   "/",
        "get_data":      {},
        "post_data":     {},
        "server_port":   443 if base_url.startswith("https") else 8000,
    }
    auth = OneLogin_Saml2_Auth(req, settings)
    return auth.login()


def generate_saml_metadata(config: dict, base_url: str) -> str:
    """Return SAML SP metadata XML."""
    from onelogin.saml2.metadata import OneLogin_Saml2_Metadata
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    settings_data = _build_saml_settings(config, base_url)
    saml_settings = OneLogin_Saml2_Settings(settings=settings_data, sp_validation_only=True)
    metadata = saml_settings.get_sp_metadata()
    return metadata


def process_saml_response(
    config: dict,
    base_url: str,
    post_data: dict,
    http_host: str,
) -> dict:
    """Validate a SAML Response and return extracted attributes.

    Returns dict with: name_id, email, full_name, attributes.
    Raises ValueError if validation fails.
    """
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    settings = _build_saml_settings(config, base_url)
    req = {
        "https":       "on" if base_url.startswith("https") else "off",
        "http_host":   http_host,
        "script_name": "/",
        "get_data":    {},
        "post_data":   post_data,
        "server_port": 443 if base_url.startswith("https") else 8000,
    }
    auth = OneLogin_Saml2_Auth(req, settings)
    auth.process_response()

    errors = auth.get_errors()
    if errors:
        raise ValueError(f"SAML validation errors: {errors} — {auth.get_last_error_reason()}")
    if not auth.is_authenticated():
        raise ValueError("SAML authentication failed: not authenticated")

    name_id    = auth.get_nameid()
    attrs      = auth.get_attributes()
    email      = name_id  # NameID is configured as email format
    full_name  = ""
    for key in ("displayName", "cn", "http://schemas.microsoft.com/identity/claims/displayname"):
        vals = attrs.get(key, [])
        if vals:
            full_name = vals[0]
            break

    return {
        "name_id":   name_id,
        "email":     email,
        "full_name": full_name,
        "attributes": {k: v[0] if len(v) == 1 else v for k, v in attrs.items()},
    }


# ── JIT user provisioning ─────────────────────────────────────────────────────

async def jit_provision_user(
    db: AsyncSession,
    tenant_id: str,
    sso_config: dict,
    email: str,
    full_name: str,
    sso_provider: str,
    sso_subject_id: str,
    attributes: dict | None = None,
) -> dict:
    """Find or create a user record for an SSO login.

    1. Look up by (tenant_id, sso_provider, sso_subject_id) — fastest, stable.
    2. Fall back to email match within tenant — links existing local users.
    3. Create new user if auto_provision=True, else raise.

    Returns the user row dict (id, email, role_id, full_name, is_active).
    """
    # 1. Look up by SSO subject (bypassing RLS — we already know the tenant)
    result = await db.execute(
        text("""
            SELECT id, email, role_id, full_name, is_active, sso_provider, sso_subject_id
            FROM users
            WHERE tenant_id = CAST(:tid AS uuid)
              AND sso_provider = :provider
              AND sso_subject_id = :subject
        """),
        {"tid": tenant_id, "provider": sso_provider, "subject": sso_subject_id},
    )
    row = result.mappings().first()
    if row:
        if not row["is_active"]:
            raise ValueError("User account is deactivated")
        return dict(row)

    # 2. Fall back to email match — link existing local account
    result = await db.execute(
        text("""
            SELECT id, email, role_id, full_name, is_active, sso_provider, sso_subject_id
            FROM users
            WHERE tenant_id = CAST(:tid AS uuid) AND email = :email
        """),
        {"tid": tenant_id, "email": email.lower()},
    )
    row = result.mappings().first()
    if row:
        if not row["is_active"]:
            raise ValueError("User account is deactivated")
        # Link this existing account to the SSO subject
        await db.execute(
            text("""
                UPDATE users
                SET sso_provider = :provider, sso_subject_id = :subject,
                    sso_provisioned = FALSE, updated_at = now()
                WHERE id = CAST(:uid AS uuid)
            """),
            {"provider": sso_provider, "subject": sso_subject_id, "uid": str(row["id"])},
        )
        return dict(row)

    # 3. Auto-provision new user
    if not sso_config.get("auto_provision", True):
        raise ValueError(f"User {email!r} not found and auto-provision is disabled")

    default_role_id = sso_config.get("default_role_id") or 6  # viewer
    role_mapping    = sso_config.get("role_mapping") or {}

    # Try role mapping from attributes
    if role_mapping and attributes:
        for attr_val, role_id in role_mapping.items():
            group_vals = attributes.get("memberOf", attributes.get("groups", []))
            if isinstance(group_vals, str):
                group_vals = [group_vals]
            if attr_val in group_vals:
                default_role_id = int(role_id)
                break

    new_id = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO users
                (id, tenant_id, role_id, email, hashed_password, full_name,
                 sso_provider, sso_subject_id, sso_provisioned, is_active)
            VALUES
                (CAST(:uid AS uuid), CAST(:tid AS uuid), :role_id,
                 :email, NULL, :full_name,
                 :provider, :subject, TRUE, TRUE)
        """),
        {
            "uid":      new_id,
            "tid":      tenant_id,
            "role_id":  default_role_id,
            "email":    email.lower(),
            "full_name": full_name or email,
            "provider": sso_provider,
            "subject":  sso_subject_id,
        },
    )
    logger.info("jit_provision: created user email=%s tenant=%s provider=%s", email, tenant_id, sso_provider)
    return {
        "id":       new_id,
        "email":    email.lower(),
        "role_id":  default_role_id,
        "full_name": full_name or email,
        "is_active": True,
    }


# ── LDAP helpers ──────────────────────────────────────────────────────────────

def _build_ldap_server(config: dict):
    """Return an ldap3 Server object from config."""
    import ldap3
    return ldap3.Server(
        config["ldap_host"],
        port=int(config.get("ldap_port") or 636),
        use_ssl=bool(config.get("ldap_use_ssl", True)),
        get_info=ldap3.ALL,
    )


def test_ldap_connection(config: dict) -> dict:
    """Synchronous LDAP connection test — returns {ok, message}."""
    import ldap3
    try:
        server = _build_ldap_server(config)
        conn = ldap3.Connection(
            server,
            user=config.get("ldap_bind_dn", ""),
            password=config.get("ldap_bind_password", ""),
            auto_bind=True,
            receive_timeout=10,
        )
        conn.unbind()
        return {"ok": True, "message": "Connection successful"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def ldap_sync_users(config: dict, max_users: int = 500) -> list[dict]:
    """Synchronous LDAP user search — returns list of {email, full_name, dn}."""
    import ldap3
    server = _build_ldap_server(config)
    conn = ldap3.Connection(
        server,
        user=config.get("ldap_bind_dn", ""),
        password=config.get("ldap_bind_password", ""),
        auto_bind=True,
        receive_timeout=30,
    )

    attr_email = config.get("ldap_attr_email") or "mail"
    attr_name  = config.get("ldap_attr_name")  or "cn"
    attr_group = config.get("ldap_attr_group") or "memberOf"
    base_dn    = config.get("ldap_base_dn", "")
    user_filter = config.get("ldap_user_filter") or "(objectClass=person)"

    conn.search(
        search_base=base_dn,
        search_filter=user_filter,
        search_scope=ldap3.SUBTREE,
        attributes=[attr_email, attr_name, attr_group, "userAccountControl"],
        size_limit=max_users,
    )

    users = []
    for entry in conn.entries:
        raw_email = entry[attr_email].value if attr_email in entry else None
        if not raw_email:
            continue
        email = str(raw_email).lower().strip()
        name  = str(entry[attr_name].value) if attr_name in entry else email
        groups = []
        if attr_group in entry:
            g = entry[attr_group].value
            groups = g if isinstance(g, list) else [g]

        # Windows AD: userAccountControl bit 2 = disabled
        uac = 0
        if "userAccountControl" in entry:
            try:
                uac = int(entry["userAccountControl"].value or 0)
            except (ValueError, TypeError):
                pass
        is_active = not bool(uac & 2)

        users.append({
            "email":     email,
            "full_name": name,
            "dn":        entry.entry_dn,
            "groups":    groups,
            "is_active": is_active,
        })

    conn.unbind()
    return users
