"""API routes for Phase 5 advanced safety detection events:
- Camera tampering events
- Abandoned object events
- Slip/fall events
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/advanced-detections", tags=["advanced-detections"])


# ── Camera tampering ──────────────────────────────────────────────────────────

@router.get("/tampering", dependencies=[Depends(require_permission("tampering:read"))])
async def list_tampering_events(
    camera_id: Optional[str] = Query(None),
    tampering_type: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db=Depends(get_db_with_tenant),
):
    where_clauses = []
    params: dict = {"limit": limit}
    if camera_id:
        where_clauses.append("te.camera_id = :camera_id")
        params["camera_id"] = camera_id
    if tampering_type:
        where_clauses.append("te.tampering_type = :tampering_type")
        params["tampering_type"] = tampering_type

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    rows = (await db.execute(
        text(f"""
            SELECT te.detection_id, te.detected_at, te.camera_id, te.tampering_type,
                   te.score, te.reason,
                   c.name AS camera_name
            FROM tampering_events te
            LEFT JOIN cameras c ON c.id = te.camera_id
            {where_sql}
            ORDER BY te.detected_at DESC
            LIMIT :limit
        """),
        params,
    )).fetchall()

    return [dict(r._mapping) for r in rows]


# ── Abandoned objects ─────────────────────────────────────────────────────────

@router.get("/abandoned", dependencies=[Depends(require_permission("abandoned:read"))])
async def list_abandoned_events(
    camera_id: Optional[str] = Query(None),
    min_dwell_seconds: Optional[float] = Query(None),
    limit: int = Query(50, le=200),
    db=Depends(get_db_with_tenant),
):
    where_clauses = []
    params: dict = {"limit": limit}
    if camera_id:
        where_clauses.append("ae.camera_id = :camera_id")
        params["camera_id"] = camera_id
    if min_dwell_seconds is not None:
        where_clauses.append("ae.dwell_seconds >= :min_dwell")
        params["min_dwell"] = min_dwell_seconds

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    rows = (await db.execute(
        text(f"""
            SELECT ae.detection_id, ae.detected_at, ae.camera_id, ae.object_class,
                   ae.dwell_seconds, ae.bbox,
                   c.name AS camera_name
            FROM abandoned_object_events ae
            LEFT JOIN cameras c ON c.id = ae.camera_id
            {where_sql}
            ORDER BY ae.detected_at DESC
            LIMIT :limit
        """),
        params,
    )).fetchall()

    return [dict(r._mapping) for r in rows]


# ── Slip/fall events ──────────────────────────────────────────────────────────

@router.get("/falls", dependencies=[Depends(require_permission("fall:read"))])
async def list_fall_events(
    camera_id: Optional[str] = Query(None),
    min_confidence: Optional[float] = Query(None),
    limit: int = Query(50, le=200),
    db=Depends(get_db_with_tenant),
):
    where_clauses = []
    params: dict = {"limit": limit}
    if camera_id:
        where_clauses.append("fe.camera_id = :camera_id")
        params["camera_id"] = camera_id
    if min_confidence is not None:
        where_clauses.append("fe.fall_confidence >= :min_conf")
        params["min_conf"] = min_confidence

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    rows = (await db.execute(
        text(f"""
            SELECT fe.detection_id, fe.detected_at, fe.camera_id,
                   fe.fall_confidence, fe.person_bbox,
                   c.name AS camera_name
            FROM fall_events fe
            LEFT JOIN cameras c ON c.id = fe.camera_id
            {where_sql}
            ORDER BY fe.detected_at DESC
            LIMIT :limit
        """),
        params,
    )).fetchall()

    return [dict(r._mapping) for r in rows]
