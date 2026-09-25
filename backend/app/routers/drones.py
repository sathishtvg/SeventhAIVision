"""Drone fleet: the aircraft, how they connect, their health and upkeep.

PERMISSIONS FOLLOW WHAT A PERSON DOES, NOT WHAT A SCREEN SHOWS. drone:read sees
the fleet. drone:create / update / delete change it. drone:maintenance:* records
upkeep. None of these flies anything — that is mission execution, Phase 4.

THE LICENCE GATES CHANGES, NOT HISTORY. Every endpoint that creates, changes or
removes something depends on require_drone_module; reads do not. See
dependencies/drone_module.py for why.

SECRETS ARE WRITE-ONLY. A provider's credentials are encrypted on the way in
and never come back out — responses say only whether one is set. An edge
gateway's access credential is shown once, at creation or rotation, and only its
SHA-256 is stored, the way refresh tokens are.

A DRONE WITH HISTORY IS DISABLED, NOT DELETED. Delete refuses once a drone has
flown; disabling keeps every session and event readable and frees the licence
slot.
"""
from __future__ import annotations

import hashlib
import json
import secrets as pysecrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.pagination import paginate
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.drone_module import (
    enforce_drone_limit, enforce_site_limit, entitlement_problem, load_entitlement,
    require_drone_module, usage,
)
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids
from app.dependencies.tenant import get_db_with_tenant
from app.services import drone_provider_registry as registry
from app.services.drone_geometry import GeometryError, normalize_zone
from app.services.drone_access import (
    assert_site_visible, audit, scope_sql, site_or_404, unique_violation,
)

router = APIRouter(prefix="/api/v1/drones", tags=["drones"])

_READ = Depends(require_permission("drone:read"))
_CREATE = Depends(require_permission("drone:create"))
_UPDATE = Depends(require_permission("drone:update"))
_DELETE = Depends(require_permission("drone:delete"))
_MAINT_READ = Depends(require_permission("drone:maintenance:read"))
_MAINT_MANAGE = Depends(require_permission("drone:maintenance:manage"))
_LICENSED = Depends(require_drone_module)

IN_FLIGHT = ("PRECHECK", "READY", "LAUNCHING", "ACTIVE", "PAUSED", "EVENT_DETECTED", "RETURNING")
_IN_FLIGHT_SQL = ", ".join(f"'{s}'" for s in IN_FLIGHT)
#: Below this a drone is flagged on the dashboard. Pre-flight has its own,
#: per-mission threshold (drone_missions.min_battery_pct).
LOW_BATTERY_PCT = 30
MAX_TELEMETRY_WINDOW = timedelta(hours=24)

MaintenanceType = Literal["SERVICE", "INSPECTION", "BATTERY_REPLACEMENT", "FIRMWARE_UPDATE",
                          "CAMERA_SERVICE", "REPAIR", "FAULT"]

_CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


# ── Schemas ──────────────────────────────────────────────────────────────────

class DroneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=40, pattern=_CODE_PATTERN)
    site_id: uuid.UUID | None = None
    provider_config_id: uuid.UUID | None = None
    edge_gateway_id: uuid.UUID | None = None
    #: The camera that stands for this drone's video (decision D1): the AI
    #: workers process it like any camera, and drone events are read from it.
    camera_id: uuid.UUID | None = None
    manufacturer: str | None = Field(None, max_length=120)
    model: str | None = Field(None, max_length=120)
    serial_number: str | None = Field(None, max_length=120)
    firmware_version: str | None = Field(None, max_length=60)
    drone_type: str | None = Field(None, max_length=40)
    camera_type: str | None = Field(None, max_length=40)
    communication_type: str | None = Field(None, max_length=40)
    provider_drone_ref: str | None = Field(None, max_length=120)
    heartbeat_timeout_seconds: int = Field(30, ge=5, le=3600)
    maintenance_interval_hours: int | None = Field(None, ge=1, le=100_000)
    next_maintenance_at: datetime | None = None


class DroneUpdate(BaseModel):
    """Every field optional. A field sent as null is cleared; a field left out
    is left alone — which is why this uses exclude_unset, not `is not None`."""
    name: str | None = Field(None, min_length=1, max_length=120)
    code: str | None = Field(None, min_length=1, max_length=40, pattern=_CODE_PATTERN)
    site_id: uuid.UUID | None = None
    provider_config_id: uuid.UUID | None = None
    edge_gateway_id: uuid.UUID | None = None
    camera_id: uuid.UUID | None = None
    manufacturer: str | None = Field(None, max_length=120)
    model: str | None = Field(None, max_length=120)
    serial_number: str | None = Field(None, max_length=120)
    firmware_version: str | None = Field(None, max_length=60)
    drone_type: str | None = Field(None, max_length=40)
    camera_type: str | None = Field(None, max_length=40)
    communication_type: str | None = Field(None, max_length=40)
    provider_drone_ref: str | None = Field(None, max_length=120)
    heartbeat_timeout_seconds: int | None = Field(None, ge=5, le=3600)
    maintenance_interval_hours: int | None = Field(None, ge=1, le=100_000)
    next_maintenance_at: datetime | None = None


_NOT_NULL_ON_UPDATE = {"name", "code", "heartbeat_timeout_seconds"}


class MaintenanceCreate(BaseModel):
    maintenance_type: MaintenanceType
    description: str | None = Field(None, max_length=4000)
    performed_at: datetime | None = None
    performed_by_name: str | None = Field(None, max_length=160)
    next_due_at: datetime | None = None
    firmware_version_after: str | None = Field(None, max_length=60)


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider_key: str = Field(min_length=1, max_length=60)
    settings: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class ProviderUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    settings: dict[str, Any] | None = None
    is_active: bool | None = None


class GatewayCreate(BaseModel):
    site_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=40, pattern=_CODE_PATTERN)
    is_active: bool = True


class GatewayUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    is_active: bool | None = None
    heartbeat_timeout_seconds: int | None = Field(None, ge=10, le=3600)


# ── Shared lookups ───────────────────────────────────────────────────────────

_DRONE_SELECT = f"""
    SELECT d.*, s.name AS site_name,
           p.name AS provider_name, p.provider_key,
           g.name AS edge_gateway_name, c.name AS camera_name,
           CASE WHEN d.last_heartbeat_at IS NULL THEN NULL
                ELSE EXTRACT(EPOCH FROM (now() - d.last_heartbeat_at))::bigint END
               AS seconds_since_heartbeat,
           (SELECT ps.id FROM drone_patrol_sessions ps
             WHERE ps.drone_id = d.id AND ps.status IN ({_IN_FLIGHT_SQL}) LIMIT 1)
               AS active_session_id
      FROM drones d
      LEFT JOIN sites s                  ON s.id = d.site_id
      LEFT JOIN drone_provider_configs p ON p.id = d.provider_config_id
      LEFT JOIN drone_edge_gateways g    ON g.id = d.edge_gateway_id
      LEFT JOIN cameras c                ON c.id = d.camera_id
"""


async def _drone_or_404(db: AsyncSession, drone_id: uuid.UUID,
                        allowed: list[str] | None) -> dict:
    row = (await db.execute(text(_DRONE_SELECT + " WHERE d.id = CAST(:id AS uuid)"),
                            {"id": str(drone_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Drone not found")
    assert_site_visible(allowed, row["site_id"], "Drone")
    return dict(row)


async def _check_references(db: AsyncSession, *, site_id, provider_config_id, edge_gateway_id,
                            camera_id=None) -> None:
    """The provider, gateway and camera a drone points at must exist; a gateway
    serves one site — a drone on site A cannot report through site B's — and so
    does a camera, whose site is where its events and incidents belong."""
    if camera_id is not None:
        cam = (await db.execute(text("SELECT site_id FROM cameras WHERE id = CAST(:id AS uuid)"),
                                {"id": str(camera_id)})).first()
        if cam is None:
            raise HTTPException(422, "Camera not found.")
        if cam[0] is not None and (site_id is None or str(cam[0]) != str(site_id)):
            raise HTTPException(422, "The drone's camera must be at the drone's site.")
        if (await db.execute(text("SELECT 1 FROM drone_camera_coverage WHERE camera_id = CAST(:id AS uuid)"),
                             {"id": str(camera_id)})).first():
            raise HTTPException(409, "That camera has fixed coverage recorded; a drone's camera moves.")
    if provider_config_id is not None:
        ok = (await db.execute(text(
            "SELECT 1 FROM drone_provider_configs WHERE id = CAST(:id AS uuid)"),
            {"id": str(provider_config_id)})).first()
        if ok is None:
            raise HTTPException(422, "Provider configuration not found.")
    if edge_gateway_id is not None:
        gw = (await db.execute(text(
            "SELECT site_id FROM drone_edge_gateways WHERE id = CAST(:id AS uuid)"),
            {"id": str(edge_gateway_id)})).first()
        if gw is None:
            raise HTTPException(422, "Edge gateway not found.")
        if site_id is None or str(gw[0]) != str(site_id):
            raise HTTPException(422, "An edge gateway serves one site; the drone must be on the gateway's site.")


def _conflict_message(exc: IntegrityError) -> str | None:
    if unique_violation(exc, "uq_drones_code"):
        return "Another drone already uses this code."
    if unique_violation(exc, "uq_drones_camera"):
        return "That camera already stands for another drone."
    if unique_violation(exc, "uq_drones_serial"):
        return "Another drone already has this serial number."
    return None


# ── Licence and overview (not gated: they explain the gate) ──────────────────

@router.get("/entitlement", dependencies=[_READ])
async def get_entitlement(db: AsyncSession = Depends(get_db_with_tenant)):
    """Whether this organisation has Drone Patrol, its limits and what is used.
    Readable without the licence, so a screen can say why it is disabled."""
    ent = await load_entitlement(db)
    problem = entitlement_problem(ent, datetime.now(timezone.utc))
    return {
        "licensed": problem is None,
        "reason": problem,
        "expires_at": ent["expires_at"] if ent else None,
        "limits": {k: (ent or {}).get(k) for k in ("max_drones", "max_missions", "max_sites")},
        "usage": await usage(db),
    }


@router.get("/dashboard", dependencies=[_READ])
async def fleet_dashboard(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {"low": LOW_BATTERY_PCT}
    d_scope = scope_sql(allowed, "d.site_id", params)
    s_scope = scope_sql(allowed, "ps.site_id", params)
    e_scope = scope_sql(allowed, "e.site_id", params)
    fleet = (await db.execute(text(f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE d.status NOT IN ('OFFLINE','COMMUNICATION_LOST','DISABLED')) AS online,
               count(*) FILTER (WHERE d.status IN ('OFFLINE','COMMUNICATION_LOST'))              AS offline,
               count(*) FILTER (WHERE d.status = 'DISABLED')                                      AS disabled,
               count(*) FILTER (WHERE d.status IN ('WARNING','CRITICAL','COMMUNICATION_LOST'))    AS needs_attention,
               count(*) FILTER (WHERE d.status <> 'DISABLED' AND d.battery_level < :low)          AS battery_warnings,
               count(*) FILTER (WHERE d.status <> 'DISABLED' AND d.next_maintenance_at IS NOT NULL
                                  AND d.next_maintenance_at <= now() + interval '7 days')        AS maintenance_due
          FROM drones d WHERE TRUE {d_scope}
    """), params)).mappings().first()
    sessions = (await db.execute(text(f"""
        SELECT count(*) FILTER (WHERE ps.status IN ({_IN_FLIGHT_SQL}))                           AS active_missions,
               count(*) FILTER (WHERE ps.status IN ('FAILED','ABORTED','BLOCKED','MISSED')
                                  AND ps.created_at > now() - interval '24 hours')               AS failed_last_24h,
               count(*) FILTER (WHERE ps.status = 'COMPLETED'
                                  AND ps.created_at > now() - interval '24 hours')               AS completed_last_24h
          FROM drone_patrol_sessions ps WHERE TRUE {s_scope}
    """), params)).mappings().first()
    events = (await db.execute(text(f"""
        SELECT e.risk_level, count(*) AS n FROM drone_events e
         WHERE e.status IN ('NEW','ACKNOWLEDGED','INVESTIGATING','ESCALATED') {e_scope}
         GROUP BY e.risk_level
    """), params)).mappings().all()
    open_events = {lvl: 0 for lvl in ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")}
    open_events.update({r["risk_level"]: r["n"] for r in events})
    return {"fleet": dict(fleet), "missions": dict(sessions), "open_events_by_risk": open_events}


# ── Providers ────────────────────────────────────────────────────────────────

_PROVIDER_SELECT = """
    SELECT p.id, p.name, p.provider_key, p.config,
           (p.secret_encrypted IS NOT NULL) AS has_secret,
           p.is_active, p.created_at, p.updated_at,
           (SELECT count(*) FROM drones d WHERE d.provider_config_id = p.id) AS drone_count
      FROM drone_provider_configs p
"""


@router.get("/providers/catalogue", dependencies=[_READ])
async def provider_catalogue():
    """The providers this installation can talk to, and their settings."""
    return registry.catalogue()


@router.get("/providers", dependencies=[_READ])
async def list_providers(db: AsyncSession = Depends(get_db_with_tenant)):
    rows = (await db.execute(text(_PROVIDER_SELECT + " ORDER BY p.name"))).mappings().all()
    return [dict(r) for r in rows]


def _encrypt(secret_values: dict[str, str]) -> str | None:
    return encrypt_secret(json.dumps(secret_values)) if secret_values else None


@router.post("/providers", status_code=status.HTTP_201_CREATED, dependencies=[_CREATE, _LICENSED])
async def create_provider(
    body: ProviderCreate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    try:
        config, secret_values = registry.split_config(body.provider_key, body.settings)
    except registry.ProviderConfigError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        row = (await db.execute(text("""
            INSERT INTO drone_provider_configs
                (tenant_id, name, provider_key, config, secret_encrypted, is_active, created_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, :name, :key, CAST(:config AS jsonb),
                    :secret, :active, CAST(:by AS uuid))
            RETURNING id
        """), {"name": body.name, "key": body.provider_key, "config": json.dumps(config),
               "secret": _encrypt(secret_values), "active": body.is_active,
               "by": token.user_id})).first()
    except IntegrityError as exc:
        if unique_violation(exc, "uq_dpc_name"):
            raise HTTPException(409, "A provider configuration with this name already exists.") from exc
        raise
    await audit(db, request, token, "drone.provider.create", "drone_provider_config", row[0],
                {"name": body.name, "provider_key": body.provider_key,
                 "secret_fields_set": sorted(secret_values)})
    result = dict((await db.execute(text(_PROVIDER_SELECT + " WHERE p.id = :id"),
                                    {"id": row[0]})).mappings().first())
    await db.commit()
    return result


@router.put("/providers/{provider_id}", dependencies=[_UPDATE, _LICENSED])
async def update_provider(
    provider_id: uuid.UUID, body: ProviderUpdate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Settings, when sent, replace the stored configuration. A secret field
    left out keeps the value already stored — the form never shows it, so it
    cannot send it back."""
    existing = (await db.execute(text(
        "SELECT * FROM drone_provider_configs WHERE id = CAST(:id AS uuid)"),
        {"id": str(provider_id)})).mappings().first()
    if existing is None:
        raise HTTPException(404, "Provider configuration not found")
    sets, params = [], {"id": str(provider_id)}
    changed: list[str] = []
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name; changed.append("name")
    if body.is_active is not None:
        sets.append("is_active = :active"); params["active"] = body.is_active; changed.append("is_active")
    if body.settings is not None:
        try:
            config, secret_values = registry.split_config(
                existing["provider_key"], body.settings,
                has_existing_secret=existing["secret_encrypted"] is not None)
        except registry.ProviderConfigError as exc:
            raise HTTPException(422, str(exc)) from exc
        stored: dict[str, str] = {}
        if existing["secret_encrypted"]:
            stored = json.loads(decrypt_secret(existing["secret_encrypted"]))
        stored.update(secret_values)
        sets += ["config = CAST(:config AS jsonb)", "secret_encrypted = :secret"]
        params["config"] = json.dumps(config)
        params["secret"] = _encrypt(stored)
        changed.append("settings")
    if not sets:
        raise HTTPException(422, "No fields to update")
    try:
        await db.execute(text(
            f"UPDATE drone_provider_configs SET {', '.join(sets)}, updated_at = now() "
            " WHERE id = CAST(:id AS uuid)"), params)
    except IntegrityError as exc:
        if unique_violation(exc, "uq_dpc_name"):
            raise HTTPException(409, "A provider configuration with this name already exists.") from exc
        raise
    await audit(db, request, token, "drone.provider.update", "drone_provider_config",
                provider_id, {"changed": changed})
    result = dict((await db.execute(text(_PROVIDER_SELECT + " WHERE p.id = CAST(:id AS uuid)"),
                                    {"id": str(provider_id)})).mappings().first())
    await db.commit()
    return result


@router.delete("/providers/{provider_id}", dependencies=[_DELETE, _LICENSED])
async def delete_provider(
    provider_id: uuid.UUID, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    row = (await db.execute(text(_PROVIDER_SELECT + " WHERE p.id = CAST(:id AS uuid)"),
                            {"id": str(provider_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Provider configuration not found")
    if row["drone_count"]:
        raise HTTPException(409, f"{row['drone_count']} drone(s) use this provider. Move them first.")
    await db.execute(text("DELETE FROM drone_provider_configs WHERE id = CAST(:id AS uuid)"),
                     {"id": str(provider_id)})
    await audit(db, request, token, "drone.provider.delete", "drone_provider_config",
                provider_id, {"name": row["name"]})
    await db.commit()
    return {"deleted": str(provider_id)}


# ── Edge gateways ────────────────────────────────────────────────────────────

_GATEWAY_SELECT = """
    SELECT g.id, g.site_id, s.name AS site_name, g.name, g.code, g.status, g.last_seen_at,
           g.software_version, g.credential_prefix, g.credential_rotated_at,
           (g.credential_hash IS NOT NULL) AS has_credential,
           g.last_sync_at, g.buffer_depth, g.oldest_buffered_at, g.storage_free_pct, g.clock_offset_s,
           g.health, g.heartbeat_timeout_seconds,
           g.is_active, g.created_at, g.updated_at,
           (SELECT count(*) FROM drones d WHERE d.edge_gateway_id = g.id) AS drone_count
      FROM drone_edge_gateways g
      JOIN sites s ON s.id = g.site_id
"""


def _new_gateway_credential(tenant_id: str) -> tuple[str, str, str]:
    """(credential, sha256, display prefix). Prefixed with the tenant, like a
    refresh token, so the edge sync can scope RLS before looking it up."""
    random_part = pysecrets.token_urlsafe(32)
    credential = f"deg.{tenant_id}.{random_part}"
    return credential, hashlib.sha256(credential.encode("utf-8")).hexdigest(), random_part[:8]


_CREDENTIAL_NOTE = ("Copy this credential now and install it on the edge gateway. "
                    "It is stored only as a hash and cannot be shown again.")


@router.get("/edge-gateways", dependencies=[_READ])
async def list_gateways(
    site_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {"site": str(site_id) if site_id else None}
    scope = scope_sql(allowed, "g.site_id", params)
    rows = (await db.execute(text(_GATEWAY_SELECT + f"""
         WHERE (CAST(:site AS uuid) IS NULL OR g.site_id = CAST(:site AS uuid)) {scope}
         ORDER BY s.name, g.name"""), params)).mappings().all()
    return [dict(r) for r in rows]


@router.post("/edge-gateways", status_code=status.HTTP_201_CREATED, dependencies=[_CREATE, _LICENSED])
async def create_gateway(
    body: GatewayCreate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    await site_or_404(db, body.site_id, allowed)
    credential, digest, prefix = _new_gateway_credential(token.tenant_id)
    try:
        row = (await db.execute(text("""
            INSERT INTO drone_edge_gateways
                (tenant_id, site_id, name, code, credential_hash, credential_prefix,
                 credential_rotated_at, is_active, created_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:site AS uuid), :name, :code,
                    :hash, :prefix, now(), :active, CAST(:by AS uuid))
            RETURNING id
        """), {"site": str(body.site_id), "name": body.name, "code": body.code, "hash": digest,
               "prefix": prefix, "active": body.is_active, "by": token.user_id})).first()
    except IntegrityError as exc:
        if unique_violation(exc, "uq_deg_code"):
            raise HTTPException(409, "Another edge gateway already uses this code.") from exc
        raise
    await audit(db, request, token, "drone.gateway.create", "drone_edge_gateway", row[0],
                {"name": body.name, "code": body.code, "site_id": str(body.site_id)})
    gw = (await db.execute(text(_GATEWAY_SELECT + " WHERE g.id = :id"), {"id": row[0]})).mappings().first()
    await db.commit()
    return {**dict(gw), "credential": credential, "credential_note": _CREDENTIAL_NOTE}


async def _gateway_or_404(db: AsyncSession, gateway_id: uuid.UUID, allowed) -> dict:
    row = (await db.execute(text(_GATEWAY_SELECT + " WHERE g.id = CAST(:id AS uuid)"),
                            {"id": str(gateway_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Edge gateway not found")
    assert_site_visible(allowed, row["site_id"], "Edge gateway")
    return dict(row)


@router.post("/edge-gateways/{gateway_id}/rotate-credential", dependencies=[_UPDATE, _LICENSED])
async def rotate_gateway_credential(
    gateway_id: uuid.UUID, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Issue a new credential. The old one stops working immediately."""
    await _gateway_or_404(db, gateway_id, allowed)
    credential, digest, prefix = _new_gateway_credential(token.tenant_id)
    await db.execute(text("""
        UPDATE drone_edge_gateways
           SET credential_hash = :hash, credential_prefix = :prefix,
               credential_rotated_at = now(), updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"hash": digest, "prefix": prefix, "id": str(gateway_id)})
    await audit(db, request, token, "drone.gateway.rotate_credential", "drone_edge_gateway",
                gateway_id, {"credential_prefix": prefix})
    gw = await _gateway_or_404(db, gateway_id, allowed)
    await db.commit()
    return {**gw, "credential": credential, "credential_note": _CREDENTIAL_NOTE}


@router.put("/edge-gateways/{gateway_id}", dependencies=[_UPDATE, _LICENSED])
async def update_gateway(
    gateway_id: uuid.UUID, body: GatewayUpdate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    await _gateway_or_404(db, gateway_id, allowed)
    changes = body.model_dump(exclude_unset=True)
    changes = {k: v for k, v in changes.items() if v is not None}
    if not changes:
        raise HTTPException(422, "No fields to update")
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    await db.execute(text(f"UPDATE drone_edge_gateways SET {sets}, updated_at = now() "
                          " WHERE id = CAST(:id AS uuid)"), {**changes, "id": str(gateway_id)})
    await audit(db, request, token, "drone.gateway.update", "drone_edge_gateway", gateway_id,
                {"changed": sorted(changes)})
    result = await _gateway_or_404(db, gateway_id, allowed)
    await db.commit()
    return result


@router.delete("/edge-gateways/{gateway_id}", dependencies=[_DELETE, _LICENSED])
async def delete_gateway(
    gateway_id: uuid.UUID, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    gw = await _gateway_or_404(db, gateway_id, allowed)
    if gw["drone_count"]:
        raise HTTPException(409, f"{gw['drone_count']} drone(s) report through this gateway. Move them first.")
    flying = (await db.execute(text(
        f"SELECT count(*) FROM drone_patrol_sessions WHERE edge_gateway_id = CAST(:id AS uuid) "
        f"   AND status IN ({_IN_FLIGHT_SQL})"), {"id": str(gateway_id)})).scalar()
    if flying:
        raise HTTPException(409, f"This gateway is flying {flying} mission(s). Let them finish first.")
    await db.execute(text("DELETE FROM drone_edge_gateways WHERE id = CAST(:id AS uuid)"),
                     {"id": str(gateway_id)})
    await audit(db, request, token, "drone.gateway.delete", "drone_edge_gateway", gateway_id,
                {"code": gw["code"]})
    await db.commit()
    return {"deleted": str(gateway_id)}


@router.get("/edge-gateways/{gateway_id}/sync-receipts", dependencies=[_READ])
async def list_sync_receipts(
    gateway_id: uuid.UUID, limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What the gateway has sent lately, newest first: how much, how much was
    new, and every item refused with the reason. Kept for a week."""
    await _gateway_or_404(db, gateway_id, allowed)
    rows = (await db.execute(text("""
        SELECT id, batch_id, received_at, edge_sent_at, clock_offset_s, item_count, accepted,
               duplicates, rejected, result
          FROM drone_sync_receipts WHERE gateway_id = CAST(:id AS uuid)
         ORDER BY received_at DESC LIMIT :n
    """), {"id": str(gateway_id), "n": limit})).mappings().all()
    out = []
    for r in rows:
        r = dict(r)
        res = r.pop("result") or {}
        r["rejections"] = [{"kind": k, **x} for k in ("health", "updates", "commands", "events", "media")
                           for x in (res.get(k) or {}).get("rejected", [])]
        out.append(r)
    return out


# ── Fixed-camera coverage (for CCTV correlation) ─────────────────────────────
#
# Optional (decision D2). A camera with none is correlated by distance and
# called NEARBY; one with surveyed coverage is called COVERING only when the
# drone's spot falls inside what it can see.

class CoverageIn(BaseModel):
    heading_deg: float | None = Field(None, ge=0, lt=360)
    fov_deg: float | None = Field(None, gt=0, le=360)
    range_m: float | None = Field(None, gt=0, le=5000)
    coverage_polygon: list | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=2000)


_COVERAGE_SELECT = """
    SELECT cov.id, cov.camera_id, c.name AS camera_name, c.site_id, s.name AS site_name,
           c.latitude, c.longitude, cov.heading_deg, cov.fov_deg, cov.range_m, cov.coverage_polygon,
           cov.notes, cov.updated_at
      FROM drone_camera_coverage cov
      JOIN cameras c ON c.id = cov.camera_id
      LEFT JOIN sites s ON s.id = c.site_id
"""


async def _fixed_camera_or_404(db: AsyncSession, camera_id: uuid.UUID, allowed) -> dict:
    cam = (await db.execute(text(
        "SELECT id, name, site_id, latitude, longitude FROM cameras WHERE id = CAST(:id AS uuid)"),
        {"id": str(camera_id)})).mappings().first()
    if cam is None:
        raise HTTPException(404, "Camera not found")
    assert_site_visible(allowed, cam["site_id"], "Camera")
    return dict(cam)


@router.get("/camera-coverage", dependencies=[_READ])
async def list_camera_coverage(
    site_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {"site": str(site_id) if site_id else None}
    scope = scope_sql(allowed, "c.site_id", params)
    rows = (await db.execute(text(_COVERAGE_SELECT + f"""
         WHERE (CAST(:site AS uuid) IS NULL OR c.site_id = CAST(:site AS uuid)) {scope}
         ORDER BY s.name, c.name"""), params)).mappings().all()
    return [dict(r) for r in rows]


@router.put("/camera-coverage/{camera_id}", dependencies=[_UPDATE, _LICENSED])
async def set_camera_coverage(
    camera_id: uuid.UUID, body: CoverageIn, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What a fixed camera can see: a sector (heading, field of view, range) or
    an explicit polygon on the map. Replaces what was there."""
    cam = await _fixed_camera_or_404(db, camera_id, allowed)
    if cam["latitude"] is None or cam["longitude"] is None:
        raise HTTPException(422, "The camera has no position; set its latitude and longitude first.")
    if (await db.execute(text("SELECT 1 FROM drones WHERE camera_id = CAST(:id AS uuid)"),
                         {"id": str(camera_id)})).first():
        raise HTTPException(409, "That is a drone's camera; it moves, so it has no fixed coverage.")
    polygon = None
    if body.coverage_polygon is not None:
        try:
            polygon = normalize_zone("POLYGON", body.coverage_polygon)["polygon"]
        except GeometryError as exc:
            raise HTTPException(422, f"Coverage polygon: {exc}") from exc
    elif None in (body.heading_deg, body.fov_deg, body.range_m):
        raise HTTPException(422, "Give either a coverage polygon, or all of heading_deg, fov_deg and range_m.")
    await db.execute(text("""
        INSERT INTO drone_camera_coverage
            (tenant_id, camera_id, heading_deg, fov_deg, range_m, coverage_polygon, notes, updated_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:c AS uuid), :h, :f, :r, CAST(:p AS jsonb),
                :n, CAST(:by AS uuid))
        ON CONFLICT (camera_id) DO UPDATE SET
            heading_deg = EXCLUDED.heading_deg, fov_deg = EXCLUDED.fov_deg, range_m = EXCLUDED.range_m,
            coverage_polygon = EXCLUDED.coverage_polygon, notes = EXCLUDED.notes,
            updated_by_user_id = EXCLUDED.updated_by_user_id, updated_at = now()
    """), {"c": str(camera_id), "h": body.heading_deg, "f": body.fov_deg, "r": body.range_m,
           "p": json.dumps(polygon) if polygon is not None else None, "n": body.notes, "by": token.user_id})
    await audit(db, request, token, "drone.camera_coverage.set", "camera", camera_id,
                {"heading_deg": body.heading_deg, "fov_deg": body.fov_deg, "range_m": body.range_m,
                 "polygon_points": len(polygon) if polygon else 0})
    row = (await db.execute(text(_COVERAGE_SELECT + " WHERE cov.camera_id = CAST(:c AS uuid)"),
                            {"c": str(camera_id)})).mappings().first()
    await db.commit()
    return dict(row)


@router.delete("/camera-coverage/{camera_id}", dependencies=[_UPDATE, _LICENSED])
async def delete_camera_coverage(
    camera_id: uuid.UUID, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    await _fixed_camera_or_404(db, camera_id, allowed)
    gone = (await db.execute(text("DELETE FROM drone_camera_coverage WHERE camera_id = CAST(:c AS uuid) "
                                  "RETURNING id"), {"c": str(camera_id)})).first()
    if gone is None:
        raise HTTPException(404, "That camera has no coverage recorded.")
    await audit(db, request, token, "drone.camera_coverage.delete", "camera", camera_id)
    await db.commit()
    return {"deleted": str(camera_id)}


# ── Drones ───────────────────────────────────────────────────────────────────

@router.get("", dependencies=[_READ])
async def list_drones(
    site_id: uuid.UUID | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    q: str | None = Query(None, max_length=120),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {"site": str(site_id) if site_id else None, "status": status_filter,
                    "q": f"%{q}%" if q else None}
    scope = scope_sql(allowed, "d.site_id", params)
    where = f"""
         WHERE (CAST(:site AS uuid) IS NULL OR d.site_id = CAST(:site AS uuid))
           AND (CAST(:status AS text) IS NULL OR d.status = CAST(:status AS text))
           AND (CAST(:q AS text) IS NULL OR d.name ILIKE CAST(:q AS text) OR d.code ILIKE CAST(:q AS text))
           {scope}"""
    return await paginate(
        db,
        _DRONE_SELECT + where + " ORDER BY d.name LIMIT :limit OFFSET :offset",
        "SELECT count(*) FROM drones d" + where,
        params, limit, offset,
    )


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[_CREATE])
async def create_drone(
    body: DroneCreate, request: Request,
    ent: dict = Depends(require_drone_module),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    if body.site_id is not None:
        await site_or_404(db, body.site_id, allowed)
    elif allowed is not None:
        # A site-restricted user cannot own a drone that belongs to no site —
        # they could never see it again.
        raise HTTPException(422, "Choose a site for this drone.")
    await _check_references(db, site_id=body.site_id, provider_config_id=body.provider_config_id,
                            edge_gateway_id=body.edge_gateway_id, camera_id=body.camera_id)
    await enforce_drone_limit(db, ent)
    await enforce_site_limit(db, ent, body.site_id)

    values = body.model_dump()
    for k in ("site_id", "provider_config_id", "edge_gateway_id", "camera_id"):
        values[k] = str(values[k]) if values[k] else None
    try:
        row = (await db.execute(text("""
            INSERT INTO drones
                (tenant_id, site_id, provider_config_id, edge_gateway_id, camera_id, name, code,
                 manufacturer, model, serial_number, firmware_version, drone_type, camera_type,
                 communication_type, provider_drone_ref, heartbeat_timeout_seconds,
                 maintenance_interval_hours, next_maintenance_at, created_by_user_id, updated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:site_id AS uuid),
                    CAST(:provider_config_id AS uuid), CAST(:edge_gateway_id AS uuid),
                    CAST(:camera_id AS uuid), :name, :code,
                    :manufacturer, :model, :serial_number, :firmware_version, :drone_type, :camera_type,
                    :communication_type, :provider_drone_ref, :heartbeat_timeout_seconds,
                    :maintenance_interval_hours, :next_maintenance_at, CAST(:by AS uuid), CAST(:by AS uuid))
            RETURNING id
        """), {**values, "by": token.user_id})).first()
    except IntegrityError as exc:
        msg = _conflict_message(exc)
        if msg:
            raise HTTPException(409, msg) from exc
        raise
    await audit(db, request, token, "drone.create", "drone", row[0],
                {"name": body.name, "code": body.code,
                 "site_id": values["site_id"]})
    result = await _drone_or_404(db, row[0], allowed)
    await db.commit()
    return result


@router.get("/{drone_id}", dependencies=[_READ])
async def get_drone(
    drone_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    drone = await _drone_or_404(db, drone_id, allowed)
    missions = (await db.execute(text(
        "SELECT id, name, enabled FROM drone_missions WHERE drone_id = CAST(:id AS uuid) ORDER BY name"),
        {"id": str(drone_id)})).mappings().all()
    maintenance = (await db.execute(text("""
        SELECT id, maintenance_type, description, performed_at, performed_by_name, next_due_at
          FROM drone_maintenance_logs WHERE drone_id = CAST(:id AS uuid)
         ORDER BY performed_at DESC LIMIT 5
    """), {"id": str(drone_id)})).mappings().all()
    return {**drone, "missions": [dict(m) for m in missions],
            "recent_maintenance": [dict(m) for m in maintenance]}


@router.put("/{drone_id}", dependencies=[_UPDATE])
async def update_drone(
    drone_id: uuid.UUID, body: DroneUpdate, request: Request,
    ent: dict = Depends(require_drone_module),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    existing = await _drone_or_404(db, drone_id, allowed)
    changes = body.model_dump(exclude_unset=True)
    for k in _NOT_NULL_ON_UPDATE:
        if k in changes and changes[k] is None:
            raise HTTPException(422, f"{k} cannot be empty.")
    if not changes:
        raise HTTPException(422, "No fields to update")

    site_id = changes.get("site_id", existing["site_id"])
    if "site_id" in changes:
        if site_id is not None:
            await site_or_404(db, site_id, allowed)
            if existing["status"] != "DISABLED":
                await enforce_site_limit(db, ent, site_id)
        elif allowed is not None:
            raise HTTPException(422, "Choose a site for this drone.")
    await _check_references(
        db, site_id=site_id,
        provider_config_id=changes.get("provider_config_id", existing["provider_config_id"]),
        edge_gateway_id=changes.get("edge_gateway_id", existing["edge_gateway_id"]),
        # Checked only when the camera or the site changes: an unrelated edit
        # must not fail over a link that was valid when it was made.
        camera_id=(changes.get("camera_id", existing["camera_id"])
                   if ("camera_id" in changes or "site_id" in changes) else None),
    )
    params = {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in changes.items()}
    casts = {"site_id", "provider_config_id", "edge_gateway_id", "camera_id"}
    sets = ", ".join(f"{k} = CAST(:{k} AS uuid)" if k in casts else f"{k} = :{k}" for k in params)
    try:
        await db.execute(text(
            f"UPDATE drones SET {sets}, updated_by_user_id = CAST(:by AS uuid), updated_at = now() "
            " WHERE id = CAST(:id AS uuid)"), {**params, "id": str(drone_id), "by": token.user_id})
    except IntegrityError as exc:
        msg = _conflict_message(exc)
        if msg:
            raise HTTPException(409, msg) from exc
        raise
    await audit(db, request, token, "drone.update", "drone", drone_id, {"changed": sorted(changes)})
    result = await _drone_or_404(db, drone_id, allowed)
    await db.commit()
    return result


@router.post("/{drone_id}/disable", dependencies=[_UPDATE, _LICENSED])
async def disable_drone(
    drone_id: uuid.UUID, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    drone = await _drone_or_404(db, drone_id, allowed)
    if drone["active_session_id"]:
        raise HTTPException(409, "This drone is on a mission. Abort it or let it finish first.")
    if drone["status"] == "DISABLED":
        raise HTTPException(409, "This drone is already disabled.")
    await db.execute(text("UPDATE drones SET status = 'DISABLED', updated_at = now(), "
                          " updated_by_user_id = CAST(:by AS uuid) WHERE id = CAST(:id AS uuid)"),
                     {"id": str(drone_id), "by": token.user_id})
    await audit(db, request, token, "drone.disable", "drone", drone_id, {"previous_status": drone["status"]})
    result = await _drone_or_404(db, drone_id, allowed)
    await db.commit()
    return result


@router.post("/{drone_id}/enable", dependencies=[_UPDATE])
async def enable_drone(
    drone_id: uuid.UUID, request: Request,
    ent: dict = Depends(require_drone_module),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Back to OFFLINE, not READY: nothing is known about its state until the
    next heartbeat says so."""
    drone = await _drone_or_404(db, drone_id, allowed)
    if drone["status"] != "DISABLED":
        raise HTTPException(409, "Only a disabled drone can be enabled.")
    await enforce_drone_limit(db, ent)
    await enforce_site_limit(db, ent, drone["site_id"])
    await db.execute(text("UPDATE drones SET status = 'OFFLINE', updated_at = now(), "
                          " updated_by_user_id = CAST(:by AS uuid) WHERE id = CAST(:id AS uuid)"),
                     {"id": str(drone_id), "by": token.user_id})
    await audit(db, request, token, "drone.enable", "drone", drone_id)
    result = await _drone_or_404(db, drone_id, allowed)
    await db.commit()
    return result


@router.delete("/{drone_id}", dependencies=[_DELETE, _LICENSED])
async def delete_drone(
    drone_id: uuid.UUID, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    drone = await _drone_or_404(db, drone_id, allowed)
    flights = (await db.execute(text(
        "SELECT count(*) FROM drone_patrol_sessions WHERE drone_id = CAST(:id AS uuid)"),
        {"id": str(drone_id)})).scalar()
    if flights:
        raise HTTPException(409, f"This drone has {flights} recorded flight(s). "
                                 "Disable it instead, so that history stays readable.")
    missions = (await db.execute(text(
        "SELECT count(*) FROM drone_missions WHERE drone_id = CAST(:id AS uuid) AND enabled"),
        {"id": str(drone_id)})).scalar()
    if missions:
        raise HTTPException(409, f"{missions} enabled mission(s) use this drone. Reassign them first.")
    await db.execute(text("DELETE FROM drones WHERE id = CAST(:id AS uuid)"), {"id": str(drone_id)})
    await audit(db, request, token, "drone.delete", "drone", drone_id,
                {"name": drone["name"], "code": drone["code"]})
    await db.commit()
    return {"deleted": str(drone_id)}


# ── Maintenance ──────────────────────────────────────────────────────────────

@router.get("/{drone_id}/maintenance", dependencies=[_MAINT_READ])
async def list_maintenance(
    drone_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    await _drone_or_404(db, drone_id, allowed)
    rows = (await db.execute(text("""
        SELECT * FROM drone_maintenance_logs WHERE drone_id = CAST(:id AS uuid)
         ORDER BY performed_at DESC LIMIT :limit
    """), {"id": str(drone_id), "limit": limit})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/{drone_id}/maintenance", status_code=status.HTTP_201_CREATED,
             dependencies=[_MAINT_MANAGE, _LICENSED])
async def record_maintenance(
    drone_id: uuid.UUID, body: MaintenanceCreate, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Records the work, and moves the drone's own maintenance dates with it:
    last_maintenance_at never goes backwards when an older job is entered late."""
    drone = await _drone_or_404(db, drone_id, allowed)
    performed_at = body.performed_at or datetime.now(timezone.utc)
    row = (await db.execute(text("""
        INSERT INTO drone_maintenance_logs
            (tenant_id, drone_id, maintenance_type, description, performed_at,
             performed_by_user_id, performed_by_name, flight_seconds_at,
             firmware_version_after, next_due_at)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:d AS uuid), :t, :desc, :at,
                CAST(:by AS uuid), :who, :fs, :fw, :next)
        RETURNING *
    """), {"d": str(drone_id), "t": body.maintenance_type, "desc": body.description,
           "at": performed_at, "by": token.user_id, "who": body.performed_by_name,
           "fs": drone["total_flight_seconds"], "fw": body.firmware_version_after,
           "next": body.next_due_at})).mappings().first()
    await db.execute(text("""
        UPDATE drones
           SET last_maintenance_at = GREATEST(COALESCE(last_maintenance_at, :at), :at),
               next_maintenance_at = COALESCE(:next, next_maintenance_at),
               firmware_version    = COALESCE(:fw, firmware_version),
               updated_at = now()
         WHERE id = CAST(:d AS uuid)
    """), {"at": performed_at, "next": body.next_due_at, "fw": body.firmware_version_after,
           "d": str(drone_id)})
    await audit(db, request, token, "drone.maintenance.record", "drone", drone_id,
                {"maintenance_type": body.maintenance_type, "log_id": str(row["id"])})
    await db.commit()
    return dict(row)


# ── Telemetry (read) ─────────────────────────────────────────────────────────

@router.get("/{drone_id}/telemetry", dependencies=[_READ])
async def drone_telemetry(
    drone_id: uuid.UUID,
    start: datetime | None = Query(None, description="Defaults to one hour before `end`"),
    end: datetime | None = Query(None, description="Defaults to now"),
    limit: int = Query(2000, ge=1, le=5000),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Samples in time order. The window is capped at 24 hours so a careless
    query cannot walk every partition of the busiest table in the system."""
    await _drone_or_404(db, drone_id, allowed)
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=1)
    if start >= end:
        raise HTTPException(422, "start must be before end.")
    if end - start > MAX_TELEMETRY_WINDOW:
        raise HTTPException(422, "Ask for at most 24 hours of telemetry at a time.")
    rows = (await db.execute(text("""
        SELECT recorded_at, session_id, latitude, longitude, altitude_m, heading_deg, speed_mps,
               battery_pct, gps_fix, signal_quality, mission_state, waypoint_sequence,
               gimbal_pitch_deg, gimbal_yaw_deg
          FROM drone_telemetry
         WHERE drone_id = CAST(:d AS uuid) AND recorded_at > :s AND recorded_at <= :e
         ORDER BY recorded_at
         LIMIT :limit
    """), {"d": str(drone_id), "s": start, "e": end, "limit": limit})).mappings().all()
    return {"drone_id": str(drone_id), "start": start, "end": end,
            "truncated": len(rows) >= limit, "points": [dict(r) for r in rows]}
