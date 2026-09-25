"""The simulated drone: a whole patrol, with no aircraft.

It exists so the complete module — scheduling, pre-flight, launch, telemetry,
waypoints, pause, abort, return home, failures, alerts — can be built and tested
without hardware. It NEVER commands a real aircraft, and everything it produces
is labelled simulated by its provider (the catalogue says so, and so does the
mission reference it hands out, "sim-…").

DETERMINISTIC AND RESTART-SAFE. All of its state lives in the session's
provider_state; each call advances that state from the last real timestamp to
`now`. A runner that restarts, or skips ticks, simply advances further next time.
Injected failures are decided by a hash of the session id, so a flight that
failed in a test fails the same way, at the same point, every time it is replayed.

THE SAFE BEHAVIOURS MIRROR REAL AUTOPILOTS. Low battery → return home. Lost
link → return home on its own, and report again when the link is back. Motor
fault → land where it is. Abort and return-to-home both bring it back to base.
The outcome says what happened: COMPLETED, ABORTED (an operator ended it) or
FAILED (it could not finish, and why).
"""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timedelta
from typing import Any

from app.services.drone_flight_plan import (
    DRAIN_PCT_PER_S, MissionPlan, PlanPoint, Segment, heading_deg, legs_to, position_at, timeline,
)
from app.services.drone_providers.base import (
    Capability, DroneHealth, DroneProvider, DroneRef, FlightContext, FlightEvent, FlightUpdate,
    TelemetrySample,
)

#: Simulated seconds between telemetry samples.
SAMPLE_EVERY_S = 1.0
#: However long the gap since the last call, at most this many samples come back.
MAX_SAMPLES = 120
LOW_BATTERY_RETURN_PCT = 20.0
COMMS_OUTAGE_S = 20.0
#: Recharge while docked, percent per real second (~80 minutes empty to full).
DOCK_RECHARGE_PCT_PER_S = 0.02

FAILURE_KINDS = ("COMMS_LOST", "MOTOR_FAULT", "BATTERY_FAULT")
FAILURE_TEXT = {
    "LOW_BATTERY": "Battery reached the return threshold; the drone returned home.",
    "COMMS_LOST": "The link to the drone was lost; it returned home on its own.",
    "BATTERY_FAULT": "A battery cell fault forced an early return.",
    "MOTOR_FAULT": "A motor fault forced an emergency landing where the drone was.",
}


def planned_failure(session_id: str, failure_rate: float, duration_s: float) -> dict | None:
    """Whether (and how, and when) this flight fails — from its id, so the same
    session always does the same thing."""
    if failure_rate <= 0:
        return None
    h = hashlib.sha256(session_id.encode("utf-8")).digest()
    u_fail = int.from_bytes(h[0:4], "big") / 2**32
    if u_fail >= failure_rate:
        return None
    u_when = int.from_bytes(h[4:8], "big") / 2**32
    return {"kind": FAILURE_KINDS[h[8] % len(FAILURE_KINDS)],
            "at": round(duration_s * (0.2 + 0.6 * u_when), 1)}


def _iso(t: datetime) -> str:
    return t.isoformat()


def _path(plan: MissionPlan, state: dict) -> list[Segment]:
    """The segments the drone is flying right now: the mission, a return to base
    from wherever it turned back, or an emergency descent."""
    if state["path"] == "MISSION":
        return timeline(plan)
    frm = tuple(state["return"]["from"])
    if state["path"] == "EMERGENCY_LAND":
        # Straight down where it is, to the site's ground level.
        return legs_to(frm, [], 0.0, land=True, ground_alt=plan.base.alt_m)
    cruise = max(frm[2], plan.default_alt_m)
    home = PlanPoint(0, plan.base.lat, plan.base.lng, cruise, plan.default_speed_mps)
    return legs_to(frm, [home], 0.0, land=True, ground_alt=plan.base.alt_m)


def _phase(state: dict, seg: Segment) -> str:
    if state.get("landed"):
        return "LANDED"
    if state.get("paused"):
        return "PAUSED"
    if state["path"] != "MISSION":
        return "RETURNING"
    if seg.kind == "TAKEOFF":
        return "LAUNCHING"
    if seg.kind == "LANDING" or (seg.kind == "LEG" and seg.waypoint == 0):
        return "RETURNING"
    return "ACTIVE"


def new_state(plan: MissionPlan, battery: float, now: datetime, failure: dict | None) -> dict:
    return {
        "v": 1, "path": "MISSION", "clock": 0.0, "air_s": 0.0, "battery0": float(battery),
        "paused": False, "landed": False, "last_real": _iso(now),
        "reached": [], "departed": [], "failure": failure, "failure_fired": False,
        "comms_until_air": None, "return": None, "outcome": None,
        "failure_code": None, "failure_reason": None, "last_emit_air": None,
    }


def battery_of(state: dict) -> float:
    return max(0.0, state["battery0"] - state["air_s"] * DRAIN_PCT_PER_S)


def _begin_return(plan: MissionPlan, state: dict, pos, reason: str) -> None:
    state["path"] = "RETURN"
    state["clock"] = 0.0
    state["paused"] = False
    state["return"] = {"from": list(pos), "reason": reason}


def advance(plan: MissionPlan, state: dict, now: datetime, speed_factor: float,
            ) -> tuple[dict, list[TelemetrySample], list[FlightEvent]]:
    """Move the flight from its last real timestamp to `now`. Returns a new state;
    the one passed in is left untouched (a deep copy, since it holds lists)."""
    state = copy.deepcopy(state)
    last = datetime.fromisoformat(state["last_real"])
    dt_real = max(0.0, (now - last).total_seconds())
    state["last_real"] = _iso(now)
    samples: list[TelemetrySample] = []
    events: list[FlightEvent] = []
    if state["landed"] or dt_real == 0:
        return state, samples, events

    dt = dt_real * speed_factor
    steps = max(1, int(dt / SAMPLE_EVERY_S))
    step = dt / steps
    # Emit at most MAX_SAMPLES, evenly, however long the gap was.
    emit_every = max(1, -(-steps // MAX_SAMPLES))

    def real_at(i: int) -> datetime:
        return last + timedelta(seconds=(i * step) / speed_factor)

    # Built once, and again only when the path changes (a return or a fault).
    segs = _path(plan, state)
    for i in range(1, steps + 1):
        when = real_at(i)
        state["air_s"] += step
        if not state["paused"]:
            state["clock"] += step

        # An injected failure, when the mission reaches its moment.
        f = state.get("failure")
        if (f and not state["failure_fired"] and state["path"] == "MISSION"
                and state["clock"] >= f["at"]):
            state["failure_fired"] = True
            pos, _ = position_at(segs, state["clock"])
            events.append(FlightEvent("FAULT" if f["kind"] != "COMMS_LOST" else "COMMS_LOST",
                                      when, None, FAILURE_TEXT[f["kind"]]))
            state["failure_code"] = f["kind"]
            state["failure_reason"] = FAILURE_TEXT[f["kind"]]
            if f["kind"] == "MOTOR_FAULT":
                state["path"], state["clock"], state["paused"] = "EMERGENCY_LAND", 0.0, False
                state["return"] = {"from": list(pos), "reason": "MOTOR_FAULT"}
            elif f["kind"] == "COMMS_LOST":
                state["comms_until_air"] = state["air_s"] + COMMS_OUTAGE_S
                _begin_return(plan, state, pos, "COMMS_LOST")
            else:  # BATTERY_FAULT: a cell drops the pack to the return threshold
                state["battery0"] -= max(0.0, battery_of(state) - LOW_BATTERY_RETURN_PCT)
            segs = _path(plan, state)

        # Low battery sends it home, whatever it was doing.
        if (state["path"] == "MISSION" and battery_of(state) <= LOW_BATTERY_RETURN_PCT):
            pos, _ = position_at(segs, state["clock"])
            events.append(FlightEvent("LOW_BATTERY", when, None,
                                      f"Battery at {battery_of(state):.0f}%; returning home."))
            if not state["failure_code"]:
                state["failure_code"] = "LOW_BATTERY"
                state["failure_reason"] = FAILURE_TEXT["LOW_BATTERY"]
            _begin_return(plan, state, pos, state["failure_code"])
            segs = _path(plan, state)

        if state["comms_until_air"] is not None and state["air_s"] >= state["comms_until_air"]:
            state["comms_until_air"] = None
            events.append(FlightEvent("COMMS_RESTORED", when, None, "Link to the drone restored."))

        pos, seg = position_at(segs, state["clock"])

        # Waypoints passed on the mission path.
        if state["path"] == "MISSION":
            for s in segs:
                if s.t1 > state["clock"] or not s.waypoint:
                    continue
                wp = s.waypoint
                if s.kind == "LEG" and wp not in state["reached"]:
                    state["reached"].append(wp)
                    events.append(FlightEvent("WAYPOINT_REACHED", when, wp))
                    has_dwell = any(x.kind == "DWELL" and x.waypoint == wp for x in segs)
                    if not has_dwell and wp not in state["departed"]:
                        state["departed"].append(wp)
                        events.append(FlightEvent("WAYPOINT_DEPARTED", when, wp))
                elif s.kind == "DWELL" and wp not in state["departed"]:
                    state["departed"].append(wp)
                    events.append(FlightEvent("WAYPOINT_DEPARTED", when, wp))

        landed = state["clock"] >= segs[-1].t1
        comms_down = state["comms_until_air"] is not None
        if not comms_down and (i % emit_every == 0 or i == steps or landed):
            samples.append(TelemetrySample(
                recorded_at=when, latitude=pos[0], longitude=pos[1], altitude_m=round(pos[2], 2),
                heading_deg=heading_deg(seg.start, seg.end) if seg.kind == "LEG" else None,
                speed_mps=0.0 if (state["paused"] or seg.kind != "LEG") else seg.speed_mps,
                battery_pct=round(battery_of(state), 1), mission_state=_phase(state, seg),
                waypoint_sequence=seg.waypoint if seg.waypoint else None,
                signal_quality=None if comms_down else 95,
            ))

        if landed:
            state["landed"] = True
            reason = (state.get("return") or {}).get("reason")
            patrolled = all(w.sequence in state["departed"] for w in plan.waypoints)
            if state["path"] == "MISSION":
                state["outcome"] = "COMPLETED"
            elif reason in ("ABORT", "RETURN_TO_HOME"):
                state["outcome"] = "ABORTED"
            elif patrolled and state["path"] == "RETURN":
                # Every waypoint was already patrolled when the fault sent it
                # home: the patrol is complete. failure_code stays set, so the
                # fault itself is still reported.
                state["outcome"] = "COMPLETED"
            else:
                state["outcome"] = "FAILED"
            events.append(FlightEvent("LANDED", when, None, state["outcome"]))
            break
    return state, samples, events


class SimulatorProvider(DroneProvider):
    key = "simulator"
    name = "Simulator"
    capabilities = frozenset({
        Capability.HEALTH, Capability.TELEMETRY, Capability.POSITION, Capability.MISSION,
        Capability.PAUSE, Capability.RESUME, Capability.ABORT, Capability.RETURN_TO_HOME,
    })

    @property
    def speed_factor(self) -> float:
        return float(self.config.get("speed_factor") or 1.0)

    @property
    def failure_rate(self) -> float:
        return float(self.config.get("failure_rate") or 0.0)

    async def get_status(self, drone: DroneRef, now: datetime) -> DroneHealth:
        """A docked simulated drone is healthy and charging back up."""
        battery = 100.0 if drone.battery_level is None else float(drone.battery_level)
        if drone.last_heartbeat_at is not None and drone.status not in ("MISSION_ACTIVE", "RETURNING"):
            gap = max(0.0, (now - drone.last_heartbeat_at).total_seconds())
            battery = min(100.0, battery + gap * DOCK_RECHARGE_PCT_PER_S)
        return DroneHealth(
            observed_at=now, battery_level=round(battery, 1), gps_status="OK",
            communication_status="OK", camera_status="OK", storage_status="OK",
            battery_health=100.0, temperature_c=31.0, latitude=drone.latitude,
            longitude=drone.longitude, altitude_m=0.0,
            status_hint="READY" if battery >= 95 else "CHARGING",
        )

    def _update(self, state: dict, plan: MissionPlan, samples, events) -> FlightUpdate:
        segs = _path(plan, state)
        _, seg = position_at(segs, state["clock"])
        return FlightUpdate(
            provider_state=state, phase=_phase(state, seg), samples=list(samples), events=list(events),
            outcome=state.get("outcome"), failure_code=state.get("failure_code"),
            failure_reason=state.get("failure_reason"),
        )

    async def start_mission(self, flight: FlightContext, now: datetime) -> FlightUpdate:
        self.require(Capability.MISSION)
        segs = timeline(flight.plan)
        battery = 100.0 if flight.drone.battery_level is None else float(flight.drone.battery_level)
        state = new_state(flight.plan, battery, now,
                          planned_failure(flight.session_id, self.failure_rate, segs[-1].t1))
        b = flight.plan.base
        first = TelemetrySample(recorded_at=now, latitude=b.lat, longitude=b.lng, altitude_m=b.alt_m,
                                heading_deg=None, speed_mps=0.0, battery_pct=battery,
                                mission_state="LAUNCHING", signal_quality=95)
        up = self._update(state, flight.plan, [first], [])
        up.provider_mission_ref = f"sim-{flight.session_id[:8]}"
        return up

    async def get_telemetry(self, flight: FlightContext, now: datetime) -> FlightUpdate:
        state, samples, events = advance(flight.plan, flight.provider_state, now, self.speed_factor)
        return self._update(state, flight.plan, samples, events)

    async def _command(self, flight: FlightContext, now: datetime, apply) -> FlightUpdate:
        # Bring the flight up to now first, so the command acts where the drone
        # actually is.
        state, samples, events = advance(flight.plan, flight.provider_state, now, self.speed_factor)
        apply(state, events)
        return self._update(state, flight.plan, samples, events)

    async def pause_mission(self, flight: FlightContext, now: datetime) -> FlightUpdate:
        self.require(Capability.PAUSE)

        def apply(state, events):
            if not state["landed"] and state["path"] == "MISSION" and not state["paused"]:
                state["paused"] = True
                events.append(FlightEvent("PAUSED", now, None, "Holding position."))
        return await self._command(flight, now, apply)

    async def resume_mission(self, flight: FlightContext, now: datetime) -> FlightUpdate:
        self.require(Capability.RESUME)

        def apply(state, events):
            if state["paused"]:
                state["paused"] = False
                events.append(FlightEvent("RESUMED", now, None, "Mission resumed."))
        return await self._command(flight, now, apply)

    async def _return(self, flight: FlightContext, now: datetime, reason: str) -> FlightUpdate:
        plan = flight.plan

        def apply(state, events):
            if state["landed"] or state["path"] != "MISSION":
                return  # already on its way home, or down: nothing further to do
            pos, _ = position_at(_path(plan, state), state["clock"])
            _begin_return(plan, state, pos, reason)
            events.append(FlightEvent("RETURN_STARTED", now, None,
                                      "Aborted by operator." if reason == "ABORT"
                                      else "Returning home on operator's command."))
        return await self._command(flight, now, apply)

    async def abort_mission(self, flight: FlightContext, now: datetime) -> FlightUpdate:
        self.require(Capability.ABORT)
        return await self._return(flight, now, "ABORT")

    async def return_to_home(self, flight: FlightContext, now: datetime) -> FlightUpdate:
        self.require(Capability.RETURN_TO_HOME)
        return await self._return(flight, now, "RETURN_TO_HOME")
