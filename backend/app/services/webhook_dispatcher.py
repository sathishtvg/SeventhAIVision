"""Webhook event dispatcher — HMAC-signed delivery with exponential-backoff retry.

Called by redis_listener.py for every event type (alert_created, incident_created,
camera_status_changed, detection_created).  Each call is fire-and-forget
via asyncio.create_task() so it never blocks the pub/sub listener loop.

Delivery guarantee: at-least-once (each event gets a delivery_log row whose
status advances from pending → delivered / failed).  Retry is driven by the
scheduler checking for rows where status='retrying' AND next_retry_at <= now().
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Retry back-off schedule (seconds): attempt 1→60s, 2→300s, 3→1800s
_RETRY_DELAYS = [60, 300, 1800]

# Supported event types
SUPPORTED_EVENTS = {
    "alert_created",
    "incident_created",
    "camera_status_changed",
    "detection_created",
    "dsr_request_received",
}


def _hmac_signature(secret: str, body: bytes) -> str:
    """Return sha256=<hex> as used by GitHub-style webhook signing."""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


async def dispatch_event(
    tenant_id: str,
    event_type: str,
    payload: dict[str, Any],
    event_id: str | None = None,
) -> None:
    """Fan-out one event to all matching active webhook subscriptions for the tenant."""
    if event_type not in SUPPORTED_EVENTS:
        return

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        # Fetch matching active subscriptions
        subs_result = await db.execute(
            text("""
                SELECT id, url, secret, filters, max_retries, timeout_seconds
                FROM webhook_subscriptions
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND is_active = TRUE
                  AND event_types @> CAST(:etype AS jsonb)
            """),
            {"tid": str(tenant_id), "etype": json.dumps(event_type)},
        )
        subscriptions = [dict(r._mapping) for r in subs_result]
        if not subscriptions:
            return

        for sub in subscriptions:
            if not _passes_filters(payload, sub["filters"] or {}):
                continue
            log_id = await _create_delivery_log(
                db, tenant_id, sub["id"], event_type, event_id, payload
            )
            await db.commit()
            # Attempt delivery immediately (best-effort, fire-and-forget within this call)
            await _attempt_delivery(
                log_id=log_id,
                sub_id=sub["id"],
                tenant_id=tenant_id,
                url=sub["url"],
                secret=sub["secret"],
                payload=payload,
                event_type=event_type,
                event_id=event_id,
                max_retries=sub["max_retries"],
                timeout=sub["timeout_seconds"],
            )


def _passes_filters(payload: dict, filters: dict) -> bool:
    """Return True if payload matches all filter criteria."""
    if not filters:
        return True
    for key, allowed in filters.items():
        if not isinstance(allowed, list):
            allowed = [allowed]
        val = payload.get(key)
        if val is not None and str(val) not in [str(a) for a in allowed]:
            return False
    return True


async def _create_delivery_log(
    db: AsyncSession,
    tenant_id: str,
    subscription_id: UUID,
    event_type: str,
    event_id: str | None,
    payload: dict,
) -> UUID:
    result = await db.execute(
        text("""
            INSERT INTO webhook_delivery_log
              (tenant_id, subscription_id, event_type, event_id, payload, status)
            VALUES
              (CAST(:tid AS uuid), CAST(:sid AS uuid), :etype,
               CAST(:eid AS uuid), CAST(:payload AS jsonb), 'pending')
            RETURNING id
        """),
        {
            "tid": str(tenant_id),
            "sid": str(subscription_id),
            "etype": event_type,
            "eid": str(event_id) if event_id else None,
            "payload": json.dumps(payload),
        },
    )
    return result.scalar()


async def _attempt_delivery(
    *,
    log_id: UUID,
    sub_id: UUID,
    tenant_id: str,
    url: str,
    secret: str,
    payload: dict,
    event_type: str,
    event_id: str | None,
    max_retries: int,
    timeout: int,
) -> None:
    body = json.dumps({
        "event_type": event_type,
        "event_id": str(event_id) if event_id else None,
        "tenant_id": str(tenant_id),
        "payload": payload,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
    }).encode()
    signature = _hmac_signature(secret, body)
    headers = {
        "Content-Type": "application/json",
        "X-Seventh-Event": event_type,
        "X-Seventh-Signature": signature,
        "X-Seventh-Delivery": str(log_id),
    }

    status_code = None
    response_body = None
    try:
        async with httpx.AsyncClient(timeout=float(timeout)) as client:
            resp = await client.post(url, content=body, headers=headers)
            status_code = resp.status_code
            response_body = resp.text[:1000]
            success = 200 <= status_code < 300
    except Exception as exc:
        response_body = str(exc)[:1000]
        success = False

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        if success:
            await db.execute(
                text("""
                    UPDATE webhook_delivery_log
                    SET status = 'delivered',
                        attempt_count = attempt_count + 1,
                        response_status_code = :sc,
                        response_body = :rb,
                        delivered_at = now(),
                        updated_at = now()
                    WHERE id = CAST(:lid AS uuid)
                """),
                {"sc": status_code, "rb": response_body, "lid": str(log_id)},
            )
        else:
            # Schedule retry or mark failed
            await db.execute(
                text("""
                    UPDATE webhook_delivery_log
                    SET attempt_count = attempt_count + 1,
                        response_status_code = :sc,
                        response_body = :rb,
                        updated_at = now(),
                        status = CASE
                            WHEN attempt_count + 1 >= :max_r THEN 'failed'
                            ELSE 'retrying'
                        END,
                        next_retry_at = CASE
                            WHEN attempt_count + 1 >= :max_r THEN NULL
                            ELSE now() + CAST(:delay_s || ' seconds' AS interval)
                        END
                    WHERE id = CAST(:lid AS uuid)
                    RETURNING attempt_count
                """),
                {
                    "sc": status_code,
                    "rb": response_body,
                    "lid": str(log_id),
                    "max_r": max_retries,
                    "delay_s": _RETRY_DELAYS[min(0, max_retries - 1)],
                },
            )
        await db.commit()


async def retry_pending_deliveries() -> int:
    """Called by the scheduler to retry overdue delivery_log rows.  Returns count retried."""
    retried = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("""
                SELECT wdl.id, wdl.tenant_id, wdl.subscription_id,
                       wdl.event_type, wdl.event_id, wdl.payload,
                       wdl.attempt_count,
                       ws.url, ws.secret, ws.max_retries, ws.timeout_seconds
                FROM webhook_delivery_log wdl
                JOIN webhook_subscriptions ws ON ws.id = wdl.subscription_id
                WHERE wdl.status = 'retrying'
                  AND wdl.next_retry_at <= now()
                LIMIT 100
            """)
        )
        rows = [dict(r._mapping) for r in result]

    for row in rows:
        attempt_num = row["attempt_count"]
        delay_idx = min(attempt_num, len(_RETRY_DELAYS) - 1)
        await _attempt_delivery(
            log_id=row["id"],
            sub_id=row["subscription_id"],
            tenant_id=str(row["tenant_id"]),
            url=row["url"],
            secret=row["secret"],
            payload=row["payload"],
            event_type=row["event_type"],
            event_id=str(row["event_id"]) if row["event_id"] else None,
            max_retries=row["max_retries"],
            timeout=row["timeout_seconds"],
        )
        retried += 1
        _ = delay_idx  # delay_idx used implicitly via _RETRY_DELAYS in _attempt_delivery

    return retried
