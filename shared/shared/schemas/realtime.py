from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class RealtimeEventType(str, Enum):
    ALERT_CREATED = "alert_created"
    INCIDENT_CREATED = "incident_created"
    CAMERA_STATUS_CHANGED = "camera_status_changed"


class RealtimeEvent(BaseModel):
    """Contract for messages published on `tenant_events:{tenant_id}` and
    forwarded verbatim to connected /ws/live clients for that tenant.

    schema_version exists so a future breaking change to this shape can be
    introduced without client-side guessing (plan §16.5) — bump it only when
    the payload shape itself changes incompatibly, not on every new event_type.
    """

    schema_version: int = 1
    event_type: RealtimeEventType
    tenant_id: UUID
    payload: dict[str, Any]
    occurred_at: datetime
