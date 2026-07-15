import os
import uuid
from datetime import datetime, timezone

import numpy as np
import pytest
import redis.asyncio as redis

from app.ingestion_main import _encode_jpeg_b64
from shared.constants import FRAME_JOBS_STREAM
from shared.events import FrameJob

# Derive Redis URL from env (Docker: redis://redis:6379/0, host: redis://localhost:6379/0)
# Use DB 1 so the test stream doesn't interfere with app data on DB 0.
_redis_base = os.environ.get("REDIS_URL", "redis://localhost:6379/0").rsplit("/", 1)[0]
REDIS_URL = f"{_redis_base}/1"


@pytest.mark.asyncio
async def test_frame_encode_and_xadd_round_trip():
    """No real camera available — stands a synthetic frame (a plain numpy array,
    same shape cv2.VideoCapture.read() would return) in for one, and proves the
    rest of the real pipeline for real: JPEG encode -> base64 -> FrameJob ->
    XADD against the actually-running Redis -> XREAD back -> FrameJob round
    trip. This is the part of camera ingestion that doesn't need hardware to
    verify; the cv2.VideoCapture/RTSP connection itself is exercised by manual
    testing against a real or simulated RTSP source, not by this suite."""
    synthetic_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    synthetic_frame[:, :] = (60, 120, 200)  # arbitrary solid color, just needs to encode

    frame_b64, width, height = _encode_jpeg_b64(synthetic_frame)
    assert width == 640
    assert height == 480
    assert len(frame_b64) > 100

    tenant_id, camera_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job = FrameJob(
        job_id=job_id, tenant_id=tenant_id, camera_id=camera_id,
        frame_jpeg_b64=frame_b64, frame_width=width, frame_height=height,
        captured_at=datetime.now(timezone.utc), ai_modules_enabled=["lpr"],
    )

    r = redis.from_url(REDIS_URL)
    try:
        await r.delete(FRAME_JOBS_STREAM)  # isolate from anything else on this stream
        msg_id = await r.xadd(FRAME_JOBS_STREAM, job.to_redis_fields())
        assert msg_id

        entries = await r.xrange(FRAME_JOBS_STREAM, min=msg_id, max=msg_id)
        assert len(entries) == 1
        _, fields = entries[0]
        roundtripped = FrameJob.from_redis_fields(fields)
        assert roundtripped.job_id == job_id
        assert roundtripped.camera_id == camera_id
        assert roundtripped.ai_modules_enabled == ["lpr"]
        assert roundtripped.frame_jpeg_b64 == frame_b64
    finally:
        await r.delete(FRAME_JOBS_STREAM)
        await r.aclose()
