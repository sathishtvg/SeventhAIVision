"""Where a video's files are: the original, a frame a second, and a playable copy.

The installation's STORAGE_BACKEND decides, as it does for the API
(backend/app/services/footage.py): "local" is a directory under EVIDENCE_ROOT
shared with the API and the scheduler, "s3" a bucket. Paths are always
relative to that root and never come from a request.
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np

BACKEND = os.environ.get("STORAGE_BACKEND", "local")
ROOT = Path(os.environ.get("EVIDENCE_ROOT", "/data/evidence"))
BUCKET = os.environ.get("S3_BUCKET", "evidence")


def _client():
    import boto3

    return boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
                        aws_access_key_id=os.environ.get("S3_ACCESS_KEY") or None,
                        aws_secret_access_key=os.environ.get("S3_SECRET_KEY") or None)


def _local(path: str) -> Path:
    root = ROOT.resolve()
    full = (root / path).resolve()
    if root not in full.parents:
        raise ValueError(f"{path!r} is not under the storage root")
    return full


def video_dir(storage_path: str) -> str:
    """The directory the original sits in: footage/<tenant>/<video>."""
    return storage_path.rsplit("/", 1)[0]


def frame_path(storage_path: str, second: int) -> str:
    return f"{video_dir(storage_path)}/frames/{second:06d}.jpg"


def proxy_path(storage_path: str) -> str:
    return f"{video_dir(storage_path)}/playable.mp4"


def source(path: str) -> str:
    """What ffmpeg is given to read: the file on disk, or an address good for an hour."""
    if BACKEND == "s3":
        return _client().generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": path}, ExpiresIn=3600)
    return str(_local(path))


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    if BACKEND == "s3":
        body = _client().get_object(Bucket=BUCKET, Key=path)["Body"]
        for block in iter(lambda: body.read(1 << 20), b""):
            digest.update(block)
    else:
        with open(_local(path), "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
    return digest.hexdigest()


def put_frame(path: str, frame: np.ndarray, width: int = 320) -> None:
    """The frame kept for one second, at most `width` wide."""
    from PIL import Image

    picture = Image.fromarray(frame)
    if picture.width > width:
        picture = picture.resize((width, max(2, round(picture.height * width / picture.width))), Image.BILINEAR)
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=75)
    data = buffer.getvalue()
    if BACKEND == "s3":
        _client().put_object(Bucket=BUCKET, Key=path, Body=data, ContentType="image/jpeg")
        return
    target = _local(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


@contextmanager
def writing(path: str) -> Iterator[str]:
    """A local file to write to; kept at `path` when the block ends without an error."""
    if BACKEND == "s3":
        with tempfile.TemporaryDirectory() as scratch:
            local = os.path.join(scratch, "out.mp4")
            yield local
            _client().upload_file(local, BUCKET, path, ExtraArgs={"ContentType": "video/mp4"})
        return
    target = _local(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part.mp4")
    try:
        yield str(partial)
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()


def remove_frames(storage_path: str) -> None:
    """What an earlier analysis of the same video left, before it is analysed again."""
    prefix = f"{video_dir(storage_path)}/frames/"
    if BACKEND == "s3":
        client = _client()
        token = None
        while True:
            page = client.list_objects_v2(Bucket=BUCKET, Prefix=prefix, **({"ContinuationToken": token} if token else {}))
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                client.delete_objects(Bucket=BUCKET, Delete={"Objects": keys, "Quiet": True})
            if not page.get("IsTruncated"):
                return
            token = page.get("NextContinuationToken")
    folder = _local(prefix)
    if folder.exists():
        for f in folder.glob("*.jpg"):
            f.unlink()
