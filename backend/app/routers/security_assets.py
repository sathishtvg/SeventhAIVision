"""Security assets and the health of the devices behind them.

Phase 8 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
DEVICE_HEALTH_ARCHITECTURE.md.

  GET  /health                       every device the caller may see, with its state and why
  GET  /health/{kind}/{device_id}    one device: its state, what was kept of it, how long it was down
  GET  /                             the register
  POST /                             add an asset
  GET  /unregistered                 devices the platform knows that are not in the register
  POST /register-devices             put those in the register, as they are known
  GET  /{id}                         one asset, with the health of the device it is
  PATCH /{id}                        change what is recorded of it
  POST /{id}/retire|restore          take it out of service, with why; or put it back

A READING IS MADE OF WHAT THE PLATFORM IS TOLD (services/device_health.py). The
answer names what is not measured wherever a reading is given.

AN ASSET IS A RECORD, NOT A SWITCH. Nothing here changes a camera, a recorder,
a sensor, a drone, a gateway or a panel: those are kept where they always were.
An asset is retired, never removed.

EVERY CHANGE IS BY A PERSON WHO IS SIGNED IN, and is written to the audit log.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Literal, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import clamp
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services import device_health, intel_audit

KINDS = ("CAMERA", "NVR", "SERVER", "EDGE_GATEWAY", "ACCESS_CONTROLLER", "ALARM_PANEL", "DRONE", "SENSOR", "UPS",
         "NETWORK", "OTHER")
KIND_LABEL = {**device_health.KIND_LABEL, "SERVER": "Server", "ACCESS_CONTROLLER": "Access controller", "UPS": "UPS",
              "NETWORK": "Network equipment", "OTHER": "Other"}
STATUSES = ("IN_SERVICE", "UNDER_REPAIR", "SPARE", "RETIRED")
PERMISSIONS = ("asset:read", "asset:manage", "maintenance:read", "maintenance:manage")
MAX_AT_ONCE = 200
NOT_MONITORED = "The platform does not know this as a device, so it has no reading of it."

router = APIRouter(prefix="/api/v1/security-assets", tags=["security-assets"])
_READ = [Depends(require_permission("asset:read"))]
_MANAGE = [Depends(require_permission("asset:manage"))]

#: For each kind the platform knows as a device: its table, and what of it goes into the register.
_DEVICES = {
    "CAMERA": ("cameras", "d.site_id", "NULL", "NULL", "NULL", "d.location"),
    "NVR": ("nvr_connections", "NULL::uuid", "NULL", "NULL", "NULL", "NULL"),
    "SENSOR": ("iot_sensors", "d.site_id", "NULL", "NULL", "NULL", "d.location"),
    "DRONE": ("drones", "d.site_id", "d.manufacturer", "d.model", "d.serial_number", "NULL"),
    "EDGE_GATEWAY": ("drone_edge_gateways", "d.site_id", "NULL", "NULL", "NULL", "NULL"),
    "ALARM_PANEL": ("alarm_panels", "d.site_id", "NULL", "d.model", "d.serial_number", "NULL"),
}


def _a_person(token: TokenPayload) -> None:
    """The register says who entered and who changed what."""
    if token.via_api_key:
        raise HTTPException(403, "This is done by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "This is done by the organisation's own staff, not from a support session.")


async def _held(db: AsyncSession, role_id: int) -> frozenset[str]:
    rows = await db.execute(text("""
        SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
         WHERE rp.role_id = :role AND p.code = ANY(CAST(:codes AS text[]))
    """), {"role": role_id, "codes": list(PERMISSIONS)})
    return frozenset(r.code for r in rows)


# ─── Health ──────────────────────────────────────────────────────────────────

@router.get("/health", dependencies=_READ)
async def read_health(
    site_id: uuid.UUID | None = Query(None),
    kind: list[str] = Query(default=[]),
    state: list[str] = Query(default=[]),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Every device the caller may see, what wants somebody first, each with
    its state, why, and since when. With it: how many are in each state, and
    what the platform does not measure."""
    for value, known, what in ((kind, device_health.KINDS, "kind of device"), (state, device_health.STATES, "state")):
        unknown = [v for v in value if v not in known]
        if unknown:
            raise HTTPException(422, f"Unknown {what} '{unknown[0]}'.")
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    now = datetime.now(timezone.utc)
    items = await device_health.readings(db, now, allowed=allowed, site_id=site_id, kinds=kind or None)
    return {"items": [r for r in items if not state or r["state"] in state], "summary": device_health.summary(items),
            "as_of": now, "note": device_health.NOTE, "not_measured": list(device_health.NOT_MEASURED),
            "kinds": [{"key": k, "label": device_health.KIND_LABEL[k]} for k in device_health.KINDS],
            "states": list(device_health.STATES)}


@router.get("/health/{kind}/{device_id:uuid}", dependencies=_READ)
async def read_device(
    kind: str,
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One device: its state and why, the states that were kept of it, and how
    long it was down in the last week and month — as far back as is known."""
    if kind not in device_health.KINDS:
        raise HTTPException(404, "Device not found")
    now = datetime.now(timezone.utc)
    found = await device_health.readings(db, now, allowed=allowed, kinds=[kind], device_id=device_id)
    if not found:
        raise HTTPException(404, "Device not found")
    return {**found[0], "as_of": now, "note": device_health.NOTE, "not_measured": list(device_health.NOT_MEASURED),
            "history": await device_health.history(db, kind, device_id),
            "down": [await device_health.availability(db, kind, device_id, days, now) for days in (7, 30)]}


# ─── The register ────────────────────────────────────────────────────────────

_LINKS = tuple(device_health.LINK.items())
_ASSET = f"""
    SELECT a.id, a.asset_code, a.kind, a.name, a.make, a.model, a.serial_number, a.location, a.site_id,
           s.name AS site_name, a.place_id, p.name AS place_name, a.vendor, a.installed_on, a.warranty_until,
           a.warranty_until - (now() AT TIME ZONE t.timezone)::date AS warranty_days_left,
           a.status, a.notes, a.created_at, cu.full_name AS created_by_name, a.updated_at,
           uu.full_name AS updated_by_name, a.retired_at, ru.full_name AS retired_by_name, a.retire_reason,
           COALESCE({', '.join('a.' + column for _, column in _LINKS)}) AS device_id,
           (SELECT count(*) FROM maintenance_work_orders w
             WHERE w.asset_id = a.id AND w.state IN ('OPEN', 'IN_PROGRESS')) AS open_orders
      FROM asset_register a
      JOIN tenants t ON t.id = a.tenant_id
      LEFT JOIN sites s ON s.id = a.site_id
      LEFT JOIN site_places p ON p.id = a.place_id
      LEFT JOIN users cu ON cu.id = a.created_by_user_id
      LEFT JOIN users uu ON uu.id = a.updated_by_user_id
      LEFT JOIN users ru ON ru.id = a.retired_by_user_id
"""


def _warranty(row: Mapping) -> str:
    if row["warranty_until"] is None:
        return "NOT_RECORDED"
    return "OUT" if row["warranty_days_left"] < 0 else "IN"


def _asset(row: Mapping, health: Mapping | None, held: frozenset[str]) -> dict:
    monitored = row["device_id"] is not None and row["kind"] in device_health.KINDS
    out = {k: row[k] for k in row.keys() if k != "open_orders"}
    return {**out, "kind_label": KIND_LABEL[row["kind"]], "warranty": _warranty(row), "monitored": monitored,
            # The reading of the device it is — or that there is none, and why.
            "health": ({k: health[k] for k in ("state", "reasons", "since", "since_is_when_first_read")} if health else None),
            "not_monitored": None if monitored else NOT_MONITORED,
            "open_orders": row["open_orders"] if "maintenance:read" in held else None,
            "may": {"change": "asset:manage" in held, "retire": "asset:manage" in held and row["status"] != "RETIRED",
                    "restore": "asset:manage" in held and row["status"] == "RETIRED"}}


async def _one(db: AsyncSession, asset_id, allowed) -> dict:
    row = (await db.execute(text(f"{_ASSET} WHERE a.id = CAST(:id AS uuid)"), {"id": str(asset_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Asset not found")
    return dict(row)


async def _given(db: AsyncSession, asset_id, allowed, held: frozenset[str]) -> dict:
    row = await _one(db, asset_id, allowed)
    health = None
    if row["device_id"] is not None and row["kind"] in device_health.KINDS:
        found = await device_health.readings(db, datetime.now(timezone.utc), kinds=[row["kind"]],
                                             device_id=row["device_id"])
        health = found[0] if found else None
    return _asset(row, health, held)


@router.get("", dependencies=_READ)
async def list_assets(
    site_id: uuid.UUID | None = Query(None),
    kind: list[str] = Query(default=[]),
    status: Literal["IN_SERVICE", "UNDER_REPAIR", "SPARE", "RETIRED"] | None = Query(None),
    warranty: Literal["IN", "OUT", "NOT_RECORDED"] | None = Query(None),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The register, by its codes: at the sites the caller may see. Retired
    assets are left out unless they are asked for."""
    unknown = [k for k in kind if k not in KINDS]
    if unknown:
        raise HTTPException(422, f"Unknown kind of asset '{unknown[0]}'.")
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    cap, skip = clamp(limit, offset)
    params: dict = {"limit": cap + 1, "offset": skip}
    where = ["a.status = :status" if status else "a.status <> 'RETIRED'"]
    if status:
        params["status"] = status
    scope = site_scope_clause(allowed, "a.site_id", params)
    if scope:
        where.append(scope)
    if site_id is not None:
        where.append("a.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    if kind:
        where.append("a.kind = ANY(:kinds)")
        params["kinds"] = kind
    if warranty is not None:
        today = "(now() AT TIME ZONE t.timezone)::date"
        where.append({"NOT_RECORDED": "a.warranty_until IS NULL", "IN": f"a.warranty_until >= {today}",
                      "OUT": f"a.warranty_until < {today}"}[warranty])
    if q and q.strip():
        # The words as typed: a percent sign or an underscore is looked for, not treated as a wildcard.
        params["q"] = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(" + " OR ".join(f"a.{column} ILIKE :q ESCAPE '\\'" for column in (
            "name", "asset_code", "serial_number", "make", "model", "vendor")) + ")")
    rows = (await db.execute(text(f"{_ASSET} WHERE {' AND '.join(where)} ORDER BY a.asset_code LIMIT :limit OFFSET :offset"),
                             params)).mappings().all()
    held = await _held(db, token.role_id)
    health = {(r["kind"], str(r["device_id"])): r
              for r in await device_health.readings(db, datetime.now(timezone.utc), allowed=allowed)}
    return {"items": [_asset(r, health.get((r["kind"], str(r["device_id"]))), held) for r in rows[:cap]],
            "limit": cap, "offset": skip, "has_more": len(rows) > cap, "can_manage": "asset:manage" in held,
            "kinds": [{"key": k, "label": KIND_LABEL[k], "monitored": k in device_health.KINDS} for k in KINDS],
            "statuses": list(STATUSES)}


class AssetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["CAMERA", "NVR", "SERVER", "EDGE_GATEWAY", "ACCESS_CONTROLLER", "ALARM_PANEL", "DRONE", "SENSOR",
                  "UPS", "NETWORK", "OTHER"]
    name: str = Field(..., min_length=1, max_length=200)
    site_id: uuid.UUID | None = None
    #: The camera, recorder, sensor, drone, gateway or panel it is, when the platform knows it as one.
    device_id: uuid.UUID | None = None
    make: str | None = Field(None, max_length=120)
    model: str | None = Field(None, max_length=120)
    serial_number: str | None = Field(None, max_length=120)
    location: str | None = Field(None, max_length=1000)
    place_id: uuid.UUID | None = None
    vendor: str | None = Field(None, max_length=200)
    installed_on: date | None = None
    warranty_until: date | None = None
    status: Literal["IN_SERVICE", "UNDER_REPAIR", "SPARE"] = "IN_SERVICE"
    notes: str | None = Field(None, max_length=5000)


async def _device(db: AsyncSession, kind: str, device_id, allowed) -> dict:
    """The device an asset is said to be: one the platform knows, of that kind, where the caller may see it."""
    if kind not in _DEVICES:
        raise HTTPException(422, f"The platform does not know a {KIND_LABEL[kind].lower()} as a device. Leave the "
                                 "device out.")
    table, site, make, model, serial, location = _DEVICES[kind]
    row = (await db.execute(text(f"""
        SELECT d.id, d.name, {site} AS site_id, {make} AS make, {model} AS model, {serial} AS serial_number,
               {location} AS location
          FROM {table} d WHERE d.id = CAST(:id AS uuid)
    """), {"id": str(device_id)})).mappings().first()
    if row is None or (kind != "NVR" and not is_site_allowed(allowed, row["site_id"])) or (kind == "NVR" and allowed is not None):
        raise HTTPException(422, f"No {KIND_LABEL[kind].lower()} of that id is known.")
    return dict(row)


async def _place(db: AsyncSession, place_id, site_id) -> None:
    if place_id is None:
        return
    known = (await db.execute(text(
        "SELECT 1 FROM site_places WHERE id = CAST(:p AS uuid) AND site_id = :s AND is_active"),
        {"p": str(place_id), "s": site_id})).scalar()
    if not known:
        raise HTTPException(422, "The place named is not an active place of the asset's site.")


def _dates(installed_on: date | None, warranty_until: date | None) -> None:
    if installed_on and warranty_until and warranty_until < installed_on:
        raise HTTPException(422, "A warranty does not end before the asset was installed.")


async def _next_code(db: AsyncSession) -> str:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('asset:' || current_setting('app.current_tenant')))"))
    number = (await db.execute(text(
        "SELECT COALESCE(max(CAST(substring(asset_code FROM 5) AS integer)), 0) + 1 FROM asset_register"))).scalar()
    return f"AST-{number:04d}"


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


async def _insert(db: AsyncSession, token: TokenPayload, values: Mapping) -> uuid.UUID:
    link = device_health.LINK.get(values["kind"]) if values.get("device_id") else None
    try:
        async with db.begin_nested():
            return (await db.execute(text(f"""
                INSERT INTO asset_register
                       (tenant_id, asset_code, kind, name, site_id, make, model, serial_number, location, place_id,
                        vendor, installed_on, warranty_until, status, notes, created_by_user_id, updated_by_user_id
                        {', ' + link if link else ''})
                VALUES (current_setting('app.current_tenant')::uuid, :code, :kind, :name, :site, :make, :model, :serial,
                        :location, :place, :vendor, :installed, :warranty, :status, :notes, CAST(:who AS uuid),
                        CAST(:who AS uuid) {', :device' if link else ''})
                RETURNING id
            """), {"code": await _next_code(db), "kind": values["kind"], "name": values["name"].strip(),
                   "site": values.get("site_id"), "make": _clean(values.get("make")), "model": _clean(values.get("model")),
                   "serial": _clean(values.get("serial_number")), "location": _clean(values.get("location")),
                   "place": values.get("place_id"), "vendor": _clean(values.get("vendor")),
                   "installed": values.get("installed_on"), "warranty": values.get("warranty_until"),
                   "status": values.get("status", "IN_SERVICE"), "notes": _clean(values.get("notes")),
                   "who": token.user_id, **({"device": values["device_id"]} if link else {})})).scalar()
    except IntegrityError as exc:
        if "uq_asset_" in str(exc.orig):
            raise HTTPException(409, "That device is already in the register.") from exc
        raise


@router.post("", status_code=201, dependencies=_READ + _MANAGE)
async def add_asset(
    body: AssetBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Add an asset to the register. Naming the device it is gives it that
    device's health; an asset the platform does not know as a device is still
    an asset — a UPS, a switch, a server — and has no reading."""
    _a_person(token)
    if not body.name.strip():
        raise HTTPException(422, "An asset has a name.")
    _dates(body.installed_on, body.warranty_until)
    site_id = body.site_id
    if body.device_id is not None:
        device = await _device(db, body.kind, body.device_id, allowed)
        if site_id is not None and device["site_id"] is not None and device["site_id"] != site_id:
            raise HTTPException(422, "That device is at another site.")
        site_id = site_id or device["site_id"]
    if site_id is not None:
        known = (await db.execute(text("SELECT 1 FROM sites WHERE id = :s"), {"s": site_id})).scalar()
        if not known or not is_site_allowed(allowed, site_id):
            raise HTTPException(404, "Site not found")
    elif allowed is not None:
        raise HTTPException(422, "Say which site the asset is at.")
    await _place(db, body.place_id, site_id)
    new_id = await _insert(db, token, {**body.model_dump(), "site_id": site_id})
    held = await _held(db, token.role_id)
    answer = await _given(db, new_id, allowed, held)
    await intel_audit.record(db, request, token, "asset.create", "security_asset", new_id, site_id=site_id,
                             detail={"code": answer["asset_code"], "kind": body.kind, "monitored": answer["monitored"]})
    await db.commit()
    return answer


async def _unregistered(db: AsyncSession, allowed, only: dict[str, set[str]] | None = None) -> list[dict]:
    """The devices the platform knows that the register does not have."""
    out: list[dict] = []
    for kind, (table, site, make, model, serial, location) in _DEVICES.items():
        if kind == "NVR" and allowed is not None:
            continue
        if only is not None and kind not in only:
            continue
        params: dict = {}
        where = ["a.id IS NULL"]
        if kind != "NVR":
            scope = site_scope_clause(allowed, "d.site_id", params)
            if scope:
                where.append(scope)
        if only is not None:
            where.append("d.id = ANY(:ids)")
            params["ids"] = [uuid.UUID(i) for i in only[kind]]
        rows = await db.execute(text(f"""
            SELECT d.id, d.name, {site} AS site_id, {make} AS make, {model} AS model, {serial} AS serial_number,
                   {location} AS location
              FROM {table} d LEFT JOIN asset_register a ON a.{device_health.LINK[kind]} = d.id
             WHERE {' AND '.join(where)} ORDER BY d.name
        """), params)
        out += [{"kind": kind, "kind_label": KIND_LABEL[kind], "device_id": r["id"], **{k: r[k] for k in (
            "name", "site_id", "make", "model", "serial_number", "location")}} for r in rows.mappings()]
    if out:
        names = {r.id: r.name for r in await db.execute(text("SELECT id, name FROM sites"))}
        for item in out:
            item["site_name"] = names.get(item["site_id"])
    return out


@router.get("/unregistered", dependencies=_READ + _MANAGE)
async def list_unregistered(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The cameras, recorders, sensors, drones, gateways and panels the
    platform knows that are not in the register."""
    return {"items": await _unregistered(db, allowed)}


class DeviceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["CAMERA", "NVR", "SENSOR", "DRONE", "EDGE_GATEWAY", "ALARM_PANEL"]
    device_id: uuid.UUID


class RegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    devices: list[DeviceRef] = Field(..., min_length=1, max_length=MAX_AT_ONCE)


@router.post("/register-devices", status_code=201, dependencies=_READ + _MANAGE)
async def register_devices(
    body: RegisterBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Put devices the platform already knows into the register, each as it is
    known: its name, its site, and its make, model and serial number where the
    device has them. The rest is for a person to fill in. One that is already
    in the register, or is not known, is left and said to be."""
    _a_person(token)
    wanted: dict[str, set[str]] = {}
    for d in body.devices:
        wanted.setdefault(d.kind, set()).add(str(d.device_id))
    found = await _unregistered(db, allowed, wanted)
    made = []
    for item in found:
        new_id = await _insert(db, token, {**item, "status": "IN_SERVICE"})
        made.append({"id": new_id, "kind": item["kind"], "device_id": item["device_id"], "name": item["name"]})
    done = {(m["kind"], str(m["device_id"])) for m in made}
    left = [{"kind": d.kind, "device_id": d.device_id} for d in body.devices if (d.kind, str(d.device_id)) not in done]
    await intel_audit.record(db, request, token, "asset.register_devices", "security_asset", None,
                             detail={"registered": len(made), "left": len(left),
                                     "kinds": sorted({m["kind"] for m in made})})
    codes = {r.id: r.asset_code for r in await db.execute(text(
        "SELECT id, asset_code FROM asset_register WHERE id = ANY(:ids)"), {"ids": [m["id"] for m in made]})}
    await db.commit()
    return {"registered": [{**m, "asset_code": codes[m["id"]]} for m in made], "left": left}


@router.get("/{asset_id:uuid}", dependencies=_READ)
async def read_asset(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One asset: what is recorded of it, the health of the device it is, and
    — for somebody who reads maintenance — the work done and to be done on it."""
    held = await _held(db, token.role_id)
    answer = await _given(db, asset_id, allowed, held)
    orders = None
    if "maintenance:read" in held:
        orders = [dict(r) for r in (await db.execute(text("""
            SELECT w.id, w.number, w.title, w.kind, w.state, w.due_at, w.raised_at, w.completed_at, w.completion_note,
                   w.downtime_minutes
              FROM maintenance_work_orders w WHERE w.asset_id = :a ORDER BY w.raised_at DESC LIMIT 50
        """), {"a": answer["id"]})).mappings()]
    return {**answer, "work_orders": orders, "note": device_health.NOTE,
            "not_measured": list(device_health.NOT_MEASURED) if answer["monitored"] else []}


class ChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=200)
    site_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    make: str | None = Field(None, max_length=120)
    model: str | None = Field(None, max_length=120)
    serial_number: str | None = Field(None, max_length=120)
    location: str | None = Field(None, max_length=1000)
    place_id: uuid.UUID | None = None
    vendor: str | None = Field(None, max_length=200)
    installed_on: date | None = None
    warranty_until: date | None = None
    status: Literal["IN_SERVICE", "UNDER_REPAIR", "SPARE"] | None = None
    notes: str | None = Field(None, max_length=5000)


@router.patch("/{asset_id:uuid}", dependencies=_READ + _MANAGE)
async def change_asset(
    asset_id: uuid.UUID,
    body: ChangeBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Change what is recorded of an asset. What is left out stays; what is
    given as nothing is cleared. Its code and its kind are not changed, and a
    retired asset is restored before it is changed."""
    _a_person(token)
    row = await _one(db, asset_id, allowed)
    if row["status"] == "RETIRED":
        raise HTTPException(409, "This asset is retired. Restore it to change it.")
    given = body.model_fields_set
    if not given:
        raise HTTPException(422, "Nothing to change.")
    for must in ("name", "status"):
        if must in given and getattr(body, must) is None:
            raise HTTPException(422, f"An asset has a {must}.")
    if "name" in given and not body.name.strip():
        raise HTTPException(422, "An asset has a name.")
    site_id = body.site_id if "site_id" in given else row["site_id"]
    if "site_id" in given:
        if site_id is None and allowed is not None:
            raise HTTPException(422, "Say which site the asset is at.")
        if site_id is not None:
            known = (await db.execute(text("SELECT 1 FROM sites WHERE id = :s"), {"s": site_id})).scalar()
            if not known or not is_site_allowed(allowed, site_id):
                raise HTTPException(404, "Site not found")
    sets, params = [], {"id": row["id"], "who": token.user_id}
    if "device_id" in given:
        if body.device_id is not None:
            device = await _device(db, row["kind"], body.device_id, allowed)
            if site_id is not None and device["site_id"] is not None and device["site_id"] != site_id:
                raise HTTPException(422, "That device is at another site.")
        elif row["kind"] not in device_health.LINK:
            raise HTTPException(422, "This kind of asset is not a device the platform knows.")
        sets.append(f"{device_health.LINK[row['kind']]} = :device")
        params["device"] = body.device_id
    place_id = body.place_id if "place_id" in given else row["place_id"]
    if "place_id" in given or ("site_id" in given and place_id is not None):
        await _place(db, place_id, site_id)
    _dates(body.installed_on if "installed_on" in given else row["installed_on"],
           body.warranty_until if "warranty_until" in given else row["warranty_until"])
    for column in ("name", "site_id", "make", "model", "serial_number", "location", "place_id", "vendor",
                   "installed_on", "warranty_until", "status", "notes"):
        if column in given:
            value = getattr(body, column)
            sets.append(f"{column} = :{column}")
            params[column] = _clean(value) if isinstance(value, str) and column != "status" else value
    try:
        async with db.begin_nested():
            await db.execute(text(f"""
                UPDATE asset_register SET {', '.join(sets)}, updated_by_user_id = CAST(:who AS uuid), updated_at = now()
                 WHERE id = :id
            """), params)
    except IntegrityError as exc:
        if "uq_asset_" in str(exc.orig):
            raise HTTPException(409, "That device is already in the register.") from exc
        raise
    await intel_audit.record(db, request, token, "asset.update", "security_asset", row["id"], site_id=site_id,
                             detail={"code": row["asset_code"], "changed": sorted(given)})
    answer = await _given(db, row["id"], allowed, await _held(db, token.role_id))
    await db.commit()
    return answer


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=2000)


@router.post("/{asset_id:uuid}/retire", dependencies=_READ + _MANAGE)
async def retire_asset(
    asset_id: uuid.UUID,
    body: ReasonBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Take an asset out of the register's working list, and say why. It is
    kept, with its history; the device it was, if any, is not touched."""
    _a_person(token)
    row = await _one(db, asset_id, allowed)
    if row["status"] == "RETIRED":
        raise HTTPException(409, "This asset is already retired.")
    if not body.reason.strip():
        raise HTTPException(422, "Say why it is retired.")
    await db.execute(text("""
        UPDATE asset_register
           SET status = 'RETIRED', retired_at = now(), retired_by_user_id = CAST(:who AS uuid), retire_reason = :why,
               updated_by_user_id = CAST(:who AS uuid), updated_at = now()
         WHERE id = :id
    """), {"who": token.user_id, "why": body.reason.strip(), "id": row["id"]})
    await intel_audit.record(db, request, token, "asset.retire", "security_asset", row["id"], site_id=row["site_id"],
                             detail={"code": row["asset_code"], "was": row["status"]})
    answer = await _given(db, row["id"], allowed, await _held(db, token.role_id))
    await db.commit()
    return answer


@router.post("/{asset_id:uuid}/restore", dependencies=_READ + _MANAGE)
async def restore_asset(
    asset_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Put a retired asset back in service."""
    _a_person(token)
    row = await _one(db, asset_id, allowed)
    if row["status"] != "RETIRED":
        raise HTTPException(409, "This asset is not retired.")
    await db.execute(text("""
        UPDATE asset_register
           SET status = 'IN_SERVICE', retired_at = NULL, retired_by_user_id = NULL, retire_reason = NULL,
               updated_by_user_id = CAST(:who AS uuid), updated_at = now()
         WHERE id = :id
    """), {"who": token.user_id, "id": row["id"]})
    await intel_audit.record(db, request, token, "asset.restore", "security_asset", row["id"], site_id=row["site_id"],
                             detail={"code": row["asset_code"]})
    answer = await _given(db, row["id"], allowed, await _held(db, token.role_id))
    await db.commit()
    return answer
