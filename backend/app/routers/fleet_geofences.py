"""Fleet geofences, their rules and events, and the alerts when a vehicle stops reporting — /api/v1/fleet

Design: docs/plans/2026-10-14-gps-fleet-tracking-design.md, section 5; how a fix is judged against a geofence is
in app/services/fleet_geofences.py.

    GET  /geofences                       the geofences the person may see, with their rules
    GET  /geofences/options               sites, groups, people and roles a rule may name
    GET  /geofences/{id}
    POST /geofences                       draw one (fleet:geofence:manage)
    PATCH /geofences/{id}                 change it, its rules, or switch it off and on
    POST /geofences/{id}/archive          archived, never removed: its events stay readable
    GET  /geofence-events                 entries, exits, stays and overspeeds, newest first
    GET|PUT /alert-settings               the alerts when a vehicle stops reporting (fleet:alerts:manage)

The older GPS API (`/api/v1/gps/geofences`) keeps working for what already uses it. A geofence drawn here also
sets the flags that API reads, so both say the same.

SITE LIMITS. Somebody limited to sites sees the geofences of those sites and the organisation's own, and the
events of their sites' vehicles only. They draw and change geofences of their own sites only, naming only their
sites' groups.

A geofence never opens a barrier, admits anybody or confirms who is driving.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import _client_ip, get_db_with_tenant
from app.routers.operations_board import held_by
from app.services import fleet_alerts as fa
from app.services import fleet_geofences as fg
from app.services.audit import write_audit_log

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])

_READ = [Depends(require_permission("gps:read"))]
_MANAGE = [Depends(require_permission("fleet:geofence:manage"))]
_ALERTS = [Depends(require_permission("fleet:alerts:manage"))]
NAME_LIMIT = 120
#: Roles a rule may name: every role that may read vehicles, less Super Admin and Client, who get no fleet rights.
RULE_ROLES = (2, 3, 4, 5, 6, 8)
SEVERITY = Literal["low", "medium", "high", "critical"]


class ShapeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["circle", "polygon"]
    center: dict | None = None
    radius_m: float | None = None
    points: list[dict] | None = None


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["entry", "exit", "dwell_inside", "dwell_outside", "overspeed"]
    minutes: int | None = Field(None, ge=1, le=1440)
    speed_kmh: float | None = Field(None, ge=5, le=300)
    severity: SEVERITY = "medium"
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    role_ids: list[int] = Field(default_factory=list, max_length=10)
    is_active: bool = True


class GeofenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, max_length=NAME_LIMIT)
    description: str | None = Field(None, max_length=2000)
    site_id: uuid.UUID | None = None
    #: Start from the site's own boundary (its drawn polygon, or its circle) instead of `shape`.
    from_site: bool = False
    shape: ShapeIn | None = None
    group_ids: list[uuid.UUID] | None = Field(None, max_length=50)
    #: The rules it has afterwards: a kind left out is switched off (kept, so its events still name it).
    rules: list[RuleIn] | None = Field(None, max_length=5)
    is_active: bool | None = None


class ArchiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(..., min_length=3, max_length=500)


class AlertSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool
    severity: SEVERITY


class AlertSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offline: AlertSetting
    stale: AlertSetting


# ── reading ──────────────────────────────────────────────────────────────────

_SELECT = """
    SELECT g.id, g.name, g.description, g.site_id, s.name AS site_name, g.shape, g.polygon, g.center_lat,
           g.center_lon, g.radius_m, g.is_active, g.archived_at, g.archive_reason, g.created_at, g.updated_at,
           COALESCE((SELECT json_agg(json_build_object('id', vg.id, 'name', vg.name, 'site_id', vg.site_id)
                                     ORDER BY vg.name)
                       FROM fleet_geofence_groups gg JOIN fleet_vehicle_groups vg ON vg.id = gg.group_id
                      WHERE gg.geofence_id = g.id), '[]') AS groups,
           COALESCE((SELECT json_agg(json_build_object(
                         'kind', r.kind, 'minutes', r.minutes, 'speed_kmh', r.speed_kmh, 'severity', r.severity,
                         'user_ids', r.recipient_user_ids, 'role_ids', r.recipient_role_ids, 'is_active', r.is_active)
                       ORDER BY r.kind) FROM fleet_geofence_rules r WHERE r.geofence_id = g.id), '[]') AS rules,
           (SELECT count(*) FROM fleet_geofence_state st JOIN vehicles v ON v.id = st.vehicle_id
             WHERE st.geofence_id = g.id AND st.inside AND v.archived_at IS NULL {inside_scope}) AS inside_now
      FROM geofences g
      LEFT JOIN sites s ON s.id = g.site_id
"""


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _out(row) -> dict:
    g = dict(row)
    g["polygon"] = _json(g["polygon"])
    g["groups"] = _json(g["groups"])
    g["rules"] = _json(g["rules"])
    g["archived"] = g["archived_at"] is not None
    if g["shape"] == "circle":
        g["center"] = {"lat": g.pop("center_lat"), "lon": g.pop("center_lon")}
    else:
        g.pop("center_lat"), g.pop("center_lon")
        g["center"] = None
    return g


def _scope(allowed: list[str] | None, params: dict) -> tuple[str, str]:
    """(the geofences a person may see, the vehicles counted inside them)."""
    if allowed is None:
        return "TRUE", ""
    clause = site_scope_clause(allowed, "g.site_id", params)
    vehicles = site_scope_clause(allowed, "v.site_id", params)
    return f"(g.site_id IS NULL OR {clause})", f"AND {vehicles}"


async def _visible(db: AsyncSession, geofence_id: uuid.UUID, allowed: list[str] | None) -> dict:
    params: dict = {"id": str(geofence_id)}
    where, inside_scope = _scope(allowed, params)
    row = (await db.execute(text(_SELECT.format(inside_scope=inside_scope) + f" WHERE g.id = CAST(:id AS uuid) AND {where}"),
                            params)).mappings().first()
    if row is None:
        raise HTTPException(404, "Geofence not found")
    return _out(row)


def _may_change(g_site, allowed: list[str] | None) -> bool:
    return allowed is None or (g_site is not None and is_site_allowed(allowed, g_site))


@router.get("/geofences", dependencies=_READ)
async def list_geofences(
    archived: bool = False,
    site_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {"archived": archived}
    where, inside_scope = _scope(allowed, params)
    clauses = [where, "(g.archived_at IS NOT NULL) = :archived"]
    if site_id is not None:
        clauses.append("g.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    rows = (await db.execute(text(_SELECT.format(inside_scope=inside_scope) + " WHERE " + " AND ".join(clauses)
                                  + " ORDER BY g.name"), params)).mappings().all()
    held = await held_by(db, token.role_id, ("fleet:geofence:manage", "fleet:alerts:manage"))
    items = [_out(r) for r in rows]
    for g in items:
        g["may_change"] = "fleet:geofence:manage" in held and _may_change(g["site_id"], allowed)
    return {"items": items, "may": {"manage": "fleet:geofence:manage" in held,
                                    "manage_alerts": "fleet:alerts:manage" in held,
                                    "organisation_wide": allowed is None}}


@router.get("/geofences/options", dependencies=_READ)
async def geofence_options(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {}
    site_clause = site_scope_clause(allowed, "s.id", params) or "TRUE"
    sites = (await db.execute(text(f"""
        SELECT s.id, s.name, s.latitude, s.longitude, s.geofence_radius_meters,
               CASE WHEN jsonb_typeof(s.geofence_polygon) = 'array' THEN jsonb_array_length(s.geofence_polygon) >= 3
                    ELSE FALSE END AS has_polygon
          FROM sites s WHERE {site_clause} ORDER BY s.name
    """), params)).mappings().all()
    group_params: dict = {}
    group_clause = site_scope_clause(allowed, "vg.site_id", group_params) or "TRUE"
    groups = (await db.execute(text(f"""
        SELECT vg.id, vg.name, vg.site_id FROM fleet_vehicle_groups vg WHERE vg.is_active AND {group_clause} ORDER BY vg.name
    """), group_params)).mappings().all()
    people = (await db.execute(text("""
        SELECT DISTINCT u.id, u.full_name, u.role_id, ro.name AS role_name
          FROM users u
          JOIN roles ro ON ro.id = u.role_id
          JOIN role_permissions rp ON rp.role_id = u.role_id
          JOIN permissions p ON p.id = rp.permission_id AND p.code = 'gps:read'
         WHERE u.is_active AND u.role_id = ANY(:roles)
         ORDER BY u.full_name
    """), {"roles": list(RULE_ROLES)})).mappings().all()
    roles = (await db.execute(text("SELECT id, name FROM roles WHERE id = ANY(:roles) ORDER BY id"),
                              {"roles": list(RULE_ROLES)})).mappings().all()
    return {
        "sites": [{**dict(s), "has_boundary": bool(s["has_polygon"] or (s["latitude"] is not None and s["geofence_radius_meters"]))}
                  for s in sites],
        "groups": [dict(g) for g in groups], "people": [dict(p) for p in people], "roles": [dict(r) for r in roles],
        "kinds": [{"value": k, "label": fg.KIND_LABEL[k]} for k in fg.KINDS],
        "severities": list(fa.SEVERITIES),
        "limits": {"least_radius_m": fg.LEAST_RADIUS_M, "most_radius_m": fg.MOST_RADIUS_M,
                   "least_points": fg.LEAST_POINTS, "most_points": fg.MOST_POINTS},
    }


@router.get("/geofences/{geofence_id}", dependencies=_READ)
async def get_geofence(
    geofence_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    return await _visible(db, geofence_id, allowed)


# ── drawing and changing ─────────────────────────────────────────────────────

async def _site_shape(db: AsyncSession, site_id: str) -> dict:
    site = (await db.execute(text("""
        SELECT latitude, longitude, geofence_radius_meters, geofence_polygon FROM sites WHERE id = CAST(:s AS uuid)
    """), {"s": site_id})).mappings().first()
    if site is None:
        raise HTTPException(422, "That site is not this organisation's")
    polygon = _json(site["geofence_polygon"])
    if isinstance(polygon, list) and len(polygon) >= fg.LEAST_POINTS:
        return {"kind": "polygon", "points": polygon}
    if site["latitude"] is not None and site["longitude"] is not None and site["geofence_radius_meters"]:
        return {"kind": "circle", "center": {"lat": site["latitude"], "lon": site["longitude"]},
                "radius_m": max(float(site["geofence_radius_meters"]), fg.LEAST_RADIUS_M)}
    raise HTTPException(422, "The site has no boundary drawn and no radius set: draw the geofence instead")


def _shape_columns(shape: dict) -> dict:
    try:
        if shape["kind"] == "circle":
            center, radius = fg.check_circle(shape.get("center"), shape.get("radius_m"))
            return {"shape": "circle", "center_lat": center[0], "center_lon": center[1], "radius_m": radius,
                    "polygon": json.dumps(fg.circle_outline(center, radius))}
        points = fg.check_polygon(shape.get("points"))
        return {"shape": "polygon", "center_lat": None, "center_lon": None, "radius_m": None,
                "polygon": json.dumps([{"lat": lat, "lon": lon} for lat, lon in points])}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


async def _check_groups(db: AsyncSession, group_ids: list[uuid.UUID], allowed: list[str] | None) -> list[str]:
    ids = sorted({str(g) for g in group_ids})
    if not ids:
        return []
    rows = (await db.execute(text("SELECT id, site_id FROM fleet_vehicle_groups WHERE id = ANY(CAST(:ids AS uuid[])) AND is_active"),
                             {"ids": ids})).mappings().all()
    if len(rows) != len(ids):
        raise HTTPException(422, "A group named is not one of this organisation's active groups")
    if allowed is not None and not all(is_site_allowed(allowed, r["site_id"]) for r in rows):
        raise HTTPException(403, "A group named is not at one of your sites")
    return ids


async def _check_rules(db: AsyncSession, rules: list[RuleIn]) -> list[RuleIn]:
    kinds = [r.kind for r in rules]
    if len(set(kinds)) != len(kinds):
        raise HTTPException(422, "One rule of each kind")
    for r in rules:
        if r.kind in ("dwell_inside", "dwell_outside") and r.minutes is None:
            raise HTTPException(422, "A rule about staying needs its minutes")
        if r.kind == "overspeed" and r.speed_kmh is None:
            raise HTTPException(422, "An overspeed rule needs its limit in km/h")
        if r.kind not in ("dwell_inside", "dwell_outside"):
            r.minutes = None
        if r.kind != "overspeed":
            r.speed_kmh = None
        if any(role not in RULE_ROLES for role in r.role_ids):
            raise HTTPException(422, "A role named may not be told about vehicles")
    users = sorted({str(u) for r in rules for u in r.user_ids})
    if users:
        found = (await db.execute(text("SELECT count(*) FROM users WHERE id = ANY(CAST(:u AS uuid[])) AND is_active"),
                                  {"u": users})).scalar()
        if found != len(users):
            raise HTTPException(422, "A person named is not an active user of this organisation")
    return rules


async def _write_rules(db: AsyncSession, tenant_id: str, geofence_id: str, rules: list[RuleIn]) -> None:
    for r in rules:
        await db.execute(text("""
            INSERT INTO fleet_geofence_rules (tenant_id, geofence_id, kind, minutes, speed_kmh, severity,
                                              recipient_user_ids, recipient_role_ids, is_active)
            VALUES (CAST(:t AS uuid), CAST(:g AS uuid), :kind, :minutes, :speed, :sev, CAST(:users AS uuid[]),
                    CAST(:roles AS int[]), :active)
            ON CONFLICT (geofence_id, kind) DO UPDATE
               SET minutes = EXCLUDED.minutes, speed_kmh = EXCLUDED.speed_kmh, severity = EXCLUDED.severity,
                   recipient_user_ids = EXCLUDED.recipient_user_ids, recipient_role_ids = EXCLUDED.recipient_role_ids,
                   is_active = EXCLUDED.is_active, updated_at = now()
        """), {"t": tenant_id, "g": geofence_id, "kind": r.kind, "minutes": r.minutes, "speed": r.speed_kmh,
               "sev": r.severity, "users": [str(u) for u in r.user_ids], "roles": r.role_ids, "active": r.is_active})
    await db.execute(text("""
        UPDATE fleet_geofence_rules SET is_active = FALSE, updated_at = now()
         WHERE geofence_id = CAST(:g AS uuid) AND is_active AND NOT (kind = ANY(:kinds))
    """), {"g": geofence_id, "kinds": [r.kind for r in rules]})
    # The flags the older GPS API reads, so that both say the same.
    await db.execute(text("""
        UPDATE geofences g
           SET alert_on_entry = EXISTS (SELECT 1 FROM fleet_geofence_rules r WHERE r.geofence_id = g.id AND r.kind = 'entry' AND r.is_active),
               alert_on_exit = EXISTS (SELECT 1 FROM fleet_geofence_rules r WHERE r.geofence_id = g.id AND r.kind = 'exit' AND r.is_active),
               speed_limit = (SELECT r.speed_kmh FROM fleet_geofence_rules r WHERE r.geofence_id = g.id AND r.kind = 'overspeed' AND r.is_active)
         WHERE g.id = CAST(:g AS uuid)
    """), {"g": geofence_id})


async def _write_groups(db: AsyncSession, tenant_id: str, geofence_id: str, group_ids: list[str]) -> None:
    await db.execute(text("DELETE FROM fleet_geofence_groups WHERE geofence_id = CAST(:g AS uuid)"), {"g": geofence_id})
    for group in group_ids:
        await db.execute(text("""
            INSERT INTO fleet_geofence_groups (tenant_id, geofence_id, group_id)
            VALUES (CAST(:t AS uuid), CAST(:g AS uuid), CAST(:grp AS uuid))
        """), {"t": tenant_id, "g": geofence_id, "grp": group})


async def _forget_sides(db: AsyncSession, geofence_id: str) -> None:
    """Its shape or the vehicles it applies to changed: every vehicle's next fix says again which side it is on,
    without an alert."""
    await db.execute(text("DELETE FROM fleet_geofence_state WHERE geofence_id = CAST(:g AS uuid)"), {"g": geofence_id})


@router.post("/geofences", dependencies=_MANAGE, status_code=201)
async def create_geofence(
    body: GeofenceIn,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(422, "A geofence needs a name")
    site = str(body.site_id) if body.site_id else None
    if not _may_change(site, allowed):
        raise HTTPException(403, "You may draw geofences at your own sites only")
    if body.from_site:
        if site is None:
            raise HTTPException(422, "Starting from a site's boundary needs the site")
        shape = await _site_shape(db, site)
    elif body.shape is not None:
        shape = body.shape.model_dump()
    else:
        raise HTTPException(422, "Draw a circle or a polygon, or start from the site's boundary")
    if site is not None and (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"), {"s": site})).first() is None:
        raise HTTPException(422, "That site is not this organisation's")
    columns = _shape_columns(shape)
    groups = await _check_groups(db, body.group_ids or [], allowed)
    rules = await _check_rules(db, body.rules or [])
    tenant_id = str(token.tenant_id)
    gid = (await db.execute(text("""
        INSERT INTO geofences (tenant_id, site_id, name, description, polygon, shape, center_lat, center_lon, radius_m,
                               alert_on_entry, alert_on_exit, is_active, created_by_user_id)
        VALUES (CAST(:t AS uuid), CAST(:site AS uuid), :name, :description, CAST(:polygon AS jsonb), :shape,
                :center_lat, :center_lon, :radius_m, FALSE, FALSE, :active, CAST(:u AS uuid))
        RETURNING id
    """), {"t": tenant_id, "site": site, "name": name, "description": body.description, "active": body.is_active is not False,
           "u": str(token.user_id), **columns})).scalar()
    await _write_groups(db, tenant_id, str(gid), groups)
    await _write_rules(db, tenant_id, str(gid), rules)
    await write_audit_log(db, tenant_id=tenant_id, user_id=str(token.user_id), action="fleet.geofence.created",
                          resource_type="geofence", resource_id=str(gid), ip_address=_client_ip(request),
                          detail={"name": name, "shape": columns["shape"], "site_id": site, "groups": groups,
                                  "rules": [r.kind for r in rules]})
    out = await _visible(db, gid, allowed)  # before the commit: the tenant setting ends with it
    await db.commit()
    return out


@router.patch("/geofences/{geofence_id}", dependencies=_MANAGE)
async def change_geofence(
    geofence_id: uuid.UUID,
    body: GeofenceIn,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    current = await _visible(db, geofence_id, allowed)
    if current["archived"]:
        raise HTTPException(409, "An archived geofence is not changed")
    if not _may_change(current["site_id"], allowed):
        raise HTTPException(403, "You may change geofences of your own sites only")
    sent = body.model_fields_set
    changes: dict = {}
    if "name" in sent:
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(422, "A geofence needs a name")
        changes["name"] = name
    if "description" in sent:
        changes["description"] = body.description
    site = str(current["site_id"]) if current["site_id"] else None
    if "site_id" in sent:
        site = str(body.site_id) if body.site_id else None
        if not _may_change(site, allowed):
            raise HTTPException(403, "You may move geofences to your own sites only")
        if site is not None and (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"), {"s": site})).first() is None:
            raise HTTPException(422, "That site is not this organisation's")
        changes["site_id"] = site
    if body.from_site:
        if site is None:
            raise HTTPException(422, "Starting from a site's boundary needs the site")
        changes.update(_shape_columns(await _site_shape(db, site)))
    elif body.shape is not None:
        changes.update(_shape_columns(body.shape.model_dump()))
    if "is_active" in sent and body.is_active is not None:
        changes["is_active"] = body.is_active
    groups = await _check_groups(db, body.group_ids, allowed) if body.group_ids is not None else None
    rules = await _check_rules(db, body.rules) if body.rules is not None else None
    tenant_id, gid = str(token.tenant_id), str(geofence_id)
    if changes:
        sets = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k == "polygon" else
                         (f"{k} = CAST(:{k} AS uuid)" if k == "site_id" else f"{k} = :{k}") for k in changes)
        await db.execute(text(f"UPDATE geofences SET {sets}, updated_at = now() WHERE id = CAST(:id AS uuid)"),
                         {**changes, "id": gid})
    if groups is not None:
        await _write_groups(db, tenant_id, gid, groups)
    if rules is not None:
        await _write_rules(db, tenant_id, gid, rules)
    if "polygon" in changes or "site_id" in changes or groups is not None or changes.get("is_active") is False:
        await _forget_sides(db, gid)
    await write_audit_log(db, tenant_id=tenant_id, user_id=str(token.user_id), action="fleet.geofence.changed",
                          resource_type="geofence", resource_id=gid, ip_address=_client_ip(request),
                          detail={"changed": sorted({*changes} - {"center_lat", "center_lon", "radius_m"}
                                                    | ({"groups"} if groups is not None else set())
                                                    | ({"rules"} if rules is not None else set()))})
    out = await _visible(db, geofence_id, allowed)  # before the commit: the tenant setting ends with it
    await db.commit()
    return out


@router.post("/geofences/{geofence_id}/archive", dependencies=_MANAGE)
async def archive_geofence(
    geofence_id: uuid.UUID,
    body: ArchiveIn,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    current = await _visible(db, geofence_id, allowed)
    if current["archived"]:
        raise HTTPException(409, "It is archived already")
    if not _may_change(current["site_id"], allowed):
        raise HTTPException(403, "You may archive geofences of your own sites only")
    await db.execute(text("""
        UPDATE geofences SET is_active = FALSE, archived_at = now(), archived_by_user_id = CAST(:u AS uuid),
                             archive_reason = :reason, updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"u": str(token.user_id), "reason": body.reason.strip(), "id": str(geofence_id)})
    await _forget_sides(db, str(geofence_id))
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id), action="fleet.geofence.archived",
                          resource_type="geofence", resource_id=str(geofence_id), ip_address=_client_ip(request),
                          detail={"name": current["name"], "reason": body.reason.strip()})
    out = await _visible(db, geofence_id, allowed)  # before the commit: the tenant setting ends with it
    await db.commit()
    return out


# ── events ───────────────────────────────────────────────────────────────────

@router.get("/geofence-events", dependencies=_READ)
async def geofence_events(
    geofence_id: uuid.UUID | None = None,
    vehicle_id: uuid.UUID | None = None,
    hours: int = Query(24, ge=1, le=24 * 31),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {"since": datetime.now(timezone.utc) - timedelta(hours=hours), "limit": limit}
    clauses = ["e.occurred_at >= :since"]
    scope = site_scope_clause(allowed, "v.site_id", params)
    if scope:
        clauses.append(scope)
    if geofence_id is not None:
        clauses.append("e.geofence_id = CAST(:g AS uuid)")
        params["g"] = str(geofence_id)
    if vehicle_id is not None:
        clauses.append("e.vehicle_id = CAST(:v AS uuid)")
        params["v"] = str(vehicle_id)
    rows = (await db.execute(text(f"""
        SELECT e.id, e.event_type, e.occurred_at, e.lat, e.lon, e.speed, e.accuracy, e.between_start, e.between_end,
               e.alert_id, e.detail, e.vehicle_id, v.name AS vehicle_name, v.plate_number, e.geofence_id,
               g.name AS geofence_name, a.status AS alert_status, a.severity AS alert_severity
          FROM geofence_events e
          JOIN vehicles v ON v.id = e.vehicle_id
          JOIN geofences g ON g.id = e.geofence_id
          LEFT JOIN alerts a ON a.id = e.alert_id
         WHERE {' AND '.join(clauses)}
         ORDER BY e.occurred_at DESC
         LIMIT :limit
    """), params)).mappings().all()
    return {"items": [{**dict(r), "detail": _json(r["detail"])} for r in rows]}


# ── when a vehicle stops reporting ───────────────────────────────────────────

@router.get("/alert-settings", dependencies=_READ)
async def read_alert_settings(db: AsyncSession = Depends(get_db_with_tenant)):
    return await fa.settings_of(db)


@router.put("/alert-settings", dependencies=_ALERTS)
async def set_alert_settings(
    body: AlertSettingsIn,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    values = {"offline": body.offline.model_dump(), "stale": body.stale.model_dump()}
    for key, name in ((fa.OFFLINE_KEY, "offline"), (fa.STALE_KEY, "stale")):
        await db.execute(text("""
            INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id)
            VALUES (:t, :k, CAST(:v AS jsonb), CAST(:u AS uuid))
            ON CONFLICT (tenant_id, setting_key)
            DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_by_user_id = EXCLUDED.updated_by_user_id,
                          updated_at = now()
        """), {"t": str(token.tenant_id), "k": key, "v": json.dumps(values[name]), "u": str(token.user_id)})
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="fleet.alert_settings.changed", resource_type="tenant_settings",
                          ip_address=_client_ip(request), detail=values)
    await db.commit()
    return values

