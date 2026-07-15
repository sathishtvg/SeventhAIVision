"""SSO/SAML/LDAP — per-tenant identity provider management and auth flows.

SAML 2.0 endpoints (no authentication required — called from the IdP):
  GET  /api/v1/auth/sso/{tenant_slug}/metadata  → SP metadata XML for IdP setup
  GET  /api/v1/auth/sso/{tenant_slug}/initiate  → redirect to IdP
  POST /api/v1/auth/sso/{tenant_slug}/callback  → validate assertion, issue JWT

SSO config management (admin only):
  GET    /api/v1/sso-configs           → list configs for current tenant
  POST   /api/v1/sso-configs           → create / update config
  GET    /api/v1/sso-configs/{id}      → get config (password masked)
  PUT    /api/v1/sso-configs/{id}      → update config
  DELETE /api/v1/sso-configs/{id}      → delete config
  POST   /api/v1/sso-configs/{id}/test-ldap  → verify LDAP bind credentials
  POST   /api/v1/sso-configs/{id}/sync-ldap  → sync users from LDAP/AD
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services import auth_service
from app.services.sso_service import (
    generate_saml_metadata,
    get_sso_config_for_slug,
    jit_provision_user,
    ldap_sync_users,
    process_saml_response,
    test_ldap_connection,
)

logger = logging.getLogger(__name__)

# Two routers: one for the unauthenticated SAML flow, one for admin config CRUD
auth_sso_router   = APIRouter(prefix="/api/v1/auth/sso",  tags=["sso"])
sso_config_router = APIRouter(prefix="/api/v1/sso-configs", tags=["sso"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class SSOConfigCreate(BaseModel):
    protocol:           str = "saml"
    is_enabled:         bool = False
    # SAML
    idp_entity_id:      str | None = None
    idp_sso_url:        str | None = None
    idp_slo_url:        str | None = None
    idp_certificate:    str | None = None
    sp_entity_id:       str | None = None
    sp_acs_url:         str | None = None
    # LDAP
    ldap_host:          str | None = None
    ldap_port:          int | None = 636
    ldap_use_ssl:       bool = True
    ldap_bind_dn:       str | None = None
    ldap_bind_password: str | None = None
    ldap_base_dn:       str | None = None
    ldap_user_filter:   str | None = "(objectClass=person)"
    ldap_attr_email:    str | None = "mail"
    ldap_attr_name:     str | None = "cn"
    ldap_attr_group:    str | None = "memberOf"
    # Provisioning
    auto_provision:     bool = True
    default_role_id:    int | None = None
    role_mapping:       dict = {}


class SSOConfigUpdate(BaseModel):
    is_enabled:         bool | None = None
    idp_entity_id:      str | None = None
    idp_sso_url:        str | None = None
    idp_slo_url:        str | None = None
    idp_certificate:    str | None = None
    sp_entity_id:       str | None = None
    sp_acs_url:         str | None = None
    ldap_host:          str | None = None
    ldap_port:          int | None = None
    ldap_use_ssl:       bool | None = None
    ldap_bind_dn:       str | None = None
    ldap_bind_password: str | None = None
    ldap_base_dn:       str | None = None
    ldap_user_filter:   str | None = None
    ldap_attr_email:    str | None = None
    ldap_attr_name:     str | None = None
    ldap_attr_group:    str | None = None
    auto_provision:     bool | None = None
    default_role_id:    int | None = None
    role_mapping:       dict | None = None


# ── SAML endpoints (unauthenticated) ─────────────────────────────────────────

@auth_sso_router.get("/{tenant_slug}/metadata")
async def saml_metadata(tenant_slug: str, request: Request):
    """Return SAML SP metadata XML — give this URL to the IdP during setup."""
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        config = await get_sso_config_for_slug(db, tenant_slug, "saml")
    if not config:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SAML SSO not configured for this tenant")

    base_url = str(request.base_url).rstrip("/")
    try:
        xml = generate_saml_metadata(config, base_url)
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Metadata generation failed: {exc}")

    return Response(content=xml, media_type="application/xml")


@auth_sso_router.get("/{tenant_slug}/initiate")
async def saml_initiate(tenant_slug: str, request: Request):
    """Build SAML AuthnRequest and redirect browser to the IdP."""
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        config = await get_sso_config_for_slug(db, tenant_slug, "saml")
    if not config:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SAML SSO not configured for this tenant")

    base_url = str(request.base_url).rstrip("/")
    try:
        from app.services.sso_service import build_saml_redirect_url
        redirect_url = build_saml_redirect_url(config, base_url)
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"AuthnRequest failed: {exc}")

    return RedirectResponse(redirect_url)


@auth_sso_router.post("/{tenant_slug}/callback")
async def saml_callback(tenant_slug: str, request: Request):
    """Receive and validate SAML Response from IdP.

    On success, JIT-provisions the user and returns a JWT token pair
    (same shape as /api/v1/auth/login).
    """
    form  = await request.form()
    post_data = dict(form)

    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        config = await get_sso_config_for_slug(db, tenant_slug, "saml")
        if not config:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "SAML SSO not configured for this tenant")

        base_url  = str(request.base_url).rstrip("/")
        http_host = request.headers.get("host", "localhost")

        try:
            saml_info = process_saml_response(config, base_url, post_data, http_host)
        except ValueError as exc:
            logger.warning("SAML callback error tenant=%s: %s", tenant_slug, exc)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))

        # Set tenant GUC for JIT provisioning (user table is RLS-protected)
        tenant_id = str(config["tenant_id_val"])
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": tenant_id},
        )

        try:
            user = await jit_provision_user(
                db, tenant_id, config,
                email=saml_info["email"],
                full_name=saml_info["full_name"],
                sso_provider="saml",
                sso_subject_id=saml_info["name_id"],
                attributes=saml_info.get("attributes"),
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))

        await db.execute(
            text("UPDATE users SET last_login_at = now() WHERE id = CAST(:uid AS uuid)"),
            {"uid": str(user["id"])},
        )
        await db.commit()

    # Issue tokens using the standard service
    token_pair = await auth_service.issue_tokens(
        tenant_id=tenant_id,
        user_id=str(user["id"]),
        role_id=int(user["role_id"]),
    )
    return token_pair


# ── SSO config CRUD ───────────────────────────────────────────────────────────

@sso_config_router.get("", dependencies=[Depends(require_permission("sso:manage"))])
async def list_sso_configs(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(text("""
        SELECT id, protocol, is_enabled,
               idp_entity_id, idp_sso_url, idp_slo_url, sp_entity_id, sp_acs_url,
               ldap_host, ldap_port, ldap_use_ssl, ldap_bind_dn,
               ldap_base_dn, ldap_user_filter, ldap_attr_email, ldap_attr_name, ldap_attr_group,
               auto_provision, default_role_id, role_mapping,
               created_at, updated_at
        FROM tenant_sso_configs
        ORDER BY protocol
    """))
    rows = []
    for r in result.mappings():
        d = dict(r)
        d["idp_certificate"] = "***" if d.get("idp_certificate") else None
        rows.append(d)
    return rows


@sso_config_router.post("", dependencies=[Depends(require_permission("sso:manage"))])
async def create_sso_config(
    body: SSOConfigCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if body.protocol not in ("saml", "ldap", "oidc"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "protocol must be saml, ldap, or oidc")

    import json
    result = await db.execute(text("""
        INSERT INTO tenant_sso_configs (
            tenant_id, protocol, is_enabled,
            idp_entity_id, idp_sso_url, idp_slo_url, idp_certificate, sp_entity_id, sp_acs_url,
            ldap_host, ldap_port, ldap_use_ssl, ldap_bind_dn, ldap_bind_password,
            ldap_base_dn, ldap_user_filter, ldap_attr_email, ldap_attr_name, ldap_attr_group,
            auto_provision, default_role_id, role_mapping
        ) VALUES (
            current_setting('app.current_tenant')::uuid, :proto, :enabled,
            :idp_eid, :idp_sso, :idp_slo, :idp_cert, :sp_eid, :sp_acs,
            :lhost, :lport, :lssl, :lbdn, :lbpw,
            :lbase, :lfilter, :lemail, :lname, :lgroup,
            :autoprov, :drole, CAST(:rmap AS jsonb)
        )
        RETURNING id, protocol, is_enabled, created_at
    """), {
        "proto":    body.protocol,
        "enabled":  body.is_enabled,
        "idp_eid":  body.idp_entity_id,
        "idp_sso":  body.idp_sso_url,
        "idp_slo":  body.idp_slo_url,
        "idp_cert": body.idp_certificate,
        "sp_eid":   body.sp_entity_id,
        "sp_acs":   body.sp_acs_url,
        "lhost":    body.ldap_host,
        "lport":    body.ldap_port,
        "lssl":     body.ldap_use_ssl,
        "lbdn":     body.ldap_bind_dn,
        "lbpw":     body.ldap_bind_password,
        "lbase":    body.ldap_base_dn,
        "lfilter":  body.ldap_user_filter,
        "lemail":   body.ldap_attr_email,
        "lname":    body.ldap_attr_name,
        "lgroup":   body.ldap_attr_group,
        "autoprov": body.auto_provision,
        "drole":    body.default_role_id,
        "rmap":     json.dumps(body.role_mapping),
    })
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@sso_config_router.get("/{config_id}", dependencies=[Depends(require_permission("sso:manage"))])
async def get_sso_config(config_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("SELECT * FROM tenant_sso_configs WHERE id = CAST(:id AS uuid)"),
        {"id": config_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SSO config not found")
    d = dict(row)
    d["idp_certificate"]  = "***" if d.get("idp_certificate")  else None
    d["ldap_bind_password"] = "***" if d.get("ldap_bind_password") else None
    return d


@sso_config_router.put("/{config_id}", dependencies=[Depends(require_permission("sso:manage"))])
async def update_sso_config(
    config_id: str,
    body: SSOConfigUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    import json
    updates = {}
    fields = {
        "is_enabled": "enabled", "idp_entity_id": "idp_eid", "idp_sso_url": "idp_sso",
        "idp_slo_url": "idp_slo", "idp_certificate": "idp_cert",
        "sp_entity_id": "sp_eid", "sp_acs_url": "sp_acs",
        "ldap_host": "lhost", "ldap_port": "lport", "ldap_use_ssl": "lssl",
        "ldap_bind_dn": "lbdn", "ldap_bind_password": "lbpw",
        "ldap_base_dn": "lbase", "ldap_user_filter": "lfilter",
        "ldap_attr_email": "lemail", "ldap_attr_name": "lname", "ldap_attr_group": "lgroup",
        "auto_provision": "autoprov", "default_role_id": "drole",
    }
    set_clauses = []
    params: dict[str, Any] = {"id": config_id}
    for attr, param in fields.items():
        val = getattr(body, attr, None)
        if val is not None:
            set_clauses.append(f"{attr} = :{param}")
            params[param] = val

    if body.role_mapping is not None:
        set_clauses.append("role_mapping = CAST(:rmap AS jsonb)")
        params["rmap"] = json.dumps(body.role_mapping)

    if not set_clauses:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")

    set_clauses.append("updated_at = now()")
    result = await db.execute(
        text(f"UPDATE tenant_sso_configs SET {', '.join(set_clauses)} "
             f"WHERE id = CAST(:id AS uuid) RETURNING id, protocol, is_enabled, updated_at"),
        params,
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SSO config not found")
    await db.commit()
    return dict(row._mapping)


@sso_config_router.delete("/{config_id}", dependencies=[Depends(require_permission("sso:manage"))])
async def delete_sso_config(config_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("DELETE FROM tenant_sso_configs WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": config_id},
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SSO config not found")
    await db.commit()
    return {"deleted": True}


# ── LDAP test + sync ──────────────────────────────────────────────────────────

@sso_config_router.post("/{config_id}/test-ldap",
                         dependencies=[Depends(require_permission("sso:manage"))])
async def test_ldap(config_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """Test LDAP/AD connection — returns {ok, message}."""
    result = await db.execute(
        text("SELECT * FROM tenant_sso_configs WHERE id = CAST(:id AS uuid) AND protocol = 'ldap'"),
        {"id": config_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "LDAP config not found")

    config = dict(row)
    # Run synchronous LDAP call in a thread pool
    ldap_result = await asyncio.get_event_loop().run_in_executor(
        None, test_ldap_connection, config
    )
    return ldap_result


@sso_config_router.post("/{config_id}/sync-ldap",
                          dependencies=[Depends(require_permission("sso:manage"))])
async def sync_ldap(
    config_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Sync users from LDAP/AD — creates/updates user records.

    Returns {synced, created, updated, disabled} counts.
    """
    result = await db.execute(
        text("SELECT * FROM tenant_sso_configs WHERE id = CAST(:id AS uuid) AND protocol = 'ldap'"),
        {"id": config_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "LDAP config not found")

    config = dict(row)
    tenant_id = str(config["tenant_id"])

    # Fetch LDAP users (blocking — run in thread)
    try:
        ldap_users = await asyncio.get_event_loop().run_in_executor(
            None, ldap_sync_users, config
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LDAP error: {exc}")

    created = updated = disabled = 0
    default_role_id = config.get("default_role_id") or 6
    role_mapping    = config.get("role_mapping") or {}

    import json
    for lu in ldap_users:
        email = lu["email"]

        # Determine role from group mapping
        role_id = default_role_id
        if role_mapping:
            for group_dn, mapped_role in role_mapping.items():
                if group_dn in lu.get("groups", []):
                    role_id = int(mapped_role)
                    break

        # Upsert: if email exists → update; else → create
        existing = (await db.execute(
            text("SELECT id, is_active FROM users WHERE tenant_id = CAST(:tid AS uuid) AND email = :email"),
            {"tid": tenant_id, "email": email},
        )).first()

        if existing:
            await db.execute(
                text("""
                    UPDATE users
                    SET full_name = :name, role_id = :role, is_active = :active,
                        sso_provider = 'ldap', sso_subject_id = :dn, updated_at = now()
                    WHERE id = CAST(:uid AS uuid)
                """),
                {"name": lu["full_name"], "role": role_id,
                 "active": lu["is_active"], "dn": lu["dn"], "uid": str(existing.id)},
            )
            updated += 1
            if not lu["is_active"]:
                disabled += 1
        else:
            if not lu["is_active"]:
                continue  # skip creating disabled accounts
            new_id = str(__import__("uuid").uuid4())
            await db.execute(
                text("""
                    INSERT INTO users
                        (id, tenant_id, role_id, email, hashed_password, full_name,
                         sso_provider, sso_subject_id, sso_provisioned)
                    VALUES
                        (CAST(:uid AS uuid), CAST(:tid AS uuid), :role, :email,
                         NULL, :name, 'ldap', :dn, TRUE)
                """),
                {"uid": new_id, "tid": tenant_id, "role": role_id,
                 "email": email, "name": lu["full_name"], "dn": lu["dn"]},
            )
            created += 1

    await db.commit()

    synced = len(ldap_users)
    logger.info("ldap_sync: tenant=%s synced=%d created=%d updated=%d disabled=%d",
                tenant_id, synced, created, updated, disabled)
    return {
        "synced":   synced,
        "created":  created,
        "updated":  updated,
        "disabled": disabled,
    }
