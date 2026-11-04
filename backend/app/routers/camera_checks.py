"""A camera's checks and recording gaps, and the organisation's limits for them.

Phase 6 of closing the feature reference's known limits (4 November 2026; services/camera_checks.py,
services/recording_gaps.py).

  GET /api/v1/camera-checks/latest                 each camera the caller may see, its streams with their latest
                                                   check, and its recording gaps over the limit in 24 hours   camera:read
  GET /api/v1/camera-checks/cameras/{camera_id}    a camera's checks and gaps over 1 to 30 days              asset:read
  GET /api/v1/camera-checks/settings               the limits, and whether the caller may change them        asset:read
  PUT /api/v1/camera-checks/settings               change them                                               settings:write

The limits are changed by a signed-in person who may change the organisation's settings, not an API key and not a
support session, and the change is audited with what they were and what they became. A change judges the checks
made after it; a check is kept with the limits it broke when it was made.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.routers.operations_board import held_by
from app.services import camera_checks as checks
from app.services import intel_audit

router = APIRouter(prefix="/api/v1/camera-checks", tags=["device-health"])
_SEE = [Depends(require_permission("camera:read"))]
_READ = [Depends(require_permission("asset:read"))]
_SET = [Depends(require_permission("settings:write"))]
FIELDS = tuple(checks.DEFAULTS)
NOTE = ("A camera is listened to for 10 seconds every 15 minutes; between two checks nothing is seen. The answer "
        "time and the first picture are how long the camera takes to answer and to send a whole picture, not the "
        "delay between the scene and the screen. The cameras are read over TCP, which sends a lost packet again, so "
        "loss is seen as a stall or a missed frame. The quality of the picture is not judged.")


def _a_person(token: TokenPayload) -> None:
    if token.via_api_key:
        raise HTTPException(403, "This is done by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "This is done by the organisation's own staff, not from a support session.")


async def _camera(db: AsyncSession, camera_id: uuid.UUID, allowed: list[str] | None) -> dict:
    params: dict = {"c": str(camera_id)}
    scope = site_scope_clause(allowed, "c.site_id", params)
    row = (await db.execute(text(f"""
        SELECT c.id, c.name, c.site_id, s.name AS site_name FROM cameras c LEFT JOIN sites s ON s.id = c.site_id
         WHERE c.id = CAST(:c AS uuid) {('AND ' + scope) if scope else ''}
    """), params)).mappings().first()
    if row is None:
        raise HTTPException(404, "Camera not found")
    return dict(row)


@router.get("/latest", dependencies=_SEE)
async def read_latest(
    site_id: uuid.UUID | None = Query(None),
    camera_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Each camera the caller may see: its streams, each with its latest check or why it is not checked, and its
    recording gaps over the limit in the last 24 hours."""
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    if camera_id is not None:
        await _camera(db, camera_id, allowed)
    now = datetime.now(timezone.utc)
    return {"cameras": await checks.latest(db, now, allowed=allowed, site_id=site_id, camera_id=camera_id),
            "as_of": now, "note": NOTE, "stale_after_s": checks.STALE_AFTER_S, "every_s": checks.EVERY_S}


@router.get("/cameras/{camera_id:uuid}", dependencies=_READ)
async def read_camera(
    camera_id: uuid.UUID,
    days: int = Query(7, ge=1, le=30),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """A camera's checks over the last `days` days, oldest first, and its recording gaps, newest first, with the
    limits they are judged by now."""
    camera = await _camera(db, camera_id, allowed)
    now = datetime.now(timezone.utc)
    found = await checks.for_camera(db, camera_id, days, now)
    return {"camera": camera, "days": days, **found, "limits": await checks.limits(db), "as_of": now, "note": NOTE}


def _shown(config: dict, may_set: bool) -> dict:
    return {**{k: config[k] for k in FIELDS}, "updated_at": config["updated_at"],
            "updated_by_name": config["updated_by_name"], "defaults": checks.DEFAULTS,
            "ranges": {k: {"least": a, "most": b} for k, (a, b) in checks.RANGES.items()},
            "labels": checks.LIMIT_LABEL, "may_set": may_set}


@router.get("/settings", dependencies=_READ)
async def read_settings(db: AsyncSession = Depends(get_db_with_tenant),
                        token: TokenPayload = Depends(get_token_payload)):
    """The organisation's limits: the defaults until it changes them."""
    held = await held_by(db, token.role_id, ("settings:write",))
    may = "settings:write" in held and not token.via_api_key and not token.support_session_id
    return _shown(await checks.limits(db), may)


class LimitsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    least_rate_pct: int = Field(..., ge=10, le=100)
    most_stall_ms: int = Field(..., ge=200, le=60000)
    most_missed_pct: float = Field(..., ge=0, le=50)
    most_answer_ms: int = Field(..., ge=10, le=10000)
    most_first_picture_ms: int = Field(..., ge=500, le=60000)
    most_gap_s: int = Field(..., ge=10, le=3600)


@router.put("/settings", dependencies=_SET)
async def set_settings(body: LimitsBody, request: Request, db: AsyncSession = Depends(get_db_with_tenant),
                       token: TokenPayload = Depends(get_token_payload)):
    """Change the limits. The checks made from now on are judged by them; the next gap pass marks gaps by them."""
    _a_person(token)
    before = await checks.limits(db)
    values = {**body.model_dump(), "most_missed_pct": round(body.most_missed_pct, 1)}
    await db.execute(text(f"""
        INSERT INTO camera_check_settings (tenant_id, {', '.join(FIELDS)}, updated_by_user_id, updated_at)
        VALUES (current_setting('app.current_tenant')::uuid, {', '.join(':' + k for k in FIELDS)},
                CAST(:who AS uuid), now())
        ON CONFLICT (tenant_id) DO UPDATE
           SET {', '.join(f'{k} = EXCLUDED.{k}' for k in FIELDS)}, updated_by_user_id = EXCLUDED.updated_by_user_id,
               updated_at = now()
    """), {**values, "who": token.user_id})
    await intel_audit.record(db, request, token, "camera_check.settings", "camera_check_settings", None,
                             detail={"from": {k: before[k] for k in FIELDS}, "to": values})
    after = await checks.limits(db)
    await db.commit()
    return _shown(after, True)
