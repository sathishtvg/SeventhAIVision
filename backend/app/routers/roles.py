"""Custom roles (Gap 91) — per-tenant roles with bespoke permission sets.

Built-in roles (ids 1-7, tenant_id NULL) are visible and assignable to every
tenant but not editable. Custom roles (is_custom, tenant_id = the tenant) are
fully managed here. Enforcement is unchanged: require_permission() resolves
permissions by role_id, so a custom role Just Works the moment its
role_permissions rows exist.

Gated on role:manage (super_admin + admin). Uses get_db_with_tenant so the
user_count subquery is RLS-scoped and so custom-role filtering can read the
tenant GUC; roles/permissions/role_permissions themselves have no RLS, so the
router scopes them explicitly.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/roles", tags=["roles"])

_MANAGE = Depends(require_permission("role:manage"))


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    permission_codes: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    permission_codes: list[str] | None = None


async def _validate_permission_codes(db: AsyncSession, codes: list[str], caller_role_id: int) -> None:
    """Also enforces "cannot delegate a permission you don't hold yourself":
    without this, an Admin (who holds role:manage) could create a custom
    role carrying super-admin-exclusive permissions like tenant:manage or
    license:manage, then assign themselves to it — a privilege escalation
    to full platform super-admin. Checking against the caller's own
    role_permissions (rather than hardcoding a deny-list) means this stays
    correct as new permissions are added later, and doesn't restrict an
    actual super_admin, who legitimately holds everything."""
    codes = list(set(codes))
    if not codes:
        return
    found = (await db.execute(
        text("SELECT COUNT(*) FROM permissions WHERE code = ANY(:codes)"),
        {"codes": codes},
    )).scalar()
    if (found or 0) != len(codes):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "One or more permission codes are unknown")

    held = (await db.execute(
        text(
            "SELECT COUNT(*) FROM role_permissions rp "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE rp.role_id = :rid AND p.code = ANY(:codes)"
        ),
        {"rid": caller_role_id, "codes": codes},
    )).scalar()
    if (held or 0) != len(codes):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Cannot grant a permission you do not hold yourself")


async def _load_editable_custom_role(db: AsyncSession, role_id: int, token: TokenPayload):
    """Return the role row if it is a custom role owned by the caller's
    tenant; else 404 (built-ins and other tenants' roles are not editable)."""
    row = (await db.execute(
        text("SELECT id, is_custom, tenant_id FROM roles WHERE id = :id"),
        {"id": role_id},
    )).first()
    if row is None or not row.is_custom or str(row.tenant_id) != str(token.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Custom role not found")
    return row


@router.get("", dependencies=[_MANAGE])
async def list_roles(db: AsyncSession = Depends(get_db_with_tenant)):
    """Built-in roles + this tenant's custom roles, each with its permission
    codes and how many of the tenant's users hold it."""
    result = await db.execute(text("""
        SELECT r.id, r.code, r.name, r.description, r.is_custom,
               COALESCE(
                   ARRAY_AGG(DISTINCT p.code) FILTER (WHERE p.code IS NOT NULL),
                   '{}'
               ) AS permission_codes,
               (SELECT COUNT(*) FROM users u WHERE u.role_id = r.id) AS user_count
        FROM roles r
        LEFT JOIN role_permissions rp ON rp.role_id = r.id
        LEFT JOIN permissions p ON p.id = rp.permission_id
        WHERE r.tenant_id IS NULL
           OR r.tenant_id = current_setting('app.current_tenant')::uuid
        GROUP BY r.id
        ORDER BY r.id
    """))
    return [dict(r._mapping) for r in result]


@router.get("/permissions", dependencies=[_MANAGE])
async def list_permission_catalogue(db: AsyncSession = Depends(get_db_with_tenant)):
    """The full permission catalogue, for building the role editor's checkboxes."""
    result = await db.execute(
        text("SELECT code, category, description FROM permissions ORDER BY category, code")
    )
    return [dict(r._mapping) for r in result]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGE])
async def create_role(
    body: RoleCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _validate_permission_codes(db, body.permission_codes, token.role_id)
    new_id = (await db.execute(
        text("SELECT nextval('roles_custom_id_seq')::smallint AS id")
    )).scalar()
    await db.execute(
        text("""
            INSERT INTO roles (id, code, name, description, tenant_id, is_custom)
            VALUES (:id, :code, :name, :desc,
                    current_setting('app.current_tenant')::uuid, TRUE)
        """),
        {"id": new_id, "code": f"custom_{new_id}", "name": body.name,
         "desc": body.description},
    )
    codes = list(set(body.permission_codes))
    if codes:
        await db.execute(
            text("INSERT INTO role_permissions (role_id, permission_id) "
                 "SELECT :id, id FROM permissions WHERE code = ANY(:codes)"),
            {"id": new_id, "codes": codes},
        )
    await db.commit()
    return {"id": new_id, "name": body.name, "description": body.description,
            "is_custom": True, "permission_codes": sorted(codes), "user_count": 0}


@router.put("/{role_id}", dependencies=[_MANAGE])
async def update_role(
    role_id: int,
    body: RoleUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _load_editable_custom_role(db, role_id, token)

    sets, params = [], {"id": role_id}
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.description is not None:
        sets.append("description = :desc"); params["desc"] = body.description
    if sets:
        await db.execute(text(f"UPDATE roles SET {', '.join(sets)} WHERE id = :id"), params)

    if body.permission_codes is not None:
        await _validate_permission_codes(db, body.permission_codes, token.role_id)
        await db.execute(text("DELETE FROM role_permissions WHERE role_id = :id"), {"id": role_id})
        codes = list(set(body.permission_codes))
        if codes:
            await db.execute(
                text("INSERT INTO role_permissions (role_id, permission_id) "
                     "SELECT :id, id FROM permissions WHERE code = ANY(:codes)"),
                {"id": role_id, "codes": codes},
            )
    await db.commit()
    return {"id": role_id, "updated": True}


@router.delete("/{role_id}", dependencies=[_MANAGE])
async def delete_role(
    role_id: int,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _load_editable_custom_role(db, role_id, token)
    in_use = (await db.execute(
        text("SELECT COUNT(*) FROM users WHERE role_id = :id"), {"id": role_id}
    )).scalar()
    if (in_use or 0) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{in_use} user(s) still have this role; reassign them first")
    await db.execute(text("DELETE FROM roles WHERE id = :id"), {"id": role_id})
    await db.commit()
    return {"id": role_id, "deleted": True}
