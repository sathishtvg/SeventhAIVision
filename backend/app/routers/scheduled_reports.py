"""Scheduled report delivery management.

Admins define schedules (daily/weekly/monthly) for automatic PDF report generation
and delivery via email or webhook. The scheduler_main.py job executes due schedules.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/scheduled-reports", tags=["reports"])

VALID_REPORT_TYPES = {"site_summary", "dob", "incident_summary"}
VALID_FREQUENCIES  = {"daily", "weekly", "monthly"}
VALID_DELIVERIES   = {"email", "webhook"}


def _compute_next_run(frequency: str, day_of_week: int | None,
                      day_of_month: int | None, hour_utc: int) -> datetime:
    """Compute the next scheduled run time from now (UTC)."""
    now = datetime.now(timezone.utc)
    target_today = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)

    if frequency == "daily":
        if now < target_today:
            return target_today
        return target_today + timedelta(days=1)

    if frequency == "weekly":
        dow = day_of_week if day_of_week is not None else 0
        days_ahead = (dow - now.weekday()) % 7
        if days_ahead == 0 and now >= target_today:
            days_ahead = 7
        return target_today + timedelta(days=days_ahead)

    # monthly
    dom = day_of_month if day_of_month is not None else 1
    candidate = now.replace(day=min(dom, 28), hour=hour_utc, minute=0, second=0, microsecond=0)
    if now >= candidate:
        # move to next month
        if now.month == 12:
            candidate = candidate.replace(year=now.year + 1, month=1)
        else:
            candidate = candidate.replace(month=now.month + 1)
    return candidate


class ScheduleCreate(BaseModel):
    name: str
    report_type: str = "site_summary"
    frequency: str = "daily"
    day_of_week: int | None = None   # 0=Mon … 6=Sun
    day_of_month: int | None = None  # 1-28
    hour_utc: int = 8
    site_id: str | None = None
    delivery_method: str = "email"
    recipients: list[str] = []
    webhook_url: str | None = None
    is_active: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    frequency: str | None = None
    day_of_week: int | None = None
    day_of_month: int | None = None
    hour_utc: int | None = None
    delivery_method: str | None = None
    recipients: list[str] | None = None
    webhook_url: str | None = None
    is_active: bool | None = None


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(require_permission("report:schedule"))])
async def list_schedules(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(text("""
        SELECT rs.id, rs.name, rs.report_type, rs.frequency,
               rs.day_of_week, rs.day_of_month, rs.hour_utc,
               rs.site_id, s.name AS site_name,
               rs.delivery_method, rs.recipients, rs.webhook_url,
               rs.is_active, rs.last_run_at, rs.next_run_at, rs.created_at
        FROM report_schedules rs
        LEFT JOIN sites s ON s.id = rs.site_id
        ORDER BY rs.created_at DESC
    """))
    return [dict(r._mapping) for r in result]


@router.post("", dependencies=[Depends(require_permission("report:schedule"))])
async def create_schedule(
    body: ScheduleCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if body.report_type not in VALID_REPORT_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"report_type must be one of: {sorted(VALID_REPORT_TYPES)}")
    if body.frequency not in VALID_FREQUENCIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"frequency must be one of: {sorted(VALID_FREQUENCIES)}")
    if body.delivery_method not in VALID_DELIVERIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"delivery_method must be one of: {sorted(VALID_DELIVERIES)}")
    if body.hour_utc < 0 or body.hour_utc > 23:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "hour_utc must be 0-23")

    next_run = _compute_next_run(body.frequency, body.day_of_week, body.day_of_month, body.hour_utc)

    import json
    result = await db.execute(text("""
        INSERT INTO report_schedules
            (tenant_id, name, report_type, frequency, day_of_week, day_of_month,
             hour_utc, site_id, delivery_method, recipients, webhook_url,
             is_active, next_run_at, created_by_user_id)
        VALUES (
            current_setting('app.current_tenant')::uuid,
            :name, :report_type, :frequency, :day_of_week, :day_of_month,
            :hour_utc, CAST(:site_id AS uuid), :delivery_method,
            CAST(:recipients AS jsonb), :webhook_url,
            :is_active, :next_run_at, CAST(:user_id AS uuid)
        )
        RETURNING id, name, report_type, frequency, day_of_week, day_of_month,
                  hour_utc, site_id, delivery_method, recipients, webhook_url,
                  is_active, next_run_at, last_run_at, created_at
    """), {
        "name": body.name,
        "report_type": body.report_type,
        "frequency": body.frequency,
        "day_of_week": body.day_of_week,
        "day_of_month": body.day_of_month,
        "hour_utc": body.hour_utc,
        "site_id": body.site_id,
        "delivery_method": body.delivery_method,
        "recipients": json.dumps(body.recipients),
        "webhook_url": body.webhook_url,
        "is_active": body.is_active,
        "next_run_at": next_run,
        "user_id": token.user_id,
    })
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@router.get("/{schedule_id}", dependencies=[Depends(require_permission("report:schedule"))])
async def get_schedule(schedule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(text("""
        SELECT rs.*, s.name AS site_name
        FROM report_schedules rs
        LEFT JOIN sites s ON s.id = rs.site_id
        WHERE rs.id = CAST(:id AS uuid)
    """), {"id": schedule_id})
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    return dict(row._mapping)


@router.put("/{schedule_id}", dependencies=[Depends(require_permission("report:schedule"))])
async def update_schedule(
    schedule_id: str,
    body: ScheduleUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    sets = ["updated_at = now()"]
    params: dict = {"id": schedule_id}

    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.frequency is not None:
        if body.frequency not in VALID_FREQUENCIES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"frequency must be one of: {sorted(VALID_FREQUENCIES)}")
        sets.append("frequency = :frequency"); params["frequency"] = body.frequency
    if body.day_of_week is not None:
        sets.append("day_of_week = :day_of_week"); params["day_of_week"] = body.day_of_week
    if body.day_of_month is not None:
        sets.append("day_of_month = :day_of_month"); params["day_of_month"] = body.day_of_month
    if body.hour_utc is not None:
        sets.append("hour_utc = :hour_utc"); params["hour_utc"] = body.hour_utc
    if body.delivery_method is not None:
        if body.delivery_method not in VALID_DELIVERIES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"delivery_method must be one of: {sorted(VALID_DELIVERIES)}")
        sets.append("delivery_method = :delivery_method")
        params["delivery_method"] = body.delivery_method
    if body.recipients is not None:
        import json
        sets.append("recipients = CAST(:recipients AS jsonb)")
        params["recipients"] = json.dumps(body.recipients)
    if body.webhook_url is not None:
        sets.append("webhook_url = :webhook_url"); params["webhook_url"] = body.webhook_url
    if body.is_active is not None:
        sets.append("is_active = :is_active"); params["is_active"] = body.is_active

    if len(sets) == 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")

    result = await db.execute(
        text(f"UPDATE report_schedules SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) RETURNING *"),
        params,
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    await db.commit()
    return dict(row._mapping)


@router.delete("/{schedule_id}", dependencies=[Depends(require_permission("report:schedule"))])
async def delete_schedule(schedule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("DELETE FROM report_schedules WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": schedule_id},
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    await db.commit()
    return {"id": schedule_id, "deleted": True}


@router.get("/{schedule_id}/deliveries", dependencies=[Depends(require_permission("report:schedule"))])
async def list_deliveries(schedule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(text("""
        SELECT id, status, delivered_at, error_message,
               report_period_start, report_period_end, created_at
        FROM report_deliveries
        WHERE schedule_id = CAST(:schedule_id AS uuid)
        ORDER BY created_at DESC LIMIT 50
    """), {"schedule_id": schedule_id})
    return [dict(r._mapping) for r in result]


@router.post("/{schedule_id}/run-now", dependencies=[Depends(require_permission("report:schedule"))])
async def run_schedule_now(schedule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """Trigger a scheduled report immediately (async — returns delivery ID, actual run is background)."""
    row = (await db.execute(
        text("SELECT id, tenant_id FROM report_schedules WHERE id = CAST(:id AS uuid)"),
        {"id": schedule_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")

    delivery = (await db.execute(text("""
        INSERT INTO report_deliveries (tenant_id, schedule_id, status)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:sid AS uuid), 'pending')
        RETURNING id
    """), {"sid": schedule_id})).first()
    await db.commit()
    return {"delivery_id": str(delivery.id), "status": "queued"}
