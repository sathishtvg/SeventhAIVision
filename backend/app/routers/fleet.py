"""Fleet: vehicles, their custom fields and groups — /api/v1/fleet

The upgrade of the GPS module's vehicles (design:
docs/plans/2026-10-14-gps-fleet-tracking-design.md). The GPS API
(`/api/v1/gps`) keeps working for what already uses it; both go through
`app.services.fleet_vehicles`, so a vehicle is checked and audited the same way.

Permissions:
    gps:read                vehicles, their groups and fields, a vehicle's history of changes
    gps:write               register and change a vehicle; groups and their members
    fleet:vehicle:archive   archive a vehicle, and restore one
    fleet:fields:manage     define, change and retire custom fields

Every read and every change is limited to the sites the person may see
(`user_sites`); a vehicle without a site is seen only by somebody with no site
limit. A vehicle somebody may not see is answered 404, as one that does not
exist.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import clamp
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import _client_ip, get_db_with_tenant
from app.routers.operations_board import held_by
from app.services import fleet_tracking as ft
from app.services import fleet_vehicles as fv
from app.services.audit import write_audit_log

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])

_READ = [Depends(require_permission("gps:read"))]
_WRITE = [Depends(require_permission("gps:write"))]
_ARCHIVE = [Depends(require_permission("fleet:vehicle:archive"))]
_FIELDS = [Depends(require_permission("fleet:fields:manage"))]
HELD = ("gps:read", "gps:write", "fleet:vehicle:archive", "fleet:fields:manage", "fleet:device:manage",
        "fleet:alerts:manage", "fleet:history:read", "fleet:history:export")

SORTS = {
    "name": "v.name", "plate_number": "v.plate_key", "vehicle_type": "v.vehicle_type", "site_name": "s.name",
    "service_status": "v.service_status", "updated_at": "v.updated_at", "last_position_at": "v.last_position_at",
}
GROUP_NAME_LIMIT = 120


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _may(held: frozenset[str]) -> dict:
    return {"edit": "gps:write" in held, "archive": "fleet:vehicle:archive" in held,
            "manage_fields": "fleet:fields:manage" in held, "manage_devices": "fleet:device:manage" in held,
            "manage_limits": "fleet:alerts:manage" in held, "read_history": "fleet:history:read" in held,
            "export_history": "fleet:history:export" in held}


def _out(row) -> dict:
    v = {k: val for k, val in dict(row).items() if k != "total"}
    v["type_label"] = fv.TYPE_LABEL.get(v.get("vehicle_type"), v.get("vehicle_type"))
    v["service_label"] = fv.SERVICE_LABEL.get(v.get("service_status"), v.get("service_status"))
    v["archived"] = v.get("archived_at") is not None
    if "tracking_status" in v:
        v["tracking_label"] = ft.STATUS_LABEL.get(v["tracking_status"], v["tracking_status"])
    return v


# ── vehicles ──────────────────────────────────────────────────────────────────

@router.get("/vehicles", dependencies=_READ)
async def list_vehicles(
    q: str | None = Query(None, max_length=100),
    site_id: uuid.UUID | None = Query(None),
    vehicle_type: str | None = Query(None),
    service_status: str | None = Query(None),
    group_id: uuid.UUID | None = Query(None),
    tracking: Literal["online", "stale", "offline", "not_configured"] | None = Query(None),
    archived: Literal["active", "archived", "all"] = Query("active"),
    sort: str = Query("name"),
    order: Literal["asc", "desc"] = Query("asc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The vehicles the person may see, a page at a time, searched and sorted by the server."""
    if sort not in SORTS:
        raise HTTPException(422, f"sort is one of {', '.join(SORTS)}")
    if vehicle_type is not None and vehicle_type not in fv.VEHICLE_TYPES:
        raise HTTPException(422, f"vehicle_type is one of {', '.join(fv.VEHICLE_TYPES)}")
    if service_status is not None and service_status not in fv.SERVICE_STATUSES:
        raise HTTPException(422, f"service_status is one of {', '.join(fv.SERVICE_STATUSES)}")
    limit, offset = clamp(limit, offset)
    params: dict = {"limit": limit, "offset": offset, **(await ft.limits(db))}
    where: list[str] = []
    scope = site_scope_clause(allowed, "v.site_id", params)
    if scope:
        where.append(scope)
    if archived == "active":
        where.append("v.archived_at IS NULL")
    elif archived == "archived":
        where.append("v.archived_at IS NOT NULL")
    if q and q.strip():
        params["q"] = _like(q.strip())
        params["qkey"] = _like(fv.plate_key(q) or "\x00")
        where.append("""(v.name ILIKE :q OR v.plate_number ILIKE :q OR v.plate_key LIKE :qkey OR v.make ILIKE :q
                         OR v.model ILIKE :q OR v.department ILIKE :q OR v.driver_name ILIKE :q OR d.full_name ILIKE :q)""")
    if site_id is not None:
        where.append("v.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    if vehicle_type:
        where.append("v.vehicle_type = :vtype")
        params["vtype"] = vehicle_type
    if service_status:
        where.append("v.service_status = :service")
        params["service"] = service_status
    if group_id is not None:
        where.append("EXISTS (SELECT 1 FROM fleet_vehicle_group_members m "
                     "WHERE m.vehicle_id = v.id AND m.group_id = CAST(:grp AS uuid))")
        params["grp"] = str(group_id)
    if tracking:
        where.append(f"{ft.status_sql('v')} = :tracking")
        params["tracking"] = tracking
    rows = (await db.execute(text(f"""
        SELECT v.id, v.name, v.plate_number, v.vehicle_type, v.make, v.model, v.color, v.site_id,
               s.name AS site_name, v.department, v.service_status, v.assigned_driver_user_id,
               coalesce(d.full_name, v.driver_name) AS driver_name, v.current_status, v.last_position_at,
               v.archived_at, v.archive_reason, v.created_at, v.updated_at,
               v.tracking_enabled, v.device_identifier, v.last_fix_at, {ft.status_sql('v')} AS tracking_status,
               count(*) OVER () AS total
          FROM vehicles v
          LEFT JOIN sites s ON s.id = v.site_id
          LEFT JOIN users d ON d.id = v.assigned_driver_user_id
         {('WHERE ' + ' AND '.join(where)) if where else ''}
         ORDER BY {SORTS[sort]} {order.upper()} NULLS LAST, v.id
         LIMIT :limit OFFSET :offset
    """), params)).mappings().all()
    held = await held_by(db, token.role_id, HELD)
    return {"items": [_out(r) for r in rows], "total": rows[0]["total"] if rows else 0,
            "limit": limit, "offset": offset, "may": _may(held)}


@router.post("/vehicles", status_code=201, dependencies=_WRITE)
async def create_vehicle(
    request: Request,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Register a vehicle. Its registration number is required, and is the organisation's only vehicle with it."""
    row = await fv.create(db, body, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          allowed=allowed, plate_required=True, ip=_client_ip(request))
    await db.commit()
    return _out(row)


@router.get("/vehicles/{vehicle_id}", dependencies=_READ)
async def get_vehicle(
    vehicle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One vehicle: its record, its custom fields with their definitions, and its groups."""
    await fv.visible_vehicle(db, str(vehicle_id), allowed)
    row = (await db.execute(text("""
        SELECT v.*, s.name AS site_name, coalesce(d.full_name, v.driver_name) AS driver_display,
               d.full_name AS driver_user_name, a.full_name AS archived_by_name
          FROM vehicles v
          LEFT JOIN sites s ON s.id = v.site_id
          LEFT JOIN users d ON d.id = v.assigned_driver_user_id
          LEFT JOIN users a ON a.id = v.archived_by_user_id
         WHERE v.id = CAST(:i AS uuid)
    """), {"i": str(vehicle_id)})).mappings().one()
    groups = (await db.execute(text("""
        SELECT g.id, g.name, g.site_id FROM fleet_vehicle_group_members m
          JOIN fleet_vehicle_groups g ON g.id = m.group_id
         WHERE m.vehicle_id = CAST(:i AS uuid) AND g.is_active ORDER BY g.name
    """), {"i": str(vehicle_id)})).mappings().all()
    held = await held_by(db, token.role_id, HELD)
    status = ft.status_of_row(row, await ft.limits(db), datetime.now(timezone.utc))
    return {**_out({**row, "tracking_status": status}), "groups": [dict(g) for g in groups], "fields": await fv.field_definitions(db),
            "may": _may(held), "archived_note": fv.ARCHIVED_NOTE}


@router.patch("/vehicles/{vehicle_id}", dependencies=_WRITE)
async def change_vehicle(
    vehicle_id: uuid.UUID,
    request: Request,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    row = await fv.update(db, str(vehicle_id), body, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          allowed=allowed, ip=_client_ip(request))
    await db.commit()
    return _out(row)


@router.post("/vehicles/{vehicle_id}/archive", dependencies=_ARCHIVE)
async def archive_vehicle(
    vehicle_id: uuid.UUID,
    request: Request,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Take the vehicle off the lists and the map, with the reason. Its history stays; it can be restored."""
    row = await fv.archive(db, str(vehicle_id), body.get("reason"), tenant_id=str(token.tenant_id),
                           user_id=str(token.user_id), allowed=allowed, ip=_client_ip(request))
    await db.commit()
    return _out(row)


@router.post("/vehicles/{vehicle_id}/restore", dependencies=_ARCHIVE)
async def restore_vehicle(
    vehicle_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    row = await fv.restore(db, str(vehicle_id), tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                           allowed=allowed, ip=_client_ip(request))
    await db.commit()
    return _out(row)


@router.get("/vehicles/{vehicle_id}/history", dependencies=_READ)
async def vehicle_history(
    vehicle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Who changed the vehicle and when, from the audit log, newest first. What was changed, not to what."""
    await fv.visible_vehicle(db, str(vehicle_id), allowed)
    rows = (await db.execute(text("""
        SELECT a.action, a.created_at, a.detail, u.full_name AS by_name
          FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id
         WHERE a.resource_type = 'vehicle' AND a.resource_id = CAST(:i AS uuid)
         ORDER BY a.created_at DESC, a.id DESC LIMIT 200
    """), {"i": str(vehicle_id)})).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get("/options", dependencies=_READ)
async def options(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What the vehicle form chooses from: types, service states, the sites the person may assign, drivers."""
    params: dict = {}
    scope = site_scope_clause(allowed, "s.id", params)
    sites = (await db.execute(text(f"""
        SELECT s.id, s.name FROM sites s {('WHERE ' + scope) if scope else ''} ORDER BY s.name
    """), params)).mappings().all()
    drivers = (await db.execute(text("""
        SELECT id, full_name FROM users WHERE is_active AND full_name IS NOT NULL ORDER BY full_name LIMIT 500
    """))).mappings().all()
    held = await held_by(db, token.role_id, HELD)
    return {
        "vehicle_types": [{"value": t, "label": fv.TYPE_LABEL[t]} for t in fv.VEHICLE_TYPES],
        "service_statuses": [{"value": s, "label": fv.SERVICE_LABEL[s]} for s in fv.SERVICE_STATUSES],
        "field_types": list(fv.FIELD_TYPES),
        "sites": [dict(s) for s in sites], "drivers": [dict(d) for d in drivers],
        "fields": await fv.field_definitions(db, active_only=True),
        "site_required": allowed is not None, "may": _may(held),
    }


# ── custom fields ─────────────────────────────────────────────────────────────

@router.get("/vehicle-fields", dependencies=_READ)
async def list_fields(db: AsyncSession = Depends(get_db_with_tenant)):
    return {"items": await fv.field_definitions(db)}


@router.post("/vehicle-fields", status_code=201, dependencies=_FIELDS)
async def create_field(
    request: Request,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    values = fv.check_field_definition(body, creating=True)
    if values["field_type"] == "select" and not values.get("options"):
        raise HTTPException(422, "A choice field needs its options")
    if (await db.execute(text("SELECT 1 FROM fleet_vehicle_fields WHERE field_key = :k"),
                         {"k": values["field_key"]})).first():
        raise HTTPException(409, "A field with this key already exists (retired fields keep their key)")
    columns = ["tenant_id", *values]
    params = {**values, "tenant_id": str(token.tenant_id)}
    if "options" in params:
        params["options"] = json.dumps(params["options"])
    placeholders = [f"CAST(:{c} AS jsonb)" if c == "options" else f":{c}" for c in columns]
    row = (await db.execute(text(f"""
        INSERT INTO fleet_vehicle_fields ({', '.join(columns)}) VALUES ({', '.join(placeholders)}) RETURNING *
    """), params)).mappings().one()
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="fleet.field.created", resource_type="fleet_vehicle_field",
                          resource_id=str(row["id"]), ip_address=_client_ip(request),
                          detail={"field_key": row["field_key"], "field_type": row["field_type"]})
    await db.commit()
    return dict(row)


async def _field(db: AsyncSession, field_id: uuid.UUID) -> dict:
    row = (await db.execute(text("SELECT * FROM fleet_vehicle_fields WHERE id = CAST(:i AS uuid)"),
                            {"i": str(field_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Field not found")
    return dict(row)


@router.patch("/vehicle-fields/{field_id}", dependencies=_FIELDS)
async def change_field(
    field_id: uuid.UUID,
    request: Request,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Change a field's words, choices or order. Its key and type stay, so the values given under it keep their meaning."""
    current = await _field(db, field_id)
    values = fv.check_field_definition(body, creating=False)
    if not values:
        raise HTTPException(422, "Nothing to change")
    if current["field_type"] == "select" and "options" in values and not values["options"]:
        raise HTTPException(422, "A choice field needs its options")
    sets = [f"{k} = CAST(:{k} AS jsonb)" if k == "options" else f"{k} = :{k}" for k in values]
    params = {k: json.dumps(v) if k == "options" else v for k, v in values.items()}
    row = (await db.execute(text(f"""
        UPDATE fleet_vehicle_fields SET {', '.join(sets)}, updated_at = now()
         WHERE id = CAST(:i AS uuid) RETURNING *
    """), {**params, "i": str(field_id)})).mappings().one()
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="fleet.field.changed", resource_type="fleet_vehicle_field",
                          resource_id=str(field_id), ip_address=_client_ip(request),
                          detail={"changed": sorted(values)})
    await db.commit()
    return dict(row)


@router.post("/vehicle-fields/{field_id}/{action}", dependencies=_FIELDS)
async def retire_or_reinstate_field(
    field_id: uuid.UUID,
    action: Literal["retire", "reinstate"],
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """A retired field is no longer asked for; the values already given under it are kept and still shown."""
    current = await _field(db, field_id)
    active = action == "reinstate"
    if current["is_active"] == active:
        raise HTTPException(409, f"This field is already {'in use' if active else 'retired'}")
    row = (await db.execute(text("""
        UPDATE fleet_vehicle_fields SET is_active = :a, updated_at = now() WHERE id = CAST(:i AS uuid) RETURNING *
    """), {"a": active, "i": str(field_id)})).mappings().one()
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action=f"fleet.field.{'reinstated' if active else 'retired'}",
                          resource_type="fleet_vehicle_field", resource_id=str(field_id),
                          ip_address=_client_ip(request), detail={"field_key": current["field_key"]})
    await db.commit()
    return dict(row)


# ── groups ────────────────────────────────────────────────────────────────────

def _group_scope(allowed: list[str] | None, params: dict) -> str | None:
    """A group without a site is the organisation's; somebody limited to sites sees only their sites' groups."""
    return site_scope_clause(allowed, "g.site_id", params)


async def _group(db: AsyncSession, group_id: uuid.UUID, allowed: list[str] | None) -> dict:
    params: dict = {"i": str(group_id)}
    scope = _group_scope(allowed, params)
    row = (await db.execute(text(f"""
        SELECT g.* FROM fleet_vehicle_groups g WHERE g.id = CAST(:i AS uuid) {('AND ' + scope) if scope else ''}
    """), params)).mappings().first()
    if row is None:
        raise HTTPException(404, "Group not found")
    return dict(row)


def _group_values(body: dict, *, creating: bool) -> dict:
    unknown = set(body) - {"name", "description", "site_id"}
    if unknown:
        raise HTTPException(422, f"Not a group setting: {', '.join(sorted(unknown))}")
    out: dict = {}
    if creating or "name" in body:
        name = body.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > GROUP_NAME_LIMIT:
            raise HTTPException(422, f"name is required, at most {GROUP_NAME_LIMIT} characters")
        out["name"] = name.strip()
    if "description" in body:
        desc = body["description"]
        if desc is not None and (not isinstance(desc, str) or len(desc) > 500):
            raise HTTPException(422, "description is at most 500 characters")
        out["description"] = desc.strip() if desc else None
    if "site_id" in body:
        out["site_id"] = str(body["site_id"]) if body["site_id"] else None
    return out


@router.get("/groups", dependencies=_READ)
async def list_groups(
    include_retired: bool = Query(False),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {}
    where = [] if include_retired else ["g.is_active"]
    scope = _group_scope(allowed, params)
    if scope:
        where.append(scope)
    rows = (await db.execute(text(f"""
        SELECT g.id, g.name, g.description, g.site_id, s.name AS site_name, g.is_active, g.created_at,
               (SELECT count(*) FROM fleet_vehicle_group_members m JOIN vehicles v ON v.id = m.vehicle_id
                 WHERE m.group_id = g.id AND v.archived_at IS NULL) AS vehicles
          FROM fleet_vehicle_groups g LEFT JOIN sites s ON s.id = g.site_id
         {('WHERE ' + ' AND '.join(where)) if where else ''}
         ORDER BY g.is_active DESC, g.name
    """), params)).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.post("/groups", status_code=201, dependencies=_WRITE)
async def create_group(
    request: Request,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    values = _group_values(body, creating=True)
    await fv.check_site(db, values.get("site_id"), allowed)
    if (await db.execute(text("SELECT 1 FROM fleet_vehicle_groups WHERE is_active AND lower(name) = lower(:n)"),
                         {"n": values["name"]})).first():
        raise HTTPException(409, "A group with this name already exists")
    row = (await db.execute(text("""
        INSERT INTO fleet_vehicle_groups (tenant_id, site_id, name, description, created_by_user_id)
        VALUES (:t, :site_id, :name, :description, CAST(:u AS uuid)) RETURNING *
    """), {"t": str(token.tenant_id), "u": str(token.user_id), "site_id": values.get("site_id"),
           "name": values["name"], "description": values.get("description")})).mappings().one()
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="fleet.group.created", resource_type="fleet_vehicle_group",
                          resource_id=str(row["id"]), ip_address=_client_ip(request), detail={"name": row["name"]})
    await db.commit()
    return dict(row)


@router.patch("/groups/{group_id}", dependencies=_WRITE)
async def change_group(
    group_id: uuid.UUID,
    request: Request,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    current = await _group(db, group_id, allowed)
    if not current["is_active"]:
        raise HTTPException(409, "This group is retired")
    values = _group_values(body, creating=False)
    if not values:
        raise HTTPException(422, "Nothing to change")
    if "site_id" in values:
        await fv.check_site(db, values["site_id"], allowed)
    if "name" in values and (await db.execute(text(
            "SELECT 1 FROM fleet_vehicle_groups WHERE is_active AND lower(name) = lower(:n) AND id <> CAST(:i AS uuid)"),
            {"n": values["name"], "i": str(group_id)})).first():
        raise HTTPException(409, "A group with this name already exists")
    sets = ", ".join(f"{k} = :{k}" for k in values)
    row = (await db.execute(text(f"""
        UPDATE fleet_vehicle_groups SET {sets}, updated_at = now() WHERE id = CAST(:i AS uuid) RETURNING *
    """), {**values, "i": str(group_id)})).mappings().one()
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="fleet.group.changed", resource_type="fleet_vehicle_group",
                          resource_id=str(group_id), ip_address=_client_ip(request),
                          detail={"changed": sorted(values)})
    await db.commit()
    return dict(row)


@router.post("/groups/{group_id}/retire", dependencies=_WRITE)
async def retire_group(
    group_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    current = await _group(db, group_id, allowed)
    if not current["is_active"]:
        raise HTTPException(409, "This group is already retired")
    row = (await db.execute(text("""
        UPDATE fleet_vehicle_groups SET is_active = FALSE, updated_at = now() WHERE id = CAST(:i AS uuid) RETURNING *
    """), {"i": str(group_id)})).mappings().one()
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="fleet.group.retired", resource_type="fleet_vehicle_group",
                          resource_id=str(group_id), ip_address=_client_ip(request), detail={"name": current["name"]})
    await db.commit()
    return dict(row)


@router.get("/groups/{group_id}/vehicles", dependencies=_READ)
async def group_vehicles(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    await _group(db, group_id, allowed)
    params: dict = {"g": str(group_id)}
    scope = site_scope_clause(allowed, "v.site_id", params)
    rows = (await db.execute(text(f"""
        SELECT v.id, v.name, v.plate_number, v.site_id, v.archived_at
          FROM fleet_vehicle_group_members m JOIN vehicles v ON v.id = m.vehicle_id
         WHERE m.group_id = CAST(:g AS uuid) {('AND ' + scope) if scope else ''}
         ORDER BY v.name
    """), params)).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.put("/groups/{group_id}/vehicles", dependencies=_WRITE)
async def set_group_vehicles(
    group_id: uuid.UUID,
    request: Request,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The group's vehicles become exactly these. Each must be one the person may see and not archived; a group
    that belongs to a site takes only that site's vehicles. Members the person may not see are left as they are."""
    group = await _group(db, group_id, allowed)
    if not group["is_active"]:
        raise HTTPException(409, "This group is retired")
    ids = body.get("vehicle_ids")
    if not isinstance(ids, list) or len(ids) > 1000:
        raise HTTPException(422, "vehicle_ids is a list of up to 1000 vehicles")
    try:
        wanted = {str(uuid.UUID(str(i))) for i in ids}
    except ValueError:
        raise HTTPException(422, "vehicle_ids holds vehicle ids") from None
    for vid in wanted:
        v = await fv.visible_vehicle(db, vid, allowed)
        if v["archived_at"] is not None:
            raise HTTPException(409, f"{v['plate_number'] or v['name']} is archived")
        if group["site_id"] is not None and v["site_id"] != group["site_id"]:
            raise HTTPException(422, f"{v['plate_number'] or v['name']} is not at this group's site")
    params: dict = {"g": str(group_id)}
    scope = site_scope_clause(allowed, "v.site_id", params)
    present = {str(r.vehicle_id) for r in await db.execute(text(f"""
        SELECT m.vehicle_id FROM fleet_vehicle_group_members m JOIN vehicles v ON v.id = m.vehicle_id
         WHERE m.group_id = CAST(:g AS uuid) {('AND ' + scope) if scope else ''}
    """), params)}
    added, removed = wanted - present, present - wanted
    if removed:
        await db.execute(text("""
            DELETE FROM fleet_vehicle_group_members
             WHERE group_id = CAST(:g AS uuid) AND vehicle_id = ANY(CAST(:ids AS uuid[]))
        """), {"g": str(group_id), "ids": sorted(removed)})
    for vid in sorted(added):
        await db.execute(text("""
            INSERT INTO fleet_vehicle_group_members (tenant_id, group_id, vehicle_id, added_by_user_id)
            VALUES (:t, CAST(:g AS uuid), CAST(:v AS uuid), CAST(:u AS uuid))
        """), {"t": str(token.tenant_id), "g": str(group_id), "v": vid, "u": str(token.user_id)})
    if added or removed:
        await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                              action="fleet.group.members_changed", resource_type="fleet_vehicle_group",
                              resource_id=str(group_id), ip_address=_client_ip(request),
                              detail={"added": len(added), "removed": len(removed)})
    await db.commit()
    return {"added": len(added), "removed": len(removed), "vehicles": len(wanted)}
