"""Small helpers every drone router shares: site visibility, scoping, audit.

SITE VISIBILITY ANSWERS 404, NOT 403. A supervisor restricted to two sites who
asks for a drone on a third learns nothing from "not found" — not even that the
site has drones. "Forbidden" would confirm it exists.

AUDIT GOES THROUGH services.audit.write_audit_log, the hash-chained log, with the
caller's address. Every create, change, delete, licence change and event decision
in the drone module is written there.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload
from app.dependencies.sites import is_site_allowed, site_scope_clause
from app.dependencies.tenant import _client_ip
from app.services.audit import write_audit_log


def scope_sql(allowed: list[str] | None, column: str, params: dict) -> str:
    """" AND <column> is an allowed site", or "" when the caller is
    unrestricted. Rows with no site are hidden from restricted callers."""
    clause = site_scope_clause(allowed, column, params)
    return "" if clause is None else f" AND {clause}"


async def site_or_404(db: AsyncSession, site_id: uuid.UUID | str | None,
                      allowed: list[str] | None) -> dict:
    """The site row, if it exists in this tenant and the caller may see it."""
    if site_id is None:
        raise HTTPException(422, "A site is required.")
    row = (await db.execute(text(
        "SELECT id, name, latitude, longitude, geofence_radius_meters, geofence_polygon "
        "  FROM sites WHERE id = CAST(:id AS uuid)"), {"id": str(site_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["id"]):
        raise HTTPException(404, "Site not found")
    return dict(row)


def assert_site_visible(allowed: list[str] | None, site_id: Any, what: str) -> None:
    """For rows already fetched: hide them from a caller outside their site."""
    if not is_site_allowed(allowed, site_id):
        raise HTTPException(404, f"{what} not found")


async def audit(db: AsyncSession, request: Request, token: TokenPayload, action: str,
                resource_type: str, resource_id: Any, detail: dict | None = None) -> None:
    await write_audit_log(
        db,
        tenant_id=token.tenant_id,
        user_id=token.user_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        ip_address=_client_ip(request),
        detail=detail,
    )


def unique_violation(exc: Exception, constraint: str) -> bool:
    """Did this IntegrityError come from the named unique constraint or index?"""
    return constraint in str(getattr(exc, "orig", exc))
