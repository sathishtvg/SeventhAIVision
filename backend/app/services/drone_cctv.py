"""CCTV correlation rules — pure, no database.

Given where a drone saw something and when, which of the site's fixed cameras
could have seen it too, and does what they detected agree?

NEARBY IS NOT COVERING. A camera stores only its position, so by default the
answer is "within NEARBY_RADIUS_M of the spot" — the camera may be facing the
other way. A camera with surveyed coverage (drone_camera_coverage: a sector of
heading, field of view and range, or an explicit polygon) is said to COVER the
spot only if the spot falls inside it, and is listed first. Nothing here claims
a camera saw something it could not.

THE SPOT IS THE DRONE'S POSITION. The drone records where it was, not where the
object was (DRONE_PATROL_AI.md); from 40 m up that can be tens of metres off, so
the nearby radius is generous and coverage is tested against the same point.

CORROBORATION IS A MATCHING DETECTION, not any activity: a person for a person,
the same plate for a plate, fire for fire, a weapon for a weapon — on that
camera, within the event's window.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

from app.services.geofence import haversine_meters, normalize_polygon, point_in_polygon

#: How far from the spot a camera without surveyed coverage still counts.
NEARBY_RADIUS_M = 150.0
MAX_CAMERAS = 8
#: Correlation is refreshed at most this often while an event is still growing.
REFRESH_EVERY = timedelta(seconds=15)
#: A fixed camera's detection may be written a little after the event's window
#: closes; correlation is settled only once this has passed too.
LATE_DETECTIONS = timedelta(seconds=30)

PERSON = {"intrusion", "crowd", "face", "behavior", "fall", "ppe"}
_KIND = {**{m: "person" for m in PERSON}, "lpr": "vehicle", "fire_smoke": "fire", "weapon": "weapon"}


@dataclass
class Candidate:
    camera_id: str
    name: str
    distance_m: float
    bearing_deg: float
    in_coverage: bool | None          # None: the camera's coverage is not surveyed
    method: str                        # DISTANCE | COVERAGE
    rank: int = 0


def bearing_deg(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> float:
    """Initial compass bearing from one point to another, 0 = north, clockwise."""
    p1, p2 = math.radians(from_lat), math.radians(to_lat)
    dl = math.radians(to_lng - from_lng)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def angle_between(a: float, b: float) -> float:
    """The smaller angle between two bearings, 0–180."""
    d = abs(a - b) % 360.0
    return 360.0 - d if d > 180.0 else d


def covers(coverage: dict | None, cam_lat: float, cam_lng: float, lat: float, lng: float) -> bool | None:
    """Whether a camera's surveyed coverage contains the point; None if it has
    none. An explicit polygon wins over a sector."""
    if not coverage:
        return None
    poly = normalize_polygon(coverage.get("coverage_polygon"))
    if poly:
        return point_in_polygon(lat, lng, poly)
    heading, fov, rng = coverage.get("heading_deg"), coverage.get("fov_deg"), coverage.get("range_m")
    if heading is None or fov is None or rng is None:
        return None
    if haversine_meters(cam_lat, cam_lng, lat, lng) > float(rng):
        return False
    if float(fov) >= 360:
        return True
    return angle_between(bearing_deg(cam_lat, cam_lng, lat, lng), float(heading)) <= float(fov) / 2.0


def candidates(cameras: list[dict], lat: float, lng: float, *, radius_m: float = NEARBY_RADIUS_M,
               limit: int = MAX_CAMERAS) -> list[Candidate]:
    """The cameras worth showing an operator, best first: those that cover the
    spot, then those merely near it, each by distance. A surveyed camera that
    faces away is left out even if it is close; one whose view reaches the spot
    from beyond the radius is kept."""
    out: list[Candidate] = []
    for c in cameras:
        if c.get("latitude") is None or c.get("longitude") is None:
            continue
        dist = haversine_meters(float(c["latitude"]), float(c["longitude"]), lat, lng)
        cov = covers(c.get("coverage"), float(c["latitude"]), float(c["longitude"]), lat, lng)
        if cov is False:
            continue
        if cov is None and dist > radius_m:
            continue
        out.append(Candidate(camera_id=str(c["id"]), name=c.get("name") or "", distance_m=round(dist, 2),
                             bearing_deg=round(bearing_deg(float(c["latitude"]), float(c["longitude"]), lat, lng), 2),
                             in_coverage=cov, method="COVERAGE" if cov else "DISTANCE"))
    out.sort(key=lambda k: (0 if k.in_coverage else 1, k.distance_m))
    for i, k in enumerate(out[:limit], start=1):
        k.rank = i
    return out[:limit]


def _plate(v: str | None) -> str:
    return "".join((v or "").split()).upper()


def corroborates(event_module: str, event_label: str | None, cctv_module: str, cctv_label: str | None) -> bool:
    """Does a fixed camera's detection back up the drone's? Same kind of thing;
    for plates, the same plate."""
    kind = _KIND.get(event_module)
    if kind is None or kind != _KIND.get(cctv_module):
        return False
    if kind == "vehicle":
        return bool(event_label) and _plate(event_label) == _plate(cctv_label)
    return True
