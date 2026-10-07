"""Telling people about a response: the guard who was sent, and whoever an escalation is addressed to.

Three ways, all of them already the platform's:

  the organisation's own screens   the event on `tenant_events:{tenant}`, which
                                   the API forwards to every open session
  the phones of the people named   Expo push, to the devices those people
                                   registered (`push_tokens:{tenant}:{user}`)
  the notification rules           whichever of the organisation's rules asks
                                   for this kind of event (email, SMS, webhook)

NOTHING HERE WRITES TO THE DATABASE, and nothing here can fail a response: every
way of telling is tried on its own and a failure is logged. Each function says
whether anybody could have been reached, which is what the caller records.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

SENT_EVENT = "incident_response_sent"
DECLINED_EVENT = "incident_response_declined"
STOOD_DOWN_EVENT = "incident_response_stood_down"
PUSH_TITLE = "7th AI Vision"
#: A sending older than this is recorded without waking anybody's phone: the
#: record is being caught up, not a guard being sent.
FRESH_FOR_S = 3600


async def tokens_of(redis, tenant_id: str, user_ids: Sequence[Any]) -> list[str]:
    """The devices these people registered."""
    found: set[str] = set()
    for user_id in user_ids:
        try:
            raw = await redis.smembers(f"push_tokens:{tenant_id}:{user_id}")
        except Exception:
            continue
        found.update(t.decode() if isinstance(t, bytes) else t for t in raw)
    return sorted(found)


async def push(redis, tenant_id: str, user_ids: Sequence[Any], body: str, data: Mapping) -> int:
    """Push to these people's phones. Returns how many devices it went to."""
    if redis is None or not user_ids:
        return 0
    tokens = await tokens_of(redis, tenant_id, user_ids)
    if not tokens:
        return 0
    try:
        from app.realtime.redis_listener import _send_expo_push

        await _send_expo_push(tokens, PUSH_TITLE, body, dict(data))
    except Exception:
        logger.exception("response: push failed for tenant %s", tenant_id)
        return 0
    return len(tokens)


async def announce(redis, tenant_id: str, event_type: str, payload: Mapping, now: datetime | None = None) -> bool:
    """Put the event on the organisation's channel. True when it went."""
    if redis is None:
        return False
    at = (now or datetime.now(timezone.utc)).isoformat()
    try:
        await redis.publish(f"tenant_events:{tenant_id}", json.dumps({
            "event_type": event_type, "tenant_id": str(tenant_id), "payload": dict(payload), "occurred_at": at,
        }, default=str))
        return True
    except Exception:
        logger.warning("response: could not announce %s for tenant %s", event_type, tenant_id)
        return False


async def sent(redis, tenant_id: str, sending: Mapping, now: datetime | None = None) -> bool:
    """Tell a guard they have been sent — once, when the record of the sending
    is made."""
    now = now or datetime.now(timezone.utc)
    if (now - sending["dispatched_at"]).total_seconds() > FRESH_FOR_S:
        return False
    payload = {"incident_id": str(sending["incident_id"]), "response_id": str(sending["response_id"]),
               "title": sending["title"], "severity": sending["severity"],
               "site_id": str(sending["site_id"]) if sending["site_id"] else None,
               "site_name": sending["site_name"], "guard_user_id": str(sending["guard_user_id"])}
    announced = await announce(redis, tenant_id, SENT_EVENT, payload, now)
    where = f" at {sending['site_name']}" if sending["site_name"] else ""
    devices = await push(redis, tenant_id, [sending["guard_user_id"]],
                         f"You have been sent to: {sending['title']}{where}",
                         {"type": SENT_EVENT, "incident_id": payload["incident_id"]})
    return announced or devices > 0


async def declined(redis, tenant_id: str, incident: Mapping, guard_name: str | None, reason: str) -> bool:
    """Tell the desk a guard is not coming: the incident needs sending again."""
    return await announce(redis, tenant_id, DECLINED_EVENT, {
        "incident_id": str(incident["id"]), "title": incident["title"], "severity": incident["severity"],
        "site_id": str(incident["site_id"]) if incident.get("site_id") else None,
        "site_name": incident.get("site_name"), "guard_name": guard_name, "reason": reason})


async def stood_down(redis, tenant_id: str, incident: Mapping, reason: str) -> bool:
    """Tell a guard they have been called off."""
    announced = await announce(redis, tenant_id, STOOD_DOWN_EVENT, {
        "incident_id": str(incident["id"]), "title": incident["title"],
        "guard_user_id": str(incident["dispatched_guard_id"]), "reason": reason})
    devices = await push(redis, tenant_id, [incident["dispatched_guard_id"]],
                         f"Stood down from: {incident['title']} ({reason})",
                         {"type": STOOD_DOWN_EVENT, "incident_id": str(incident["id"])})
    return announced or devices > 0


async def escalation(redis, session_factory, tenant_id: str, item: Mapping, now: datetime | None = None) -> bool:
    """Tell people a clock ran out or a policy step fell due. `item` is what
    `response_sla.evaluate_tenant` returned for it."""
    payload = {k: v for k, v in item.items() if k not in ("event_type", "escalation_event_id")}
    announced = await announce(redis, tenant_id, item["event_type"], payload, now)
    devices = await push(redis, tenant_id, item["notify_user_ids"], item["message"],
                         {"type": item["event_type"], "incident_id": item["incident_id"]})
    try:
        from app.notifications.dispatch import dispatch_notifications_for_event

        async with session_factory() as db:
            await dispatch_notifications_for_event(db, tenant_id, item["event_type"], dict(payload),
                                                   ref_id=item["incident_id"])
    except Exception:
        logger.exception("response: could not notify %s for tenant %s", item["event_type"], tenant_id)
    return announced or devices > 0
