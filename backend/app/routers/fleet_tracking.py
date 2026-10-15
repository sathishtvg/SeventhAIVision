"""Fleet tracking: GPS devices, provider connections, and the addresses Traccar forwards to — /api/v1/fleet

Phase 2 of the GPS fleet upgrade (docs/plans/2026-10-14-gps-fleet-tracking-design.md).
How to set it up: docs/FLEET_GPS_PROVIDERS.md.

    POST /ingest/traccar                    the platform's own Traccar forwards here (X-Fleet-Secret)
    POST /ingest/traccar/{connection_id}    an organisation's own Traccar forwards here (its own secret)
    GET|PUT|DELETE /vehicles/{id}/device    a vehicle's device, and what is known of it
    POST /vehicles/{id}/device/test         ask the provider about the device
    /providers ...                          an organisation's own Traccar connections
    GET|PUT /settings                       when a vehicle counts as stale and offline

The forwarding addresses take no sign-in: the secret is what lets a position in,
and the device's ID is what says whose vehicle it is (a database function that
answers only that). A position that is refused is answered 200 with the reason,
so Traccar does not send it again and again; only a server fault is not, so that
Traccar retries it.

Permissions: gps:read sees a vehicle's device and status; fleet:device:manage
sets devices and connections and tests them; fleet:alerts:manage sets the limits.
No secret, password or token is ever in an answer, except a connection's
forwarding secret, once, when it is made or replaced.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import _client_ip, get_db_with_tenant, get_raw_db
from app.routers.operations_board import held_by
from app.services import fleet_alerts
from app.services import fleet_tracking as ft
from app.services import fleet_vehicles as fv
from app.services.audit import write_audit_log
from app.services.fleet_providers import (
    BUILTIN, Connection, ForwardRefused, adapter, builtin_traccar, source_key,
)

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])

_READ = [Depends(require_permission("gps:read"))]
_DEVICES = [Depends(require_permission("fleet:device:manage"))]
_LIMITS = [Depends(require_permission("fleet:alerts:manage"))]

MOST_BODY = 64 * 1024
DEVICE_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,100}$")


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


# ── what Traccar forwards ─────────────────────────────────────────────────────

async def _body(request: Request) -> object:
    raw = await request.body()
    if len(raw) > MOST_BODY:
        raise HTTPException(413, "A forwarded position is at most 64 KB")
    try:
        return json.loads(raw or b"null")
    except ValueError:
        return None


async def _take(db: AsyncSession, request: Request, key: str, owner_tenant: str | None) -> dict:
    """One forwarded position: whose it is, and whether it is kept. `owner_tenant` is the organisation a
    connection belongs to; a device registered under that connection must be that organisation's."""
    traccar = adapter("traccar")
    try:
        pos = traccar.parse_forward(await _body(request))
    except ForwardRefused as refused:
        if refused.device_identifier:
            owner = (await db.execute(text("SELECT * FROM fleet_device_owner(:s, :d)"),
                                      {"s": key, "d": refused.device_identifier})).mappings().first()
            if owner and (owner_tenant is None or str(owner["tenant_id"]) == owner_tenant):
                await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(owner["tenant_id"])})
                await ft.note_reject(db, str(owner["tenant_id"]), str(owner["vehicle_id"]), refused.reason, refused.detail)
                await db.commit()
                return {"accepted": False, "reason": refused.reason}
        await db.execute(text("SELECT fleet_note_unknown_device(:s, :d, :r)"),
                         {"s": key, "d": refused.device_identifier or "-", "r": refused.reason})
        await db.commit()
        return {"accepted": False, "reason": refused.reason}
    owner = (await db.execute(text("SELECT * FROM fleet_device_owner(:s, :d)"),
                              {"s": key, "d": pos.device_identifier})).mappings().first()
    if owner is None or (owner_tenant is not None and str(owner["tenant_id"]) != owner_tenant):
        await db.execute(text("SELECT fleet_note_unknown_device(:s, :d, 'unknown_device')"),
                         {"s": key, "d": pos.device_identifier})
        await db.commit()
        return {"accepted": False, "reason": "unknown_device"}
    tenant_id, vehicle_id = str(owner["tenant_id"]), str(owner["vehicle_id"])
    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
    result = await ft.accept(db, tenant_id, vehicle_id, pos, source="traccar")
    await db.commit()
    redis = getattr(request.app.state, "redis", None)
    if result.get("moved") and redis is not None:
        await ft.publish(redis, tenant_id, [vehicle_id])
    await fleet_alerts.announce(redis, tenant_id, result.get("raised", []))
    return {k: v for k, v in result.items() if k in ("accepted", "reason", "latest", "status")}


@router.post("/ingest/traccar")
async def ingest_builtin(request: Request, db: AsyncSession = Depends(get_raw_db)):
    """The platform's own Traccar forwards each position here, with TRACCAR_FORWARD_SECRET in X-Fleet-Secret."""
    expected = settings.TRACCAR_FORWARD_SECRET
    if not expected:
        raise HTTPException(503, "This server has no Traccar forwarding set up (TRACCAR_FORWARD_SECRET)")
    if not hmac.compare_digest(request.headers.get("X-Fleet-Secret", "").encode(), expected.encode()):
        raise HTTPException(401, "Not a forwarding this server accepts")
    return await _take(db, request, BUILTIN, None)


@router.post("/ingest/traccar/{connection_id}")
async def ingest_connection(connection_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_raw_db)):
    """An organisation's own Traccar forwards each position here, with the connection's own secret."""
    conn = (await db.execute(text("SELECT * FROM fleet_connection_owner(CAST(:c AS uuid))"),
                             {"c": str(connection_id)})).mappings().first()
    given = request.headers.get("X-Fleet-Secret", "")
    if conn is None or not conn["is_active"] or not hmac.compare_digest(_hash(given), conn["forward_secret_hash"]):
        raise HTTPException(401, "Not a forwarding this server accepts")
    return await _take(db, request, source_key(str(connection_id)), str(conn["tenant_id"]))


# ── the live map ──────────────────────────────────────────────────────────────

MOST_ON_MAP = 2000


@router.get("/map", dependencies=_READ)
async def fleet_map(
    ids: str | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Every vehicle the person may see that is not archived, with its tracking status worked out now and its last
    known place where it has one. `ids` (comma-separated) asks for just those - what a screen fetches when the live
    connection says they moved, so the live connection never carries a position."""
    params: dict = {**(await ft.limits(db)), "most": MOST_ON_MAP}
    where = ["v.archived_at IS NULL"]
    scope = site_scope_clause(allowed, "v.site_id", params)
    if scope:
        where.append(scope)
    if ids:
        try:
            wanted = [str(uuid.UUID(i.strip())) for i in ids.split(",") if i.strip()]
        except ValueError:
            raise HTTPException(422, "ids is a comma-separated list of vehicle ids") from None
        if len(wanted) > 200:
            raise HTTPException(422, "ids holds at most 200 vehicles")
        where.append("v.id = ANY(CAST(:ids AS uuid[]))")
        params["ids"] = wanted
    rows = (await db.execute(text(f"""
        SELECT v.id, v.name, v.plate_number, v.vehicle_type, v.site_id, s.name AS site_name,
               coalesce(d.full_name, v.driver_name) AS driver_name, v.service_status,
               {ft.status_sql('v')} AS tracking_status, v.current_status AS motion,
               CASE WHEN v.last_fix_at IS NOT NULL THEN v.last_lat END AS last_lat,
               CASE WHEN v.last_fix_at IS NOT NULL THEN v.last_lon END AS last_lon,
               v.last_fix_at, v.last_speed, v.last_heading, v.last_accuracy
          FROM vehicles v
          LEFT JOIN sites s ON s.id = v.site_id
          LEFT JOIN users d ON d.id = v.assigned_driver_user_id
         WHERE {' AND '.join(where)}
         ORDER BY v.plate_key NULLS LAST, v.name
         LIMIT :most
    """), params)).mappings().all()
    items = []
    for r in rows:
        item = {**dict(r), "type_label": fv.TYPE_LABEL.get(r["vehicle_type"], r["vehicle_type"]),
                "status_label": ft.STATUS_LABEL[r["tracking_status"]]}
        # Moving or stopped is only said of a vehicle that is being heard from.
        if r["tracking_status"] != "online":
            item["motion"] = None
        items.append(item)
    return {"items": items, "limits": {k: params[k] for k in ("online_minutes", "offline_minutes")},
            "at": datetime.now(timezone.utc)}


# ── a vehicle's device ────────────────────────────────────────────────────────

async def _connection(db: AsyncSession, connection_id: str | None) -> tuple[Connection | None, dict | None]:
    """The provider a device reports through: an organisation's connection, or the platform's Traccar."""
    if connection_id is None:
        return builtin_traccar(), None
    row = (await db.execute(text("SELECT * FROM fleet_provider_connections WHERE id = CAST(:c AS uuid)"),
                            {"c": connection_id})).mappings().first()
    if row is None:
        return None, None
    return Connection(row["base_url"], row["auth_kind"], row["username"], decrypt_secret(row["secret_encrypted"])), dict(row)


@router.get("/vehicles/{vehicle_id}/device", dependencies=_READ)
async def read_device(
    vehicle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The vehicle's device, its status worked out now, its last known fix, and what was refused."""
    v = await fv.visible_vehicle(db, str(vehicle_id), allowed)
    bounds = await ft.limits(db)
    now = datetime.now(timezone.utc)
    status = ft.status_of_row(v, bounds, now)
    rejects = (await db.execute(text("""
        SELECT reason, times, first_at, last_at FROM fleet_position_rejects
         WHERE vehicle_id = CAST(:v AS uuid) ORDER BY last_at DESC
    """), {"v": str(vehicle_id)})).mappings().all()
    changes = (await db.execute(text("""
        SELECT from_status, to_status, changed_at FROM fleet_status_changes
         WHERE vehicle_id = CAST(:v AS uuid) ORDER BY changed_at DESC LIMIT 10
    """), {"v": str(vehicle_id)})).mappings().all()
    provider = None
    if v["provider_connection_id"]:
        provider = (await db.execute(text("SELECT id, name, is_active FROM fleet_provider_connections WHERE id = :c"),
                                     {"c": v["provider_connection_id"]})).mappings().first()
    held = await held_by(db, token.role_id, ("fleet:device:manage",))
    return {
        "vehicle_id": str(vehicle_id), "archived": v["archived_at"] is not None,
        "tracking_enabled": v["tracking_enabled"], "tracking_source": v["tracking_source"],
        "device_identifier": v["device_identifier"], "device_assigned_at": v["device_assigned_at"],
        "provider": dict(provider) if provider else ({"id": None, "name": "The platform's Traccar"}
                                                     if v["device_identifier"] else None),
        "status": status, "status_label": ft.STATUS_LABEL[status], "limits": bounds,
        "last_fix_at": v["last_fix_at"], "last_received_at": v["last_received_at"],
        "last_lat": v["last_lat"], "last_lon": v["last_lon"], "last_speed": v["last_speed"],
        "last_heading": v["last_heading"], "last_accuracy": v["last_accuracy"], "last_ignition": v["last_ignition"],
        "last_satellites": v["last_satellites"], "motion": v["current_status"] if status == "online" else None,
        "rejects": [{**dict(r), "label": ft.REASON_WORDS.get(r["reason"], r["reason"])} for r in rejects],
        "status_changes": [dict(c) for c in changes],
        "builtin_available": builtin_traccar() is not None and bool(settings.TRACCAR_FORWARD_SECRET),
        "may_manage": "fleet:device:manage" in held,
    }


@router.put("/vehicles/{vehicle_id}/device", dependencies=_DEVICES)
async def set_device(
    vehicle_id: uuid.UUID,
    request: Request,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Link a device to the vehicle (or change or remove it), and switch tracking on or off. A device is one
    vehicle's on the whole platform. With `register_with_provider`, the provider is told about it too."""
    unknown = set(body) - {"tracking_enabled", "device_identifier", "provider_connection_id", "register_with_provider"}
    if unknown:
        raise HTTPException(422, f"Not a device setting: {', '.join(sorted(unknown))}")
    v = await fv.visible_vehicle(db, str(vehicle_id), allowed)
    if v["archived_at"] is not None:
        raise HTTPException(409, "This vehicle is archived. Restore it before giving it a device.")
    identifier = body.get("device_identifier", v["device_identifier"])
    identifier = identifier.strip() if isinstance(identifier, str) else identifier
    if identifier in ("", None):
        identifier = None
    elif not isinstance(identifier, str) or not DEVICE_ID.fullmatch(identifier):
        raise HTTPException(422, "The device ID is up to 100 letters, digits and . _ : - (an IMEI, or the ID in the phone app)")
    connection_id = body.get("provider_connection_id", str(v["provider_connection_id"]) if v["provider_connection_id"] else None)
    connection_id = str(uuid.UUID(str(connection_id))) if connection_id else None
    conn, conn_row = await _connection(db, connection_id)
    if connection_id and (conn_row is None or not conn_row["is_active"]):
        raise HTTPException(422, "That provider connection is not one of this organisation's, in use")
    enabled = bool(body.get("tracking_enabled", v["tracking_enabled"]))
    if enabled and identifier is None:
        raise HTTPException(422, "Give the device ID to switch tracking on")
    key = source_key(connection_id)
    try:
        async with db.begin_nested():
            if identifier is None:
                await db.execute(text("SELECT fleet_device_release(CAST(:v AS uuid))"), {"v": str(vehicle_id)})
            else:
                await db.execute(text("SELECT fleet_device_claim(:s, :d, CAST(:v AS uuid))"),
                                 {"s": key, "d": identifier, "v": str(vehicle_id)})
    except DBAPIError as exc:
        if "fleet device in use" in str(exc.orig):
            raise HTTPException(409, "This device is already linked to another vehicle") from None
        raise
    changed_device = identifier != v["device_identifier"] or connection_id != (str(v["provider_connection_id"]) if v["provider_connection_id"] else None)
    row = (await db.execute(text("""
        UPDATE vehicles
           SET tracking_enabled = CAST(:enabled AS boolean), device_identifier = CAST(:d AS varchar),
               provider_connection_id = CAST(:c AS uuid),
               tracking_source = CASE WHEN CAST(:d AS varchar) IS NULL THEN NULL ELSE 'traccar' END,
               device_assigned_at = CASE WHEN CAST(:changed AS boolean)
                                         THEN (CASE WHEN CAST(:d AS varchar) IS NULL THEN NULL ELSE now() END)
                                         ELSE device_assigned_at END,
               last_fix_at = CASE WHEN CAST(:changed AS boolean) THEN NULL ELSE last_fix_at END,
               updated_at = now()
         WHERE id = CAST(:v AS uuid) RETURNING *
    """), {"enabled": enabled, "d": identifier, "c": connection_id, "changed": changed_device, "v": str(vehicle_id)})).mappings().one()
    now = datetime.now(timezone.utc)
    await ft.record_status(db, str(token.tenant_id), row, ft.status_of_row(row, await ft.limits(db), now), now)
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id), action="fleet.device.changed",
                          resource_type="vehicle", resource_id=str(vehicle_id), ip_address=_client_ip(request),
                          detail={"device_identifier": identifier, "was": v["device_identifier"], "tracking_enabled": enabled,
                                  "provider": "own connection" if connection_id else "platform Traccar"})
    await db.commit()
    registered = None
    if identifier and body.get("register_with_provider"):
        if conn is None:
            registered = {"ok": False, "message": "This server has no Traccar set up to register the device in"}
        else:
            label = f"{row['plate_number'] or ''} {row['name']}".strip()
            outcome = await adapter("traccar").register_device(conn, identifier, label)
            registered = {"ok": outcome.ok, "message": outcome.message}
    return {"ok": True, "device_identifier": identifier, "tracking_enabled": enabled, "registered": registered}


@router.delete("/vehicles/{vehicle_id}/device", dependencies=_DEVICES)
async def remove_device(
    vehicle_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Unlink the device and switch tracking off. The positions already kept stay."""
    return await set_device(vehicle_id, request, {"device_identifier": None, "tracking_enabled": False}, db, token, allowed)


@router.post("/vehicles/{vehicle_id}/device/test", dependencies=_DEVICES)
async def test_device(
    vehicle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Ask the provider whether it knows the device and when it last heard from it; say when we last did."""
    v = await fv.visible_vehicle(db, str(vehicle_id), allowed)
    if not v["device_identifier"]:
        raise HTTPException(409, "This vehicle has no device to test")
    conn, _ = await _connection(db, str(v["provider_connection_id"]) if v["provider_connection_id"] else None)
    if conn is None:
        provider = {"ok": False, "message": "There is no Traccar to ask: the platform's is not set up"}
    else:
        outcome = await adapter("traccar").device(conn, v["device_identifier"])
        provider = {"ok": outcome.ok, "message": outcome.message, **outcome.detail}
    return {"provider": provider, "last_received_at": v["last_received_at"], "last_fix_at": v["last_fix_at"]}


# ── an organisation's own Traccar ─────────────────────────────────────────────

def _check_url(url: object) -> str:
    """A provider's address: http(s), and not this server's own machine or a cloud's metadata service."""
    if not isinstance(url, str) or len(url) > 500:
        raise HTTPException(422, "base_url is the provider's web address")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(422, "base_url starts with http:// or https://")
    host = parsed.hostname.lower()
    if host in ("localhost", "metadata.google.internal") or host.endswith(".localhost"):
        raise HTTPException(422, "That address is not one a provider can be at")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            raise HTTPException(422, "That address is not one a provider can be at")
    except ValueError:
        pass
    return url.strip().rstrip("/")


def _public(row) -> dict:
    out = {k: v for k, v in dict(row).items() if k not in ("secret_encrypted", "forward_secret_hash")}
    out["forward_path"] = f"/api/v1/fleet/ingest/traccar/{row['id']}"
    return out


@router.get("/providers", dependencies=_DEVICES)
async def list_providers(db: AsyncSession = Depends(get_db_with_tenant)):
    rows = (await db.execute(text("SELECT * FROM fleet_provider_connections ORDER BY is_active DESC, name"))).mappings().all()
    return {"items": [_public(r) for r in rows],
            "builtin": {"available": builtin_traccar() is not None, "forwarding": bool(settings.TRACCAR_FORWARD_SECRET),
                        "forward_path": "/api/v1/fleet/ingest/traccar"}}


def _connection_values(body: dict, *, creating: bool) -> dict:
    unknown = set(body) - {"name", "base_url", "auth_kind", "username", "secret"}
    if unknown:
        raise HTTPException(422, f"Not a connection setting: {', '.join(sorted(unknown))}")
    out: dict = {}
    if creating or "name" in body:
        name = body.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise HTTPException(422, "name is required, at most 120 characters")
        out["name"] = name.strip()
    if creating or "base_url" in body:
        out["base_url"] = _check_url(body.get("base_url"))
    if creating or "auth_kind" in body:
        if body.get("auth_kind") not in ("token", "basic"):
            raise HTTPException(422, "auth_kind is token or basic")
        out["auth_kind"] = body["auth_kind"]
    if "username" in body:
        out["username"] = (body["username"] or "").strip() or None
    if creating or body.get("secret"):
        secret = body.get("secret")
        if not isinstance(secret, str) or not secret or len(secret) > 1000:
            raise HTTPException(422, "secret is the token or password")
        out["secret_encrypted"] = encrypt_secret(secret)
    return out


@router.post("/providers", status_code=201, dependencies=_DEVICES)
async def create_provider(
    request: Request,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Connect the organisation's own Traccar. The answer holds its forwarding secret - once."""
    values = _connection_values(body, creating=True)
    if values["auth_kind"] == "basic" and not values.get("username"):
        raise HTTPException(422, "A user and password sign-in needs the user")
    forward_secret = secrets.token_urlsafe(32)
    row = (await db.execute(text("""
        INSERT INTO fleet_provider_connections (tenant_id, name, base_url, auth_kind, username, secret_encrypted,
                                                forward_secret_hash, created_by_user_id)
        VALUES (:t, :name, :base_url, :auth_kind, :username, :secret_encrypted, :h, CAST(:u AS uuid)) RETURNING *
    """), {"t": str(token.tenant_id), "u": str(token.user_id), "h": _hash(forward_secret), "username": None,
           **values})).mappings().one()
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id), action="fleet.provider.created",
                          resource_type="fleet_provider_connection", resource_id=str(row["id"]),
                          ip_address=_client_ip(request), detail={"name": row["name"], "base_url": row["base_url"]})
    await db.commit()
    return {**_public(row), "forward_secret": forward_secret,
            "forward_secret_note": "Shown this once. Put it in this Traccar's FORWARD_HEADER as X-Fleet-Secret."}


async def _provider(db: AsyncSession, provider_id: uuid.UUID) -> dict:
    row = (await db.execute(text("SELECT * FROM fleet_provider_connections WHERE id = CAST(:i AS uuid)"),
                            {"i": str(provider_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Provider connection not found")
    return dict(row)


@router.patch("/providers/{provider_id}", dependencies=_DEVICES)
async def change_provider(
    provider_id: uuid.UUID,
    request: Request,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    current = await _provider(db, provider_id)
    if not current["is_active"]:
        raise HTTPException(409, "This connection is retired")
    values = _connection_values(body, creating=False)
    if not values:
        raise HTTPException(422, "Nothing to change")
    sets = ", ".join(f"{k} = :{k}" for k in values)
    row = (await db.execute(text(f"""
        UPDATE fleet_provider_connections SET {sets}, updated_at = now() WHERE id = CAST(:i AS uuid) RETURNING *
    """), {**values, "i": str(provider_id)})).mappings().one()
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id), action="fleet.provider.changed",
                          resource_type="fleet_provider_connection", resource_id=str(provider_id),
                          ip_address=_client_ip(request),
                          detail={"changed": sorted("secret" if k == "secret_encrypted" else k for k in values)})
    await db.commit()
    return _public(row)


@router.post("/providers/{provider_id}/forward-secret", dependencies=_DEVICES)
async def replace_forward_secret(
    provider_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """A new forwarding secret, shown once; the old one stops working at once."""
    current = await _provider(db, provider_id)
    if not current["is_active"]:
        raise HTTPException(409, "This connection is retired")
    forward_secret = secrets.token_urlsafe(32)
    await db.execute(text("UPDATE fleet_provider_connections SET forward_secret_hash = :h, updated_at = now() "
                          "WHERE id = CAST(:i AS uuid)"), {"h": _hash(forward_secret), "i": str(provider_id)})
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="fleet.provider.forward_secret_replaced", resource_type="fleet_provider_connection",
                          resource_id=str(provider_id), ip_address=_client_ip(request), detail={"name": current["name"]})
    await db.commit()
    return {"forward_secret": forward_secret, "forward_path": f"/api/v1/fleet/ingest/traccar/{provider_id}"}


@router.post("/providers/{provider_id}/test", dependencies=_DEVICES)
async def test_provider(provider_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    current = await _provider(db, provider_id)
    conn = Connection(current["base_url"], current["auth_kind"], current["username"], decrypt_secret(current["secret_encrypted"]))
    outcome = await adapter(current["provider"]).test(conn)
    await db.execute(text("""
        UPDATE fleet_provider_connections SET last_test_at = now(), last_test_ok = :ok, last_test_message = :m
         WHERE id = CAST(:i AS uuid)
    """), {"ok": outcome.ok, "m": outcome.message[:500], "i": str(provider_id)})
    await db.commit()
    return {"ok": outcome.ok, "message": outcome.message, **outcome.detail}


@router.post("/providers/{provider_id}/retire", dependencies=_DEVICES)
async def retire_provider(
    provider_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Its forwarding stops being accepted. Vehicles linked through it keep their positions; their devices
    must be linked again elsewhere."""
    current = await _provider(db, provider_id)
    if not current["is_active"]:
        raise HTTPException(409, "This connection is already retired")
    await db.execute(text("UPDATE fleet_provider_connections SET is_active = FALSE, updated_at = now() "
                          "WHERE id = CAST(:i AS uuid)"), {"i": str(provider_id)})
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id), action="fleet.provider.retired",
                          resource_type="fleet_provider_connection", resource_id=str(provider_id),
                          ip_address=_client_ip(request), detail={"name": current["name"]})
    await db.commit()
    return {"ok": True}


# ── when a vehicle counts as stale and offline ────────────────────────────────

@router.get("/settings", dependencies=_READ)
async def read_settings(db: AsyncSession = Depends(get_db_with_tenant)):
    return await ft.limits(db)


@router.put("/settings", dependencies=_LIMITS)
async def set_settings(
    request: Request,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    try:
        values = ft.check_limits(body.get("online_minutes"), body.get("offline_minutes"))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    for key, value in ((ft.ONLINE_KEY, values["online_minutes"]), (ft.OFFLINE_KEY, values["offline_minutes"])):
        await db.execute(text("""
            INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id)
            VALUES (:t, :k, CAST(:v AS jsonb), CAST(:u AS uuid))
            ON CONFLICT (tenant_id, setting_key)
            DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_by_user_id = EXCLUDED.updated_by_user_id,
                          updated_at = now()
        """), {"t": str(token.tenant_id), "k": key, "v": json.dumps(value), "u": str(token.user_id)})
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id), action="fleet.settings.changed",
                          resource_type="tenant_settings", ip_address=_client_ip(request), detail=values)
    await db.commit()
    return values
