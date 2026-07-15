import json
import logging

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.core.pagination import paginate
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])


class IncidentCreate(BaseModel):
    title: str
    description: str | None = None
    severity: str = "medium"
    camera_id: str | None = None


class IncidentNoteCreate(BaseModel):
    note: str


class IncidentAssign(BaseModel):
    assigned_to_user_id: str


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("incident:create"))])
async def create_incident(
    request: Request,
    body: IncidentCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if body.severity not in ("low", "medium", "high", "critical"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "severity must be low/medium/high/critical")
    result = await db.execute(
        text(
            "INSERT INTO incidents (tenant_id, camera_id, title, description, severity, is_auto_created) "
            "VALUES (current_setting('app.current_tenant')::uuid, :camera_id, :title, :description, :severity, false) "
            "RETURNING id"
        ),
        {
            "camera_id": body.camera_id,
            "title": body.title,
            "description": body.description,
            "severity": body.severity,
        },
    )
    new_id = result.scalar_one()
    await db.commit()

    _redis = getattr(request.app.state, "redis", None)
    if _redis is not None:
        try:
            await _redis.publish(f"tenant_events:{token.tenant_id}", json.dumps({
                "event_type": "incident_created",
                "tenant_id": str(token.tenant_id),
                "payload": {
                    "incident_id": str(new_id),
                    "title": body.title,
                    "severity": body.severity,
                    "camera_id": body.camera_id,
                },
                "occurred_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
            }))
        except Exception as exc:
            logger.warning("incident_created publish failed: %s", exc)

    return {"id": new_id, "title": body.title, "severity": body.severity, "status": "open"}


@router.get("", dependencies=[Depends(require_permission("incident:read"))])
async def list_incidents(
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
        where_clauses.append("i.status = :status_filter")
        params["status_filter"] = status_filter
    if site_id:
        where_clauses.append("c.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    if module_type:
        where_clauses.append("i.alert_code LIKE :module_prefix")
        params["module_prefix"] = f"{module_type}.%"
    scope = site_scope_clause(allowed_sites, "c.site_id", params)
    if scope:
        where_clauses.append(scope)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    data_sql = f"""
        SELECT i.id, i.alert_id, i.camera_id, i.title, i.severity, i.status,
               i.is_auto_created, i.assigned_to_user_id, i.created_at,
               c.site_id, s.name AS site_name, c.name AS camera_name
        FROM incidents i
        LEFT JOIN cameras c ON c.id = i.camera_id
        LEFT JOIN sites s ON s.id = c.site_id
        {where}
        ORDER BY i.created_at DESC LIMIT :limit OFFSET :offset
    """
    count_sql = f"""
        SELECT COUNT(*) FROM incidents i
        LEFT JOIN cameras c ON c.id = i.camera_id
        {where}
    """
    return await paginate(db, data_sql, count_sql, params, limit, offset)


@router.post("/{incident_id}/notes", dependencies=[Depends(require_permission("incident:update"))])
async def add_incident_note(
    incident_id: str,
    body: IncidentNoteCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    result = await db.execute(
        text(
            "INSERT INTO incident_notes (tenant_id, incident_id, author_user_id, note) "
            "VALUES (current_setting('app.current_tenant')::uuid, :iid, :uid, :note) RETURNING id"
        ),
        {"iid": incident_id, "uid": token.user_id, "note": body.note},
    )
    new_id = result.scalar_one()
    await db.commit()
    return {"id": new_id}


@router.post("/{incident_id}/assign", dependencies=[Depends(require_permission("incident:assign"))])
async def assign_incident(incident_id: str, body: IncidentAssign, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("UPDATE incidents SET assigned_to_user_id = :uid, updated_at = now() WHERE id = :id RETURNING id"),
        {"id": incident_id, "uid": body.assigned_to_user_id},
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    return {"id": row.id}


@router.post("/{incident_id}/resolve", dependencies=[Depends(require_permission("incident:resolve"))])
async def resolve_incident(incident_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text(
            "UPDATE incidents SET status = 'resolved', resolved_at = now(), updated_at = now() "
            "WHERE id = :id AND status != 'resolved' RETURNING id"
        ),
        {"id": incident_id},
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found or already resolved")
    return {"id": row.id, "status": "resolved"}


# ── Extended workflow status transitions ─────────────────────────────────────

VALID_STATUSES = ("open", "dispatched", "en_route", "on_scene", "contained", "investigating", "resolved", "closed")


class StatusUpdateBody(BaseModel):
    status: str
    notes: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@router.put("/{incident_id}/status", dependencies=[Depends(require_permission("incident:update"))])
async def update_incident_status(
    incident_id: str,
    body: StatusUpdateBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Update incident status with GPS location and history tracking.

    Valid transitions: open → dispatched → en_route → on_scene → contained → resolved → closed
    Guards typically call this from mobile to report en_route, on_scene, etc.
    """
    if body.status not in VALID_STATUSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}",
        )

    # Get current status
    cur = (await db.execute(
        text("SELECT id, status FROM incidents WHERE id = :id"), {"id": incident_id}
    )).first()
    if cur is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")

    from_status = cur.status
    extra_cols = ""
    if body.status == "resolved":
        extra_cols = ", resolved_at = now()"

    await db.execute(
        text(f"UPDATE incidents SET status = :s, updated_at = now(){extra_cols} WHERE id = :id"),
        {"s": body.status, "id": incident_id},
    )
    await db.execute(
        text(
            "INSERT INTO incident_status_history "
            "(tenant_id, incident_id, changed_by_user_id, from_status, to_status, latitude, longitude, notes) "
            "VALUES (current_setting('app.current_tenant')::uuid, :iid, :uid, :from, :to, :lat, :lon, :notes)"
        ),
        {
            "iid": incident_id, "uid": token.user_id,
            "from": from_status, "to": body.status,
            "lat": body.latitude, "lon": body.longitude,
            "notes": body.notes,
        },
    )
    await db.commit()
    return {"id": incident_id, "status": body.status, "from_status": from_status}


@router.get("/{incident_id}/timeline")
async def get_incident_timeline(
    incident_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Full timeline: status history + notes in chronological order."""
    statuses = (await db.execute(
        text("""
            SELECT 'status_change' AS type, changed_at AS occurred_at,
                   from_status, to_status, notes, latitude, longitude,
                   u.full_name AS actor_name
            FROM incident_status_history h
            LEFT JOIN users u ON u.id = h.changed_by_user_id
            WHERE h.incident_id = :id
        """),
        {"id": incident_id},
    )).fetchall()

    notes = (await db.execute(
        text("""
            SELECT 'note' AS type, n.created_at AS occurred_at,
                   NULL AS from_status, NULL AS to_status,
                   note AS notes, NULL AS latitude, NULL AS longitude,
                   u.full_name AS actor_name
            FROM incident_notes n
            LEFT JOIN users u ON u.id = n.author_user_id
            WHERE n.incident_id = :id
        """),
        {"id": incident_id},
    )).fetchall()

    timeline = sorted(
        [dict(r._mapping) for r in statuses] + [dict(r._mapping) for r in notes],
        key=lambda x: x["occurred_at"],
    )
    return timeline


# ── Bulk operations ───────────────────────────────────────────────────────────

class BulkIdsBody(BaseModel):
    ids: list[str]


@router.post("/bulk-resolve", dependencies=[Depends(require_permission("incident:resolve"))])
async def bulk_resolve_incidents(
    body: BulkIdsBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if not body.ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ids must not be empty")
    if len(body.ids) > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At most 100 ids per bulk request")
    rows = (await db.execute(
        text(
            "UPDATE incidents SET status = 'resolved', resolved_at = now(), updated_at = now() "
            "WHERE id::text = ANY(:ids) AND status != 'resolved' RETURNING id"
        ),
        {"ids": body.ids},
    )).fetchall()
    await db.commit()
    return {"updated": len(rows), "skipped": len(body.ids) - len(rows)}


class BulkIncidentStatusBody(BaseModel):
    ids: list[str]
    status: str


@router.post("/bulk-update-status", dependencies=[Depends(require_permission("incident:update"))])
async def bulk_update_incident_status(
    body: BulkIncidentStatusBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if not body.ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ids must not be empty")
    if len(body.ids) > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At most 100 ids per bulk request")
    if body.status not in VALID_STATUSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}",
        )
    extra_cols = ", resolved_at = now()" if body.status == "resolved" else ""
    rows = (await db.execute(
        text(f"UPDATE incidents SET status = :s, updated_at = now(){extra_cols} WHERE id::text = ANY(:ids) RETURNING id"),
        {"ids": body.ids, "s": body.status},
    )).fetchall()
    await db.commit()
    return {"updated": len(rows), "skipped": len(body.ids) - len(rows)}
