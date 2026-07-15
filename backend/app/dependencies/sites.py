"""Site-scoped access control (Gap 81).

RLS isolates *tenants*; this dependency narrows visibility to *sites* within
a tenant for users who have explicit site assignments in user_sites.

Semantics:
  - Roles 1-2 (super_admin, admin) and API keys → None (unrestricted).
  - Roles 3-6 → restricted to assigned sites when assignments exist;
    None when no assignments (fail-open, backwards compatible).
  - Role 7 (client) → assigned sites, or [] when none (fail-closed —
    an external client user must be explicitly granted their site).

Routers add the returned filter via `site_scope_clause()`; a returned
clause of "FALSE" (empty allowed list) yields zero rows.
"""
from __future__ import annotations

import uuid

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.tenant import get_db_with_tenant

_UNRESTRICTED_ROLES = {1, 2}
_CLIENT_ROLE = 7


async def get_allowed_site_ids(
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
) -> list[str] | None:
    """None = unrestricted; [] = no visibility; else list of allowed site UUIDs.

    FastAPI's per-request dependency cache means this shares the same
    RLS-scoped session as the endpoint that depends on it.
    """
    if token.via_api_key or token.role_id in _UNRESTRICTED_ROLES:
        return None
    result = await db.execute(
        text("SELECT site_id FROM user_sites WHERE user_id = CAST(:uid AS uuid)"),
        {"uid": token.user_id},
    )
    site_ids = [str(r.site_id) for r in result]
    if site_ids:
        return site_ids
    return [] if token.role_id == _CLIENT_ROLE else None


def site_scope_clause(
    allowed: list[str] | None, column: str, params: dict
) -> str | None:
    """Build a SQL predicate limiting `column` to the allowed sites.

    Returns None when unrestricted (caller skips the clause). Mutates
    `params` with the bound uuid[] value. Rows whose site column is NULL
    are excluded for restricted users (fail-closed).
    """
    if allowed is None:
        return None
    if not allowed:
        return "FALSE"
    params["allowed_site_ids"] = [uuid.UUID(s) for s in allowed]
    return f"{column} = ANY(:allowed_site_ids)"


def is_site_allowed(allowed: list[str] | None, site_id) -> bool:
    """Check a single site (or a row's site value) against the allowed set."""
    if allowed is None:
        return True
    return site_id is not None and str(site_id) in allowed


async def get_allowed_client_ids(
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
) -> list[str] | None:
    """Billing-client visibility for the invoicing router (Client Portal
    Invoice Viewing round). None = unrestricted (every other role reaching
    this dependency is already gated by invoicing:read/manage); [] = no
    visibility; else the distinct billing_client ids reachable through the
    caller's own site assignments. Only role 7 is narrowed here —
    invoicing:manage already excludes role 7 entirely, so this only scopes
    the read surface."""
    if token.role_id != _CLIENT_ROLE:
        return None
    result = await db.execute(
        text(
            "SELECT DISTINCT s.client_id FROM user_sites us "
            "JOIN sites s ON s.id = us.site_id "
            "WHERE us.user_id = CAST(:uid AS uuid) AND s.client_id IS NOT NULL"
        ),
        {"uid": token.user_id},
    )
    return [str(r.client_id) for r in result]


def client_scope_clause(allowed: list[str] | None, column: str, params: dict) -> str | None:
    """Mirrors site_scope_clause exactly, kept separate since the bound
    param name (allowed_client_ids) differs and there's only ever one
    caller (invoicing.py) today."""
    if allowed is None:
        return None
    if not allowed:
        return "FALSE"
    params["allowed_client_ids"] = [uuid.UUID(c) for c in allowed]
    return f"{column} = ANY(:allowed_client_ids)"


def is_client_allowed(allowed: list[str] | None, client_id) -> bool:
    """Check a single billing_client (or a row's client_id) against the allowed set."""
    if allowed is None:
        return True
    return client_id is not None and str(client_id) in allowed
