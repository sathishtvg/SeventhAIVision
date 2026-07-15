"""Alert deduplication rules — Tier 2 Feature 5.

Admins define rules keyed on (module_type, camera_id) with a window_seconds
value.  When a new alert is about to be created for a matching
(camera_id, module_type) pair, the system checks whether an open alert already
exists within the rule's window and suppresses the duplicate if so.

Rule specificity (most-specific wins):
  camera_id=X  module_type=Y  → exact match
  camera_id=X  module_type=*  → camera-level wildcard
  camera_id=*  module_type=Y  → module-level wildcard
  camera_id=*  module_type=*  → global wildcard
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/alert-dedup-rules", tags=["alert-dedup"])


class DedupRuleBody(BaseModel):
    module_type: str | None = None
    camera_id: str | None = None
    window_seconds: int = Field(default=300, ge=1, le=86400)
    is_active: bool = True


@router.get("", dependencies=[Depends(require_permission("alert:dedup:manage"))])
async def list_dedup_rules(db: AsyncSession = Depends(get_db_with_tenant)):
    rows = (await db.execute(text("""
        SELECT r.id, r.module_type, r.camera_id, r.window_seconds, r.is_active,
               r.created_at, r.updated_at,
               c.name AS camera_name
        FROM alert_dedup_rules r
        LEFT JOIN cameras c ON c.id = r.camera_id
        ORDER BY r.created_at DESC
    """))).fetchall()
    return [dict(row._mapping) for row in rows]


@router.post("", dependencies=[Depends(require_permission("alert:dedup:manage"))])
async def create_dedup_rule(
    body: DedupRuleBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    cam = body.camera_id if body.camera_id else None
    row = (await db.execute(
        text("""
            INSERT INTO alert_dedup_rules
                (tenant_id, module_type, camera_id, window_seconds, is_active, created_by_user_id)
            VALUES
                (current_setting('app.current_tenant')::uuid,
                 :mod, CAST(:cam AS uuid), :win, :act, CAST(:uid AS uuid))
            RETURNING id, module_type, camera_id, window_seconds, is_active, created_at
        """),
        {
            "mod": body.module_type,
            "cam": cam,
            "win": body.window_seconds,
            "act": body.is_active,
            "uid": token.user_id,
        },
    )).first()
    await db.commit()
    return dict(row._mapping)


@router.get("/{rule_id}", dependencies=[Depends(require_permission("alert:dedup:manage"))])
async def get_dedup_rule(rule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(
        text("""
            SELECT r.id, r.module_type, r.camera_id, r.window_seconds, r.is_active,
                   r.created_at, r.updated_at, c.name AS camera_name
            FROM alert_dedup_rules r
            LEFT JOIN cameras c ON c.id = r.camera_id
            WHERE r.id = :id
        """),
        {"id": rule_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    return dict(row._mapping)


@router.put("/{rule_id}", dependencies=[Depends(require_permission("alert:dedup:manage"))])
async def update_dedup_rule(
    rule_id: str,
    body: DedupRuleBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    cam = body.camera_id if body.camera_id else None
    row = (await db.execute(
        text("""
            UPDATE alert_dedup_rules
            SET module_type    = :mod,
                camera_id      = CAST(:cam AS uuid),
                window_seconds = :win,
                is_active      = :act,
                updated_at     = now()
            WHERE id = :id
            RETURNING id, module_type, camera_id, window_seconds, is_active, updated_at
        """),
        {"mod": body.module_type, "cam": cam, "win": body.window_seconds, "act": body.is_active, "id": rule_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    await db.commit()
    return dict(row._mapping)


@router.delete("/{rule_id}", dependencies=[Depends(require_permission("alert:dedup:manage"))])
async def delete_dedup_rule(rule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("DELETE FROM alert_dedup_rules WHERE id = :id RETURNING id"),
        {"id": rule_id},
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    await db.commit()
    return {"deleted": rule_id}


# ── Dedup check (shared by alert creation) ────────────────────────────────────

async def check_alert_dedup(
    db: AsyncSession,
    camera_id: str,
    module_type: str,
) -> tuple[bool, str | None]:
    """Return (is_duplicate, existing_alert_id).

    Finds the most specific active rule for this (camera_id, module_type) pair
    and checks whether an open alert was created within the rule's window.

    Wildcard rules (camera_id IS NULL or module_type IS NULL) broaden the
    existing-alert lookup along the wildcard dimension — a module-wildcard rule
    for camera X suppresses any module's alert on camera X, not just :mod.
    """
    rule_row = (await db.execute(
        text("""
            SELECT window_seconds,
                   camera_id   IS NULL AS camera_wildcard,
                   module_type IS NULL AS module_wildcard
            FROM alert_dedup_rules
            WHERE is_active = TRUE
              AND (camera_id = CAST(:cam AS uuid) OR camera_id IS NULL)
              AND (module_type = :mod              OR module_type IS NULL)
            ORDER BY
                (CASE WHEN camera_id   IS NOT NULL THEN 2 ELSE 0 END +
                 CASE WHEN module_type IS NOT NULL THEN 1 ELSE 0 END) DESC
            LIMIT 1
        """),
        {"cam": camera_id, "mod": module_type},
    )).first()

    if rule_row is None:
        return False, None

    # Build the existing-alert query respecting wildcard dimensions.
    # A wildcard dimension means "any value matches", so we omit that filter.
    where_parts = [
        "status = 'open'",
        "created_at > now() - (:win * INTERVAL '1 second')",
    ]
    params: dict = {"win": rule_row.window_seconds}

    if not rule_row.camera_wildcard:
        where_parts.append("camera_id = CAST(:cam AS uuid)")
        params["cam"] = camera_id

    if not rule_row.module_wildcard:
        where_parts.append("module_type = :mod")
        params["mod"] = module_type

    existing = (await db.execute(
        text(
            "SELECT id FROM alerts WHERE "
            + " AND ".join(where_parts)
            + " ORDER BY created_at DESC LIMIT 1"
        ),
        params,
    )).first()

    if existing:
        return True, str(existing.id)
    return False, None
