"""Drone patrol, phase 7: CCTV correlation rules — pure geometry and matching."""
from __future__ import annotations

import pytest

from app.services import drone_cctv as cctv

SPOT = (1.3010, 103.8000)
M = 1 / 111_320          # degrees of latitude per metre, near enough at the equator


def _cam(name: str, dn: float = 0.0, de: float = 0.0, coverage: dict | None = None) -> dict:
    """A camera `dn` metres north and `de` metres east of the spot."""
    return {"id": name, "name": name, "latitude": SPOT[0] + dn * M, "longitude": SPOT[1] + de * M,
            "coverage": coverage}


def test_bearings():
    assert cctv.bearing_deg(0, 0, 1, 0) == pytest.approx(0, abs=0.01)
    assert cctv.bearing_deg(0, 0, 0, 1) == pytest.approx(90, abs=0.01)
    assert cctv.bearing_deg(0, 0, -1, 0) == pytest.approx(180, abs=0.01)
    assert cctv.angle_between(350, 10) == 20 and cctv.angle_between(90, 270) == 180


def test_a_sector_covers_only_what_is_in_front_of_it_and_in_range():
    east = _cam("E", de=80)                              # the spot is due west of it
    facing = {"heading_deg": 270, "fov_deg": 60, "range_m": 120}
    assert cctv.covers(facing, east["latitude"], east["longitude"], *SPOT) is True
    assert cctv.covers({**facing, "heading_deg": 90}, east["latitude"], east["longitude"], *SPOT) is False
    assert cctv.covers({**facing, "range_m": 50}, east["latitude"], east["longitude"], *SPOT) is False
    assert cctv.covers({**facing, "heading_deg": 90, "fov_deg": 360}, east["latitude"], east["longitude"], *SPOT)
    assert cctv.covers(None, east["latitude"], east["longitude"], *SPOT) is None


def test_an_explicit_polygon_wins_over_a_sector():
    box = [[SPOT[0] - 0.0002, SPOT[1] - 0.0002], [SPOT[0] - 0.0002, SPOT[1] + 0.0002],
           [SPOT[0] + 0.0002, SPOT[1] + 0.0002], [SPOT[0] + 0.0002, SPOT[1] - 0.0002]]
    away = {"heading_deg": 90, "fov_deg": 30, "range_m": 10, "coverage_polygon": box}
    assert cctv.covers(away, SPOT[0], SPOT[1] + 0.01, *SPOT) is True


def test_candidates_are_covering_first_then_nearby_by_distance():
    cams = [
        _cam("near-30m", dn=30),
        _cam("covers-80m", de=80, coverage={"heading_deg": 270, "fov_deg": 60, "range_m": 120}),
        _cam("faces-away-20m", dn=-20, coverage={"heading_deg": 180, "fov_deg": 60, "range_m": 100}),
        _cam("too-far-500m", dn=500),
        _cam("covers-from-200m", de=-200, coverage={"heading_deg": 90, "fov_deg": 30, "range_m": 300}),
        {"id": "no-position", "name": "no-position", "latitude": None, "longitude": None},
    ]
    got = cctv.candidates(cams, *SPOT)
    assert [c.name for c in got] == ["covers-80m", "covers-from-200m", "near-30m"]
    assert [c.method for c in got] == ["COVERAGE", "COVERAGE", "DISTANCE"]
    assert [c.rank for c in got] == [1, 2, 3]
    assert got[2].in_coverage is None and got[2].distance_m == pytest.approx(30, abs=0.5)
    assert got[0].bearing_deg == pytest.approx(270, abs=0.5)


def test_candidates_are_limited():
    cams = [_cam(f"c{i}", dn=i * 10) for i in range(1, 15)]
    got = cctv.candidates(cams, *SPOT, limit=5)
    assert [c.name for c in got] == ["c1", "c2", "c3", "c4", "c5"]


@pytest.mark.parametrize("event, event_label, other, other_label, agrees", [
    ("intrusion", None, "face", None, True),
    ("face", None, "intrusion", None, True),
    ("intrusion", None, "fire_smoke", None, False),
    ("lpr", "SGX 1234 A", "lpr", "sgx1234a", True),
    ("lpr", "SGX1234A", "lpr", "SGX9999Z", False),
    ("lpr", None, "lpr", "SGX1234A", False),
    ("fire_smoke", "fire", "fire_smoke", "smoke", True),
    ("weapon", "handgun", "intrusion", None, False),
    ("tampering", None, "tampering", None, False),
])
def test_what_corroborates_what(event, event_label, other, other_label, agrees):
    assert cctv.corroborates(event, event_label, other, other_label) is agrees
