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
  DRONE_RUNNER_REPORT_SECONDS   60   finished flights' reports, and the report emails

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
from app.services import drone_platform_health, drone_runner

logger = logging.getLogger("drone_runner")

TICK_SECONDS = float(os.environ.get("DRONE_RUNNER_TICK_SECONDS", "2"))
HEALTH_SECONDS = float(os.environ.get("DRONE_RUNNER_HEALTH_SECONDS", "15"))
SCHEDULE_SECONDS = float(os.environ.get("DRONE_RUNNER_SCHEDULE_SECONDS", "30"))
AI_SECONDS = float(os.environ.get("DRONE_RUNNER_AI_SECONDS", "3"))
REPORT_SECONDS = float(os.environ.get("DRONE_RUNNER_REPORT_SECONDS", "60"))


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
    if name == "reports":
        return any(r.values())
    return bool(r.get("ready") or r.get("blocked") or r.get("missed"))


async def _guarded(name: str, coro) -> bool:
    """Run one job; a failure is logged and costs this pass only. Says whether
    it ran clean, which is what the heartbeat reports."""
    try:
        result = await coro
        if isinstance(result, dict) and _worth_logging(name, result):
            logger.info("%s: %s", name, result)
        return True
    except Exception:
        logger.exception("%s failed", name)
        return False


async def _beat(redis, tick_ok: bool) -> None:
    """Tell the platform console the runner is alive. Never worth a flight: a
    Redis that will not take the key is logged once a pass and nothing more."""
    try:
        await drone_platform_health.write_heartbeat(redis, tick_ok)
    except Exception as exc:
        logger.warning("drone runner heartbeat not written: %s", type(exc).__name__)


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

    logger.info("drone runner started: tick %.1fs, health %.0fs, schedule %.0fs, ai %.0fs, reports %.0fs",
                TICK_SECONDS, HEALTH_SECONDS, SCHEDULE_SECONDS, AI_SECONDS, REPORT_SECONDS)
    last_health = last_schedule = last_ai = last_reports = float("-inf")
    report_task: asyncio.Task | None = None
    try:
        while not stop.is_set():
            now = loop.time()
            if now - last_health >= HEALTH_SECONDS:
                await _guarded("health", drone_runner.run_health_tick(AsyncSessionLocal, pub))
                last_health = now
            if now - last_schedule >= SCHEDULE_SECONDS:
                await _guarded("schedule", drone_runner.run_schedule_tick(AsyncSessionLocal, pub))
                last_schedule = now
            tick_ok = await _guarded("tick", drone_runner.run_tick(AsyncSessionLocal, pub))
            await _beat(redis, tick_ok)
            if now - last_ai >= AI_SECONDS:
                await _guarded("ai", drone_runner.run_ai_tick(AsyncSessionLocal, pub))
                last_ai = now
            # Beside the loop, not in it: building a PDF or waiting on a mail
            # server must never sit in front of a flight command. One at a time —
            # a slow run is not joined by a second.
            if now - last_reports >= REPORT_SECONDS and (report_task is None or report_task.done()):
                report_task = asyncio.create_task(
                    _guarded("reports", drone_runner.run_report_tick(AsyncSessionLocal)))
                last_reports = now
            try:
                await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        logger.info("drone runner stopping")
        if report_task is not None and not report_task.done():
            # A row it had claimed stays PROCESSING and is taken again later.
            report_task.cancel()
            await asyncio.gather(report_task, return_exceptions=True)
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
