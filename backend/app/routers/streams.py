"""Stream management — RTSP/ONVIF stream records per camera.

Each camera can have multiple streams (e.g. main + sub stream).
The ingestion service reads active streams; status is updated by the ingestion
health monitor. Operators manage streams here; status is read-only (set by the
ingestion service itself).

Live MJPEG proxy: GET /{camera_id}/streams/{stream_id}/live?token=<jwt>
Returns multipart/x-mixed-replace so browsers can display it directly via
an <img> tag without any JavaScript player.
"""

import asyncio
import json
import os
import time
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import cv2
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.rtsp_validate import validate_rtsp_url
from app.core.security import InvalidTokenError, decode_access_token
from app.db.session import AsyncSessionLocal
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services.video_compat import H264Writer, ensure_browser_playable

router = APIRouter(prefix="/api/v1/cameras", tags=["streams"])

VALID_PROTOCOLS = {"rtsp", "rtmp", "onvif", "http"}
RECORDINGS_ROOT = os.environ.get("RECORDINGS_ROOT", "/data/recordings")

# Active recording asyncio tasks: recording_id -> Task
_active_recordings: dict[str, asyncio.Task] = {}


class StreamCreate(BaseModel):
    url: str
    protocol: str = "rtsp"
    username: str | None = None
    password: str | None = None
    continuous_recording: bool = False


class StreamUpdate(BaseModel):
    url: str | None = None
    protocol: str | None = None
    username: str | None = None
    password: str | None = None
    continuous_recording: bool | None = None


def _build_auth_url(url: str, auth_config: dict) -> str:
    """Embed username/password into RTSP URL from auth_config.

    Decrypts password_enc (Fernet token) if present; falls back to legacy
    plaintext 'password' key for backwards compatibility.
    """
    username = auth_config.get("username")
    raw_password = auth_config.get("password_enc") or auth_config.get("password")
    if not username or not raw_password:
        return url
    if "://" not in url:
        return url
    try:
        password = decrypt_secret(raw_password) if auth_config.get("password_enc") else raw_password
    except ValueError:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        return url
    return f"{scheme}://{username}:{password}@{rest}"


# ──────────────────────────────────────────────────────────
# Streams CRUD
# ──────────────────────────────────────────────────────────

async def _check_camera_site_allowed(db: AsyncSession, camera_id: str,
                                     allowed_sites: list[str] | None) -> None:
    """404 when the camera's site is outside the caller's scope (Gap 81)."""
    if allowed_sites is None:
        return
    row = (await db.execute(
        text("SELECT site_id FROM cameras WHERE id = CAST(:cid AS uuid)"),
        {"cid": camera_id},
    )).first()
    if row is None or not is_site_allowed(allowed_sites, row.site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")


@router.get("/{camera_id}/streams", dependencies=[Depends(require_permission("camera:read"))])
async def list_streams(
    camera_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    await _check_camera_site_allowed(db, camera_id, allowed_sites)
    result = await db.execute(
        text(
            "SELECT id, camera_id, protocol, url, status, last_frame_at, "
            "       (auth_config != '{}') AS has_credentials, continuous_recording, "
            "       created_at, updated_at "
            "FROM streams WHERE camera_id = :camera_id ORDER BY created_at"
        ),
        {"camera_id": camera_id},
    )
    return [dict(r._mapping) for r in result]


@router.post("/{camera_id}/streams", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("camera:create"))])
async def create_stream(camera_id: str, body: StreamCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    if body.protocol not in VALID_PROTOCOLS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"protocol must be one of {VALID_PROTOCOLS}")
    ok, reason = validate_rtsp_url(body.url)
    if not ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, reason)
    cam = await db.execute(text("SELECT id FROM cameras WHERE id = :id"), {"id": camera_id})
    if not cam.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    auth_config = {}
    if body.username:
        auth_config["username"] = body.username
    if body.password:
        auth_config["password_enc"] = encrypt_secret(body.password)
    result = await db.execute(
        text(
            "INSERT INTO streams (tenant_id, camera_id, protocol, url, auth_config, continuous_recording) "
            "VALUES (current_setting('app.current_tenant')::uuid, :camera_id, :protocol, :url, "
            "        CAST(:auth_config AS jsonb), :cont_rec) "
            "RETURNING id"
        ),
        {
            "camera_id": camera_id,
            "protocol": body.protocol,
            "url": body.url,
            "auth_config": json.dumps(auth_config),
            "cont_rec": body.continuous_recording,
        },
    )
    new_id = result.scalar_one()
    await db.commit()
    return {
        "id": new_id,
        "camera_id": camera_id,
        "protocol": body.protocol,
        "url": body.url,
        "status": "offline",
        "has_credentials": bool(auth_config),
        "continuous_recording": body.continuous_recording,
    }


@router.put("/{camera_id}/streams/{stream_id}", dependencies=[Depends(require_permission("camera:update"))])
async def update_stream(camera_id: str, stream_id: str, body: StreamUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    if body.protocol is not None and body.protocol not in VALID_PROTOCOLS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"protocol must be one of {VALID_PROTOCOLS}")
    sets, params = [], {"id": stream_id, "camera_id": camera_id}
    if body.url is not None:
        ok, reason = validate_rtsp_url(body.url)
        if not ok:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, reason)
        sets.append("url = :url"); params["url"] = body.url
    if body.protocol is not None:
        sets.append("protocol = :protocol"); params["protocol"] = body.protocol
    if body.username is not None or body.password is not None:
        existing = await db.execute(
            text("SELECT auth_config FROM streams WHERE id = :id AND camera_id = :camera_id"),
            {"id": stream_id, "camera_id": camera_id},
        )
        row = existing.first()
        current_auth = dict(row[0]) if row and row[0] else {}
        if body.username is not None:
            current_auth["username"] = body.username
        if body.password is not None:
            current_auth["password_enc"] = encrypt_secret(body.password)
            current_auth.pop("password", None)  # remove any legacy plaintext key
        sets.append("auth_config = CAST(:auth_config AS jsonb)")
        params["auth_config"] = json.dumps(current_auth)
    if body.continuous_recording is not None:
        sets.append("continuous_recording = :cont_rec")
        params["cont_rec"] = body.continuous_recording
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    sets.append("updated_at = now()")
    result = await db.execute(
        text(
            f"UPDATE streams SET {', '.join(sets)} WHERE id = :id AND camera_id = :camera_id "
            "RETURNING id, camera_id, protocol, url, status, last_frame_at, "
            "       (auth_config != '{}') AS has_credentials, continuous_recording, updated_at"
        ),
        params,
    )
    row = result.mappings().first()
    await db.commit()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stream not found")
    return dict(row)


@router.delete("/{camera_id}/streams/{stream_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_permission("camera:delete"))])
async def delete_stream(camera_id: str, stream_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(
        text("DELETE FROM streams WHERE id = :id AND camera_id = :camera_id"),
        {"id": stream_id, "camera_id": camera_id},
    )
    await db.commit()


# ──────────────────────────────────────────────────────────
# Live MJPEG proxy
# ──────────────────────────────────────────────────────────

def _read_frame(cap: cv2.VideoCapture):
    return cap.read()


def _encode_jpeg(frame) -> bytes | None:
    ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return buf.tobytes() if ok else None


async def _mjpeg_frames(rtsp_url: str):
    cap: cv2.VideoCapture = await asyncio.to_thread(cv2.VideoCapture, rtsp_url)
    try:
        if not cap.isOpened():
            return
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        while True:
            ret, frame = await asyncio.to_thread(_read_frame, cap)
            if not ret:
                break
            jpeg = await asyncio.to_thread(_encode_jpeg, frame)
            if jpeg is None:
                continue
            yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg + b'\r\n'
            await asyncio.sleep(0)
    finally:
        await asyncio.to_thread(cap.release)


@router.get("/{camera_id}/streams/{stream_id}/live")
async def live_stream(
    camera_id: str,
    stream_id: str,
    token: str = Query(..., description="JWT access token"),
):
    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))

    tenant_id = payload["tenant_id"]
    role_id   = payload["role_id"]
    user_id   = payload["sub"]

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": tenant_id},
        )
        perm = await session.execute(
            text(
                "SELECT 1 FROM role_permissions rp "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE rp.role_id = :role_id AND p.code = 'camera:read'"
            ),
            {"role_id": role_id},
        )
        if perm.first() is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: camera:read")

        result = await session.execute(
            text(
                "SELECT s.url, s.auth_config, c.site_id FROM streams s "
                "JOIN cameras c ON c.id = s.camera_id "
                "WHERE s.id = :sid AND c.id = :cid"
            ),
            {"sid": stream_id, "cid": camera_id},
        )
        row = result.first()

        # Site-scoped access (Gap 81): roles 3-7 with site assignments may only
        # view cameras at their assigned sites; clients with none see nothing.
        if row is not None and role_id not in (1, 2):
            assigned = await session.execute(
                text("SELECT site_id FROM user_sites WHERE user_id = CAST(:uid AS uuid)"),
                {"uid": user_id},
            )
            allowed = {str(r.site_id) for r in assigned}
            restricted = bool(allowed) or role_id == 7
            if restricted and (row[2] is None or str(row[2]) not in allowed):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Stream not found")

    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stream not found")

    rtsp_url = _build_auth_url(row[0], row[1] or {})
    return StreamingResponse(
        _mjpeg_frames(rtsp_url),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ──────────────────────────────────────────────────────────
# HLS live streaming (Gap 90) — scalable alternative to MJPEG
# ──────────────────────────────────────────────────────────

async def _authorize_stream_access(token: str, camera_id: str, stream_id: str) -> tuple[str, dict]:
    """Query-param-JWT auth for media endpoints (browsers can't set an
    Authorization header on <video>/<img>). Returns (url, auth_config) or
    raises 401/403/404. Enforces camera:read + Gap 81 site scoping."""
    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))
    role_id = payload["role_id"]
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": payload["tenant_id"]},
        )
        perm = await session.execute(
            text("SELECT 1 FROM role_permissions rp "
                 "JOIN permissions p ON p.id = rp.permission_id "
                 "WHERE rp.role_id = :role_id AND p.code = 'camera:read'"),
            {"role_id": role_id},
        )
        if perm.first() is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: camera:read")
        row = (await session.execute(
            text("SELECT s.url, s.auth_config, c.site_id FROM streams s "
                 "JOIN cameras c ON c.id = s.camera_id "
                 "WHERE s.id = :sid AND c.id = :cid"),
            {"sid": stream_id, "cid": camera_id},
        )).first()
        if row is not None and role_id not in (1, 2):
            assigned = await session.execute(
                text("SELECT site_id FROM user_sites WHERE user_id = CAST(:uid AS uuid)"),
                {"uid": payload["sub"]},
            )
            allowed = {str(r.site_id) for r in assigned}
            restricted = bool(allowed) or role_id == 7
            if restricted and (row[2] is None or str(row[2]) not in allowed):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Stream not found")
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stream not found")
    return row[0], row[1] or {}


@router.get("/{camera_id}/streams/{stream_id}/hls/index.m3u8")
async def hls_playlist(
    camera_id: str,
    stream_id: str,
    token: str = Query(..., description="JWT access token"),
):
    """Start (or reuse) the ffmpeg HLS session and return the live playlist.
    503 while the first segment is still being produced — the player retries."""
    from app.services.hls_stream import ensure_session

    url, auth = await _authorize_stream_access(token, camera_id, stream_id)
    rtsp_url = _build_auth_url(url, auth)
    playlist = await ensure_session(stream_id, rtsp_url)
    if playlist is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Stream is starting or unavailable; retry shortly")
    return FileResponse(playlist, media_type="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-cache"})


@router.get("/{camera_id}/streams/{stream_id}/hls/{segment}")
async def hls_segment(
    camera_id: str,
    stream_id: str,
    segment: str,
    token: str = Query(..., description="JWT access token"),
):
    """Serve an HLS segment (or the playlist re-request). Path-traversal-safe:
    the filename is validated and resolved strictly inside the stream's dir."""
    from app.services.hls_stream import segment_file_path

    await _authorize_stream_access(token, camera_id, stream_id)
    path = segment_file_path(stream_id, segment)
    if path is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid segment name")
    if not os.path.exists(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment not found")
    if segment.endswith(".m3u8"):
        media = "application/vnd.apple.mpegurl"
    elif segment.endswith(".m4s"):
        media = "video/iso.segment"
    else:
        media = "video/mp2t"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-cache"})


# ──────────────────────────────────────────────────────────
# Camera health events
# ──────────────────────────────────────────────────────────

@router.get("/{camera_id}/health", dependencies=[Depends(require_permission("camera:read"))])
async def get_camera_health(
    camera_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    await _check_camera_site_allowed(db, camera_id, allowed_sites)
    result = await db.execute(
        text(
            "SELECT id, event_type, detail, occurred_at "
            "FROM camera_health_events WHERE camera_id = :camera_id "
            "ORDER BY occurred_at DESC LIMIT :limit"
        ),
        {"camera_id": camera_id, "limit": min(limit, 100)},
    )
    return [dict(r._mapping) for r in result]


# ──────────────────────────────────────────────────────────
# Recordings — start / stop / list / download
# ──────────────────────────────────────────────────────────

def _record_to_file(file_path: str, rtsp_url: str, stop_event: asyncio.Event) -> tuple[int, int]:
    """Write RTSP stream frames to MP4. Returns (frame_count, file_size_bytes).

    Encodes H.264 via `H264Writer`, not `cv2.VideoWriter`. This used to write
    the `mp4v` fourcc, which produces MPEG-4 Part 2 — a format no browser can
    decode, so recorded footage downloaded fine but never played back in the
    web or desktop app. See `app/services/video_compat` for why cv2 can't do
    H.264 itself."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        return 0, 0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720
    writer = H264Writer(file_path, fps=10.0, size=(w, h))
    frame_count = 0
    try:
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                break
            writer.write(frame)
            frame_count += 1
    finally:
        # release() is what finalises the container (and runs the faststart
        # rewrite), so the file size has to be read after it returns.
        writer.release()
        cap.release()
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    return frame_count, file_size


async def _recording_task(
    recording_id: str,
    tenant_id: str,
    camera_id: str,
    rtsp_url: str,
    file_path: str,
    stop_event: asyncio.Event,
):
    started = time.monotonic()
    frame_count, file_size = await asyncio.to_thread(
        _record_to_file, file_path, rtsp_url, stop_event
    )
    duration = int(time.monotonic() - started)

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": tenant_id},
        )
        await session.execute(
            text(
                "UPDATE recordings SET status = 'completed', ended_at = now(), "
                "file_size_bytes = :size, duration_seconds = :dur "
                "WHERE id = :id"
            ),
            {"id": recording_id, "size": file_size, "dur": duration},
        )
        await session.commit()

    _active_recordings.pop(recording_id, None)


@router.post(
    "/{camera_id}/streams/{stream_id}/recordings/start",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("recording:create"))],
)
async def start_recording(
    camera_id: str,
    stream_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    result = await db.execute(
        text("SELECT s.url, s.auth_config, c.site_id FROM streams s JOIN cameras c ON c.id = s.camera_id "
             "WHERE s.id = :sid AND c.id = :cid"),
        {"sid": stream_id, "cid": camera_id},
    )
    row = result.first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stream not found")

    rtsp_url = _build_auth_url(row[0], row[1] or {})
    site_id = row[2]

    tenant_id_row = await db.execute(text("SELECT current_setting('app.current_tenant')"))
    tenant_id = tenant_id_row.scalar()

    recording_id = str(_uuid.uuid4())
    rel_path = f"{tenant_id}/{camera_id}/{recording_id}.mp4"
    file_path = os.path.join(RECORDINGS_ROOT, rel_path)

    await db.execute(
        text(
            "INSERT INTO recordings "
            "  (id, tenant_id, camera_id, stream_id, site_id, status, file_path) "
            "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:cid AS uuid), "
            "        CAST(:sid AS uuid), CAST(:site_id AS uuid), 'recording', :fp)"
        ),
        {
            "id": recording_id,
            "tid": str(tenant_id),
            "cid": str(camera_id),
            "sid": str(stream_id),
            "site_id": str(site_id) if site_id else None,
            "fp": rel_path,
        },
    )
    await db.commit()

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        _recording_task(recording_id, tenant_id, camera_id, rtsp_url, file_path, stop_event)
    )
    # Store both task and stop_event so /stop can signal it
    _active_recordings[recording_id] = (task, stop_event)

    return {"recording_id": recording_id, "status": "recording", "file_path": rel_path}


@router.post(
    "/{camera_id}/streams/{stream_id}/recordings/{recording_id}/stop",
    dependencies=[Depends(require_permission("recording:create"))],
)
async def stop_recording(
    camera_id: str,
    stream_id: str,
    recording_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    entry = _active_recordings.get(recording_id)
    if entry:
        task, stop_event = entry
        stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        _active_recordings.pop(recording_id, None)
    else:
        # If the task is gone (completed naturally), just update the DB row
        await db.execute(
            text(
                "UPDATE recordings SET status = 'completed', ended_at = coalesce(ended_at, now()) "
                "WHERE id = :id"
            ),
            {"id": recording_id},
        )
        # commit clears the transaction-scoped app.current_tenant GUC (SET LOCAL
        # semantics) — capture it, commit, restore it so the RLS-scoped read-back
        # below still sees this tenant. Without this the SELECT runs with an empty
        # GUC and `recordings`' policy fails casting '' to uuid, turning a normal
        # stop-recording into a 500. Same convention as leave.py/training.py.
        tid = (await db.execute(text("SELECT current_setting('app.current_tenant', true)"))).scalar()
        await db.commit()
        if tid:
            await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tid})

    result = await db.execute(
        text("SELECT id, status, started_at, ended_at, file_size_bytes, duration_seconds FROM recordings WHERE id = :id"),
        {"id": recording_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    return dict(row)


@router.get(
    "/{camera_id}/streams/{stream_id}/recordings",
    dependencies=[Depends(require_permission("recording:read"))],
)
async def list_recordings(
    camera_id: str,
    stream_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    result = await db.execute(
        text(
            "SELECT id, status, started_at, ended_at, file_path, file_size_bytes, duration_seconds, created_at "
            "FROM recordings WHERE camera_id = :cid AND stream_id = :sid "
            "ORDER BY started_at DESC LIMIT 100"
        ),
        {"cid": camera_id, "sid": stream_id},
    )
    rows = [dict(r._mapping) for r in result]
    # Annotate active recordings
    for row in rows:
        row["is_active"] = row["id"] in _active_recordings
    return rows


@router.get("/recordings/{recording_id}/download", dependencies=[Depends(require_permission("recording:read"))])
async def download_recording(recording_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("SELECT file_path, status FROM recordings WHERE id = :id"),
        {"id": recording_id},
    )
    row = result.first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    if row[1] != "completed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Recording is not yet completed")
    file_path = os.path.join(RECORDINGS_ROOT, row[0])
    if not os.path.exists(file_path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording file not found on disk")
    return FileResponse(
        file_path,
        media_type="video/mp4",
        filename=os.path.basename(file_path),
        headers={"Content-Disposition": f'attachment; filename="{os.path.basename(file_path)}"'},
    )


# ---------------------------------------------------------------------------
# Global (tenant-wide) endpoints — registered under a separate router prefix
# ---------------------------------------------------------------------------
global_router = APIRouter(prefix="/api/v1", tags=["streams"])


@global_router.get("/recordings", dependencies=[Depends(require_permission("recording:read"))])
async def list_all_recordings(
    db: AsyncSession = Depends(get_db_with_tenant),
    status_filter: str | None = None,
    site_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Tenant-wide recording list with camera and site context."""
    where_clauses = []
    params: dict = {"limit": min(limit, 200), "offset": max(offset, 0)}
    if status_filter:
        where_clauses.append("r.status = :status_filter")
        params["status_filter"] = status_filter
    if site_id:
        where_clauses.append("r.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    scope = site_scope_clause(allowed_sites, "r.site_id", params)
    if scope:
        where_clauses.append(scope)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    query = f"""
        SELECT r.id, r.camera_id, r.stream_id, r.site_id, r.status,
               r.started_at, r.ended_at, r.file_path, r.file_size_bytes,
               r.duration_seconds, r.created_at,
               c.name AS camera_name, s.name AS site_name
        FROM recordings r
        LEFT JOIN cameras c ON c.id = r.camera_id
        LEFT JOIN sites s ON s.id = r.site_id
        {where}
        ORDER BY r.started_at DESC LIMIT :limit OFFSET :offset
    """
    result = await db.execute(text(query), params)
    rows = [dict(r._mapping) for r in result]
    for row in rows:
        row["is_active"] = row["id"] in _active_recordings
    return rows


@global_router.get("/recordings/timeline", dependencies=[Depends(require_permission("recording:read"))])
async def recording_timeline(
    camera_id: str,
    date: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """One day of recorded segments + alert markers for a camera (Gap 85).

    date: YYYY-MM-DD, interpreted in the TENANT's timezone rather than UTC.
    An operator asking for "1 August" means their 1 August; served as a UTC
    day, a Singapore tenant got a window running 08:00 to 08:00 and footage
    appeared on the wrong date. Same idiom as attendance's live board."""
    tz_row = (await db.execute(
        text("SELECT COALESCE(timezone, 'UTC') AS tz FROM tenants "
             "WHERE id = current_setting('app.current_tenant')::uuid")
    )).first()
    try:
        tz = ZoneInfo(tz_row.tz if tz_row else "UTC")
    except Exception:
        tz = timezone.utc  # a bad tenant timezone must not 500 the page
    try:
        day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "date must be YYYY-MM-DD")
    day_end = day_start + timedelta(days=1)

    cam = (await db.execute(
        text("SELECT id, name, site_id FROM cameras WHERE id = CAST(:cid AS uuid)"),
        {"cid": camera_id},
    )).first()
    if cam is None or not is_site_allowed(allowed_sites, cam.site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")

    seg_result = await db.execute(
        text("""
            SELECT id, status, started_at, ended_at, file_size_bytes, duration_seconds
            FROM recordings
            WHERE camera_id = CAST(:cid AS uuid)
              AND started_at < :day_end
              AND COALESCE(ended_at, now()) > :day_start
            ORDER BY started_at
        """),
        {"cid": camera_id, "day_start": day_start, "day_end": day_end},
    )
    segments = []
    for r in seg_result:
        d = dict(r._mapping)
        d["id"] = str(d["id"])
        d["is_active"] = d["id"] in _active_recordings
        segments.append(d)

    alert_result = await db.execute(
        text("""
            SELECT id, severity, title, module_type, created_at
            FROM alerts
            WHERE camera_id = CAST(:cid AS uuid)
              AND created_at >= :day_start AND created_at < :day_end
            ORDER BY created_at
        """),
        {"cid": camera_id, "day_start": day_start, "day_end": day_end},
    )
    alerts = [dict(r._mapping) for r in alert_result]

    return {
        "camera_id": camera_id,
        "camera_name": cam.name,
        "date": date,
        "segments": segments,
        "alerts": alerts,
    }


@global_router.get("/recordings/{recording_id}/play")
async def play_recording(
    recording_id: str,
    token: str = Query(..., description="JWT access token"),
):
    """Inline playback for <video> tags (Gap 85). Browsers can't set an
    Authorization header on media elements, so — exactly like the live MJPEG
    endpoint — auth arrives as a query-param JWT, checked for recording:read."""
    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": payload["tenant_id"]},
        )
        perm = await session.execute(
            text(
                "SELECT 1 FROM role_permissions rp "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE rp.role_id = :role_id AND p.code = 'recording:read'"
            ),
            {"role_id": payload["role_id"]},
        )
        if perm.first() is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: recording:read")
        row = (await session.execute(
            text("SELECT r.file_path, r.status, r.site_id FROM recordings r "
                 "WHERE r.id = CAST(:id AS uuid)"),
            {"id": recording_id},
        )).first()

        # Site-scoped access (Gap 81): roles 3-7 with site assignments may
        # only play footage from their assigned sites; clients need a match.
        if row is not None and payload["role_id"] not in (1, 2):
            assigned = await session.execute(
                text("SELECT site_id FROM user_sites WHERE user_id = CAST(:uid AS uuid)"),
                {"uid": payload["sub"]},
            )
            allowed = {str(r.site_id) for r in assigned}
            restricted = bool(allowed) or payload["role_id"] == 7
            if restricted and (row[2] is None or str(row[2]) not in allowed):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")

    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    if row[1] != "completed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Recording is not yet completed")
    file_path = os.path.join(RECORDINGS_ROOT, row[0])
    if not os.path.exists(file_path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording file not found on disk")
    # Footage recorded before the H.264 fix is MPEG-4 Part 2 and won't decode in
    # any browser; heal it once here so an existing archive stays watchable.
    # New recordings are already H.264 and pass straight through.
    playable = await ensure_browser_playable(file_path)
    # Inline (no attachment disposition) so the browser <video> element plays
    # it. FileResponse honours the Range header, which is what makes seeking
    # and mid-file scrubbing work rather than forcing a full download.
    return FileResponse(playable, media_type="video/mp4")


@global_router.get("/streams", dependencies=[Depends(require_permission("camera:read"))])
async def list_all_streams(
    db: AsyncSession = Depends(get_db_with_tenant),
    status_filter: str | None = None,
    site_id: str | None = None,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Tenant-wide stream list with camera and site context — for dashboard camera status grid."""
    where_clauses = []
    params: dict = {}
    if status_filter:
        where_clauses.append("s.status = :status_filter")
        params["status_filter"] = status_filter
    if site_id:
        where_clauses.append("c.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    scope = site_scope_clause(allowed_sites, "c.site_id", params)
    if scope:
        where_clauses.append(scope)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    query = f"""
        SELECT s.id, s.camera_id, s.protocol, s.url, s.status, s.last_frame_at,
               s.created_at, s.updated_at,
               c.name AS camera_name, c.location, c.is_active AS camera_active,
               c.site_id, site.name AS site_name
        FROM streams s
        JOIN cameras c ON c.id = s.camera_id
        LEFT JOIN sites site ON site.id = c.site_id
        {where}
        ORDER BY c.name, s.created_at
    """
    result = await db.execute(text(query), params)
    return [dict(r._mapping) for r in result]
