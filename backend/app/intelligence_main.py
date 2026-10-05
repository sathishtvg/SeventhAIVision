"""The intelligence runner process — its own worker, like the drone runner.

Why a separate process: everything here is extra. If it is slow, stopped or
broken, alerts still arrive, pushes still go out, incidents still open and the
live wall still plays, because none of that passes through here. Inside the API
or the scheduler it could hold one of them up; on its own it cannot.

It reads what the platform has already recorded and writes the `security_*`
tables. It takes no security action of any kind — see services/intel_runner.py.

Cadence, each overridable by environment:
  INTEL_RUNNER_TICK_SECONDS      3    read each source for each tenant with the feature on
  INTEL_RUNNER_MIN_GAP_SECONDS   0.5  the least time between two passes, however busy

It wakes early when a tenant's event channel announces a new alert or incident,
so an event is usually normalised within a moment of being raised. The channel
is only a nudge: the database is what is read, so a missed message costs a tick
and never an event, and if Redis is down the tick still runs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal

from redis.asyncio import Redis

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services import intel_runner

logger = logging.getLogger("intelligence_runner")

TICK_SECONDS = float(os.environ.get("INTEL_RUNNER_TICK_SECONDS", "3"))
#: A site in the middle of an alert storm nudges the runner constantly. It still
#: rests this long between passes, so a storm cannot turn it into a busy loop.
MIN_GAP_SECONDS = float(os.environ.get("INTEL_RUNNER_MIN_GAP_SECONDS", "0.5"))

#: The live events that mean "there is something new to read".
WAKE_ON = frozenset({"alert_created", "incident_created", "sos_triggered", "camera_status_changed"})
TENANT_EVENTS_PATTERN = "tenant_events:*"


async def _listen(redis, wake: asyncio.Event, stop: asyncio.Event) -> None:
    """Set `wake` when a tenant's channel announces something to read. Any
    trouble with Redis ends in a pause and another try; the tick does not wait
    for this."""
    while not stop.is_set():
        try:
            pubsub = redis.pubsub()
            await pubsub.psubscribe(TENANT_EVENTS_PATTERN)
            try:
                async for message in pubsub.listen():
                    if stop.is_set():
                        break
                    if message.get("type") != "pmessage":
                        continue
                    try:
                        event_type = json.loads(message["data"]).get("event_type")
                    except (ValueError, AttributeError, TypeError):
                        continue
                    if event_type in WAKE_ON:
                        wake.set()
            finally:
                await pubsub.aclose()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never worth stopping the runner
            logger.warning("event channel unavailable (%s); reading on the tick alone", type(exc).__name__)
            await asyncio.sleep(5)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    stop = asyncio.Event()
    wake = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # not available on every platform
            pass
    stop_waiter = asyncio.create_task(stop.wait())
    listener = asyncio.create_task(_listen(redis, wake, stop))

    logger.info("intelligence runner started: tick %.1fs", TICK_SECONDS)
    try:
        while not stop.is_set():
            wake.clear()
            ok, result = True, None
            try:
                result = await intel_runner.run_ingest_tick(AsyncSessionLocal)
                ok = not result["failed"]
                if result["events"] or result["failed"]:
                    logger.info("ingest: %s", result)
            except Exception:  # noqa: BLE001 — a failed pass costs this pass only
                ok = False
                logger.exception("ingest pass failed")
            try:
                await intel_runner.write_heartbeat(redis, ok, result)
            except Exception as exc:  # noqa: BLE001
                logger.warning("heartbeat not written: %s", type(exc).__name__)
            # Rest, then wait for the tick, a nudge, or the order to stop —
            # whichever is first.
            await asyncio.wait({stop_waiter}, timeout=MIN_GAP_SECONDS)
            if stop.is_set():
                break
            wake_waiter = asyncio.create_task(wake.wait())
            await asyncio.wait({stop_waiter, wake_waiter}, timeout=max(TICK_SECONDS - MIN_GAP_SECONDS, 0),
                               return_when=asyncio.FIRST_COMPLETED)
            wake_waiter.cancel()
    finally:
        logger.info("intelligence runner stopping")
        for task in (listener, stop_waiter):
            task.cancel()
        await asyncio.gather(listener, stop_waiter, return_exceptions=True)
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
