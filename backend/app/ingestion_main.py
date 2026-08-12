"""Standalone camera-ingestion process (plan §5). Runs as its own container so
an RTSP decode crash can't take the API down. One asyncio task per active
camera; the blocking cv2 calls within each task's loop are individually wrapped
in asyncio.to_thread so one camera's slow/blocked read doesn't stall the others
or the polling-for-new-cameras loop.
"""

import asyncio
import base64
import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from uuid import UUID, uuid4

import cv2
import numpy as np
import redis.asyncio as redis
from prometheus_client import Counter, start_http_server
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.db.session import AsyncSessionLocal
from app.services.camera_service import StreamHealthTracker
from app.services.video_compat import H264Writer
from shared.constants import FRAME_JOBS_STREAM
from shared.events import FrameJob

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_SECONDS = 2.0
CAMERA_REFRESH_INTERVAL_SECONDS = 30.0

# Pre/post event video buffer configuration
PRE_EVENT_BUFFER_FRAMES = int(os.environ.get("PRE_EVENT_BUFFER_FRAMES", "20"))  # ~40s
POST_EVENT_CAPTURE_FRAMES = int(os.environ.get("POST_EVENT_CAPTURE_FRAMES", "10"))  # ~20s
RECORDINGS_ROOT = os.environ.get("RECORDINGS_ROOT", "/data/recordings")

# Per-camera ring buffer: camera_id (str) -> deque of (jpeg_bytes, captured_at, stream_id, tenant_id)
_frame_buffers: dict[str, deque] = {}
# Cameras currently recording a post-event clip: camera_id -> frames_remaining
_post_event_remaining: dict[str, int] = {}
_post_event_clips: dict[str, list] = {}  # camera_id -> list of jpeg_bytes

# ingestion isn't an HTTP app, so it exposes Prometheus metrics the same way
# the AI workers do: a minimal threaded HTTP server prometheus_client spins
# up internally (plan §10), not a FastAPI route.
frame_jobs_published_total = Counter(
    "frame_jobs_published_total", "Frame jobs published to the frame_jobs Redis Stream", ["tenant_id", "camera_id"]
)


async def set_tenant_context(db: AsyncSession, tenant_id: UUID) -> None:
    await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})


async def record_health_transition(db: AsyncSession, tenant_id: UUID, camera_id: UUID, stream_id: UUID, status: str, event_type: str, detail: str | None = None) -> None:
    await set_tenant_context(db, tenant_id)
    await db.execute(
        text("UPDATE streams SET status = :status, updated_at = now() WHERE id = :id"),
        {"status": status, "id": stream_id},
    )
    await db.execute(
        text(
            "INSERT INTO camera_health_events (tenant_id, camera_id, event_type, detail) "
            "VALUES (:tid, :cid, :etype, :detail)"
        ),
        {"tid": tenant_id, "cid": camera_id, "etype": event_type, "detail": detail},
    )
    await db.commit()


def _open_capture(url: str) -> cv2.VideoCapture:
    return cv2.VideoCapture(url, cv2.CAP_FFMPEG)


def _read_frame(cap: cv2.VideoCapture):
    if cap is None or not cap.isOpened():
        return False, None
    ok, frame = cap.read()
    return ok, frame


def _encode_jpeg_b64(frame) -> tuple[str, int, int]:
    height, width = frame.shape[0], frame.shape[1]
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii"), width, height


def _build_auth_url(url: str, auth_config: dict | None) -> str:
    """Embed username/password from auth_config into the RTSP URL.

    Decrypts password_enc (Fernet token) if present; falls back to legacy
    plaintext 'password' key for backwards compatibility.
    """
    if not auth_config:
        return url
    username = auth_config.get("username")
    raw_password = auth_config.get("password_enc") or auth_config.get("password")
    if not username or not raw_password:
        return url
    try:
        password = decrypt_secret(raw_password) if auth_config.get("password_enc") else raw_password
    except ValueError:
        logger.warning("Failed to decrypt stream credential; skipping auth URL embedding")
        return url
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        return url
    return f"{scheme}://{username}:{password}@{rest}"


def _write_clip_to_mp4(frames_jpeg: list[bytes], output_path: str, fps: float = 0.5) -> int:
    """Write JPEG frame list to an MP4 file. Returns file size in bytes."""
    if not frames_jpeg:
        return 0
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    first = cv2.imdecode(np.frombuffer(frames_jpeg[0], np.uint8), cv2.IMREAD_COLOR)
    if first is None:
        return 0
    h, w = first.shape[:2]
    # H.264, not cv2's mp4v fourcc — mp4v is MPEG-4 Part 2, which no browser
    # decodes, so these event clips were unplayable in the `<video>` element on
    # Playback/Evidence even though the file itself was valid.
    writer = H264Writer(output_path, fps=fps, size=(w, h))
    for jpeg in frames_jpeg:
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is not None:
            writer.write(frame)
    writer.release()  # finalises the container; size is only valid after this
    return os.path.getsize(output_path) if os.path.exists(output_path) else 0


async def _save_event_clip(
    tenant_id: UUID, camera_id: UUID, stream_id: UUID,
    pre_frames: list[bytes], post_frames: list[bytes],
) -> None:
    """Persist a pre+post event clip to disk and insert a recordings row."""
    all_frames = pre_frames + post_frames
    if not all_frames:
        return

    rec_id = uuid4()
    rel_path = f"{tenant_id}/{camera_id}/{rec_id}_event.mp4"
    abs_path = os.path.join(RECORDINGS_ROOT, rel_path)

    try:
        file_size = await asyncio.to_thread(_write_clip_to_mp4, all_frames, abs_path)
    except Exception as exc:
        logger.warning("Failed to write event clip %s: %s", abs_path, exc)
        return

    duration_s = max(1, len(all_frames))
    try:
        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, tenant_id)
            await db.execute(
                text(
                    """
                    INSERT INTO recordings (id, tenant_id, camera_id, stream_id, status,
                                            started_at, ended_at, file_path, file_size_bytes,
                                            duration_seconds)
                    VALUES (:id, :tid, :cid, :sid, 'completed',
                            now() - (:dur * INTERVAL '1 second'),
                            now(), :fp, :fsize, :dur)
                    """
                ),
                {
                    "id": rec_id, "tid": tenant_id, "cid": camera_id, "sid": stream_id,
                    "dur": duration_s, "fp": rel_path, "fsize": file_size,
                },
            )
            await db.commit()
        logger.info("Event clip saved: %s (%d frames, %d bytes)", rel_path, len(all_frames), file_size)
    except Exception as exc:
        logger.warning("Failed to insert recording row: %s", exc)


async def alert_event_subscriber(redis_client: redis.Redis) -> None:
    """Subscribe to tenant alert events and trigger pre/post clip saves."""
    pubsub = redis_client.pubsub()
    await pubsub.psubscribe("tenant_events:*")
    async for message in pubsub.listen():
        if message["type"] != "pmessage":
            continue
        try:
            payload = json.loads(message["data"])
            if payload.get("event_type") != "alert_created":
                continue
            camera_id_str = str(payload.get("payload", {}).get("camera_id") or "")
            if not camera_id_str or camera_id_str not in _frame_buffers:
                continue
            # Don't start a new clip if one is already in progress
            if camera_id_str in _post_event_remaining:
                continue
            # Snapshot the pre-event buffer
            pre_frames = [entry[0] for entry in list(_frame_buffers[camera_id_str])]
            # Store context so run_camera_loop knows to capture post-event frames
            _post_event_remaining[camera_id_str] = POST_EVENT_CAPTURE_FRAMES
            _post_event_clips[camera_id_str] = pre_frames
        except Exception as exc:
            logger.debug("alert_event_subscriber error: %s", exc)


async def run_camera_loop(redis_client: redis.Redis, tenant_id: UUID, camera_id: UUID, stream_id: UUID, url: str, ai_modules_enabled: list[str], auth_config: dict | None = None) -> None:
    tracker = StreamHealthTracker()
    cap: cv2.VideoCapture | None = None
    full_url = _build_auth_url(url, auth_config)
    cam_key = str(camera_id)

    # Initialise ring buffer for this camera
    if cam_key not in _frame_buffers:
        _frame_buffers[cam_key] = deque(maxlen=PRE_EVENT_BUFFER_FRAMES)

    while True:
        if cap is None:
            cap = await asyncio.to_thread(_open_capture, full_url)

        ok, frame = await asyncio.to_thread(_read_frame, cap)

        if ok:
            transition = tracker.record_success()
            if transition is not None:
                async with AsyncSessionLocal() as db:
                    await record_health_transition(db, tenant_id, camera_id, stream_id, transition.new_status, transition.event_type)

            frame_b64, width, height = await asyncio.to_thread(_encode_jpeg_b64, frame)
            jpeg_bytes = base64.b64decode(frame_b64)
            captured_at = datetime.now(timezone.utc)

            # Push frame to ring buffer (always, regardless of post-event state)
            _frame_buffers[cam_key].append((jpeg_bytes, captured_at, stream_id, tenant_id))

            # Post-event clip accumulation
            if cam_key in _post_event_remaining:
                _post_event_clips[cam_key].append(jpeg_bytes)
                _post_event_remaining[cam_key] -= 1
                if _post_event_remaining[cam_key] <= 0:
                    pre_post = _post_event_clips.pop(cam_key, [])
                    del _post_event_remaining[cam_key]
                    asyncio.create_task(_save_event_clip(tenant_id, camera_id, stream_id, [], pre_post))

            job = FrameJob(
                job_id=uuid4(), tenant_id=tenant_id, camera_id=camera_id,
                frame_jpeg_b64=frame_b64, frame_width=width, frame_height=height,
                captured_at=captured_at, ai_modules_enabled=ai_modules_enabled,
            )
            # maxlen: each entry embeds a full base64 JPEG frame, so this cap
            # is a memory bound, not just a count — 10_000 measured out to
            # ~1.24GB of Redis memory in practice with only 2 of 9 consumer
            # groups draining it, which was the actual blocker to running
            # more AI workers on this host. Worker crash-recovery already
            # reclaims stale-but-unacked messages within ~30s (plan §6/§13's
            # claim_stale_messages), so a large backlog buffer isn't needed
            # for normal resilience — 2_000 (~250MB worst case) still covers
            # several minutes of backlog per module.
            await redis_client.xadd(FRAME_JOBS_STREAM, job.to_redis_fields(), maxlen=2_000, approximate=True)
            frame_jobs_published_total.labels(tenant_id=str(tenant_id), camera_id=str(camera_id)).inc()

            async with AsyncSessionLocal() as db:
                await set_tenant_context(db, tenant_id)
                await db.execute(
                    text("UPDATE streams SET last_frame_at = now() WHERE id = :id"), {"id": stream_id}
                )
                await db.commit()

            await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)
        else:
            transition = tracker.record_failure()
            if transition is not None:
                async with AsyncSessionLocal() as db:
                    await record_health_transition(
                        db, tenant_id, camera_id, stream_id, transition.new_status, transition.event_type,
                        detail=f"consecutive_failures={tracker.consecutive_failures}",
                    )
            # Stale RTSP sessions don't reliably recover via re-read() alone — recreate.
            if cap is not None:
                await asyncio.to_thread(cap.release)
                cap = None
            await asyncio.sleep(tracker.backoff_seconds())


async def discover_and_run_cameras(redis_client: redis.Redis, running: dict[UUID, asyncio.Task]) -> None:
    """Ingestion serves every tenant's cameras from one process, but `cameras`
    and `streams` are RLS-protected — an unscoped query returns zero rows for
    every tenant, not "everything" (RLS is fail-closed, plan §2/§3). There is no
    single tenant context for a process whose job is ingesting for all tenants
    at once, so this loops over each active tenant explicitly (via `tenants`,
    which has no RLS) and queries each one's cameras under its own context,
    rather than reaching for a privileged/bypass connection — keeps every
    service on the same restricted svc_app role, no special case."""
    rows = []
    async with AsyncSessionLocal() as db:
        tenant_rows = (
            await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))
        ).fetchall()
        for tenant_row in tenant_rows:
            await set_tenant_context(db, tenant_row.id)
            tenant_cameras = (
                await db.execute(
                    text(
                        """
                        SELECT s.id AS stream_id, s.camera_id, c.tenant_id,
                               s.url, c.ai_modules_enabled, s.auth_config
                        FROM streams s
                        JOIN cameras c ON c.id = s.camera_id
                        WHERE c.is_active = TRUE
                        """
                    )
                )
            ).fetchall()
            rows.extend(tenant_cameras)

    for row in rows:
        if row.stream_id in running and not running[row.stream_id].done():
            continue
        running[row.stream_id] = asyncio.create_task(
            run_camera_loop(
                redis_client, row.tenant_id, row.camera_id, row.stream_id,
                row.url, list(row.ai_modules_enabled or []),
                auth_config=dict(row.auth_config) if row.auth_config else None,
            )
        )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    start_http_server(int(os.environ.get("METRICS_PORT", "8001")))
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    # A second Redis client for the subscriber (can't reuse the stream-write client)
    redis_sub = redis.from_url(settings.REDIS_URL, decode_responses=True)
    running: dict[UUID, asyncio.Task] = {}
    subscriber_task = asyncio.create_task(alert_event_subscriber(redis_sub))
    try:
        while True:
            await discover_and_run_cameras(redis_client, running)
            await asyncio.sleep(CAMERA_REFRESH_INTERVAL_SECONDS)
    finally:
        subscriber_task.cancel()
        for task in running.values():
            task.cancel()
        await redis_client.aclose()
        await redis_sub.aclose()


if __name__ == "__main__":
    asyncio.run(main())
