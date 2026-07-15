"""Realtime push, AI-worker side (plan §9). Workers publish to Redis Pub/Sub
right after their DB transaction commits — the API's redis_pubsub_listener
forwards to connected /ws/live clients. Uses the exact RealtimeEvent envelope
shape from shared/shared/schemas/realtime.py, built here as a plain dict
(rather than importing the Pydantic class) so workers don't need a pydantic
dependency on top of everything else — only backend imports the schema class
directly; the contract is the shape, not the class."""

import json
import os
from datetime import datetime, timezone
from uuid import UUID

import redis

from shared.constants import tenant_events_channel

_redis_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    return _redis_client


def _publish(tenant_id: UUID, event_type: str, payload: dict) -> None:
    message = json.dumps({
        "schema_version": 1,
        "event_type": event_type,
        "tenant_id": str(tenant_id),
        "payload": payload,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    })
    _get_client().publish(tenant_events_channel(str(tenant_id)), message)


def publish_alert_created(
    tenant_id: UUID, alert_id: UUID, module_type: str, severity: str,
    camera_id: UUID | None = None, title: str | None = None,
) -> None:
    payload = {"alert_id": str(alert_id), "module_type": module_type, "severity": severity}
    if camera_id is not None:
        payload["camera_id"] = str(camera_id)
    if title is not None:
        payload["title"] = title
    _publish(tenant_id, "alert_created", payload)


def publish_incident_created(tenant_id: UUID, incident_id: UUID, alert_id: UUID, severity: str) -> None:
    _publish(tenant_id, "incident_created", {"incident_id": str(incident_id), "alert_id": str(alert_id), "severity": severity})


def publish_lpr_plate_detected(
    tenant_id: UUID,
    detection_id: UUID,
    camera_id: UUID,
    plate_number: str,
    direction: str,
    confidence: float,
) -> None:
    """Published for every plate that clears the confidence threshold (not just
    watchlist hits) so the parking-automation handler in the API process can
    auto-create/close sessions for plates that match a parking_lpr_cameras config."""
    _publish(tenant_id, "lpr_plate_detected", {
        "detection_id": str(detection_id),
        "camera_id": str(camera_id),
        "plate_number": plate_number,
        "direction": direction,
        "confidence": confidence,
    })
