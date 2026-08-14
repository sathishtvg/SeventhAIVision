"""Geofence checks for attendance check-in (ShiftSecure Phase 2A).

Two shapes are supported, and a site may define either:

    radius   — a circle around the site's pin. Simple, and right for a single
               building.
    polygon  — the boundary drawn on the map. Right for everything a circle
               gets wrong: an L-shaped mall, a yard that hugs a road, a
               compound whose neighbour's car park a circle would swallow.

Where a site has both, the polygon wins — an admin who took the trouble to
draw the real boundary meant it, and silently applying the leftover radius
instead would quietly re-admit the ground they drew around.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

_EARTH_RADIUS_METERS = 6_371_000.0


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_METERS * c


def normalize_polygon(raw: Any) -> list[tuple[float, float]] | None:
    """Accept the stored JSONB and return usable (lat, lng) pairs, or None.

    Returns None rather than raising for anything unusable — a malformed
    polygon must fall back to the radius check, not block a guard from
    starting their shift. Fewer than 3 points cannot enclose an area, so that
    counts as unusable too.
    """
    if not isinstance(raw, (list, tuple)):
        return None
    points: list[tuple[float, float]] = []
    for item in raw:
        try:
            if isinstance(item, dict):
                lat, lng = float(item["lat"]), float(item["lng"])
            else:  # [lat, lng] pair
                lat, lng = float(item[0]), float(item[1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        points.append((lat, lng))
    return points if len(points) >= 3 else None


def point_in_polygon(lat: float, lon: float, polygon: Sequence[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon, in degrees.

    Treating lat/lon as plane coordinates is accurate enough here: a site
    boundary spans metres, not degrees, and over that distance the error from
    ignoring the earth's curvature is far below GPS noise. It also keeps the
    check dependency-free rather than pulling in shapely for one predicate.

    Points exactly on an edge are not guaranteed either way — that ambiguity
    is inherent to the algorithm and irrelevant against a GPS fix that is
    already several metres uncertain.
    """
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        lat_i, lon_i = polygon[i]
        lat_j, lon_j = polygon[j]
        # Does the edge straddle the test latitude, and if so is the crossing
        # to the east of the point?
        if (lon_i > lon) != (lon_j > lon):
            x = (lat_j - lat_i) * (lon - lon_i) / (lon_j - lon_i) + lat_i
            if lat < x:
                inside = not inside
        j = i
    return inside


def is_within_site(
    lat: float,
    lon: float,
    *,
    site_lat: float | None,
    site_lon: float | None,
    radius_meters: float | None,
    polygon: Any = None,
) -> bool | None:
    """One answer for "is this guard on site", whichever shape is configured.

    None means "cannot tell" — no polygon and no site coordinates — which the
    caller records as unknown rather than as a failure. Flagging a guard for
    an unconfigured site would punish them for an admin's omission.
    """
    points = normalize_polygon(polygon)
    if points is not None:
        return point_in_polygon(lat, lon, points)
    if site_lat is None or site_lon is None or not radius_meters:
        return None
    return haversine_meters(lat, lon, site_lat, site_lon) <= radius_meters
