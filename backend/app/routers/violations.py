"""Violations — auto-detected and manually-logged guard conduct/attendance
issues, with a points-based per-guard summary (ShiftSecure Phase 3).

Auto-detection (late check-in, geofence failure, early departure, no-show)
happens in shifts.py / scheduler_main.py via services/violations.py's
shared create_violation() helper; this router owns manual entry + the
review (acknowledge/dispute/waive) workflow + the summary view.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.violations import create_violation

router = APIRouter(prefix="/api/v1/violations", tags=["guard-ops"])

_GUARD_ROLES = {4, 5}
_VIOLATION_TYPES = {"no_show", "late_checkin", "geofence_failure", "early_departure", "manual"}
_REVIEW_STATUSES = {"acknowledged", "disputed", "waived"}


async def _publish_violation_event(
    request: Request, tenant_id: str, violation_id: str, violation_type: str, guard_user_id: str,
) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return
    try:
        event = json.dumps({
            "event_type": "violation_created",
            "tenant_id": tenant_id,
            "payload": {"violation_id": violation_id, "violation_type": violation_type, "guard_user_id": guard_user_id},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        })
        await redis.publish(f"tenant_events:{tenant_id}", event)
    except Exception:
        pass


@router.get("/", dependencies=[Depends(require_permission("violation:read"))])
async def list_violations(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    guard_user_id: str | None = None,
    site_id: str | None = None,
    violation_type: str | None = None,
    violation_status: str | None = None,
):
    where = []
    params: dict = {}
    if token.role_id in _GUARD_ROLES:
        where.append("v.guard_user_id = :uid")
        params["uid"] = token.user_id
    elif guard_user_id:
        where.append("v.guard_user_id = CAST(:gid AS uuid)")
        params["gid"] = guard_user_id
    if site_id:
        where.append("v.site_id = CAST(:sid AS uuid)")
        params["sid"] = site_id
    if violation_type:
        where.append("v.violation_type = :vtype")
        params["vtype"] = violation_type
    if violation_status:
        where.append("v.status = :vstatus")
        params["vstatus"] = violation_status
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(
        text(f"""
            SELECT v.id, v.guard_user_id, v.shift_id, v.site_id, v.violation_type, v.description,
                   v.points, v.status, v.is_auto_generated, v.reported_by_user_id,
                   v.reviewed_by_user_id, v.reviewed_at, v.review_notes, v.occurred_at, v.created_at,
                   u.full_name AS guard_name, s.name AS site_name, r.full_name AS reported_by_name
            FROM violations v
            JOIN users u ON u.id = v.guard_user_id
            LEFT JOIN sites s ON s.id = v.site_id
            LEFT JOIN users r ON r.id = v.reported_by_user_id
            {where_clause}
            ORDER BY v.occurred_at DESC
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


class ViolationCreate(BaseModel):
    guard_user_id: str
    violation_type: str = "manual"
    description: str | None = None
    points: int | None = None
    shift_id: str | None = None
    site_id: str | None = None


@router.post("/", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("violation:manage"))])
async def create_manual_violation(
    body: ViolationCreate,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if body.violation_type not in _VIOLATION_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown violation_type: {body.violation_type}")

    violation_id = await create_violation(
        db, token.tenant_id, body.guard_user_id, body.violation_type,
        shift_id=body.shift_id, site_id=body.site_id, description=body.description,
        points=body.points, is_auto_generated=False, reported_by_user_id=token.user_id,
    )
    await db.commit()
    await _publish_violation_event(request, token.tenant_id, violation_id, body.violation_type, body.guard_user_id)
    return {"id": violation_id, "status": "open"}


class ViolationReview(BaseModel):
    review_status: str
    review_notes: str | None = None


@router.put("/{violation_id}/review", dependencies=[Depends(require_permission("violation:manage"))])
async def review_violation(
    violation_id: str,
    body: ViolationReview,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if body.review_status not in _REVIEW_STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"review_status must be one of {sorted(_REVIEW_STATUSES)}")

    row = (await db.execute(
        text("""
            UPDATE violations
            SET status = :rstatus, reviewed_by_user_id = :rid, reviewed_at = now(), review_notes = :notes
            WHERE id = :id
            RETURNING id, status
        """),
        {"rstatus": body.review_status, "rid": token.user_id, "notes": body.review_notes, "id": violation_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Violation not found")
    await db.commit()
    return dict(row._mapping)


@router.get("/summary", dependencies=[Depends(require_permission("violation:read"))])
async def get_violations_summary(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    days: int = 90,
):
    guard_clause = ""
    params: dict = {"days": days}
    if token.role_id in _GUARD_ROLES:
        guard_clause = "AND v.guard_user_id = :uid"
        params["uid"] = token.user_id

    result = await db.execute(
        text(f"""
            SELECT v.guard_user_id, u.full_name AS guard_name,
                   SUM(v.points) FILTER (WHERE v.status != 'waived') AS total_points,
                   COUNT(*) AS violation_count,
                   MAX(v.occurred_at) AS last_violation_at
            FROM violations v
            JOIN users u ON u.id = v.guard_user_id
            WHERE v.occurred_at >= now() - make_interval(days => :days)
            {guard_clause}
            GROUP BY v.guard_user_id, u.full_name
            ORDER BY total_points DESC NULLS LAST
        """),
        params,
    )
    return [dict(r._mapping) for r in result]
