"""The security map: what is where, in layers, and the places of a site.

Phase 3 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
GIS_SECURITY_ARCHITECTURE.md.

  GET  /layers     the layers there are, and which this caller may see
  GET  /features   what is on each layer, now
  GET  /around     what is near one incident, alert or situation
  ...              the places of a site: read, draw, change, retire, restore

NOTHING HERE CHANGES AN INCIDENT, AN ALERT, A CAMERA OR A GUARD'S SHIFT. The
map reads what other screens already show, each layer under that screen's own
permission and the caller's sites (app/services/security_map.py). The only
thing written here is a site's places, by someone holding `sitemap:manage`, and
each change is audited.

A GUARD IS SHOWN WHERE THEY LAST RECORDED BEING, WITH HOW LONG AGO. The
platform has no live position of anybody (app/services/guard_positions.py).

`/around` lists what is near an incident for a person to read. Nobody is sent
anywhere by it.
"""
from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed
from app.dependencies.tenant import get_db_with_tenant
from app.services import guard_positions, intel_audit
from app.services import security_map as maps

#: The most points an outline is drawn with.
MAX_OUTLINE = 200

router = APIRouter(prefix="/api/v1/site-map", tags=["site-map"],
                   dependencies=[Depends(require_permission("sitemap:read"))])
_MANAGE = [Depends(require_permission("sitemap:manage"))]


def _a_person(token: TokenPayload) -> None:
    """A change to the map names who made it."""
    if token.via_api_key:
        raise HTTPException(403, "A site's places are drawn by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "A site's places are drawn by the organisation's own staff, "
                                 "not from a support session.")


# ─── The map ─────────────────────────────────────────────────────────────────

@router.get("/layers")
async def list_layers(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """The layers of the map, and which of them this caller may see."""
    mine = await maps.held_permissions(db, token.role_id)
    return {
        "layers": [{"key": layer.key, "label": layer.label, "permission": layer.permission,
                    "may_see": layer.permission in mine, "live": layer.live} for layer in maps.LAYERS],
        "place_kinds": list(maps.PLACE_KINDS), "can_manage": "sitemap:manage" in mine,
        "default_hours": maps.DEFAULT_HOURS, "max_hours": maps.MAX_HOURS,
        "default_radius_m": maps.DEFAULT_RADIUS_M, "max_radius_m": maps.MAX_RADIUS_M,
        "stale_after_s": guard_positions.STALE_AFTER_S, "note": maps.NOTE,
    }


@router.get("/features")
async def list_features(
    site_id: uuid.UUID | None = Query(None),
    layers: list[str] = Query(default=[]),
    hours: int = Query(maps.DEFAULT_HOURS, ge=1, le=maps.MAX_HOURS),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What is on each layer, now. `hours` is how far back the live layers —
    incidents, alerts, situations — look. `without_position` counts what could
    not be drawn; `not_shown` names each layer left out, and why."""
    unknown = [key for key in layers if key not in maps.BY_KEY]
    if unknown:
        raise HTTPException(422, f"Unknown layer '{unknown[0]}'. One of: {', '.join(maps.BY_KEY)}.")
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    mine = await maps.held_permissions(db, token.role_id)
    return await maps.features(db, mine, allowed, site_id=site_id, layers=layers, hours=hours)


@router.get("/around")
async def what_is_near(
    kind: Literal["INCIDENT", "ALERT", "SITUATION"] = Query(...),
    id: uuid.UUID = Query(...),
    radius_m: int = Query(maps.DEFAULT_RADIUS_M, ge=10, le=maps.MAX_RADIUS_M),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What is near one incident, alert or situation: cameras, drones, patrol
    checkpoints, places and guards within the radius, nearest first — and every
    guard on shift at its site, nearest first by last recorded position, those
    free before those already sent somewhere.

    A reading for a person. Nobody is dispatched by it."""
    mine = await maps.held_permissions(db, token.role_id)
    answer = await maps.around(db, kind, id, mine, allowed, radius_m=radius_m)
    if answer is None:
        raise HTTPException(404, f"{kind.capitalize()} not found")
    return answer


# ─── The places of a site ────────────────────────────────────────────────────

@router.get("/places")
async def list_places(
    site_id: uuid.UUID | None = Query(None),
    include_retired: bool = Query(False),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The places drawn for a site — or for every site the caller may see."""
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    mine = await maps.held_permissions(db, token.role_id)
    found = await maps._places(db, allowed, site_id, None, door_states="access:read" in mine,
                               with_retired=include_retired and "sitemap:manage" in mine)
    return {"items": found, "kinds": list(maps.PLACE_KINDS), "can_manage": "sitemap:manage" in mine}


class PlaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: uuid.UUID
    kind: str
    name: str = Field(..., min_length=1, max_length=120)
    parent_id: uuid.UUID | None = None
    level: int | None = Field(None, ge=-20, le=200)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    #: An outline: [[lat, lng], ...], three points or more.
    polygon: list[list[float]] | None = Field(None, max_length=MAX_OUTLINE)
    door_id: uuid.UUID | None = None
    description: str | None = Field(None, max_length=2000)


class PlaceChange(BaseModel):
    """Only what is given is changed. A place's site and kind are what it is
    and are not changed: retire it and draw another."""
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=120)
    parent_id: uuid.UUID | None = None
    level: int | None = Field(None, ge=-20, le=200)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    polygon: list[list[float]] | None = Field(None, max_length=MAX_OUTLINE)
    door_id: uuid.UUID | None = None
    description: str | None = Field(None, max_length=2000)


async def _checked(db: AsyncSession, *, site_id, kind: str, parent_id, latitude, longitude, polygon, door_id,
                   self_id=None) -> list | None:
    """What a place has to be before it is saved. Returns its outline as it
    will be stored."""
    if (latitude is None) != (longitude is None):
        raise HTTPException(422, "A position needs both a latitude and a longitude.")
    shape = None
    if polygon is not None:
        shape = maps.outline(polygon)
        if shape is None:
            raise HTTPException(422, "An outline is three or more [latitude, longitude] points.")
    if parent_id is not None:
        if self_id is not None and str(parent_id) == str(self_id):
            raise HTTPException(422, "A place cannot be a part of itself.")
        parent = (await db.execute(text(
            "SELECT site_id, kind, is_active FROM site_places WHERE id = CAST(:p AS uuid)"),
            {"p": str(parent_id)})).first()
        if parent is None or str(parent.site_id) != str(site_id) or not parent.is_active:
            raise HTTPException(422, "The place it is a part of has to be an active place of the same site.")
        if parent.kind != "BUILDING":
            raise HTTPException(422, "A place can be a part of a building, and of nothing else.")
    if door_id is not None:
        if kind not in ("ACCESS_POINT", "GATE"):
            raise HTTPException(422, "Only an access point or a gate names a door.")
        door = (await db.execute(text("SELECT site_id FROM access_doors WHERE id = CAST(:d AS uuid)"),
                                 {"d": str(door_id)})).first()
        if door is None or str(door.site_id) != str(site_id):
            raise HTTPException(422, "That door is not a door of this site.")
    if latitude is None and shape is None and not (kind == "FLOOR" and parent_id is not None):
        raise HTTPException(422, "A place needs a position or an outline. A floor may have neither, and then "
                                 "says which building it is a level of.")
    return shape


async def _one(db: AsyncSession, place_id, allowed) -> dict:
    row = (await db.execute(text(f"{maps._PLACE} WHERE p.id = CAST(:id AS uuid)"),
                            {"id": str(place_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Place not found")
    return dict(row)


def _taken(exc: IntegrityError) -> HTTPException:
    said = str(exc.orig)
    if "uq_site_places_door" in said:
        return HTTPException(409, "That door is already at another place of this site.")
    if "uq_site_places_name" in said:
        return HTTPException(409, "This site already has an active place of that kind with that name.")
    return HTTPException(422, "That place could not be saved as it is.")


@router.post("/places", status_code=201, dependencies=_MANAGE)
async def draw_place(
    body: PlaceBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Draw a place at a site: a point, an outline, or — for a floor — a level
    of a building."""
    _a_person(token)
    if body.kind not in maps.PLACE_KINDS:
        raise HTTPException(422, f"Unknown kind of place '{body.kind}'. One of: {', '.join(maps.PLACE_KINDS)}.")
    known = (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"),
                              {"s": str(body.site_id)})).scalar()
    if not known or not is_site_allowed(allowed, str(body.site_id)):
        raise HTTPException(404, "Site not found")
    if not body.name.strip():
        raise HTTPException(422, "A place has a name.")
    shape = await _checked(db, site_id=body.site_id, kind=body.kind, parent_id=body.parent_id,
                           latitude=body.latitude, longitude=body.longitude, polygon=body.polygon,
                           door_id=body.door_id)
    try:
        new_id = (await db.execute(text("""
            INSERT INTO site_places
                   (tenant_id, site_id, kind, name, parent_id, level, latitude, longitude, polygon, door_id,
                    description, created_by_user_id, updated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:site AS uuid), :kind, :name,
                    CAST(:parent AS uuid), :level, :lat, :lng, CAST(:polygon AS jsonb), CAST(:door AS uuid),
                    :description, CAST(:who AS uuid), CAST(:who AS uuid))
            RETURNING id
        """), {"site": str(body.site_id), "kind": body.kind, "name": body.name.strip(),
               "parent": str(body.parent_id) if body.parent_id else None, "level": body.level,
               "lat": body.latitude, "lng": body.longitude, "polygon": json.dumps(shape) if shape else None,
               "door": str(body.door_id) if body.door_id else None,
               "description": body.description.strip() if body.description and body.description.strip() else None,
               "who": token.user_id})).scalar()
    except IntegrityError as exc:
        raise _taken(exc) from exc
    await intel_audit.record(db, request, token, "sitemap.place.create", "site_place", new_id,
                             site_id=body.site_id, detail={"kind": body.kind, "name": body.name.strip()})
    answer = maps.place(await _one(db, new_id, allowed))
    await db.commit()
    return answer


@router.patch("/places/{place_id:uuid}", dependencies=_MANAGE)
async def change_place(
    place_id: uuid.UUID,
    body: PlaceChange,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Change a place. Only what is given is changed; giving `null` for a
    position, an outline, a door or what it is a part of removes that."""
    _a_person(token)
    current = await _one(db, place_id, allowed)
    if not current["is_active"]:
        raise HTTPException(409, "This place is retired. Restore it to change it.")
    given = body.model_fields_set
    if not given:
        raise HTTPException(422, "Nothing was given to change.")
    if "name" in given and (body.name is None or not body.name.strip()):
        raise HTTPException(422, "A place has a name.")
    merged = {
        "parent_id": body.parent_id if "parent_id" in given else current["parent_id"],
        "latitude": body.latitude if "latitude" in given else current["latitude"],
        "longitude": body.longitude if "longitude" in given else current["longitude"],
        "polygon": body.polygon if "polygon" in given else maps.outline(current["polygon"]),
        "door_id": body.door_id if "door_id" in given else current["door_id"],
    }
    shape = await _checked(db, site_id=current["site_id"], kind=current["kind"], self_id=place_id, **merged)
    try:
        await db.execute(text("""
            UPDATE site_places
               SET name = :name, parent_id = CAST(:parent AS uuid), level = :level, latitude = :lat,
                   longitude = :lng, polygon = CAST(:polygon AS jsonb), door_id = CAST(:door AS uuid),
                   description = :description, updated_by_user_id = CAST(:who AS uuid), updated_at = now()
             WHERE id = CAST(:id AS uuid)
        """), {"name": body.name.strip() if "name" in given else current["name"],
               "parent": str(merged["parent_id"]) if merged["parent_id"] else None,
               "level": body.level if "level" in given else current["level"],
               "lat": merged["latitude"], "lng": merged["longitude"],
               "polygon": json.dumps(shape) if shape else None,
               "door": str(merged["door_id"]) if merged["door_id"] else None,
               "description": ((body.description or "").strip() or None) if "description" in given
               else current["description"],
               "who": token.user_id, "id": str(place_id)})
    except IntegrityError as exc:
        raise _taken(exc) from exc
    await intel_audit.record(db, request, token, "sitemap.place.update", "site_place", place_id,
                             site_id=current["site_id"], detail={"kind": current["kind"], "name": current["name"],
                                                                 "changed": sorted(given)})
    answer = maps.place(await _one(db, place_id, allowed))
    await db.commit()
    return answer


async def _set_active(db, request, token, place_id, allowed, active: bool) -> dict:
    _a_person(token)
    current = await _one(db, place_id, allowed)
    if current["is_active"] == active:
        raise HTTPException(409, "This place is already active." if active else "This place is already retired.")
    try:
        await db.execute(text("""
            UPDATE site_places SET is_active = :active, updated_by_user_id = CAST(:who AS uuid), updated_at = now()
             WHERE id = CAST(:id AS uuid)
        """), {"active": active, "who": token.user_id, "id": str(place_id)})
    except IntegrityError as exc:
        raise _taken(exc) from exc
    await intel_audit.record(db, request, token, "sitemap.place.restore" if active else "sitemap.place.retire",
                             "site_place", place_id, site_id=current["site_id"],
                             detail={"kind": current["kind"], "name": current["name"]})
    await db.commit()
    return {"is_active": active}


@router.post("/places/{place_id:uuid}/retire", dependencies=_MANAGE)
async def retire_place(
    place_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Take a place off the map. It is kept, marked retired, and can be restored."""
    return await _set_active(db, request, token, place_id, allowed, False)


@router.post("/places/{place_id:uuid}/restore", dependencies=_MANAGE)
async def restore_place(
    place_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Put a retired place back on the map."""
    return await _set_active(db, request, token, place_id, allowed, True)
