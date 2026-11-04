"""Privacy areas for drones: drawn on the map, and taken away - never edited.

Phase 5 of closing the feature reference's known limits (4 November 2026; services/drone_privacy.py applies them).

  GET  /api/v1/drone-privacy-areas              the areas in force, for the map    drone:read
  POST /api/v1/drone-privacy-areas              draw one                           privacy:manage
  POST /api/v1/drone-privacy-areas/{id}/remove  take one away (kept, with who)     privacy:manage

Drawing is a signed-in person's act - not an API key, not a support session - by whoever may draw a fixed camera's
privacy zones, and each is a line in the audit log. A drone's camera asks every couple of seconds whether it is at
one, so a change takes effect within about that long.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed
from app.dependencies.tenant import get_db_with_tenant
from app.services import drone_geometry, drone_privacy
from app.services.drone_access import audit

router = APIRouter(prefix="/api/v1/drone-privacy-areas", tags=["drones"])
_READ = [Depends(require_permission("drone:read"))]
_DRAW = [Depends(require_permission("privacy:manage"))]
#: The most areas a site has.
MOST_AREAS = 50


def _a_person(token: TokenPayload) -> None:
    if token.via_api_key:
        raise HTTPException(403, "A privacy area is drawn by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "A privacy area is drawn by the organisation's own staff, not from a support session.")


_AREA = """
    SELECT a.id, a.site_id, s.name AS site_name, a.name, a.shape, a.polygon, a.center_latitude, a.center_longitude,
           a.radius_m, a.margin_m, a.created_at, u.full_name AS created_by_name
      FROM drone_privacy_areas a JOIN sites s ON s.id = a.site_id LEFT JOIN users u ON u.id = a.created_by_user_id
"""


@router.get("", dependencies=_READ)
async def list_areas(db: AsyncSession = Depends(get_db_with_tenant),
                     allowed: list[str] | None = Depends(get_allowed_site_ids)):
    rows = (await db.execute(text(f"{_AREA} WHERE a.removed_at IS NULL ORDER BY s.name, a.created_at"))).mappings()
    return {"areas": [dict(r) for r in rows if is_site_allowed(allowed, r["site_id"])],
            "margin_default_m": drone_privacy.MARGIN_DEFAULT_M, "margin_most_m": drone_privacy.MARGIN_MOST_M,
            "applies_within_seconds": drone_privacy.REFRESH_SECONDS,
            "note": "While a drone is in an area or within its margin - or its position is not known - its whole "
                    "picture is withheld: its live view, what the AI is given and its recordings. A snapshot taken "
                    "there is stored blank, and a clip recorded there is not kept."}


class AreaBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=120)
    shape: Literal["POLYGON", "RECTANGLE", "CIRCLE"]
    polygon: list | None = Field(None, max_length=200)
    center_latitude: float | None = None
    center_longitude: float | None = None
    radius_m: float | None = None
    margin_m: float = Field(drone_privacy.MARGIN_DEFAULT_M, ge=0, le=drone_privacy.MARGIN_MOST_M)


@router.post("", status_code=201, dependencies=_DRAW)
async def draw_area(body: AreaBody, request: Request, db: AsyncSession = Depends(get_db_with_tenant),
                    token: TokenPayload = Depends(get_token_payload),
                    allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Draw a privacy area. It applies to every drone of the organisation within a couple of seconds."""
    _a_person(token)
    if not is_site_allowed(allowed, body.site_id) or not (await db.execute(
            text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"), {"s": str(body.site_id)})).scalar():
        raise HTTPException(404, "Site not found")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Give the area a name.")
    try:
        shape = drone_geometry.normalize_zone(body.shape, body.polygon, body.center_latitude, body.center_longitude,
                                              body.radius_m)
    except drone_geometry.GeometryError as exc:
        raise HTTPException(422, str(exc)) from exc
    drawn = (await db.execute(text(
        "SELECT count(*) FROM drone_privacy_areas WHERE site_id = CAST(:s AS uuid) AND removed_at IS NULL"),
        {"s": str(body.site_id)})).scalar()
    if drawn >= MOST_AREAS:
        raise HTTPException(409, f"A site has at most {MOST_AREAS} privacy areas. Take one away first.")
    import json

    new_id = (await db.execute(text("""
        INSERT INTO drone_privacy_areas (tenant_id, site_id, name, shape, polygon, center_latitude, center_longitude,
                                         radius_m, margin_m, created_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:s AS uuid), :name, :shape, CAST(:poly AS jsonb),
                :lat, :lng, :r, :margin, CAST(:who AS uuid))
        RETURNING id
    """), {"s": str(body.site_id), "name": name, "shape": body.shape,
           "poly": json.dumps(shape["polygon"]) if shape["polygon"] is not None else None,
           "lat": shape["center_latitude"], "lng": shape["center_longitude"], "r": shape["radius_m"],
           "margin": body.margin_m, "who": token.user_id})).scalar()
    await audit(db, request, token, "drone.privacy_area.draw", "drone_privacy_area", new_id,
                {"name": name, "shape": body.shape, "margin_m": body.margin_m, "site_id": str(body.site_id)})
    row = (await db.execute(text(f"{_AREA} WHERE a.id = CAST(:id AS uuid)"), {"id": str(new_id)})).mappings().one()
    await db.commit()
    return {**dict(row), "applies_within_seconds": drone_privacy.REFRESH_SECONDS}


@router.post("/{area_id}/remove", dependencies=_DRAW)
async def remove_area(area_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db_with_tenant),
                      token: TokenPayload = Depends(get_token_payload),
                      allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Take an area away. It is kept, with who took it away and when."""
    _a_person(token)
    row = (await db.execute(text(
        "SELECT id, site_id, name FROM drone_privacy_areas WHERE id = CAST(:id AS uuid) AND removed_at IS NULL"),
        {"id": str(area_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Privacy area not found")
    await db.execute(text("""
        UPDATE drone_privacy_areas SET removed_at = now(), removed_by_user_id = CAST(:who AS uuid)
         WHERE id = CAST(:id AS uuid)
    """), {"who": token.user_id, "id": str(area_id)})
    await audit(db, request, token, "drone.privacy_area.remove", "drone_privacy_area", area_id,
                {"name": row["name"], "site_id": str(row["site_id"])})
    await db.commit()
    return {"removed": True}
