"""Footage Review: what a video may be, where its file is kept, and how it arrives.

Design: docs/plans/2026-10-20-footage-review-design.md, sections 1 and 2.

A VIDEO ARRIVES IN PARTS, IN ORDER. The person's browser says how big the file
is, and is given an id and a part size. It then sends the file one part at a
time, each at the offset the server says it has reached. A part sent at any
other offset is refused with the offset the server has, so an upload that
stopped — a dropped connection, a closed laptop — carries on from where it was.
Every part is exactly PART_SIZE bytes except the last, which is what is left.

ONE PATH FOR LOCAL DISK AND S3. The installation's STORAGE_BACKEND decides where
the file goes. On disk each part is written at its offset in one file under
EVIDENCE_ROOT. In S3 each part is one part of a multipart upload, numbered from
its offset; sending a part again replaces it. A browser never writes to S3
itself: agency networks often allow it to reach the platform and nothing else.

THE FILE IS NEVER RUN. It is only ever read by the footage worker, which opens
it with ffprobe before anything else and refuses what is not a video.

THE QUEUE IS A DOORBELL, THE TABLE IS THE TRUTH. A finished upload is marked
QUEUED and announced on the Redis stream `footage_jobs`. If the announcement is
lost, the video is still QUEUED, and the worker looks for QUEUED videos it has
not been told about when it starts and from time to time.

A VIDEO IS KEPT FOR RETENTION_DAYS from when its upload began, then removed with
everything worked out from it, unless an evidence hold of kind FOOTAGE is in
force on it (services/footage_retention.py). An upload nobody has added to for
ABANDONED_AFTER is removed by the same job.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from datetime import timedelta
from pathlib import Path, PurePosixPath

from app.core.config import settings

#: Every part but the last is exactly this long. S3 asks at least 5 MB of each.
PART_SIZE = 16 * 1024 * 1024
#: The largest file accepted. The worker refuses a video longer than MAX_SECONDS.
MAX_BYTES = int(os.environ.get("FOOTAGE_MAX_BYTES", str(4 * 1024 ** 3)))
MAX_SECONDS = int(os.environ.get("FOOTAGE_MAX_SECONDS", str(6 * 3600)))
RETENTION_DAYS = 30
RETENTION = timedelta(days=RETENTION_DAYS)
ABANDONED_AFTER = timedelta(days=1)

#: The kinds of file accepted, by extension, with the type each is stored as.
EXTENSIONS = {".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}

STATUSES = ("UPLOADING", "QUEUED", "PROCESSING", "READY", "FAILED")
STATUS_LABEL = {
    "UPLOADING": "Uploading",
    "QUEUED": "Waiting to be analysed",
    "PROCESSING": "Being analysed",
    "READY": "Ready",
    "FAILED": "Could not be analysed",
}
JOBS_STREAM = "footage_jobs"

TITLE_LIMIT = 200
PLACE_LIMIT = 200
FILENAME_LIMIT = 255


def extension_of(filename: str) -> str | None:
    """The accepted extension of a file name, lower case, or None."""
    ext = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    return ext if ext in EXTENSIONS else None


def storage_dir(tenant_id, video_id) -> str:
    """Where everything of one video is kept, relative to the storage root."""
    return f"footage/{uuid.UUID(str(tenant_id))}/{uuid.UUID(str(video_id))}"


def original_path(tenant_id, video_id, ext: str) -> str:
    assert ext in EXTENSIONS
    return f"{storage_dir(tenant_id, video_id)}/original{ext}"


def expected_part(size_bytes: int, offset: int, part_size: int = PART_SIZE) -> int:
    """How long the part at this offset must be."""
    return min(part_size, size_bytes - offset)


def _local(path: str) -> Path:
    root = Path(settings.EVIDENCE_ROOT).resolve()
    full = (root / path).resolve()
    if root not in full.parents:
        raise ValueError(f"{path!r} is not under the storage root")
    return full


def local_file(path: str) -> Path:
    """The file on disk for a path the platform wrote itself, never one from a request."""
    return _local(path)


def frame_path(storage_path: str, second: int) -> str:
    """Where the footage worker keeps the frame of one second (ai-worker/worker/footage/store.py)."""
    return f"{storage_path.rsplit('/', 1)[0]}/frames/{second:06d}.jpg"


def _s3() -> bool:
    return settings.STORAGE_BACKEND == "s3"


# ── writing ───────────────────────────────────────────────────────────────────

async def begin(path: str, content_type: str) -> str | None:
    """Make room for a new file. Returns the S3 upload's id, or None on disk."""
    if _s3():
        from app.core.object_store import _get_client

        answer = await asyncio.to_thread(
            _get_client().create_multipart_upload, Bucket=settings.S3_BUCKET, Key=path, ContentType=content_type)
        return answer["UploadId"]

    def _make() -> None:
        target = _local(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    await asyncio.to_thread(_make)
    return None


async def write_part(path: str, offset: int, data: bytes, upload_id: str | None,
                     part_size: int = PART_SIZE) -> dict | None:
    """Write one part at its offset. Returns the S3 part as the upload's completion
    needs it, or None on disk. On disk anything past the part is cut away: a
    part that was half-written before a crash is not left behind it."""
    if _s3():
        from app.core.object_store import _get_client

        number = offset // part_size + 1
        answer = await asyncio.to_thread(
            _get_client().upload_part, Bucket=settings.S3_BUCKET, Key=path, UploadId=upload_id,
            PartNumber=number, Body=data)
        return {"PartNumber": number, "ETag": answer["ETag"]}

    def _write() -> None:
        with open(_local(path), "r+b") as f:
            f.seek(offset)
            f.write(data)
            f.truncate()
    await asyncio.to_thread(_write)
    return None


async def finish(path: str, upload_id: str | None, parts: list[dict]) -> None:
    """Join the parts into the one file."""
    if _s3():
        from app.core.object_store import _get_client

        ordered = sorted(parts, key=lambda p: p["PartNumber"])
        await asyncio.to_thread(
            _get_client().complete_multipart_upload, Bucket=settings.S3_BUCKET, Key=path, UploadId=upload_id,
            MultipartUpload={"Parts": [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in ordered]})


def with_part(parts: list[dict], part: dict) -> list[dict]:
    """The parts so far, with this one in place of any sent before at its number."""
    return [p for p in parts if p["PartNumber"] != part["PartNumber"]] + [part]


# ── removing ──────────────────────────────────────────────────────────────────

def remove_everything(directory: str, path: str | None = None, upload_id: str | None = None) -> None:
    """Remove a video's file and everything kept beside it. Synchronous: the
    scheduler and the API call it in a thread. An S3 upload not yet finished is
    abandoned so that S3 keeps none of its parts."""
    if _s3():
        from app.core.object_store import _get_client

        client = _get_client()
        if upload_id and path:
            try:
                client.abort_multipart_upload(Bucket=settings.S3_BUCKET, Key=path, UploadId=upload_id)
            except Exception:  # noqa: BLE001 — already finished or already abandoned
                pass
        prefix = directory.rstrip("/") + "/"
        token = None
        while True:
            page = client.list_objects_v2(Bucket=settings.S3_BUCKET, Prefix=prefix,
                                          **({"ContinuationToken": token} if token else {}))
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                client.delete_objects(Bucket=settings.S3_BUCKET, Delete={"Objects": keys, "Quiet": True})
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        return
    target = _local(directory)
    if target.exists():
        shutil.rmtree(target)


# ── the doorbell ──────────────────────────────────────────────────────────────

async def announce(redis, tenant_id, video_id) -> bool:
    """Tell the footage worker a video is waiting. False when it could not be
    told: the video stays QUEUED and the worker finds it anyway."""
    if redis is None:
        return False
    try:
        await redis.xadd(JOBS_STREAM, {"tenant_id": str(tenant_id), "video_id": str(video_id)},
                         maxlen=10_000, approximate=True)
        return True
    except Exception:  # noqa: BLE001 — the table is the truth
        return False
