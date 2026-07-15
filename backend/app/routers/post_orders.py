"""Post orders — per-site standing instructions with acknowledgment tracking
(Gap 87).

Guards read the post orders for their sites and acknowledge; supervisors see
who has and hasn't acknowledged the CURRENT version. Editing title/body bumps
the version, invalidating earlier acknowledgments by construction (acks are
keyed per version).

Permissions:
  read + acknowledge  — shift:read   (all operational roles + viewer)
  create/update/delete — shift:manage (roles 1-3)
Site scoping (Gap 81) applies to every read path.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/post-orders", tags=["guard-ops"])

VALID_CATEGORIES = {"general", "emergency", "access", "patrol", "equipment", "contacts"}

# Roles whose site assignment makes them accountable for acknowledging
_ACK_ROLES = (3, 4, 5)


class PostOrderCreate(BaseModel):
    site_id: str
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    category: str = "general"
    requires_acknowledgment: bool = True


class PostOrderUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1)
    category: str | None = None
    requires_acknowledgment: bool | None = None
    is_active: bool | None = None


def _check_category(category: str) -> None:
    if category not in VALID_CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"category must be one of {sorted(VALID_CATEGORIES)}")


@router.get("", dependencies=[Depends(require_permission("shift:read"))])
async def list_post_orders(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    site_id: str | None = None,
    include_inactive: bool = False,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Post orders visible to the caller, each annotated with the caller's
    acknowledgment state for the CURRENT version."""
    where_clauses = []
    params: dict = {"uid": token.user_id}
    if not include_inactive:
        where_clauses.append("po.is_active = TRUE")
    if site_id:
        where_clauses.append("po.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    scope = site_scope_clause(allowed_sites, "po.site_id", params)
    if scope:
        where_clauses.append(scope)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    result = await db.execute(
        text(f"""
            SELECT po.id, po.site_id, po.title, po.category, po.version,
                   po.is_active, po.requires_acknowledgment,
                   po.created_at, po.updated_at,
                   s.name AS site_name,
                   u.full_name AS created_by_name,
                   (a.version IS NOT NULL) AS acknowledged
            FROM post_orders po
            JOIN sites s ON s.id = po.site_id
            LEFT JOIN users u ON u.id = po.created_by_user_id
            LEFT JOIN post_order_acks a
                   ON a.post_order_id = po.id
                  AND a.user_id = CAST(:uid AS uuid)
                  AND a.version = po.version
            {where}
            ORDER BY s.name, po.category, po.title
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("shift:manage"))])
async def create_post_order(
    body: PostOrderCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    _check_category(body.category)
    site = await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:id AS uuid)"),
                            {"id": body.site_id})
    if site.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    result = await db.execute(
        text("""
            INSERT INTO post_orders
                (tenant_id, site_id, title, body, category,
                 requires_acknowledgment, created_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:site AS uuid),
                    :title, :body, :category, :req_ack, CAST(:uid AS uuid))
            RETURNING id, site_id, title, body, category, version, is_active,
                      requires_acknowledgment, created_at, updated_at
        """),
        {"site": body.site_id, "title": body.title, "body": body.body,
         "category": body.category, "req_ack": body.requires_acknowledgment,
         "uid": token.user_id},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.get("/{order_id}", dependencies=[Depends(require_permission("shift:read"))])
async def get_post_order(
    order_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    result = await db.execute(
        text("""
            SELECT po.id, po.site_id, po.title, po.body, po.category, po.version,
                   po.is_active, po.requires_acknowledgment,
                   po.created_at, po.updated_at,
                   s.name AS site_name,
                   (a.version IS NOT NULL) AS acknowledged
            FROM post_orders po
            JOIN sites s ON s.id = po.site_id
            LEFT JOIN post_order_acks a
                   ON a.post_order_id = po.id
                  AND a.user_id = CAST(:uid AS uuid)
                  AND a.version = po.version
            WHERE po.id = CAST(:id AS uuid)
        """),
        {"id": order_id, "uid": token.user_id},
    )
    row = result.mappings().first()
    if row is None or not is_site_allowed(allowed_sites, row["site_id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post order not found")
    return dict(row)


@router.put("/{order_id}", dependencies=[Depends(require_permission("shift:manage"))])
async def update_post_order(
    order_id: str,
    body: PostOrderUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if body.category is not None:
        _check_category(body.category)
    sets, params = [], {"id": order_id}
    if body.title is not None:
        sets.append("title = :title"); params["title"] = body.title
    if body.body is not None:
        sets.append("body = :body"); params["body"] = body.body
    if body.category is not None:
        sets.append("category = :category"); params["category"] = body.category
    if body.requires_acknowledgment is not None:
        sets.append("requires_acknowledgment = :req_ack")
        params["req_ack"] = body.requires_acknowledgment
    if body.is_active is not None:
        sets.append("is_active = :active"); params["active"] = body.is_active
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No fields to update")

    # Content edits invalidate acknowledgments: bump the version
    if body.title is not None or body.body is not None:
        sets.append("version = version + 1")
    sets.append("updated_at = now()")

    result = await db.execute(
        text(f"""
            UPDATE post_orders SET {', '.join(sets)}
            WHERE id = CAST(:id AS uuid)
            RETURNING id, site_id, title, body, category, version, is_active,
                      requires_acknowledgment, updated_at
        """),
        params,
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post order not found")
    await db.commit()
    return dict(row)


@router.delete("/{order_id}", dependencies=[Depends(require_permission("shift:manage"))])
async def deactivate_post_order(order_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """Soft delete — acknowledgment history is compliance data and survives."""
    result = await db.execute(
        text("UPDATE post_orders SET is_active = FALSE, updated_at = now() "
             "WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": order_id},
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post order not found")
    await db.commit()
    return {"id": order_id, "is_active": False}


@router.post("/{order_id}/acknowledge", dependencies=[Depends(require_permission("shift:read"))])
async def acknowledge_post_order(
    order_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Record that the caller has read the CURRENT version. Idempotent."""
    row = (await db.execute(
        text("SELECT site_id, version, is_active FROM post_orders WHERE id = CAST(:id AS uuid)"),
        {"id": order_id},
    )).first()
    if row is None or not is_site_allowed(allowed_sites, row.site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post order not found")
    if not row.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Post order is no longer active")
    await db.execute(
        text("""
            INSERT INTO post_order_acks (post_order_id, user_id, tenant_id, version)
            VALUES (CAST(:oid AS uuid), CAST(:uid AS uuid),
                    current_setting('app.current_tenant')::uuid, :version)
            ON CONFLICT (post_order_id, user_id, version) DO NOTHING
        """),
        {"oid": order_id, "uid": token.user_id, "version": row.version},
    )
    await db.commit()
    return {"id": order_id, "acknowledged": True, "version": row.version}


@router.get("/{order_id}/acks", dependencies=[Depends(require_permission("shift:manage"))])
async def list_acknowledgments(
    order_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Compliance view: who has acknowledged the current version, and which
    site-assigned operational users (roles 3-5) still haven't."""
    order = (await db.execute(
        text("SELECT site_id, version FROM post_orders WHERE id = CAST(:id AS uuid)"),
        {"id": order_id},
    )).first()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post order not found")

    acked = await db.execute(
        text("""
            SELECT a.user_id, u.full_name, u.email, a.acknowledged_at
            FROM post_order_acks a
            JOIN users u ON u.id = a.user_id
            WHERE a.post_order_id = CAST(:oid AS uuid) AND a.version = :version
            ORDER BY a.acknowledged_at
        """),
        {"oid": order_id, "version": order.version},
    )
    acked_rows = [dict(r._mapping) for r in acked]

    pending = await db.execute(
        text("""
            SELECT u.id AS user_id, u.full_name, u.email
            FROM user_sites us
            JOIN users u ON u.id = us.user_id
            WHERE us.site_id = :site_id
              AND u.is_active = TRUE
              AND u.role_id = ANY(:roles)
              AND NOT EXISTS (
                  SELECT 1 FROM post_order_acks a
                  WHERE a.post_order_id = CAST(:oid AS uuid)
                    AND a.user_id = u.id AND a.version = :version
              )
            ORDER BY u.full_name
        """),
        {"site_id": order.site_id, "roles": list(_ACK_ROLES),
         "oid": order_id, "version": order.version},
    )
    pending_rows = [dict(r._mapping) for r in pending]

    return {
        "post_order_id": order_id,
        "current_version": order.version,
        "acknowledged": acked_rows,
        "pending": pending_rows,
    }
