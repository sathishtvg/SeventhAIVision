from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import paginate
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/detections", tags=["detections"])


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
    data_sql = f"SELECT id, camera_id, module_type, confidence, bounding_box, detected_at FROM detections {where} ORDER BY detected_at DESC LIMIT :limit OFFSET :offset"
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
    data_sql = f"SELECT detection_id, camera_id, plate_number, plate_confidence, direction, vehicle_type, vehicle_color, watchlist_match, created_at FROM lpr_events {where} ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
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
    data_sql = f"SELECT detection_id, camera_id, matched_watchlist_id, match_confidence, watchlist_match, created_at FROM face_events {where} ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
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
    data_sql = f"SELECT detection_id, camera_id, zone_id, person_bbox, dwell_time_seconds, created_at FROM intrusion_events {where} ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
    count_sql = f"SELECT COUNT(*) FROM intrusion_events {where}"
    return await paginate(db, data_sql, count_sql, params, limit, offset)


# ---------------------------------------------------------------------------
# Phase 3 AI module endpoints
# ---------------------------------------------------------------------------

@router.get("/ppe-events", dependencies=[Depends(require_permission("detection:read"))])
async def list_ppe_events(db: AsyncSession = Depends(get_db_with_tenant), limit: int = 50):
    result = await db.execute(
        text(
            "SELECT detection_id, camera_id, person_bbox, items_detected, items_missing, severity, created_at "
            "FROM ppe_events ORDER BY created_at DESC LIMIT :limit"
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
                "SELECT detection_id, camera_id, zone_id, person_count, max_capacity, density_ratio, created_at "
                "FROM crowd_events WHERE zone_id = :zone_id ORDER BY created_at DESC LIMIT :limit"
            ),
            {"zone_id": zone_id, "limit": min(limit, 200)},
        )
    else:
        result = await db.execute(
            text(
                "SELECT detection_id, camera_id, zone_id, person_count, max_capacity, density_ratio, created_at "
                "FROM crowd_events ORDER BY created_at DESC LIMIT :limit"
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
                "SELECT detection_id, camera_id, detection_type, confidence, bbox, created_at "
                "FROM fire_smoke_events WHERE detection_type = :detection_type "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            {"detection_type": detection_type, "limit": min(limit, 200)},
        )
    else:
        result = await db.execute(
            text(
                "SELECT detection_id, camera_id, detection_type, confidence, bbox, created_at "
                "FROM fire_smoke_events ORDER BY created_at DESC LIMIT :limit"
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
                "SELECT detection_id, camera_id, weapon_type, confidence, bbox, created_at "
                "FROM weapon_events WHERE weapon_type = :weapon_type "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            {"weapon_type": weapon_type, "limit": min(limit, 200)},
        )
    else:
        result = await db.execute(
            text(
                "SELECT detection_id, camera_id, weapon_type, confidence, bbox, created_at "
                "FROM weapon_events ORDER BY created_at DESC LIMIT :limit"
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
                "SELECT detection_id, camera_id, zone_id, behavior_type, confidence, duration_seconds, created_at "
                "FROM behavior_events WHERE behavior_type = :behavior_type "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            {"behavior_type": behavior_type, "limit": min(limit, 200)},
        )
    else:
        result = await db.execute(
            text(
                "SELECT detection_id, camera_id, zone_id, behavior_type, confidence, duration_seconds, created_at "
                "FROM behavior_events ORDER BY created_at DESC LIMIT :limit"
            ),
            {"limit": min(limit, 200)},
        )
    return [dict(row._mapping) for row in result]
