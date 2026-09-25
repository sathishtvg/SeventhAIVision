"""The drone provider interface: what the platform may ask of any aircraft.

No manufacturer is built into the patrol system. Everything above this layer —
the runner, pre-flight, sessions, the API — talks to a DroneProvider, and each
kind of aircraft (or the simulator) is an adapter behind it. Adding a vendor is a
new adapter and a catalogue entry; nothing else changes.

CAPABILITIES ARE DECLARED, AND ENFORCED. An adapter lists what its aircraft can
actually do. Asking for anything else raises CapabilityNotSupported — the
platform never pretends a drone can pause, or return home, because the button
exists. The API checks the same list before it queues a command, so an operator
is told at once rather than finding out mid-flight.

ADAPTERS ARE CALLED BY THE DRONE RUNNER, NEVER BY A WEB REQUEST. Every method is
given `now` rather than reading a clock, so the runner, a replay and a test all
mean the same instant.

FLIGHTS CARRY THEIR OWN STATE. `provider_state` is whatever an adapter needs
between calls; the runner stores it on the session after every call and hands it
back on the next, so a restarted runner resumes a flight instead of losing it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar

from app.services.drone_flight_plan import MissionPlan


class Capability(str, Enum):
    HEALTH = "HEALTH"
    TELEMETRY = "TELEMETRY"
    POSITION = "POSITION"
    MISSION = "MISSION"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    ABORT = "ABORT"
    RETURN_TO_HOME = "RETURN_TO_HOME"
    LIVE_STREAM = "LIVE_STREAM"
    RECORDING = "RECORDING"
    SNAPSHOT = "SNAPSHOT"


class ProviderError(Exception):
    """The provider could not do what was asked. Shown to an operator."""


class CapabilityNotSupported(ProviderError):
    def __init__(self, provider: str, capability: Capability):
        super().__init__(f"The {provider} provider cannot {capability.value.lower().replace('_', ' ')}.")
        self.capability = capability


class ProviderUnavailable(ProviderError):
    """The provider (or the aircraft) cannot be reached right now."""


@dataclass(frozen=True)
class DroneRef:
    id: str
    code: str
    provider_drone_ref: str | None = None
    status: str = "OFFLINE"
    battery_level: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    last_heartbeat_at: datetime | None = None


@dataclass(frozen=True)
class DroneHealth:
    observed_at: datetime
    battery_level: float | None
    gps_status: str = "UNKNOWN"             # OK | WARNING | FAULT | UNKNOWN
    communication_status: str = "UNKNOWN"
    camera_status: str = "UNKNOWN"
    storage_status: str = "UNKNOWN"
    battery_health: float | None = None
    temperature_c: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    status_hint: str | None = None          # what the drone says it is doing: READY, CHARGING…


@dataclass(frozen=True)
class TelemetrySample:
    recorded_at: datetime
    latitude: float
    longitude: float
    altitude_m: float
    heading_deg: float | None
    speed_mps: float
    battery_pct: float
    mission_state: str
    waypoint_sequence: int | None = None
    gps_fix: str = "3D"
    signal_quality: int | None = None


@dataclass(frozen=True)
class FlightEvent:
    kind: str       # WAYPOINT_REACHED | WAYPOINT_DEPARTED | COMMS_LOST | COMMS_RESTORED |
                    # LOW_BATTERY | FAULT | RETURN_STARTED | PAUSED | RESUMED | LANDED
    at: datetime
    waypoint: int | None = None
    detail: str = ""


@dataclass
class FlightContext:
    session_id: str
    tenant_id: str
    drone: DroneRef
    plan: MissionPlan
    provider_state: dict[str, Any] = field(default_factory=dict)
    provider_mission_ref: str | None = None


@dataclass
class FlightUpdate:
    """What changed since the last call. `phase` is where the flight is now:
    LAUNCHING, ACTIVE, PAUSED, RETURNING or LANDED. `outcome` is set once, when
    it lands: COMPLETED, ABORTED or FAILED."""
    provider_state: dict[str, Any]
    phase: str
    samples: list[TelemetrySample] = field(default_factory=list)
    events: list[FlightEvent] = field(default_factory=list)
    outcome: str | None = None
    failure_code: str | None = None
    failure_reason: str | None = None
    provider_mission_ref: str | None = None


class DroneProvider(ABC):
    key: ClassVar[str]
    name: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]]

    def __init__(self, config: dict[str, Any] | None = None, secrets: dict[str, str] | None = None):
        self.config = dict(config or {})
        self.secrets = dict(secrets or {})

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def require(self, capability: Capability) -> None:
        if not self.supports(capability):
            raise CapabilityNotSupported(self.name, capability)

    async def connect(self) -> None:
        """Open whatever session the provider needs. Default: nothing to open."""

    async def disconnect(self) -> None:
        """Release it. Default: nothing to release."""

    @abstractmethod
    async def get_status(self, drone: DroneRef, now: datetime) -> DroneHealth:
        """Health of an aircraft on the ground or in the air — also its heartbeat."""

    async def get_position(self, drone: DroneRef, now: datetime) -> tuple[float, float, float] | None:
        self.require(Capability.POSITION)
        h = await self.get_status(drone, now)
        if h.latitude is None or h.longitude is None:
            return None
        return h.latitude, h.longitude, h.altitude_m or 0.0

    @abstractmethod
    async def start_mission(self, flight: FlightContext, now: datetime) -> FlightUpdate:
        """Upload the plan and launch."""

    @abstractmethod
    async def get_telemetry(self, flight: FlightContext, now: datetime) -> FlightUpdate:
        """Everything since the last call: samples, events, phase, outcome."""

    async def pause_mission(self, flight: FlightContext, now: datetime) -> FlightUpdate:
        self.require(Capability.PAUSE)
        raise NotImplementedError

    async def resume_mission(self, flight: FlightContext, now: datetime) -> FlightUpdate:
        self.require(Capability.RESUME)
        raise NotImplementedError

    async def abort_mission(self, flight: FlightContext, now: datetime) -> FlightUpdate:
        self.require(Capability.ABORT)
        raise NotImplementedError

    async def return_to_home(self, flight: FlightContext, now: datetime) -> FlightUpdate:
        self.require(Capability.RETURN_TO_HOME)
        raise NotImplementedError

    async def get_live_stream(self, drone: DroneRef) -> dict | None:
        """{url, protocol} of the aircraft's live video, for the camera row that
        represents it (decision D1)."""
        self.require(Capability.LIVE_STREAM)
        raise NotImplementedError

    async def get_recording(self, flight: FlightContext) -> dict | None:
        self.require(Capability.RECORDING)
        raise NotImplementedError

    async def capture_snapshot(self, drone: DroneRef, flight: FlightContext | None) -> bytes | None:
        self.require(Capability.SNAPSHOT)
        raise NotImplementedError
