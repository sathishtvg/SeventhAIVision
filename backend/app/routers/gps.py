"""GPS Vehicle / Fleet Tracking router — /api/v1/gps"""
import json
from datetime import datetime, timezone, timedelta
from math import radians, sin, cos, sqrt, atan2
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/gps", tags=["gps"])

VALID_VEHICLE_TYPES = {
    "patrol_car", "motorcycle", "van", "truck",
    "golf_cart", "bicycle", "boat", "custom",
}

MOVING_SPEED_KMH = 5.0      # speed above this = moving
IDLE_TIMEOUT_MIN = 10       # minutes without position = offline


# ── helpers ──────────────────────────────────────────────────────────────────

def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _point_in_polygon(lat: float, lon: float, polygon: list) -> bool:
    """Ray-casting algorithm for point-in-polygon (polygon is list of {lat, lon} dicts)."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]["lon"], polygon[i]["lat"]
        xj, yj = polygon[j]["lon"], polygon[j]["lat"]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-10) + xi):
            inside = not inside
        j = i
    return inside


# ── vehicles ──────────────────────────────────────────────────────────────────

@router.get("/dashboard", dependencies=[Depends(require_permission("gps:read"))])
async def gps_dashboard(
    site_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    site_filter = "AND v.site_id = :site_id" if site_id else ""
    rows = await db.execute(text(f"""
        SELECT v.id, v.name, v.plate_number, v.vehicle_type, v.color,
               v.current_status, v.last_lat, v.last_lon, v.last_speed,
               v.last_position_at, v.site_id, s.name AS site_name,
               u.full_name AS driver_name,
               (SELECT COUNT(*) FROM vehicle_journeys j
                WHERE j.vehicle_id = v.id AND j.status = 'active') AS active_journey
        FROM vehicles v
        LEFT JOIN sites s ON s.id = v.site_id
        LEFT JOIN users u ON u.id = v.assigned_driver_user_id
        WHERE v.is_active = TRUE {site_filter}
        ORDER BY v.name
    """), {"site_id": site_id} if site_id else {})
    vehicles = [dict(r._mapping) for r in rows]

    total   = len(vehicles)
    moving  = sum(1 for v in vehicles if v["current_status"] == "moving")
    idle    = sum(1 for v in vehicles if v["current_status"] == "idle")
    offline = sum(1 for v in vehicles if v["current_status"] == "offline")

    # recent geofence events (last 2 hours)
    ev_rows = await db.execute(text("""
        SELECT ge.id, ge.event_type, ge.occurred_at, ge.speed,
               v.name AS vehicle_name, g.name AS geofence_name
        FROM geofence_events ge
        JOIN vehicles v  ON v.id  = ge.vehicle_id
        JOIN geofences g ON g.id = ge.geofence_id
        WHERE ge.occurred_at >= now() - INTERVAL '2 hours'
        ORDER BY ge.occurred_at DESC
        LIMIT 20
    """))
    events = [dict(r._mapping) for r in ev_rows]

    return {
        "total": total,
        "summary": {"total": total, "moving": moving, "idle": idle, "offline": offline},
        "vehicles": vehicles,
        "recent_events": events,
    }


@router.get("/vehicles", dependencies=[Depends(require_permission("gps:read"))])
async def list_vehicles(
    site_id: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    filters = "WHERE v.is_active = TRUE"
    params: dict = {}
    if site_id:
        filters += " AND v.site_id = :site_id"
        params["site_id"] = site_id
    if status:
        filters += " AND v.current_status = :status"
        params["status"] = status

    rows = await db.execute(text(f"""
        SELECT v.*, s.name AS site_name, u.full_name AS driver_name
        FROM vehicles v
        LEFT JOIN sites s ON s.id = v.site_id
        LEFT JOIN users u ON u.id = v.assigned_driver_user_id
        {filters}
        ORDER BY v.name
    """), params)
    return [dict(r._mapping) for r in rows]


@router.post("/vehicles", dependencies=[Depends(require_permission("gps:write"))])
async def create_vehicle(
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    vtype = body.get("vehicle_type", "patrol_car")
    if vtype not in VALID_VEHICLE_TYPES:
        raise HTTPException(422, f"vehicle_type must be one of {sorted(VALID_VEHICLE_TYPES)}")

    row = await db.execute(text("""
        INSERT INTO vehicles (tenant_id, site_id, name, plate_number, vehicle_type,
                              make, model, color, assigned_driver_user_id)
        VALUES (:tid, :site_id, :name, :plate, :vtype, :make, :model, :color, :driver)
        RETURNING *
    """), {
        "tid":    token.tenant_id,
        "site_id": body.get("site_id"),
        "name":   name,
        "plate":  body.get("plate_number"),
        "vtype":  vtype,
        "make":   body.get("make"),
        "model":  body.get("model"),
        "color":  body.get("color"),
        "driver": body.get("assigned_driver_user_id"),
    })
    await db.commit()
    return dict(row.mappings().one())


@router.get("/vehicles/{vehicle_id}", dependencies=[Depends(require_permission("gps:read"))])
async def get_vehicle(vehicle_id: UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    row = await db.execute(text("""
        SELECT v.*, s.name AS site_name, u.full_name AS driver_name
        FROM vehicles v
        LEFT JOIN sites s ON s.id = v.site_id
        LEFT JOIN users u ON u.id = v.assigned_driver_user_id
        WHERE v.id = :id AND v.is_active = TRUE
    """), {"id": str(vehicle_id)})
    vehicle = row.mappings().first()
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")

    # last 200 positions
    pos_rows = await db.execute(text("""
        SELECT lat, lon, speed, heading, recorded_at
        FROM vehicle_positions
        WHERE vehicle_id = :id
        ORDER BY recorded_at DESC
        LIMIT 200
    """), {"id": str(vehicle_id)})
    positions = [dict(r._mapping) for r in pos_rows]

    return {**dict(vehicle), "recent_positions": positions}


@router.put("/vehicles/{vehicle_id}", dependencies=[Depends(require_permission("gps:write"))])
async def update_vehicle(
    vehicle_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    allowed = {"name", "plate_number", "vehicle_type", "make", "model",
               "color", "site_id", "assigned_driver_user_id", "is_active"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if "vehicle_type" in updates and updates["vehicle_type"] not in VALID_VEHICLE_TYPES:
        raise HTTPException(400, f"vehicle_type must be one of {sorted(VALID_VEHICLE_TYPES)}")
    if not updates:
        raise HTTPException(400, "No valid fields to update")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = str(vehicle_id)
    await db.execute(text(f"UPDATE vehicles SET {set_clause}, updated_at = now() WHERE id = :id"), updates)
    await db.commit()
    return {"ok": True}


@router.delete("/vehicles/{vehicle_id}", dependencies=[Depends(require_permission("gps:write"))])
async def deactivate_vehicle(vehicle_id: UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text("UPDATE vehicles SET is_active = FALSE, updated_at = now() WHERE id = :id"),
                     {"id": str(vehicle_id)})
    await db.commit()
    return {"ok": True}


# ── position ingestion ────────────────────────────────────────────────────────

@router.post("/vehicles/{vehicle_id}/positions", dependencies=[Depends(require_permission("gps:ingest"))])
async def ingest_position(
    vehicle_id: UUID,
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    lat = body.get("lat")
    lon = body.get("lon")
    if lat is None or lon is None:
        raise HTTPException(400, "lat and lon are required")
    if not (-90 <= float(lat) <= 90) or not (-180 <= float(lon) <= 180):
        raise HTTPException(400, "Invalid coordinates")

    speed   = body.get("speed")
    heading = body.get("heading")
    accuracy = body.get("accuracy")

    # verify vehicle exists
    v_row = await db.execute(text("SELECT * FROM vehicles WHERE id = :id AND is_active = TRUE"),
                              {"id": str(vehicle_id)})
    vehicle = v_row.mappings().first()
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")

    # insert position
    pos_row = (await db.execute(text("""
        INSERT INTO vehicle_positions (tenant_id, vehicle_id, lat, lon, speed, heading, accuracy)
        VALUES (:tid, :vid, :lat, :lon, :speed, :heading, :accuracy)
        RETURNING id
    """), {
        "tid": str(token.tenant_id), "vid": str(vehicle_id),
        "lat": float(lat), "lon": float(lon),
        "speed": float(speed) if speed is not None else None,
        "heading": int(heading) if heading is not None else None,
        "accuracy": float(accuracy) if accuracy is not None else None,
    })).first()
    position_id = str(pos_row.id)

    # update vehicle status
    spd = float(speed) if speed is not None else 0.0
    new_status = "moving" if spd >= MOVING_SPEED_KMH else "idle"
    old_lat  = vehicle["last_lat"]
    old_lon  = vehicle["last_lon"]

    await db.execute(text("""
        UPDATE vehicles
        SET last_position_at = now(), last_lat = :lat, last_lon = :lon,
            last_speed = :speed, current_status = :status, updated_at = now()
        WHERE id = :id
    """), {"lat": float(lat), "lon": float(lon), "speed": spd, "status": new_status, "id": str(vehicle_id)})

    # ── journey management ────────────────────────────────────────────────────
    journey_event = None
    if new_status == "moving":
        # check for active journey
        j_row = await db.execute(text("""
            SELECT id, distance_km, max_speed, start_lat, start_lon
            FROM vehicle_journeys
            WHERE vehicle_id = :vid AND status = 'active'
            LIMIT 1
        """), {"vid": str(vehicle_id)})
        journey = j_row.mappings().first()
        if not journey:
            # start new journey
            await db.execute(text("""
                INSERT INTO vehicle_journeys (tenant_id, vehicle_id, start_lat, start_lon)
                VALUES (:tid, :vid, :lat, :lon)
            """), {"tid": str(token.tenant_id), "vid": str(vehicle_id),
                  "lat": float(lat), "lon": float(lon)})
            journey_event = "journey_started"
        else:
            # update distance
            if old_lat is not None and old_lon is not None:
                dist = _haversine_km(old_lat, old_lon, float(lat), float(lon))
                new_dist = float(journey["distance_km"]) + dist
                new_max  = max(float(journey["max_speed"] or 0), spd)
                await db.execute(text("""
                    UPDATE vehicle_journeys
                    SET distance_km = :dist, max_speed = :mspd, end_lat = :lat, end_lon = :lon
                    WHERE id = :id
                """), {"dist": new_dist, "mspd": new_max, "lat": float(lat),
                      "lon": float(lon), "id": str(journey["id"])})
    elif new_status == "idle":
        # complete any active journey
        j_row = await db.execute(text("""
            SELECT id, distance_km, start_at
            FROM vehicle_journeys
            WHERE vehicle_id = :vid AND status = 'active'
            LIMIT 1
        """), {"vid": str(vehicle_id)})
        journey = j_row.mappings().first()
        if journey:
            duration_min = (datetime.now(timezone.utc) - journey["start_at"].replace(tzinfo=timezone.utc)).total_seconds() / 60
            dist_km = float(journey["distance_km"])
            avg_spd = (dist_km / (duration_min / 60)) if duration_min > 0 else 0
            await db.execute(text("""
                UPDATE vehicle_journeys
                SET status = 'completed', end_at = now(), end_lat = :lat, end_lon = :lon,
                    avg_speed = :avg
                WHERE id = :id
            """), {"lat": float(lat), "lon": float(lon), "avg": round(avg_spd, 2), "id": str(journey["id"])})
            journey_event = "journey_completed"

    # ── geofence evaluation ───────────────────────────────────────────────────
    geo_rows = await db.execute(text("""
        SELECT id, name, polygon, alert_on_entry, alert_on_exit, speed_limit
        FROM geofences
        WHERE is_active = TRUE
    """))
    geofences = [dict(r._mapping) for r in geo_rows]

    geofence_alerts = []
    for gf in geofences:
        polygon = gf["polygon"] if isinstance(gf["polygon"], list) else []
        if len(polygon) < 3:
            continue
        inside_now = _point_in_polygon(float(lat), float(lon), polygon)

        # check previous position to detect transition
        if old_lat is not None and old_lon is not None:
            was_inside = _point_in_polygon(old_lat, old_lon, polygon)
        else:
            was_inside = False

        event_type = None
        if inside_now and not was_inside and gf["alert_on_entry"]:
            event_type = "entry"
        elif not inside_now and was_inside and gf["alert_on_exit"]:
            event_type = "exit"
        elif inside_now and gf["speed_limit"] and spd > float(gf["speed_limit"]):
            event_type = "speed_violation"

        if event_type:
            await db.execute(text("""
                INSERT INTO geofence_events (tenant_id, vehicle_id, geofence_id, event_type, lat, lon, speed)
                VALUES (:tid, :vid, :gid, :etype, :lat, :lon, :speed)
            """), {
                "tid": str(token.tenant_id), "vid": str(vehicle_id),
                "gid": str(gf["id"]), "etype": event_type,
                "lat": float(lat), "lon": float(lon), "speed": spd,
            })
            geofence_alerts.append({"geofence": gf["name"], "event": event_type})

            _tid = str(token.tenant_id)
            _sev = "high" if event_type == "speed_violation" else "medium"
            _title = f"GPS {event_type.replace('_', ' ').title()}: {gf['name']}"
            _alert_row = (await db.execute(text("""
                INSERT INTO alerts (tenant_id, camera_id, module_type, severity,
                                    alert_code, message_params, title, message, status)
                SELECT CAST(:tid AS uuid),
                       (SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1),
                       'gps', :sev, :code,
                       CAST(:params AS jsonb), :title, :msg, 'open'
                WHERE (SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1) IS NOT NULL
                RETURNING id
            """), {
                "tid": _tid, "sev": _sev,
                "code": f"gps.{event_type}",
                "params": json.dumps({"vehicle_id": str(vehicle_id), "geofence": gf["name"],
                                      "lat": float(lat), "lon": float(lon), "speed": spd}),
                "title": _title, "msg": _title,
            })).first()

            if _alert_row:
                _redis = getattr(request.app.state, "redis", None)
                if _redis:
                    await _redis.publish(f"tenant_events:{_tid}", json.dumps({
                        "event_type": "alert_created",
                        "tenant_id": _tid,
                        "payload": {
                            "alert_id": str(_alert_row.id),
                            "alert_code": f"gps.{event_type}",
                            "severity": _sev,
                            "module_type": "gps",
                        },
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                    }))

    await db.commit()
    return {
        "id": position_id,
        "status": new_status,
        "journey_event": journey_event,
        "geofence_alerts": geofence_alerts,
    }


@router.get("/vehicles/{vehicle_id}/positions", dependencies=[Depends(require_permission("gps:read"))])
async def get_positions(
    vehicle_id: UUID,
    hours: int = 8,
    limit: int = 500,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    rows = await db.execute(text("""
        SELECT lat, lon, speed, heading, recorded_at
        FROM vehicle_positions
        WHERE vehicle_id = :id
          AND recorded_at >= now() - make_interval(hours => :hours)
        ORDER BY recorded_at DESC
        LIMIT :limit
    """), {"id": str(vehicle_id), "hours": hours, "limit": limit})
    return [dict(r._mapping) for r in rows]


@router.get("/vehicles/{vehicle_id}/journeys", dependencies=[Depends(require_permission("gps:read"))])
async def get_journeys(
    vehicle_id: UUID,
    limit: int = 30,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    rows = await db.execute(text("""
        SELECT * FROM vehicle_journeys
        WHERE vehicle_id = :id
        ORDER BY start_at DESC
        LIMIT :limit
    """), {"id": str(vehicle_id), "limit": limit})
    return [dict(r._mapping) for r in rows]


# ── geofences ─────────────────────────────────────────────────────────────────

@router.get("/geofences", dependencies=[Depends(require_permission("gps:read"))])
async def list_geofences(db: AsyncSession = Depends(get_db_with_tenant)):
    rows = await db.execute(text("""
        SELECT g.*, s.name AS site_name
        FROM geofences g
        LEFT JOIN sites s ON s.id = g.site_id
        WHERE g.is_active = TRUE
        ORDER BY g.name
    """))
    return [dict(r._mapping) for r in rows]


@router.post("/geofences", dependencies=[Depends(require_permission("gps:write"))])
async def create_geofence(body: dict, db: AsyncSession = Depends(get_db_with_tenant),
                          token: TokenPayload = Depends(get_token_payload)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    polygon = body.get("polygon", [])
    if len(polygon) < 3:
        raise HTTPException(400, "polygon must have at least 3 points [{lat, lon}]")

    import json
    row = await db.execute(text("""
        INSERT INTO geofences (tenant_id, site_id, name, description, polygon,
                               alert_on_entry, alert_on_exit, speed_limit)
        VALUES (:tid, :site_id, :name, :desc, CAST(:poly AS jsonb), :entry, :exit, :speed)
        RETURNING *
    """), {
        "tid":     str(token.tenant_id),
        "site_id": body.get("site_id"),
        "name":    name,
        "desc":    body.get("description"),
        "poly":    json.dumps(polygon),
        "entry":   body.get("alert_on_entry", True),
        "exit":    body.get("alert_on_exit", False),
        "speed":   body.get("speed_limit"),
    })
    await db.commit()
    return dict(row.mappings().one())


@router.put("/geofences/{geofence_id}", dependencies=[Depends(require_permission("gps:write"))])
async def update_geofence(geofence_id: UUID, body: dict,
                          db: AsyncSession = Depends(get_db_with_tenant)):
    import json
    allowed = {"name", "description", "polygon", "alert_on_entry",
               "alert_on_exit", "speed_limit", "site_id", "is_active"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if "polygon" in updates:
        updates["polygon"] = json.dumps(updates["polygon"]) + "::jsonb"
    if not updates:
        raise HTTPException(400, "No valid fields to update")

    set_parts = []
    params: dict = {"id": str(geofence_id)}
    for k, v in updates.items():
        if k == "polygon":
            set_parts.append(f"{k} = CAST(:poly AS jsonb)")
            params["poly"] = json.dumps(body["polygon"])
        else:
            set_parts.append(f"{k} = :{k}")
            params[k] = v

    await db.execute(text(f"UPDATE geofences SET {', '.join(set_parts)}, updated_at = now() WHERE id = :id"), params)
    await db.commit()
    return {"ok": True}


@router.delete("/geofences/{geofence_id}", dependencies=[Depends(require_permission("gps:write"))])
async def deactivate_geofence(geofence_id: UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text("UPDATE geofences SET is_active = FALSE, updated_at = now() WHERE id = :id"),
                     {"id": str(geofence_id)})
    await db.commit()
    return {"ok": True}


@router.get("/geofence-events", dependencies=[Depends(require_permission("gps:read"))])
async def list_geofence_events(
    vehicle_id: Optional[str] = None,
    geofence_id: Optional[str] = None,
    hours: int = 24,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    filters = "WHERE ge.occurred_at >= now() - make_interval(hours => :hours)"
    params: dict = {"hours": hours}
    if vehicle_id:
        filters += " AND ge.vehicle_id = :vid"
        params["vid"] = vehicle_id
    if geofence_id:
        filters += " AND ge.geofence_id = :gid"
        params["gid"] = geofence_id

    rows = await db.execute(text(f"""
        SELECT ge.*, v.name AS vehicle_name, v.plate_number,
               g.name AS geofence_name
        FROM geofence_events ge
        JOIN vehicles  v ON v.id = ge.vehicle_id
        JOIN geofences g ON g.id = ge.geofence_id
        {filters}
        ORDER BY ge.occurred_at DESC
        LIMIT 100
    """), params)
    return [dict(r._mapping) for r in rows]
