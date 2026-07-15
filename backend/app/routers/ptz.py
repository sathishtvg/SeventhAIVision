"""
Gap 31 — Full ONVIF PTZ Integration

Endpoints:
  POST /api/v1/cameras/discover
  GET  /api/v1/cameras/{camera_id}/ptz/presets
  POST /api/v1/cameras/{camera_id}/ptz/presets
  DEL  /api/v1/cameras/{camera_id}/ptz/presets/{preset_id}
  PUT  /api/v1/cameras/{camera_id}/ptz/move
  PUT  /api/v1/cameras/{camera_id}/ptz/goto-preset/{preset_id}
  PUT  /api/v1/cameras/{camera_id}/ptz/stop
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services import onvif_service

router = APIRouter(prefix="/api/v1/cameras", tags=["ptz"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class DiscoverRequest(BaseModel):
    timeout_sec: int = Field(default=5, ge=1, le=30)


class PresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    tilt: float = Field(default=0.0, ge=-1.0, le=1.0)
    zoom: float = Field(default=0.0, ge=0.0, le=1.0)
    onvif_token: str | None = None


class PTZMoveRequest(BaseModel):
    host: str
    port: int = Field(default=80)
    username: str
    password: str
    profile_token: str
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    tilt: float = Field(default=0.0, ge=-1.0, le=1.0)
    zoom: float = Field(default=0.0, ge=0.0, le=1.0)
    mode: str = Field(default="continuous", pattern="^(continuous|absolute)$")


class PTZStopRequest(BaseModel):
    host: str
    port: int = Field(default=80)
    username: str
    password: str
    profile_token: str


class GotoPresetRequest(BaseModel):
    host: str
    port: int = Field(default=80)
    username: str
    password: str
    profile_token: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/discover", dependencies=[Depends(require_permission("camera:create"))])
async def discover_cameras(
    body: DiscoverRequest,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """ONVIF WS-Discovery: multicast UDP scan for cameras on the local network."""
    found = await onvif_service.discover_onvif(timeout_sec=body.timeout_sec)
    for cam in found:
        await db.execute(
            text(
                "INSERT INTO onvif_discovery_cache "
                "  (tenant_id, xaddr, types, scopes, last_seen_at) "
                "VALUES "
                "  (current_setting('app.current_tenant')::uuid, :xaddr, :types, :scopes, now()) "
                "ON CONFLICT (tenant_id, xaddr) DO UPDATE "
                "  SET last_seen_at = now(), types = :types, scopes = :scopes"
            ),
            {
                "xaddr": cam["xaddr"],
                "types": cam.get("types", []),
                "scopes": cam.get("scopes", []),
            },
        )
    await db.commit()
    return {"discovered": found, "count": len(found)}


@router.get("/{camera_id}/ptz/presets",
            dependencies=[Depends(require_permission("camera:read"))])
async def list_ptz_presets(
    camera_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """List saved PTZ presets for a camera (from DB)."""
    await _assert_camera_exists(db, camera_id)
    result = await db.execute(
        text(
            "SELECT id, name, pan, tilt, zoom, onvif_token, created_at "
            "FROM ptz_presets WHERE camera_id = :camera_id "
            "ORDER BY name"
        ),
        {"camera_id": camera_id},
    )
    return [dict(r._mapping) for r in result]


@router.post("/{camera_id}/ptz/presets", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("camera:update"))])
async def create_ptz_preset(
    camera_id: str,
    body: PresetCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Save a named PTZ preset position for a camera."""
    await _assert_camera_exists(db, camera_id)
    existing = await db.execute(
        text(
            "SELECT id FROM ptz_presets "
            "WHERE tenant_id = current_setting('app.current_tenant')::uuid "
            "  AND camera_id = :camera_id AND name = :name"
        ),
        {"camera_id": camera_id, "name": body.name},
    )
    if existing.first():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Preset '{body.name}' already exists for this camera",
        )
    result = await db.execute(
        text(
            "INSERT INTO ptz_presets "
            "  (tenant_id, camera_id, name, pan, tilt, zoom, onvif_token) "
            "VALUES "
            "  (current_setting('app.current_tenant')::uuid, "
            "   :camera_id, :name, :pan, :tilt, :zoom, :token) "
            "RETURNING id"
        ),
        {
            "camera_id": camera_id, "name": body.name,
            "pan": body.pan, "tilt": body.tilt, "zoom": body.zoom,
            "token": body.onvif_token,
        },
    )
    preset_id = result.scalar_one()
    await db.commit()
    return {
        "id": preset_id, "name": body.name,
        "pan": body.pan, "tilt": body.tilt, "zoom": body.zoom,
    }


@router.delete("/{camera_id}/ptz/presets/{preset_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_permission("camera:update"))])
async def delete_ptz_preset(
    camera_id: str,
    preset_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Delete a saved PTZ preset."""
    await _assert_camera_exists(db, camera_id)
    result = await db.execute(
        text(
            "DELETE FROM ptz_presets "
            "WHERE id = :preset_id AND camera_id = :camera_id "
            "  AND tenant_id = current_setting('app.current_tenant')::uuid"
        ),
        {"preset_id": preset_id, "camera_id": camera_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preset not found")
    await db.commit()


@router.put("/{camera_id}/ptz/move",
            dependencies=[Depends(require_permission("camera:update"))])
async def ptz_move(
    camera_id: str,
    body: PTZMoveRequest,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Send a PTZ move command (continuous or absolute) to the camera via ONVIF."""
    await _assert_camera_exists(db, camera_id)
    try:
        if body.mode == "absolute":
            await onvif_service.ptz_absolute_move(
                body.host, body.port, body.username, body.password,
                body.profile_token, body.pan, body.tilt, body.zoom,
            )
        else:
            await onvif_service.ptz_continuous_move(
                body.host, body.port, body.username, body.password,
                body.profile_token, body.pan, body.tilt, body.zoom,
            )
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ONVIF PTZ error: {exc}") from exc
    return {"status": "ok", "mode": body.mode, "pan": body.pan, "tilt": body.tilt, "zoom": body.zoom}


@router.put("/{camera_id}/ptz/goto-preset/{preset_id}",
            dependencies=[Depends(require_permission("camera:update"))])
async def ptz_goto_preset(
    camera_id: str,
    preset_id: str,
    body: GotoPresetRequest,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Move camera to a saved DB preset (uses onvif_token if set, else AbsoluteMove)."""
    await _assert_camera_exists(db, camera_id)
    row = await db.execute(
        text(
            "SELECT onvif_token, pan, tilt, zoom FROM ptz_presets "
            "WHERE id = :preset_id AND camera_id = :camera_id "
            "  AND tenant_id = current_setting('app.current_tenant')::uuid"
        ),
        {"preset_id": preset_id, "camera_id": camera_id},
    )
    preset = row.mappings().first()
    if not preset:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preset not found")
    try:
        if preset["onvif_token"]:
            await onvif_service.ptz_goto_preset(
                body.host, body.port, body.username, body.password,
                body.profile_token, preset["onvif_token"],
            )
        else:
            await onvif_service.ptz_absolute_move(
                body.host, body.port, body.username, body.password,
                body.profile_token,
                float(preset["pan"]), float(preset["tilt"]), float(preset["zoom"]),
            )
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ONVIF PTZ error: {exc}") from exc
    return {"status": "ok", "preset_id": preset_id}


@router.put("/{camera_id}/ptz/stop",
            dependencies=[Depends(require_permission("camera:update"))])
async def ptz_stop(
    camera_id: str,
    body: PTZStopRequest,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Stop all PTZ movement on the camera."""
    await _assert_camera_exists(db, camera_id)
    try:
        await onvif_service.ptz_stop(
            body.host, body.port, body.username, body.password, body.profile_token,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ONVIF PTZ error: {exc}") from exc
    return {"status": "stopped"}


# ── Helper ────────────────────────────────────────────────────────────────────

async def _assert_camera_exists(db: AsyncSession, camera_id: str) -> None:
    row = await db.execute(
        text("SELECT id FROM cameras WHERE id = :id"), {"id": camera_id}
    )
    if not row.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
