"""Evidence snapshot storage — shared by all AI modules (plan §6).

STORAGE_BACKEND=local (default): writes to EVIDENCE_ROOT on the local filesystem.
STORAGE_BACKEND=s3: uploads to MinIO/S3; EVIDENCE_ROOT is ignored.

The storage_path returned is the S3 object key in both cases, using the same
convention — {tenant_id}/{yyyy}/{mm}/{dd}/{detection_id}.jpg — so the evidence
table requires no schema change when switching backends.
"""

import hashlib
import io
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import cv2
import numpy as np

STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
EVIDENCE_ROOT = os.environ.get("EVIDENCE_ROOT", "/data/evidence")

S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "http://minio:9000")
S3_BUCKET = os.environ.get("S3_BUCKET", "evidence")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "minioadmin")

_s3_client = None


def _get_s3():
    global _s3_client
    if _s3_client is None:
        import boto3

        _s3_client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT_URL,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
        )
    return _s3_client


def save_evidence_snapshot(
    frame: np.ndarray, tenant_id: UUID, detection_id: UUID, suffix: str = ""
) -> tuple[str, str]:
    """Returns (relative_storage_path, sha256_checksum).

    Path convention: {tenant_id}/{yyyy}/{mm}/{dd}/{detection_id}{suffix}.jpg.
    Always uses forward slashes (the path doubles as an S3 object key).

    `suffix` exists so one detection can store more than one image without
    the second overwriting the first — LPR saves both the full frame and a
    crop of the plate, and the filename is keyed on detection_id alone.
    Defaults to "" so every existing caller keeps its current path exactly.
    """
    now = datetime.now(timezone.utc)
    rel_path = (
        f"{tenant_id}/{now:%Y}/{now:%m}/{now:%d}/{detection_id}{suffix}.jpg"
    )

    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError("JPEG encode failed for evidence snapshot")
    raw_bytes = buf.tobytes()
    checksum = hashlib.sha256(raw_bytes).hexdigest()

    if STORAGE_BACKEND == "s3":
        _get_s3().put_object(
            Bucket=S3_BUCKET,
            Key=rel_path,
            Body=io.BytesIO(raw_bytes),
            ContentType="image/jpeg",
        )
    else:
        abs_dir = Path(EVIDENCE_ROOT) / str(tenant_id) / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"
        abs_dir.mkdir(parents=True, exist_ok=True)
        (abs_dir / f"{detection_id}{suffix}.jpg").write_bytes(raw_bytes)

    return rel_path, checksum
