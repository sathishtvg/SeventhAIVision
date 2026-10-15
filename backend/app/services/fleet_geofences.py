"""Fleet geofences: which side of each geofence a vehicle is on, and the alerts its rules ask for.

Design: docs/plans/2026-10-14-gps-fleet-tracking-design.md, section 5.

SHAPES. A circle of 25 m to 50 km, or a polygon of 3 to 200 points whose edges do not cross. A geofence belongs
to the organisation, or to one of its sites; it applies to the vehicles of the groups it names, or else to every
vehicle of its site (of the organisation, when it has no site).

A FIX THAT WOBBLES ON THE BOUNDARY CHANGES NOTHING. Each vehicle's side of each geofence is kept
(`fleet_geofence_state`). A fix counts for a side only when it is past the boundary by at least
max(15 m, the accuracy the device reported); a fix nearer than that is on the line and is ignored. The side
changes when a second fix agrees, or when 30 seconds pass with no fix saying otherwise (the minute pass). The
change is dated at the first fix on the new side. A position older than the last one looked at never changes
the side. When nothing was heard for more than 15 minutes across the change, it is recorded as having happened
"between" the last fix on the old side and the first on the new: nobody knows when.

The first fix after a geofence is made, or after a vehicle starts reporting, says which side it is on and
raises nothing: a vehicle parked inside a new geofence has not entered it.

RULES (one of each kind per geofence): entry, exit, inside longer than N minutes, outside longer than N
minutes, overspeed inside. Every entry and exit is recorded (`geofence_events`); an alert is raised only where
a rule asks, and the same alert for the same vehicle and geofence is not raised again within 2 minutes.
Overspeed needs the speed the device reported (never one worked out from two positions), over the limit at
two fixes running, and is raised once per episode. A stay is counted from the change to the last fix heard,
by the minute pass, and raised once per stay.

A GEOFENCE NEVER OPENS A BARRIER, ADMITS ANYBODY OR CONFIRMS WHO IS DRIVING. It records where a vehicle's
tracker said the vehicle was and raises an alert for a person to look at. Nothing here reads or writes access
control, barriers, doors or people's identities.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import fleet_alerts as fa
from app.services.geofence import haversine_meters, point_in_polygon

logger = logging.getLogger(__name__)

KINDS = ("entry", "exit", "dwell_inside", "dwell_outside", "overspeed")
KIND_LABEL = {"entry": "Enters", "exit": "Leaves", "dwell_inside": "Inside longer than",
              "dwell_outside": "Outside longer than", "overspeed": "Faster than, inside"}
#: The event type written to geofence_events for each kind: the older endpoint's names where it had one.
EVENT_TYPE = {"entry": "entry", "exit": "exit", "dwell_inside": "dwell_inside", "dwell_outside": "dwell_outside",
              "overspeed": "speed_violation"}
ALERT_CODE = {k: f"gps.{EVENT_TYPE[k]}" for k in KINDS}

LEAST_RADIUS_M, MOST_RADIUS_M = 25.0, 50_000.0
LEAST_POINTS, MOST_POINTS = 3, 200
LEAST_MARGIN_M = 15.0
HOLD = timedelta(seconds=30)
AGAIN_AFTER = timedelta(minutes=2)
SILENT = timedelta(minutes=15)
OVER_FIXES = 2
CIRCLE_POINTS = 48


# ── shapes ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Shape:
    kind: str
    points: tuple[tuple[float, float], ...] = ()
    center: tuple[float, float] | None = None
    radius_m: float | None = None


def _point(p: Any) -> tuple[float, float]:
    if isinstance(p, Mapping):
        lat, lon = p.get("lat"), p.get("lon", p.get("lng"))
    else:
        lat, lon = p[0], p[1]
    lat, lon = float(lat), float(lon)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("A point is outside the world")
    return lat, lon


def _crosses(a, b, c, d) -> bool:
    def turn(p, q, r):
        v = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
        return 0 if abs(v) < 1e-15 else (1 if v > 0 else -1)
    return turn(a, b, c) != turn(a, b, d) and turn(c, d, a) != turn(c, d, b) and 0 not in (
        turn(a, b, c), turn(a, b, d), turn(c, d, a), turn(c, d, b))


def check_polygon(raw: Any) -> list[tuple[float, float]]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError("A polygon is a list of points")
    try:
        points = [_point(p) for p in raw]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("Each point of a polygon is {lat, lon}") from None
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if not LEAST_POINTS <= len(points) <= MOST_POINTS:
        raise ValueError(f"A polygon has {LEAST_POINTS} to {MOST_POINTS} points")
    if len(set(points)) != len(points):
        raise ValueError("A polygon has the same point twice")
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            if abs(i - j) in (1, n - 1):
                continue
            if _crosses(points[i], points[(i + 1) % n], points[j], points[(j + 1) % n]):
                raise ValueError("The polygon's edges cross each other")
    if _area_m2(points) < 1.0:
        raise ValueError("The polygon encloses no area")
    return points


def check_circle(center: Any, radius: Any) -> tuple[tuple[float, float], float]:
    try:
        c = _point(center)
        r = float(radius)
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("A circle is a centre {lat, lon} and a radius in metres") from None
    if not (math.isfinite(r) and LEAST_RADIUS_M <= r <= MOST_RADIUS_M):
        raise ValueError(f"A circle's radius is {LEAST_RADIUS_M:.0f} m to {MOST_RADIUS_M / 1000:.0f} km")
    return c, r


def circle_outline(center: tuple[float, float], radius_m: float, n: int = CIRCLE_POINTS) -> list[dict]:
    """The circle as a polygon, for the `polygon` column the older position endpoint reads."""
    lat0, lon0 = center
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(math.cos(math.radians(lat0)), 1e-6))
    return [{"lat": lat0 + dlat * math.sin(2 * math.pi * i / n), "lon": lon0 + dlon * math.cos(2 * math.pi * i / n)}
            for i in range(n)]


def shape_of(row: Mapping[str, Any]) -> Shape | None:
    """The shape a stored geofence has, or None when it is unusable (it is then skipped, never guessed)."""
    try:
        if row.get("shape") == "circle":
            c, r = check_circle({"lat": row["center_lat"], "lon": row["center_lon"]}, row["radius_m"])
            return Shape("circle", center=c, radius_m=r)
        raw = row["polygon"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        return Shape("polygon", points=tuple(check_polygon(raw)))
    except (ValueError, TypeError, KeyError):
        return None


def _local(lat0: float, lon0: float):
    k = 111_320.0
    c = math.cos(math.radians(lat0))
    return lambda lat, lon: ((lon - lon0) * k * c, (lat - lat0) * k)


def _area_m2(points: Sequence[tuple[float, float]]) -> float:
    to = _local(*points[0])
    xy = [to(*p) for p in points]
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]))) / 2


def where(lat: float, lon: float, shape: Shape) -> tuple[bool, float]:
    """Inside or not, and how far the point is from the boundary in metres."""
    if shape.kind == "circle":
        d = haversine_meters(lat, lon, *shape.center)
        return d <= shape.radius_m, abs(d - shape.radius_m)
    inside = point_in_polygon(lat, lon, shape.points)
    to = _local(lat, lon)
    xy = [to(*p) for p in shape.points]
    best = math.inf
    for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]):
        dx, dy = x2 - x1, y2 - y1
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / length))
        best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return inside, best


def side(lat: float, lon: float, accuracy: float | None, shape: Shape) -> bool | None:
    """True inside, False outside, None on the line: nearer the boundary than max(15 m, the fix's accuracy)."""
    inside, margin = where(lat, lon, shape)
    need = max(LEAST_MARGIN_M, float(accuracy) if accuracy is not None and accuracy > 0 else 0.0)
    return inside if margin >= need else None


# ── which geofences apply to a vehicle ────────────────────────────────────────

_APPLYING = """
    SELECT g.id, g.name, g.site_id, g.shape, g.polygon, g.center_lat, g.center_lon, g.radius_m,
           COALESCE((SELECT json_agg(json_build_object(
                         'id', r.id, 'kind', r.kind, 'minutes', r.minutes, 'speed_kmh', r.speed_kmh,
                         'severity', r.severity, 'users', r.recipient_user_ids, 'roles', r.recipient_role_ids))
                       FROM fleet_geofence_rules r WHERE r.geofence_id = g.id AND r.is_active), '[]') AS rules
      FROM geofences g
     WHERE g.is_active AND g.archived_at IS NULL
       AND CASE WHEN EXISTS (SELECT 1 FROM fleet_geofence_groups gg WHERE gg.geofence_id = g.id)
                THEN EXISTS (SELECT 1 FROM fleet_geofence_groups gg
                               JOIN fleet_vehicle_group_members m ON m.group_id = gg.group_id
                               JOIN fleet_vehicle_groups vg ON vg.id = gg.group_id AND vg.is_active
                              WHERE gg.geofence_id = g.id AND m.vehicle_id = CAST(:v AS uuid))
                ELSE g.site_id IS NULL OR g.site_id = CAST(:site AS uuid) END
"""


def _rules(raw: Any) -> dict[str, dict]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return {r["kind"]: r for r in raw or []}


async def applying(db: AsyncSession, v: Mapping[str, Any]) -> list[dict]:
    rows = (await db.execute(text(_APPLYING), {"v": str(v["id"]), "site": str(v["site_id"]) if v.get("site_id") else None})).mappings()
    return [{**dict(r), "rules": _rules(r["rules"])} for r in rows]


# ── one fix ───────────────────────────────────────────────────────────────────

@dataclass
class Fix:
    at: datetime
    lat: float
    lon: float
    accuracy: float | None = None
    speed_kmh: float | None = None


async def evaluate(db: AsyncSession, tenant_id: str, v: Mapping[str, Any], fix: Fix) -> list[fa.Raised]:
    """Look at one fix - the vehicle's newest - against every geofence that applies to it."""
    raised: list[fa.Raised] = []
    fences = await applying(db, v)
    if not fences:
        return raised
    states = {str(r["geofence_id"]): dict(r) for r in (await db.execute(text(
        "SELECT * FROM fleet_geofence_state WHERE vehicle_id = CAST(:v AS uuid)"), {"v": str(v["id"])})).mappings()}
    for g in fences:
        shape = shape_of(g)
        if shape is None:
            continue
        s = states.get(str(g["id"]))
        if s is not None and fix.at <= s["last_fix_at"]:
            continue  # older than what was already looked at: it changes nothing
        now_side = side(fix.lat, fix.lon, fix.accuracy, shape)
        if s is None:
            if now_side is not None:
                await db.execute(text("""
                    INSERT INTO fleet_geofence_state (tenant_id, vehicle_id, geofence_id, inside, since, last_fix_at,
                                                      last_side_fix_at)
                    VALUES (CAST(:t AS uuid), CAST(:v AS uuid), CAST(:g AS uuid), :inside, :at, :at, :at)
                    ON CONFLICT (vehicle_id, geofence_id) DO NOTHING
                """), {"t": tenant_id, "v": str(v["id"]), "g": str(g["id"]), "inside": now_side, "at": fix.at})
            continue
        s["last_fix_at"] = fix.at
        if now_side is None:
            pass
        elif now_side == s["inside"]:
            s.update(pending_inside=None, pending_since=None, pending_fixes=0, last_side_fix_at=fix.at)
        else:
            if s["pending_inside"] is now_side:
                s["pending_fixes"] += 1
            else:
                s.update(pending_inside=now_side, pending_since=fix.at, pending_fixes=1)
            if s["pending_fixes"] >= 2 or fix.at - s["pending_since"] >= HOLD:
                raised += await _change(db, tenant_id, v, g, s, fix)
        if s["inside"] and "overspeed" in g["rules"] and fix.speed_kmh is not None:
            raised += await _overspeed(db, tenant_id, v, g, s, fix)
        await _save(db, s)
    return raised


async def _change(db: AsyncSession, tenant_id: str, v, g, s: dict, fix: Fix | None) -> list[fa.Raised]:
    """The side has changed: dated at the first fix on the new side."""
    entered = bool(s["pending_inside"])
    at = s["pending_since"]
    between = (s["last_side_fix_at"], at) if at - s["last_side_fix_at"] > SILENT else None
    s.update(inside=entered, since=at, last_side_fix_at=fix.at if fix else at, pending_inside=None,
             pending_since=None, pending_fixes=0, dwell_alerted=False, over_fixes=0, over_alerted=False)
    kind = "entry" if entered else "exit"
    rule = g["rules"].get(kind)
    words = "entered" if entered else "left"
    when = (f" between {between[0].astimezone(timezone.utc):%H:%M} and {between[1].astimezone(timezone.utc):%H:%M} UTC"
            if between else "")
    title = f"{fa.vehicle_words(v)} {words} {g['name']}{when}"
    return await _record(db, tenant_id, v, g, kind, rule, at, title, lat=fix.lat if fix else None,
                         lon=fix.lon if fix else None, accuracy=fix.accuracy if fix else None,
                         speed=fix.speed_kmh if fix else None, between=between)


async def _overspeed(db: AsyncSession, tenant_id: str, v, g, s: dict, fix: Fix) -> list[fa.Raised]:
    rule = g["rules"]["overspeed"]
    limit = float(rule["speed_kmh"])
    if fix.speed_kmh <= limit:
        s.update(over_fixes=0, over_alerted=False)
        return []
    s["over_fixes"] += 1
    if s["over_fixes"] < OVER_FIXES or s["over_alerted"]:
        return []
    s["over_alerted"] = True
    title = f"{fa.vehicle_words(v)} at {fix.speed_kmh:.0f} km/h in {g['name']} (limit {limit:.0f})"
    return await _record(db, tenant_id, v, g, "overspeed", rule, fix.at, title, lat=fix.lat, lon=fix.lon,
                         accuracy=fix.accuracy, speed=fix.speed_kmh, detail={"limit_kmh": limit})


async def _record(db: AsyncSession, tenant_id: str, v, g, kind: str, rule: Mapping | None, at: datetime, title: str,
                  *, lat=None, lon=None, accuracy=None, speed=None, between=None, detail=None) -> list[fa.Raised]:
    """The event, always; the alert where a rule asks and the same one was not raised in the last 2 minutes."""
    raised: list[fa.Raised] = []
    alert_id = None
    if rule is not None:
        again = (await db.execute(text("""
            SELECT 1 FROM geofence_events
             WHERE vehicle_id = CAST(:v AS uuid) AND geofence_id = CAST(:g AS uuid) AND event_type = :e
               AND alert_id IS NOT NULL AND occurred_at > :since
             LIMIT 1
        """), {"v": str(v["id"]), "g": str(g["id"]), "e": EVENT_TYPE[kind], "since": at - AGAIN_AFTER})).first()
        if again is None:
            told = await fa.recipients(db, v, rule.get("users") or [], rule.get("roles") or [])
            r = await fa.raise_alert(db, tenant_id, v, code=ALERT_CODE[kind], severity=rule["severity"], title=title,
                                     params={"geofence_id": str(g["id"]), "geofence": g["name"], "kind": kind,
                                             "at": at, "lat": lat, "lon": lon, "speed": speed,
                                             "between": [between[0], between[1]] if between else None},
                                     recipients=told)
            alert_id = r.alert_id
            raised.append(r)
    await db.execute(text("""
        INSERT INTO geofence_events (tenant_id, vehicle_id, geofence_id, event_type, lat, lon, speed, occurred_at,
                                     rule_id, alert_id, between_start, between_end, accuracy, detail)
        VALUES (CAST(:t AS uuid), CAST(:v AS uuid), CAST(:g AS uuid), :e, :lat, :lon, :speed, :at,
                CAST(:rule AS uuid), CAST(:alert AS uuid), :b0, :b1, :acc, CAST(:detail AS jsonb))
    """), {"t": tenant_id, "v": str(v["id"]), "g": str(g["id"]), "e": EVENT_TYPE[kind], "lat": lat, "lon": lon,
           "speed": speed, "at": at, "rule": str(rule["id"]) if rule else None, "alert": alert_id,
           "b0": between[0] if between else None, "b1": between[1] if between else None, "acc": accuracy,
           "detail": json.dumps(detail) if detail else None})
    return raised


async def _save(db: AsyncSession, s: Mapping[str, Any]) -> None:
    await db.execute(text("""
        UPDATE fleet_geofence_state
           SET inside = :inside, since = :since, last_fix_at = :last_fix_at, last_side_fix_at = :last_side_fix_at,
               pending_inside = :pending_inside, pending_since = :pending_since, pending_fixes = :pending_fixes,
               dwell_alerted = :dwell_alerted, over_fixes = :over_fixes, over_alerted = :over_alerted,
               updated_at = now()
         WHERE vehicle_id = CAST(:vehicle_id AS uuid) AND geofence_id = CAST(:geofence_id AS uuid)
    """), {k: (str(s[k]) if k in ("vehicle_id", "geofence_id") else s[k]) for k in (
        "inside", "since", "last_fix_at", "last_side_fix_at", "pending_inside", "pending_since", "pending_fixes",
        "dwell_alerted", "over_fixes", "over_alerted", "vehicle_id", "geofence_id")})


# ── the minute pass ───────────────────────────────────────────────────────────

async def minute(db: AsyncSession, tenant_id: str, now: datetime) -> list[fa.Raised]:
    """In one organisation's transaction: a side held 30 seconds with no fix saying otherwise changes; a stay
    longer than a rule's minutes is raised, once per stay."""
    raised: list[fa.Raised] = []
    rows = (await db.execute(text("""
        SELECT s.*, v.name AS v_name, v.plate_number AS v_plate, v.site_id AS v_site
          FROM fleet_geofence_state s
          JOIN vehicles v ON v.id = s.vehicle_id AND v.archived_at IS NULL
          JOIN geofences g ON g.id = s.geofence_id AND g.is_active AND g.archived_at IS NULL
         WHERE (s.pending_inside IS NOT NULL AND s.pending_since <= :held)
            OR (NOT s.dwell_alerted AND EXISTS (
                    SELECT 1 FROM fleet_geofence_rules r
                     WHERE r.geofence_id = s.geofence_id AND r.is_active
                       AND r.kind = CASE WHEN s.inside THEN 'dwell_inside' ELSE 'dwell_outside' END))
    """), {"held": now - HOLD})).mappings().all()
    fences: dict[tuple[str, str], dict] = {}
    for row in rows:
        s = {k: row[k] for k in row.keys() if not k.startswith("v_")}
        v = {"id": row["vehicle_id"], "name": row["v_name"], "plate_number": row["v_plate"], "site_id": row["v_site"]}
        key = (str(v["id"]), str(s["geofence_id"]))
        if key not in fences:
            for g in await applying(db, v):
                fences[(str(v["id"]), str(g["id"]))] = g
        g = fences.get(key)
        if g is None:
            continue  # no longer applies to this vehicle
        if s["pending_inside"] is not None and s["pending_since"] <= now - HOLD:
            raised += await _change(db, tenant_id, v, g, s, None)
        kind = "dwell_inside" if s["inside"] else "dwell_outside"
        rule = g["rules"].get(kind)
        if rule is not None and not s["dwell_alerted"] and s["last_fix_at"] - s["since"] >= timedelta(minutes=rule["minutes"]):
            s["dwell_alerted"] = True
            where_words = "inside" if s["inside"] else "outside"
            title = f"{fa.vehicle_words(v)} {where_words} {g['name']} for more than {rule['minutes']} min"
            raised += await _record(db, tenant_id, v, g, kind, rule, s["last_fix_at"], title,
                                    detail={"since": s["since"].isoformat(), "minutes": rule["minutes"]})
        await _save(db, s)
    return raised
