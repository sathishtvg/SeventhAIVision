"""Crowd Zones CRUD — capacity-bounded polygon zones for crowd density monitoring.

Mirrors the restricted_zones router (zones.py) but adds max_capacity.
Uses the same 'zone:manage' permission so admins manage both zone types.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/crowd-zones", tags=["crowd-zones"])

VALID_SEVERITIES = {"low", "medium", "high", "critical"}


class ZonePoint(BaseModel):
    x: float
    y: float


class CrowdZoneCreate(BaseModel):
    camera_id: str
    name: str
    polygon: list[ZonePoint]
    max_capacity: int = 10
    severity: str = "medium"

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v not in VALID_SEVERITIES:
            raise ValueError(f"severity must be one of {VALID_SEVERITIES}")
        return v

    @field_validator("max_capacity")
    @classmethod
    def validate_capacity(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_capacity must be >= 1")
        return v


class CrowdZoneUpdate(BaseModel):
    name: str | None = None
    max_capacity: int | None = None
    severity: str | None = None
    is_active: bool | None = None


@router.get("", dependencies=[Depends(require_permission("zone:manage"))])
async def list_crowd_zones(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(text(
        "SELECT id, camera_id, name, polygon, max_capacity, severity, is_active, created_at, updated_at "
        "FROM crowd_zones ORDER BY created_at DESC"
    ))
    return [dict(r._mapping) for r in result]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("zone:manage"))])
async def create_crowd_zone(body: CrowdZoneCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text(
            "INSERT INTO crowd_zones (tenant_id, camera_id, name, polygon, max_capacity, severity) "
            "VALUES (current_setting('app.current_tenant')::uuid, :camera_id, :name, CAST(:polygon AS jsonb), :max_capacity, :severity) "
            "RETURNING id"
        ),
        {
            "camera_id": body.camera_id,
            "name": body.name,
            "polygon": json.dumps([p.model_dump() for p in body.polygon]),
            "max_capacity": body.max_capacity,
            "severity": body.severity,
        },
    )
    new_id = result.scalar_one()
    await db.commit()
    return {"id": new_id}


@router.put("/{zone_id}", dependencies=[Depends(require_permission("zone:manage"))])
async def update_crowd_zone(zone_id: str, body: CrowdZoneUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    sets, params = [], {"id": zone_id}
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.max_capacity is not None:
        if body.max_capacity < 1:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "max_capacity must be >= 1")
        sets.append("max_capacity = :max_capacity"); params["max_capacity"] = body.max_capacity
    if body.severity is not None:
        if body.severity not in VALID_SEVERITIES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid severity")
        sets.append("severity = :severity"); params["severity"] = body.severity
    if body.is_active is not None:
        sets.append("is_active = :is_active"); params["is_active"] = body.is_active
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    sets.append("updated_at = now()")
    result = await db.execute(
        text(f"UPDATE crowd_zones SET {', '.join(sets)} WHERE id = :id "
             "RETURNING id, camera_id, name, max_capacity, severity, is_active, updated_at"),
        params,
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Crowd zone not found")
    await db.commit()
    return dict(row)


@router.delete("/{zone_id}", dependencies=[Depends(require_permission("zone:manage"))])
async def deactivate_crowd_zone(zone_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text("UPDATE crowd_zones SET is_active = FALSE WHERE id = :id"), {"id": zone_id})
    await db.commit()
    return {"id": zone_id, "is_active": False}
