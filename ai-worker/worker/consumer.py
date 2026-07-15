"""Generic Redis Streams consumer loop shared by all three AI modules (plan
§6). A message is only XACK'd after process_fn returns without raising, which
in every pipeline means its DB transaction already committed — so a worker
crash mid-job leaves the message in the consumer group's PEL, recovered via
XPENDING/XCLAIM on a later loop pass (by this or another worker), never
silently dropped.

Poison-message guard: if a message has been delivered MAX_DELIVERY_ATTEMPTS
times across all workers, it is XACK'd (discarded) rather than retried
forever, preventing a permanent-failure message from blocking the consumer
group indefinitely."""

import logging
import time
from uuid import uuid4

import redis

from shared.constants import FRAME_JOBS_STREAM, IDLE_CLAIM_MS
from shared.events import FrameJob
from worker.common.metrics import (
    consumer_group_lag,
    frames_consumed_total,
    frames_failed_total,
    frames_processed_total,
    processing_latency_seconds,
)

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 5


def ensure_group(r: redis.Redis, group: str) -> None:
    try:
        r.xgroup_create(name=FRAME_JOBS_STREAM, groupname=group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def claim_stale_messages(r: redis.Redis, group: str, consumer_name: str) -> list:
    """Reclaims messages idle longer than IDLE_CLAIM_MS under a dead consumer's
    name. Messages that have exceeded MAX_DELIVERY_ATTEMPTS are dead-lettered
    (XACK'd and discarded) instead of retried — prevents a poison message from
    blocking the consumer group permanently."""
    pending = r.xpending_range(FRAME_JOBS_STREAM, group, min="-", max="+", count=100)
    stale_ids = []
    dead_ids = []
    for p in pending:
        if p["time_since_delivered"] > IDLE_CLAIM_MS:
            if p["times_delivered"] >= MAX_DELIVERY_ATTEMPTS:
                dead_ids.append(p["message_id"])
            else:
                stale_ids.append(p["message_id"])
    if dead_ids:
        logger.error(
            "dead-lettering %d message(s) after %d delivery attempts: %s",
            len(dead_ids), MAX_DELIVERY_ATTEMPTS, dead_ids,
        )
        r.xack(FRAME_JOBS_STREAM, group, *dead_ids)
    if stale_ids:
        return r.xclaim(FRAME_JOBS_STREAM, group, consumer_name, min_idle_time=IDLE_CLAIM_MS, message_ids=stale_ids)
    return []


def update_lag_gauge(r: redis.Redis, group: str, module_type: str) -> None:
    """XINFO GROUPS' `lag` field directly (Redis 7+, confirmed — the compose
    redis:7-alpine image qualifies) rather than approximating via
    XPENDING+XLEN (plan §10)."""
    try:
        for info in r.xinfo_groups(FRAME_JOBS_STREAM):
            name = info.get("name")
            if isinstance(name, bytes):
                name = name.decode()
            if name == group and "lag" in info:
                consumer_group_lag.labels(module_type=module_type).set(info["lag"])
                return
    except redis.ResponseError:
        pass  # stream/group not created yet on the very first iteration


def run_consumer_loop(redis_client: redis.Redis, group: str, process_fn, module_type: str | None = None, max_iterations: int | None = None) -> None:
    """process_fn(job: FrameJob) -> None; raise to leave the message unacked.
    module_type labels the metrics below (defaults to the group name minus
    its "_workers" suffix if not given). max_iterations is test-only
    (None = run forever, the real entrypoint)."""
    module_type = module_type or group.removesuffix("_workers")
    consumer_name = f"worker-{uuid4().hex[:8]}"
    ensure_group(redis_client, group)
    # IDs that failed in this worker instance's lifetime: skip in the PEL
    # drain below so they can age out and hit the MAX_DELIVERY_ATTEMPTS guard
    # rather than spinning in a hot retry loop.
    failed_this_session: set = set()
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        # Transfer dead consumers' stale messages to our PEL, dead-letter
        # any that have exceeded MAX_DELIVERY_ATTEMPTS.
        claim_stale_messages(redis_client, group, consumer_name)
        update_lag_gauge(redis_client, group, module_type)
        # Drain our own PEL first (messages un-ACK'd from a previous crash of
        # this consumer), skipping IDs that already failed in this session so
        # we don't hot-loop on a permanent failure.  Only if the PEL is empty
        # (or all remaining entries are in failed_this_session) do we block
        # waiting for a new undelivered message.
        try:
            pel = redis_client.xreadgroup(group, consumer_name, {FRAME_JOBS_STREAM: "0"}, count=10)
            pel_messages = pel[0][1] if pel and pel[0][1] else []
            pending_to_process = [(mid, f) for mid, f in pel_messages if mid not in failed_this_session]
            if pending_to_process:
                resp = [(b"frame_jobs", pending_to_process[:1])]
            else:
                resp = redis_client.xreadgroup(group, consumer_name, {FRAME_JOBS_STREAM: ">"}, count=1, block=5000)
        except redis.ResponseError as e:
            if "NOGROUP" in str(e):
                # Redis restarted and lost the stream/group — recreate and retry
                logger.warning("Stream/group lost (Redis restart?); recreating: %s", e)
                ensure_group(redis_client, group)
                continue
            raise
        if not resp:
            continue
        for _stream_name, messages in resp:
            for msg_id, fields in messages:
                frames_consumed_total.labels(module_type=module_type).inc()
                start = time.perf_counter()
                try:
                    job = FrameJob.from_redis_fields(fields)
                    process_fn(job)
                except Exception:
                    frames_failed_total.labels(module_type=module_type).inc()
                    logger.exception("frame job %s failed; left unacked for retry", msg_id)
                    failed_this_session.add(msg_id)
                    continue
                finally:
                    processing_latency_seconds.labels(module_type=module_type).observe(time.perf_counter() - start)
                frames_processed_total.labels(module_type=module_type).inc()
                failed_this_session.discard(msg_id)
                redis_client.xack(FRAME_JOBS_STREAM, group, msg_id)
