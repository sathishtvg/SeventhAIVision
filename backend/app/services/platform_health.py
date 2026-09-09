"""Is the platform itself working?

§4 and §18 want service, worker, storage and database health on the platform
owner's console. This measures rather than reports: every figure below comes
from asking the thing itself, and anything that cannot be asked is returned as
`unknown` rather than as a reassuring green.

That distinction is the whole design. A dashboard that shows green because a
check failed to run is worse than no dashboard, because it is trusted. So each
probe returns one of

    ok        measured, and within its threshold
    degraded  measured, and outside it
    down      asked, and it refused or did not answer
    unknown   could not be asked at all

and the overall status is the worst of them, with `unknown` never counted as
healthy.

WHAT IS NOT HERE. A customer's cameras. A site going dark is that company's
emergency and their operators are already looking at it; the vendor's concern
is the recording supervisor being stuck for everyone, which is a different
question and the one asked below.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time

from sqlalchemy import text

#: The Redis stream every AI worker consumes from, one consumer group per
#: module. Imported by value rather than from shared.constants so the API does
#: not take a dependency on the worker package for a health check.
FRAME_JOBS_STREAM = "frame_jobs"

#: Past this many unacknowledged frame jobs a consumer group is falling behind
#: rather than merely busy. Generous: a burst of motion across a hundred
#: cameras is normal and clears itself.
QUEUE_BACKLOG_DEGRADED = 500

#: A disk this full is a warning, not yet an outage — recordings roll over and
#: the purge runs hourly, so there is time to act if anyone is told.
DISK_DEGRADED_PERCENT = 85
DISK_CRITICAL_PERCENT = 95

#: A database or cache answering slower than this is degraded even if it
#: answers. Everything else waits on these two.
SLOW_MS = 500

_ORDER = {"ok": 0, "unknown": 1, "degraded": 2, "down": 3}


def worst(statuses: list[str]) -> str:
    """The overall verdict. `unknown` outranks `ok` deliberately: a check that
    did not run is not evidence that a thing is working."""
    return max(statuses, key=lambda s: _ORDER.get(s, 1)) if statuses else "unknown"


async def check_database(session) -> dict:
    started = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "service": "database",
            "status": "degraded" if ms > SLOW_MS else "ok",
            "detail": f"responded in {ms} ms",
            "latency_ms": ms,
        }
    except Exception as exc:
        return {"service": "database", "status": "down", "detail": str(exc)[:200]}


async def check_redis() -> dict:
    """Redis carries the frame-job stream and the realtime fan-out, so it being
    slow is not a cache problem — it is every live view and every AI job."""
    from redis.asyncio import Redis

    url = os.environ.get("REDIS_URL")
    if not url:
        return {"service": "redis", "status": "unknown",
                "detail": "REDIS_URL is not configured"}
    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    started = time.perf_counter()
    try:
        await client.ping()
        ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "service": "redis",
            "status": "degraded" if ms > SLOW_MS else "ok",
            "detail": f"responded in {ms} ms",
            "latency_ms": ms,
        }
    except Exception as exc:
        return {"service": "redis", "status": "down", "detail": str(exc)[:200]}
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


async def check_ai_workers() -> list[dict]:
    """One row per AI module, from the consumer group it reads.

    XINFO GROUPS is the honest source: it knows how many workers are attached
    and how far behind they are. A group with no consumers is a module that is
    licensed and silently not running, which is exactly the failure a customer
    reports as "detection stopped working" three days later.
    """
    from redis.asyncio import Redis

    url = os.environ.get("REDIS_URL")
    if not url:
        return [{"service": "ai-workers", "status": "unknown",
                 "detail": "REDIS_URL is not configured"}]

    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2,
                            decode_responses=True)
    try:
        groups = await client.xinfo_groups(FRAME_JOBS_STREAM)
    except Exception as exc:
        # A missing stream is not a failure: nothing has published a frame job
        # yet, which is the normal state of a fresh installation.
        detail = str(exc)
        if "no such key" in detail.lower():
            return [{"service": "ai-workers", "status": "unknown",
                     "detail": "no frame jobs have been queued yet"}]
        return [{"service": "ai-workers", "status": "down", "detail": detail[:200]}]
    finally:
        try:
            await client.aclose()
        except Exception:
            pass

    if not groups:
        return [{"service": "ai-workers", "status": "unknown",
                 "detail": "no consumer groups registered"}]

    out = []
    for g in groups:
        consumers = int(g.get("consumers", 0) or 0)
        pending = int(g.get("pending", 0) or 0)
        lag = g.get("lag")
        if consumers == 0:
            status = "down"
            detail = "no worker attached"
        elif pending > QUEUE_BACKLOG_DEGRADED:
            status = "degraded"
            detail = f"{pending} jobs unacknowledged"
        else:
            status = "ok"
            detail = f"{consumers} worker(s), {pending} in flight"
        out.append({
            "service": f"ai-worker:{g.get('name')}",
            "status": status, "detail": detail,
            "consumers": consumers, "pending": pending,
            "lag": int(lag) if lag is not None else None,
        })
    return out


def check_storage() -> list[dict]:
    """The volumes that fill up. Recordings first, because they are the ones
    that grow whether anyone is watching or not."""
    out = []
    for name, path in (
        ("recordings", os.environ.get("RECORDINGS_ROOT", "/data/recordings")),
        ("evidence", os.environ.get("EVIDENCE_ROOT", "/data/evidence")),
    ):
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            out.append({"service": f"storage:{name}", "status": "unknown",
                        "detail": str(exc)[:200]})
            continue
        percent = round(usage.used / usage.total * 100, 1) if usage.total else 0
        status = ("down" if percent >= DISK_CRITICAL_PERCENT
                  else "degraded" if percent >= DISK_DEGRADED_PERCENT else "ok")
        out.append({
            "service": f"storage:{name}", "status": status,
            "detail": f"{percent}% used, {usage.free // (1024 ** 3)} GB free",
            "used_percent": percent,
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "total_gb": round(usage.total / (1024 ** 3), 1),
        })
    return out


async def check_recording(session) -> dict:
    """Not "is a camera up" — that is the customer's problem and their
    operators are already looking at it. This asks whether the supervisor is
    keeping up across everybody, which is the vendor's.
    """
    # Through the SECURITY DEFINER function (migration 0108), not the tables.
    # streams and cameras are RLS'd and the console runs on the zero-UUID
    # sentinel tenant, so counting them directly returned zero and reported
    # "no streams are set to record" on an installation recording three — a
    # green light produced by a query that could not see anything.
    try:
        row = (await session.execute(
            text("SELECT * FROM platform_recording_health()")
        )).mappings().first()
    except Exception as exc:
        return {"service": "recording", "status": "unknown", "detail": str(exc)[:200]}

    expected, live = int(row["expected"]), int(row["live"])
    if expected == 0:
        return {"service": "recording", "status": "ok",
                "detail": "no streams are set to record continuously"}
    if live == 0:
        status = "down"
    elif live < expected:
        status = "degraded"
    else:
        status = "ok"
    return {"service": "recording", "status": status,
            "detail": f"{live} of {expected} streams recording",
            "expected": expected, "live": live}


async def collect(session) -> dict:
    """Every probe, and the worst of them as the headline.

    Run concurrently because they are all waiting on something external and a
    console that takes four seconds to say "everything is fine" gets left
    closed.
    """
    db, redis_status, workers, recording = await asyncio.gather(
        check_database(session),
        check_redis(),
        check_ai_workers(),
        check_recording(session),
        return_exceptions=False,
    )
    services = [db, redis_status, *workers, *check_storage(), recording]
    return {
        "status": worst([s["status"] for s in services]),
        "services": services,
        "critical": sum(1 for s in services if s["status"] == "down"),
        "degraded": sum(1 for s in services if s["status"] == "degraded"),
        "unknown": sum(1 for s in services if s["status"] == "unknown"),
    }
