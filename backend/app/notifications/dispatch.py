"""Notification dispatch: load matching rules for an event and fire each channel.

Called from the Redis Pub/Sub listener as a fire-and-forget asyncio.create_task().
All failures are caught and written to notification_logs — they never propagate up
to the WebSocket fan-out path.

Supports two entry points:
  dispatch_notifications_for_alert()  — alert_created events (original)
  dispatch_notifications_for_event()  — T4-T8 non-alert events (camera_offline,
                                        sos_triggered, incident_created, etc.)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.notifications.providers.email import send_email
from app.notifications.providers.sms import send_sms
from app.notifications.providers.webhook import send_webhook

logger = logging.getLogger(__name__)

# Ordered from least to most severe so >= comparisons work with .index()
SEVERITY_ORDER = ("info", "low", "medium", "high", "critical")

# Default severity assigned to each non-alert event type
_EVENT_SEVERITY: dict[str, str] = {
    "sos_triggered":           "critical",
    "broadcast_created":       "high",
    "incident_created":        "medium",   # overridden by event payload if present
    "camera_status_changed":   "medium",
    "tour_occurrence_missed":  "medium",
}


def _severity_ge(a: str, b: str) -> bool:
    """Return True if severity a is >= severity b."""
    try:
        return SEVERITY_ORDER.index(a) >= SEVERITY_ORDER.index(b)
    except ValueError:
        return False


async def _load_matching_rules(
    session: AsyncSession,
    tenant_id: str,
    event_type: str,
    event_severity: str,
    alert_module: str = "",
    alert_code: str = "",
    alert_site_id: str = "",
) -> list[dict[str, Any]]:
    """Return active notification_rules whose trigger_events and filters match.

    Matching logic:
    - trigger_events = []  (legacy)  → rule matches alert_created only
    - trigger_events = [...]         → rule matches if list contains event_type or '*'
    - For alert_created: additionally check min_severity / module_types /
      alert_codes / site_ids (Gap 82 — empty site_ids matches every site,
      including alerts from cameras with no site)
    - For other events: only check min_severity against the event's severity
    """
    result = await session.execute(
        text("""
            SELECT r.id AS rule_id, r.min_severity, r.module_types, r.alert_codes,
                   r.trigger_events, r.site_ids,
                   c.id AS channel_id, c.channel_type, c.config AS channel_config
            FROM notification_rules r
            JOIN notification_channels c ON c.id = r.channel_id
            WHERE r.tenant_id = :tenant_id
              AND r.is_active = TRUE
              AND c.is_active = TRUE
        """),
        {"tenant_id": tenant_id},
    )
    rows = result.mappings().all()

    matching = []
    for row in rows:
        trigger_events: list = row["trigger_events"] or []

        # ── trigger_events filter ─────────────────────────────────────────────
        if not trigger_events:
            # Legacy: empty list → alert_created only
            if event_type != "alert_created":
                continue
        else:
            # Explicit list: must contain this event_type or '*' wildcard
            if "*" not in trigger_events and event_type not in trigger_events:
                continue

        # ── severity filter ───────────────────────────────────────────────────
        if not _severity_ge(event_severity, row["min_severity"]):
            continue

        # ── alert-only filters (skip for non-alert events) ────────────────────
        if event_type == "alert_created":
            module_types: list = row["module_types"] or []
            if module_types and alert_module not in module_types:
                continue
            alert_codes: list = row["alert_codes"] or []
            if alert_codes and alert_code not in alert_codes:
                continue
            # .get(): tolerate rows without the column (pre-0052 fakes in tests)
            site_ids: list = row.get("site_ids") or []
            if site_ids and alert_site_id not in site_ids:
                continue

        matching.append(dict(row))

    return matching


async def _write_log(
    session: AsyncSession,
    tenant_id: str,
    alert_id: str | None,
    channel_id: str | None,
    channel_type: str,
    status: str,
    error_detail: str | None,
    event_type: str = "alert_created",
) -> None:
    sent_at = datetime.now(timezone.utc) if status == "sent" else None
    await session.execute(
        text("""
            INSERT INTO notification_logs
                (id, tenant_id, alert_id, channel_id, channel_type,
                 status, error_detail, sent_at, event_type)
            VALUES
                (:id, :tenant_id, :alert_id, :channel_id, :channel_type,
                 :status, :error_detail, :sent_at, :event_type)
        """),
        {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "alert_id": alert_id,
            "channel_id": channel_id,
            "channel_type": channel_type,
            "status": status,
            "error_detail": error_detail,
            "sent_at": sent_at,
            "event_type": event_type,
        },
    )


_PROVIDER_MAP = {
    "email": send_email,
    "sms": send_sms,
    "webhook": send_webhook,
}


async def _fetch_alert(session: AsyncSession, tenant_id: str, alert_id: str) -> dict[str, Any] | None:
    result = await session.execute(
        text("""
            SELECT id::text, tenant_id::text, camera_id::text, module_type,
                   severity, alert_code, title, message, status, created_at
            FROM alerts
            WHERE id = :alert_id
        """),
        {"alert_id": alert_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


def _build_event_payload(event_type: str, payload: dict[str, Any], severity: str) -> dict[str, Any]:
    """Shape a non-alert event into a provider-compatible notification dict."""
    titles = {
        "sos_triggered":          "SOS Panic Alert",
        "incident_created":       "New Incident Opened",
        "camera_status_changed":  "Camera Offline",
        "tour_occurrence_missed": "Guard Tour Missed",
        "broadcast_created":      "Emergency Broadcast",
    }
    title = payload.get("title") or titles.get(event_type, event_type.replace("_", " ").title())

    # Build a human-readable message from the payload fields
    lines = []
    for key in ("guard_name", "camera_name", "message", "reason",
                "schedule_name", "subject", "camera_id"):
        val = payload.get(key)
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val}")

    return {
        "id": str(uuid.uuid4()),
        "event_type": event_type,
        "severity": severity,
        "module_type": event_type,
        "title": title,
        "message": "\n".join(lines) if lines else event_type,
        "camera_id": payload.get("camera_id", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


async def _dispatch_channels(
    session: AsyncSession,
    tenant_id: str,
    event_type: str,
    payload_dict: dict[str, Any],
    ref_id: str | None,
    rules: list[dict[str, Any]],
) -> None:
    """Dispatch all matching rules and log each attempt."""

    async def _dispatch_one(rule: dict[str, Any]) -> None:
        channel_id = str(rule["channel_id"])
        channel_type = rule["channel_type"]
        provider = _PROVIDER_MAP.get(channel_type)
        if provider is None:
            logger.warning("dispatch: unknown channel_type=%s", channel_type)
            return
        try:
            await provider(rule["channel_config"], payload_dict)
            st, err = "sent", None
        except Exception as exc:
            st, err = "failed", str(exc)[:500]
            logger.error(
                "dispatch: channel=%s type=%s event=%s error=%s",
                channel_id, channel_type, event_type, exc,
            )
        async with session.begin_nested():
            await _write_log(
                session, tenant_id, ref_id,
                channel_id, channel_type, st, err, event_type,
            )
        await session.commit()

    await asyncio.gather(*(_dispatch_one(rule) for rule in rules), return_exceptions=True)


async def dispatch_notifications_for_alert(
    session: AsyncSession,
    tenant_id: str,
    alert_id: str,
    module_type: str,
    severity: str,
) -> None:
    """Load matching rules, dispatch each channel, write a log row.

    Original entry point for alert_created events — unchanged behaviour.
    """
    try:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": tenant_id},
        )
        alert = await _fetch_alert(session, tenant_id, alert_id)
    except Exception as exc:
        logger.error("dispatch: failed fetching alert tenant=%s alert=%s: %s", tenant_id, alert_id, exc)
        return

    if alert is None:
        logger.warning("dispatch: alert not found tenant=%s alert=%s", tenant_id, alert_id)
        return

    # Site lookup only narrows rule matching — its failure must never block
    # notification dispatch (rules with empty site_ids still match "").
    try:
        from app.services.alert_routing import resolve_alert_site_id

        site_id = await resolve_alert_site_id(session, alert.get("camera_id"))
    except Exception as exc:
        logger.warning("dispatch: site lookup failed tenant=%s alert=%s: %s", tenant_id, alert_id, exc)
        site_id = None

    try:
        rules = await _load_matching_rules(
            session, tenant_id,
            event_type="alert_created",
            event_severity=severity,
            alert_module=module_type,
            alert_code=alert.get("alert_code", ""),
            alert_site_id=site_id or "",
        )
    except Exception as exc:
        logger.error("dispatch: failed loading rules tenant=%s alert=%s: %s", tenant_id, alert_id, exc)
        return

    if not rules:
        return

    await _dispatch_channels(session, tenant_id, "alert_created", alert, alert_id, rules)


async def dispatch_notifications_for_event(
    session: AsyncSession,
    tenant_id: str,
    event_type: str,
    payload: dict[str, Any],
    ref_id: str | None = None,
) -> None:
    """Dispatch notification channels for T4–T8 non-alert events.

    event_type: one of sos_triggered, incident_created, camera_status_changed,
                tour_occurrence_missed, broadcast_created (or any future type)
    payload:    the event payload dict from the Redis message
    ref_id:     optional incident/camera ID for log correlation
    """
    severity = payload.get("severity") or _EVENT_SEVERITY.get(event_type, "medium")

    try:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": tenant_id},
        )
        rules = await _load_matching_rules(
            session, tenant_id,
            event_type=event_type,
            event_severity=severity,
        )
    except Exception as exc:
        logger.error(
            "dispatch_event: failed loading rules tenant=%s event=%s: %s",
            tenant_id, event_type, exc,
        )
        return

    if not rules:
        return

    event_dict = _build_event_payload(event_type, payload, severity)
    await _dispatch_channels(session, tenant_id, event_type, event_dict, ref_id, rules)
