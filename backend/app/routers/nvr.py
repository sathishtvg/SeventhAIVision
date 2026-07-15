"""NVR adapter router — Hikvision ISAPI, Dahua HTTP, connections CRUD, model library."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services import nvr_service

router = APIRouter(prefix="/api/v1/nvr", tags=["nvr"])

# ── Pydantic schemas ──────────────────────────────────────────────────────────

class HikvisionProbeRequest(BaseModel):
    host: str
    port: int = 80
    username: str
    password: str
    timeout_sec: float = Field(default=10.0, ge=1.0, le=30.0)


class DahuaProbeRequest(BaseModel):
    host: str
    port: int = 80
    username: str
    password: str
    timeout_sec: float = Field(default=10.0, ge=1.0, le=30.0)


class NVRConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=80, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1)
    adapter_type: Literal["hikvision", "dahua", "generic"]


class CameraModelCreate(BaseModel):
    make: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=255)
    model_series: str | None = None
    device_type: Literal["ipc", "nvr", "ptz_dome", "ptz_speed", "fisheye"]
    default_http_port: int = 80
    default_rtsp_port: int = 554
    rtsp_path_template: str | None = None
    protocols: list[str] | None = None
    ptz_supported: bool = False
    max_resolution_mp: float | None = None
    notes: str | None = None


# ── Helper ────────────────────────────────────────────────────────────────────

def _wrap_probe_error(exc: Exception) -> HTTPException:
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in (401, 403):
            return HTTPException(502, detail=f"NVR returned {exc.response.status_code}: authentication failed")
        return HTTPException(502, detail=f"NVR returned HTTP {exc.response.status_code}")
    if isinstance(exc, httpx.TimeoutException):
        return HTTPException(502, detail="NVR connection timed out")
    return HTTPException(502, detail=f"NVR connection error: {exc}")


# ── One-shot probe endpoints ──────────────────────────────────────────────────

@router.post("/hikvision/probe", dependencies=[Depends(require_permission("camera:create"))])
async def probe_hikvision(body: HikvisionProbeRequest) -> dict[str, Any]:
    try:
        device_info = await nvr_service.hikvision_get_device_info(
            body.host, body.port, body.username, body.password, body.timeout_sec)
        channels = await nvr_service.hikvision_list_channels(
            body.host, body.port, body.username, body.password, body.timeout_sec)
    except Exception as exc:
        raise _wrap_probe_error(exc) from exc
    return {"adapter": "hikvision", "device_info": device_info, "channels": channels}


@router.post("/dahua/probe", dependencies=[Depends(require_permission("camera:create"))])
async def probe_dahua(body: DahuaProbeRequest) -> dict[str, Any]:
    try:
        device_info = await nvr_service.dahua_get_device_info(
            body.host, body.port, body.username, body.password, body.timeout_sec)
        channels = await nvr_service.dahua_list_channels(
            body.host, body.port, body.username, body.password, body.timeout_sec)
    except Exception as exc:
        raise _wrap_probe_error(exc) from exc
    return {"adapter": "dahua", "device_info": device_info, "channels": channels}


# ── NVR Connections CRUD ──────────────────────────────────────────────────────

@router.get("/connections", dependencies=[Depends(require_permission("camera:read"))])
async def list_connections(db: AsyncSession = Depends(get_db_with_tenant)) -> list[dict]:
    result = await db.execute(
        text("SELECT id, tenant_id, name, host, port, username, adapter_type, "
             "is_active, last_probe_at, last_probe_status, created_at, updated_at "
             "FROM nvr_connections ORDER BY created_at DESC"))
    rows = result.mappings().all()
    return [dict(r) for r in rows]


@router.post("/connections", status_code=201,
             dependencies=[Depends(require_permission("camera:create"))])
async def create_connection(
    body: NVRConnectionCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
) -> dict:
    result = await db.execute(
        text("""
            INSERT INTO nvr_connections
                (tenant_id, name, host, port, username, password_enc, adapter_type)
            VALUES (current_setting('app.current_tenant')::uuid,
                    :name, :host, :port, :username, :password_enc, :adapter_type)
            RETURNING id, tenant_id, name, host, port, username, adapter_type,
                      is_active, last_probe_at, last_probe_status, created_at, updated_at
        """),
        {"name": body.name, "host": body.host, "port": body.port,
         "username": body.username, "password_enc": encrypt_secret(body.password),
         "adapter_type": body.adapter_type},
    )
    await db.commit()
    return dict(result.mappings().one())


@router.get("/connections/{connection_id}", dependencies=[Depends(require_permission("camera:read"))])
async def get_connection(connection_id: uuid.UUID,
                         db: AsyncSession = Depends(get_db_with_tenant)) -> dict:
    result = await db.execute(
        text("SELECT id, tenant_id, name, host, port, username, adapter_type, "
             "is_active, last_probe_at, last_probe_status, created_at, updated_at "
             "FROM nvr_connections WHERE id = :id"),
        {"id": str(connection_id)})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, detail="NVR connection not found")
    return dict(row)


@router.delete("/connections/{connection_id}", status_code=204,
               dependencies=[Depends(require_permission("camera:create"))])
async def delete_connection(connection_id: uuid.UUID,
                            db: AsyncSession = Depends(get_db_with_tenant)) -> None:
    result = await db.execute(
        text("DELETE FROM nvr_connections WHERE id = :id RETURNING id"),
        {"id": str(connection_id)})
    if not result.first():
        raise HTTPException(404, detail="NVR connection not found")
    await db.commit()


@router.post("/connections/{connection_id}/probe",
             dependencies=[Depends(require_permission("camera:read"))])
async def probe_saved_connection(
    connection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
) -> dict[str, Any]:
    result = await db.execute(
        text("SELECT host, port, username, password_enc, adapter_type "
             "FROM nvr_connections WHERE id = :id"),
        {"id": str(connection_id)})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, detail="NVR connection not found")

    host, port = row["host"], row["port"]
    username = row["username"]
    password = decrypt_secret(row["password_enc"])
    adapter = row["adapter_type"]

    probe_result: dict[str, Any] = {"adapter": adapter}
    status = "ok"
    try:
        if adapter == "hikvision":
            probe_result["device_info"] = await nvr_service.hikvision_get_device_info(
                host, port, username, password)
            probe_result["channels"] = await nvr_service.hikvision_list_channels(
                host, port, username, password)
        elif adapter == "dahua":
            probe_result["device_info"] = await nvr_service.dahua_get_device_info(
                host, port, username, password)
            probe_result["channels"] = await nvr_service.dahua_list_channels(
                host, port, username, password)
        else:
            probe_result["note"] = "generic adapter — no structured probe available"
    except Exception as exc:
        status = "error"
        probe_result["error"] = str(exc)

    now = datetime.now(timezone.utc)
    await db.execute(
        text("UPDATE nvr_connections SET last_probe_at = :at, last_probe_status = :s "
             "WHERE id = :id"),
        {"at": now, "s": status, "id": str(connection_id)})
    await db.commit()

    probe_result["probe_status"] = status
    probe_result["probed_at"] = now.isoformat()
    return probe_result


# ── Camera Model Library ──────────────────────────────────────────────────────

@router.get("/models", dependencies=[Depends(require_permission("camera:read"))])
async def list_models(
    make: str | None = Query(default=None),
    device_type: str | None = Query(default=None),
    ptz_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_with_tenant),
) -> list[dict]:
    conditions = []
    params: dict[str, Any] = {}
    if make:
        conditions.append("make ILIKE :make")
        params["make"] = f"%{make}%"
    if device_type:
        conditions.append("device_type = :device_type")
        params["device_type"] = device_type
    if ptz_only:
        conditions.append("ptz_supported = TRUE")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    result = await db.execute(
        text(f"SELECT id, tenant_id, make, model_name, model_series, device_type, "
             f"default_http_port, default_rtsp_port, rtsp_path_template, protocols, "
             f"ptz_supported, max_resolution_mp, notes, is_builtin, created_at "
             f"FROM camera_model_library {where} ORDER BY make, model_name"),
        params)
    rows = result.mappings().all()
    return [dict(r) for r in rows]


@router.post("/models", status_code=201,
             dependencies=[Depends(require_permission("camera:create"))])
async def create_model(
    body: CameraModelCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
) -> dict:
    result = await db.execute(
        text("""
            INSERT INTO camera_model_library
                (tenant_id, make, model_name, model_series, device_type, default_http_port,
                 default_rtsp_port, rtsp_path_template, protocols, ptz_supported,
                 max_resolution_mp, notes, is_builtin)
            VALUES (current_setting('app.current_tenant')::uuid,
                    :make, :model_name, :model_series, :device_type, :http_port,
                    :rtsp_port, :rtsp_tmpl, :protocols, :ptz, :mp, :notes, false)
            RETURNING id, tenant_id, make, model_name, model_series, device_type,
                      default_http_port, default_rtsp_port, rtsp_path_template, protocols,
                      ptz_supported, max_resolution_mp, notes, is_builtin, created_at
        """),
        {
            "make": body.make,
            "model_name": body.model_name,
            "model_series": body.model_series,
            "device_type": body.device_type,
            "http_port": body.default_http_port,
            "rtsp_port": body.default_rtsp_port,
            "rtsp_tmpl": body.rtsp_path_template,
            "protocols": body.protocols,
            "ptz": body.ptz_supported,
            "mp": body.max_resolution_mp,
            "notes": body.notes,
        },
    )
    await db.commit()
    return dict(result.mappings().one())


@router.delete("/models/{model_id}", status_code=204,
               dependencies=[Depends(require_permission("camera:create"))])
async def delete_model(model_id: uuid.UUID,
                       db: AsyncSession = Depends(get_db_with_tenant)) -> None:
    result = await db.execute(
        text("SELECT id, is_builtin FROM camera_model_library WHERE id = :id"),
        {"id": str(model_id)})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, detail="Camera model not found")
    if row["is_builtin"]:
        raise HTTPException(403, detail="Built-in models cannot be deleted")
    await db.execute(
        text("DELETE FROM camera_model_library WHERE id = :id"), {"id": str(model_id)})
    await db.commit()
