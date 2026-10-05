"""What the intelligence runner does on each pass, as functions tests can call.

THE RUNNER READS AND RECORDS. IT DOES NOT ACT. Nothing in this module, or in
anything it imports, can dispatch a guard, open or change an incident,
acknowledge an alert, command a drone or operate a door. Those belong to an API
request made by a signed-in person with the permission for it. This is kept by
what the module imports — only the `intel_*` services and the database — and a
test fails if that ever widens (tests/test_intel_events.py).

ONE TENANT AT A TIME, UNDER THAT TENANT. The runner connects as the application
role, which sees no rows until a tenant is set, and each tenant's work is done
in its own sessions. A tenant whose pass fails costs that tenant one pass.

ONLY FOR TENANTS THAT ASKED. `security_intel_tenants()` lists the tenants whose
administrator has switched `intel.enabled` on. For everyone else the runner
does nothing at all and their data is not read.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import text

from app.services import intel_correlation as correlation
from app.services import intel_events as events
from app.services import intel_risk as risk

logger = logging.getLogger("intelligence_runner")

HEARTBEAT_KEY = "intel_runner:heartbeat"
#: Twenty ticks. The console calls the runner down when the key has gone.
HEARTBEAT_TTL_SECONDS = 60


class RedisPublisher:
    """The existing realtime channel, in the existing envelope. The API's
    listener forwards every event type on it to the tenant's own clients."""

    def __init__(self, redis):
        self.redis = redis

    async def publish(self, tenant_id: str, event_type: str, payload: dict) -> None:
        await self.redis.publish(f"tenant_events:{tenant_id}", json.dumps({
            "event_type": event_type, "tenant_id": str(tenant_id), "payload": payload,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }))


class ListPublisher:
    """Collects instead of publishing — for tests and dry runs."""

    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    async def publish(self, tenant_id: str, event_type: str, payload: dict) -> None:
        self.events.append((str(tenant_id), event_type, payload))


async def tenants(factory) -> list[str]:
    """The tenants that have the feature switched on."""
    async with factory() as db:
        rows = (await db.execute(text("SELECT tenant_id FROM security_intel_tenants()"))).scalars().all()
    return [str(r) for r in rows]


async def run_ingest_tick(factory, now: datetime | None = None, batch: int = events.BATCH) -> dict:
    """Normalise what is new, for every tenant that has the feature on.

    Returns {"tenants": n, "events": new rows, "failed": sources that could not
    be read, "by_source": {source: new rows}}."""
    out = {"tenants": 0, "events": 0, "failed": 0, "by_source": {}}
    for tenant_id in await tenants(factory):
        out["tenants"] += 1
        try:
            counts = await events.ingest_tenant(factory, tenant_id, now=now, batch=batch)
        except Exception:  # noqa: BLE001 — this tenant's pass, not everyone's
            logger.exception("ingest failed for tenant %s", tenant_id)
            out["failed"] += 1
            continue
        for source, n in counts.items():
            if n < 0:
                out["failed"] += 1
                logger.warning("tenant %s: source %s could not be read", tenant_id, source)
            elif n:
                out["events"] += n
                out["by_source"][source] = out["by_source"].get(source, 0) + n
    return out


def heartbeat_value(ok: bool, result: dict | None, now: datetime | None = None) -> str:
    """What the runner tells the platform about itself: when, whether the last
    pass ran clean, and how much it did. Counts only — never a tenant's data."""
    now = now or datetime.now(timezone.utc)
    result = result or {}
    return json.dumps({
        "at": now.isoformat(),
        "ok": bool(ok),
        "tenants": int(result.get("tenants", 0)),
        "events": int(result.get("events", 0)),
        "failed": int(result.get("failed", 0)),
    })


async def write_heartbeat(redis, ok: bool, result: dict | None = None) -> None:
    await redis.set(HEARTBEAT_KEY, heartbeat_value(ok, result), ex=HEARTBEAT_TTL_SECONDS)


async def read_heartbeat(redis) -> dict | None:
    """The last heartbeat, or None when there is none — the runner is stopped,
    or has been for longer than the key lives."""
    raw = await redis.get(HEARTBEAT_KEY)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def ask_heartbeat() -> tuple[bool, dict | None]:
    """(could it be asked, what it said). A key that is not there was asked and
    answered: there is no runner. The API asks through here so that a status
    page never mistakes "Redis did not answer" for "the runner is stopped"."""
    from redis.asyncio import Redis

    url = os.environ.get("REDIS_URL")
    if not url:
        return False, None
    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True)
    try:
        return True, await read_heartbeat(client)
    except Exception:  # noqa: BLE001 — not being able to ask is an answer of its own
        return False, None
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass


def runner_state(asked: bool, heartbeat: dict | None) -> str:
    """running · degraded (alive, last pass had a failure) · stopped · unknown
    (could not be asked — never shown as running)."""
    if not asked:
        return "unknown"
    if heartbeat is None:
        return "stopped"
    return "running" if heartbeat.get("ok") else "degraded"


async def run_correlate_tick(factory, pub, now: datetime | None = None,
                             batch: int = correlation.BATCH) -> dict:
    """Place every event not yet in a situation, for every tenant with the
    feature on, and announce each situation that opened or grew on that
    tenant's own channel.

    Returns {"tenants", "settled", "opened", "joined", "duplicates", "failed"}.
    A situation is recorded before it is announced: if the announcement cannot
    be sent the situation is still there, and a screen that asks will find it."""
    out = {"tenants": 0, "settled": 0, "opened": 0, "joined": 0, "duplicates": 0, "failed": 0}
    for tenant_id in await tenants(factory):
        out["tenants"] += 1
        try:
            if now is None:
                async with factory() as db:
                    moment = await events.database_now(db)
            else:
                moment = now
            result = await correlation.correlate_tenant(factory, tenant_id, moment, batch)
        except Exception:  # noqa: BLE001 — this tenant's pass, not everyone's
            logger.exception("correlation failed for tenant %s", tenant_id)
            out["failed"] += 1
            continue
        for key in ("settled", "opened", "joined", "duplicates", "failed"):
            out[key] += result[key]
        for event_type, payload in result["announce"]:
            try:
                await pub.publish(tenant_id, event_type, payload)
            except Exception as exc:  # noqa: BLE001 — the record exists; only the nudge was lost
                logger.warning("could not announce %s: %s", event_type, type(exc).__name__)
    return out


async def run_assess_tick(factory, pub, now: datetime | None = None, batch: int = risk.BATCH) -> dict:
    """Assess every situation whose events have changed since it was last
    assessed, for every tenant with the feature on, and announce each new
    assessment on that tenant's own channel.

    Returns {"tenants", "assessed", "changed", "failed"}. An assessment that
    says what the last one said is not written again and not announced."""
    out = {"tenants": 0, "assessed": 0, "changed": 0, "failed": 0}
    for tenant_id in await tenants(factory):
        out["tenants"] += 1
        try:
            if now is None:
                async with factory() as db:
                    moment = await events.database_now(db)
            else:
                moment = now
            result = await risk.assess_tenant(factory, tenant_id, moment, batch)
        except Exception:  # noqa: BLE001 — this tenant's pass, not everyone's
            logger.exception("assessment failed for tenant %s", tenant_id)
            out["failed"] += 1
            continue
        for key in ("assessed", "changed", "failed"):
            out[key] += result[key]
        for event_type, payload in result["announce"]:
            try:
                await pub.publish(tenant_id, event_type, payload)
            except Exception as exc:  # noqa: BLE001 — the record exists; only the nudge was lost
                logger.warning("could not announce %s: %s", event_type, type(exc).__name__)
    return out
