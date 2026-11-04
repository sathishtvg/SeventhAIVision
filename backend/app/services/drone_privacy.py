"""Privacy areas for drones: where a drone's camera is not to show, and whether it is there now.

Phase 5 of closing the feature reference's known limits (4 November 2026; docs/plans/2026-11-04-drone-privacy-areas-
design.md). A fixed camera's privacy zone is drawn on its picture (services/privacy_mask.py); a drone's picture moves
with the drone, so its areas are drawn on the map, and while the drone is at one its whole picture is withheld.

AT AN AREA means the drone's last reported position is inside the area or within its margin. Height does not count.
A position not known, or older than FRESH_SECONDS, counts as at one: what is not known is not shown.

The areas of every site of the organisation apply to every one of its drones: a drone flying for one site may pass
over another's neighbour.

A snapshot from the site's gateway taken at an area is stored blank; a clip that overlaps a time at one is not kept.
Either is recorded in `drone_media_withheld`. Where the picture was taken is read from the telemetry the gateway
sent with it, and from the drone's track around it; a picture with no position at all is withheld.

Planning is told, not refused: a route that crosses an area says which, and for about how long the picture will be
blank.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.geofence import haversine_meters, normalize_polygon, point_in_polygon

#: A position older than this is not believed.
FRESH_SECONDS = 10
#: How often a drone's camera asks again whether it is at an area.
REFRESH_SECONDS = 2.0
MARGIN_DEFAULT_M = 25.0
MARGIN_MOST_M = 200.0
#: The track read around a picture, either side of when it was taken.
TRACK_SLACK = timedelta(seconds=5)
#: How finely a route is walked to see how much of it is over an area.
STEP_M = 5.0
WITHHELD_WORDS = "Picture withheld: privacy area"


@dataclass(frozen=True)
class Area:
    id: str
    name: str
    shape: str
    points: tuple[tuple[float, float], ...] | None
    center: tuple[float, float] | None
    radius_m: float | None
    margin_m: float


def area_of(row: Mapping) -> Area:
    pts = normalize_polygon(row.get("polygon")) if row.get("polygon") is not None else None
    center = (float(row["center_latitude"]), float(row["center_longitude"])) \
        if row.get("center_latitude") is not None else None
    return Area(str(row["id"]), row["name"], row["shape"], tuple(pts) if pts else None, center,
                float(row["radius_m"]) if row.get("radius_m") is not None else None, float(row["margin_m"]))


def _metres(a: tuple[float, float], b: tuple[float, float], at_lat: float) -> tuple[float, float]:
    """b relative to a, in metres east and north (flat over a site's few hundred metres)."""
    k = 111_320.0
    return (b[1] - a[1]) * k * math.cos(math.radians(at_lat)), (b[0] - a[0]) * k


def _to_segment_m(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = _metres(p, a, p[0])
    bx, by = _metres(p, b, p[0])
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length))
    return math.hypot(ax + t * dx, ay + t * dy)


def contains(area: Area, lat: float, lng: float) -> bool:
    """Is this point inside the area or within its margin? An area whose shape cannot be read contains everything:
    where it was meant to be is not known, so the picture is withheld."""
    if area.shape == "CIRCLE":
        if area.center is None or area.radius_m is None:
            return True
        return haversine_meters(lat, lng, area.center[0], area.center[1]) <= area.radius_m + area.margin_m
    if not area.points:
        return True
    if point_in_polygon(lat, lng, list(area.points)):
        return True
    if area.margin_m <= 0:
        return False
    ring = list(area.points) + [area.points[0]]
    return any(_to_segment_m((lat, lng), a, b) <= area.margin_m for a, b in zip(ring, ring[1:]))


def at(lat: float | None, lng: float | None, areas: Sequence[Area]) -> list[Area]:
    if lat is None or lng is None:
        return list(areas)
    return [a for a in areas if contains(a, lat, lng)]


async def areas(db: AsyncSession) -> list[Area]:
    """The organisation's areas in force, under row level security."""
    rows = await db.execute(text("""
        SELECT id, name, shape, polygon, center_latitude, center_longitude, radius_m, margin_m
          FROM drone_privacy_areas WHERE removed_at IS NULL ORDER BY created_at, id
    """))
    return [area_of(r) for r in rows.mappings()]


# ─── A drone's camera, now ───────────────────────────────────────────────────

@dataclass(frozen=True)
class CameraState:
    is_drone: bool
    #: The organisation has areas: a drone camera is then watched, and has no copy-through stream.
    watched: bool
    withheld: bool
    over: tuple[str, ...] = ()


async def camera_state(db: AsyncSession, camera_id: Any, now: datetime | None = None) -> CameraState:
    """Whether this camera is a drone's and, if so, whether its picture is to be withheld now."""
    drone = (await db.execute(text("""
        SELECT current_latitude, current_longitude, last_heartbeat_at FROM drones WHERE camera_id = CAST(:c AS uuid)
         LIMIT 1
    """), {"c": str(camera_id)})).mappings().first()
    if drone is None:
        return CameraState(False, False, False)
    found = await areas(db)
    if not found:
        return CameraState(True, False, False)
    now = now or datetime.now(timezone.utc)
    seen = drone["last_heartbeat_at"]
    fresh = seen is not None and (now - seen).total_seconds() <= FRESH_SECONDS \
        and drone["current_latitude"] is not None and drone["current_longitude"] is not None
    if not fresh:
        return CameraState(True, True, True, ("its position is not known",))
    over = at(float(drone["current_latitude"]), float(drone["current_longitude"]), found)
    return CameraState(True, True, bool(over), tuple(a.name for a in over))


# ─── A picture from the gateway ──────────────────────────────────────────────

def _position(snapshot: Any) -> tuple[float, float] | None:
    if not isinstance(snapshot, Mapping):
        return None
    for lat_key, lng_key in (("latitude", "longitude"), ("lat", "lng"), ("lat", "lon")):
        if snapshot.get(lat_key) is not None and snapshot.get(lng_key) is not None:
            try:
                return float(snapshot[lat_key]), float(snapshot[lng_key])
            except (TypeError, ValueError):
                return None
    return None


async def where_taken(db: AsyncSession, *, drone_id: Any, session_id: Any, captured_at: datetime | None,
                      duration_seconds: float | None, telemetry_snapshot: Any) -> list[tuple[float, float]]:
    """The positions the drone reported while a picture was being taken."""
    out = []
    first = _position(telemetry_snapshot)
    if first:
        out.append(first)
    if captured_at is not None and (drone_id or session_id):
        end = captured_at + timedelta(seconds=float(duration_seconds or 0))
        rows = await db.execute(text("""
            SELECT latitude, longitude FROM drone_telemetry
             WHERE (CAST(:d AS uuid) IS NOT NULL AND drone_id = CAST(:d AS uuid)
                    OR CAST(:s AS uuid) IS NOT NULL AND session_id = CAST(:s AS uuid))
               AND recorded_at BETWEEN :a AND :b AND latitude IS NOT NULL AND longitude IS NOT NULL
             ORDER BY recorded_at LIMIT 2000
        """), {"d": str(drone_id) if drone_id else None, "s": str(session_id) if session_id else None,
               "a": captured_at - TRACK_SLACK, "b": end + TRACK_SLACK})
        out += [(float(r[0]), float(r[1])) for r in rows]
    return out


async def picture_at_area(db: AsyncSession, **where) -> list[str] | None:
    """The areas a picture was taken at - ["its position is not known"] when none was reported - or None when it was
    taken at none, or the organisation has none."""
    found = await areas(db)
    if not found:
        return None
    positions = await where_taken(db, **where)
    if not positions:
        return ["its position is not known"]
    names = sorted({a.name for lat, lng in positions for a in at(lat, lng, found)})
    return names or None


def withheld_reason(kind: str, names: Sequence[str]) -> str:
    where = ", ".join(names)
    if kind == "SNAPSHOT":
        return f"Stored blank: it was taken over a privacy area ({where})."
    return f"Not kept: it was recorded over a privacy area ({where})."


def blank_jpeg(width: int = 1280, height: int = 720) -> bytes:
    """The picture a withheld snapshot is stored as: solid, saying why."""
    import cv2
    import numpy as np

    frame = np.zeros((height, width, 3), dtype=np.uint8)
    paint_withheld(frame)
    ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return bytes(data) if ok else b""


def blank_like(image: bytes) -> bytes:
    """A withheld snapshot's stored picture: the size of the one sent, painted solid, saying why. Nothing of what was
    sent is kept."""
    import cv2
    import numpy as np

    frame = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR) if image else None
    if frame is None:
        return blank_jpeg()
    paint_withheld(frame)
    ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return bytes(data) if ok else blank_jpeg()


def paint_withheld(frame):
    """Paint a frame solid, in place, with the words that say why."""
    import cv2

    frame[:] = (40, 40, 40)
    height, width = frame.shape[:2]
    scale = max(0.6, width / 1100)
    (tw, th), _ = cv2.getTextSize(WITHHELD_WORDS, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    cv2.putText(frame, WITHHELD_WORDS, ((width - tw) // 2, (height + th) // 2), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (220, 220, 220), 2, cv2.LINE_AA)
    return frame


# ─── A route ─────────────────────────────────────────────────────────────────

def crossings(path: Sequence[tuple[float, float]], speeds: Sequence[float], found: Sequence[Area]) -> list[dict]:
    """For each area a path crosses: how many metres of it are over the area, and about how many seconds the picture
    is blank there at the speeds flown. `speeds[i]` is the speed of the leg path[i] -> path[i+1]."""
    if len(path) < 2 or not found:
        return []
    metres = {a.id: 0.0 for a in found}
    for i, (a, b) in enumerate(zip(path, path[1:])):
        leg = haversine_meters(a[0], a[1], b[0], b[1])
        steps = max(1, int(leg // STEP_M))
        piece = leg / steps
        speed = speeds[i] if i < len(speeds) and speeds[i] else 5.0
        for n in range(steps):
            t = (n + 0.5) / steps
            lat, lng = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            for area in found:
                if contains(area, lat, lng):
                    metres[area.id] += piece
        for area in found:
            metres.setdefault(area.id, 0.0)
    out = []
    by_id = {a.id: a for a in found}
    for area_id, m in metres.items():
        if m <= 0:
            continue
        legs = [s for s in speeds if s] or [5.0]
        speed = sum(legs) / len(legs)
        out.append({"area_id": area_id, "name": by_id[area_id].name, "metres": round(m), "seconds": round(m / speed)})
    return sorted(out, key=lambda x: -x["metres"])


def crossing_words(found: Sequence[Mapping]) -> str | None:
    if not found:
        return None
    seconds = sum(c["seconds"] for c in found)
    names = ", ".join(c["name"] for c in found)
    many = "s" if len(found) != 1 else ""
    return (f"This route crosses {len(found)} privacy area{many}: the picture will be blank for about {seconds} "
            f"seconds ({names}).")


async def route_crossings(db: AsyncSession, route_id: Any) -> list[dict]:
    """The areas a saved route crosses, from its base, its waypoints in order, and back to base if it returns."""
    route = (await db.execute(text("""
        SELECT base_latitude, base_longitude, return_to_base, default_speed_mps FROM drone_routes
         WHERE id = CAST(:r AS uuid)
    """), {"r": str(route_id)})).mappings().first()
    if route is None or route["base_latitude"] is None:
        return []
    found = await areas(db)
    if not found:
        return []
    wps = (await db.execute(text("""
        SELECT latitude, longitude, speed_mps FROM drone_waypoints WHERE route_id = CAST(:r AS uuid) ORDER BY sequence
    """), {"r": str(route_id)})).mappings().all()
    base = (float(route["base_latitude"]), float(route["base_longitude"]))
    path = [base, *[(float(w["latitude"]), float(w["longitude"])) for w in wps]]
    default = float(route["default_speed_mps"] or 5)
    speeds = [float(w["speed_mps"] or default) for w in wps]
    if route["return_to_base"] and wps:
        path.append(base)
        speeds.append(default)
    return crossings(path, speeds, found)
