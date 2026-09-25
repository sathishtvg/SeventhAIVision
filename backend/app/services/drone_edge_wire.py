"""What a site edge gateway and the central server say to each other.

One module, imported by both ends: the central API validates a gateway's batch
against these models, and the edge agent builds its batches from them. A field
added on one side and not the other is a failed test, not a silent drop.

The bounds are the database's. A value the database would refuse is refused
here, per item, with a reason — so one bad sample is rejected on its own rather
than failing the whole batch it arrived in and every retry of it after.

NO DATABASE, NO FASTAPI. The edge agent runs on site hardware with neither.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.drone_providers.base import FlightEvent, FlightUpdate, TelemetrySample

#: The provider state a gateway may park on a session — the simulator needs a
#: few hundred bytes; anything near this is a bug, not a flight.
MAX_PROVIDER_STATE_BYTES = 64 * 1024
#: Samples in one batch. A gateway catching up after an outage sends many
#: batches; one enormous request is slower to retry and easier to lose.
MAX_SAMPLES_PER_BATCH = 6000

KEY_PREFIX = "deg"


def parse_key(key: str | None) -> tuple[str, str] | None:
    """(tenant_id, sha256) from a gateway credential `deg.<tenant>.<secret>`, or
    None. The tenant is in the credential so the central lookup can run under
    that tenant's row-level security instead of around it."""
    if not key or len(key) > 200:
        return None
    parts = key.split(".", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX or not parts[2]:
        return None
    try:
        tenant = str(uuid.UUID(parts[1]))
    except ValueError:
        return None
    return tenant, hashlib.sha256(key.encode("utf-8")).hexdigest()


AiModuleName = Literal["lpr", "face", "intrusion", "ppe", "crowd", "fire_smoke", "weapon",
                       "behavior", "tampering", "abandoned", "fall"]
ComponentState = Literal["OK", "WARNING", "FAULT", "UNKNOWN"]
DroneStatusHint = Literal["OFFLINE", "STANDBY", "READY", "PREPARING", "MISSION_ACTIVE", "RETURNING",
                          "CHARGING", "WARNING", "COMMUNICATION_LOST", "CRITICAL"]
FlightEventKind = Literal["WAYPOINT_REACHED", "WAYPOINT_DEPARTED", "COMMS_LOST", "COMMS_RESTORED",
                          "LOW_BATTERY", "FAULT", "RETURN_STARTED", "PAUSED", "RESUMED", "LANDED"]
MediaKind = Literal["SNAPSHOT", "PRE_CLIP", "EVENT_CLIP", "POST_CLIP", "CLIP"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Sample(_Strict):
    recorded_at: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float = Field(ge=-9999, le=99999)
    heading_deg: float | None = Field(None, ge=0, le=360)
    speed_mps: float = Field(ge=0, le=9999)
    battery_pct: float = Field(ge=0, le=100)
    mission_state: str = Field(max_length=24)
    waypoint_sequence: int | None = Field(None, ge=0, le=100_000)
    gps_fix: str = Field("3D", max_length=10)
    signal_quality: int | None = Field(None, ge=0, le=100)


class Event(_Strict):
    kind: FlightEventKind
    at: datetime
    waypoint: int | None = Field(None, ge=0, le=100_000)
    detail: str = Field("", max_length=500)


class Update(_Strict):
    """One FlightUpdate, as the provider adapter at the site produced it."""
    phase: Literal["LAUNCHING", "ACTIVE", "PAUSED", "RETURNING", "LANDED"]
    outcome: Literal["COMPLETED", "ABORTED", "FAILED"] | None = None
    failure_code: str | None = Field(None, max_length=40)
    failure_reason: str | None = Field(None, max_length=2000)
    provider_mission_ref: str | None = Field(None, max_length=120)
    provider_state: dict[str, Any] = Field(default_factory=dict)
    samples: list[Sample] = Field(default_factory=list, max_length=600)
    events: list[Event] = Field(default_factory=list, max_length=200)

    @field_validator("provider_state")
    @classmethod
    def _small_state(cls, v: dict) -> dict:
        if len(json.dumps(v, default=str)) > MAX_PROVIDER_STATE_BYTES:
            raise ValueError("provider_state is larger than 64 KB")
        return v


class SessionUpdate(_Strict):
    """Update number `seq` of a session this gateway flies. Numbers start at 1
    and only go up; the server applies each once, in order."""
    session_id: uuid.UUID
    seq: int = Field(ge=1)
    at: datetime
    launched: bool = False
    update: Update


class DroneHealthIn(_Strict):
    drone_id: uuid.UUID
    observed_at: datetime
    battery_level: float | None = Field(None, ge=0, le=100)
    battery_health: float | None = Field(None, ge=0, le=100)
    gps_status: ComponentState = "UNKNOWN"
    communication_status: ComponentState = "UNKNOWN"
    camera_status: ComponentState = "UNKNOWN"
    storage_status: ComponentState = "UNKNOWN"
    temperature_c: float | None = Field(None, ge=-99, le=999)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    altitude_m: float | None = Field(None, ge=-9999, le=99999)
    status_hint: DroneStatusHint | None = None


class CommandResult(_Strict):
    command_id: uuid.UUID
    status: Literal["DONE", "REJECTED", "FAILED"]
    result: str = Field(max_length=2000)
    at: datetime


class EventIn(_Strict):
    """Something seen during a flight, recorded at the site. `client_ref` is the
    gateway's own id for it: sending it twice is sending it once."""
    client_ref: uuid.UUID
    drone_id: uuid.UUID
    session_id: uuid.UUID | None = None
    module_type: AiModuleName
    detected_at: datetime
    ai_confidence: float | None = Field(None, ge=0, le=1)
    drone_latitude: float | None = Field(None, ge=-90, le=90)
    drone_longitude: float | None = Field(None, ge=-180, le=180)
    drone_altitude_m: float | None = Field(None, ge=-9999, le=99999)
    waypoint_sequence: int | None = Field(None, ge=0, le=100_000)
    observed_seconds: float | None = Field(None, ge=0, le=99999)
    detection_ref: uuid.UUID | None = None


class MediaIn(_Strict):
    """A file held at the site: its description now, its bytes only if the
    recording policy asks for them."""
    client_ref: uuid.UUID
    event_client_ref: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    waypoint_sequence: int | None = Field(None, ge=0, le=100_000)
    media_kind: MediaKind
    captured_at: datetime
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0, le=4 * 1024 ** 3)
    duration_seconds: float | None = Field(None, ge=0, le=99999)
    telemetry_snapshot: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _owned(self) -> "MediaIn":
        if self.event_client_ref is None and self.session_id is None:
            raise ValueError("media must belong to an event or a session")
        return self


class GatewayState(_Strict):
    buffer_depth: int = Field(0, ge=0)
    oldest_buffered_at: datetime | None = None
    storage_free_pct: float | None = Field(None, ge=0, le=100)
    uptime_s: float | None = Field(None, ge=0)


class SyncBatch(_Strict):
    """Everything a gateway has to say since its last acknowledged batch. An
    empty batch is its heartbeat."""
    batch_id: uuid.UUID
    sent_at: datetime
    software_version: str | None = Field(None, max_length=40)
    state: GatewayState = Field(default_factory=GatewayState)
    have_sessions: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    health: list[DroneHealthIn] = Field(default_factory=list, max_length=200)
    updates: list[SessionUpdate] = Field(default_factory=list, max_length=500)
    commands: list[CommandResult] = Field(default_factory=list, max_length=200)
    events: list[EventIn] = Field(default_factory=list, max_length=500)
    media: list[MediaIn] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _bounded(self) -> "SyncBatch":
        if sum(len(u.update.samples) for u in self.updates) > MAX_SAMPLES_PER_BATCH:
            raise ValueError(f"a batch may carry at most {MAX_SAMPLES_PER_BATCH} telemetry samples")
        return self

    def item_count(self) -> int:
        return len(self.health) + len(self.updates) + len(self.commands) + len(self.events) + len(self.media)


# ── FlightUpdate ⇄ wire ──────────────────────────────────────────────────────

def to_flight_update(u: Update) -> FlightUpdate:
    return FlightUpdate(
        provider_state=dict(u.provider_state), phase=u.phase,
        samples=[TelemetrySample(recorded_at=x.recorded_at, latitude=x.latitude, longitude=x.longitude,
                                 altitude_m=x.altitude_m, heading_deg=x.heading_deg, speed_mps=x.speed_mps,
                                 battery_pct=x.battery_pct, mission_state=x.mission_state,
                                 waypoint_sequence=x.waypoint_sequence, gps_fix=x.gps_fix,
                                 signal_quality=x.signal_quality) for x in u.samples],
        events=[FlightEvent(kind=e.kind, at=e.at, waypoint=e.waypoint, detail=e.detail) for e in u.events],
        outcome=u.outcome, failure_code=u.failure_code, failure_reason=u.failure_reason,
        provider_mission_ref=u.provider_mission_ref,
    )


def from_flight_update(up: FlightUpdate) -> dict:
    """The wire form of an adapter's update, JSON-ready — what the edge stores in
    its outbox and later sends."""
    return Update(
        phase=up.phase, outcome=up.outcome, failure_code=up.failure_code, failure_reason=up.failure_reason,
        provider_mission_ref=up.provider_mission_ref, provider_state=up.provider_state,
        samples=[Sample(recorded_at=x.recorded_at, latitude=x.latitude, longitude=x.longitude,
                        altitude_m=x.altitude_m, heading_deg=x.heading_deg, speed_mps=x.speed_mps,
                        battery_pct=x.battery_pct, mission_state=x.mission_state,
                        waypoint_sequence=x.waypoint_sequence, gps_fix=x.gps_fix,
                        signal_quality=x.signal_quality) for x in up.samples],
        events=[Event(kind=e.kind, at=e.at, waypoint=e.waypoint, detail=e.detail or "") for e in up.events],
    ).model_dump(mode="json")
