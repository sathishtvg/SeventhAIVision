"""The drone runner process — its own worker, like the scheduler and ingestion.

Why a separate process rather than another job in scheduler_main: that loop wakes
once a minute, which is far too slow to fly (an abort must not wait a minute),
and it also escalates man-down alerts, a safety job that must never sit behind a
slow provider call. Here a stuck provider costs the runner one tick, and nothing
else in the platform notices.

Cadence, each overridable by environment:
  DRONE_RUNNER_TICK_SECONDS      2   commands and live flights
  DRONE_RUNNER_HEALTH_SECONDS   15   drone heartbeats, then the lost-link sweep
  DRONE_RUNNER_SCHEDULE_SECONDS 30   sessions the schedules owe
  DRONE_RUNNER_AI_SECONDS        3   flights' new AI detections into drone events

Everything it does is also a function in services/drone_runner.py that tests call
directly, with a fixed clock.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal

from redis.asyncio import Redis

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services import drone_runner

logger = logging.getLogger("drone_runner")

TICK_SECONDS = float(os.environ.get("DRONE_RUNNER_TICK_SECONDS", "2"))
HEALTH_SECONDS = float(os.environ.get("DRONE_RUNNER_HEALTH_SECONDS", "15"))
SCHEDULE_SECONDS = float(os.environ.get("DRONE_RUNNER_SCHEDULE_SECONDS", "30"))
AI_SECONDS = float(os.environ.get("DRONE_RUNNER_AI_SECONDS", "3"))


def _worth_logging(name: str, r: dict) -> bool:
    """Only when something happened: a quiet fleet should make a quiet log."""
    if name == "tick":
        # Not "sessions": every airborne flight ticks every two seconds, and its
        # changes are already announced and recorded on the session itself.
        return bool(r.get("commands") or any((r.get("edge") or {}).values()))
    if name == "health":
        return bool(r.get("recovered") or r.get("lost") or r.get("gateways_offline"))
    if name == "ai":
        return bool(r.get("accepted") or r.get("failed"))
    return bool(r.get("ready") or r.get("blocked") or r.get("missed"))


async def _guarded(name: str, coro) -> None:
    try:
        result = await coro
        if isinstance(result, dict) and _worth_logging(name, result):
            logger.info("%s: %s", name, result)
    except Exception:
        logger.exception("%s failed", name)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    pub = drone_runner.RedisPublisher(redis)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # not available on every platform
            pass

    logger.info("drone runner started: tick %.1fs, health %.0fs, schedule %.0fs, ai %.0fs",
                TICK_SECONDS, HEALTH_SECONDS, SCHEDULE_SECONDS, AI_SECONDS)
    last_health = last_schedule = last_ai = float("-inf")
    try:
        while not stop.is_set():
            now = loop.time()
            if now - last_health >= HEALTH_SECONDS:
                await _guarded("health", drone_runner.run_health_tick(AsyncSessionLocal, pub))
                last_health = now
            if now - last_schedule >= SCHEDULE_SECONDS:
                await _guarded("schedule", drone_runner.run_schedule_tick(AsyncSessionLocal, pub))
                last_schedule = now
            await _guarded("tick", drone_runner.run_tick(AsyncSessionLocal, pub))
            if now - last_ai >= AI_SECONDS:
                await _guarded("ai", drone_runner.run_ai_tick(AsyncSessionLocal, pub))
                last_ai = now
            try:
                await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        logger.info("drone runner stopping")
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
