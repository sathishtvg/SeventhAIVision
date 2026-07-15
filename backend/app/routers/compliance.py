"""Guard Tour Compliance Reports — schedules, occurrences, and compliance analytics."""

import uuid as _uuid
from datetime import date, datetime, time, timedelta, timezone

import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ScheduleCreate(BaseModel):
    route_id: str
    name: str
    description: str | None = None
    recurrence: str = "daily"  # daily | weekdays | weekends | custom
    days_of_week: list[int] | None = None  # 0=Mon..6=Sun; None = every day
    scheduled_time: str  # "HH:MM" UTC
    window_minutes: int = 30
    assigned_guard_user_id: str | None = None


class ScheduleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    recurrence: str | None = None
    days_of_week: list[int] | None = None
    scheduled_time: str | None = None
    window_minutes: int | None = None
    assigned_guard_user_id: str | None = None
    is_active: bool | None = None


class OccurrenceResolve(BaseModel):
    session_id: str | None = None
    status: str  # completed | missed | incomplete | late
    compliance_score: float | None = None
    notes: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _recurrence_matches_day(recurrence: str, days_of_week: list | None, weekday: int) -> bool:
    """weekday: 0=Mon..6=Sun (Python convention)"""
    if recurrence == "daily":
        return True
    if recurrence == "weekdays":
        return weekday < 5
    if recurrence == "weekends":
        return weekday >= 5
    if recurrence == "custom" and days_of_week:
        return weekday in days_of_week
    return True


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/dashboard", dependencies=[Depends(require_permission("compliance:read"))])
async def get_compliance_dashboard(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'pending'
                AND scheduled_at >= now() - INTERVAL '24 hours'
                AND scheduled_at <= now() + INTERVAL '24 hours')       AS tours_today,
            COUNT(*) FILTER (WHERE status = 'completed'
                AND scheduled_at >= CURRENT_DATE)                       AS completed_today,
            COUNT(*) FILTER (WHERE status = 'missed'
                AND scheduled_at >= CURRENT_DATE)                       AS missed_today,
            COUNT(*) FILTER (WHERE status = 'late'
                AND scheduled_at >= CURRENT_DATE)                       AS late_today,
            COUNT(*) FILTER (WHERE status = 'incomplete'
                AND scheduled_at >= CURRENT_DATE)                       AS incomplete_today,
            -- 7-day compliance rate
            COUNT(*) FILTER (WHERE status != 'pending'
                AND scheduled_at >= now() - INTERVAL '7 days')         AS total_7d,
            COUNT(*) FILTER (WHERE status = 'completed'
                AND scheduled_at >= now() - INTERVAL '7 days')         AS completed_7d,
            -- 30-day
            COUNT(*) FILTER (WHERE status != 'pending'
                AND scheduled_at >= now() - INTERVAL '30 days')        AS total_30d,
            COUNT(*) FILTER (WHERE status = 'completed'
                AND scheduled_at >= now() - INTERVAL '30 days')        AS completed_30d,
            ROUND(AVG(compliance_score) FILTER (
                WHERE compliance_score IS NOT NULL
                AND scheduled_at >= now() - INTERVAL '7 days'), 1)     AS avg_score_7d,
            COUNT(DISTINCT schedule_id) FILTER (WHERE is_active IS NOT FALSE) AS active_schedules
        FROM tour_occurrences
        LEFT JOIN tour_schedules ON tour_schedules.id = tour_occurrences.schedule_id
    """))
    row = dict(result.first()._mapping)

    total_7d = row["total_7d"] or 0
    completed_7d = row["completed_7d"] or 0
    total_30d = row["total_30d"] or 0
    completed_30d = row["completed_30d"] or 0

    row["compliance_rate_7d"] = round(completed_7d / total_7d * 100, 1) if total_7d else None
    row["compliance_rate_30d"] = round(completed_30d / total_30d * 100, 1) if total_30d else None

    # Active schedules count
    sched = await db.execute(text(
        "SELECT COUNT(*) AS cnt FROM tour_schedules WHERE is_active = TRUE"
    ))
    row["active_schedules"] = sched.scalar()

    # Last 7 days daily breakdown
    daily_result = await db.execute(text("""
        SELECT
            DATE(scheduled_at) AS day,
            COUNT(*) FILTER (WHERE status != 'pending')          AS total,
            COUNT(*) FILTER (WHERE status = 'completed')         AS completed,
            COUNT(*) FILTER (WHERE status = 'missed')            AS missed,
            COUNT(*) FILTER (WHERE status IN ('late','incomplete')) AS partial
        FROM tour_occurrences
        WHERE scheduled_at >= now() - INTERVAL '7 days'
        GROUP BY DATE(scheduled_at)
        ORDER BY day ASC
    """))
    row["daily_trend"] = [dict(r._mapping) for r in daily_result]

    # Recent missed tours
    missed_result = await db.execute(text("""
        SELECT o.id, o.scheduled_at, o.status,
               ts.name AS schedule_name,
               pr.name AS route_name,
               u.full_name AS assigned_guard
        FROM tour_occurrences o
        JOIN tour_schedules ts ON ts.id = o.schedule_id
        JOIN patrol_routes pr ON pr.id = ts.route_id
        LEFT JOIN users u ON u.id = ts.assigned_guard_user_id
        WHERE o.status = 'missed'
        ORDER BY o.scheduled_at DESC
        LIMIT 10
    """))
    row["recent_missed"] = [dict(r._mapping) for r in missed_result]

    return row


# ── Tour Schedules ────────────────────────────────────────────────────────────

@router.get("/schedules", dependencies=[Depends(require_permission("compliance:read"))])
async def list_schedules(
    is_active: bool | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    where = ""
    params: dict = {}
    if is_active is not None:
        where = "WHERE ts.is_active = :active"
        params["active"] = is_active

    result = await db.execute(text(f"""
        SELECT ts.id, ts.route_id, ts.name, ts.description,
               ts.recurrence, ts.days_of_week, ts.scheduled_time,
               ts.window_minutes, ts.assigned_guard_user_id,
               ts.is_active, ts.created_at,
               pr.name AS route_name,
               s.name  AS site_name,
               u.full_name AS assigned_guard_name,
               -- compliance stats (last 30 days)
               COUNT(o.id) FILTER (WHERE o.scheduled_at >= now() - INTERVAL '30 days'
                   AND o.status != 'pending')                       AS total_30d,
               COUNT(o.id) FILTER (WHERE o.scheduled_at >= now() - INTERVAL '30 days'
                   AND o.status = 'completed')                      AS completed_30d,
               ROUND(AVG(o.compliance_score) FILTER (
                   WHERE o.compliance_score IS NOT NULL
                   AND o.scheduled_at >= now() - INTERVAL '30 days'), 1) AS avg_score_30d
        FROM tour_schedules ts
        JOIN patrol_routes pr ON pr.id = ts.route_id
        LEFT JOIN sites s ON s.id = pr.site_id
        LEFT JOIN users u ON u.id = ts.assigned_guard_user_id
        LEFT JOIN tour_occurrences o ON o.schedule_id = ts.id
        {where}
        GROUP BY ts.id, pr.name, s.name, u.full_name
        ORDER BY ts.name
    """), params)
    rows = [dict(r._mapping) for r in result]
    for row in rows:
        t = row.get("total_30d") or 0
        c = row.get("completed_30d") or 0
        row["compliance_rate_30d"] = round(c / t * 100, 1) if t else None
    return rows


@router.post("/schedules", dependencies=[Depends(require_permission("compliance:manage"))])
async def create_schedule(
    body: ScheduleCreate,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if body.recurrence not in ("daily", "weekdays", "weekends", "custom"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "recurrence must be daily|weekdays|weekends|custom")
    if body.recurrence == "custom" and not body.days_of_week:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "days_of_week required for custom recurrence")

    # Validate route belongs to tenant (RLS handles isolation)
    route_chk = await db.execute(
        text("SELECT id FROM patrol_routes WHERE id = CAST(:rid AS uuid) AND is_active = TRUE"),
        {"rid": body.route_id},
    )
    if not route_chk.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patrol route not found")

    dow = list(body.days_of_week) if body.days_of_week else None
    sched_time = time.fromisoformat(body.scheduled_time) if body.scheduled_time else None
    result = await db.execute(text("""
        INSERT INTO tour_schedules (
            tenant_id, route_id, name, description,
            recurrence, days_of_week, scheduled_time,
            window_minutes, assigned_guard_user_id, created_by_user_id
        ) VALUES (
            current_setting('app.current_tenant')::uuid,
            CAST(:route_id AS uuid), :name, :description,
            :recurrence, :dow, :scheduled_time,
            :window_minutes,
            CAST(:guard_id AS uuid), CAST(:creator_id AS uuid)
        )
        RETURNING id, route_id, name, description, recurrence, days_of_week,
                  scheduled_time, window_minutes, assigned_guard_user_id,
                  is_active, created_at
    """), {
        "route_id": body.route_id,
        "name": body.name,
        "description": body.description,
        "recurrence": body.recurrence,
        "dow": dow,
        "scheduled_time": sched_time,
        "window_minutes": body.window_minutes,
        "guard_id": body.assigned_guard_user_id,
        "creator_id": str(token.user_id),
    })
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@router.get("/schedules/{schedule_id}", dependencies=[Depends(require_permission("compliance:read"))])
async def get_schedule(schedule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(text("""
        SELECT ts.*, pr.name AS route_name, s.name AS site_name,
               u.full_name AS assigned_guard_name
        FROM tour_schedules ts
        JOIN patrol_routes pr ON pr.id = ts.route_id
        LEFT JOIN sites s ON s.id = pr.site_id
        LEFT JOIN users u ON u.id = ts.assigned_guard_user_id
        WHERE ts.id = CAST(:id AS uuid)
    """), {"id": schedule_id})
    row = result.first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    return dict(row._mapping)


@router.put("/schedules/{schedule_id}", dependencies=[Depends(require_permission("compliance:manage"))])
async def update_schedule(
    schedule_id: str,
    body: ScheduleUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    chk = await db.execute(
        text("SELECT id FROM tour_schedules WHERE id = CAST(:id AS uuid)"),
        {"id": schedule_id},
    )
    if not chk.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")

    sets, params = [], {"id": schedule_id}
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.description is not None:
        sets.append("description = :desc"); params["desc"] = body.description
    if body.recurrence is not None:
        sets.append("recurrence = :rec"); params["rec"] = body.recurrence
    if body.days_of_week is not None:
        sets.append("days_of_week = :dow"); params["dow"] = list(body.days_of_week)
    if body.scheduled_time is not None:
        sets.append("scheduled_time = :stime"); params["stime"] = time.fromisoformat(body.scheduled_time)
    if body.window_minutes is not None:
        sets.append("window_minutes = :wm"); params["wm"] = body.window_minutes
    if body.assigned_guard_user_id is not None:
        sets.append("assigned_guard_user_id = CAST(:guard AS uuid)")
        params["guard"] = body.assigned_guard_user_id
    if body.is_active is not None:
        sets.append("is_active = :active"); params["active"] = body.is_active

    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")

    sets.append("updated_at = now()")
    result = await db.execute(
        text(f"UPDATE tour_schedules SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) RETURNING *"),
        params,
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


# ── Generate Occurrences ──────────────────────────────────────────────────────

@router.post("/schedules/{schedule_id}/generate",
             dependencies=[Depends(require_permission("compliance:manage"))])
async def generate_occurrences(
    schedule_id: str,
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Generate occurrence records for the next N days (skips existing ones)."""
    result = await db.execute(
        text("SELECT * FROM tour_schedules WHERE id = CAST(:id AS uuid) AND is_active = TRUE"),
        {"id": schedule_id},
    )
    sched = result.first()
    if not sched:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active schedule not found")

    s = dict(sched._mapping)
    recurrence = s["recurrence"]
    days_of_week = s["days_of_week"] or []
    window_minutes = s["window_minutes"]

    # Parse scheduled_time (HH:MM:SS from DB)
    t_str = str(s["scheduled_time"])
    h, m = int(t_str[:2]), int(t_str[3:5])

    created, skipped = 0, 0
    today = datetime.now(timezone.utc).date()

    for delta in range(days):
        day = today + timedelta(days=delta)
        weekday = day.weekday()  # 0=Mon..6=Sun

        if not _recurrence_matches_day(recurrence, days_of_week, weekday):
            continue

        scheduled_at = datetime(day.year, day.month, day.day, h, m, 0, tzinfo=timezone.utc)
        window_end = scheduled_at + timedelta(minutes=window_minutes)

        try:
            await db.execute(text("""
                INSERT INTO tour_occurrences
                    (tenant_id, schedule_id, scheduled_at, window_end)
                VALUES (
                    current_setting('app.current_tenant')::uuid,
                    CAST(:schedule_id AS uuid), :scheduled_at, :window_end
                )
                ON CONFLICT (schedule_id, scheduled_at) DO NOTHING
            """), {
                "schedule_id": schedule_id,
                "scheduled_at": scheduled_at,
                "window_end": window_end,
            })
            created += 1
        except Exception:
            skipped += 1

    await db.commit()
    return {"generated": created, "skipped_existing": skipped, "days": days}


# ── Tour Occurrences ──────────────────────────────────────────────────────────

@router.get("/occurrences", dependencies=[Depends(require_permission("compliance:read"))])
async def list_occurrences(
    date_from: date | None = None,
    date_to: date | None = None,
    schedule_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    guard_id: str | None = None,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    wheres = []
    params: dict = {"limit": limit}

    if date_from:
        wheres.append("o.scheduled_at >= :date_from")
        params["date_from"] = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    if date_to:
        wheres.append("o.scheduled_at < :date_to + INTERVAL '1 day'")
        params["date_to"] = datetime(date_to.year, date_to.month, date_to.day, tzinfo=timezone.utc)
    if schedule_id:
        wheres.append("o.schedule_id = CAST(:schedule_id AS uuid)")
        params["schedule_id"] = schedule_id
    if status_filter:
        wheres.append("o.status = :status_filter")
        params["status_filter"] = status_filter
    if guard_id:
        wheres.append("ts.assigned_guard_user_id = CAST(:guard_id AS uuid)")
        params["guard_id"] = guard_id

    where_clause = ("WHERE " + " AND ".join(wheres)) if wheres else ""

    result = await db.execute(text(f"""
        SELECT o.id, o.schedule_id, o.session_id, o.scheduled_at, o.window_end,
               o.status, o.compliance_score, o.missed_checkpoints, o.notes,
               o.created_at, o.updated_at,
               ts.name AS schedule_name,
               ts.assigned_guard_user_id,
               u.full_name AS assigned_guard_name,
               pr.name AS route_name,
               s.name  AS site_name,
               -- session details if linked
               ps.started_at AS session_started_at,
               ps.scanned_checkpoints,
               ps.total_checkpoints,
               ps.status AS session_status
        FROM tour_occurrences o
        JOIN tour_schedules ts ON ts.id = o.schedule_id
        JOIN patrol_routes pr ON pr.id = ts.route_id
        LEFT JOIN sites s ON s.id = pr.site_id
        LEFT JOIN users u ON u.id = ts.assigned_guard_user_id
        LEFT JOIN patrol_sessions ps ON ps.id = o.session_id
        {where_clause}
        ORDER BY o.scheduled_at DESC
        LIMIT :limit
    """), params)
    return [dict(r._mapping) for r in result]


@router.put("/occurrences/{occurrence_id}/resolve",
            dependencies=[Depends(require_permission("compliance:manage"))])
async def resolve_occurrence(
    request: Request,
    occurrence_id: str,
    body: OccurrenceResolve,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    valid_statuses = {"completed", "missed", "incomplete", "late"}
    if body.status not in valid_statuses:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"status must be one of: {', '.join(valid_statuses)}")

    chk = await db.execute(
        text("SELECT id FROM tour_occurrences WHERE id = CAST(:id AS uuid)"),
        {"id": occurrence_id},
    )
    if not chk.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occurrence not found")

    # If linking a session, compute compliance score from it
    score = body.compliance_score
    missed = None
    if body.session_id:
        sess = await db.execute(text("""
            SELECT scanned_checkpoints, total_checkpoints
            FROM patrol_sessions WHERE id = CAST(:sid AS uuid)
        """), {"sid": body.session_id})
        sess_row = sess.first()
        if sess_row and sess_row.total_checkpoints:
            sc = sess_row.scanned_checkpoints or 0
            tc = sess_row.total_checkpoints
            score = round(sc / tc * 100, 1)
            missed = tc - sc

    await db.execute(text("""
        UPDATE tour_occurrences
        SET session_id = CAST(:session_id AS uuid),
            status = :status,
            compliance_score = :score,
            missed_checkpoints = :missed,
            notes = :notes,
            updated_at = now()
        WHERE id = CAST(:id AS uuid)
    """), {
        "id": occurrence_id,
        "session_id": body.session_id,
        "status": body.status,
        "score": score,
        "missed": missed,
        "notes": body.notes,
    })
    await db.commit()
    redis = getattr(request.app.state, "redis", None)
    if redis:
        await redis.publish(f"tenant_events:{token.tenant_id}", json.dumps({
            "event_type": "tour_occurrence_resolved",
            "tenant_id": token.tenant_id,
            "payload": {
                "occurrence_id": occurrence_id,
                "status": body.status,
                "compliance_score": score,
            },
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }))
    return {"id": occurrence_id, "status": body.status, "compliance_score": score}


# ── Compliance Report ─────────────────────────────────────────────────────────

@router.get("/report", dependencies=[Depends(require_permission("compliance:read"))])
async def get_compliance_report(
    date_from: date,
    date_to: date,
    guard_id: str | None = None,
    site_id: str | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    params: dict = {
        "from": datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc),
        "to": datetime(date_to.year, date_to.month, date_to.day, tzinfo=timezone.utc) + timedelta(days=1),
    }
    extra_where = ""
    if guard_id:
        extra_where += " AND ts.assigned_guard_user_id = CAST(:guard_id AS uuid)"
        params["guard_id"] = guard_id
    if site_id:
        extra_where += " AND pr.site_id = CAST(:site_id AS uuid)"
        params["site_id"] = site_id

    # Overall summary
    summary_result = await db.execute(text(f"""
        SELECT
            COUNT(*) FILTER (WHERE o.status != 'pending')               AS total_resolved,
            COUNT(*) FILTER (WHERE o.status = 'completed')              AS completed,
            COUNT(*) FILTER (WHERE o.status = 'missed')                 AS missed,
            COUNT(*) FILTER (WHERE o.status = 'late')                   AS late,
            COUNT(*) FILTER (WHERE o.status = 'incomplete')             AS incomplete,
            COUNT(*) FILTER (WHERE o.status = 'pending')                AS pending,
            ROUND(AVG(o.compliance_score) FILTER (WHERE o.compliance_score IS NOT NULL), 1) AS avg_score
        FROM tour_occurrences o
        JOIN tour_schedules ts ON ts.id = o.schedule_id
        JOIN patrol_routes pr ON pr.id = ts.route_id
        WHERE o.scheduled_at >= :from AND o.scheduled_at < :to {extra_where}
    """), params)
    summary = dict(summary_result.first()._mapping)
    total = summary["total_resolved"] or 0
    summary["compliance_rate"] = round(
        (summary["completed"] or 0) / total * 100, 1
    ) if total else None

    # By guard breakdown
    guard_result = await db.execute(text(f"""
        SELECT
            ts.assigned_guard_user_id AS guard_id,
            u.full_name AS guard_name,
            COUNT(*) FILTER (WHERE o.status != 'pending')               AS total,
            COUNT(*) FILTER (WHERE o.status = 'completed')              AS completed,
            COUNT(*) FILTER (WHERE o.status = 'missed')                 AS missed,
            ROUND(AVG(o.compliance_score) FILTER (WHERE o.compliance_score IS NOT NULL), 1) AS avg_score
        FROM tour_occurrences o
        JOIN tour_schedules ts ON ts.id = o.schedule_id
        JOIN patrol_routes pr ON pr.id = ts.route_id
        LEFT JOIN users u ON u.id = ts.assigned_guard_user_id
        WHERE o.scheduled_at >= :from AND o.scheduled_at < :to {extra_where}
        GROUP BY ts.assigned_guard_user_id, u.full_name
        ORDER BY total DESC
    """), params)
    by_guard = []
    for r in guard_result:
        row = dict(r._mapping)
        t = row["total"] or 0
        c = row["completed"] or 0
        row["compliance_rate"] = round(c / t * 100, 1) if t else None
        by_guard.append(row)

    # By route breakdown
    route_result = await db.execute(text(f"""
        SELECT
            pr.id AS route_id, pr.name AS route_name,
            s.name AS site_name,
            COUNT(*) FILTER (WHERE o.status != 'pending')               AS total,
            COUNT(*) FILTER (WHERE o.status = 'completed')              AS completed,
            COUNT(*) FILTER (WHERE o.status = 'missed')                 AS missed,
            ROUND(AVG(o.compliance_score) FILTER (WHERE o.compliance_score IS NOT NULL), 1) AS avg_score
        FROM tour_occurrences o
        JOIN tour_schedules ts ON ts.id = o.schedule_id
        JOIN patrol_routes pr ON pr.id = ts.route_id
        LEFT JOIN sites s ON s.id = pr.site_id
        WHERE o.scheduled_at >= :from AND o.scheduled_at < :to {extra_where}
        GROUP BY pr.id, pr.name, s.name
        ORDER BY total DESC
    """), params)
    by_route = []
    for r in route_result:
        row = dict(r._mapping)
        t = row["total"] or 0
        c = row["completed"] or 0
        row["compliance_rate"] = round(c / t * 100, 1) if t else None
        by_route.append(row)

    # Daily trend
    daily_result = await db.execute(text(f"""
        SELECT
            DATE(o.scheduled_at) AS day,
            COUNT(*) FILTER (WHERE o.status != 'pending')               AS total,
            COUNT(*) FILTER (WHERE o.status = 'completed')              AS completed,
            COUNT(*) FILTER (WHERE o.status = 'missed')                 AS missed,
            ROUND(AVG(o.compliance_score) FILTER (WHERE o.compliance_score IS NOT NULL), 1) AS avg_score
        FROM tour_occurrences o
        JOIN tour_schedules ts ON ts.id = o.schedule_id
        JOIN patrol_routes pr ON pr.id = ts.route_id
        WHERE o.scheduled_at >= :from AND o.scheduled_at < :to {extra_where}
        GROUP BY DATE(o.scheduled_at)
        ORDER BY day
    """), params)
    daily_trend = []
    for r in daily_result:
        row = dict(r._mapping)
        t = row["total"] or 0
        c = row["completed"] or 0
        row["compliance_rate"] = round(c / t * 100, 1) if t else None
        daily_trend.append(row)

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "summary": summary,
        "by_guard": by_guard,
        "by_route": by_route,
        "daily_trend": daily_trend,
    }
