"""Shapes on the map: security zones, routes, and whether a point is inside.

Built on services/geofence.py rather than beside it. That module already has a
tested haversine, a ray-casting point-in-polygon and the polygon format sites
use, and there is no PostGIS in this database to reach for instead.

Zones are stored as the same [{"lat": .., "lng": ..}, ...] list that
sites.geofence_polygon uses, so one reader serves both. A rectangle is stored as
its four corners. A circle is a centre and a radius and has no polygon.
"""
from __future__ import annotations

from typing import Any, Sequence

from app.services.geofence import haversine_meters, is_within_site, normalize_polygon, point_in_polygon

#: A zone bigger than this is almost certainly a typo in the radius (metres
#: entered as centimetres, or km as m), not a security zone.
MAX_CIRCLE_RADIUS_M = 50_000


class GeometryError(ValueError):
    """A shape that cannot be used. The message is shown to an administrator."""


def _check_point(lat: float, lng: float) -> None:
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        raise GeometryError(f"({lat}, {lng}) is not a valid latitude/longitude.")


def _as_points(raw: Any) -> list[tuple[float, float]]:
    """Accept [{"lat","lng"}] or [[lat, lng]] and return (lat, lng) pairs,
    whatever the count — normalize_polygon insists on three, and a rectangle
    arrives as two corners."""
    if not isinstance(raw, (list, tuple)):
        raise GeometryError("Points must be a list of {lat, lng}.")
    pts: list[tuple[float, float]] = []
    for item in raw:
        try:
            if isinstance(item, dict):
                lat, lng = float(item["lat"]), float(item["lng"])
            else:
                lat, lng = float(item[0]), float(item[1])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GeometryError("Every point needs a numeric lat and lng.") from exc
        _check_point(lat, lng)
        pts.append((lat, lng))
    return pts


def _to_json(points: Sequence[tuple[float, float]]) -> list[dict]:
    return [{"lat": lat, "lng": lng} for lat, lng in points]


def normalize_zone(
    shape: str,
    polygon: Any = None,
    center_latitude: float | None = None,
    center_longitude: float | None = None,
    radius_m: float | None = None,
) -> dict:
    """Validate a zone's geometry and return the columns to store.

    POLYGON    three or more points.
    RECTANGLE  two opposite corners (as a map tool draws it) or four corners;
               stored as four corners either way.
    CIRCLE     a centre and a radius in metres.
    """
    if shape == "CIRCLE":
        if center_latitude is None or center_longitude is None or radius_m is None:
            raise GeometryError("A circle needs a centre and a radius.")
        _check_point(center_latitude, center_longitude)
        if not (0 < radius_m <= MAX_CIRCLE_RADIUS_M):
            raise GeometryError(f"A circle's radius must be between 0 and {MAX_CIRCLE_RADIUS_M} metres.")
        return {"polygon": None, "center_latitude": center_latitude,
                "center_longitude": center_longitude, "radius_m": radius_m}

    pts = _as_points(polygon)
    if shape == "RECTANGLE":
        if len(pts) == 2:
            (a_lat, a_lng), (b_lat, b_lng) = pts
            if a_lat == b_lat or a_lng == b_lng:
                raise GeometryError("A rectangle's corners must differ in both latitude and longitude.")
            south, north = sorted((a_lat, b_lat))
            west, east = sorted((a_lng, b_lng))
            pts = [(south, west), (south, east), (north, east), (north, west)]
        elif len(pts) != 4:
            raise GeometryError("A rectangle is two opposite corners or four corners.")
    elif shape == "POLYGON":
        if len(pts) < 3:
            raise GeometryError("A polygon needs at least three points.")
    else:
        raise GeometryError(f"Unknown shape {shape!r}.")

    if len(set(pts)) < 3:
        raise GeometryError("The points must enclose an area; several are the same point.")
    return {"polygon": _to_json(pts), "center_latitude": None,
            "center_longitude": None, "radius_m": None}


def zone_contains(zone: dict, lat: float, lng: float) -> bool:
    """Is this point inside the zone? A zone whose stored shape cannot be read
    contains nothing — an unreadable zone must not flag the whole map."""
    if zone.get("shape") == "CIRCLE":
        if zone.get("center_latitude") is None or zone.get("radius_m") is None:
            return False
        d = haversine_meters(lat, lng, float(zone["center_latitude"]), float(zone["center_longitude"]))
        return d <= float(zone["radius_m"])
    pts = normalize_polygon(zone.get("polygon"))
    return bool(pts) and point_in_polygon(lat, lng, pts)


def route_length_m(base: tuple[float, float], points: Sequence[tuple[float, float]],
                   return_to_base: bool = True) -> float:
    """Straight-line length of base → each waypoint in order (→ base)."""
    if not points:
        return 0.0
    path = [base, *points, *( [base] if return_to_base else [] )]
    return sum(haversine_meters(a[0], a[1], b[0], b[1]) for a, b in zip(path, path[1:]))


def waypoints_outside_site(site: dict, waypoints: Sequence[dict]) -> list[int]:
    """Sequence numbers of waypoints that fall outside the site's geofence.

    A warning, not a refusal: a perimeter patrol may legitimately look over the
    fence, and whether it may fly there is the operator's and the regulator's
    call. A site with no geofence at all flags nothing — "cannot tell" is not
    "outside".
    """
    out: list[int] = []
    for wp in waypoints:
        inside = is_within_site(
            float(wp["latitude"]), float(wp["longitude"]),
            site_lat=site.get("latitude"), site_lon=site.get("longitude"),
            radius_meters=site.get("geofence_radius_meters"),
            polygon=site.get("geofence_polygon"),
        )
        if inside is False:
            out.append(int(wp["sequence"]))
    return out
