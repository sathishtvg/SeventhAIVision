"""The footage worker: python -m worker.footage_main

Takes the videos uploaded to Footage Review one at a time, from the Redis
stream `footage_jobs`, and analyses each (worker.footage.pipeline). The stream
is a doorbell: the table is the truth. So, when it starts and every minute
after, the worker also asks the database which videos are waiting
(`footage_waiting()`) and takes those nobody rang for, and puts back in the
queue a video a stopped worker was holding.

It also answers the API's request to turn a typed description into a vector
(worker.footage.textserver), on port 8090 of the stack's own network.
"""
from __future__ import annotations

import logging
import os
import socket
import time

import redis

from worker.footage import db, model, pipeline, textserver

logger = logging.getLogger("footage")

STREAM = "footage_jobs"
GROUP = "footage-workers"
SWEEP_EVERY = int(os.environ.get("FOOTAGE_SWEEP_SECONDS", "60"))


def _group(client: redis.Redis) -> None:
    try:
        client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def sweep(conn, embedder) -> None:
    for tenant_id, video_id, status, _attempts in db.waiting(conn):
        if status == "PROCESSING":
            became = db.release_stale(conn, tenant_id, video_id)
            if became:
                logger.warning("footage %s: a stopped worker was holding it; now %s", video_id, became)
            if became != "QUEUED":
                continue
        pipeline.process(conn, embedder, tenant_id, video_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    embedder = model.Embedder()
    # Both halves loaded before the first search, which would otherwise wait for the text half to load.
    embedder.embed_text("ready")
    textserver.serve(embedder, int(os.environ.get("FOOTAGE_TEXT_PORT", "8090")))
    logger.info("footage worker ready: %s", model.VERSION)
    client = redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"), socket_timeout=None,
                            socket_connect_timeout=5)
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    conn = db.connect()
    last_sweep = 0.0
    while True:
        try:
            if conn.closed:
                conn = db.connect()
            if time.monotonic() - last_sweep >= SWEEP_EVERY:
                sweep(conn, embedder)
                last_sweep = time.monotonic()
            _group(client)
            answer = client.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=1, block=5000)
            for _stream, messages in answer or []:
                for message_id, fields in messages:
                    tenant_id = fields.get(b"tenant_id", b"").decode()
                    video_id = fields.get(b"video_id", b"").decode()
                    try:
                        if tenant_id and video_id:
                            outcome = pipeline.process(conn, embedder, tenant_id, video_id)
                            logger.info("footage %s: %s", video_id, outcome)
                    finally:
                        client.xack(STREAM, GROUP, message_id)
        except (redis.ConnectionError, redis.TimeoutError):
            logger.warning("redis unreachable; trying again in 5 s")
            time.sleep(5)
        except Exception:  # noqa: BLE001 — the worker keeps going; the table keeps what is waiting
            logger.exception("footage worker loop")
            time.sleep(5)


if __name__ == "__main__":
    main()
