import json
from datetime import time as dt_time

from pydantic import BaseModel, field_validator
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/zones", tags=["zones"])

_VALID_SEVERITIES = ("low", "medium", "high", "critical")
_VALID_ZONE_MODULES = ("intrusion", "behavior")

# is_currently_active SQL expression:
#  FALSE when zone is deactivated OR under manual bypass
#  TRUE  when schedule_enabled=FALSE (always-on)
#  Otherwise checks ISODOW-1 (0=Mon…6=Sun) in active_days AND time in window
_ACTIVE_EXPR = """
    CASE
        WHEN NOT is_active                                           THEN FALSE
        WHEN bypass_until IS NOT NULL AND bypass_until > now()       THEN FALSE
        WHEN NOT schedule_enabled                                    THEN TRUE
        ELSE (
            (EXTRACT(ISODOW FROM NOW() AT TIME ZONE schedule_timezone)::int - 1)
                = ANY(active_days)
            AND
            (NOW() AT TIME ZONE schedule_timezone)::time
                BETWEEN active_start_time AND active_end_time
        )
    END
"""

_ZONE_SELECT = f"""
    SELECT id, camera_id, name, polygon, severity, is_active, bypass_until,
           schedule_enabled, schedule_timezone, active_days,
           active_start_time, active_end_time, applies_to_modules,
           ({_ACTIVE_EXPR}) AS is_currently_active,
           created_at
    FROM restricted_zones
"""


class ZonePoint(BaseModel):
    x: float
    y: float


class ZoneCreate(BaseModel):
    camera_id: str
    name: str
    polygon: list[ZonePoint]
    severity: str = "medium"
    applies_to_modules: list[str] = ["intrusion"]

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v not in _VALID_SEVERITIES:
            raise ValueError(f"severity must be one of {_VALID_SEVERITIES}")
        return v

    @field_validator("applies_to_modules")
    @classmethod
    def validate_applies_to_modules(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("applies_to_modules must not be empty")
        if any(m not in _VALID_ZONE_MODULES for m in v):
            raise ValueError(f"applies_to_modules values must be one of {_VALID_ZONE_MODULES}")
        return v


class ZoneScheduleBody(BaseModel):
    enabled: bool = True
    timezone: str = "UTC"
    active_days: list[int] = list(range(7))   # 0=Mon … 6=Sun
    active_start_time: str = "00:00:00"        # HH:MM or HH:MM:SS
    active_end_time: str = "23:59:59"

    @field_validator("active_days")
    @classmethod
    def validate_days(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("active_days must not be empty")
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("active_days values must be 0 (Mon) – 6 (Sun)")
        return sorted(set(v))

    @field_validator("active_start_time", "active_end_time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) < 2:
            raise ValueError("Time must be HH:MM or HH:MM:SS")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("Invalid time value")
        return v


class BulkIdsBody(BaseModel):
    ids: list[str]


class BulkBypassBody(BaseModel):
    ids: list[str]
    bypass_minutes: int = 60


@router.get("", dependencies=[Depends(require_permission("zone:manage"))])
async def list_zones(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(text(_ZONE_SELECT + " ORDER BY created_at DESC"))
    return [dict(row._mapping) for row in result]


@router.post("", status_code=201, dependencies=[Depends(require_permission("zone:manage"))])
async def create_zone(body: ZoneCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text(
            "INSERT INTO restricted_zones (tenant_id, camera_id, name, polygon, severity, applies_to_modules) "
            "VALUES (current_setting('app.current_tenant')::uuid, :camera_id, :name, CAST(:polygon AS jsonb), "
            ":severity, CAST(:applies_to_modules AS jsonb)) "
            "RETURNING id"
        ),
        {
            "camera_id": body.camera_id,
            "name": body.name,
            "polygon": json.dumps([p.model_dump() for p in body.polygon]),
            "severity": body.severity,
            "applies_to_modules": json.dumps(body.applies_to_modules),
        },
    )
    new_id = result.scalar_one()
    await db.commit()
    return {"id": new_id}


@router.put("/{zone_id}/schedule", dependencies=[Depends(require_permission("zone:manage"))])
async def set_zone_schedule(
    zone_id: str,
    body: ZoneScheduleBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Set or update the time-based activation schedule for a zone.

    When schedule_enabled=True the zone fires intrusion alerts ONLY when:
      - The current weekday (0=Mon…6=Sun) is in active_days, AND
      - The current local time (in schedule_timezone) is between
        active_start_time and active_end_time.

    When schedule_enabled=False (default) the zone is always-on.
    """
    result = (await db.execute(
        text("""
            UPDATE restricted_zones
               SET schedule_enabled  = :enabled,
                   schedule_timezone = :tz,
                   active_days       = CAST(:days AS smallint[]),
                   active_start_time = CAST(:start_time AS time),
                   active_end_time   = CAST(:end_time AS time)
             WHERE id = :id
         RETURNING id, schedule_enabled, schedule_timezone,
                   active_days, active_start_time, active_end_time
        """),
        {
            "id": zone_id,
            "enabled": body.enabled,
            "tz": body.timezone,
            "days": list(body.active_days),
            "start_time": dt_time(*[int(p) for p in body.active_start_time.split(":")]),
            "end_time": dt_time(*[int(p) for p in body.active_end_time.split(":")]),
        },
    )).first()
    await db.commit()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")
    return dict(result._mapping)


@router.delete("/{zone_id}", dependencies=[Depends(require_permission("zone:manage"))])
async def deactivate_zone(zone_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text("UPDATE restricted_zones SET is_active = FALSE WHERE id = :id"), {"id": zone_id})
    await db.commit()
    return {"id": zone_id, "is_active": False}


# ── Bulk bypass / restore ─────────────────────────────────────────────────────

@router.post("/bulk-bypass", dependencies=[Depends(require_permission("zone:manage"))])
async def bulk_bypass_zones(
    body: BulkBypassBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Temporarily suppress intrusion alerts for the specified zones.

    Sets bypass_until = now() + bypass_minutes on active zones.
    The intrusion worker skips zones where bypass_until > now().
    bypass_minutes must be 1–1440 (up to 24 hours).
    """
    if not body.ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ids must not be empty")
    if len(body.ids) > 50:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At most 50 ids per bulk request")
    if not (1 <= body.bypass_minutes <= 1440):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bypass_minutes must be between 1 and 1440")

    rows = (await db.execute(
        text(
            "UPDATE restricted_zones "
            "SET bypass_until = now() + (:minutes * INTERVAL '1 minute') "
            "WHERE id::text = ANY(:ids) AND is_active = TRUE "
            "RETURNING id, bypass_until"
        ),
        {"ids": body.ids, "minutes": body.bypass_minutes},
    )).fetchall()
    await db.commit()
    return {
        "bypassed": len(rows),
        "skipped": len(body.ids) - len(rows),
        "bypass_minutes": body.bypass_minutes,
        "zones": [{"id": str(r.id), "bypass_until": r.bypass_until.isoformat()} for r in rows],
    }


@router.post("/bulk-restore", dependencies=[Depends(require_permission("zone:manage"))])
async def bulk_restore_zones(
    body: BulkIdsBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Clear bypass_until on the specified zones, re-enabling intrusion detection immediately."""
    if not body.ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ids must not be empty")
    if len(body.ids) > 50:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At most 50 ids per bulk request")

    rows = (await db.execute(
        text(
            "UPDATE restricted_zones SET bypass_until = NULL "
            "WHERE id::text = ANY(:ids) AND bypass_until IS NOT NULL "
            "RETURNING id"
        ),
        {"ids": body.ids},
    )).fetchall()
    await db.commit()
    return {"restored": len(rows), "skipped": len(body.ids) - len(rows)}
