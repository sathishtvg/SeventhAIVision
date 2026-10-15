"""Journeys and stops, worked out from a vehicle's stored positions.

Design: docs/plans/2026-10-14-gps-fleet-tracking-design.md, section 4.

NOTHING IS WORKED OUT FROM ONE POSITION. A journey needs two positions at the
least; with fewer the answer says so and has no trips.

WHAT IS LEFT OUT, AND SAID: a position the device reported as less accurate
than `MOST_INACCURATE_M`, and one that would need the vehicle to have gone
faster than `FASTEST_KMH` from the last one kept (a jump, not a journey). They
are counted in `left_out`; the route and the distance are worked out without
them.

A STOP is the vehicle staying within `STOP_RADIUS_M` of where it stopped for
`STOP_MINUTES` or longer, or its ignition reported off for that long. A trip is
what lies between two stops (or the start or end of the range). The distance is
added up fix by fix over the positions kept; it is never taken from a thinned
route. A gap with no position for longer than `GAP_MINUTES` ends a trip: the
time without positions is not counted as driving or stopping, and is said.

The route drawn on a screen is thinned (`thin`) so a week of positions stays
quick to draw; the distance is never worked out from it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

STOP_RADIUS_M = 50.0
STOP_MINUTES = 3.0
MOST_INACCURATE_M = 100.0
FASTEST_KMH = 250.0
GAP_MINUTES = 15.0
MOST_DRAWN = 5000
MOST_DAYS = 7
EARTH_M = 6_371_000.0


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(min(1.0, a)))


@dataclass
class Fix:
    at: datetime
    lat: float
    lon: float
    speed: float | None = None
    accuracy: float | None = None
    ignition: bool | None = None


@dataclass
class Stop:
    start: datetime
    end: datetime
    lat: float
    lon: float
    ignition_off: bool = False

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


@dataclass
class Trip:
    start: datetime
    end: datetime
    fixes: list[Fix] = field(default_factory=list)
    distance_m: float = 0.0
    max_speed: float | None = None

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    @property
    def avg_kmh(self) -> float | None:
        hours = self.minutes / 60
        return round(self.distance_m / 1000 / hours, 1) if hours > 0 else None


@dataclass
class Gap:
    start: datetime
    end: datetime


@dataclass
class History:
    kept: list[Fix]
    left_out: dict[str, int]
    trips: list[Trip]
    stops: list[Stop]
    gaps: list[Gap]
    enough: bool

    @property
    def distance_m(self) -> float:
        return sum(t.distance_m for t in self.trips)


def keep(fixes: list[Fix]) -> tuple[list[Fix], dict[str, int]]:
    """The positions a journey is worked out from, oldest first, and how many were left out and why."""
    left_out = {"inaccurate": 0, "impossible_jump": 0}
    kept: list[Fix] = []
    for f in sorted(fixes, key=lambda x: x.at):
        if f.accuracy is not None and f.accuracy > MOST_INACCURATE_M:
            left_out["inaccurate"] += 1
            continue
        if kept:
            last = kept[-1]
            seconds = (f.at - last.at).total_seconds()
            if seconds <= 0:
                continue
            kmh = metres(last.lat, last.lon, f.lat, f.lon) / seconds * 3.6
            if kmh > FASTEST_KMH:
                left_out["impossible_jump"] += 1
                continue
        kept.append(f)
    return kept, left_out


def work_out(fixes: list[Fix]) -> History:
    kept, left_out = keep(fixes)
    if len(kept) < 2:
        return History(kept, left_out, [], [], [], enough=False)
    stops: list[Stop] = []
    gaps: list[Gap] = []
    # Where each run of positions within the stop radius begins.
    i = 0
    stop_spans: list[tuple[int, int, bool]] = []
    while i < len(kept):
        j = i
        anchor = kept[i]
        off = anchor.ignition is False
        while j + 1 < len(kept) and metres(anchor.lat, anchor.lon, kept[j + 1].lat, kept[j + 1].lon) <= STOP_RADIUS_M \
                and (kept[j + 1].at - kept[j].at).total_seconds() / 60 <= GAP_MINUTES:
            j += 1
            off = off or kept[j].ignition is False
        if (kept[j].at - kept[i].at).total_seconds() / 60 >= STOP_MINUTES:
            stop_spans.append((i, j, off))
            i = j + 1
        else:
            i += 1
    for a, b, off in stop_spans:
        stops.append(Stop(kept[a].at, kept[b].at, kept[a].lat, kept[a].lon, ignition_off=off))

    # Trips: the stretches between stops, cut wherever positions stop coming for too long.
    in_stop = [False] * len(kept)
    for a, b, _ in stop_spans:
        for k in range(a, b + 1):
            in_stop[k] = True
    trips: list[Trip] = []
    current: Trip | None = None
    for k, f in enumerate(kept):
        if k > 0 and (f.at - kept[k - 1].at).total_seconds() / 60 > GAP_MINUTES:
            gaps.append(Gap(kept[k - 1].at, f.at))
            if current and len(current.fixes) >= 2:
                trips.append(current)
            current = None
        if in_stop[k]:
            if current is not None:
                current.fixes.append(f)
                current.end = f.at
                if len(current.fixes) >= 2:
                    trips.append(current)
                current = None
            continue
        if current is None:
            start_from = kept[k - 1] if k > 0 and in_stop[k - 1] and (f.at - kept[k - 1].at).total_seconds() / 60 <= GAP_MINUTES else f
            current = Trip(start_from.at, f.at, [start_from] if start_from is not f else [])
        current.fixes.append(f)
        current.end = f.at
    if current and len(current.fixes) >= 2:
        trips.append(current)
    for t in trips:
        t.distance_m = sum(metres(p.lat, p.lon, q.lat, q.lon) for p, q in zip(t.fixes, t.fixes[1:]))
        speeds = [p.speed for p in t.fixes if p.speed is not None]
        t.max_speed = max(speeds) if speeds else None
    return History(kept, left_out, trips, stops, gaps, enough=True)


def thin(fixes: list[Fix], most: int = MOST_DRAWN) -> list[Fix]:
    """At most `most` positions for drawing the route, keeping its shape (Douglas-Peucker, widening until it fits)."""
    if len(fixes) <= most:
        return fixes
    tolerance = 2.0
    out = fixes
    while len(out) > most:
        out = _simplify(fixes, tolerance)
        tolerance *= 2
    return out


def _simplify(points: list[Fix], tolerance_m: float) -> list[Fix]:
    keep_flags = [False] * len(points)
    keep_flags[0] = keep_flags[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        far, at = 0.0, -1
        for k in range(a + 1, b):
            d = _off_line(points[k], points[a], points[b])
            if d > far:
                far, at = d, k
        if at >= 0 and far > tolerance_m:
            keep_flags[at] = True
            stack.extend([(a, at), (at, b)])
    return [p for p, k in zip(points, keep_flags) if k]


def _off_line(p: Fix, a: Fix, b: Fix) -> float:
    # Small distances: a flat projection in metres is close enough.
    lat0 = math.radians(a.lat)
    def xy(f: Fix) -> tuple[float, float]:
        return (math.radians(f.lon) * EARTH_M * math.cos(lat0), math.radians(f.lat) * EARTH_M)
    (x, y), (x1, y1), (x2, y2) = xy(p), xy(a), xy(b)
    dx, dy = x2 - x1, y2 - y1
    if dx == dy == 0:
        return math.hypot(x - x1, y - y1)
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def check_range(start: datetime, end: datetime) -> None:
    if end <= start:
        raise ValueError("The range ends before it starts")
    if end - start > timedelta(days=MOST_DAYS):
        raise ValueError(f"A range is at most {MOST_DAYS} days")
