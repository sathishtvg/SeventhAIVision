import json as _json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant, get_raw_db

router = APIRouter(prefix="/api/v1/licenses", tags=["licenses"])
_MANAGE = Depends(require_permission("license:manage"))

ALL_MODULES = [
    "lpr", "face", "intrusion", "ppe", "crowd",
    "fire_smoke", "weapon", "behavior",
    "tampering", "abandoned", "fall",
]


@router.get("/me/enabled-modules")
async def my_enabled_modules(db: AsyncSession = Depends(get_db_with_tenant)):
    """Which AI modules the caller's own tenant has licensed — any authenticated
    user, not just license:manage, so operator-facing UI (e.g. Live Wall's
    analytics filter) can show only what was actually purchased. Tenants with
    no license rows configured yet (fresh/demo tenants) see every module, same
    fallback convention as cameras.py's _check_module_licenses."""
    result = await db.execute(
        text("SELECT module_type, is_enabled FROM tenant_module_licenses")
    )
    rows = list(result)
    if not rows:
        return {"modules": ALL_MODULES}
    return {"modules": [r.module_type for r in rows if r.is_enabled]}


class LicenseUpsert(BaseModel):
    is_enabled: bool = True
    max_cameras: int | None = None
    expires_at: datetime | None = None
    notes: str | None = None


async def seed_tenant_licenses(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Insert disabled license rows for all 11 modules on tenant creation."""
    for module in ALL_MODULES:
        await db.execute(
            text(
                "INSERT INTO tenant_module_licenses (tenant_id, module_type, is_enabled) "
                "VALUES (:tid, :module, FALSE) "
                "ON CONFLICT (tenant_id, module_type) DO NOTHING"
            ),
            {"tid": tenant_id, "module": module},
        )


@router.get("/{tenant_id}", dependencies=[_MANAGE])
async def list_tenant_licenses(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_raw_db)):
    # Verify tenant exists (tenants table has no RLS — safe without GUC)
    tenant_check = await db.execute(
        text("SELECT id FROM tenants WHERE id = :id"), {"id": tenant_id}
    )
    if not tenant_check.scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    # Set the tenant GUC so the RLS USING clause allows reading this tenant's rows
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )

    # Fetch existing license rows
    result = await db.execute(
        text(
            "SELECT module_type, is_enabled, max_cameras, licensed_at, expires_at, notes, updated_at "
            "FROM tenant_module_licenses WHERE tenant_id = :tid ORDER BY module_type"
        ),
        {"tid": tenant_id},
    )
    existing = {row._mapping["module_type"]: dict(row._mapping) for row in result}

    # Return all 11 modules; modules without a row show as disabled
    return [
        existing.get(
            module,
            {
                "module_type": module,
                "is_enabled": False,
                "max_cameras": None,
                "licensed_at": None,
                "expires_at": None,
                "notes": None,
                "updated_at": None,
            },
        )
        for module in ALL_MODULES
    ]


@router.put("/{tenant_id}/{module_type}", dependencies=[_MANAGE])
async def upsert_license(
    tenant_id: uuid.UUID,
    module_type: str,
    body: LicenseUpsert,
    db: AsyncSession = Depends(get_raw_db),
    token: TokenPayload = Depends(get_token_payload),
):
    if module_type not in ALL_MODULES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown module_type: {module_type}. Valid: {ALL_MODULES}")

    tenant_check = await db.execute(
        text("SELECT id FROM tenants WHERE id = :id"), {"id": tenant_id}
    )
    if not tenant_check.scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    # Set the tenant GUC so the RLS WITH CHECK allows cross-tenant writes by super-admin
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    await db.execute(
        text(
            "INSERT INTO tenant_module_licenses "
            "    (tenant_id, module_type, is_enabled, max_cameras, expires_at, notes) "
            "VALUES (:tid, :module, :enabled, :max_cam, :expires, :notes) "
            "ON CONFLICT (tenant_id, module_type) DO UPDATE SET "
            "    is_enabled = EXCLUDED.is_enabled, "
            "    max_cameras = EXCLUDED.max_cameras, "
            "    expires_at = EXCLUDED.expires_at, "
            "    notes = EXCLUDED.notes, "
            "    licensed_at = CASE WHEN tenant_module_licenses.is_enabled = FALSE AND EXCLUDED.is_enabled = TRUE "
            "                       THEN now() ELSE tenant_module_licenses.licensed_at END, "
            "    updated_at = now()"
        ),
        {
            "tid": tenant_id,
            "module": module_type,
            "enabled": body.is_enabled,
            "max_cam": body.max_cameras,
            "expires": body.expires_at,
            "notes": body.notes,
        },
    )
    await db.execute(
        text(
            "INSERT INTO audit_logs (tenant_id, user_id, action, resource_type, resource_id, detail) "
            "VALUES (:tid, :uid, 'license.upsert', 'tenant_module_license', :rid, CAST(:detail AS jsonb))"
        ),
        {
            "tid": tenant_id,
            "uid": token.user_id,
            "rid": tenant_id,
            "detail": _json.dumps({"module_type": module_type, "is_enabled": body.is_enabled, "max_cameras": body.max_cameras}),
        },
    )
    await db.commit()
    return {"tenant_id": tenant_id, "module_type": module_type, "is_enabled": body.is_enabled}


@router.delete("/{tenant_id}/{module_type}", dependencies=[_MANAGE])
async def revoke_license(
    tenant_id: uuid.UUID,
    module_type: str,
    db: AsyncSession = Depends(get_raw_db),
    token: TokenPayload = Depends(get_token_payload),
):
    if module_type not in ALL_MODULES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown module_type: {module_type}")

    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    await db.execute(
        text(
            "UPDATE tenant_module_licenses SET is_enabled = FALSE, updated_at = now() "
            "WHERE tenant_id = :tid AND module_type = :module"
        ),
        {"tid": tenant_id, "module": module_type},
    )
    await db.execute(
        text(
            "INSERT INTO audit_logs (tenant_id, user_id, action, resource_type, resource_id, detail) "
            "VALUES (:tid, :uid, 'license.revoke', 'tenant_module_license', :rid, CAST(:detail AS jsonb))"
        ),
        {
            "tid": tenant_id,
            "uid": token.user_id,
            "rid": tenant_id,
            "detail": _json.dumps({"module_type": module_type, "is_enabled": False}),
        },
    )
    await db.commit()
    return {"tenant_id": tenant_id, "module_type": module_type, "is_enabled": False}
