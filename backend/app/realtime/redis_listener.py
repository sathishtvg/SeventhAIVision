"""Background task, one per API process, started in FastAPI's lifespan.
Bridges Redis Pub/Sub (where AI workers publish after committing an
alert/incident) to this process's locally-connected WebSocket clients via
ConnectionManager (plan §9)."""

import asyncio
import json
import logging
from uuid import UUID

import httpx
from redis.asyncio import Redis

from app.core.metrics import ws_events_forwarded_total
from app.db.session import AsyncSessionLocal
from app.notifications.dispatch import (
    dispatch_notifications_for_alert,
    dispatch_notifications_for_event,
)
from app.realtime.connection_manager import manager
from app.services.parking_lpr import handle_lpr_plate_detected
from app.services.webhook_dispatcher import dispatch_event
from shared.constants import TENANT_EVENTS_CHANNEL_PREFIX

logger = logging.getLogger(__name__)

TENANT_EVENTS_PATTERN = f"{TENANT_EVENTS_CHANNEL_PREFIX}*"
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_HIGH_SEVERITIES = {"high", "critical"}


async def _send_expo_push(tokens: list[str], title: str, body: str, data: dict) -> None:
    """Fire-and-forget Expo push notification to a list of tokens.
    Uses Expo's push gateway which handles FCM + APNs routing automatically."""
    if not tokens:
        return
    messages = [
        {"to": t, "title": title, "body": body, "data": data, "sound": "default", "priority": "high"}
        for t in tokens
    ]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(EXPO_PUSH_URL, json=messages)
    except Exception as exc:
        logger.warning("expo_push failed: %s", exc)


async def _resolve_push_tokens(
    redis_client: Redis, tenant_id_str: str, payload: dict
) -> list[str]:
    """Site+shift-aware token resolution (Gap 82).

    Route to guards on active shift at the alert's site (plus operational
    users assigned to that site). Falls back to the tenant-wide token set
    when the camera has no site, nobody is rostered, or the targeted users
    have no registered devices — an alert must never be dropped."""
    target_user_ids: list[str] | None = None
    camera_id = payload.get("camera_id") or ""
    if camera_id:
        try:
            from sqlalchemy import text as sa_text

            from app.services.alert_routing import resolve_push_targets

            async with AsyncSessionLocal() as session:
                await session.execute(
                    sa_text("SELECT set_config('app.current_tenant', :tid, true)"),
                    {"tid": tenant_id_str},
                )
                target_user_ids = await resolve_push_targets(session, camera_id)
        except Exception as exc:
            logger.warning("push routing failed, falling back tenant-wide: %s", exc)
            target_user_ids = None

    if target_user_ids:
        tokens: set[str] = set()
        for uid in target_user_ids:
            raw = await redis_client.smembers(f"push_tokens:{tenant_id_str}:{uid}")
            tokens.update(t.decode() if isinstance(t, bytes) else t for t in raw)
        if tokens:
            return list(tokens)
        # Targeted users have no registered devices → tenant-wide fallback.

    tokens_raw = await redis_client.smembers(f"push_tokens:{tenant_id_str}")
    return [t.decode() if isinstance(t, bytes) else t for t in tokens_raw]


async def _handle_push_notification(redis_client: Redis, tenant_id_str: str, payload: dict) -> None:
    """Fetch push tokens (site-routed when possible) and send Expo push."""
    severity = payload.get("severity", "")
    if severity not in _HIGH_SEVERITIES:
        return
    tokens = await _resolve_push_tokens(redis_client, tenant_id_str, payload)
    if not tokens:
        return
    title = payload.get("title") or "Security Alert"
    module = payload.get("module_type", "")
    body_text = f"[{module.upper()}] {title}" if module else title
    await _send_expo_push(tokens, "7th AI Vision Alert", body_text, {
        "alert_id": payload.get("alert_id", ""),
        "severity": severity,
    })


async def _handle_alert_notification(tenant_id_str: str, payload: dict) -> None:
    """Fire-and-forget: open a DB session and dispatch notification channels.
    Called via asyncio.create_task() so it never blocks WebSocket fan-out."""
    alert_id = payload.get("alert_id")
    if not alert_id:
        return
    try:
        async with AsyncSessionLocal() as session:
            await dispatch_notifications_for_alert(
                session=session,
                tenant_id=tenant_id_str,
                alert_id=alert_id,
                module_type=payload.get("module_type", ""),
                severity=payload.get("severity", "medium"),
            )
    except Exception as exc:
        logger.error("_handle_alert_notification failed tenant=%s alert=%s: %s", tenant_id_str, alert_id, exc)


async def _handle_event_notification(tenant_id_str: str, event_type: str, payload: dict, ref_id: str | None = None) -> None:
    """Fire-and-forget: dispatch notification channels for T4-T8 events."""
    try:
        async with AsyncSessionLocal() as session:
            await dispatch_notifications_for_event(
                session=session,
                tenant_id=tenant_id_str,
                event_type=event_type,
                payload=payload,
                ref_id=ref_id,
            )
    except Exception as exc:
        logger.error("_handle_event_notification failed tenant=%s event=%s: %s", tenant_id_str, event_type, exc)


async def _handle_lpr_parking(redis_client: Redis, tenant_id_str: str, payload: dict) -> None:
    """Fire-and-forget: open a DB session and run LPR→parking automation."""
    from sqlalchemy import text as sa_text
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": tenant_id_str},
            )
            await handle_lpr_plate_detected(session, redis_client, tenant_id_str, payload)
    except Exception as exc:
        logger.error("_handle_lpr_parking failed tenant=%s: %s", tenant_id_str, exc)


async def _handle_lpr_access(redis_client: Redis, tenant_id_str: str, payload: dict) -> None:
    """Fire-and-forget: run the gate chain for one plate read — access
    decision, barrier actuation, and the site's visitor entry/exit.

    Deliberately a SEPARATE task from _handle_lpr_parking rather than an
    extension of it: parking meters occupancy and fees, this decides whether a
    vehicle may enter and who is driving it. A failure in either must not stop
    the other from running, and a site can legitimately use one without the
    other.
    """
    from sqlalchemy import text as sa_text

    from app.services.vms import handle_lpr_access
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": tenant_id_str},
            )
            await handle_lpr_access(
                session, redis_client, tenant_id=tenant_id_str, payload=payload
            )
            await session.commit()
    except Exception as exc:
        logger.error("_handle_lpr_access failed tenant=%s: %s", tenant_id_str, exc)


async def redis_pubsub_listener(redis_client: Redis) -> None:
    """Reconnects automatically on any Redis connection error (e.g. Docker
    network resets between containers) so a transient disruption doesn't
    permanently kill the WebSocket push path."""
    while True:
        pubsub = redis_client.pubsub()
        try:
            await pubsub.psubscribe(TENANT_EVENTS_PATTERN)
            logger.info("redis_pubsub_listener started pattern=%s", TENANT_EVENTS_PATTERN)
            async for message in pubsub.listen():
                if message["type"] != "pmessage":
                    continue
                channel = message["channel"]
                channel_str = channel.decode() if isinstance(channel, bytes) else channel
                tenant_id_str = channel_str.removeprefix(TENANT_EVENTS_CHANNEL_PREFIX)
                try:
                    tenant_id = UUID(tenant_id_str)
                except ValueError:
                    logger.warning("redis_pubsub_listener bad_channel=%s", channel_str)
                    continue
                data = message["data"]
                data_str = data.decode() if isinstance(data, bytes) else data
                await manager.broadcast_to_tenant(tenant_id, data_str)

                event_type = "unknown"
                parsed: dict = {}
                try:
                    parsed = json.loads(data_str)
                    event_type = parsed.get("event_type", "unknown")
                except (json.JSONDecodeError, AttributeError):
                    pass
                ws_events_forwarded_total.labels(tenant_id=str(tenant_id), event_type=event_type).inc()

                # Webhook fan-out for all supported event types
                if event_type in (
                    "alert_created", "incident_created", "camera_status_changed",
                    "detection_created", "dsr_request_received",
                ):
                    asyncio.create_task(
                        dispatch_event(
                            tenant_id=tenant_id_str,
                            event_type=event_type,
                            payload=parsed.get("payload", {}),
                            event_id=parsed.get("payload", {}).get(
                                "alert_id") or parsed.get("payload", {}).get("incident_id"),
                        )
                    )

                if event_type == "alert_created":
                    alert_payload = parsed.get("payload", {})
                    asyncio.create_task(
                        _handle_alert_notification(tenant_id_str, alert_payload)
                    )
                    asyncio.create_task(
                        _handle_push_notification(redis_client, tenant_id_str, alert_payload)
                    )

                elif event_type == "lpr_plate_detected":
                    lpr_payload = parsed.get("payload", {})
                    asyncio.create_task(
                        _handle_lpr_parking(redis_client, tenant_id_str, lpr_payload)
                    )
                    asyncio.create_task(
                        _handle_lpr_access(redis_client, tenant_id_str, lpr_payload)
                    )

                # ── T4–T8: non-alert event notification dispatch ──────────
                elif event_type in (
                    "incident_created",
                    "camera_status_changed",
                    "sos_triggered",
                    "tour_occurrence_missed",
                    "broadcast_created",
                ):
                    ep = parsed.get("payload", {})
                    ref = ep.get("incident_id") or ep.get("camera_id") or ep.get("entry_id")
                    asyncio.create_task(
                        _handle_event_notification(tenant_id_str, event_type, ep, ref)
                    )
                    # SOS also gets Expo push regardless of notification rules
                    if event_type == "sos_triggered":
                        sos_payload = {**ep, "severity": "critical"}
                        asyncio.create_task(
                            _handle_push_notification(redis_client, tenant_id_str, sos_payload)
                        )

        except asyncio.CancelledError:
            await pubsub.punsubscribe(TENANT_EVENTS_PATTERN)
            await pubsub.aclose()
            raise
        except Exception as exc:
            logger.error("redis_pubsub_listener connection error, reconnecting in 3s: %s", exc)
            await asyncio.sleep(3)
        finally:
            try:
                await pubsub.punsubscribe(TENANT_EVENTS_PATTERN)
                await pubsub.aclose()
            except Exception:
                pass
