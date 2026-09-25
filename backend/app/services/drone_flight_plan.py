"""A mission, turned into a flight: the path, the timeline, and what it will cost.

Two uses. Pre-flight asks "how long will this take and how much battery will it
need" before anything leaves the ground. The simulator flies the same timeline.
Both read one model so the estimate pre-flight gives and the flight the simulator
flies cannot disagree.

THE KINEMATICS ARE A PLANNING MODEL, NOT A FLIGHT CONTROLLER. Straight legs at
the waypoint (or route default) speed, a vertical climb and descent, a hover for
each waypoint's dwell. A real aircraft's own planner decides how it actually
flies; this decides whether a mission is plausible and what the operator is told
to expect. The constants are deliberately conservative.

PURE. Plan-building takes plain dicts, so the session's configuration snapshot is
built from exactly what was loaded, and tests need no database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from app.services.geofence import haversine_meters

CLIMB_MPS = 3.0
DESCENT_MPS = 2.0
#: Battery drain while airborne, percent per second. ~33 minutes from full,
#: typical of an enterprise quadcopter; hover costs nearly as much as cruise.
DRAIN_PCT_PER_S = 0.05
#: Reserve pre-flight insists on beyond the estimated need.
RESERVE_PCT = 20.0
MIN_LEG_SECONDS = 1.0


@dataclass(frozen=True)
class PlanPoint:
    sequence: int              # 0 is the base
    lat: float
    lng: float
    alt_m: float
    speed_mps: float
    dwell_s: float = 0.0
    name: str | None = None
    snapshot_required: bool = False
    zone_id: str | None = None


@dataclass(frozen=True)
class MissionPlan:
    base: PlanPoint
    waypoints: tuple[PlanPoint, ...]
    return_to_base: bool = True
    default_alt_m: float = 40.0
    default_speed_mps: float = 5.0


@dataclass(frozen=True)
class Segment:
    kind: str                  # TAKEOFF | LEG | DWELL | LANDING
    t0: float
    t1: float
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    waypoint: int | None = None    # the waypoint a LEG flies to, or a DWELL hovers at
    speed_mps: float = 0.0

    @property
    def duration(self) -> float:
        return self.t1 - self.t0


@dataclass(frozen=True)
class Estimate:
    duration_s: float
    distance_m: float
    battery_needed_pct: float
    segments: tuple[Segment, ...] = field(default_factory=tuple)


def _f(v, default: float) -> float:
    return float(v) if v is not None else default


def jsonable(v):
    """A value as JSONB can store it. Checked by type, not by duck-typing — a
    float has a .hex() method too, and would otherwise be taken for a UUID."""
    import uuid as _uuid
    from datetime import date as _date, time as _time
    from decimal import Decimal
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, _uuid.UUID):
        return str(v)
    if isinstance(v, (_date, _time)):          # datetime is a date
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    return str(v)


def build_plan(route: dict, waypoints: Sequence[dict]) -> MissionPlan:
    """From a drone_routes row and its drone_waypoints rows (in sequence order)."""
    alt = _f(route.get("default_altitude_m"), 40.0)
    speed = _f(route.get("default_speed_mps"), 5.0)
    base = PlanPoint(0, float(route["base_latitude"]), float(route["base_longitude"]),
                     _f(route.get("base_altitude_m"), 0.0), speed)
    pts = tuple(
        PlanPoint(
            sequence=int(w["sequence"]), lat=float(w["latitude"]), lng=float(w["longitude"]),
            alt_m=_f(w.get("altitude_m"), alt), speed_mps=_f(w.get("speed_mps"), speed),
            dwell_s=float(w.get("hover_seconds") or 0) + float(w.get("observe_seconds") or 0),
            name=w.get("name"), snapshot_required=bool(w.get("snapshot_required")),
            zone_id=str(w["security_zone_id"]) if w.get("security_zone_id") else None,
        )
        for w in sorted(waypoints, key=lambda w: int(w["sequence"]))
    )
    return MissionPlan(base=base, waypoints=pts, return_to_base=bool(route.get("return_to_base", True)),
                       default_alt_m=alt, default_speed_mps=speed)


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return haversine_meters(a[0], a[1], b[0], b[1])


def legs_to(start: tuple[float, float, float], targets: Sequence[PlanPoint], t0: float,
            *, land: bool, ground_alt: float) -> list[Segment]:
    """Legs from `start` through each target (with its dwell), then an optional
    descent to `ground_alt`. Shared by the mission and by a return-to-home."""
    segs: list[Segment] = []
    t, pos = t0, start
    for p in targets:
        dest = (p.lat, p.lng, p.alt_m)
        dur = max(MIN_LEG_SECONDS, _dist(pos, dest) / max(p.speed_mps, 0.1))
        segs.append(Segment("LEG", t, t + dur, pos, dest, p.sequence, p.speed_mps))
        t, pos = t + dur, dest
        if p.dwell_s > 0:
            segs.append(Segment("DWELL", t, t + p.dwell_s, pos, pos, p.sequence))
            t += p.dwell_s
    if land:
        ground = (pos[0], pos[1], ground_alt)
        dur = max(MIN_LEG_SECONDS, max(pos[2] - ground_alt, 0.0) / DESCENT_MPS)
        segs.append(Segment("LANDING", t, t + dur, pos, ground))
    return segs


def timeline(plan: MissionPlan) -> list[Segment]:
    """Take off at the base, fly each waypoint in order, return and land (or
    land at the last waypoint when return_to_base is off)."""
    b = plan.base
    cruise = plan.waypoints[0].alt_m if plan.waypoints else plan.default_alt_m
    climb = max(MIN_LEG_SECONDS, max(cruise - b.alt_m, 0.0) / CLIMB_MPS)
    segs = [Segment("TAKEOFF", 0.0, climb, (b.lat, b.lng, b.alt_m), (b.lat, b.lng, cruise))]
    targets = list(plan.waypoints)
    if plan.return_to_base:
        targets.append(PlanPoint(0, b.lat, b.lng, cruise, plan.default_speed_mps))
    segs += legs_to(segs[-1].end, targets, segs[-1].t1, land=True, ground_alt=b.alt_m)
    return segs


def estimate(plan: MissionPlan) -> Estimate:
    segs = timeline(plan)
    duration = segs[-1].t1 if segs else 0.0
    distance = sum(_dist(s.start, s.end) for s in segs if s.kind == "LEG")
    return Estimate(duration_s=round(duration, 1), distance_m=round(distance, 1),
                    battery_needed_pct=round(duration * DRAIN_PCT_PER_S, 1), segments=tuple(segs))


def position_at(segs: Sequence[Segment], t: float) -> tuple[tuple[float, float, float], Segment]:
    """Where the aircraft is at time t on this timeline, and the segment it is in.
    Linear interpolation is exact enough over the few kilometres of a site."""
    if not segs:
        raise ValueError("empty timeline")
    if t <= segs[0].t0:
        return segs[0].start, segs[0]
    for s in segs:
        if t <= s.t1:
            f = 0.0 if s.duration <= 0 else (t - s.t0) / s.duration
            pos = tuple(a + (b - a) * f for a, b in zip(s.start, s.end))
            return pos, s  # type: ignore[return-value]
    return segs[-1].end, segs[-1]


def heading_deg(a: tuple[float, float, float], b: tuple[float, float, float]) -> float | None:
    """Initial bearing from a to b, 0–360, or None if they are the same point."""
    import math
    if (a[0], a[1]) == (b[0], b[1]):
        return None
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    x = math.sin(dl) * math.cos(la2)
    y = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def snapshot_config(*, mission: dict, route: dict, waypoints: Sequence[dict], drone: dict | None,
                    profile: dict | None, rules: Sequence[dict], zones: Sequence[dict],
                    est: Estimate) -> dict:
    """The configuration a session is frozen with. Everything later stages read
    about "what this flight was" comes from here, never from the live tables."""
    def clean(d: dict | None, keys: Sequence[str]) -> dict | None:
        if d is None:
            return None
        return {k: jsonable(d[k]) for k in keys if k in d}

    return {
        "mission": clean(mission, ("id", "name", "site_id", "priority", "min_battery_pct",
                                   "max_duration_minutes", "recording_sync_mode")),
        "route": clean(route, ("id", "name", "base_latitude", "base_longitude", "base_altitude_m",
                               "default_altitude_m", "default_speed_mps", "return_to_base", "is_active")),
        "waypoints": [clean(w, ("sequence", "name", "latitude", "longitude", "altitude_m", "speed_mps",
                                "hover_seconds", "observe_seconds", "gimbal_pitch_deg", "gimbal_yaw_deg",
                                "zoom", "snapshot_required", "security_zone_id", "security_profile_id"))
                      for w in waypoints],
        "drone": clean(drone, ("id", "name", "code", "provider_config_id", "provider_key")),
        "profile": clean(profile, ("id", "name", "min_confidence", "verify_min_seconds")),
        "rules": [clean(r, ("module_type", "is_enabled", "min_confidence", "base_severity",
                            "incident_risk_level")) for r in rules],
        "zones": [clean(z, ("id", "name", "zone_type", "shape", "polygon", "center_latitude",
                            "center_longitude", "radius_m", "severity", "active_from", "active_to",
                            "active_weekdays", "alert_policy", "detection_threshold")) for z in zones],
        "estimate": {"duration_s": est.duration_s, "distance_m": est.distance_m,
                     "battery_needed_pct": est.battery_needed_pct},
    }
