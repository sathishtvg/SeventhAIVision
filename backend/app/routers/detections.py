from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import paginate
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/detections", tags=["detections"])

# ---------------------------------------------------------------------------
# Detection screenshots
# ---------------------------------------------------------------------------
#
# Every AI worker already saves an evidence snapshot for every detection it
# writes — all eleven modules call save_evidence_snapshot. None of these
# endpoints returned a pointer to it, so the images accumulated on disk and in
# the evidence table with no way for any UI to display them. This adds that
# pointer.
#
# PARTITION PRUNING IS LOAD-BEARING, NOT AN OPTIMISATION.
#     `evidence` is RANGE-partitioned monthly on captured_at. Correlating on
#     detection_id alone gives Postgres no partition key, so it scans EVERY
#     partition for every row returned — that degrades without bound as
#     history accumulates. The captured_at window restricts it to the one or
#     two partitions that can possibly contain the row.
#
#     The window is +/- 1 hour rather than exact equality. Both timestamps
#     derive from the same job.captured_at today, so equality would work now —
#     but it would silently return NULL (a detection that appears to have no
#     screenshot) the first time any module writes now() instead. A bounded
#     window prunes just as well and degrades to "still correct" rather than
#     "silently missing evidence".
#
# array_agg(...)[1] rather than MAX(): Postgres has no MAX() for uuid.
def _evidence_lateral(alias: str, id_col: str = "detection_id",
                      time_col: str = "detected_at") -> str:
    return f"""
        LEFT JOIN LATERAL (
            SELECT
              (array_agg(e.id) FILTER (WHERE e.capture_kind = 'frame'))[1]      AS frame_evidence_id,
              (array_agg(e.id) FILTER (WHERE e.capture_kind = 'plate_crop'))[1] AS plate_evidence_id
            FROM evidence e
            WHERE e.detection_id = {alias}.{id_col}
              AND e.captured_at BETWEEN {alias}.{time_col} - INTERVAL '1 hour'
                                    AND {alias}.{time_col} + INTERVAL '1 hour'
        ) ev ON TRUE
    """


_EVIDENCE_COLS = "ev.frame_evidence_id, ev.plate_evidence_id"




@router.get("", dependencies=[Depends(require_permission("detection:read"))])
async def list_detections(
    db: AsyncSession = Depends(get_db_with_tenant),
    module_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    params: dict = {}
    where = ""
    if module_type:
        where = "WHERE module_type = :module_type"
        params["module_type"] = module_type
    data_sql = f"SELECT d.id, d.camera_id, d.module_type, d.confidence, d.bounding_box, d.detected_at, {_EVIDENCE_COLS} FROM detections d {_evidence_lateral('d', 'id', 'detected_at')} {where} ORDER BY detected_at DESC LIMIT :limit OFFSET :offset"
    count_sql = f"SELECT COUNT(*) FROM detections {where}"
    return await paginate(db, data_sql, count_sql, params, limit, offset)


@router.get("/lpr-events", dependencies=[Depends(require_permission("detection:read"))])
async def list_lpr_events(
    db: AsyncSession = Depends(get_db_with_tenant),
    plate_number: str | None = None,
    watchlist_match: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    where_clauses = []
    params: dict = {}
    if plate_number:
        where_clauses.append("plate_number ILIKE :plate_number")
        params["plate_number"] = f"%{plate_number}%"
    if watchlist_match:
        where_clauses.append("watchlist_match = :watchlist_match")
        params["watchlist_match"] = watchlist_match
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    data_sql = f"SELECT le.detection_id, le.camera_id, le.plate_number, le.plate_confidence, le.direction, le.vehicle_type, le.vehicle_color, le.watchlist_match, le.created_at, {_EVIDENCE_COLS} FROM lpr_events le {_evidence_lateral('le', 'detection_id', 'detected_at')} {where} ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
    count_sql = f"SELECT COUNT(*) FROM lpr_events {where}"
    return await paginate(db, data_sql, count_sql, params, limit, offset)


@router.get("/face-events", dependencies=[Depends(require_permission("detection:read"))])
async def list_face_events(
    db: AsyncSession = Depends(get_db_with_tenant),
    watchlist_match: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    params: dict = {}
    where = ""
    if watchlist_match:
        where = "WHERE watchlist_match = :watchlist_match"
        params["watchlist_match"] = watchlist_match
    data_sql = f"SELECT fe.detection_id, fe.camera_id, fe.matched_watchlist_id, fe.match_confidence, fe.watchlist_match, fe.created_at, {_EVIDENCE_COLS} FROM face_events fe {_evidence_lateral('fe', 'detection_id', 'detected_at')} {where} ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
    count_sql = f"SELECT COUNT(*) FROM face_events {where}"
    return await paginate(db, data_sql, count_sql, params, limit, offset)


@router.get("/intrusion-events", dependencies=[Depends(require_permission("detection:read"))])
async def list_intrusion_events(
    db: AsyncSession = Depends(get_db_with_tenant),
    zone_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    params: dict = {}
    where = ""
    if zone_id:
        where = "WHERE zone_id = CAST(:zone_id AS uuid)"
        params["zone_id"] = zone_id
    data_sql = f"SELECT ie.detection_id, ie.camera_id, ie.zone_id, ie.person_bbox, ie.dwell_time_seconds, ie.created_at, {_EVIDENCE_COLS} FROM intrusion_events ie {_evidence_lateral('ie', 'detection_id', 'detected_at')} {where} ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
    count_sql = f"SELECT COUNT(*) FROM intrusion_events {where}"
    return await paginate(db, data_sql, count_sql, params, limit, offset)


# ---------------------------------------------------------------------------
# Phase 3 AI module endpoints
# ---------------------------------------------------------------------------

@router.get("/ppe-events", dependencies=[Depends(require_permission("detection:read"))])
async def list_ppe_events(db: AsyncSession = Depends(get_db_with_tenant), limit: int = 50):
    result = await db.execute(
        text(
            f"SELECT pe.detection_id, pe.camera_id, pe.person_bbox, pe.items_detected, pe.items_missing, pe.severity, pe.created_at, {_EVIDENCE_COLS} "
            f"FROM ppe_events pe {_evidence_lateral('pe')} ORDER BY created_at DESC LIMIT :limit"
        ),
        {"limit": min(limit, 200)},
    )
    return [dict(row._mapping) for row in result]


@router.get("/crowd-events", dependencies=[Depends(require_permission("detection:read"))])
async def list_crowd_events(
    db: AsyncSession = Depends(get_db_with_tenant),
    zone_id: str | None = None,
    limit: int = 50,
):
    if zone_id:
        result = await db.execute(
            text(
                f"SELECT ce.detection_id, ce.camera_id, ce.zone_id, ce.person_count, ce.max_capacity, ce.density_ratio, ce.created_at, {_EVIDENCE_COLS} "
                f"FROM crowd_events ce {_evidence_lateral('ce')} WHERE zone_id = :zone_id ORDER BY created_at DESC LIMIT :limit"
            ),
            {"zone_id": zone_id, "limit": min(limit, 200)},
        )
    else:
        result = await db.execute(
            text(
                f"SELECT ce.detection_id, ce.camera_id, ce.zone_id, ce.person_count, ce.max_capacity, ce.density_ratio, ce.created_at, {_EVIDENCE_COLS} "
                f"FROM crowd_events ce {_evidence_lateral('ce')} ORDER BY created_at DESC LIMIT :limit"
            ),
            {"limit": min(limit, 200)},
        )
    return [dict(row._mapping) for row in result]


@router.get("/fire-smoke-events", dependencies=[Depends(require_permission("detection:read"))])
async def list_fire_smoke_events(
    db: AsyncSession = Depends(get_db_with_tenant),
    detection_type: str | None = None,
    limit: int = 50,
):
    if detection_type:
        result = await db.execute(
            text(
                f"SELECT fse.detection_id, fse.camera_id, fse.detection_type, fse.confidence, fse.bbox, fse.created_at, {_EVIDENCE_COLS} "
                f"FROM fire_smoke_events fse {_evidence_lateral('fse')} WHERE detection_type = :detection_type "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            {"detection_type": detection_type, "limit": min(limit, 200)},
        )
    else:
        result = await db.execute(
            text(
                f"SELECT fse.detection_id, fse.camera_id, fse.detection_type, fse.confidence, fse.bbox, fse.created_at, {_EVIDENCE_COLS} "
                f"FROM fire_smoke_events fse {_evidence_lateral('fse')} ORDER BY created_at DESC LIMIT :limit"
            ),
            {"limit": min(limit, 200)},
        )
    return [dict(row._mapping) for row in result]


@router.get("/weapon-events", dependencies=[Depends(require_permission("detection:read"))])
async def list_weapon_events(
    db: AsyncSession = Depends(get_db_with_tenant),
    weapon_type: str | None = None,
    limit: int = 50,
):
    if weapon_type:
        result = await db.execute(
            text(
                f"SELECT we.detection_id, we.camera_id, we.weapon_type, we.confidence, we.bbox, we.created_at, {_EVIDENCE_COLS} "
                f"FROM weapon_events we {_evidence_lateral('we')} WHERE weapon_type = :weapon_type "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            {"weapon_type": weapon_type, "limit": min(limit, 200)},
        )
    else:
        result = await db.execute(
            text(
                f"SELECT we.detection_id, we.camera_id, we.weapon_type, we.confidence, we.bbox, we.created_at, {_EVIDENCE_COLS} "
                f"FROM weapon_events we {_evidence_lateral('we')} ORDER BY created_at DESC LIMIT :limit"
            ),
            {"limit": min(limit, 200)},
        )
    return [dict(row._mapping) for row in result]


@router.get("/behavior-events", dependencies=[Depends(require_permission("detection:read"))])
async def list_behavior_events(
    db: AsyncSession = Depends(get_db_with_tenant),
    behavior_type: str | None = None,
    limit: int = 50,
):
    if behavior_type:
        result = await db.execute(
            text(
                f"SELECT be.detection_id, be.camera_id, be.zone_id, be.behavior_type, be.confidence, be.duration_seconds, be.created_at, {_EVIDENCE_COLS} "
                f"FROM behavior_events be {_evidence_lateral('be')} WHERE behavior_type = :behavior_type "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            {"behavior_type": behavior_type, "limit": min(limit, 200)},
        )
    else:
        result = await db.execute(
            text(
                f"SELECT be.detection_id, be.camera_id, be.zone_id, be.behavior_type, be.confidence, be.duration_seconds, be.created_at, {_EVIDENCE_COLS} "
                f"FROM behavior_events be {_evidence_lateral('be')} ORDER BY created_at DESC LIMIT :limit"
            ),
            {"limit": min(limit, 200)},
        )
    return [dict(row._mapping) for row in result]
