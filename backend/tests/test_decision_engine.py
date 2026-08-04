"""Vehicle access decision engine + barrier drivers.

Tests backend/app/services/decision_engine.py and app/services/barrier/.

WHY THIS FILE IS PURE (no DB, no network)
    `decide()` takes plain data and returns a verdict, and the simulator driver
    holds state in memory. That is deliberate: the logic deciding whether a
    barrier opens for a vehicle is the highest-consequence code in this feature,
    so it must be testable exhaustively and instantly, without a database being
    up or a gate controller being plugged in.

    The DB-backed wrapper (`evaluate_vehicle`) and the HTTP layer are covered in
    test_vms_barriers_api.py.

Sections:
  A — Decision precedence, one test per rule + the ordering that matters (12)
  B — Tenant-configurable thresholds (3)
  C — Barrier driver contract: simulator, capabilities, registry (7)
  D — Real drivers degrade correctly when the device is unreachable (2)
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.decision_engine import (
    AUTO_OPEN_CATEGORIES,
    BOOKING_CATEGORIES,
    Decision,
    VehicleFacts,
    decide,
)

TODAY = date(2026, 8, 2)


def _decide(**kw):
    """Build facts with sensible defaults so each test states only what it is
    actually about. Confidence defaults high so it never accidentally trips the
    low-confidence rule in a test that isn't about confidence."""
    facts = VehicleFacts(
        plate_number=kw.pop("plate", "SGA1234X"),
        category=kw.pop("category", "whitelist"),
        is_active=kw.pop("is_active", True),
        valid_from=kw.pop("valid_from", None),
        valid_to=kw.pop("valid_to", None),
        confidence=kw.pop("confidence", 0.99),
        has_valid_booking=kw.pop("has_valid_booking", False),
        owner_name=kw.pop("owner_name", None),
        company=kw.pop("company", None),
    )
    return decide(facts, today=TODAY, **kw)


# ── A. Decision precedence ────────────────────────────────────────────────

def test_blacklist_alarms():
    o = _decide(category="blacklist")
    assert o.decision is Decision.ALARM
    assert o.rule == "blacklisted"
    assert o.is_refusal


def test_blacklist_beats_everything_else():
    """A blacklisted vehicle is refused even when every other signal is
    'wrong' in a way that would normally produce a softer verdict. This is the
    single most important ordering guarantee in the engine — if a later rule
    ever short-circuits ahead of it, a barred vehicle gets in."""
    o = _decide(
        category="blacklist",
        is_active=False,               # would be inactive_registration
        valid_to=date(2020, 1, 1),     # would be expired
        confidence=0.01,               # would be low_confidence
    )
    assert o.decision is Decision.ALARM
    assert o.rule == "blacklisted"


def test_unregistered_plate_holds_for_operator():
    o = _decide(category=None)
    assert o.decision is Decision.REQUIRE_OPERATOR
    assert o.rule == "unknown_plate"


def test_deactivated_registration_holds_not_blocks():
    """Deactivation is an administrative state, not an accusation — it must not
    escalate to a refusal."""
    o = _decide(category="vip", is_active=False)
    assert o.decision is Decision.REQUIRE_OPERATOR
    assert o.rule == "inactive_registration"
    assert not o.is_refusal


def test_expired_registration():
    o = _decide(category="vip", valid_to=date(2026, 7, 1))
    assert o.rule == "expired_registration"
    assert "2026-07-01" in o.reason


def test_not_yet_valid_is_distinct_from_expired():
    """These look identical to the code but mean opposite things to whoever is
    standing at the gate: 'come back Monday' vs 'your pass lapsed'. An earlier
    revision collapsed both into 'expired (valid to n/a)'."""
    o = _decide(category="staff", valid_from=date(2026, 9, 1))
    assert o.rule == "not_yet_valid"
    assert "not valid until" in o.reason
    assert "2026-09-01" in o.reason


def test_validity_checked_before_confidence():
    """An expired pass is a fact about the vehicle; low confidence is a fact
    about the read. Reporting 'expired' is far more actionable to an operator,
    so it must win."""
    o = _decide(category="vip", valid_to=date(2020, 1, 1), confidence=0.10)
    assert o.rule == "expired_registration"


def test_watchlist_never_auto_opens():
    """The entire point of a watchlist is that a human looks. A perfect read of
    a fully valid watchlist vehicle must still hold."""
    o = _decide(category="watchlist", confidence=1.0)
    assert o.decision is Decision.REQUIRE_OPERATOR
    assert o.rule == "watchlist_hit"


def test_low_confidence_blocks_auto_open_even_for_vip():
    """A misread plate that happens to collide with a VIP entry must not open a
    gate on its own."""
    o = _decide(category="vip", confidence=0.42)
    assert o.decision is Decision.REQUIRE_OPERATOR
    assert o.rule == "low_confidence"


def test_visitor_with_booking_opens():
    o = _decide(category="visitor", has_valid_booking=True)
    assert o.decision is Decision.AUTO_OPEN
    assert o.rule == "booking_confirmed"


def test_visitor_without_booking_holds():
    o = _decide(category="contractor", has_valid_booking=False)
    assert o.decision is Decision.REQUIRE_OPERATOR
    assert o.rule == "booking_missing"


@pytest.mark.parametrize("category", sorted(AUTO_OPEN_CATEGORIES))
def test_every_standing_permission_category_opens(category):
    o = _decide(category=category, owner_name="R Tan")
    assert o.decision is Decision.AUTO_OPEN
    assert o.rule == "standing_permission"


def test_unhandled_category_fails_safe():
    """If a category is ever added to the schema without a matching rule, the
    engine must hold for a human rather than guess."""
    o = _decide(category="some_future_category")
    assert o.decision is Decision.REQUIRE_OPERATOR
    assert o.rule == "unhandled_category"


def test_no_barrier_on_lane_yields_allow_not_open():
    """Same permitted vehicle, but nothing to actuate — it is logged as allowed,
    not reported as an opening that never happened."""
    o = _decide(category="emergency", barrier_available=False)
    assert o.decision is Decision.AUTO_ALLOW
    assert not o.should_open_barrier


def test_missing_confidence_does_not_block():
    """A detection with no confidence figure (e.g. a manual trigger) must not be
    treated as a zero-confidence read."""
    o = _decide(category="staff", confidence=None)
    assert o.decision is Decision.AUTO_OPEN


def test_booking_categories_are_disjoint_from_standing_permission():
    """Guards the category tables themselves: a category in both sets would make
    rule 7 vs rule 8 order-dependent and the behaviour ambiguous."""
    assert not (AUTO_OPEN_CATEGORIES & BOOKING_CATEGORIES)


# ── B. Tenant-configurable thresholds ─────────────────────────────────────

def test_unknown_plate_action_is_tunable():
    o = _decide(category=None, settings={"access.unknown_plate_action": "block"})
    assert o.decision is Decision.BLOCK


def test_confidence_threshold_is_tunable():
    o = _decide(category="vip", confidence=0.42,
                settings={"access.auto_open_min_confidence": 0.30})
    assert o.decision is Decision.AUTO_OPEN


def test_expired_action_is_tunable():
    o = _decide(category="vip", valid_to=date(2020, 1, 1),
                settings={"access.expired_action": "block"})
    assert o.decision is Decision.BLOCK


# ── C. Barrier driver contract ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_simulator():
    from app.services.barrier.simulator import reset_simulator
    reset_simulator()
    yield
    reset_simulator()


def _sim(key: str):
    from app.services.barrier import BarrierConfig, get_driver
    return get_driver(BarrierConfig(vendor="simulator", device_key=key))


async def test_simulator_open_close_cycle():
    d = _sim("gate-a")
    assert (await d.status()).status == "closed"
    assert (await d.open()).status == "open"
    assert (await d.close()).status == "closed"


async def test_hold_survives_a_passing_vehicle():
    """A momentary open must not silently clear a standing hold, or the UI's
    hold indicator would lie the moment a car drove through."""
    d = _sim("gate-a")
    await d.hold_open()
    assert (await d.open()).status == "held_open"
    assert (await d.status()).status == "held_open"
    assert (await d.release_hold()).status == "closed"


async def test_two_barriers_keep_independent_state():
    a, b = _sim("gate-a"), _sim("gate-b")
    await a.open()
    assert (await a.status()).status == "open"
    assert (await b.status()).status == "closed"


async def test_driver_reports_failure_without_raising():
    from app.services.barrier.simulator import force_failure
    d = _sim("gate-a")
    force_failure("gate-a", "boom motor jam")
    r = await d.open()
    assert r.ok is False
    assert r.error == "boom motor jam"


async def test_unsupported_command_is_reported_not_faked():
    """Several Dahua barrier models genuinely cannot latch open. Claiming
    success would leave a control room believing a gate is held that isn't."""
    from app.services.barrier import BarrierConfig, get_driver, supports
    d = get_driver(BarrierConfig(vendor="dahua", host="192.0.2.1"))
    r = await d.hold_open()
    assert r.ok is False
    assert "not supported" in r.error
    assert supports("dahua", "hold_open") is False


def test_unknown_vendor_raises_rather_than_returning_a_result():
    """A bad vendor is a config error, not a device condition — it must not be
    squashed into the same shape as a runtime device failure."""
    from app.services.barrier import BarrierConfig, get_driver
    with pytest.raises(ValueError, match="unknown barrier vendor"):
        get_driver(BarrierConfig(vendor="acme9000"))


def test_every_vendor_has_a_capability_entry():
    """A vendor present in the driver registry but missing from the capability
    matrix would render an operator UI with no buttons at all."""
    from app.services.barrier import SUPPORTED_VENDORS, VENDOR_CAPABILITIES
    for vendor in SUPPORTED_VENDORS:
        assert VENDOR_CAPABILITIES.get(vendor), f"{vendor} has no capabilities"
        assert "open" in VENDOR_CAPABILITIES[vendor]


# ── D. Real drivers against an unreachable device ─────────────────────────

@pytest.mark.parametrize("vendor", ["hikvision", "dahua", "relay",
                                    "hikvision_camera_io", "dahua_camera_io"])
async def test_unreachable_device_degrades_structurally(vendor):
    """192.0.2.1 is TEST-NET-1 and is guaranteed unroutable. Every real driver
    must come back with a populated error instead of raising — a gate
    controller on someone else's VLAN times out as a matter of course."""
    from app.services.barrier import BarrierConfig, get_driver
    d = get_driver(BarrierConfig(
        vendor=vendor, host="192.0.2.1", username="u", password="p",
        relay_channel=1, pulse_ms=100, timeout=0.5,
    ))
    r = await d.open()
    assert r.ok is False
    assert r.error
    assert r.status == "error"


async def test_camera_io_never_claims_to_know_boom_position():
    """A relay contact tells you the circuit state, not whether the boom
    actually rose — it could be obstructed or still travelling."""
    from app.services.barrier import BarrierConfig, get_driver
    d = get_driver(BarrierConfig(vendor="hikvision_camera_io", host="192.0.2.1"))
    r = await d.status()
    assert r.status == "unknown"
