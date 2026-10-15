"""Fleet vehicles: what a vehicle record holds and how it may change.

Shared by the fleet API (`/api/v1/fleet`) and the GPS API it upgrades
(`/api/v1/gps`), so a vehicle is checked and audited the same way whichever is
used. Design: docs/plans/2026-10-14-gps-fleet-tracking-design.md, section 3.

A VEHICLE IS ARCHIVED, NEVER DELETED HERE. Archiving takes it off lists and the
map and keeps everything recorded about it; it can be restored. The database
refuses this application any DELETE on vehicles and their history (0159).

A PLATE IS COMPARED WITHOUT SPACES, DASHES OR CASE, the same way the database
computes `plate_key`, and only one vehicle that is not archived may hold it
within an organisation.

CUSTOM FIELDS are the organisation's. A value is checked against its field's
type; a field that has been retired keeps the values already given under it,
and takes no new ones.

EVERY CHANGE IS ONE LINE IN THE AUDIT LOG: what changed, by whom. Nothing about
a vehicle is changed without it.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.audit import write_audit_log

VEHICLE_TYPES = ("patrol_car", "motorcycle", "van", "truck", "golf_cart", "bicycle", "boat", "custom")
TYPE_LABEL = {"patrol_car": "Patrol car", "motorcycle": "Motorcycle", "van": "Van", "truck": "Truck",
              "golf_cart": "Golf cart", "bicycle": "Bicycle", "boat": "Boat", "custom": "Other"}
SERVICE_STATUSES = ("in_service", "maintenance", "out_of_service")
SERVICE_LABEL = {"in_service": "In service", "maintenance": "In maintenance", "out_of_service": "Out of service"}
FIELD_TYPES = ("text", "number", "date", "select", "boolean")

#: What a person may write on a vehicle. Position, status and the archive are written elsewhere.
EDITABLE = ("name", "plate_number", "vehicle_type", "make", "model", "color", "site_id", "department",
            "assigned_driver_user_id", "driver_name", "driver_phone", "service_status", "notes")
TEXT_LIMITS = {"name": 255, "plate_number": 30, "make": 100, "model": 100, "color": 50, "department": 120,
               "driver_name": 255, "driver_phone": 40, "notes": 4000}
CUSTOM_TEXT_LIMIT = 500
MOST_OPTIONS = 50

ARCHIVED_NOTE = "Archived vehicles keep their history and can be restored. Nothing about them is deleted."


def plate_key(plate: str | None) -> str | None:
    """The plate as it is compared: letters and digits only, upper case. Matches the database's `plate_key`."""
    if plate is None:
        return None
    key = re.sub(r"[^A-Za-z0-9]", "", plate).upper()
    return key or None


def _text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(422, f"{field} must be text")
    value = value.strip()
    if len(value) > TEXT_LIMITS.get(field, 255):
        raise HTTPException(422, f"{field} is at most {TEXT_LIMITS.get(field, 255)} characters")
    return value or None


def clean(body: Mapping[str, Any], *, creating: bool, plate_required: bool) -> dict:
    """The writable fields of a vehicle, checked. Unknown fields are refused, so a misspelt one is not lost."""
    unknown = set(body) - set(EDITABLE) - {"custom_fields"}
    if unknown:
        raise HTTPException(422, f"Not a vehicle field: {', '.join(sorted(unknown))}")
    out: dict = {}
    for field in EDITABLE:
        if field not in body:
            continue
        value = body[field]
        if field in ("site_id", "assigned_driver_user_id"):
            out[field] = str(value) if value else None
        elif field == "vehicle_type":
            if value not in VEHICLE_TYPES:
                raise HTTPException(422, f"vehicle_type is one of {', '.join(VEHICLE_TYPES)}")
            out[field] = value
        elif field == "service_status":
            if value not in SERVICE_STATUSES:
                raise HTTPException(422, f"service_status is one of {', '.join(SERVICE_STATUSES)}")
            out[field] = value
        else:
            out[field] = _text(value, field)
    if creating:
        if not out.get("name"):
            raise HTTPException(422, "name is required")
        out.setdefault("vehicle_type", "patrol_car")
    elif "name" in out and not out["name"]:
        raise HTTPException(422, "name cannot be empty")
    if "plate_number" in out and out["plate_number"] and plate_key(out["plate_number"]) is None:
        raise HTTPException(422, "plate_number must have at least one letter or digit")
    if plate_required and (creating or "plate_number" in out) and not out.get("plate_number"):
        raise HTTPException(422, "The registration number is required")
    return out


async def field_definitions(db: AsyncSession, *, active_only: bool = False) -> list[dict]:
    rows = await db.execute(text(f"""
        SELECT id, field_key, label, field_type, options, is_required, help_text, sort_order, is_active,
               created_at, updated_at
          FROM fleet_vehicle_fields {'WHERE is_active' if active_only else ''}
         ORDER BY is_active DESC, sort_order, label
    """))
    return [dict(r._mapping) for r in rows]


def check_custom(defs: list[dict], given: Mapping[str, Any], current: Mapping[str, Any] | None) -> dict:
    """The custom values a vehicle will hold after `given` is applied to `current`.

    `null` removes a value. A key no field defines is refused. A retired field
    takes no new value, and keeps the one it has. A required field must end up
    with a value - on a new vehicle and on every change after.
    """
    if not isinstance(given, Mapping):
        raise HTTPException(422, "custom_fields must be an object")
    by_key = {d["field_key"]: d for d in defs}
    out = dict(current or {})
    for key, value in given.items():
        d = by_key.get(key)
        if d is None:
            raise HTTPException(422, f"No custom field is called {key}")
        if not d["is_active"]:
            if value != out.get(key):
                raise HTTPException(422, f"{d['label']} is retired and takes no new value")
            continue
        if value is None or value == "":
            out.pop(key, None)
            continue
        out[key] = _custom_value(d, value)
    missing = [d["label"] for d in defs if d["is_active"] and d["is_required"] and out.get(d["field_key"]) in (None, "")]
    if missing:
        raise HTTPException(422, f"Required: {', '.join(missing)}")
    return out


def _custom_value(d: Mapping[str, Any], value: Any) -> Any:
    label, kind = d["label"], d["field_type"]
    if kind == "text":
        if not isinstance(value, str):
            raise HTTPException(422, f"{label} must be text")
        value = value.strip()
        if len(value) > CUSTOM_TEXT_LIMIT:
            raise HTTPException(422, f"{label} is at most {CUSTOM_TEXT_LIMIT} characters")
        return value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise HTTPException(422, f"{label} must be a number")
        return value
    if kind == "date":
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError:
            raise HTTPException(422, f"{label} must be a date (YYYY-MM-DD)") from None
    if kind == "boolean":
        if not isinstance(value, bool):
            raise HTTPException(422, f"{label} must be yes or no")
        return value
    options = d["options"] if isinstance(d["options"], list) else json.loads(d["options"] or "[]")
    if value not in options:
        raise HTTPException(422, f"{label} must be one of {', '.join(map(str, options))}")
    return value


def check_field_definition(body: Mapping[str, Any], *, creating: bool) -> dict:
    allowed = {"field_key", "label", "field_type", "options", "is_required", "help_text", "sort_order"}
    if not creating:
        allowed -= {"field_key", "field_type"}
    unknown = set(body) - allowed
    if unknown:
        raise HTTPException(422, f"Not a field setting here: {', '.join(sorted(unknown))}")
    out: dict = {}
    if creating:
        key = body.get("field_key")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", key):
            raise HTTPException(422, "field_key is lower-case letters, digits and _ , starting with a letter")
        out["field_key"] = key
        if body.get("field_type") not in FIELD_TYPES:
            raise HTTPException(422, f"field_type is one of {', '.join(FIELD_TYPES)}")
        out["field_type"] = body["field_type"]
    if creating or "label" in body:
        label = body.get("label")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
            raise HTTPException(422, "label is required, at most 120 characters")
        out["label"] = label.strip()
    if "options" in body:
        options = body["options"]
        if not isinstance(options, list) or len(options) > MOST_OPTIONS or not all(
                isinstance(o, str) and o.strip() and len(o) <= 120 for o in options):
            raise HTTPException(422, f"options is a list of up to {MOST_OPTIONS} words")
        if len({o.strip() for o in options}) != len(options):
            raise HTTPException(422, "options must not repeat")
        out["options"] = [o.strip() for o in options]
    if "is_required" in body:
        out["is_required"] = bool(body["is_required"])
    if "help_text" in body:
        help_text = body["help_text"]
        if help_text is not None and (not isinstance(help_text, str) or len(help_text) > 255):
            raise HTTPException(422, "help_text is at most 255 characters")
        out["help_text"] = help_text.strip() if help_text else None
    if "sort_order" in body:
        if not isinstance(body["sort_order"], int) or isinstance(body["sort_order"], bool):
            raise HTTPException(422, "sort_order is a whole number")
        out["sort_order"] = body["sort_order"]
    return out


async def check_site(db: AsyncSession, site_id: str | None, allowed: list[str] | None) -> None:
    """A vehicle's site must be one of the organisation's, and one the person may see.

    Somebody limited to sites must give one: a vehicle with no site is one they could not see again.
    """
    if site_id is None:
        if allowed is not None:
            raise HTTPException(422, "Choose one of your sites for the vehicle")
        return
    found = (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"), {"s": site_id})).first()
    if not found or (allowed is not None and site_id not in allowed):
        raise HTTPException(422, "That site is not one you can assign")


async def check_driver(db: AsyncSession, user_id: str | None) -> None:
    if user_id is None:
        return
    found = (await db.execute(text("SELECT 1 FROM users WHERE id = CAST(:u AS uuid)"), {"u": user_id})).first()
    if not found:
        raise HTTPException(422, "The driver is not a person in this organisation")


async def visible_vehicle(db: AsyncSession, vehicle_id: str, allowed: list[str] | None) -> dict:
    """The vehicle, if it exists and the person may see it; otherwise 404 - the same answer for both."""
    row = (await db.execute(text("SELECT * FROM vehicles WHERE id = CAST(:i AS uuid)"), {"i": vehicle_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Vehicle not found")
    if allowed is not None and (row["site_id"] is None or str(row["site_id"]) not in allowed):
        raise HTTPException(404, "Vehicle not found")
    return dict(row)


def _plate_taken(exc: IntegrityError) -> bool:
    return "uq_vehicles_tenant_plate" in str(exc.orig)


async def create(db: AsyncSession, body: Mapping[str, Any], *, tenant_id: str, user_id: str,
                 allowed: list[str] | None, plate_required: bool, ip: str | None = None) -> dict:
    values = clean(body, creating=True, plate_required=plate_required)
    await check_site(db, values.get("site_id"), allowed)
    await check_driver(db, values.get("assigned_driver_user_id"))
    custom = check_custom(await field_definitions(db), body.get("custom_fields") or {}, None)
    columns = ["tenant_id", *values, "custom_fields"]
    params = {**values, "tenant_id": tenant_id, "custom_fields": json.dumps(custom)}
    placeholders = [f"CAST(:{c} AS jsonb)" if c == "custom_fields" else f":{c}" for c in columns]
    try:
        async with db.begin_nested():
            row = (await db.execute(text(f"""
                INSERT INTO vehicles ({', '.join(columns)}) VALUES ({', '.join(placeholders)}) RETURNING *
            """), params)).mappings().one()
    except IntegrityError as exc:
        if _plate_taken(exc):
            raise HTTPException(409, "Another vehicle already has this registration number") from None
        raise
    await write_audit_log(db, tenant_id=tenant_id, user_id=user_id, action="fleet.vehicle.created",
                          resource_type="vehicle", resource_id=str(row["id"]), ip_address=ip,
                          detail={"plate_number": row["plate_number"], "name": row["name"]})
    return dict(row)


async def update(db: AsyncSession, vehicle_id: str, body: Mapping[str, Any], *, tenant_id: str, user_id: str,
                 allowed: list[str] | None, ip: str | None = None) -> dict:
    current = await visible_vehicle(db, vehicle_id, allowed)
    if current["archived_at"] is not None:
        raise HTTPException(409, "This vehicle is archived. Restore it before changing it.")
    values = clean(body, creating=False, plate_required=False)
    if "site_id" in values:
        await check_site(db, values["site_id"], allowed)
    if "assigned_driver_user_id" in values:
        await check_driver(db, values["assigned_driver_user_id"])
    if "custom_fields" in body:
        values["custom_fields"] = check_custom(await field_definitions(db), body["custom_fields"] or {},
                                               current["custom_fields"])
    changed = {k: v for k, v in values.items() if _differs(current.get(k), v)}
    if not changed:
        return current
    sets = [f"{k} = CAST(:{k} AS jsonb)" if k == "custom_fields" else f"{k} = :{k}" for k in changed]
    params = {k: json.dumps(v) if k == "custom_fields" else v for k, v in changed.items()}
    try:
        async with db.begin_nested():
            row = (await db.execute(text(f"""
                UPDATE vehicles SET {', '.join(sets)}, updated_at = now()
                 WHERE id = CAST(:vehicle_id AS uuid) RETURNING *
            """), {**params, "vehicle_id": vehicle_id})).mappings().one()
    except IntegrityError as exc:
        if _plate_taken(exc):
            raise HTTPException(409, "Another vehicle already has this registration number") from None
        raise
    await write_audit_log(db, tenant_id=tenant_id, user_id=user_id, action="fleet.vehicle.changed",
                          resource_type="vehicle", resource_id=vehicle_id, ip_address=ip,
                          detail={"changed": sorted(changed)})
    return dict(row)


def _differs(before: Any, after: Any) -> bool:
    if before is None or after is None:
        return before != after
    return str(before) != str(after) if not isinstance(after, dict) else before != after


async def archive(db: AsyncSession, vehicle_id: str, reason: str | None, *, tenant_id: str, user_id: str,
                  allowed: list[str] | None, ip: str | None = None) -> dict:
    current = await visible_vehicle(db, vehicle_id, allowed)
    if current["archived_at"] is not None:
        raise HTTPException(409, "This vehicle is already archived")
    reason = (reason or "").strip()
    if not 3 <= len(reason) <= 500:
        raise HTTPException(422, "Say why the vehicle is archived (3 to 500 characters)")
    # Its device is released, so the tracker can go to another vehicle; what it reported stays.
    await db.execute(text("SELECT fleet_device_release(CAST(:i AS uuid))"), {"i": vehicle_id})
    row = (await db.execute(text("""
        UPDATE vehicles
           SET archived_at = now(), archived_by_user_id = CAST(:u AS uuid), archive_reason = :reason,
               is_active = FALSE, updated_at = now(),
               tracking_enabled = FALSE, device_identifier = NULL, tracking_source = NULL,
               provider_connection_id = NULL, tracking_status = 'not_configured', tracking_status_at = now()
         WHERE id = CAST(:i AS uuid) RETURNING *
    """), {"i": vehicle_id, "u": user_id, "reason": reason})).mappings().one()
    await write_audit_log(db, tenant_id=tenant_id, user_id=user_id, action="fleet.vehicle.archived",
                          resource_type="vehicle", resource_id=vehicle_id, ip_address=ip,
                          detail={"plate_number": row["plate_number"], "reason": reason,
                                  "device_released": current["device_identifier"]})
    return dict(row)


async def restore(db: AsyncSession, vehicle_id: str, *, tenant_id: str, user_id: str,
                  allowed: list[str] | None, ip: str | None = None) -> dict:
    current = await visible_vehicle(db, vehicle_id, allowed)
    if current["archived_at"] is None:
        raise HTTPException(409, "This vehicle is not archived")
    try:
        async with db.begin_nested():
            row = (await db.execute(text("""
                UPDATE vehicles
                   SET archived_at = NULL, archived_by_user_id = NULL, archive_reason = NULL,
                       is_active = TRUE, updated_at = now()
                 WHERE id = CAST(:i AS uuid) RETURNING *
            """), {"i": vehicle_id})).mappings().one()
    except IntegrityError as exc:
        if _plate_taken(exc):
            raise HTTPException(409, "Another vehicle now has this registration number. "
                                     "Change one of them before restoring.") from None
        raise
    await write_audit_log(db, tenant_id=tenant_id, user_id=user_id, action="fleet.vehicle.restored",
                          resource_type="vehicle", resource_id=vehicle_id, ip_address=ip,
                          detail={"plate_number": row["plate_number"],
                                  "was_archived_for": current["archive_reason"]})
    return dict(row)
