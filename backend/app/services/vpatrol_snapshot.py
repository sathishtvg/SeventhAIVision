"""Capture one real frame from a camera, for patrol evidence.

Nothing in this platform could previously be asked for a single image.
camera_service is a health tracker, hls_stream runs ffmpeg for live viewing, and
ingestion decodes RTSP for detection — none of them expose "give me a frame
now". A scheduled patrol needs exactly that, whether or not anybody happens to
be watching the camera, so this pulls directly from the source rather than
borrowing a live-view session that may not exist.

NOT A BROWSER SCREENSHOT. Section 14 is explicit, and it matters: a screenshot
proves what was on somebody's screen, which is a claim about a monitor rather
than about a gate. This goes to the camera.

THE AUTHENTICATED URL CONTAINS THE CAMERA PASSWORD, and ffmpeg echoes its input
URL in error output. Storing that verbatim as snapshot_error would write a
credential into the database and print it on an officer's screen, so every
message out of here is scrubbed. The URL is never logged either.

ONLY rtsp, rtsps, http AND https ARE ACCEPTED. The URL comes from tenant
configuration, which makes it semi-trusted input reaching a subprocess; file://
and pipe: would turn a camera record into a local file read.

CONCURRENCY IS BOUNDED. ffmpeg processes are short but real, and this platform
runs on installations with little headroom. Better a patrol that takes a few
seconds longer than one that pushes the host into swap mid-capture.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://")
CAPTURE_TIMEOUT_SECONDS = 20
_MAX_CONCURRENT_CAPTURES = 2
_capture_slot = asyncio.Semaphore(_MAX_CONCURRENT_CAPTURES)

#: user:pass@host in any URL-ish string.
_CREDENTIAL = re.compile(r"(?P<scheme>\w+://)[^/\s@]+:[^/\s@]+@")


def scrub(message: str) -> str:
    """Remove embedded credentials from anything we are about to store or log.

    ffmpeg reports failures by quoting the input URL back, and that URL carries
    the camera password. Without this, a snapshot failure writes a working
    credential into snapshot_error, where it is then shown in the UI and copied
    into a PDF report.
    """
    if not message:
        return message
    return _CREDENTIAL.sub(lambda m: f"{m.group('scheme')}***:***@", message)


def build_source_url(url: str, auth_config: dict | None) -> str:
    """Embed credentials, mirroring routers/streams.py::_build_auth_url.

    Deliberately the same behaviour rather than a second interpretation of the
    same stored config — two readings of one credential format is how a camera
    works in live view and fails in a patrol.
    """
    auth_config = auth_config or {}
    username = auth_config.get("username")
    raw = auth_config.get("password_enc") or auth_config.get("password")
    if not username or not raw or "://" not in url:
        return url
    try:
        password = decrypt_secret(raw) if auth_config.get("password_enc") else raw
    except ValueError:
        # A credential we cannot decrypt is not a reason to abandon the capture:
        # plenty of cameras on a trusted LAN need no authentication at all.
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        return url
    return f"{scheme}://{username}:{password}@{rest}"


def storage_path(*, tenant_id: str, site_id: str, session_id: str,
                 session_camera_id: str, taken_at: datetime) -> str:
    """tenant/site/virtual-patrol/YYYY/MM/DD/session/camera.jpg

    Relative to EVIDENCE_ROOT, and stored as a reference rather than served as a
    path — downloads go through an authorized route like all other evidence.
    """
    return (f"{tenant_id}/{site_id}/virtual-patrol/{taken_at:%Y/%m/%d}/"
            f"{session_id}/{session_camera_id}.jpg")


async def _resolve_source(db: AsyncSession, camera_id: str) -> tuple[str | None, str | None]:
    """The camera's stream URL with credentials, or a reason there is none."""
    row = (await db.execute(text("""
        SELECT s.url, s.auth_config, c.is_active
          FROM cameras c
          LEFT JOIN streams s ON s.camera_id = c.id
         WHERE c.id = :cam
         ORDER BY s.created_at
         LIMIT 1
    """), {"cam": camera_id})).mappings().first()

    if row is None:
        return None, "The camera no longer exists."
    if not row["is_active"]:
        return None, "The camera is disabled."
    if not row["url"]:
        return None, "The camera has no stream configured."
    if not row["url"].lower().startswith(ALLOWED_SCHEMES):
        # Never echo the URL itself — it is attacker-influenced and may carry
        # credentials.
        return None, "The camera's stream uses an unsupported protocol."
    return build_source_url(row["url"], row["auth_config"]), None


def _ffmpeg_cmd(source_url: str, out_path: str) -> list[str]:
    return [
        "ffmpeg", "-nostdin", "-loglevel", "error",
        "-rtsp_transport", "tcp",
        # Cap what ffmpeg itself will wait for, so a camera that accepts the
        # connection and then says nothing cannot hold the slot open.
        #
        # -timeout, NOT -stimeout. The old spelling was removed for RTSP and
        # ffmpeg 7 rejects it outright ("Option not found") before it ever dials
        # the camera — so every capture would have failed, and the failure would
        # have read like an unreachable camera rather than a wrong flag.
        # Microseconds.
        "-timeout", str(CAPTURE_TIMEOUT_SECONDS * 1_000_000),
        "-i", source_url,
        "-frames:v", "1",
        "-q:v", "2",
        "-y", out_path,
    ]


async def capture(
    db: AsyncSession, *, session_camera_id: str,
) -> dict:
    """Capture the frame for one session camera and record the result.

    Returns a dict with ok=True and the stored reference, or ok=False and a
    scrubbed reason. It never raises for an unreachable camera: an offline
    camera is an expected outcome of a patrol, recorded as SNAPSHOT_FAILED or
    CAMERA_UNAVAILABLE so the officer sees it and can retry — not an exception
    that abandons the session halfway through.
    """
    row = (await db.execute(text("""
        SELECT sc.id, sc.camera_id, sc.session_id, sc.tenant_id, s.site_id
          FROM virtual_patrol_session_cameras sc
          JOIN virtual_patrol_sessions s ON s.id = sc.session_id
         WHERE sc.id = CAST(:id AS uuid)
    """), {"id": session_camera_id})).mappings().first()
    if row is None:
        return {"ok": False, "status": "CAMERA_UNAVAILABLE",
                "error": "Unknown patrol camera."}

    if row["camera_id"] is None:
        await _record_failure(db, session_camera_id, "CAMERA_UNAVAILABLE",
                              "The camera has been removed from this system.")
        return {"ok": False, "status": "CAMERA_UNAVAILABLE",
                "error": "The camera has been removed from this system."}

    source_url, problem = await _resolve_source(db, str(row["camera_id"]))
    if problem:
        await _record_failure(db, session_camera_id, "CAMERA_UNAVAILABLE", problem)
        return {"ok": False, "status": "CAMERA_UNAVAILABLE", "error": problem}

    taken_at = datetime.now(timezone.utc)
    rel_path = storage_path(
        tenant_id=str(row["tenant_id"]), site_id=str(row["site_id"]),
        session_id=str(row["session_id"]), session_camera_id=str(row["id"]),
        taken_at=taken_at,
    )
    abs_path = Path(settings.EVIDENCE_ROOT) / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    async with _capture_slot:
        ok, detail = await _run_ffmpeg(source_url, str(abs_path))

    if not ok or not abs_path.exists() or abs_path.stat().st_size == 0:
        # A zero-byte file is a failed capture wearing the shape of a success.
        if abs_path.exists():
            abs_path.unlink(missing_ok=True)
        reason = detail or "The camera did not return an image."
        await _record_failure(db, session_camera_id, "SNAPSHOT_FAILED", reason)
        return {"ok": False, "status": "SNAPSHOT_FAILED", "error": reason}

    data = abs_path.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()

    await db.execute(text("""
        UPDATE virtual_patrol_session_cameras
           SET snapshot_path = :path, snapshot_filename = :fn,
               snapshot_taken_at = :at, snapshot_checksum = :sum,
               snapshot_bytes = :bytes, snapshot_error = NULL,
               status = CASE WHEN status = 'PENDING' THEN 'IN_PROGRESS' ELSE status END
         WHERE id = CAST(:id AS uuid)
    """), {
        "path": rel_path, "fn": abs_path.name, "at": taken_at,
        "sum": checksum, "bytes": len(data), "id": session_camera_id,
    })

    return {"ok": True, "path": rel_path, "checksum": checksum,
            "bytes": len(data), "taken_at": taken_at}


async def _run_ffmpeg(source_url: str, out_path: str) -> tuple[bool, str | None]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *_ffmpeg_cmd(source_url, out_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return False, "ffmpeg is not available on this server."

    try:
        _, stderr = await asyncio.wait_for(proc.communicate(),
                                           timeout=CAPTURE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False, f"The camera did not respond within {CAPTURE_TIMEOUT_SECONDS} seconds."

    if proc.returncode == 0:
        return True, None

    # Scrubbed, and truncated: ffmpeg is verbose and the officer needs a reason,
    # not a transcript.
    detail = scrub((stderr or b"").decode("utf-8", "replace")).strip()
    detail = detail.splitlines()[-1][:300] if detail else "The capture failed."
    logger.warning("virtual patrol snapshot failed: %s", detail)
    return False, detail


async def _record_failure(db: AsyncSession, session_camera_id: str,
                          status: str, reason: str) -> None:
    await db.execute(text("""
        UPDATE virtual_patrol_session_cameras
           SET status = :st, snapshot_error = :err, snapshot_path = NULL
         WHERE id = CAST(:id AS uuid)
    """), {"st": status, "err": scrub(reason)[:500], "id": session_camera_id})
