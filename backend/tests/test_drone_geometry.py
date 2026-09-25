"""Drone map geometry and the provider catalogue — pure, no database.

Zones are the context engine's input in Phase 6, so a shape stored wrongly now
becomes an intrusion missed later. These pin down what is stored and what counts
as inside.
"""
from __future__ import annotations

import pytest

from app.services import drone_geometry as geo
from app.services import drone_provider_registry as reg

# A square roughly 220 m on a side around (1.3000, 103.8000).
SQUARE = [{"lat": 1.299, "lng": 103.799}, {"lat": 1.299, "lng": 103.801},
          {"lat": 1.301, "lng": 103.801}, {"lat": 1.301, "lng": 103.799}]


# ─── Zones ───────────────────────────────────────────────────────────────────

def test_a_rectangle_drawn_as_two_corners_is_stored_as_four():
    g = geo.normalize_zone("RECTANGLE", [[1.301, 103.801], [1.299, 103.799]])
    assert g["polygon"] == [{"lat": 1.299, "lng": 103.799}, {"lat": 1.299, "lng": 103.801},
                            {"lat": 1.301, "lng": 103.801}, {"lat": 1.301, "lng": 103.799}]
    assert g["radius_m"] is None


def test_a_flat_rectangle_is_refused():
    with pytest.raises(geo.GeometryError, match="differ in both"):
        geo.normalize_zone("RECTANGLE", [[1.30, 103.80], [1.30, 103.81]])


def test_a_polygon_needs_three_distinct_points():
    with pytest.raises(geo.GeometryError, match="at least three"):
        geo.normalize_zone("POLYGON", SQUARE[:2])
    with pytest.raises(geo.GeometryError, match="enclose an area"):
        geo.normalize_zone("POLYGON", [SQUARE[0], SQUARE[0], SQUARE[1]])


def test_a_point_off_the_planet_is_refused():
    with pytest.raises(geo.GeometryError, match="not a valid"):
        geo.normalize_zone("POLYGON", [{"lat": 95, "lng": 0}, *SQUARE[1:]])


def test_a_circle_needs_a_centre_and_a_sane_radius():
    with pytest.raises(geo.GeometryError, match="centre and a radius"):
        geo.normalize_zone("CIRCLE", center_latitude=1.3, center_longitude=103.8)
    with pytest.raises(geo.GeometryError, match="between 0 and"):
        geo.normalize_zone("CIRCLE", center_latitude=1.3, center_longitude=103.8, radius_m=60_000)
    g = geo.normalize_zone("CIRCLE", center_latitude=1.3, center_longitude=103.8, radius_m=25)
    assert g["polygon"] is None and g["radius_m"] == 25


def test_inside_and_outside_a_polygon_zone():
    zone = {"shape": "POLYGON", "polygon": SQUARE}
    assert geo.zone_contains(zone, 1.3000, 103.8000)
    assert not geo.zone_contains(zone, 1.3050, 103.8000)


def test_inside_and_outside_a_circle_zone():
    zone = {"shape": "CIRCLE", "center_latitude": 1.3, "center_longitude": 103.8, "radius_m": 50}
    assert geo.zone_contains(zone, 1.3002, 103.8)        # ~22 m
    assert not geo.zone_contains(zone, 1.3010, 103.8)    # ~111 m


def test_an_unreadable_zone_contains_nothing():
    """A corrupt zone must not turn the whole map into a restricted area."""
    assert not geo.zone_contains({"shape": "POLYGON", "polygon": "garbage"}, 1.3, 103.8)
    assert not geo.zone_contains({"shape": "CIRCLE"}, 1.3, 103.8)


# ─── Routes ──────────────────────────────────────────────────────────────────

def test_route_length_includes_the_return_to_base():
    base = (1.3000, 103.8000)
    out = geo.route_length_m(base, [(1.3010, 103.8000)], return_to_base=False)
    back = geo.route_length_m(base, [(1.3010, 103.8000)], return_to_base=True)
    assert 105 < out < 118                       # 0.001° of latitude ≈ 111 m
    assert back == pytest.approx(out * 2)
    assert geo.route_length_m(base, []) == 0.0


def test_waypoints_beyond_the_site_fence_are_flagged_not_refused():
    site = {"latitude": 1.3, "longitude": 103.8, "geofence_radius_meters": 100,
            "geofence_polygon": None}
    wps = [{"sequence": 1, "latitude": 1.3002, "longitude": 103.8},   # ~22 m
           {"sequence": 2, "latitude": 1.3050, "longitude": 103.8}]   # ~556 m
    assert geo.waypoints_outside_site(site, wps) == [2]


def test_a_site_without_a_fence_flags_nothing():
    """"Cannot tell" is not "outside"."""
    site = {"latitude": None, "longitude": None, "geofence_radius_meters": None,
            "geofence_polygon": None}
    assert geo.waypoints_outside_site(site, [{"sequence": 1, "latitude": 9, "longitude": 9}]) == []


# ─── Provider catalogue ──────────────────────────────────────────────────────

def test_only_the_simulator_is_listed_until_hardware_is_chosen():
    keys = [p["key"] for p in reg.catalogue()]
    assert keys == ["simulator"]
    assert reg.catalogue()[0]["simulated"] is True


def test_an_unknown_provider_is_refused():
    with pytest.raises(reg.ProviderConfigError, match="Unknown provider"):
        reg.split_config("acme-quadcopter", {})


def test_an_unknown_setting_is_refused_not_ignored():
    """A typo left in JSONB would sit there while the adapter used its default."""
    with pytest.raises(reg.ProviderConfigError, match="speed_factr"):
        reg.split_config("simulator", {"speed_factr": 2})


def test_settings_are_typed_and_bounded():
    config, secrets = reg.split_config("simulator", {"speed_factor": "10", "failure_rate": 0.1})
    assert config == {"speed_factor": 10.0, "failure_rate": 0.1} and secrets == {}
    with pytest.raises(reg.ProviderConfigError, match="at most 1"):
        reg.split_config("simulator", {"failure_rate": 2})
    with pytest.raises(reg.ProviderConfigError, match="must be a number"):
        reg.split_config("simulator", {"speed_factor": "fast"})
