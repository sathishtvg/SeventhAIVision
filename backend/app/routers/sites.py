from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/sites", tags=["sites"])


class SiteCreate(BaseModel):
    name: str
    address: str | None = None
    description: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    geofence_radius_meters: int | None = None
    # Minutes past the rostered start before a guard counts as late here.
    # None = fall back to the tenant-wide attendance.late_grace_minutes.
    late_grace_minutes: int | None = None
    client_id: str | None = None
    bill_rate: float | None = None


class SiteUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    description: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    geofence_radius_meters: int | None = None
    late_grace_minutes: int | None = None
    is_active: bool | None = None
    client_id: str | None = None
    bill_rate: float | None = None
    # Visitor Management: enabling VMS and binding this site's own ANPR
    # cameras to its entry/exit lanes. Update-only (not on create) because
    # the cameras have to exist and be assigned to the site first — the
    # same reason employee pay fields are edit-only on users.
    vms_enabled: bool | None = None
    entry_lpr_camera_id: str | None = None
    exit_lpr_camera_id: str | None = None
    free_parking_minutes: int | None = None


@router.get("", dependencies=[Depends(require_permission("camera:read"))])
async def list_sites(
    is_active: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    where_clauses = []
    params: dict = {}
    if is_active is not None:
        where_clauses.append("s.is_active = :is_active")
        params["is_active"] = is_active
    scope = site_scope_clause(allowed_sites, "s.id", params)
    if scope:
        where_clauses.append(scope)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    result = await db.execute(
        text(
            f"""
            SELECT s.id, s.name, s.address, s.description,
                   s.latitude, s.longitude, s.geofence_radius_meters, s.late_grace_minutes, s.is_active,
                   s.client_id, s.bill_rate, bc.name AS client_name,
                   s.vms_enabled, s.entry_lpr_camera_id, s.exit_lpr_camera_id,
                   s.free_parking_minutes,
                   s.created_at, s.updated_at,
                   COUNT(c.id) AS camera_count
            FROM sites s
            LEFT JOIN cameras c ON c.site_id = s.id AND c.is_active = TRUE
            LEFT JOIN billing_clients bc ON bc.id = s.client_id
            {where}
            GROUP BY s.id, bc.name
            ORDER BY s.name
            """
        ),
        params,
    )
    return [dict(r._mapping) for r in result]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("site:manage"))])
async def create_site(body: SiteCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text(
            "INSERT INTO sites (tenant_id, name, address, description, latitude, longitude, "
            "geofence_radius_meters, late_grace_minutes, client_id, bill_rate) "
            "VALUES (current_setting('app.current_tenant')::uuid, :name, :address, :description, :lat, :lng, "
            ":radius, :grace, :client_id, :bill_rate) "
            "RETURNING id"
        ),
        {
            "name": body.name,
            "address": body.address,
            "description": body.description,
            "lat": body.latitude,
            "lng": body.longitude,
            "radius": body.geofence_radius_meters,
            "grace": body.late_grace_minutes,
            "client_id": body.client_id,
            "bill_rate": body.bill_rate,
        },
    )
    new_id = result.scalar_one()
    await db.commit()
    return {"id": new_id, "name": body.name}


@router.get("/{site_id}", dependencies=[Depends(require_permission("site:manage"))])
async def get_site(
    site_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    if not is_site_allowed(allowed_sites, site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    result = await db.execute(
        text(
            """
            SELECT s.id, s.name, s.address, s.description,
                   s.latitude, s.longitude, s.geofence_radius_meters, s.late_grace_minutes, s.is_active,
                   s.client_id, s.bill_rate, bc.name AS client_name,
                   s.vms_enabled, s.entry_lpr_camera_id, s.exit_lpr_camera_id,
                   s.free_parking_minutes,
                   s.created_at, s.updated_at,
                   COUNT(c.id) AS camera_count
            FROM sites s
            LEFT JOIN cameras c ON c.site_id = s.id AND c.is_active = TRUE
            LEFT JOIN billing_clients bc ON bc.id = s.client_id
            WHERE s.id = :id
            GROUP BY s.id, bc.name
            """
        ),
        {"id": site_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    return dict(row)


@router.put("/{site_id}", dependencies=[Depends(require_permission("site:manage"))])
async def update_site(site_id: str, body: SiteUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    sets, params = [], {"id": site_id}
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.address is not None:
        sets.append("address = :address"); params["address"] = body.address
    if body.description is not None:
        sets.append("description = :description"); params["description"] = body.description
    if body.latitude is not None:
        sets.append("latitude = :lat"); params["lat"] = body.latitude
    if body.longitude is not None:
        sets.append("longitude = :lng"); params["lng"] = body.longitude
    if body.geofence_radius_meters is not None:
        sets.append("geofence_radius_meters = :radius"); params["radius"] = body.geofence_radius_meters
    if body.late_grace_minutes is not None:
        sets.append("late_grace_minutes = :grace"); params["grace"] = body.late_grace_minutes
    if body.is_active is not None:
        sets.append("is_active = :is_active"); params["is_active"] = body.is_active
    if body.client_id is not None:
        sets.append("client_id = :client_id"); params["client_id"] = body.client_id
    if body.bill_rate is not None:
        sets.append("bill_rate = :bill_rate"); params["bill_rate"] = body.bill_rate
    if body.vms_enabled is not None:
        sets.append("vms_enabled = :vms_enabled"); params["vms_enabled"] = body.vms_enabled
    # The LPR camera bindings and the parking allowance key off model_fields_set
    # rather than `is not None`, because explicitly sending null is the only way
    # to UNBIND a camera or clear the allowance. The `is not None` pattern used
    # by the fields above silently ignores a null, which would make an entry
    # lane impossible to detach once set.
    for field, column in (
        ("entry_lpr_camera_id", "entry_lpr_camera_id"),
        ("exit_lpr_camera_id", "exit_lpr_camera_id"),
    ):
        if field in body.model_fields_set:
            sets.append(f"{column} = CAST(:{field} AS uuid)")
            params[field] = getattr(body, field)
    if "free_parking_minutes" in body.model_fields_set:
        sets.append("free_parking_minutes = :free_parking_minutes")
        params["free_parking_minutes"] = body.free_parking_minutes

    if not sets:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")

    sets.append("updated_at = now()")
    result = await db.execute(
        text(f"UPDATE sites SET {', '.join(sets)} WHERE id = :id RETURNING id"),
        params,
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    await db.commit()
    return {"id": site_id}


@router.delete("/{site_id}", dependencies=[Depends(require_permission("site:manage"))])
async def deactivate_site(site_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("UPDATE sites SET is_active = FALSE, updated_at = now() WHERE id = :id RETURNING id"),
        {"id": site_id},
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    await db.commit()
    return {"id": site_id, "is_active": False}


@router.get("/{site_id}/cameras", dependencies=[Depends(require_permission("camera:read"))])
async def list_site_cameras(
    site_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    if not is_site_allowed(allowed_sites, site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    # Verify site exists first
    site_check = await db.execute(
        text("SELECT id FROM sites WHERE id = :id"),
        {"id": site_id},
    )
    if not site_check.scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")

    result = await db.execute(
        text(
            "SELECT id, name, location, latitude, longitude, ai_modules_enabled, is_active, created_at, updated_at "
            "FROM cameras WHERE site_id = :site_id ORDER BY name"
        ),
        {"site_id": site_id},
    )
    return [dict(r._mapping) for r in result]
