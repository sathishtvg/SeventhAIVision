"""HLS live streaming (Gap 90) — scalable alternative to the MJPEG proxy.

MJPEG re-encodes every frame as a JPEG and pushes it over one long-lived HTTP
response per viewer; 16 cells over a WAN saturate the uplink. HLS instead has
ffmpeg *remux* the camera's existing H.264 RTSP stream into short MPEG-TS
segments (`-c:v copy`, no re-encode → low CPU), which the browser fetches as
cacheable files and buffers itself. One ffmpeg process serves any number of
viewers of the same stream.

Lifecycle: ffmpeg is started lazily on the first playlist request and stopped
by an idle reaper once no segment/playlist has been requested for
HLS_IDLE_SECONDS — so nothing runs while nobody is watching.

Security: segment filenames are strictly validated and resolved inside the
per-stream directory; path traversal is impossible.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time

logger = logging.getLogger(__name__)

HLS_ROOT = os.environ.get("HLS_ROOT", "/tmp/hls")
HLS_IDLE_SECONDS = int(os.environ.get("HLS_IDLE_SECONDS", "30"))
HLS_REAP_INTERVAL = int(os.environ.get("HLS_REAP_INTERVAL_SECONDS", "5"))
HLS_SEGMENT_SECONDS = int(os.environ.get("HLS_SEGMENT_SECONDS", "2"))
PLAYLIST_NAME = "index.m3u8"

# Segment/playlist names ffmpeg emits + the client requests back. No slashes,
# no dots-only, no traversal — this is the whole path-safety guarantee.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def is_safe_segment_name(name: str) -> bool:
    """Pure: True only for a plain segment/playlist filename (no traversal)."""
    if not _SAFE_NAME_RE.match(name):
        return False
    if name.startswith(".") or "/" in name or "\\" in name or ".." in name:
        return False
    return name.endswith(".ts") or name.endswith(".m3u8") or name.endswith(".m4s")


class _Session:
    __slots__ = ("stream_id", "proc", "dir", "last_access", "starting")

    def __init__(self, stream_id: str, out_dir: str):
        self.stream_id = stream_id
        self.dir = out_dir
        self.proc: asyncio.subprocess.Process | None = None
        self.last_access = time.monotonic()
        self.starting = asyncio.Lock()


_sessions: dict[str, _Session] = {}
_reaper_task: asyncio.Task | None = None


def _ffmpeg_cmd(rtsp_url: str, out_dir: str) -> list[str]:
    return [
        "ffmpeg", "-nostdin", "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-an",                      # drop audio — surveillance
        "-c:v", "copy",             # remux, no re-encode (the scalability win)
        "-f", "hls",
        "-hls_time", str(HLS_SEGMENT_SECONDS),
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+append_list+omit_endlist",
        "-hls_segment_filename", os.path.join(out_dir, "seg_%05d.ts"),
        os.path.join(out_dir, PLAYLIST_NAME),
    ]


def _touch(session: _Session) -> None:
    session.last_access = time.monotonic()


def segment_file_path(stream_id: str, name: str) -> str | None:
    """Resolve a client-requested segment/playlist to an absolute path INSIDE
    the stream's directory, or None if the name is unsafe / escapes the dir."""
    if not is_safe_segment_name(name):
        return None
    base = os.path.realpath(os.path.join(HLS_ROOT, stream_id))
    full = os.path.realpath(os.path.join(base, name))
    if full != base and not full.startswith(base + os.sep):
        return None
    # Touch the session so the reaper keeps ffmpeg alive while segments flow
    s = _sessions.get(stream_id)
    if s is not None:
        _touch(s)
    return full


async def ensure_session(stream_id: str, rtsp_url: str, wait_for_playlist: float = 8.0) -> str | None:
    """Start (or reuse) the ffmpeg HLS session for a stream and return the
    playlist path once it exists. None if ffmpeg never produced a playlist
    within `wait_for_playlist` seconds (bad URL / unsupported codec)."""
    _ensure_reaper()
    out_dir = os.path.join(HLS_ROOT, stream_id)
    session = _sessions.get(stream_id)
    if session is None:
        session = _Session(stream_id, out_dir)
        _sessions[stream_id] = session

    async with session.starting:
        _touch(session)
        if session.proc is None or session.proc.returncode is not None:
            os.makedirs(out_dir, exist_ok=True)
            try:
                session.proc = await asyncio.create_subprocess_exec(
                    *_ffmpeg_cmd(rtsp_url, out_dir),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                logger.info("hls: started ffmpeg stream=%s pid=%s", stream_id, session.proc.pid)
            except Exception as exc:
                logger.error("hls: failed to start ffmpeg stream=%s: %s", stream_id, exc)
                return None

    playlist = os.path.join(out_dir, PLAYLIST_NAME)
    deadline = time.monotonic() + wait_for_playlist
    while time.monotonic() < deadline:
        if os.path.exists(playlist):
            _touch(session)
            return playlist
        if session.proc is not None and session.proc.returncode is not None:
            return None  # ffmpeg exited early
        await asyncio.sleep(0.25)
    return None


async def _stop_session(session: _Session) -> None:
    proc = session.proc
    if proc is not None and proc.returncode is None:
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
        except ProcessLookupError:
            pass
    shutil.rmtree(session.dir, ignore_errors=True)
    logger.info("hls: stopped ffmpeg stream=%s", session.stream_id)


async def _reaper_loop() -> None:
    while True:
        try:
            await asyncio.sleep(HLS_REAP_INTERVAL)
            now = time.monotonic()
            idle = [s for s in _sessions.values() if now - s.last_access > HLS_IDLE_SECONDS]
            for session in idle:
                _sessions.pop(session.stream_id, None)
                await _stop_session(session)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("hls reaper error: %s", exc)


def _ensure_reaper() -> None:
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(_reaper_loop())


async def stop_all_hls_sessions() -> None:
    """Called from the app lifespan shutdown so no ffmpeg process is orphaned."""
    global _reaper_task
    if _reaper_task is not None:
        _reaper_task.cancel()
        _reaper_task = None
    for session in list(_sessions.values()):
        _sessions.pop(session.stream_id, None)
        await _stop_session(session)
