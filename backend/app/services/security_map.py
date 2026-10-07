"""The security map: what the platform knows the position of, in layers.

  SITE        every site, with how many of its cameras are up
  CAMERA      each camera with a position, and its stream's state
  GUARD       each guard on shift, at the LAST POSITION THEY RECORDED, with its age
  INCIDENT    open incidents, where they were reported from or at their camera
  ALERT       live alerts, at their camera
  SITUATION   open situations
  DRONE       each drone, where it last reported being
  CHECKPOINT  patrol checkpoints, and when each was last scanned
  PLACE       the places an administrator drew: buildings, gates, doors, zones
  DRONE_ZONE  the zones drawn for drone patrols

EACH LAYER IS SHOWN TO SOMEONE WHO MAY ALREADY READ WHAT IS ON IT. The
permission is the one that thing's own screen asks for. The map gives nobody a
camera, an incident or a colleague's whereabouts they could not see elsewhere,
and every layer is held to the caller's sites.

A THING WITH NO POSITION IS COUNTED, NOT HIDDEN. An incident whose camera was
never given coordinates cannot be drawn; `without_position` says how many there
are, so that an empty patch of map is not read as a quiet one. A layer that was
left out is listed with the reason.

A GUARD'S POSITION IS NOT LIVE (services/guard_positions.py). It is where they
last clocked in, scanned or reported, and says how long ago.

NOTHING HERE ACTS. `around` lists what is near an incident, nearest guard
first, for a person to read. It sends nobody anywhere.

Alarm panels and sensors have no position of their own in the platform and are
not drawn; they are on their own screens.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.sites import is_site_allowed, site_scope_clause
from app.services import guard_positions

#: How far back the live layers look when not told.
DEFAULT_HOURS = 24
MAX_HOURS = 168
#: The most features one layer returns. More than this is said, not dropped silently.
MAX_PER_LAYER = 500
DEFAULT_RADIUS_M = 300
MAX_RADIUS_M = 5000
#: A door event of one of these kinds, this recently, is worth a look.
DOOR_ALERTS = ("forced", "held_open", "tamper")
DOOR_ALERT_MINUTES = 15
PLACE_KINDS = ("BUILDING", "FLOOR", "GATE", "ACCESS_POINT", "EMERGENCY_POINT", "ASSEMBLY_POINT", "ZONE", "PARKING",
               "OTHER")
NOTE = ("Each layer shows what you may already read elsewhere, at your own sites. Something with no position "
        "recorded cannot be drawn and is counted under “without a position”. " + guard_positions.NOTE)


@dataclass(frozen=True)
class Layer:
    key: str
    label: str
    permission: str
    #: Whether the look-back period applies to it.
    live: bool = False


LAYERS: tuple[Layer, ...] = (
    Layer("SITE", "Sites", "site:read"),
    Layer("CAMERA", "Cameras", "camera:read"),
    Layer("GUARD", "Guards on shift", "shift:read"),
    Layer("INCIDENT", "Open incidents", "incident:read", live=True),
    Layer("ALERT", "Live alerts", "alert:read", live=True),
    Layer("SITUATION", "Open situations", "intel:read", live=True),
    Layer("DRONE", "Drones", "drone:read"),
    Layer("CHECKPOINT", "Patrol checkpoints", "patrol:read"),
    Layer("PLACE", "Places", "sitemap:read"),
    Layer("DRONE_ZONE", "Drone zones", "drone:read"),
)
BY_KEY = {layer.key: layer for layer in LAYERS}
#: Every permission the map consults: a layer's, or the one a door's state needs.
PERMISSIONS = frozenset({layer.permission for layer in LAYERS} | {"access:read", "sitemap:manage",
                                                                    "investigation:read"})


def outline(stored: Any) -> list[list[float]] | None:
    """An outline as [[lat, lng], ...] whichever way it was stored — or None
    when what is stored is not one."""
    if stored is None:
        return None
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except ValueError:
            return None
    if not isinstance(stored, list):
        return None
    points: list[list[float]] = []
    for point in stored:
        try:
            if isinstance(point, Mapping):
                lat = point.get("lat", point.get("latitude"))
                lng = point.get("lng", point.get("lon", point.get("longitude")))
            else:
                lat, lng = point[0], point[1]
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError, IndexError, KeyError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return None
        points.append([lat, lng])
    return points if len(points) >= 3 else None


def centre(points: Sequence[Sequence[float]] | None) -> tuple[float, float] | tuple[None, None]:
    """The middle of an outline: where to measure a distance to it from."""
    if not points:
        return None, None
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def _feature(layer: str, row: Mapping, *, label: Any, lat: Any, lng: Any, state: Any, at: Any = None,
             shape: list | None = None, **detail) -> dict:
    return {
        "layer": layer, "id": str(row["id"]), "label": label,
        "latitude": float(lat) if lat is not None else None, "longitude": float(lng) if lng is not None else None,
        "state": state, "at": at, "site_id": row.get("site_id"), "site_name": row.get("site_name"),
        "outline": shape, "detail": detail,
    }


def _where(allowed, column: str, params: dict, site_id: Any, extra: Sequence[str] = ()) -> str:
    parts = list(extra)
    scope = site_scope_clause(allowed, column, params)
    if scope:
        parts.append(scope)
    if site_id is not None:
        parts.append(f"{column} = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    return ("WHERE " + " AND ".join(parts)) if parts else ""


_LATEST_STREAM = """
    LEFT JOIN LATERAL (SELECT status, last_frame_at FROM streams st
                        WHERE st.camera_id = c.id ORDER BY st.updated_at DESC LIMIT 1) ls ON TRUE
"""


async def _sites(db, allowed, site_id, since, only=None) -> list[dict]:
    params: dict = {}
    rows = await db.execute(text(f"""
        SELECT s.id, s.id AS site_id, s.name AS site_name, s.address, s.latitude, s.longitude,
               s.geofence_radius_meters, s.geofence_polygon,
               count(DISTINCT c.id) AS cameras,
               count(DISTINCT c.id) FILTER (WHERE ls.status = 'online') AS online,
               count(DISTINCT c.id) FILTER (WHERE ls.status = 'offline') AS offline
          FROM sites s
          LEFT JOIN cameras c ON c.site_id = s.id AND c.is_active
          {_LATEST_STREAM}
          {_where(allowed, 's.id', params, site_id, ['s.is_active'])}
         GROUP BY s.id ORDER BY s.name
    """), params)
    return [_feature("SITE", r, label=r["site_name"], lat=r["latitude"], lng=r["longitude"],
                     state="attention" if r["offline"] else "ok", shape=outline(r["geofence_polygon"]),
                     address=r["address"], cameras=r["cameras"], cameras_online=r["online"],
                     cameras_offline=r["offline"], radius_m=r["geofence_radius_meters"])
            for r in rows.mappings()]


async def _cameras(db, allowed, site_id, since, only=None) -> list[dict]:
    params: dict = {}
    rows = await db.execute(text(f"""
        SELECT c.id, c.name, c.location, c.latitude, c.longitude, c.site_id, s.name AS site_name, ls.status,
               ls.last_frame_at
          FROM cameras c
          LEFT JOIN sites s ON s.id = c.site_id
          {_LATEST_STREAM}
          {_where(allowed, 'c.site_id', params, site_id, ['c.is_active'])}
         ORDER BY c.name LIMIT {MAX_PER_LAYER + 1}
    """), params)
    return [_feature("CAMERA", r, label=r["name"], lat=r["latitude"], lng=r["longitude"],
                     state=r["status"] or "unknown", at=r["last_frame_at"], location=r["location"])
            for r in rows.mappings()]


async def _guards(db, allowed, site_id, since, only=None) -> list[dict]:
    now = datetime.now(timezone.utc)
    return [_feature("GUARD", {"id": g["user_id"], "site_id": g["site_id"], "site_name": g["site_name"]},
                     label=g["full_name"], lat=g["latitude"], lng=g["longitude"],
                     state="emergency" if g["emergency_id"] else "available" if g["available"] else "busy",
                     at=g["position_at"], position_source=g["position_source"],
                     position_age_s=g["position_age_s"], stale=g["stale"], busy_incident_id=g["busy_incident_id"],
                     shift_started_at=g["shift_started_at"])
            for g in await guard_positions.on_shift(db, now, allowed, site_id=site_id)]


_INCIDENT = """
    SELECT i.id, i.title, i.severity, i.status, i.created_at, i.camera_id, c.name AS camera_name, c.site_id,
           s.name AS site_name, COALESCE(h.latitude, c.latitude) AS latitude,
           COALESCE(h.longitude, c.longitude) AS longitude, h.latitude IS NOT NULL AS from_the_ground,
           i.dispatched_guard_id, g.full_name AS dispatched_guard_name, i.dispatched_at, i.guard_arrived_at,
           i.sla_deadline_at, i.sla_breached
      FROM incidents i
      LEFT JOIN cameras c ON c.id = i.camera_id
      LEFT JOIN sites s ON s.id = c.site_id
      LEFT JOIN users g ON g.id = i.dispatched_guard_id
      LEFT JOIN LATERAL (SELECT latitude, longitude FROM incident_status_history
                          WHERE incident_id = i.id AND latitude IS NOT NULL
                          ORDER BY changed_at DESC LIMIT 1) h ON TRUE
"""


def _incident(r: Mapping) -> dict:
    return _feature("INCIDENT", r, label=r["title"], lat=r["latitude"], lng=r["longitude"], state=r["severity"],
                    at=r["created_at"], status=r["status"], camera_name=r["camera_name"],
                    position_from="reported from the ground" if r["from_the_ground"] else "its camera",
                    dispatched_guard_name=r["dispatched_guard_name"], dispatched_at=r["dispatched_at"],
                    guard_arrived_at=r["guard_arrived_at"], sla_deadline_at=r["sla_deadline_at"],
                    sla_breached=r["sla_breached"])


async def _incidents(db, allowed, site_id, since, only=None) -> list[dict]:
    params: dict = {}
    extra = ["i.id = CAST(:only AS uuid)"] if only else ["i.status NOT IN ('resolved','closed')",
                                                         "i.created_at >= :since"]
    params.update({"only": str(only)} if only else {"since": since})
    rows = await db.execute(text(f"""{_INCIDENT}
          {_where(allowed, 'c.site_id', params, site_id, extra)}
         ORDER BY i.created_at DESC LIMIT {MAX_PER_LAYER + 1}"""), params)
    return [_incident(r) for r in rows.mappings()]


async def _alerts(db, allowed, site_id, since, only=None) -> list[dict]:
    params: dict = {}
    extra = ["a.id = CAST(:only AS uuid)"] if only else ["a.status IN ('open','acknowledged','investigating')",
                                                         "a.created_at >= :since"]
    params.update({"only": str(only)} if only else {"since": since})
    rows = await db.execute(text(f"""
        SELECT a.id, a.title, a.severity, a.status, a.module_type, a.created_at, c.name AS camera_name,
               COALESCE(a.site_id, c.site_id) AS site_id, s.name AS site_name, c.latitude, c.longitude
          FROM alerts a
          LEFT JOIN cameras c ON c.id = a.camera_id
          LEFT JOIN sites s ON s.id = COALESCE(a.site_id, c.site_id)
          {_where(allowed, 'COALESCE(a.site_id, c.site_id)', params, site_id, extra)}
         ORDER BY a.created_at DESC LIMIT {MAX_PER_LAYER + 1}
    """), params)
    return [_feature("ALERT", r, label=r["title"], lat=r["latitude"], lng=r["longitude"], state=r["severity"],
                     at=r["created_at"], status=r["status"], kind=r["module_type"], camera_name=r["camera_name"])
            for r in rows.mappings()]


async def _situations(db, allowed, site_id, since, only=None) -> list[dict]:
    params: dict = {}
    extra = ["ss.id = CAST(:only AS uuid)"] if only else ["ss.closed_at IS NULL", "ss.last_event_at >= :since"]
    params.update({"only": str(only)} if only else {"since": since})
    rows = await db.execute(text(f"""
        SELECT ss.id, ss.situation_number, ss.title, ss.severity, ss.risk_level, ss.risk_score, ss.decision_status,
               ss.started_at, ss.last_event_at, ss.site_id, s.name AS site_name, c.name AS camera_name,
               COALESCE(ss.latitude, c.latitude) AS latitude, COALESCE(ss.longitude, c.longitude) AS longitude
          FROM security_situations ss
          LEFT JOIN cameras c ON c.id = ss.primary_camera_id
          LEFT JOIN sites s ON s.id = ss.site_id
          {_where(allowed, 'ss.site_id', params, site_id, extra)}
         ORDER BY ss.last_event_at DESC LIMIT {MAX_PER_LAYER + 1}
    """), params)
    return [_feature("SITUATION", r, label=r["title"], lat=r["latitude"], lng=r["longitude"],
                     state=(r["risk_level"] or r["severity"] or "").lower() or None, at=r["last_event_at"],
                     number=r["situation_number"], risk_level=r["risk_level"], risk_score=r["risk_score"],
                     stands=r["decision_status"], camera_name=r["camera_name"], started_at=r["started_at"])
            for r in rows.mappings()]


async def _drones(db, allowed, site_id, since, only=None) -> list[dict]:
    params: dict = {}
    rows = await db.execute(text(f"""
        SELECT d.id, d.name, d.code, d.status, d.battery_level, d.current_latitude, d.current_longitude,
               d.current_altitude_m, d.last_heartbeat_at, d.site_id, s.name AS site_name
          FROM drones d LEFT JOIN sites s ON s.id = d.site_id
          {_where(allowed, 'd.site_id', params, site_id)}
         ORDER BY d.name LIMIT {MAX_PER_LAYER + 1}
    """), params)
    return [_feature("DRONE", r, label=r["name"], lat=r["current_latitude"], lng=r["current_longitude"],
                     state=(r["status"] or "unknown").lower(), at=r["last_heartbeat_at"], code=r["code"],
                     battery_level=r["battery_level"],
                     altitude_m=float(r["current_altitude_m"]) if r["current_altitude_m"] is not None else None)
            for r in rows.mappings()]


async def _checkpoints(db, allowed, site_id, since, only=None) -> list[dict]:
    params: dict = {}
    rows = await db.execute(text(f"""
        SELECT pc.id, pc.name, pc.sequence, pc.latitude, pc.longitude, pr.id AS route_id, pr.name AS route_name,
               pr.site_id, s.name AS site_name,
               (SELECT max(cs.scanned_at) FROM checkpoint_scans cs WHERE cs.checkpoint_id = pc.id) AS last_scanned_at
          FROM patrol_checkpoints pc
          JOIN patrol_routes pr ON pr.id = pc.route_id AND pr.is_active
          LEFT JOIN sites s ON s.id = pr.site_id
          {_where(allowed, 'pr.site_id', params, site_id, ['pc.is_active'])}
         ORDER BY pr.name, pc.sequence LIMIT {MAX_PER_LAYER + 1}
    """), params)
    return [_feature("CHECKPOINT", r, label=r["name"], lat=r["latitude"], lng=r["longitude"],
                     state="scanned" if r["last_scanned_at"] else "never scanned", at=r["last_scanned_at"],
                     route_id=r["route_id"], route_name=r["route_name"], sequence=r["sequence"])
            for r in rows.mappings()]


_PLACE = """
    SELECT p.id, p.kind, p.name, p.parent_id, pp.name AS parent_name, p.level, p.latitude, p.longitude, p.polygon,
           p.door_id, d.name AS door_name, p.description, p.is_active, p.site_id, s.name AS site_name,
           p.created_at, p.updated_at
      FROM site_places p
      LEFT JOIN site_places pp ON pp.id = p.parent_id
      LEFT JOIN access_doors d ON d.id = p.door_id
      LEFT JOIN sites s ON s.id = p.site_id
"""


def place(r: Mapping, door: Mapping | None = None, now: datetime | None = None) -> dict:
    """A place as the map and the editor both read it."""
    shape = outline(r["polygon"])
    lat, lng = r["latitude"], r["longitude"]
    if lat is None and shape:
        lat, lng = centre(shape)
    state = r["kind"].lower()
    if door is not None and now is not None and door["event_type"] in DOOR_ALERTS \
            and now - door["occurred_at"] <= timedelta(minutes=DOOR_ALERT_MINUTES):
        state = "alert"
    return _feature("PLACE", r, label=r["name"], lat=lat, lng=lng, state=state,
                    at=door["occurred_at"] if door else None, shape=shape, kind=r["kind"], parent_id=r["parent_id"],
                    parent_name=r["parent_name"], level=r["level"], door_id=r["door_id"], door_name=r["door_name"],
                    description=r["description"], is_active=r["is_active"],
                    last_door_event=door["event_type"] if door else None,
                    has_point=r["latitude"] is not None)


async def _places(db, allowed, site_id, since, only=None, *, door_states: bool = False,
                  with_retired: bool = False) -> list[dict]:
    params: dict = {}
    rows = (await db.execute(text(f"""{_PLACE}
          {_where(allowed, 'p.site_id', params, site_id, [] if with_retired else ['p.is_active'])}
         ORDER BY s.name, p.kind, p.name LIMIT {MAX_PER_LAYER + 1}"""), params)).mappings().all()
    doors: dict = {}
    wanted = [r["door_id"] for r in rows if r["door_id"] is not None]
    if door_states and wanted:
        # Only for someone who may read door events: the last one at each door.
        for d in (await db.execute(text("""
            SELECT DISTINCT ON (ae.door_id) ae.door_id, ae.event_type, ae.occurred_at
              FROM access_events ae WHERE ae.door_id = ANY(:doors)
             ORDER BY ae.door_id, ae.occurred_at DESC
        """), {"doors": wanted})).mappings():
            doors[d["door_id"]] = d
    now = datetime.now(timezone.utc)
    return [place(r, doors.get(r["door_id"]), now) for r in rows]


async def _drone_zones(db, allowed, site_id, since, only=None) -> list[dict]:
    params: dict = {}
    rows = await db.execute(text(f"""
        SELECT z.id, z.name, z.zone_type, z.shape, z.polygon, z.center_latitude, z.center_longitude, z.radius_m,
               z.severity, z.site_id, s.name AS site_name
          FROM drone_security_zones z LEFT JOIN sites s ON s.id = z.site_id
          {_where(allowed, 'z.site_id', params, site_id, ['z.is_active'])}
         ORDER BY z.name LIMIT {MAX_PER_LAYER + 1}
    """), params)
    out = []
    for r in rows.mappings():
        shape = outline(r["polygon"])
        lat, lng = (r["center_latitude"], r["center_longitude"])
        if lat is None and shape:
            lat, lng = centre(shape)
        out.append(_feature("DRONE_ZONE", r, label=r["name"], lat=lat, lng=lng,
                            state=(r["zone_type"] or "").lower() or None, shape=shape, severity=r["severity"],
                            radius_m=float(r["radius_m"]) if r["radius_m"] is not None else None))
    return out


READERS = {"SITE": _sites, "CAMERA": _cameras, "GUARD": _guards, "INCIDENT": _incidents, "ALERT": _alerts,
           "SITUATION": _situations, "DRONE": _drones, "CHECKPOINT": _checkpoints, "PLACE": _places,
           "DRONE_ZONE": _drone_zones}


def _drawable(feature: Mapping) -> bool:
    return feature["latitude"] is not None or bool(feature["outline"])


async def features(db: AsyncSession, mine, allowed: list[str] | None, *, site_id: Any = None,
                   layers: Sequence[str] = (), hours: int = DEFAULT_HOURS, now: datetime | None = None) -> dict:
    """The layers asked for (all of them when none is named), each as the
    caller may see it."""
    now = now or datetime.now(timezone.utc)
    hours = max(1, min(hours, MAX_HOURS))
    since = now - timedelta(hours=hours)
    out: dict = {"as_of": now, "hours": hours, "layers": {}, "without_position": {}, "more": {}, "not_shown": [],
                 "note": NOTE}
    for layer in LAYERS:
        if layers and layer.key not in layers:
            continue
        if layer.permission not in mine:
            out["not_shown"].append({"layer": layer.key, "label": layer.label,
                                     "reason": f"You do not hold the permission {layer.permission}."})
            continue
        if layer.key == "PLACE":
            found = await _places(db, allowed, site_id, since, door_states="access:read" in mine)
        else:
            found = await READERS[layer.key](db, allowed, site_id, since)
        if len(found) > MAX_PER_LAYER:
            out["more"][layer.key] = True
            found = found[:MAX_PER_LAYER]
        drawn = [f for f in found if _drawable(f)]
        out["layers"][layer.key] = drawn
        if len(drawn) != len(found):
            out["without_position"][layer.key] = len(found) - len(drawn)
    return out


async def around(db: AsyncSession, kind: str, thing_id: Any, mine, allowed: list[str] | None, *,
                 radius_m: int = DEFAULT_RADIUS_M) -> dict | None:
    """What is near one incident, alert or situation: cameras, guards, drones,
    places and checkpoints within `radius_m`, nearest first — and the guards on
    shift at its site, nearest first, however far. None when the thing is not
    the caller's to see.

    For a person to read. It sends nobody anywhere."""
    layer = BY_KEY.get(kind)
    if layer is None or kind not in ("INCIDENT", "ALERT", "SITUATION") or layer.permission not in mine:
        return None
    radius_m = max(10, min(radius_m, MAX_RADIUS_M))
    found = await READERS[kind](db, allowed, None, None, only=uuid.UUID(str(thing_id)))
    if not found:
        return None
    subject = found[0]
    if allowed is not None and not is_site_allowed(allowed, subject["site_id"]):
        return None
    lat, lng, site_id = subject["latitude"], subject["longitude"], subject["site_id"]
    answer: dict = {"subject": subject, "located": lat is not None, "radius_m": radius_m, "nearby": {},
                    "nearest_guards": [], "not_shown": [], "note": guard_positions.NOTE}

    for key in ("CAMERA", "DRONE", "CHECKPOINT", "PLACE"):
        other = BY_KEY[key]
        if other.permission not in mine:
            answer["not_shown"].append({"layer": key, "label": other.label,
                                        "reason": f"You do not hold the permission {other.permission}."})
            continue
        if key == "PLACE":
            listed = await _places(db, allowed, site_id, None, door_states="access:read" in mine)
        else:
            listed = await READERS[key](db, allowed, site_id, None)
        near = []
        for f in listed:
            d = guard_positions.distance_m(f["latitude"], f["longitude"], lat, lng)
            if d is not None and d <= radius_m:
                near.append({**f, "distance_m": d})
        answer["nearby"][key] = sorted(near, key=lambda f: f["distance_m"])

    guards = BY_KEY["GUARD"]
    if guards.permission not in mine:
        answer["not_shown"].append({"layer": "GUARD", "label": guards.label,
                                    "reason": f"You do not hold the permission {guards.permission}."})
    elif site_id is not None:
        now = datetime.now(timezone.utc)
        ranked = guard_positions.nearest(await guard_positions.on_shift(db, now, allowed, site_id=site_id), lat, lng)
        answer["nearest_guards"] = ranked
        answer["nearby"]["GUARD"] = [g for g in ranked if g["distance_m"] is not None and g["distance_m"] <= radius_m]
    return answer


async def held_permissions(db: AsyncSession, role_id: int) -> frozenset[str]:
    rows = await db.execute(text("""
        SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
         WHERE rp.role_id = :role AND p.code = ANY(CAST(:codes AS text[]))
    """), {"role": role_id, "codes": sorted(PERMISSIONS)})
    return frozenset(r.code for r in rows)
