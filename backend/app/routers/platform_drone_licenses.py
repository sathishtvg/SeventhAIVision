"""Super Admin: switch Drone Patrol on or off for a tenant, and set its limits.

Its own endpoint rather than a new entry in licenses.ALL_MODULES. That list
drives the eleven AI-module licences and their "no rows means everything"
fallback; adding drone_patrol there would change behaviour for every tenant with
no licence rows yet (DRONE_PATROL_GAP_ANALYSIS.md §19.1–19.2).

license:manage is held by Super Admin alone and is one of the MFA-gated
permissions, so a platform owner who has not enrolled in 2FA cannot use this
either.

THE PLATFORM TENANT IS REFUSED. The platform owner licenses the module to
customers; it does not run drone patrols itself. Letting it would quietly make
Super Admin a tenant security operator, which is the separation the brief insists
on.

Reading and writing another tenant's rows works the way licenses.py does it:
the transaction-local tenant setting is pointed at the target for this one
transaction. Everything that must see it happens before the commit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.drone_module import entitlement_problem
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import _client_ip, get_raw_db
from app.services.audit import write_audit_log

router = APIRouter(prefix="/api/v1/platform", tags=["platform-licenses"])
_MANAGE = Depends(require_permission("license:manage"))


class DroneLicenseUpsert(BaseModel):
    is_enabled: bool
    expires_at: datetime | None = None
    max_drones: int | None = Field(None, ge=0, le=100_000)
    max_missions: int | None = Field(None, ge=0, le=1_000_000)
    max_sites: int | None = Field(None, ge=0, le=100_000)
    notes: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _expiry_in_future(self):
        if self.is_enabled and self.expires_at is not None and self.expires_at <= datetime.now(timezone.utc):
            raise ValueError("An enabled licence cannot already have expired.")
        return self


async def _target_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    row = (await db.execute(text(
        "SELECT id, name, slug, is_platform FROM tenants WHERE id = CAST(:id AS uuid)"),
        {"id": str(tenant_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Tenant not found")
    if row["is_platform"]:
        raise HTTPException(422, "The platform tenant does not run drone patrols; "
                                 "license the module to a customer tenant.")
    # Scope this transaction to the target, so RLS lets us read and write its row.
    await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"),
                     {"tid": str(tenant_id)})
    return dict(row)


async def _describe(db: AsyncSession, tenant: dict) -> dict:
    row = (await db.execute(text(
        "SELECT * FROM drone_module_licenses WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": str(tenant["id"])})).mappings().first()
    ent = dict(row) if row else None
    problem = entitlement_problem(ent, datetime.now(timezone.utc))
    return {
        "tenant_id": str(tenant["id"]), "tenant_name": tenant["name"], "tenant_slug": tenant["slug"],
        "licensed": problem is None, "reason": problem,
        "is_enabled": bool(ent and ent["is_enabled"]),
        "licensed_at": ent["licensed_at"] if ent else None,
        "expires_at": ent["expires_at"] if ent else None,
        "max_drones": ent["max_drones"] if ent else None,
        "max_missions": ent["max_missions"] if ent else None,
        "max_sites": ent["max_sites"] if ent else None,
        "notes": ent["notes"] if ent else None,
        "updated_at": ent["updated_at"] if ent else None,
    }


@router.get("/tenants/{tenant_id}/drone-license", dependencies=[_MANAGE])
async def get_drone_license(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_raw_db)):
    tenant = await _target_tenant(db, tenant_id)
    return await _describe(db, tenant)


@router.put("/tenants/{tenant_id}/drone-license", dependencies=[_MANAGE])
async def put_drone_license(
    tenant_id: uuid.UUID, body: DroneLicenseUpsert, request: Request,
    db: AsyncSession = Depends(get_raw_db),
    token: TokenPayload = Depends(get_token_payload),
):
    """Create or replace the tenant's Drone Patrol licence. licensed_at records
    when it was most recently switched on, not when the row was first made."""
    tenant = await _target_tenant(db, tenant_id)
    await db.execute(text("""
        INSERT INTO drone_module_licenses
            (tenant_id, is_enabled, licensed_at, expires_at, max_drones, max_missions, max_sites,
             licensed_by_user_id, notes)
        VALUES (CAST(:tid AS uuid), CAST(:enabled AS boolean),
                CASE WHEN CAST(:enabled AS boolean) THEN now() END, :expires,
                :drones, :missions, :sites, CAST(:by AS uuid), :notes)
        ON CONFLICT (tenant_id) DO UPDATE SET
            is_enabled   = EXCLUDED.is_enabled,
            licensed_at  = CASE WHEN EXCLUDED.is_enabled AND NOT drone_module_licenses.is_enabled
                                THEN now() ELSE drone_module_licenses.licensed_at END,
            expires_at   = EXCLUDED.expires_at,
            max_drones   = EXCLUDED.max_drones,
            max_missions = EXCLUDED.max_missions,
            max_sites    = EXCLUDED.max_sites,
            licensed_by_user_id = EXCLUDED.licensed_by_user_id,
            notes        = EXCLUDED.notes,
            updated_at   = now()
    """), {"tid": str(tenant_id), "enabled": body.is_enabled, "expires": body.expires_at,
           "drones": body.max_drones, "missions": body.max_missions, "sites": body.max_sites,
           "by": token.user_id, "notes": body.notes})
    # Written into the customer's own audit log, where their administrators will
    # look for who changed what their organisation may do.
    await write_audit_log(
        db, tenant_id=str(tenant_id), user_id=token.user_id,
        action="drone.license.set", resource_type="drone_module_license", resource_id=str(tenant_id),
        ip_address=_client_ip(request),
        detail={"is_enabled": body.is_enabled,
                "expires_at": body.expires_at.isoformat() if body.expires_at else None,
                "max_drones": body.max_drones, "max_missions": body.max_missions,
                "max_sites": body.max_sites},
    )
    result = await _describe(db, tenant)
    await db.commit()
    return result
