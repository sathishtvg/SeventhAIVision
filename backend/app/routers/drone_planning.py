"""Drone mission planning: zones, security profiles, routes, missions, schedules.

Everything here shapes a flight; nothing here starts one. Execution — pre-flight
checks, launching, aborting — is Phase 4 and needs the provider layer.

WHO MAY DO WHAT. Reading needs drone:read. Creating needs drone:mission:create;
changing or removing needs drone:mission:update. An Operator can fly and abort a
mission but cannot redraw the route it flies.

EVERYTHING BELONGS TO A SITE, AND THE PIECES MUST AGREE. A mission, its route,
its drone and every zone a waypoint names must be on the same site. A route
drawn over Factory A flown by Factory B's drone is not a configuration anyone
meant; it is refused with a reason rather than left for pre-flight to trip on.

A RUNNING FLIGHT IS NEVER CHANGED BY AN EDIT. Sessions copy their route, zones
and rules when they start (migration 0123), so editing or deleting anything here
changes future flights only.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import paginate
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.drone_module import (
    enforce_mission_limit, enforce_site_limit, require_drone_module,
)
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids
from app.dependencies.tenant import get_db_with_tenant
from app.services import drone_geometry as geo
from app.services import drone_schedule as sched
from app.services.drone_access import (
    assert_site_visible, audit, scope_sql, site_or_404, unique_violation,
)

router = APIRouter(prefix="/api/v1", tags=["drone-planning"])

_READ = Depends(require_permission("drone:read"))
_CREATE = Depends(require_permission("drone:mission:create"))
_UPDATE = Depends(require_permission("drone:mission:update"))
_LICENSED = Depends(require_drone_module)

ZoneType = Literal["NORMAL", "RESTRICTED", "CRITICAL", "VEHICLE_RESTRICTED",
                   "PERSON_RESTRICTED", "NO_ENTRY", "SPECIAL_INSPECTION"]
Shape = Literal["POLYGON", "RECTANGLE", "CIRCLE"]
Risk = Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
AlertPolicy = Literal["NONE", "ALERT", "INCIDENT"]
AiModule = Literal["lpr", "face", "intrusion", "ppe", "crowd", "fire_smoke", "weapon",
                   "behavior", "tampering", "abandoned", "fall"]
SyncMode = Literal["central", "local_only", "incident_only", "scheduled", "manual"]
ScheduleType = Literal["ONCE", "DAILY", "WEEKLY", "SELECTED_DAYS", "SPECIFIC_DATE"]

IN_FLIGHT_SQL = "'PRECHECK','READY','LAUNCHING','ACTIVE','PAUSED','EVENT_DETECTED','RETURNING'"


def _uuid(v: Any) -> str | None:
    return str(v) if v is not None else None


# ═════════════════════════════════════════════════════════════════════════════
# Security zones
# ═════════════════════════════════════════════════════════════════════════════

class ZoneBase(BaseModel):
    description: str | None = Field(None, max_length=2000)
    polygon: list[Any] | None = None
    center_latitude: float | None = None
    center_longitude: float | None = None
    radius_m: float | None = None
    active_from: time | None = None
    active_to: time | None = None
    active_weekdays: list[int] | None = None
    allowed_user_ids: list[uuid.UUID] | None = None
    allowed_role_ids: list[int] | None = None
    allowed_vehicle_plates: list[str] | None = None
    detection_threshold: float | None = Field(None, ge=0, le=1)
    is_active: bool | None = None

    @field_validator("allowed_vehicle_plates")
    @classmethod
    def _plates(cls, v):
        if v is None:
            return v
        # Stored as a plate reader would report it, so a comparison with an LPR
        # read is exact rather than case- and space-sensitive.
        cleaned = sorted({p.replace(" ", "").upper() for p in v if p and p.strip()})
        if any(len(p) > 20 for p in cleaned):
            raise ValueError("A vehicle plate is at most 20 characters.")
        return cleaned


class ZoneCreate(ZoneBase):
    site_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    zone_type: ZoneType = "NORMAL"
    shape: Shape = "POLYGON"
    severity: Risk = "MEDIUM"
    alert_policy: AlertPolicy = "ALERT"


class ZoneUpdate(ZoneBase):
    name: str | None = Field(None, min_length=1, max_length=120)
    zone_type: ZoneType | None = None
    shape: Shape | None = None
    severity: Risk | None = None
    alert_policy: AlertPolicy | None = None


async def _check_zone_people(db: AsyncSession, user_ids: list[uuid.UUID] | None,
                             role_ids: list[int] | None) -> None:
    if user_ids:
        found = (await db.execute(text(
            "SELECT count(*) FROM users WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": list({u for u in user_ids})})).scalar()
        if found != len(set(user_ids)):
            raise HTTPException(422, "Some allowed users do not exist in this organisation.")
    if role_ids:
        found = (await db.execute(text(
            "SELECT count(*) FROM roles WHERE id = ANY(CAST(:ids AS int[]))"),
            {"ids": list(set(role_ids))})).scalar()
        if found != len(set(role_ids)):
            raise HTTPException(422, "Some allowed roles do not exist.")


def _check_window(active_from: time | None, active_to: time | None,
                  weekdays: list[int] | None) -> None:
    if (active_from is None) != (active_to is None):
        raise HTTPException(422, "Give both ends of the active window, or neither. "
                                 "A window ending before it starts runs overnight.")
    if weekdays and any(d not in range(7) for d in weekdays):
        raise HTTPException(422, "Weekdays are numbered 0 (Monday) to 6 (Sunday).")


async def _zone_or_404(db: AsyncSession, zone_id: uuid.UUID, allowed) -> dict:
    row = (await db.execute(text("""
        SELECT z.*, s.name AS site_name FROM drone_security_zones z
          JOIN sites s ON s.id = z.site_id WHERE z.id = CAST(:id AS uuid)
    """), {"id": str(zone_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Security zone not found")
    assert_site_visible(allowed, row["site_id"], "Security zone")
    return dict(row)


@router.get("/drone-zones", dependencies=[_READ])
async def list_zones(
    site_id: uuid.UUID | None = Query(None),
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {"site": _uuid(site_id), "active": is_active}
    scope = scope_sql(allowed, "z.site_id", params)
    rows = (await db.execute(text(f"""
        SELECT z.*, s.name AS site_name FROM drone_security_zones z
          JOIN sites s ON s.id = z.site_id
         WHERE (CAST(:site AS uuid) IS NULL OR z.site_id = CAST(:site AS uuid))
           AND (CAST(:active AS boolean) IS NULL OR z.is_active = CAST(:active AS boolean))
           {scope}
         ORDER BY s.name, z.name
    """), params)).mappings().all()
    return [dict(r) for r in rows]


@router.post("/drone-zones", status_code=status.HTTP_201_CREATED, dependencies=[_CREATE, _LICENSED])
async def create_zone(
    body: ZoneCreate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    await site_or_404(db, body.site_id, allowed)
    try:
        g = geo.normalize_zone(body.shape, body.polygon, body.center_latitude,
                               body.center_longitude, body.radius_m)
    except geo.GeometryError as exc:
        raise HTTPException(422, str(exc)) from exc
    _check_window(body.active_from, body.active_to, body.active_weekdays)
    await _check_zone_people(db, body.allowed_user_ids, body.allowed_role_ids)
    try:
        row = (await db.execute(text("""
            INSERT INTO drone_security_zones
                (tenant_id, site_id, name, description, zone_type, shape, polygon,
                 center_latitude, center_longitude, radius_m, severity, active_from, active_to,
                 active_weekdays, allowed_user_ids, allowed_role_ids, allowed_vehicle_plates,
                 detection_threshold, alert_policy, is_active, created_by_user_id, updated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:site AS uuid), :name, :desc,
                    :ztype, :shape, CAST(:polygon AS jsonb), :clat, :clng, :radius, :sev, :af, :at,
                    CAST(:wd AS smallint[]), CAST(:uids AS uuid[]), CAST(:rids AS smallint[]),
                    CAST(:plates AS text[]), :thr, :policy, :active, CAST(:by AS uuid), CAST(:by AS uuid))
            RETURNING id
        """), {
            "site": str(body.site_id), "name": body.name, "desc": body.description,
            "ztype": body.zone_type, "shape": body.shape,
            "polygon": json.dumps(g["polygon"]) if g["polygon"] is not None else None,
            "clat": g["center_latitude"], "clng": g["center_longitude"], "radius": g["radius_m"],
            "sev": body.severity, "af": body.active_from, "at": body.active_to,
            "wd": body.active_weekdays, "uids": body.allowed_user_ids or [],
            "rids": body.allowed_role_ids or [], "plates": body.allowed_vehicle_plates or [],
            "thr": body.detection_threshold, "policy": body.alert_policy,
            "active": True if body.is_active is None else body.is_active, "by": token.user_id,
        })).first()
    except IntegrityError as exc:
        if unique_violation(exc, "uq_dsz_name"):
            raise HTTPException(409, "This site already has a zone with that name.") from exc
        raise
    await audit(db, request, token, "drone.zone.create", "drone_security_zone", row[0],
                {"name": body.name, "zone_type": body.zone_type, "site_id": str(body.site_id)})
    result = await _zone_or_404(db, row[0], allowed)
    await db.commit()
    return result


@router.get("/drone-zones/{zone_id}", dependencies=[_READ])
async def get_zone(zone_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                   allowed: list[str] | None = Depends(get_allowed_site_ids)):
    return await _zone_or_404(db, zone_id, allowed)


@router.put("/drone-zones/{zone_id}", dependencies=[_UPDATE, _LICENSED])
async def update_zone(
    zone_id: uuid.UUID, body: ZoneUpdate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The site is fixed. Moving a zone to another site would silently detach
    it from every waypoint that names it."""
    zone = await _zone_or_404(db, zone_id, allowed)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "No fields to update")
    for k in ("name", "zone_type", "shape", "severity", "alert_policy", "is_active"):
        if k in changes and changes[k] is None:
            raise HTTPException(422, f"{k} cannot be empty.")

    merged = {**zone, **changes}
    geometry_keys = {"shape", "polygon", "center_latitude", "center_longitude", "radius_m"}
    if geometry_keys & set(changes):
        try:
            g = geo.normalize_zone(merged["shape"], merged.get("polygon"), merged.get("center_latitude"),
                                   merged.get("center_longitude"),
                                   float(merged["radius_m"]) if merged.get("radius_m") is not None else None)
        except geo.GeometryError as exc:
            raise HTTPException(422, str(exc)) from exc
        changes.update({"polygon": g["polygon"], "center_latitude": g["center_latitude"],
                        "center_longitude": g["center_longitude"], "radius_m": g["radius_m"]})
    _check_window(merged.get("active_from"), merged.get("active_to"), merged.get("active_weekdays"))
    await _check_zone_people(db, changes.get("allowed_user_ids"), changes.get("allowed_role_ids"))

    casts = {"polygon": "jsonb", "active_weekdays": "smallint[]", "allowed_user_ids": "uuid[]",
             "allowed_role_ids": "smallint[]", "allowed_vehicle_plates": "text[]"}
    params: dict = {"id": str(zone_id), "by": token.user_id}
    sets = []
    for k, v in changes.items():
        if k == "polygon":
            v = json.dumps(v) if v is not None else None
        elif k in ("allowed_user_ids", "allowed_role_ids", "allowed_vehicle_plates") and v is None:
            v = []
        params[k] = v
        sets.append(f"{k} = CAST(:{k} AS {casts[k]})" if k in casts else f"{k} = :{k}")
    try:
        await db.execute(text(
            f"UPDATE drone_security_zones SET {', '.join(sets)}, "
            " updated_by_user_id = CAST(:by AS uuid), updated_at = now() WHERE id = CAST(:id AS uuid)"),
            params)
    except IntegrityError as exc:
        if unique_violation(exc, "uq_dsz_name"):
            raise HTTPException(409, "This site already has a zone with that name.") from exc
        raise
    await audit(db, request, token, "drone.zone.update", "drone_security_zone", zone_id,
                {"changed": sorted(changes)})
    result = await _zone_or_404(db, zone_id, allowed)
    await db.commit()
    return result


@router.delete("/drone-zones/{zone_id}", dependencies=[_UPDATE, _LICENSED])
async def delete_zone(
    zone_id: uuid.UUID, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Waypoints that named the zone keep their place and lose the zone
    (ON DELETE SET NULL). Past events keep the zone's name and type."""
    zone = await _zone_or_404(db, zone_id, allowed)
    await db.execute(text("DELETE FROM drone_security_zones WHERE id = CAST(:id AS uuid)"),
                     {"id": str(zone_id)})
    await audit(db, request, token, "drone.zone.delete", "drone_security_zone", zone_id,
                {"name": zone["name"]})
    await db.commit()
    return {"deleted": str(zone_id)}


# ═════════════════════════════════════════════════════════════════════════════
# Security profiles
# ═════════════════════════════════════════════════════════════════════════════

class RuleIn(BaseModel):
    module_type: AiModule
    is_enabled: bool = True
    min_confidence: float | None = Field(None, ge=0, le=1)
    base_severity: Risk = "MEDIUM"
    incident_risk_level: Risk = "HIGH"


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    min_confidence: float = Field(0.5, ge=0, le=1)
    verify_min_seconds: int = Field(3, ge=0, le=600)
    is_active: bool = True
    rules: list[RuleIn] = Field(default_factory=list, max_length=11)


class ProfileUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    min_confidence: float | None = Field(None, ge=0, le=1)
    verify_min_seconds: int | None = Field(None, ge=0, le=600)
    is_active: bool | None = None
    rules: list[RuleIn] | None = Field(None, max_length=11)


def _check_rules(rules: list[RuleIn]) -> None:
    seen = [r.module_type for r in rules]
    dupes = sorted({m for m in seen if seen.count(m) > 1})
    if dupes:
        raise HTTPException(422, f"Each AI module may appear once in a profile: {', '.join(dupes)}.")


async def _write_rules(db: AsyncSession, profile_id, rules: list[RuleIn]) -> None:
    await db.execute(text("DELETE FROM drone_profile_rules WHERE profile_id = CAST(:p AS uuid)"),
                     {"p": str(profile_id)})
    for r in rules:
        await db.execute(text("""
            INSERT INTO drone_profile_rules
                (tenant_id, profile_id, module_type, is_enabled, min_confidence,
                 base_severity, incident_risk_level)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:p AS uuid), :m, :e, :c, :s, :i)
        """), {"p": str(profile_id), "m": r.module_type, "e": r.is_enabled, "c": r.min_confidence,
               "s": r.base_severity, "i": r.incident_risk_level})


async def _profile_or_404(db: AsyncSession, profile_id: uuid.UUID) -> dict:
    row = (await db.execute(text(
        "SELECT * FROM drone_security_profiles WHERE id = CAST(:id AS uuid)"),
        {"id": str(profile_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Security profile not found")
    rules = (await db.execute(text(
        "SELECT * FROM drone_profile_rules WHERE profile_id = CAST(:id AS uuid) ORDER BY module_type"),
        {"id": str(profile_id)})).mappings().all()
    return {**dict(row), "rules": [dict(r) for r in rules]}


@router.get("/drone-security-profiles", dependencies=[_READ])
async def list_profiles(db: AsyncSession = Depends(get_db_with_tenant)):
    rows = (await db.execute(text("""
        SELECT p.*,
               (SELECT count(*) FROM drone_profile_rules r WHERE r.profile_id = p.id AND r.is_enabled)
                   AS enabled_rule_count,
               (SELECT count(*) FROM drone_missions m WHERE m.security_profile_id = p.id)
                   AS mission_count
          FROM drone_security_profiles p ORDER BY p.name
    """))).mappings().all()
    return [dict(r) for r in rows]


@router.post("/drone-security-profiles", status_code=status.HTTP_201_CREATED,
             dependencies=[_CREATE, _LICENSED])
async def create_profile(
    body: ProfileCreate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    _check_rules(body.rules)
    try:
        row = (await db.execute(text("""
            INSERT INTO drone_security_profiles
                (tenant_id, name, description, min_confidence, verify_min_seconds, is_active,
                 created_by_user_id, updated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, :name, :desc, :conf, :verify, :active,
                    CAST(:by AS uuid), CAST(:by AS uuid))
            RETURNING id
        """), {"name": body.name, "desc": body.description, "conf": body.min_confidence,
               "verify": body.verify_min_seconds, "active": body.is_active,
               "by": token.user_id})).first()
    except IntegrityError as exc:
        if unique_violation(exc, "uq_dsp_name"):
            raise HTTPException(409, "A security profile with this name already exists.") from exc
        raise
    await _write_rules(db, row[0], body.rules)
    await audit(db, request, token, "drone.profile.create", "drone_security_profile", row[0],
                {"name": body.name, "modules": [r.module_type for r in body.rules]})
    result = await _profile_or_404(db, row[0])
    await db.commit()
    return result


@router.get("/drone-security-profiles/{profile_id}", dependencies=[_READ])
async def get_profile(profile_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    return await _profile_or_404(db, profile_id)


@router.put("/drone-security-profiles/{profile_id}", dependencies=[_UPDATE, _LICENSED])
async def update_profile(
    profile_id: uuid.UUID, body: ProfileUpdate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """`rules`, when sent, replaces the whole rule set."""
    await _profile_or_404(db, profile_id)
    changes = body.model_dump(exclude_unset=True)
    rules = changes.pop("rules", None)
    if not changes and rules is None:
        raise HTTPException(422, "No fields to update")
    for k in ("name", "min_confidence", "verify_min_seconds", "is_active"):
        if k in changes and changes[k] is None:
            raise HTTPException(422, f"{k} cannot be empty.")
    if changes:
        sets = ", ".join(f"{k} = :{k}" for k in changes)
        try:
            await db.execute(text(
                f"UPDATE drone_security_profiles SET {sets}, updated_by_user_id = CAST(:by AS uuid), "
                " updated_at = now() WHERE id = CAST(:id AS uuid)"),
                {**changes, "id": str(profile_id), "by": token.user_id})
        except IntegrityError as exc:
            if unique_violation(exc, "uq_dsp_name"):
                raise HTTPException(409, "A security profile with this name already exists.") from exc
            raise
    if rules is not None:
        parsed = [RuleIn(**r) for r in rules]
        _check_rules(parsed)
        await _write_rules(db, profile_id, parsed)
    await audit(db, request, token, "drone.profile.update", "drone_security_profile", profile_id,
                {"changed": sorted(changes) + (["rules"] if rules is not None else [])})
    result = await _profile_or_404(db, profile_id)
    await db.commit()
    return result


@router.delete("/drone-security-profiles/{profile_id}", dependencies=[_UPDATE, _LICENSED])
async def delete_profile(
    profile_id: uuid.UUID, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    profile = await _profile_or_404(db, profile_id)
    in_use = (await db.execute(text(
        "SELECT count(*) FROM drone_missions WHERE security_profile_id = CAST(:id AS uuid) AND enabled"),
        {"id": str(profile_id)})).scalar()
    if in_use:
        raise HTTPException(409, f"{in_use} enabled mission(s) use this profile. Change them first.")
    await db.execute(text("DELETE FROM drone_security_profiles WHERE id = CAST(:id AS uuid)"),
                     {"id": str(profile_id)})
    await audit(db, request, token, "drone.profile.delete", "drone_security_profile", profile_id,
                {"name": profile["name"]})
    await db.commit()
    return {"deleted": str(profile_id)}


# ═════════════════════════════════════════════════════════════════════════════
# Routes and waypoints
# ═════════════════════════════════════════════════════════════════════════════

class WaypointIn(BaseModel):
    name: str | None = Field(None, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float | None = Field(None, gt=0, le=1000)
    speed_mps: float | None = Field(None, gt=0, le=50)
    heading_deg: float | None = Field(None, ge=0, lt=360)
    hover_seconds: int = Field(0, ge=0, le=3600)
    gimbal_pitch_deg: float | None = Field(None, ge=-90, le=30)
    gimbal_yaw_deg: float | None = Field(None, ge=-360, le=360)
    zoom: float | None = Field(None, ge=1, le=200)
    observe_seconds: int = Field(0, ge=0, le=3600)
    security_zone_id: uuid.UUID | None = None
    security_profile_id: uuid.UUID | None = None
    snapshot_required: bool = False
    notes: str | None = Field(None, max_length=2000)


class RouteCreate(BaseModel):
    site_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    base_latitude: float | None = Field(None, ge=-90, le=90)
    base_longitude: float | None = Field(None, ge=-180, le=180)
    base_altitude_m: float = Field(0, ge=-500, le=9000)
    default_altitude_m: float = Field(40, gt=0, le=1000)
    default_speed_mps: float = Field(5, gt=0, le=50)
    return_to_base: bool = True
    is_active: bool = True
    waypoints: list[WaypointIn] = Field(default_factory=list, max_length=200)


class RouteUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    base_latitude: float | None = Field(None, ge=-90, le=90)
    base_longitude: float | None = Field(None, ge=-180, le=180)
    base_altitude_m: float | None = Field(None, ge=-500, le=9000)
    default_altitude_m: float | None = Field(None, gt=0, le=1000)
    default_speed_mps: float | None = Field(None, gt=0, le=50)
    return_to_base: bool | None = None
    is_active: bool | None = None


class WaypointsReplace(BaseModel):
    waypoints: list[WaypointIn] = Field(max_length=200)


async def _check_waypoint_refs(db: AsyncSession, site_id, waypoints: list[WaypointIn]) -> None:
    zone_ids = {w.security_zone_id for w in waypoints if w.security_zone_id}
    if zone_ids:
        rows = (await db.execute(text(
            "SELECT id, site_id FROM drone_security_zones WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": list(zone_ids)})).all()
        if len(rows) != len(zone_ids):
            raise HTTPException(422, "A waypoint names a security zone that does not exist.")
        if any(str(r[1]) != str(site_id) for r in rows):
            raise HTTPException(422, "A waypoint names a security zone on another site.")
    profile_ids = {w.security_profile_id for w in waypoints if w.security_profile_id}
    if profile_ids:
        n = (await db.execute(text(
            "SELECT count(*) FROM drone_security_profiles WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": list(profile_ids)})).scalar()
        if n != len(profile_ids):
            raise HTTPException(422, "A waypoint names a security profile that does not exist.")


async def _insert_waypoints(db: AsyncSession, route_id, waypoints: list[WaypointIn]) -> None:
    """Sequence is the list order, from 1. The client never numbers them, so it
    can never leave a gap or a duplicate."""
    for seq, w in enumerate(waypoints, start=1):
        await db.execute(text("""
            INSERT INTO drone_waypoints
                (tenant_id, route_id, sequence, name, latitude, longitude, altitude_m, speed_mps,
                 heading_deg, hover_seconds, gimbal_pitch_deg, gimbal_yaw_deg, zoom, observe_seconds,
                 security_zone_id, security_profile_id, snapshot_required, notes)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:r AS uuid), :seq, :name, :lat, :lng,
                    :alt, :speed, :heading, :hover, :pitch, :yaw, :zoom, :observe,
                    CAST(:zone AS uuid), CAST(:profile AS uuid), :snap, :notes)
        """), {"r": str(route_id), "seq": seq, "name": w.name, "lat": w.latitude, "lng": w.longitude,
               "alt": w.altitude_m, "speed": w.speed_mps, "heading": w.heading_deg,
               "hover": w.hover_seconds, "pitch": w.gimbal_pitch_deg, "yaw": w.gimbal_yaw_deg,
               "zoom": w.zoom, "observe": w.observe_seconds, "zone": _uuid(w.security_zone_id),
               "profile": _uuid(w.security_profile_id), "snap": w.snapshot_required, "notes": w.notes})


async def _route_or_404(db: AsyncSession, route_id: uuid.UUID, allowed) -> dict:
    row = (await db.execute(text("""
        SELECT r.*, s.name AS site_name FROM drone_routes r
          JOIN sites s ON s.id = r.site_id WHERE r.id = CAST(:id AS uuid)
    """), {"id": str(route_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Route not found")
    assert_site_visible(allowed, row["site_id"], "Route")
    return dict(row)


async def _route_detail(db: AsyncSession, route_id: uuid.UUID, allowed) -> dict:
    route = await _route_or_404(db, route_id, allowed)
    waypoints = [dict(w) for w in (await db.execute(text("""
        SELECT w.*, z.name AS security_zone_name FROM drone_waypoints w
          LEFT JOIN drone_security_zones z ON z.id = w.security_zone_id
         WHERE w.route_id = CAST(:id AS uuid) ORDER BY w.sequence
    """), {"id": str(route_id)})).mappings().all()]
    site = (await db.execute(text(
        "SELECT latitude, longitude, geofence_radius_meters, geofence_polygon FROM sites "
        " WHERE id = CAST(:id AS uuid)"), {"id": str(route["site_id"])})).mappings().first()
    missions = (await db.execute(text(
        "SELECT id, name, enabled FROM drone_missions WHERE route_id = CAST(:id AS uuid) ORDER BY name"),
        {"id": str(route_id)})).mappings().all()
    base = (float(route["base_latitude"]), float(route["base_longitude"]))
    points = [(float(w["latitude"]), float(w["longitude"])) for w in waypoints]
    return {
        **route,
        "waypoints": waypoints,
        "summary": {
            "waypoint_count": len(waypoints),
            "length_m": round(geo.route_length_m(base, points, route["return_to_base"]), 1),
            # A warning for the planner, not a refusal: see drone_geometry.
            "outside_site_geofence": geo.waypoints_outside_site(dict(site or {}), waypoints),
        },
        "missions": [dict(m) for m in missions],
    }


@router.get("/drone-routes", dependencies=[_READ])
async def list_routes(
    site_id: uuid.UUID | None = Query(None),
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {"site": _uuid(site_id), "active": is_active}
    scope = scope_sql(allowed, "r.site_id", params)
    rows = (await db.execute(text(f"""
        SELECT r.*, s.name AS site_name,
               (SELECT count(*) FROM drone_waypoints w WHERE w.route_id = r.id) AS waypoint_count,
               (SELECT count(*) FROM drone_missions m WHERE m.route_id = r.id)  AS mission_count
          FROM drone_routes r JOIN sites s ON s.id = r.site_id
         WHERE (CAST(:site AS uuid) IS NULL OR r.site_id = CAST(:site AS uuid))
           AND (CAST(:active AS boolean) IS NULL OR r.is_active = CAST(:active AS boolean))
           {scope}
         ORDER BY s.name, r.name
    """), params)).mappings().all()
    return [dict(r) for r in rows]


@router.post("/drone-routes", status_code=status.HTTP_201_CREATED, dependencies=[_CREATE, _LICENSED])
async def create_route(
    body: RouteCreate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    site = await site_or_404(db, body.site_id, allowed)
    base_lat = body.base_latitude if body.base_latitude is not None else site["latitude"]
    base_lng = body.base_longitude if body.base_longitude is not None else site["longitude"]
    if base_lat is None or base_lng is None:
        raise HTTPException(422, "Set the base point: this site has no coordinates to default to.")
    await _check_waypoint_refs(db, body.site_id, body.waypoints)
    try:
        row = (await db.execute(text("""
            INSERT INTO drone_routes
                (tenant_id, site_id, name, description, base_latitude, base_longitude, base_altitude_m,
                 default_altitude_m, default_speed_mps, return_to_base, is_active,
                 created_by_user_id, updated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:site AS uuid), :name, :desc,
                    :blat, :blng, :balt, :dalt, :dspeed, :rtb, :active, CAST(:by AS uuid), CAST(:by AS uuid))
            RETURNING id
        """), {"site": str(body.site_id), "name": body.name, "desc": body.description,
               "blat": base_lat, "blng": base_lng, "balt": body.base_altitude_m,
               "dalt": body.default_altitude_m, "dspeed": body.default_speed_mps,
               "rtb": body.return_to_base, "active": body.is_active, "by": token.user_id})).first()
    except IntegrityError as exc:
        if unique_violation(exc, "uq_droute_name"):
            raise HTTPException(409, "This site already has a route with that name.") from exc
        raise
    await _insert_waypoints(db, row[0], body.waypoints)
    await audit(db, request, token, "drone.route.create", "drone_route", row[0],
                {"name": body.name, "site_id": str(body.site_id), "waypoints": len(body.waypoints)})
    result = await _route_detail(db, row[0], allowed)
    await db.commit()
    return result


@router.get("/drone-routes/{route_id}", dependencies=[_READ])
async def get_route(route_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                    allowed: list[str] | None = Depends(get_allowed_site_ids)):
    return await _route_detail(db, route_id, allowed)


@router.put("/drone-routes/{route_id}", dependencies=[_UPDATE, _LICENSED])
async def update_route(
    route_id: uuid.UUID, body: RouteUpdate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The route's site is fixed; its waypoints change through
    PUT /drone-routes/{id}/waypoints."""
    await _route_or_404(db, route_id, allowed)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "No fields to update")
    for k in ("name", "base_latitude", "base_longitude", "base_altitude_m", "default_altitude_m",
              "default_speed_mps", "return_to_base", "is_active"):
        if k in changes and changes[k] is None:
            raise HTTPException(422, f"{k} cannot be empty.")
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    try:
        await db.execute(text(
            f"UPDATE drone_routes SET {sets}, updated_by_user_id = CAST(:by AS uuid), updated_at = now() "
            " WHERE id = CAST(:id AS uuid)"), {**changes, "id": str(route_id), "by": token.user_id})
    except IntegrityError as exc:
        if unique_violation(exc, "uq_droute_name"):
            raise HTTPException(409, "This site already has a route with that name.") from exc
        raise
    await audit(db, request, token, "drone.route.update", "drone_route", route_id,
                {"changed": sorted(changes)})
    result = await _route_detail(db, route_id, allowed)
    await db.commit()
    return result


@router.put("/drone-routes/{route_id}/waypoints", dependencies=[_UPDATE, _LICENSED])
async def replace_waypoints(
    route_id: uuid.UUID, body: WaypointsReplace, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Replace the whole path in one step: what the designer shows is what is
    stored, in that order. A flight already under way flies its own copy."""
    route = await _route_or_404(db, route_id, allowed)
    await _check_waypoint_refs(db, route["site_id"], body.waypoints)
    await db.execute(text("DELETE FROM drone_waypoints WHERE route_id = CAST(:id AS uuid)"),
                     {"id": str(route_id)})
    await _insert_waypoints(db, route_id, body.waypoints)
    await db.execute(text("UPDATE drone_routes SET updated_at = now(), updated_by_user_id = CAST(:by AS uuid) "
                          " WHERE id = CAST(:id AS uuid)"), {"id": str(route_id), "by": token.user_id})
    await audit(db, request, token, "drone.route.waypoints", "drone_route", route_id,
                {"waypoints": len(body.waypoints)})
    result = await _route_detail(db, route_id, allowed)
    await db.commit()
    return result


@router.delete("/drone-routes/{route_id}", dependencies=[_UPDATE, _LICENSED])
async def delete_route(
    route_id: uuid.UUID, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    route = await _route_or_404(db, route_id, allowed)
    in_use = (await db.execute(text(
        "SELECT count(*) FROM drone_missions WHERE route_id = CAST(:id AS uuid) AND enabled"),
        {"id": str(route_id)})).scalar()
    if in_use:
        raise HTTPException(409, f"{in_use} enabled mission(s) fly this route. Change them first.")
    await db.execute(text("DELETE FROM drone_routes WHERE id = CAST(:id AS uuid)"), {"id": str(route_id)})
    await audit(db, request, token, "drone.route.delete", "drone_route", route_id, {"name": route["name"]})
    await db.commit()
    return {"deleted": str(route_id)}


# ═════════════════════════════════════════════════════════════════════════════
# Missions
# ═════════════════════════════════════════════════════════════════════════════

class MissionCreate(BaseModel):
    site_id: uuid.UUID
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(None, max_length=2000)
    drone_id: uuid.UUID | None = None
    route_id: uuid.UUID | None = None
    security_profile_id: uuid.UUID | None = None
    recording_sync_mode: SyncMode | None = None
    priority: int = Field(3, ge=1, le=5)
    min_battery_pct: int = Field(30, ge=0, le=100)
    max_duration_minutes: int | None = Field(None, ge=1, le=1440)
    enabled: bool = True


class MissionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=160)
    description: str | None = Field(None, max_length=2000)
    drone_id: uuid.UUID | None = None
    route_id: uuid.UUID | None = None
    security_profile_id: uuid.UUID | None = None
    recording_sync_mode: SyncMode | None = None
    priority: int | None = Field(None, ge=1, le=5)
    min_battery_pct: int | None = Field(None, ge=0, le=100)
    max_duration_minutes: int | None = Field(None, ge=1, le=1440)
    enabled: bool | None = None


async def _check_mission_refs(db: AsyncSession, site_id, *, drone_id=None, route_id=None,
                              profile_id=None) -> None:
    if drone_id is not None:
        d = (await db.execute(text("SELECT site_id FROM drones WHERE id = CAST(:id AS uuid)"),
                              {"id": str(drone_id)})).first()
        if d is None:
            raise HTTPException(422, "Drone not found.")
        if d[0] is not None and str(d[0]) != str(site_id):
            raise HTTPException(422, "That drone belongs to another site.")
    if route_id is not None:
        r = (await db.execute(text("SELECT site_id FROM drone_routes WHERE id = CAST(:id AS uuid)"),
                              {"id": str(route_id)})).first()
        if r is None:
            raise HTTPException(422, "Route not found.")
        if str(r[0]) != str(site_id):
            raise HTTPException(422, "That route is drawn for another site.")
    if profile_id is not None:
        p = (await db.execute(text("SELECT 1 FROM drone_security_profiles WHERE id = CAST(:id AS uuid)"),
                              {"id": str(profile_id)})).first()
        if p is None:
            raise HTTPException(422, "Security profile not found.")


_MISSION_SELECT = f"""
    SELECT m.*, s.name AS site_name, d.name AS drone_name, d.status AS drone_status,
           r.name AS route_name, p.name AS profile_name,
           (SELECT count(*) FROM drone_schedules sc WHERE sc.mission_id = m.id) AS schedule_count,
           (SELECT ps.status FROM drone_patrol_sessions ps WHERE ps.mission_id = m.id
             ORDER BY ps.created_at DESC LIMIT 1) AS last_session_status,
           (SELECT ps.created_at FROM drone_patrol_sessions ps WHERE ps.mission_id = m.id
             ORDER BY ps.created_at DESC LIMIT 1) AS last_session_at,
           EXISTS (SELECT 1 FROM drone_patrol_sessions ps WHERE ps.mission_id = m.id
                    AND ps.status IN ({IN_FLIGHT_SQL})) AS in_flight
      FROM drone_missions m
      JOIN sites s ON s.id = m.site_id
      LEFT JOIN drones d ON d.id = m.drone_id
      LEFT JOIN drone_routes r ON r.id = m.route_id
      LEFT JOIN drone_security_profiles p ON p.id = m.security_profile_id
"""


def _run_json(s: sched.Schedule, at: datetime) -> dict:
    return {"utc": at, "local": at.astimezone(ZoneInfo(s.timezone)).isoformat(), "timezone": s.timezone}


async def _next_runs(db: AsyncSession, mission_ids: list[str], now: datetime) -> dict[str, dict | None]:
    """The soonest launch across each mission's enabled schedules."""
    out: dict[str, dict | None] = {m: None for m in mission_ids}
    if not mission_ids:
        return out
    rows = (await db.execute(text(
        "SELECT * FROM drone_schedules WHERE enabled AND mission_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [uuid.UUID(m) for m in mission_ids]})).mappings().all()
    best: dict[str, tuple[datetime, sched.Schedule]] = {}
    for r in rows:
        s = sched.Schedule.from_row(dict(r))
        nxt = sched.next_occurrences(s, now, 1)
        if nxt:
            key = str(r["mission_id"])
            if key not in best or nxt[0] < best[key][0]:
                best[key] = (nxt[0], s)
    for key, (at, s) in best.items():
        out[key] = _run_json(s, at)
    return out


async def _mission_or_404(db: AsyncSession, mission_id: uuid.UUID, allowed) -> dict:
    row = (await db.execute(text(_MISSION_SELECT + " WHERE m.id = CAST(:id AS uuid)"),
                            {"id": str(mission_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Mission not found")
    assert_site_visible(allowed, row["site_id"], "Mission")
    return dict(row)


@router.get("/drone-missions", dependencies=[_READ])
async def list_missions(
    site_id: uuid.UUID | None = Query(None),
    drone_id: uuid.UUID | None = Query(None),
    enabled: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {"site": _uuid(site_id), "drone": _uuid(drone_id), "enabled": enabled}
    scope = scope_sql(allowed, "m.site_id", params)
    where = f"""
         WHERE (CAST(:site AS uuid) IS NULL OR m.site_id = CAST(:site AS uuid))
           AND (CAST(:drone AS uuid) IS NULL OR m.drone_id = CAST(:drone AS uuid))
           AND (CAST(:enabled AS boolean) IS NULL OR m.enabled = CAST(:enabled AS boolean))
           {scope}"""
    page = await paginate(db, _MISSION_SELECT + where + " ORDER BY m.name LIMIT :limit OFFSET :offset",
                          "SELECT count(*) FROM drone_missions m" + where, params, limit, offset)
    runs = await _next_runs(db, [str(i["id"]) for i in page["items"]], datetime.now(timezone.utc))
    for item in page["items"]:
        item["next_run"] = runs.get(str(item["id"])) if item["enabled"] else None
    return page


@router.post("/drone-missions", status_code=status.HTTP_201_CREATED, dependencies=[_CREATE])
async def create_mission(
    body: MissionCreate, request: Request,
    ent: dict = Depends(require_drone_module),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    await site_or_404(db, body.site_id, allowed)
    await _check_mission_refs(db, body.site_id, drone_id=body.drone_id, route_id=body.route_id,
                              profile_id=body.security_profile_id)
    await enforce_mission_limit(db, ent)
    await enforce_site_limit(db, ent, body.site_id)
    try:
        row = (await db.execute(text("""
            INSERT INTO drone_missions
                (tenant_id, site_id, drone_id, route_id, security_profile_id, name, description,
                 recording_sync_mode, priority, min_battery_pct, max_duration_minutes, enabled,
                 created_by_user_id, updated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:site AS uuid), CAST(:drone AS uuid),
                    CAST(:route AS uuid), CAST(:profile AS uuid), :name, :desc, :sync, :prio, :batt,
                    :maxdur, :enabled, CAST(:by AS uuid), CAST(:by AS uuid))
            RETURNING id
        """), {"site": str(body.site_id), "drone": _uuid(body.drone_id), "route": _uuid(body.route_id),
               "profile": _uuid(body.security_profile_id), "name": body.name, "desc": body.description,
               "sync": body.recording_sync_mode, "prio": body.priority, "batt": body.min_battery_pct,
               "maxdur": body.max_duration_minutes, "enabled": body.enabled,
               "by": token.user_id})).first()
    except IntegrityError as exc:
        if unique_violation(exc, "uq_dmission_name"):
            raise HTTPException(409, "A mission with this name already exists.") from exc
        raise
    await audit(db, request, token, "drone.mission.create", "drone_mission", row[0],
                {"name": body.name, "site_id": str(body.site_id)})
    result = await get_mission(row[0], db, allowed)
    await db.commit()
    return result


@router.get("/drone-missions/{mission_id}", dependencies=[_READ])
async def get_mission(mission_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                      allowed: list[str] | None = Depends(get_allowed_site_ids)):
    mission = await _mission_or_404(db, mission_id, allowed)
    now = datetime.now(timezone.utc)
    schedules = []
    for r in (await db.execute(text(
            "SELECT * FROM drone_schedules WHERE mission_id = CAST(:id AS uuid) ORDER BY created_at"),
            {"id": str(mission_id)})).mappings().all():
        s = sched.Schedule.from_row(dict(r))
        runs = sched.next_occurrences(s, now, 5) if r["enabled"] else []
        schedules.append({**dict(r), "next_runs": [_run_json(s, at) for at in runs]})
    recent = (await db.execute(text("""
        SELECT id, session_number, status, triggered_by, scheduled_for, started_at, ended_at,
               event_count, incident_count, blocked_reason, failure_reason
          FROM drone_patrol_sessions WHERE mission_id = CAST(:id AS uuid)
         ORDER BY created_at DESC LIMIT 5
    """), {"id": str(mission_id)})).mappings().all()
    next_run = min((s["next_runs"][0] for s in schedules if s["next_runs"]),
                   key=lambda x: x["utc"], default=None) if mission["enabled"] else None
    return {**mission, "schedules": schedules, "recent_sessions": [dict(r) for r in recent],
            "next_run": next_run}


@router.put("/drone-missions/{mission_id}", dependencies=[_UPDATE, _LICENSED])
async def update_mission(
    mission_id: uuid.UUID, body: MissionUpdate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The site is fixed: a mission's route and zones are drawn for it."""
    mission = await _mission_or_404(db, mission_id, allowed)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "No fields to update")
    for k in ("name", "priority", "min_battery_pct", "enabled"):
        if k in changes and changes[k] is None:
            raise HTTPException(422, f"{k} cannot be empty.")
    await _check_mission_refs(db, mission["site_id"], drone_id=changes.get("drone_id"),
                              route_id=changes.get("route_id"),
                              profile_id=changes.get("security_profile_id"))
    casts = {"drone_id", "route_id", "security_profile_id"}
    params = {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in changes.items()}
    sets = ", ".join(f"{k} = CAST(:{k} AS uuid)" if k in casts else f"{k} = :{k}" for k in params)
    try:
        await db.execute(text(
            f"UPDATE drone_missions SET {sets}, updated_by_user_id = CAST(:by AS uuid), updated_at = now() "
            " WHERE id = CAST(:id AS uuid)"), {**params, "id": str(mission_id), "by": token.user_id})
    except IntegrityError as exc:
        if unique_violation(exc, "uq_dmission_name"):
            raise HTTPException(409, "A mission with this name already exists.") from exc
        raise
    await audit(db, request, token, "drone.mission.update", "drone_mission", mission_id,
                {"changed": sorted(changes)})
    result = await get_mission(mission_id, db, allowed)
    await db.commit()
    return result


@router.patch("/drone-missions/{mission_id}/enabled", dependencies=[_UPDATE, _LICENSED])
async def set_mission_enabled(
    mission_id: uuid.UUID, request: Request, enabled: bool = Query(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    await _mission_or_404(db, mission_id, allowed)
    await db.execute(text("UPDATE drone_missions SET enabled = :e, updated_at = now(), "
                          " updated_by_user_id = CAST(:by AS uuid) WHERE id = CAST(:id AS uuid)"),
                     {"e": enabled, "id": str(mission_id), "by": token.user_id})
    await audit(db, request, token, "drone.mission.enable" if enabled else "drone.mission.disable",
                "drone_mission", mission_id)
    await db.commit()
    return {"id": str(mission_id), "enabled": enabled}


@router.delete("/drone-missions/{mission_id}", dependencies=[_UPDATE, _LICENSED])
async def delete_mission(
    mission_id: uuid.UUID, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Past sessions stay, with the mission's name copied into them; only a
    mission that is flying right now cannot be deleted."""
    mission = await _mission_or_404(db, mission_id, allowed)
    if mission["in_flight"]:
        raise HTTPException(409, "This mission is flying. Abort it or let it finish first.")
    await db.execute(text("DELETE FROM drone_missions WHERE id = CAST(:id AS uuid)"), {"id": str(mission_id)})
    await audit(db, request, token, "drone.mission.delete", "drone_mission", mission_id,
                {"name": mission["name"]})
    await db.commit()
    return {"deleted": str(mission_id)}


# ═════════════════════════════════════════════════════════════════════════════
# Schedules
# ═════════════════════════════════════════════════════════════════════════════

class ScheduleIn(BaseModel):
    schedule_type: ScheduleType
    timezone: str = Field("Asia/Singapore", max_length=60)
    start_date: date
    end_date: date | None = None
    launch_time: time
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    specific_dates: list[date] = Field(default_factory=list, max_length=366)
    grace_minutes: int = Field(15, ge=0, le=720)
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    schedule_type: ScheduleType | None = None
    timezone: str | None = Field(None, max_length=60)
    start_date: date | None = None
    end_date: date | None = None
    launch_time: time | None = None
    weekdays: list[int] | None = Field(None, max_length=7)
    specific_dates: list[date] | None = Field(None, max_length=366)
    grace_minutes: int | None = Field(None, ge=0, le=720)
    enabled: bool | None = None


def _normalised_schedule(values: dict) -> tuple[sched.Schedule, dict]:
    """Validate, and keep only the fields the type uses: WEEKLY takes its day
    from start_date, so a stray weekdays list would only mislead a reader."""
    stype = values["schedule_type"]
    weekdays = sorted(set(values.get("weekdays") or [])) if stype == sched.SELECTED_DAYS else None
    dates = sorted(set(values.get("specific_dates") or [])) if stype == sched.SPECIFIC_DATE else None
    s = sched.Schedule(schedule_type=stype, timezone=values["timezone"],
                       start_date=values["start_date"], launch_time=values["launch_time"],
                       end_date=values.get("end_date"), weekdays=tuple(weekdays or ()),
                       specific_dates=tuple(dates or ()))
    problems = sched.problems(s)
    if problems:
        raise HTTPException(422, " ".join(problems))
    return s, {"weekdays": weekdays, "specific_dates": dates}


async def _schedule_with_mission(db: AsyncSession, schedule_id: uuid.UUID, allowed) -> dict:
    row = (await db.execute(text("""
        SELECT sc.*, m.site_id, m.name AS mission_name FROM drone_schedules sc
          JOIN drone_missions m ON m.id = sc.mission_id WHERE sc.id = CAST(:id AS uuid)
    """), {"id": str(schedule_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Schedule not found")
    assert_site_visible(allowed, row["site_id"], "Schedule")
    return dict(row)


def _schedule_json(row: dict, count: int = 5) -> dict:
    s = sched.Schedule.from_row(row)
    runs = sched.next_occurrences(s, datetime.now(timezone.utc), count) if row["enabled"] else []
    return {**row, "next_runs": [_run_json(s, at) for at in runs]}


@router.get("/drone-missions/{mission_id}/schedules", dependencies=[_READ])
async def list_schedules(mission_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                         allowed: list[str] | None = Depends(get_allowed_site_ids)):
    await _mission_or_404(db, mission_id, allowed)
    rows = (await db.execute(text(
        "SELECT * FROM drone_schedules WHERE mission_id = CAST(:id AS uuid) ORDER BY created_at"),
        {"id": str(mission_id)})).mappings().all()
    return [_schedule_json(dict(r)) for r in rows]


@router.post("/drone-missions/{mission_id}/schedules", status_code=status.HTTP_201_CREATED,
             dependencies=[_UPDATE, _LICENSED])
async def create_schedule(
    mission_id: uuid.UUID, body: ScheduleIn, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    await _mission_or_404(db, mission_id, allowed)
    _, kept = _normalised_schedule(body.model_dump())
    row = (await db.execute(text("""
        INSERT INTO drone_schedules
            (tenant_id, mission_id, schedule_type, timezone, start_date, end_date, launch_time,
             weekdays, specific_dates, grace_minutes, enabled, created_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:m AS uuid), :t, :tz, :sd, :ed, :lt,
                CAST(:wd AS smallint[]), CAST(:dates AS date[]), :grace, :enabled, CAST(:by AS uuid))
        RETURNING *
    """), {"m": str(mission_id), "t": body.schedule_type, "tz": body.timezone, "sd": body.start_date,
           "ed": body.end_date, "lt": body.launch_time, "wd": kept["weekdays"],
           "dates": kept["specific_dates"], "grace": body.grace_minutes, "enabled": body.enabled,
           "by": token.user_id})).mappings().first()
    await audit(db, request, token, "drone.schedule.create", "drone_schedule", row["id"],
                {"mission_id": str(mission_id), "schedule_type": body.schedule_type})
    await db.commit()
    return _schedule_json(dict(row))


@router.put("/drone-schedules/{schedule_id}", dependencies=[_UPDATE, _LICENSED])
async def update_schedule(
    schedule_id: uuid.UUID, body: ScheduleUpdate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    existing = await _schedule_with_mission(db, schedule_id, allowed)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "No fields to update")
    for k in ("schedule_type", "timezone", "start_date", "launch_time", "grace_minutes", "enabled"):
        if k in changes and changes[k] is None:
            raise HTTPException(422, f"{k} cannot be empty.")
    merged = {**existing, **changes}
    _, kept = _normalised_schedule(merged)
    row = (await db.execute(text("""
        UPDATE drone_schedules
           SET schedule_type = :t, timezone = :tz, start_date = :sd, end_date = :ed, launch_time = :lt,
               weekdays = CAST(:wd AS smallint[]), specific_dates = CAST(:dates AS date[]),
               grace_minutes = :grace, enabled = :enabled, updated_at = now()
         WHERE id = CAST(:id AS uuid)
        RETURNING *
    """), {"t": merged["schedule_type"], "tz": merged["timezone"], "sd": merged["start_date"],
           "ed": merged.get("end_date"), "lt": merged["launch_time"], "wd": kept["weekdays"],
           "dates": kept["specific_dates"], "grace": merged["grace_minutes"],
           "enabled": merged["enabled"], "id": str(schedule_id)})).mappings().first()
    await audit(db, request, token, "drone.schedule.update", "drone_schedule", schedule_id,
                {"changed": sorted(changes)})
    await db.commit()
    return _schedule_json(dict(row))


@router.delete("/drone-schedules/{schedule_id}", dependencies=[_UPDATE, _LICENSED])
async def delete_schedule(
    schedule_id: uuid.UUID, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Sessions it already created keep running and keep their history;
    schedule_id is set NULL on them."""
    existing = await _schedule_with_mission(db, schedule_id, allowed)
    await db.execute(text("DELETE FROM drone_schedules WHERE id = CAST(:id AS uuid)"),
                     {"id": str(schedule_id)})
    await audit(db, request, token, "drone.schedule.delete", "drone_schedule", schedule_id,
                {"mission_id": str(existing["mission_id"])})
    await db.commit()
    return {"deleted": str(schedule_id)}


@router.get("/drone-schedules/{schedule_id}/preview", dependencies=[_READ])
async def preview_schedule(
    schedule_id: uuid.UUID, count: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The next launches this schedule will make, in UTC and in its own zone —
    so a planner can see that "23:00 daily" means what they think it means."""
    row = await _schedule_with_mission(db, schedule_id, allowed)
    s = sched.Schedule.from_row(row)
    runs = sched.next_occurrences(s, datetime.now(timezone.utc), count)
    return {"schedule_id": str(schedule_id), "enabled": row["enabled"],
            "next_runs": [_run_json(s, at) for at in runs]}
