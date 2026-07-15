import asyncio
import json
import time

import cv2
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rtsp_validate import validate_rtsp_url
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/cameras", tags=["cameras"])

VALID_AI_MODULES = {
    "lpr", "face", "intrusion", "ppe", "crowd",
    "fire_smoke", "weapon", "behavior",
    "tampering", "abandoned", "fall",
}


class CameraCreate(BaseModel):
    name: str
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    ai_modules_enabled: list[str] = []
    site_id: str | None = None


class CameraUpdate(BaseModel):
    name: str | None = None
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    ai_modules_enabled: list[str] | None = None
    is_active: bool | None = None
    site_id: str | None = None


class StreamValidateRequest(BaseModel):
    url: str
    username: str | None = None
    password: str | None = None


def _build_rtsp_url_with_creds(url: str, username: str | None, password: str | None) -> str:
    """Embed credentials into an RTSP URL if not already present."""
    if not username or not password:
        return url
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        return url
    return f"{scheme}://{username}:{password}@{rest}"


def _probe_rtsp(full_url: str) -> dict:
    """Synchronous OpenCV probe — runs in a thread via asyncio.to_thread."""
    start = time.monotonic()
    # Timeout props must be passed at construction time — cv2.VideoCapture()
    # opens the connection synchronously inside the constructor, so calling
    # cap.set(CAP_PROP_OPEN_TIMEOUT_MSEC, ...) afterward is a no-op and lets
    # an unreachable host block for OpenCV/FFmpeg's own (much longer) default.
    cap = cv2.VideoCapture(
        full_url,
        cv2.CAP_FFMPEG,
        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000],
    )
    if not cap.isOpened():
        cap.release()
        return {"valid": False, "error": "Could not open stream (connection refused or timeout)"}
    ret, _ = cap.read()
    latency_ms = int((time.monotonic() - start) * 1000)
    if not ret:
        cap.release()
        return {"valid": False, "error": "Stream opened but could not read first frame"}
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return {
        "valid": True,
        "resolution_w": w or None,
        "resolution_h": h or None,
        "fps": round(fps, 2) if fps and fps > 0 else None,
        "latency_ms": latency_ms,
        "error": None,
    }


@router.post("/validate-stream", dependencies=[Depends(require_permission("camera:create"))])
async def validate_stream(body: StreamValidateRequest):
    """Test RTSP URL and optional credentials without saving anything."""
    ok, reason = validate_rtsp_url(body.url)
    if not ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, reason)
    full_url = _build_rtsp_url_with_creds(body.url, body.username, body.password)
    result = await asyncio.to_thread(_probe_rtsp, full_url)
    return result


@router.get("", dependencies=[Depends(require_permission("camera:read"))])
async def list_cameras(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {}
    scope = site_scope_clause(allowed_sites, "c.site_id", params)
    where = f"WHERE {scope} " if scope else ""
    result = await db.execute(text(
        "SELECT c.id, c.name, c.location, c.latitude, c.longitude, "
        "       c.ai_modules_enabled, c.is_active, c.site_id, "
        "       s.name AS site_name, c.created_at, c.updated_at "
        "FROM cameras c "
        "LEFT JOIN sites s ON s.id = c.site_id "
        f"{where}"
        "ORDER BY c.name"
    ), params)
    return [dict(r._mapping) for r in result]


@router.get("/{camera_id}", dependencies=[Depends(require_permission("camera:read"))])
async def get_camera(
    camera_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    result = await db.execute(
        text(
            "SELECT c.id, c.name, c.location, c.latitude, c.longitude, "
            "       c.ai_modules_enabled, c.is_active, c.site_id, "
            "       s.name AS site_name, c.created_at, c.updated_at "
            "FROM cameras c "
            "LEFT JOIN sites s ON s.id = c.site_id "
            "WHERE c.id = :id"
        ),
        {"id": camera_id},
    )
    row = result.mappings().first()
    if not row or not is_site_allowed(allowed_sites, row["site_id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    return dict(row)


@router.get("/{camera_id}/overlay", dependencies=[Depends(require_permission("camera:read"))])
async def camera_overlay(
    camera_id: str,
    seconds: int = 15,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Live-view annotation layer (Gap 92): the camera's active restricted
    zones (normalized 0-1 polygons — always precise) plus its recent
    detections (pixel bounding boxes + the source frame dimensions when the
    AI worker recorded them, so the client can scale them). Site-scoped."""
    cam = (await db.execute(
        text("SELECT site_id FROM cameras WHERE id = CAST(:cid AS uuid)"),
        {"cid": camera_id},
    )).first()
    if cam is None or not is_site_allowed(allowed_sites, cam.site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")

    zones_result = await db.execute(
        text("SELECT id, name, polygon, severity, applies_to_modules FROM restricted_zones "
             "WHERE camera_id = CAST(:cid AS uuid) AND is_active = TRUE"),
        {"cid": camera_id},
    )
    zones = [dict(r._mapping) for r in zones_result]

    seconds = max(1, min(seconds, 120))
    det_result = await db.execute(
        text("""
            SELECT id, module_type, confidence, bounding_box,
                   (raw_metadata->>'frame_width')::int  AS frame_width,
                   (raw_metadata->>'frame_height')::int AS frame_height,
                   detected_at
            FROM detections
            WHERE camera_id = CAST(:cid AS uuid)
              AND bounding_box IS NOT NULL
              AND detected_at > now() - make_interval(secs => :secs)
            ORDER BY detected_at DESC
            LIMIT 50
        """),
        {"cid": camera_id, "secs": seconds},
    )
    detections = []
    for r in det_result:
        d = dict(r._mapping)
        d["id"] = str(d["id"])
        detections.append(d)

    return {"camera_id": camera_id, "zones": zones, "detections": detections}


async def _check_module_licenses(db: AsyncSession, modules: list[str]) -> list[str]:
    """Return list of requested modules that are NOT licensed for this tenant.

    If the tenant has zero license rows (licensing not yet configured), all modules
    are allowed — this avoids blocking fresh tenants and test environments.
    """
    if not modules:
        return []
    count_row = await db.execute(
        text(
            "SELECT COUNT(*) FROM tenant_module_licenses "
            "WHERE tenant_id = current_setting('app.current_tenant')::uuid"
        )
    )
    if (count_row.scalar() or 0) == 0:
        return []
    result = await db.execute(
        text(
            "SELECT module_type FROM tenant_module_licenses "
            "WHERE tenant_id = current_setting('app.current_tenant')::uuid "
            "  AND module_type = ANY(:modules) AND is_enabled = TRUE "
            "  AND (expires_at IS NULL OR expires_at > now())"
        ),
        {"modules": modules},
    )
    licensed = {row[0] for row in result}
    return [m for m in modules if m not in licensed]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("camera:create"))])
async def create_camera(body: CameraCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    modules = [m for m in body.ai_modules_enabled if m in VALID_AI_MODULES]
    unlicensed = await _check_module_licenses(db, modules)
    if unlicensed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Modules not licensed for this tenant: {unlicensed}",
        )
    result = await db.execute(
        text(
            "INSERT INTO cameras (tenant_id, name, location, latitude, longitude, ai_modules_enabled, site_id) "
            "VALUES (current_setting('app.current_tenant')::uuid, :name, :location, :lat, :lng, CAST(:modules AS jsonb), :site_id) "
            "RETURNING id"
        ),
        {
            "name": body.name, "location": body.location,
            "lat": body.latitude, "lng": body.longitude,
            "modules": json.dumps(modules),
            "site_id": body.site_id,
        },
    )
    new_id = result.scalar_one()
    await db.commit()
    return {"id": new_id, "name": body.name}


@router.put("/{camera_id}", dependencies=[Depends(require_permission("camera:update"))])
async def update_camera(camera_id: str, body: CameraUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    sets, params = [], {"id": camera_id}
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.location is not None:
        sets.append("location = :location"); params["location"] = body.location
    if body.latitude is not None:
        sets.append("latitude = :latitude"); params["latitude"] = body.latitude
    if body.longitude is not None:
        sets.append("longitude = :longitude"); params["longitude"] = body.longitude
    if body.ai_modules_enabled is not None:
        modules = [m for m in body.ai_modules_enabled if m in VALID_AI_MODULES]
        unlicensed = await _check_module_licenses(db, modules)
        if unlicensed:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Modules not licensed for this tenant: {unlicensed}",
            )
        sets.append("ai_modules_enabled = CAST(:modules AS jsonb)"); params["modules"] = json.dumps(modules)
    if body.is_active is not None:
        sets.append("is_active = :is_active"); params["is_active"] = body.is_active
    if body.site_id is not None:
        sets.append("site_id = :site_id"); params["site_id"] = body.site_id
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    sets.append("updated_at = now()")
    # CTE: update + return updated row + join sites — all before commit so GUC stays valid
    result = await db.execute(
        text(
            f"WITH updated AS ("
            f"  UPDATE cameras SET {', '.join(sets)} WHERE id = :id"
            f"  RETURNING id, name, location, latitude, longitude, ai_modules_enabled, is_active, site_id, updated_at"
            f") "
            f"SELECT u.id, u.name, u.location, u.latitude, u.longitude,"
            f"       u.ai_modules_enabled, u.is_active, u.site_id,"
            f"       s.name AS site_name, u.updated_at"
            f"  FROM updated u LEFT JOIN sites s ON s.id = u.site_id"
        ),
        params,
    )
    await db.commit()
    row = result.mappings().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    return dict(row)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_permission("camera:delete"))])
async def deactivate_camera(camera_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text("UPDATE cameras SET is_active = FALSE, updated_at = now() WHERE id = :id"), {"id": camera_id})
    await db.commit()
