"""Analytics API — aggregated metrics for the reporting dashboard.

All queries are read-only scoped by RLS (tenant isolation guaranteed).
Results are intentionally kept small (aggregates, not raw rows) so the
endpoints are always fast regardless of partition depth.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

_READ = Depends(require_permission("alert:read"))


@router.get("/summary", dependencies=[_READ])
async def get_summary(
    site_id: str | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Counts: open alerts, open incidents, active cameras, detections today."""
    if site_id:
        # Site-scoped variant — join through cameras for alert/incident/detection counts
        result = await db.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM alerts a JOIN cameras c ON c.id = a.camera_id
                 WHERE a.status = 'open' AND c.site_id = CAST(:site_id AS uuid))::int         AS open_alerts,
                (SELECT COUNT(*) FROM incidents i JOIN cameras c ON c.id = i.camera_id
                 WHERE i.status IN ('open','investigating') AND c.site_id = CAST(:site_id AS uuid))::int AS open_incidents,
                (SELECT COUNT(*) FROM cameras WHERE is_active = TRUE AND site_id = CAST(:site_id AS uuid))::int AS active_cameras,
                (SELECT COUNT(*) FROM detections d JOIN cameras c ON c.id = d.camera_id
                 WHERE d.detected_at >= CURRENT_DATE AND c.site_id = CAST(:site_id AS uuid))::int AS detections_today,
                (SELECT COUNT(*) FROM alerts a JOIN cameras c ON c.id = a.camera_id
                 WHERE a.created_at >= CURRENT_DATE AND c.site_id = CAST(:site_id AS uuid))::int AS alerts_today,
                (SELECT COUNT(*) FROM alerts a JOIN cameras c ON c.id = a.camera_id
                 WHERE a.created_at >= NOW() - INTERVAL '7 days' AND c.site_id = CAST(:site_id AS uuid))::int AS alerts_7d,
                (SELECT COUNT(*) FROM detections d JOIN cameras c ON c.id = d.camera_id
                 WHERE d.detected_at >= NOW() - INTERVAL '7 days' AND c.site_id = CAST(:site_id AS uuid))::int AS detections_7d,
                (SELECT COUNT(*) FROM recordings WHERE status = 'recording'
                 AND camera_id IN (SELECT id FROM cameras WHERE site_id = CAST(:site_id AS uuid)))::int AS active_recordings
        """), {"site_id": site_id})
    else:
        result = await db.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM alerts WHERE status = 'open')::int                   AS open_alerts,
                (SELECT COUNT(*) FROM incidents WHERE status IN ('open','investigating'))::int AS open_incidents,
                (SELECT COUNT(*) FROM cameras WHERE is_active = TRUE)::int                  AS active_cameras,
                (SELECT COUNT(*) FROM detections WHERE detected_at >= CURRENT_DATE)::int    AS detections_today,
                (SELECT COUNT(*) FROM alerts WHERE created_at >= CURRENT_DATE)::int         AS alerts_today,
                (SELECT COUNT(*) FROM alerts WHERE created_at >= NOW() - INTERVAL '7 days')::int AS alerts_7d,
                (SELECT COUNT(*) FROM detections WHERE detected_at >= NOW() - INTERVAL '7 days')::int AS detections_7d,
                (SELECT COUNT(*) FROM recordings WHERE status = 'recording')::int           AS active_recordings
        """))
    return dict(result.mappings().first())


@router.get("/alerts/by-severity", dependencies=[_READ])
async def alerts_by_severity(
    db: AsyncSession = Depends(get_db_with_tenant),
    days: int = 30,
):
    """Alert counts grouped by severity for the last N days."""
    result = await db.execute(text("""
        SELECT severity, COUNT(*)::int AS count
        FROM alerts
        WHERE created_at >= NOW() - :days * INTERVAL '1 day'
        GROUP BY severity
        ORDER BY count DESC
    """), {"days": min(days, 365)})
    return [dict(r._mapping) for r in result]


@router.get("/alerts/by-module", dependencies=[_READ])
async def alerts_by_module(
    db: AsyncSession = Depends(get_db_with_tenant),
    days: int = 30,
):
    """Alert counts grouped by module_type for the last N days."""
    result = await db.execute(text("""
        SELECT module_type, COUNT(*)::int AS count
        FROM alerts
        WHERE created_at >= NOW() - :days * INTERVAL '1 day'
        GROUP BY module_type
        ORDER BY count DESC
    """), {"days": min(days, 365)})
    return [dict(r._mapping) for r in result]


@router.get("/detections/trend", dependencies=[_READ])
async def detections_trend(
    db: AsyncSession = Depends(get_db_with_tenant),
    days: int = 7,
):
    """Daily detection counts for the last N days, all modules combined."""
    result = await db.execute(text("""
        SELECT
            DATE(detected_at) AS day,
            COUNT(*)::int     AS count
        FROM detections
        WHERE detected_at >= CURRENT_DATE - :days_minus_1 * INTERVAL '1 day'
        GROUP BY DATE(detected_at)
        ORDER BY day ASC
    """), {"days_minus_1": min(days, 90) - 1})
    return [dict(r._mapping) for r in result]


@router.get("/alerts/trend", dependencies=[_READ])
async def alerts_trend(
    db: AsyncSession = Depends(get_db_with_tenant),
    days: int = 7,
):
    """Daily alert counts for the last N days."""
    result = await db.execute(text("""
        SELECT
            DATE(created_at) AS day,
            COUNT(*)::int    AS count
        FROM alerts
        WHERE created_at >= CURRENT_DATE - :days_minus_1 * INTERVAL '1 day'
        GROUP BY DATE(created_at)
        ORDER BY day ASC
    """), {"days_minus_1": min(days, 90) - 1})
    return [dict(r._mapping) for r in result]


@router.get("/top-cameras", dependencies=[_READ])
async def top_cameras_by_alerts(
    db: AsyncSession = Depends(get_db_with_tenant),
    days: int = 30,
    limit: int = 10,
):
    """Cameras ranked by alert count over the last N days."""
    result = await db.execute(text("""
        SELECT
            a.camera_id,
            c.name     AS camera_name,
            COUNT(*)::int AS alert_count
        FROM alerts a
        LEFT JOIN cameras c ON c.id = a.camera_id
        WHERE a.created_at >= NOW() - :days * INTERVAL '1 day'
        GROUP BY a.camera_id, c.name
        ORDER BY alert_count DESC
        LIMIT :limit
    """), {"days": min(days, 365), "limit": min(limit, 50)})
    return [dict(r._mapping) for r in result]


@router.get("/heatmap", dependencies=[_READ])
async def get_heatmap_data(
    hours: int = Query(24, ge=1, le=720),
    module_type: Optional[str] = Query(None),
    site_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Per-camera activity metrics for the spatial heatmap visualisation.
    Returns cameras with their location coordinates + detection/alert counts
    for the requested time window."""
    params: dict = {"hours_val": hours}
    filters = []
    if module_type:
        filters.append("AND d.module_type = :module_type")
        params["module_type"] = module_type
    if site_id:
        filters.append("AND c.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id

    module_filter = " ".join(filters)

    result = await db.execute(text(f"""
        SELECT
            c.id                                                            AS camera_id,
            c.name                                                          AS camera_name,
            c.location,
            c.latitude,
            c.longitude,
            c.site_id,
            s.name                                                          AS site_name,
            COALESCE(
                (SELECT status FROM streams WHERE camera_id = c.id
                 ORDER BY updated_at DESC LIMIT 1), 'offline')              AS stream_status,
            COALESCE(d_agg.total_detections, 0)::int                        AS total_detections,
            COALESCE(a_agg.total_alerts, 0)::int                            AS total_alerts,
            COALESCE(a_agg.open_alerts, 0)::int                             AS open_alerts,
            COALESCE(a_agg.critical_alerts, 0)::int                         AS critical_alerts,
            COALESCE(a_agg.high_alerts, 0)::int                             AS high_alerts
        FROM cameras c
        LEFT JOIN sites s ON s.id = c.site_id
        LEFT JOIN LATERAL (
            SELECT COUNT(*)::int AS total_detections
            FROM detections d
            WHERE d.camera_id = c.id
              AND d.detected_at >= NOW() - make_interval(hours => :hours_val)
              {module_filter}
        ) d_agg ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*)::int                                           AS total_alerts,
                COUNT(*) FILTER (WHERE status = 'open')::int           AS open_alerts,
                COUNT(*) FILTER (WHERE severity = 'critical')::int     AS critical_alerts,
                COUNT(*) FILTER (WHERE severity = 'high')::int         AS high_alerts
            FROM alerts a
            WHERE a.camera_id = c.id
              AND a.created_at >= NOW() - make_interval(hours => :hours_val)
        ) a_agg ON TRUE
        WHERE c.is_active = TRUE
        ORDER BY total_detections DESC
    """), params)

    return [dict(r._mapping) for r in result]


@router.get("/incidents/resolution-time", dependencies=[_READ])
async def incident_resolution_time(
    db: AsyncSession = Depends(get_db_with_tenant),
    days: int = 30,
):
    """Average and p95 incident resolution time (hours) for closed incidents."""
    result = await db.execute(text("""
        SELECT
            COUNT(*)::int                                                       AS resolved_count,
            ROUND(AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 3600)::numeric, 2)
                                                                                AS avg_hours,
            ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (resolved_at - created_at)) / 3600
            )::numeric, 2)                                                      AS p95_hours
        FROM incidents
        WHERE status IN ('resolved','closed')
          AND resolved_at IS NOT NULL
          AND created_at >= NOW() - :days * INTERVAL '1 day'
    """), {"days": min(days, 365)})
    return dict(result.mappings().first())
