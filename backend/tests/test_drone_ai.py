"""Drone patrol, phase 6: the context and risk rules — pure, one rule at a time.

The two examples in the brief are pinned exactly: a person in a restricted zone
at 02:17 with no authorised shift is HIGH risk; a person in a normal zone by day
with an authorised guard on duty is LOW.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services import drone_ai as ai

SGT = ZoneInfo("Asia/Singapore")
NIGHT = datetime(2026, 9, 25, 2, 17, tzinfo=SGT)
DAY = datetime(2026, 9, 25, 14, 0, tzinfo=SGT)          # a Friday
SQUARE = [[1.3000, 103.8000], [1.3000, 103.8010], [1.3010, 103.8010], [1.3010, 103.8000]]


def _zone(**over) -> dict:
    return {"id": "z", "name": "Loading Bay", "zone_type": "RESTRICTED", "shape": "POLYGON", "polygon": SQUARE,
            "severity": "medium", "alert_policy": "ALERT", "is_active": True, **over}


def _person(conf: float = 0.85, module: str = "intrusion", **over) -> ai.Sighting:
    return ai.Sighting(module_type=module, detected_at=NIGHT, confidence=conf, latitude=1.3005,
                       longitude=103.8005, **over)


def _risk(s: ai.Sighting, when: datetime, zone: dict | None, **situation) -> ai.Risk:
    rule = ai.rule_for(None, None, s.module_type)
    return ai.assess(s, rule, ai.Situation(local_time=when, zone=zone, **situation))


# ─── The brief's own examples ────────────────────────────────────────────────

def test_person_in_a_restricted_zone_at_2_17_with_no_authorised_shift_is_high_risk():
    r = _risk(_person(), NIGHT, _zone(), verified=True, detection_count=4, observed_seconds=12)
    assert r.level == "HIGH" and r.authorisation == "UNAUTHORISED"
    assert {f["factor"] for f in r.factors} >= {"DETECTION", "ZONE", "AUTHORISATION", "TIME", "VERIFICATION"}


def test_person_in_a_normal_zone_by_day_with_a_guard_on_duty_is_low_risk():
    r = _risk(_person(), DAY, _zone(zone_type="NORMAL"), on_duty=[{"user_id": "g1", "role_id": 5}],
              verified=True, detection_count=4, observed_seconds=12)
    assert r.level == "LOW" and r.authorisation == "ON_DUTY"


def test_ai_confidence_is_never_the_risk_score():
    """A certain detection of something harmless is low risk; the confidence
    itself is left exactly as the worker gave it."""
    s = _person(conf=0.99)
    r = _risk(s, DAY, _zone(zone_type="NORMAL"), on_duty=[{"user_id": "g", "role_id": 5}],
              verified=True, detection_count=3, observed_seconds=5)
    assert s.confidence == 0.99 and r.level in ("INFO", "LOW")


# ─── Zones ───────────────────────────────────────────────────────────────────

def test_a_zone_is_in_force_only_in_its_hours_and_days_including_overnight():
    overnight = _zone(active_from=time(22, 0), active_to=time(6, 0))
    assert ai.zone_active(overnight, NIGHT) and not ai.zone_active(overnight, DAY)
    weekdays = _zone(active_weekdays=[0, 1, 2, 3])                     # Mon–Thu
    assert not ai.zone_active(weekdays, DAY)                           # Friday
    assert ai.zone_active(_zone(active_from="08:00:00", active_to="18:00:00"), DAY)


def test_the_most_serious_active_zone_wins_and_one_off_hours_does_not_count():
    zones = [_zone(id="n", zone_type="NORMAL"), _zone(id="c", zone_type="CRITICAL"),
             _zone(id="x", zone_type="NO_ENTRY", active_from=time(8), active_to=time(9))]
    assert ai.zone_at(zones, 1.3005, 103.8005, DAY)["id"] == "c"
    assert ai.zone_at(zones, 1.3100, 103.8100, DAY) is None
    assert ai.zone_at(zones, None, None, DAY) is None


def test_a_person_restricted_zone_says_nothing_about_vehicles():
    z = _zone(zone_type="PERSON_RESTRICTED")
    assert ai.zone_applies(z, "intrusion") and not ai.zone_applies(z, "lpr")
    assert ai.zone_applies(_zone(zone_type="VEHICLE_RESTRICTED"), "lpr")


# ─── What becomes an event at all ────────────────────────────────────────────

def test_the_strictest_confidence_threshold_applies():
    rule = {"module_type": "intrusion", "is_enabled": True, "min_confidence": 0.6}
    profile = {"min_confidence": 0.5}
    assert ai.min_confidence(rule, profile, _zone(detection_threshold=0.8)) == 0.8
    assert ai.ignore_reason(_person(conf=0.7), rule, profile, _zone(detection_threshold=0.8))
    assert ai.ignore_reason(_person(conf=0.85), rule, profile, _zone(detection_threshold=0.8)) is None


def test_a_module_the_profile_does_not_look_for_is_ignored_and_no_profile_looks_for_everything():
    rules = [{"module_type": "lpr", "is_enabled": True}, {"module_type": "face", "is_enabled": False}]
    profile = {"name": "Vehicles only"}
    assert ai.rule_for(rules, profile, "lpr") is not None
    assert ai.rule_for(rules, profile, "face") is None
    assert ai.rule_for(rules, profile, "intrusion") is None
    assert ai.rule_for(None, None, "intrusion")["base_severity"] == "low"


@pytest.mark.parametrize("module", ["tampering", "abandoned", "behavior"])
def test_modules_unreliable_on_a_moving_camera_are_ignored(module):
    s = ai.Sighting(module_type=module, detected_at=DAY, confidence=0.99)
    assert "moving camera" in ai.ignore_reason(s, ai.rule_for(None, None, module), None, None)


def test_every_existing_module_has_a_stated_suitability():
    from app.services.drone_edge_wire import AiModuleName
    import typing
    assert set(ai.SUITABILITY) == set(typing.get_args(AiModuleName)) == set(ai.DEFAULT_BASE_SEVERITY)


# ─── Authorisation ───────────────────────────────────────────────────────────

def test_vehicle_authorisation():
    plate = lambda p, wl=None: ai.Sighting(module_type="lpr", detected_at=DAY, confidence=0.9, label=p,  # noqa: E731
                                           watchlist=wl)
    zone = _zone(zone_type="VEHICLE_RESTRICTED", allowed_vehicle_plates=["SBA1234A"])
    assert ai.authorisation(plate("SBA 1234 A"), zone, [])[0] == "AUTHORISED"
    assert ai.authorisation(plate("SGX9999Z"), zone, [])[0] == "UNAUTHORISED"
    assert ai.authorisation(plate("SGX9999Z", "block"), None, [])[0] == "BLOCKLISTED"
    assert ai.authorisation(plate("SGX9999Z", "allow"), zone, [])[0] == "AUTHORISED"


def test_person_authorisation_follows_who_is_on_shift():
    zone = _zone(allowed_role_ids=[5], allowed_user_ids=["u-9"])
    assert ai.authorisation(_person(), zone, [{"user_id": "g1", "role_id": 5}])[0] == "ON_DUTY"
    assert ai.authorisation(_person(), zone, [{"user_id": "u-9", "role_id": 7}])[0] == "ON_DUTY"
    assert ai.authorisation(_person(), zone, [{"user_id": "g1", "role_id": 7}])[0] == "UNAUTHORISED"
    assert ai.authorisation(_person(), _zone(), [{"user_id": "g1", "role_id": 5}])[0] == "UNAUTHORISED", \
        "a zone that allows no one is not made safe by a guard on shift elsewhere"


def test_a_fall_or_missing_ppe_is_not_excused_by_who_is_on_shift():
    for module in ("fall", "ppe"):
        verdict, _ = ai.authorisation(_person(module=module), _zone(zone_type="NORMAL"), [{"user_id": "g"}])
        assert verdict == "UNKNOWN"


def test_in_a_critical_zone_someone_allowed_being_on_shift_proves_nothing():
    zone = _zone(zone_type="CRITICAL", allowed_role_ids=[5])
    r = _risk(_person(), DAY, zone, on_duty=[{"user_id": "g", "role_id": 5}], verified=True, detection_count=3)
    auth = next(f for f in r.factors if f["factor"] == "AUTHORISATION")
    assert auth["points"] == 0


# ─── Verification ────────────────────────────────────────────────────────────

def test_one_frame_is_not_verified_but_a_sustained_sighting_is():
    assert not ai.is_verified("intrusion", 1, 0, 0.9, None)
    assert ai.is_verified("intrusion", 3, 1, 0.9, None)
    assert ai.is_verified("intrusion", 2, 3, 0.9, None)
    assert not ai.is_verified("intrusion", 2, 3, 0.9, {"verify_min_seconds": 10})


def test_a_clear_weapon_or_fire_is_verified_on_first_sight():
    assert ai.is_verified("weapon", 1, 0, 0.85, None)
    assert not ai.is_verified("weapon", 1, 0, 0.6, None)
    assert ai.is_verified("fire_smoke", 1, 0, 0.9, None)


def test_an_unverified_single_sighting_scores_lower_and_raises_nothing():
    once = _risk(_person(), NIGHT, _zone(), detection_count=1)
    seen = _risk(_person(), NIGHT, _zone(), verified=True, detection_count=4, observed_seconds=10)
    assert once.score < seen.score
    assert ai.alert_severity(once.level, False, _zone()) is None


# ─── Risk and alerts ─────────────────────────────────────────────────────────

def test_more_context_raises_the_risk():
    base = _risk(_person(), NIGHT, _zone(), verified=True, detection_count=3)
    more = _risk(_person(), NIGHT, _zone(), verified=True, detection_count=6,
                 concurrent_modules={"weapon"}, history_events=6, history_incidents=1)
    assert more.score > base.score
    assert {f["factor"] for f in more.factors} >= {"CONCURRENT", "HISTORY", "REPEATED"}


def test_a_verified_weapon_is_critical():
    r = _risk(ai.Sighting(module_type="weapon", detected_at=DAY, confidence=0.92, label="handgun"), DAY, None,
              verified=True, detection_count=1)
    assert r.level == "CRITICAL" and ai.alert_severity(r.level, True, None) == "critical"


def test_scores_stay_between_0_and_100_and_levels_follow_the_thresholds():
    assert ai.level_of(0) == "INFO" and ai.level_of(15) == "LOW" and ai.level_of(35) == "MEDIUM"
    assert ai.level_of(55) == "HIGH" and ai.level_of(79) == "HIGH" and ai.level_of(80) == "CRITICAL"
    r = _risk(ai.Sighting(module_type="weapon", detected_at=NIGHT, confidence=0.99, watchlist="block"), NIGHT,
              _zone(zone_type="CRITICAL", severity="critical"), verified=True, detection_count=9,
              concurrent_modules={"intrusion"}, history_events=9, history_incidents=3)
    assert r.score == 100


def test_what_alerts():
    assert ai.alert_severity("LOW", True, None) is None
    assert ai.alert_severity("MEDIUM", True, None) == "medium"
    assert ai.alert_severity("HIGH", False, None) is None
    assert ai.alert_severity("CRITICAL", True, _zone(alert_policy="NONE")) is None
