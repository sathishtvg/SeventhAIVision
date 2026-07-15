import os
from datetime import datetime, timezone
from uuid import uuid4

import redis

from shared.events import FrameJob
from worker.consumer import claim_stale_messages, ensure_group, run_consumer_loop

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://redis:6379/1")
STREAM = "frame_jobs"  # shared.constants.FRAME_JOBS_STREAM


def _push_job(r: redis.Redis) -> str:
    job = FrameJob(
        job_id=uuid4(), tenant_id=uuid4(), camera_id=uuid4(), frame_jpeg_b64="x",
        frame_width=1, frame_height=1, captured_at=datetime.now(timezone.utc), ai_modules_enabled=["lpr"],
    )
    return r.xadd(STREAM, job.to_redis_fields())


def _fresh_redis() -> redis.Redis:
    r = redis.from_url(REDIS_URL)
    r.delete(STREAM)
    for group in ("test_group_a", "test_group_b"):
        try:
            r.xgroup_destroy(STREAM, group)
        except redis.ResponseError:
            pass
    return r


def test_message_unacked_on_processing_exception():
    r = _fresh_redis()
    try:
        _push_job(r)

        def failing_process(job):
            raise RuntimeError("simulated processing failure")

        run_consumer_loop(r, "test_group_a", failing_process, max_iterations=1)

        pending = r.xpending(STREAM, "test_group_a")
        assert pending["pending"] == 1
    finally:
        r.delete(STREAM)


def test_stale_message_reclaimed():
    r = _fresh_redis()
    try:
        msg_id = _push_job(r)
        ensure_group(r, "test_group_a")
        # First consumer reads but never acks (simulates a crash mid-job).
        r.xreadgroup("test_group_a", "dead-consumer", {STREAM: ">"}, count=1)
        pending_before = r.xpending_range(STREAM, "test_group_a", min="-", max="+", count=10)
        assert len(pending_before) == 1
        assert pending_before[0]["consumer"] == b"dead-consumer"

        # Force-claim immediately (idle threshold of 0ms) under a new consumer name,
        # standing in for "IDLE_CLAIM_MS has elapsed" without a real 30s sleep.
        claimed = r.xclaim(STREAM, "test_group_a", "replacement-consumer", min_idle_time=0, message_ids=[msg_id])
        assert len(claimed) == 1

        pending_after = r.xpending_range(STREAM, "test_group_a", min="-", max="+", count=10)
        assert pending_after[0]["consumer"] == b"replacement-consumer"
    finally:
        r.delete(STREAM)


def test_each_module_group_independent_position():
    r = _fresh_redis()
    try:
        _push_job(r)

        processed_by_a = []
        processed_by_b = []
        run_consumer_loop(r, "test_group_a", lambda job: processed_by_a.append(job.job_id), max_iterations=1)
        run_consumer_loop(r, "test_group_b", lambda job: processed_by_b.append(job.job_id), max_iterations=1)

        # Both groups independently saw and acked the same single message —
        # one group's progress doesn't consume it for the other (this is what
        # lets lpr_workers/face_workers/intrusion_workers all read frame_jobs
        # independently, plan §6).
        assert len(processed_by_a) == 1
        assert len(processed_by_b) == 1
        assert processed_by_a == processed_by_b

        assert r.xpending(STREAM, "test_group_a")["pending"] == 0
        assert r.xpending(STREAM, "test_group_b")["pending"] == 0
    finally:
        r.delete(STREAM)
