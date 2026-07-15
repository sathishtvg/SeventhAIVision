import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_raw_db
from app.routers.licenses import seed_tenant_licenses
from app.services.leave import seed_default_leave_types

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])
_MANAGE = Depends(require_permission("tenant:manage"))


class TenantCreate(BaseModel):
    name: str
    slug: str
    subdomain: str | None = None
    timezone: str = "Asia/Singapore"
    branding: dict | None = None
    admin_email: str | None = None
    admin_password: str | None = None
    admin_full_name: str | None = None


class TenantUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    subdomain: str | None = None
    custom_domain: str | None = None
    timezone: str | None = None
    branding: dict | None = None
    is_active: bool | None = None


class TenantUserCreate(BaseModel):
    email: str
    password: str
    role_id: int = 2
    full_name: str | None = None


import json as _json

_TENANT_COLS = "id, name, slug, subdomain, custom_domain, timezone, branding, is_active, created_at, updated_at"


@router.get("", dependencies=[_MANAGE])
async def list_tenants(db: AsyncSession = Depends(get_raw_db)):
    result = await db.execute(
        text(f"SELECT {_TENANT_COLS} FROM tenants ORDER BY created_at DESC")
    )
    return [dict(row._mapping) for row in result]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGE])
async def create_tenant(body: TenantCreate, db: AsyncSession = Depends(get_raw_db)):
    import json as _j
    tenant_id = uuid.uuid4()
    subdomain = body.subdomain or body.slug
    branding_json = _j.dumps(body.branding or {})
    try:
        await db.execute(
            text(
                "INSERT INTO tenants (id, name, slug, subdomain, timezone, branding) "
                "VALUES (:id, :name, :slug, :subdomain, :tz, CAST(:branding AS jsonb))"
            ),
            {"id": tenant_id, "name": body.name, "slug": body.slug,
             "subdomain": subdomain, "tz": body.timezone, "branding": branding_json},
        )
        # Always set GUC so seed_tenant_licenses WITH CHECK passes for tenant_module_licenses
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        if body.admin_email and body.admin_password:
            await db.execute(
                text(
                    "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                    "VALUES (:id, :tid, 2, :email, :pw, :name)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tid": tenant_id,
                    "email": body.admin_email,
                    "pw": hash_password(body.admin_password),
                    "name": body.admin_full_name,
                },
            )
        await seed_tenant_licenses(db, tenant_id)
        await seed_default_leave_types(db, tenant_id)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if "unique" in str(exc).lower() or "23505" in str(exc):
            raise HTTPException(status.HTTP_409_CONFLICT, "Tenant slug already exists") from exc
        raise

    result = await db.execute(
        text(f"SELECT {_TENANT_COLS} FROM tenants WHERE id = :id"),
        {"id": tenant_id},
    )
    return dict(result.first()._mapping)


@router.get("/{tenant_id}", dependencies=[_MANAGE])
async def get_tenant(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_raw_db)):
    result = await db.execute(
        text(f"SELECT {_TENANT_COLS} FROM tenants WHERE id = :id"),
        {"id": tenant_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    return dict(row._mapping)


@router.put("/{tenant_id}", dependencies=[_MANAGE])
async def update_tenant(tenant_id: uuid.UUID, body: TenantUpdate, db: AsyncSession = Depends(get_raw_db)):
    import json as _j
    raw = body.model_dump(exclude_unset=True)
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    updates: dict = {}
    set_parts: list[str] = []
    for k, v in raw.items():
        if k == "branding":
            set_parts.append("branding = CAST(:branding AS jsonb)")
            updates["branding"] = _j.dumps(v or {})
        else:
            set_parts.append(f"{k} = :{k}")
            updates[k] = v
    updates["id"] = tenant_id
    set_clause = ", ".join(set_parts)
    result = await db.execute(
        text(f"UPDATE tenants SET {set_clause}, updated_at = now() WHERE id = :id RETURNING {_TENANT_COLS}"),
        updates,
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    await db.commit()
    return dict(row._mapping)


@router.delete("/{tenant_id}", dependencies=[_MANAGE])
async def deactivate_tenant(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_raw_db)):
    result = await db.execute(
        text("UPDATE tenants SET is_active = FALSE, updated_at = now() WHERE id = :id RETURNING id, is_active"),
        {"id": tenant_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    await db.commit()
    return dict(row._mapping)


@router.post("/{tenant_id}/users", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGE])
async def create_tenant_user(
    tenant_id: uuid.UUID, body: TenantUserCreate, db: AsyncSession = Depends(get_raw_db)
):
    result = await db.execute(
        text("SELECT id FROM tenants WHERE id = :id AND is_active = TRUE"), {"id": tenant_id}
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found or inactive")

    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    user_id = uuid.uuid4()
    try:
        result = await db.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :rid, :email, :pw, :name) "
                "RETURNING id, tenant_id, role_id, email, full_name, is_active, created_at"
            ),
            {
                "id": user_id,
                "tid": tenant_id,
                "rid": body.role_id,
                "email": body.email,
                "pw": hash_password(body.password),
                "name": body.full_name,
            },
        )
        row = result.first()
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if "unique" in str(exc).lower() or "23505" in str(exc):
            raise HTTPException(status.HTTP_409_CONFLICT, "Email already exists in this tenant") from exc
        raise
    return dict(row._mapping)
