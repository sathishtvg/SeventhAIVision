"""Journeys and stops, worked out from positions (GPS fleet upgrade, phase 4), with nothing running.

A day is built from positions every 30 seconds: stopped at the depot, a drive,
a stop at a site, a time with no positions at all, and a drive back - with a
fix the device called inaccurate and one that would need 900 km/h in it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import fleet_journeys as fj

T0 = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
DEPOT = (1.3644, 103.9915)
SITE = (1.3521, 103.9450)


def _stay(at, where, minutes, *, ignition=None, step=30):
    out, t = [], at
    for k in range(int(minutes * 60 / step) + 1):
        # A parked vehicle's fixes wander by a few metres.
        out.append(fj.Fix(t, where[0] + (k % 3) * 0.00002, where[1], speed=0.0, accuracy=6, ignition=ignition))
        t += timedelta(seconds=step)
    return out, t


def _drive(at, a, b, minutes, *, step=30, kmh=40.0):
    n = int(minutes * 60 / step)
    out = []
    for k in range(1, n + 1):
        f = k / n
        out.append(fj.Fix(at + timedelta(seconds=k * step), a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f,
                          speed=kmh, accuracy=8))
    return out, at + timedelta(seconds=n * step)


def _day():
    fixes = []
    part, t = _stay(T0, DEPOT, 5, ignition=False)
    fixes += part
    part, t = _drive(t, DEPOT, SITE, 8)
    fixes += part
    part, t = _stay(t, SITE, 10)
    fixes += part
    # Nothing for 20 minutes (no signal), then the drive back.
    part, t = _drive(t + timedelta(minutes=20), SITE, DEPOT, 8)
    fixes += part
    return fixes


def test_fewer_than_two_positions_are_not_a_journey():
    h = fj.work_out([fj.Fix(T0, *DEPOT)])
    assert (h.enough, h.trips, h.stops) == (False, [], [])
    assert fj.work_out([]).enough is False


def test_a_day_is_stops_at_the_depot_and_the_site_and_two_drives_cut_by_the_time_without_positions():
    h = fj.work_out(_day())
    assert h.enough
    assert [round(s.minutes) for s in h.stops] == [5, 10]
    assert h.stops[0].ignition_off is True and h.stops[1].ignition_off is False
    assert len(h.trips) == 2
    there = fj.metres(*DEPOT, *SITE)
    assert h.trips[0].distance_m == pytest.approx(there, rel=0.01)
    # The drive back began while no positions came: the stretch it covered then is not counted as driving.
    step = there / 16
    assert h.trips[1].distance_m == pytest.approx(there - step, rel=0.01)
    assert all(trip.max_speed == 40.0 for trip in h.trips)
    assert [round(g.end.timestamp() - g.start.timestamp()) // 60 for g in h.gaps] == [21]
    assert h.distance_m == pytest.approx(2 * there - step, rel=0.01)


def test_an_inaccurate_fix_and_an_impossible_jump_are_left_out_and_counted():
    fixes = _day()
    middle = fixes[len(fixes) // 3]
    fixes.append(fj.Fix(middle.at + timedelta(seconds=1), 1.0, 104.5, accuracy=8))          # 60 km away in a second
    fixes.append(fj.Fix(middle.at + timedelta(seconds=2), middle.lat, middle.lon, accuracy=900))
    h = fj.work_out(fixes)
    assert h.left_out == {"inaccurate": 1, "impossible_jump": 1}
    assert h.distance_m == pytest.approx(fj.work_out(_day()).distance_m, rel=0.001)


def test_the_route_drawn_is_thinned_and_keeps_its_ends_but_the_distance_never_comes_from_it():
    fixes, _ = _drive(T0, DEPOT, SITE, 600, step=5)                 # 7,200 positions on a straight line
    drawn = fj.thin(fixes, most=100)
    assert len(drawn) <= 100 and drawn[0] is fixes[0] and drawn[-1] is fixes[-1]
    assert fj.thin(fixes[:50], most=100) == fixes[:50]


def test_a_range_is_at_most_a_week_and_ends_after_it_starts():
    fj.check_range(T0, T0 + timedelta(days=7))
    for bad in ((T0, T0), (T0, T0 - timedelta(hours=1)), (T0, T0 + timedelta(days=7, seconds=1))):
        with pytest.raises(ValueError):
            fj.check_range(*bad)
