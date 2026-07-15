"""Platform product licensing — super admin assigns products/modules per tenant.

Products (seventh_ai_vision, shift_secure) live in the global `products` table.
Each product has modules in `product_modules`. Tenant licensing lives in
`tenant_products` (is product assigned?) and `tenant_product_modules` (per-module toggle).
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_raw_db

router = APIRouter(prefix="/api/v1/platform", tags=["platform-licenses"])
_MANAGE = Depends(require_permission("license:manage"))


# ─── Schemas ─────────────────────────────────────────────────────────────────

class AssignProductBody(BaseModel):
    is_enabled: bool = True
    expires_at: datetime | None = None
    seat_limit: int | None = None
    notes: str | None = None


class ModuleToggleBody(BaseModel):
    is_enabled: bool


# ─── Product catalog (read-only, global) ─────────────────────────────────────

@router.get("/products")
async def list_products(db: AsyncSession = Depends(get_raw_db)):
    """Return all active products with their modules."""
    result = await db.execute(text(
        "SELECT p.id, p.name, p.description, p.icon, p.color, p.sort_order, "
        "       pm.module_code, pm.module_name, pm.description AS module_description, "
        "       pm.icon AS module_icon, pm.sort_order AS module_sort_order "
        "FROM products p "
        "JOIN product_modules pm ON pm.product_id = p.id "
        "WHERE p.is_active = TRUE AND pm.is_active = TRUE "
        "ORDER BY p.sort_order, pm.sort_order"
    ))
    rows = result.mappings().all()

    products: dict[str, dict] = {}
    for row in rows:
        pid = row["id"]
        if pid not in products:
            products[pid] = {
                "id": pid,
                "name": row["name"],
                "description": row["description"],
                "icon": row["icon"],
                "color": row["color"],
                "sort_order": row["sort_order"],
                "modules": [],
            }
        products[pid]["modules"].append({
            "module_code": row["module_code"],
            "module_name": row["module_name"],
            "description": row["module_description"],
            "icon": row["module_icon"],
            "sort_order": row["module_sort_order"],
        })

    return list(products.values())


@router.get("/products/{product_id}")
async def get_product(product_id: str, db: AsyncSession = Depends(get_raw_db)):
    result = await db.execute(
        text("SELECT id, name, description, icon, color, sort_order FROM products WHERE id = :id"),
        {"id": product_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")

    mods = await db.execute(
        text(
            "SELECT module_code, module_name, description, icon, sort_order "
            "FROM product_modules WHERE product_id = :pid AND is_active = TRUE "
            "ORDER BY sort_order"
        ),
        {"pid": product_id},
    )
    return {**dict(row), "modules": [dict(m) for m in mods.mappings()]}


# ─── Tenant product licensing (super admin only) ─────────────────────────────

@router.get("/tenants/{tenant_id}/products", dependencies=[_MANAGE])
async def list_tenant_products(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_raw_db)):
    """Return all products showing licensed state for this tenant.

    For each product:
      - is_enabled: whether the tenant has this product licensed
      - modules: each module with its effective is_enabled (defaulting to True if no override row)
    """
    tenant_check = await db.execute(
        text("SELECT id FROM tenants WHERE id = :id"), {"id": tenant_id}
    )
    if not tenant_check.scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})

    # Fetch tenant's licensed products
    tp_result = await db.execute(
        text(
            "SELECT tp.product_id, tp.is_enabled, tp.licensed_at, tp.expires_at, tp.seat_limit "
            "FROM tenant_products tp "
            "WHERE tp.tenant_id = :tid"
        ),
        {"tid": tenant_id},
    )
    tenant_prods = {row["product_id"]: dict(row) for row in tp_result.mappings()}

    # Fetch tenant's module overrides
    tpm_result = await db.execute(
        text(
            "SELECT product_id, module_code, is_enabled "
            "FROM tenant_product_modules WHERE tenant_id = :tid"
        ),
        {"tid": tenant_id},
    )
    tenant_mod_overrides: dict[tuple, bool] = {
        (row["product_id"], row["module_code"]): row["is_enabled"]
        for row in tpm_result.mappings()
    }

    # Fetch all products + modules
    all_result = await db.execute(text(
        "SELECT p.id, p.name, p.description, p.icon, p.color, p.sort_order, "
        "       pm.module_code, pm.module_name, pm.icon AS module_icon, pm.sort_order AS module_sort "
        "FROM products p "
        "JOIN product_modules pm ON pm.product_id = p.id "
        "WHERE p.is_active = TRUE AND pm.is_active = TRUE "
        "ORDER BY p.sort_order, pm.sort_order"
    ))

    products: dict[str, dict] = {}
    for row in all_result.mappings():
        pid = row["id"]
        if pid not in products:
            tp = tenant_prods.get(pid, {})
            products[pid] = {
                "product_id": pid,
                "product_name": row["name"],
                "description": row["description"],
                "icon": row["icon"],
                "color": row["color"],
                "is_licensed": pid in tenant_prods,
                "is_enabled": tp.get("is_enabled", False),
                "licensed_at": tp.get("licensed_at"),
                "expires_at": tp.get("expires_at"),
                "seat_limit": tp.get("seat_limit"),
                "modules": [],
            }
        override = tenant_mod_overrides.get((pid, row["module_code"]))
        products[pid]["modules"].append({
            "module_code": row["module_code"],
            "module_name": row["module_name"],
            "icon": row["module_icon"],
            # If no override row: module is enabled by default when product is licensed
            "is_enabled": override if override is not None else True,
        })

    return list(products.values())


@router.post("/tenants/{tenant_id}/products/{product_id}", dependencies=[_MANAGE], status_code=status.HTTP_201_CREATED)
async def assign_product_to_tenant(
    tenant_id: uuid.UUID,
    product_id: str,
    body: AssignProductBody,
    db: AsyncSession = Depends(get_raw_db),
):
    """Assign (or re-enable) a product for a tenant."""
    tenant_check = await db.execute(
        text("SELECT id FROM tenants WHERE id = :id"), {"id": tenant_id}
    )
    if not tenant_check.scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    prod_check = await db.execute(
        text("SELECT id FROM products WHERE id = :id AND is_active = TRUE"), {"id": product_id}
    )
    if not prod_check.scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")

    await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})

    await db.execute(
        text(
            "INSERT INTO tenant_products (tenant_id, product_id, is_enabled, expires_at, seat_limit) "
            "VALUES (:tid, :pid, :enabled, :expires, :seats) "
            "ON CONFLICT (tenant_id, product_id) DO UPDATE SET "
            "  is_enabled = EXCLUDED.is_enabled, "
            "  expires_at = EXCLUDED.expires_at, "
            "  seat_limit = EXCLUDED.seat_limit, "
            "  licensed_at = CASE WHEN tenant_products.is_enabled = FALSE AND EXCLUDED.is_enabled = TRUE "
            "                     THEN now() ELSE tenant_products.licensed_at END, "
            "  updated_at = now()"
        ),
        {"tid": tenant_id, "pid": product_id, "enabled": body.is_enabled,
         "expires": body.expires_at, "seats": body.seat_limit},
    )
    await db.commit()
    return {"tenant_id": tenant_id, "product_id": product_id, "is_enabled": body.is_enabled}


@router.delete("/tenants/{tenant_id}/products/{product_id}", dependencies=[_MANAGE])
async def revoke_product_from_tenant(
    tenant_id: uuid.UUID,
    product_id: str,
    db: AsyncSession = Depends(get_raw_db),
):
    """Disable a product for a tenant (soft revoke)."""
    await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})
    result = await db.execute(
        text(
            "UPDATE tenant_products SET is_enabled = FALSE, updated_at = now() "
            "WHERE tenant_id = :tid AND product_id = :pid RETURNING product_id"
        ),
        {"tid": tenant_id, "pid": product_id},
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not licensed for this tenant")
    await db.commit()
    return {"tenant_id": tenant_id, "product_id": product_id, "is_enabled": False}


@router.put("/tenants/{tenant_id}/products/{product_id}/modules/{module_code}", dependencies=[_MANAGE])
async def toggle_tenant_module(
    tenant_id: uuid.UUID,
    product_id: str,
    module_code: str,
    body: ModuleToggleBody,
    db: AsyncSession = Depends(get_raw_db),
):
    """Enable or disable a specific module within a product for a tenant."""
    # Validate module exists in this product
    mod_check = await db.execute(
        text(
            "SELECT module_code FROM product_modules "
            "WHERE product_id = :pid AND module_code = :code AND is_active = TRUE"
        ),
        {"pid": product_id, "code": module_code},
    )
    if not mod_check.scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Module '{module_code}' not found in product '{product_id}'")

    # Set GUC so tenant_products RLS allows reads and writes for this tenant
    await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})

    # Ensure product is licensed for this tenant
    prod_check = await db.execute(
        text("SELECT is_enabled FROM tenant_products WHERE tenant_id = :tid AND product_id = :pid"),
        {"tid": tenant_id, "pid": product_id},
    )
    if not prod_check.scalar_one_or_none():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Product is not licensed for this tenant")

    await db.execute(
        text(
            "INSERT INTO tenant_product_modules (tenant_id, product_id, module_code, is_enabled) "
            "VALUES (:tid, :pid, :code, :enabled) "
            "ON CONFLICT (tenant_id, product_id, module_code) DO UPDATE SET "
            "  is_enabled = EXCLUDED.is_enabled, updated_at = now()"
        ),
        {"tid": tenant_id, "pid": product_id, "code": module_code, "enabled": body.is_enabled},
    )
    await db.commit()
    return {
        "tenant_id": tenant_id,
        "product_id": product_id,
        "module_code": module_code,
        "is_enabled": body.is_enabled,
    }


# ─── Helper used by auth_service ─────────────────────────────────────────────

async def get_tenant_licensed_products(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:
    """Return only the products+modules that are enabled for this tenant.
    Called at login time to include in the token response.
    """
    tp_result = await db.execute(
        text(
            "SELECT tp.product_id, p.name AS product_name, p.icon, p.color, tp.is_enabled "
            "FROM tenant_products tp "
            "JOIN products p ON p.id = tp.product_id "
            "WHERE tp.tenant_id = :tid AND tp.is_enabled = TRUE "
            "  AND (tp.expires_at IS NULL OR tp.expires_at > now()) "
            "ORDER BY p.sort_order"
        ),
        {"tid": tenant_id},
    )
    products = [dict(row) for row in tp_result.mappings()]
    if not products:
        return []

    # Fetch module overrides for this tenant's enabled products
    product_ids = [p["product_id"] for p in products]
    tpm_result = await db.execute(
        text(
            "SELECT tpm.product_id, tpm.module_code, tpm.is_enabled "
            "FROM tenant_product_modules tpm "
            "WHERE tpm.tenant_id = :tid AND tpm.product_id = ANY(:pids)"
        ),
        {"tid": tenant_id, "pids": product_ids},
    )
    overrides: dict[tuple, bool] = {
        (r["product_id"], r["module_code"]): r["is_enabled"]
        for r in tpm_result.mappings()
    }

    # Get full module list for each enabled product
    mods_result = await db.execute(
        text(
            "SELECT product_id, module_code, module_name, icon "
            "FROM product_modules "
            "WHERE product_id = ANY(:pids) AND is_active = TRUE "
            "ORDER BY sort_order"
        ),
        {"pids": product_ids},
    )
    modules_by_product: dict[str, list] = {}
    for row in mods_result.mappings():
        pid = row["product_id"]
        if pid not in modules_by_product:
            modules_by_product[pid] = []
        override = overrides.get((pid, row["module_code"]))
        modules_by_product[pid].append({
            "module_code": row["module_code"],
            "module_name": row["module_name"],
            "icon": row["icon"],
            "is_enabled": override if override is not None else True,
        })

    for p in products:
        p["modules"] = modules_by_product.get(p["product_id"], [])

    return products
