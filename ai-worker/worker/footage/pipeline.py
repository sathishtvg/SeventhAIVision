"""One video, from the uploaded file to frames that can be searched.

    1. claim it                       QUEUED -> PROCESSING
    2. read what it is                length, size, frame rate, codec      progress  2
    3. its checksum                   SHA-256 of the original               progress  5
    4. one frame a second             a thumbnail and a SigLIP 2 vector     progress  5 -> 80
    5. a copy a browser can play      only when the original is not one     progress 80 -> 100
    6. READY

A file that is not a video, or is longer than may be analysed, is FAILED with
a reason the person can act on. Anything else that goes wrong puts the video
back in the queue, up to three tries.

Nothing here raises an alert, an incident or a notification.
"""
from __future__ import annotations

import logging
import os

from worker.footage import db, model, store, video

logger = logging.getLogger(__name__)

MAX_SECONDS = int(os.environ.get("FOOTAGE_MAX_SECONDS", str(6 * 3600)))
BATCH = int(os.environ.get("FOOTAGE_BATCH", "16"))


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} hours" if seconds >= 3600 else f"{seconds / 60:.0f} minutes"


def process(conn, embedder: "model.Embedder", tenant_id, video_id) -> str:
    """What became of the video: READY, FAILED, QUEUED (to be tried again) or SKIPPED (not waiting)."""
    v = db.claim(conn, tenant_id, video_id)
    if v is None:
        return "SKIPPED"
    path = v["storage_path"]
    try:
        src = store.source(path)
        info = video.probe(src)
        if info.duration > MAX_SECONDS:
            raise video.NotAVideo(f"This video is {_hours(info.duration)} long. At most {_hours(MAX_SECONDS)} "
                                  "can be analysed.")
        db.update(conn, tenant_id, video_id, duration_seconds=round(info.duration, 3), width=info.width,
                  height=info.height, frame_rate=round(info.frame_rate, 3) if info.frame_rate else None,
                  codec=info.codec[:40], progress=2)
        db.update(conn, tenant_id, video_id, sha256=store.sha256(path), progress=5)

        store.remove_frames(path)
        db.clear_frames(conn, tenant_id, video_id)
        expected = max(1, int(info.duration))
        written, shown, batch = 0, 5, []

        def flush() -> None:
            nonlocal written, shown, batch
            if not batch:
                return
            vectors = embedder.embed_images([frame for _, frame in batch])
            rows = []
            for (second, frame), vector in zip(batch, vectors):
                thumb = store.frame_path(path, second)
                store.put_frame(thumb, frame)
                rows.append((second, thumb, vector))
            db.add_frames(conn, tenant_id, video_id, rows, model.VERSION)
            written += len(batch)
            batch = []
            now = 5 + int(75 * min(1.0, written / expected))
            if now > shown:
                db.update(conn, tenant_id, video_id, progress=now)
                shown = now

        for second, frame in video.frames_per_second(src, info):
            batch.append((second, frame))
            if len(batch) >= BATCH:
                flush()
        flush()
        if written == 0:
            raise video.NotAVideo("No picture could be read from this file.")

        playable, copy = "ORIGINAL", None
        if not info.playable:
            copy = store.proxy_path(path)
            try:
                with store.writing(copy) as local:
                    video.proxy(src, local, timeout=max(900, int(info.duration * 3)))
                playable = "PROXY"
            except Exception:  # noqa: BLE001 — search works without it; the page says it cannot be played
                logger.exception("footage %s: no playable copy", video_id)
                playable, copy = "NONE", None
        db.finish(conn, tenant_id, video_id, frames=written, model_version=model.VERSION, playable=playable,
                  proxy_path=copy)
        logger.info("footage %s: %d frames, %s", video_id, written, playable)
        return "READY"
    except video.NotAVideo as exc:
        db.fail(conn, tenant_id, video_id, str(exc))
        return "FAILED"
    except Exception:  # noqa: BLE001 — not the file's fault: tried again, up to three times
        logger.exception("footage %s: analysis stopped", video_id)
        db.fail(conn, tenant_id, video_id, "The analysis stopped with an error three times. Start it again.",
                again=True)
        return "QUEUED" if v["attempts"] < db.MOST_ATTEMPTS else "FAILED"
