"""The flight plan and the simulated drone — pure, pinned to known instants.

The simulator is what the whole drone module is proven against until real
hardware exists, so it is held to the behaviours a real autopilot has: it flies
the plan it was given, drains its battery, comes home when told, when its
battery says so, or when it loses its link, and lands where it is on a motor
fault. And it is deterministic — the same session always flies the same way.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services import drone_flight_plan as fp
from app.services import drone_provider_registry as registry
from app.services.drone_providers import (
    ADAPTERS, Capability, CapabilityNotSupported, DroneRef, FlightContext, get_adapter,
)
from app.services.drone_providers import simulator as sim

T0 = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
ROUTE = {"base_latitude": 1.3, "base_longitude": 103.8, "base_altitude_m": 0,
         "default_altitude_m": 40, "default_speed_mps": 8, "return_to_base": True}
WPS = [{"sequence": 1, "latitude": 1.3010, "longitude": 103.8000, "hover_seconds": 5},
       {"sequence": 2, "latitude": 1.3010, "longitude": 103.8010, "observe_seconds": 10},
       {"sequence": 3, "latitude": 1.3000, "longitude": 103.8010}]
PLAN = fp.build_plan(ROUTE, WPS)


def _fly(cfg: dict | None = None, *, sid: str = "session-normal", battery: float = 90,
         commands: dict[int, str] | None = None, tick_s: float = 2.0, max_ticks: int = 500):
    """Fly a whole mission tick by tick. Returns (update, phases, events, samples)."""
    p = get_adapter("simulator", cfg or {"speed_factor": 1})
    ctx = FlightContext(session_id=sid, tenant_id="t", drone=DroneRef(id="d", code="D-1", battery_level=battery),
                        plan=PLAN)

    async def go():
        up = await p.start_mission(ctx, T0)
        ctx.provider_state = up.provider_state
        phases, events, samples = [up.phase], [], list(up.samples)
        for i in range(1, max_ticks):
            now = T0 + timedelta(seconds=tick_s * i)
            cmd = (commands or {}).get(i)
            up = await (getattr(p, cmd) if cmd else p.get_telemetry)(ctx, now)
            ctx.provider_state = up.provider_state
            samples += up.samples
            events += up.events
            if up.phase != phases[-1]:
                phases.append(up.phase)
            if up.outcome:
                break
        return up, phases, events, samples
    return asyncio.run(go())


def _kinds(events):
    return [e.kind + (f":{e.waypoint}" if e.waypoint else "") for e in events]


def _sid_for(kind: str) -> str:
    """A session id whose injected failure is of this kind."""
    for i in range(1000):
        f = sim.planned_failure(f"sid-{i}", 1.0, 100.0)
        if f["kind"] == kind:
            return f"sid-{i}"
    raise AssertionError(kind)


# ─── The plan ────────────────────────────────────────────────────────────────

def test_the_estimate_is_the_timeline():
    est = fp.estimate(PLAN)
    segs = fp.timeline(PLAN)
    assert est.duration_s == pytest.approx(segs[-1].t1, abs=0.1)
    assert [s.kind for s in segs] == ["TAKEOFF", "LEG", "DWELL", "LEG", "DWELL", "LEG", "LEG", "LANDING"]
    assert est.battery_needed_pct == pytest.approx(est.duration_s * fp.DRAIN_PCT_PER_S, abs=0.1)


def test_without_return_to_base_it_lands_at_the_last_waypoint():
    segs = fp.timeline(fp.build_plan({**ROUTE, "return_to_base": False}, WPS))
    assert segs[-1].kind == "LANDING"
    assert segs[-1].end[:2] == (1.3000, 103.8010)


def test_position_is_interpolated_along_a_leg():
    segs = fp.timeline(PLAN)
    leg = next(s for s in segs if s.kind == "LEG")
    mid, seg = fp.position_at(segs, (leg.t0 + leg.t1) / 2)
    assert seg is leg
    assert mid[0] == pytest.approx((leg.start[0] + leg.end[0]) / 2)


def test_the_snapshot_is_json_and_keeps_numbers_as_numbers():
    """A float has .hex() too; the snapshot must not mistake it for a UUID."""
    import json
    from decimal import Decimal
    snap = fp.snapshot_config(
        mission={"id": "m", "name": "M", "min_battery_pct": 30}, route={**ROUTE, "default_altitude_m": Decimal("40.00")},
        waypoints=WPS, drone=None, profile=None, rules=[], zones=[], est=fp.estimate(PLAN))
    json.dumps(snap)
    assert snap["route"]["base_latitude"] == 1.3 and snap["route"]["default_altitude_m"] == 40.0


# ─── Flying ──────────────────────────────────────────────────────────────────

def test_a_normal_flight_visits_every_waypoint_in_order_and_completes():
    up, phases, events, samples = _fly()
    assert phases == ["LAUNCHING", "ACTIVE", "RETURNING", "LANDED"]
    assert up.outcome == "COMPLETED" and up.failure_code is None
    assert _kinds(events) == ["WAYPOINT_REACHED:1", "WAYPOINT_DEPARTED:1", "WAYPOINT_REACHED:2",
                              "WAYPOINT_DEPARTED:2", "WAYPOINT_REACHED:3", "WAYPOINT_DEPARTED:3", "LANDED"]
    times = [s.recorded_at for s in samples]
    assert times == sorted(times) and len(set(times)) == len(times), "samples out of order or duplicated"
    assert samples[-1].battery_pct < samples[0].battery_pct


def test_pausing_holds_position_and_delays_the_end():
    _, _, _, base = _fly()
    up, phases, events, samples = _fly(commands={10: "pause_mission", 20: "resume_mission"})
    assert "PAUSED" in phases and up.outcome == "COMPLETED"
    held = [s for s in samples if s.mission_state == "PAUSED"]
    assert held and len({(s.latitude, s.longitude) for s in held}) == 1, "it moved while paused"
    assert samples[-1].recorded_at - base[-1].recorded_at == timedelta(seconds=20)


def test_abort_brings_it_home_and_ends_aborted():
    up, phases, events, samples = _fly(commands={15: "abort_mission"})
    assert up.outcome == "ABORTED"
    assert "RETURN_STARTED" in _kinds(events)
    assert (samples[-1].latitude, samples[-1].longitude) == pytest.approx((1.3, 103.8))
    assert samples[-1].altitude_m == pytest.approx(0.0, abs=0.01)


def test_return_to_home_also_ends_aborted():
    up, _, _, _ = _fly(commands={12: "return_to_home"})
    assert up.outcome == "ABORTED"


def test_low_battery_mid_patrol_sends_it_home_and_the_mission_fails():
    """22% reaches the 20% return threshold ~40 s in — before waypoint 2 is
    done — so the site was not fully patrolled."""
    up, _, events, _ = _fly(battery=22)
    kinds = _kinds(events)
    assert "LOW_BATTERY" in kinds and "WAYPOINT_DEPARTED:3" not in kinds
    assert up.outcome == "FAILED" and up.failure_code == "LOW_BATTERY"


def test_low_battery_after_the_last_waypoint_still_completes_the_patrol():
    """24% reaches the threshold on the way home, every waypoint already done."""
    up, _, events, _ = _fly(battery=24)
    assert "WAYPOINT_DEPARTED:3" in _kinds(events)
    assert up.outcome == "COMPLETED" and up.failure_code == "LOW_BATTERY"


def test_a_lost_link_leaves_a_gap_and_it_comes_home_on_its_own():
    up, _, events, samples = _fly({"speed_factor": 1, "failure_rate": 1.0}, sid=_sid_for("COMMS_LOST"))
    assert "COMMS_LOST" in _kinds(events) and "COMMS_RESTORED" in _kinds(events)
    gaps = [(b.recorded_at - a.recorded_at).total_seconds() for a, b in zip(samples, samples[1:])]
    assert max(gaps) >= sim.COMMS_OUTAGE_S - 1, "no telemetry gap while the link was down"
    assert up.failure_code == "COMMS_LOST" and up.outcome in ("FAILED", "COMPLETED")


def test_a_motor_fault_lands_where_it_is():
    up, _, events, samples = _fly({"speed_factor": 1, "failure_rate": 1.0}, sid=_sid_for("MOTOR_FAULT"))
    assert up.outcome == "FAILED" and up.failure_code == "MOTOR_FAULT"
    assert samples[-1].altitude_m == pytest.approx(0.0, abs=0.01)
    assert (samples[-1].latitude, samples[-1].longitude) != pytest.approx((1.3, 103.8)), "it flew home"


def test_a_fault_after_every_waypoint_is_a_completed_patrol_with_the_fault_recorded():
    """The patrol happened; the fault is still reported. Calling this FAILED
    would say the site went unpatrolled when it did not."""
    for i in range(400):
        sid = f"late-{i}"
        f = sim.planned_failure(sid, 1.0, fp.estimate(PLAN).duration_s)
        if f["kind"] == "BATTERY_FAULT" and f["at"] > fp.timeline(PLAN)[-3].t0:
            break
    up, _, events, _ = _fly({"speed_factor": 1, "failure_rate": 1.0}, sid=sid)
    assert up.outcome == "COMPLETED" and up.failure_code == "BATTERY_FAULT"


def test_no_failure_rate_means_no_failure():
    assert sim.planned_failure("anything", 0.0, 100) is None


def test_the_same_session_fails_the_same_way_every_time():
    a = _fly({"speed_factor": 1, "failure_rate": 0.5}, sid="replay-me")
    b = _fly({"speed_factor": 1, "failure_rate": 0.5}, sid="replay-me")
    assert (a[0].outcome, a[0].failure_code, _kinds(a[2])) == (b[0].outcome, b[0].failure_code, _kinds(b[2]))


def test_one_long_gap_flies_the_same_mission_as_many_short_ticks():
    """A runner that restarts, or misses ticks, must not change where the drone
    went — only how many samples it reports."""
    short = _fly(tick_s=2.0)
    long_ = _fly(tick_s=500.0)
    assert long_[0].outcome == short[0].outcome == "COMPLETED"
    assert [k for k in _kinds(long_[2]) if k.startswith("WAYPOINT")] == \
           [k for k in _kinds(short[2]) if k.startswith("WAYPOINT")]
    assert len(long_[3]) <= sim.MAX_SAMPLES + 1


def test_advance_leaves_the_state_it_was_given_untouched():
    state = sim.new_state(PLAN, 90, T0, None)
    before = repr(state)
    sim.advance(PLAN, state, T0 + timedelta(seconds=60), 1.0)
    assert repr(state) == before


def test_a_docked_simulated_drone_recharges():
    p = get_adapter("simulator", {})
    d = DroneRef(id="d", code="D", status="CHARGING", battery_level=50, last_heartbeat_at=T0)
    h = asyncio.run(p.get_status(d, T0 + timedelta(minutes=10)))
    assert h.battery_level == pytest.approx(62.0) and h.status_hint == "CHARGING"
    assert h.communication_status == "OK"


# ─── Capabilities and the catalogue ──────────────────────────────────────────

def test_every_catalogued_provider_has_an_adapter_and_every_adapter_is_catalogued():
    """A provider an administrator can configure but nothing can fly — or one
    that flies but cannot be configured — is a trap."""
    assert set(registry.PROVIDERS) == set(ADAPTERS)


def test_a_capability_the_provider_lacks_is_refused_not_faked():
    p = get_adapter("simulator", {})
    assert not p.supports(Capability.SNAPSHOT)
    with pytest.raises(CapabilityNotSupported, match="cannot snapshot"):
        asyncio.run(p.capture_snapshot(DroneRef(id="d", code="D"), None))


def test_the_simulator_says_it_is_simulated():
    up = asyncio.run(get_adapter("simulator", {}).start_mission(
        FlightContext(session_id="abcdef123456", tenant_id="t", drone=DroneRef(id="d", code="D"), plan=PLAN), T0))
    assert up.provider_mission_ref == "sim-abcdef12"
