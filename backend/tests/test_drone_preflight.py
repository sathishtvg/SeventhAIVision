"""Pre-flight: every check, blocking or warning, with a reason a person can act on.

Pure. Each test starts from a mission that would fly and breaks one thing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import drone_flight_plan as fp
from app.services.drone_preflight import PreflightFacts, evaluate

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
PLAN = fp.build_plan({"base_latitude": 1.3, "base_longitude": 103.8, "default_speed_mps": 8},
                     [{"sequence": 1, "latitude": 1.301, "longitude": 103.8}])
EST = fp.estimate(PLAN)


def _facts(**over) -> PreflightFacts:
    base = dict(
        now=NOW, licence_problem=None,
        mission={"name": "Night", "enabled": True, "min_battery_pct": 30, "security_profile_id": "p",
                 "max_duration_minutes": None},
        site={"is_active": True},
        drone={"name": "Drone One", "status": "READY", "battery_level": 90, "gps_status": "OK",
               "communication_status": "OK", "camera_status": "OK", "storage_status": "OK",
               "last_heartbeat_at": NOW - timedelta(seconds=5), "heartbeat_timeout_seconds": 30,
               "next_maintenance_at": None, "edge_gateway_id": None},
        provider={"provider_key": "simulator", "is_active": True},
        provider_capabilities=frozenset({"MISSION", "ABORT"}),
        route={"is_active": True}, waypoint_count=1, profile={"name": "P", "is_active": True},
        estimate=EST,
    )
    for k, v in over.items():
        if k.startswith("drone__"):
            base["drone"] = {**base["drone"], k[7:]: v}
        elif k.startswith("mission__"):
            base["mission"] = {**base["mission"], k[9:]: v}
        else:
            base[k] = v
    return PreflightFacts(**base)


def _blocking(**over) -> list[str]:
    return [c["code"] for c in evaluate(_facts(**over)).blocking]


def test_a_healthy_mission_passes_every_check():
    r = evaluate(_facts())
    assert r.passed and r.blocking == [] and r.warnings == []
    assert r.reason() is None


def test_each_blocking_problem_is_named():
    cases = {
        "LICENCE": dict(licence_problem="Drone Patrol is not licensed."),
        "MISSION_ENABLED": dict(mission__enabled=False),
        "SITE": dict(site={"is_active": False}),
        "DRONE_ASSIGNED": dict(drone=None),
        "DRONE_ENABLED": dict(drone__status="MAINTENANCE"),
        "COMMUNICATION": dict(drone__last_heartbeat_at=NOW - timedelta(minutes=5)),
        "BATTERY": dict(drone__battery_level=25),
        "GPS": dict(drone__gps_status="FAULT"),
        "CAMERA": dict(drone__camera_status="UNKNOWN"),
        "STORAGE": dict(drone__storage_status="FAULT"),
        "MAINTENANCE": dict(drone__next_maintenance_at=NOW - timedelta(days=1)),
        "PROVIDER": dict(provider=None),
        "PROVIDER_MISSIONS": dict(provider_capabilities=frozenset()),
        "ROUTE": dict(waypoint_count=0),
        "SECURITY_PROFILE": dict(profile={"name": "P", "is_active": False}),
        "DURATION": dict(mission__max_duration_minutes=0.5),
    }
    for code, over in cases.items():
        assert code in _blocking(**over), f"{code} did not block: {_blocking(**over)}"


def test_a_drone_already_flying_is_not_available():
    r = evaluate(_facts(drone_in_flight=True))
    assert "DRONE_AVAILABLE" in [c["code"] for c in r.blocking]
    assert "already on a mission" in r.reason()


def test_a_charging_drone_may_fly_if_its_charge_is_enough():
    assert evaluate(_facts(drone__status="CHARGING")).passed
    assert "BATTERY" in _blocking(drone__status="CHARGING", drone__battery_level=22)


def test_battery_needs_the_estimate_plus_the_reserve():
    """The mission minimum is 30%, but a flight needing 40% must not go on 35."""
    long_est = fp.Estimate(duration_s=800, distance_m=0, battery_needed_pct=40.0)
    r = evaluate(_facts(estimate=long_est, drone__battery_level=55))
    reason = r.reason()
    assert not r.passed and "60%" in reason and "40% estimated" in reason


def test_a_drone_never_heard_from_says_so():
    r = evaluate(_facts(drone__last_heartbeat_at=None))
    assert "Nothing has been heard" in r.reason()


def test_an_offline_edge_gateway_blocks_a_drone_that_uses_one():
    assert "EDGE_GATEWAY" in _blocking(drone__edge_gateway_id="g", gateway={"is_active": True, "status": "OFFLINE"})
    assert "EDGE_GATEWAY" not in _blocking(drone__edge_gateway_id="g", gateway={"is_active": True, "status": "UNKNOWN"})


def test_warnings_do_not_block():
    r = evaluate(_facts(mission__security_profile_id=None, profile=None, outside_site=[2]))
    assert r.passed
    assert {c["code"] for c in r.warnings} == {"SECURITY_PROFILE", "ROUTE_GEOFENCE"}


def test_every_problem_is_reported_at_once():
    r = evaluate(_facts(drone__battery_level=5, drone__gps_status="FAULT", licence_problem="No licence."))
    assert {"BATTERY", "GPS", "LICENCE"} <= {c["code"] for c in r.blocking}
    j = r.as_json()
    assert j["passed"] is False and set(j["blocking"]) >= {"BATTERY", "GPS", "LICENCE"}
