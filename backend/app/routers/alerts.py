import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.core.pagination import paginate
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.routers.alert_dedup import check_alert_dedup
from app.services.alert_routing import resolve_alert_site_id

# ── Correlation window: link alerts of the same module_type within 5 minutes ─
_CORRELATION_WINDOW_SECONDS = int(__import__("os").environ.get("ALERT_CORRELATION_WINDOW_SECONDS", "300"))

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


class AlertCreateBody(BaseModel):
    camera_id: str
    module_type: str
    severity: str = "medium"
    title: str
    message: str | None = None


@router.post("", dependencies=[Depends(require_permission("alert:create"))])
async def create_alert(
    body: AlertCreateBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Manually create an alert, respecting active dedup rules."""
    is_dup, existing_id = await check_alert_dedup(db, body.camera_id, body.module_type)
    if is_dup:
        return {"id": existing_id, "deduplicated": True}

    row = (await db.execute(
        text("""
            INSERT INTO alerts (tenant_id, camera_id, module_type, severity, title, message, status)
            VALUES (current_setting('app.current_tenant')::uuid,
                    CAST(:cam AS uuid), :mod, :sev, :title, :msg, 'open')
            RETURNING id
        """),
        {
            "cam": body.camera_id,
            "mod": body.module_type,
            "sev": body.severity,
            "title": body.title,
            "msg": body.message,
        },
    )).first()
    await db.commit()
    return {"id": str(row.id), "deduplicated": False}


@router.get("", dependencies=[Depends(require_permission("alert:read"))])
async def list_alerts(
    db: AsyncSession = Depends(get_db_with_tenant),
    status_filter: str | None = None,
    site_id: str | None = None,
    module_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    where_clauses = []
    params: dict = {}
    if status_filter:
        where_clauses.append("a.status = :status_filter")
        params["status_filter"] = status_filter
    if site_id:
        where_clauses.append("c.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    if module_type:
        where_clauses.append("a.module_type = :module_type")
        params["module_type"] = module_type
    scope = site_scope_clause(allowed_sites, "c.site_id", params)
    if scope:
        where_clauses.append(scope)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    data_sql = f"""
        SELECT a.id, a.detection_id, a.camera_id, a.module_type, a.severity,
               a.alert_code, a.title, a.status, a.created_at,
               a.assigned_to_user_id, a.assigned_at,
               au.full_name AS assigned_to_name, au.email AS assigned_to_email,
               a.acknowledged_by_user_id, a.acknowledged_at, a.acknowledged_via,
               au2.full_name AS acknowledged_by_name,
               a.fp_reason, a.fp_marked_by_user_id, a.fp_marked_at, a.fp_marked_via,
               au3.full_name AS fp_marked_by_name,
               c.site_id, s.name AS site_name, c.name AS camera_name
        FROM alerts a
        LEFT JOIN cameras c ON c.id = a.camera_id
        LEFT JOIN sites s ON s.id = c.site_id
        LEFT JOIN users au ON au.id = a.assigned_to_user_id
        LEFT JOIN users au2 ON au2.id = a.acknowledged_by_user_id
        LEFT JOIN users au3 ON au3.id = a.fp_marked_by_user_id
        {where}
        ORDER BY a.created_at DESC LIMIT :limit OFFSET :offset
    """
    count_sql = f"""
        SELECT COUNT(*) FROM alerts a
        LEFT JOIN cameras c ON c.id = a.camera_id
        {where}
    """
    return await paginate(db, data_sql, count_sql, params, limit, offset)


_VALID_RESPONSE_CHANNELS = ("web", "mobile")


def _clean_via(via: str | None) -> str:
    return via if via in _VALID_RESPONSE_CHANNELS else "web"


class AcknowledgeBody(BaseModel):
    via: str = "web"


@router.post("/{alert_id}/acknowledge", dependencies=[Depends(require_permission("alert:acknowledge"))])
async def acknowledge_alert(
    alert_id: str,
    body: AcknowledgeBody | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    result = await db.execute(
        text(
            """
            UPDATE alerts SET status = 'acknowledged', acknowledged_by_user_id = :uid, acknowledged_at = now(),
                              acknowledged_via = :via
            WHERE id = :id AND status = 'open'
            RETURNING id
            """
        ),
        {"id": alert_id, "uid": token.user_id, "via": _clean_via(body.via if body else None)},
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found or not open")
    return {"id": row.id, "status": "acknowledged"}


class FalsePositiveBody(BaseModel):
    fp_reason: str | None = None
    via: str = "web"


@router.post("/{alert_id}/false-positive", dependencies=[Depends(require_permission("alert:acknowledge"))])
async def mark_false_positive(
    alert_id: str,
    body: FalsePositiveBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    result = await db.execute(
        text(
            """
            UPDATE alerts
            SET status = 'false_positive',
                fp_reason = :fp_reason,
                fp_marked_by_user_id = :uid,
                fp_marked_at = now(),
                fp_marked_via = :via
            WHERE id = :id AND status IN ('open', 'acknowledged')
            RETURNING id
            """
        ),
        {"id": alert_id, "uid": token.user_id, "fp_reason": body.fp_reason, "via": _clean_via(body.via)},
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found or already closed/false-positive")
    return {"id": row.id, "status": "false_positive"}


# ── Correlation ───────────────────────────────────────────────────────────────

@router.get("/{alert_id}/correlated", dependencies=[Depends(require_permission("alert:read"))])
async def get_correlated_alerts(
    alert_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Return all alerts sharing the same correlation_id as the given alert.

    When the same event (e.g., a person intrusion) triggers alerts on multiple
    cameras within the correlation window, they are grouped by a shared UUID in
    the correlation_id column. This endpoint surfaces that group so operators
    can see the full multi-camera picture without duplicate incidents.
    """
    # Fetch the target alert's correlation_id
    row = (await db.execute(
        text("SELECT correlation_id FROM alerts WHERE id = :id"), {"id": alert_id}
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")

    if row.correlation_id is None:
        return []  # this alert was never correlated

    result = await db.execute(
        text("""
            SELECT a.id, a.camera_id, a.module_type, a.severity, a.title,
                   a.status, a.created_at, c.name AS camera_name,
                   s.name AS site_name
            FROM alerts a
            LEFT JOIN cameras c ON c.id = a.camera_id
            LEFT JOIN sites s ON s.id = c.site_id
            WHERE a.correlation_id = :cid AND a.id != :aid
            ORDER BY a.created_at
        """),
        {"cid": row.correlation_id, "aid": alert_id},
    )
    return [dict(r._mapping) for r in result]


# ── Bulk operations ───────────────────────────────────────────────────────────

class BulkIdsBody(BaseModel):
    ids: list[str]


@router.post("/bulk-acknowledge", dependencies=[Depends(require_permission("alert:acknowledge"))])
async def bulk_acknowledge_alerts(
    body: BulkIdsBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if not body.ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ids must not be empty")
    if len(body.ids) > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At most 100 ids per bulk request")
    rows = (await db.execute(
        text(
            "UPDATE alerts SET status = 'acknowledged', acknowledged_by_user_id = :uid, acknowledged_at = now() "
            "WHERE id::text = ANY(:ids) AND status = 'open' RETURNING id"
        ),
        {"ids": body.ids, "uid": token.user_id},
    )).fetchall()
    await db.commit()
    return {"updated": len(rows), "skipped": len(body.ids) - len(rows)}


@router.post("/bulk-dismiss", dependencies=[Depends(require_permission("alert:acknowledge"))])
async def bulk_dismiss_alerts(
    body: BulkIdsBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if not body.ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ids must not be empty")
    if len(body.ids) > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At most 100 ids per bulk request")
    rows = (await db.execute(
        text(
            "UPDATE alerts SET status = 'dismissed' "
            "WHERE id::text = ANY(:ids) AND status IN ('open', 'acknowledged') RETURNING id"
        ),
        {"ids": body.ids},
    )).fetchall()
    await db.commit()
    return {"updated": len(rows), "skipped": len(body.ids) - len(rows)}


class BulkAssignBody(BaseModel):
    ids: list[str]
    assigned_to_user_id: str


@router.post("/bulk-assign", dependencies=[Depends(require_permission("alert:acknowledge"))])
async def bulk_assign_alerts(
    body: BulkAssignBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if not body.ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ids must not be empty")
    if len(body.ids) > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At most 100 ids per bulk request")
    user = (await db.execute(
        text("SELECT id FROM users WHERE id = :id"), {"id": body.assigned_to_user_id}
    )).first()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    rows = (await db.execute(
        text(
            "UPDATE alerts SET assigned_to_user_id = :uid, assigned_at = now() "
            "WHERE id::text = ANY(:ids) RETURNING id"
        ),
        {"ids": body.ids, "uid": body.assigned_to_user_id},
    )).fetchall()
    await db.commit()
    return {"updated": len(rows), "skipped": len(body.ids) - len(rows)}


@router.post("/bulk-unassign", dependencies=[Depends(require_permission("alert:acknowledge"))])
async def bulk_unassign_alerts(
    body: BulkIdsBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if not body.ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ids must not be empty")
    if len(body.ids) > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At most 100 ids per bulk request")
    rows = (await db.execute(
        text(
            "UPDATE alerts SET assigned_to_user_id = NULL, assigned_at = NULL "
            "WHERE id::text = ANY(:ids) RETURNING id"
        ),
        {"ids": body.ids},
    )).fetchall()
    await db.commit()
    return {"updated": len(rows), "skipped": len(body.ids) - len(rows)}


@router.post("/bulk-create-incidents", dependencies=[Depends(require_permission("incident:create"))])
async def bulk_create_incidents_from_alerts(
    body: BulkIdsBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Create one incident per alert that does not already have an incident linked.

    Skips alerts that already have an associated incident (via alert_id FK).
    Returns the list of newly-created incident IDs.
    At most 50 alert IDs per call.
    """
    if not body.ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ids must not be empty")
    if len(body.ids) > 50:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At most 50 ids per bulk request")

    # Fetch open alerts that have no existing incident yet
    alerts = (await db.execute(
        text("""
            SELECT a.id, a.camera_id, a.module_type, a.severity, a.title,
                   a.alert_code, a.message_params
            FROM alerts a
            LEFT JOIN incidents i ON i.alert_id = a.id
            WHERE a.id::text = ANY(:ids) AND i.id IS NULL
        """),
        {"ids": body.ids},
    )).fetchall()

    created_ids: list[str] = []
    for alert in alerts:
        params_json: Any = alert.message_params
        if params_json is not None and not isinstance(params_json, str):
            params_json = json.dumps(dict(params_json))

        result = await db.execute(
            text("""
                INSERT INTO incidents
                    (tenant_id, alert_id, camera_id, title, severity, alert_code, message_params, is_auto_created)
                VALUES
                    (current_setting('app.current_tenant')::uuid,
                     CAST(:aid AS uuid), :cam,
                     :title, :sev, :code, CAST(:params AS jsonb), false)
                RETURNING id
            """),
            {
                "aid": str(alert.id),
                "cam": str(alert.camera_id) if alert.camera_id else None,
                "title": f"Incident: {alert.title}",
                "sev": alert.severity,
                "code": alert.alert_code,
                "params": params_json,
            },
        )
        created_ids.append(str(result.scalar_one()))

    await db.commit()
    return {
        "created": len(created_ids),
        "skipped": len(body.ids) - len(created_ids),
        "incident_ids": created_ids,
    }


# ── Alert assignment ──────────────────────────────────────────────────────────

class AlertAssignBody(BaseModel):
    assigned_to_user_id: str


@router.post(
    "/{alert_id}/assign",
    dependencies=[Depends(require_permission("alert:acknowledge"))],
)
async def assign_alert(
    alert_id: str,
    body: AlertAssignBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    # Verify the target user exists within this tenant (RLS scopes the query)
    user = (await db.execute(
        text("SELECT id FROM users WHERE id = :id"), {"id": body.assigned_to_user_id}
    )).first()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    result = (await db.execute(
        text("""
            UPDATE alerts
               SET assigned_to_user_id = :uid, assigned_at = now()
             WHERE id = :id
         RETURNING id
        """),
        {"id": alert_id, "uid": body.assigned_to_user_id},
    )).first()
    await db.commit()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    return {"id": result.id, "assigned_to_user_id": body.assigned_to_user_id}


@router.post(
    "/{alert_id}/unassign",
    dependencies=[Depends(require_permission("alert:acknowledge"))],
)
async def unassign_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    result = (await db.execute(
        text("""
            UPDATE alerts
               SET assigned_to_user_id = NULL, assigned_at = NULL
             WHERE id = :id
         RETURNING id
        """),
        {"id": alert_id},
    )).first()
    await db.commit()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    return {"id": result.id, "assigned_to_user_id": None}


@router.post(
    "/{alert_id}/dismiss",
    dependencies=[Depends(require_permission("alert:acknowledge"))],
)
async def dismiss_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Singular counterpart to bulk-dismiss — surfaced to operators as
    "Resolve" (reuses the existing 'dismissed' status rather than adding a
    new one, so every other reader of alerts.status is unaffected)."""
    result = (await db.execute(
        text("""
            UPDATE alerts SET status = 'dismissed'
            WHERE id = :id AND status IN ('open', 'acknowledged')
            RETURNING id
        """),
        {"id": alert_id},
    )).first()
    await db.commit()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found or already closed")
    return {"id": result.id, "status": "dismissed"}


@router.get(
    "/{alert_id}/escalation-targets",
    dependencies=[Depends(require_permission("alert:acknowledge"))],
)
async def get_escalation_targets(
    alert_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Who should this alert be escalated to? Reuses the same site+shift
    routing Gap 82's push notifications use (resolve_alert_site_id in
    alert_routing.py): on-duty guards and site-assigned supervisors/
    operators for the alert's camera. Falls back to tenant admins/
    supervisors when the camera has no site or nobody is currently
    rostered — the picker must never come back empty."""
    row = (await db.execute(
        text("SELECT camera_id FROM alerts WHERE id = :id"), {"id": alert_id}
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")

    site_id = await resolve_alert_site_id(db, str(row.camera_id) if row.camera_id else None)

    if site_id is not None:
        result = await db.execute(
            text("""
                SELECT DISTINCT u.id AS user_id, u.full_name, u.role_id
                FROM users u
                WHERE u.is_active = TRUE
                  AND u.role_id = ANY(:roles)
                  AND (
                    u.id IN (SELECT guard_user_id FROM shifts
                              WHERE status = 'active' AND site_id = CAST(:sid AS uuid))
                    OR u.id IN (SELECT user_id FROM user_sites WHERE site_id = CAST(:sid AS uuid))
                  )
                ORDER BY u.role_id, u.full_name
            """),
            {"sid": site_id, "roles": [3, 4, 5]},
        )
        targets = [dict(r._mapping) for r in result]
        if targets:
            return targets

    result = await db.execute(
        text("""
            SELECT id AS user_id, full_name, role_id
            FROM users
            WHERE is_active = TRUE AND role_id = ANY(:roles)
            ORDER BY role_id, full_name
        """),
        {"roles": [2, 3]},
    )
    return [dict(r._mapping) for r in result]


# ── Alert notes ───────────────────────────────────────────────────────────────

class AlertNoteCreate(BaseModel):
    note: str


@router.post(
    "/{alert_id}/notes",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("alert:acknowledge"))],
)
async def add_alert_note(
    alert_id: str,
    body: AlertNoteCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if not body.note or not body.note.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "note must not be empty")
    # Verify alert exists in this tenant (RLS will block if not)
    exists = (await db.execute(
        text("SELECT id FROM alerts WHERE id = :id"), {"id": alert_id}
    )).first()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")

    result = await db.execute(
        text("""
            INSERT INTO alert_notes (tenant_id, alert_id, author_user_id, note)
            VALUES (current_setting('app.current_tenant')::uuid, :alert_id, :uid, :note)
            RETURNING id, alert_id, author_user_id, note, created_at
        """),
        {"alert_id": alert_id, "uid": token.user_id, "note": body.note.strip()},
    )
    row = result.mappings().first()
    await db.commit()
    return dict(row)


@router.get(
    "/{alert_id}/notes",
    dependencies=[Depends(require_permission("alert:read"))],
)
async def list_alert_notes(
    alert_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    # Verify alert exists (RLS enforces tenant scope)
    exists = (await db.execute(
        text("SELECT id FROM alerts WHERE id = :id"), {"id": alert_id}
    )).first()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")

    result = await db.execute(
        text("""
            SELECT n.id, n.alert_id, n.author_user_id, n.note,
                   n.created_at, n.updated_at,
                   u.full_name AS author_name, u.email AS author_email
            FROM alert_notes n
            LEFT JOIN users u ON u.id = n.author_user_id
            WHERE n.alert_id = :alert_id
            ORDER BY n.created_at ASC
        """),
        {"alert_id": alert_id},
    )
    return [dict(r._mapping) for r in result]


async def assign_alert_correlation(db: AsyncSession, new_alert_id: str, tenant_id: str, module_type: str) -> None:
    """Called when an alert is created: look for a recent alert of the same type
    within the correlation window and group them with a shared correlation_id.
    Uses DB-level logic so it works even if multiple API workers run concurrently.
    """
    existing = (await db.execute(
        text("""
            SELECT id, correlation_id FROM alerts
            WHERE status = 'open'
              AND module_type = :mod
              AND created_at > now() - (:window * INTERVAL '1 second')
              AND id != :aid
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"mod": module_type, "window": _CORRELATION_WINDOW_SECONDS, "aid": new_alert_id},
    )).first()

    if existing is None:
        return  # no recent sibling — this alert stands alone

    # Re-use the sibling's correlation_id or create a new one if sibling was first
    corr_id = existing.correlation_id or str(uuid.uuid4())

    # Stamp the new alert
    await db.execute(
        text("UPDATE alerts SET correlation_id = :cid WHERE id = :id"),
        {"cid": corr_id, "id": new_alert_id},
    )
    # Also stamp the sibling if it didn't have one yet
    if existing.correlation_id is None:
        await db.execute(
            text("UPDATE alerts SET correlation_id = :cid WHERE id = :id"),
            {"cid": corr_id, "id": existing.id},
        )
