"""Smart Parking / Carpark Management router"""
from decimal import Decimal
from typing import Optional
from uuid import UUID

import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(tags=["parking"])

VALID_BAY_STATUS   = {"available", "occupied", "reserved", "blocked"}
VALID_ZONE_TYPES   = {"regular", "handicap", "ev", "vip", "motorcycle", "loading", "standard"}
VALID_SESSION_STATUS = {"active", "completed", "overstay", "disputed"}
VALID_PAYMENT_STATUS = {"unpaid", "paid", "waived", "void"}


# ── dashboard ────────────────────────────────────────────────────────────────

@router.get("/api/v1/parking/dashboard", dependencies=[Depends(require_permission("parking:read"))])
async def parking_dashboard(db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM car_parks WHERE is_active = TRUE)                                         AS total_carparks,
            (SELECT COALESCE(SUM(total_capacity), 0) FROM car_parks WHERE is_active = TRUE)                 AS total_capacity,
            (SELECT COUNT(*) FROM parking_bays WHERE status = 'available')                                  AS available_bays,
            (SELECT COUNT(*) FROM parking_bays WHERE status = 'occupied')                                   AS occupied_bays,
            (SELECT COUNT(*) FROM parking_bays WHERE status = 'reserved')                                   AS reserved_bays,
            (SELECT COUNT(*) FROM parking_sessions WHERE status = 'active')                                 AS active_sessions,
            (SELECT COALESCE(SUM(fee_amount), 0) FROM parking_sessions
             WHERE payment_status = 'paid' AND exit_at >= now()::date)                                      AS revenue_today,
            (SELECT COUNT(*) FROM parking_sessions WHERE status = 'overstay')                               AS overstay_count
    """))).mappings().one()
    return dict(row)


# ── carparks ─────────────────────────────────────────────────────────────────

@router.get("/api/v1/carparks", dependencies=[Depends(require_permission("parking:read"))])
async def list_carparks(db: AsyncSession = Depends(get_db_with_tenant)):
    rows = await db.execute(text("""
        SELECT cp.*,
               s.name AS site_name,
               (SELECT COUNT(*) FROM parking_bays pb WHERE pb.car_park_id = cp.id AND pb.status = 'available') AS available_bays,
               (SELECT COUNT(*) FROM parking_bays pb WHERE pb.car_park_id = cp.id AND pb.status = 'occupied')  AS occupied_bays,
               (SELECT COUNT(*) FROM parking_bays pb WHERE pb.car_park_id = cp.id)                             AS bay_count
        FROM car_parks cp
        LEFT JOIN sites s ON s.id = cp.site_id
        WHERE cp.is_active = TRUE
        ORDER BY cp.name
    """))
    return [dict(r._mapping) for r in rows]


@router.post("/api/v1/carparks", dependencies=[Depends(require_permission("parking:manage"))])
async def create_carpark(
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    row = await db.execute(text("""
        INSERT INTO car_parks (tenant_id, site_id, name, description, total_capacity, levels, address)
        VALUES (:tid, :sid, :name, :desc, :cap, :levels, :addr)
        RETURNING *
    """), {
        "tid":    token.tenant_id,
        "sid":    body.get("site_id"),
        "name":   name,
        "desc":   body.get("description"),
        "cap":    body.get("total_capacity", 0),
        "levels": body.get("levels", 1),
        "addr":   body.get("address"),
    })
    await db.commit()
    return dict(row.mappings().one())


@router.get("/api/v1/carparks/{carpark_id}", dependencies=[Depends(require_permission("parking:read"))])
async def get_carpark(carpark_id: UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    cp = (await db.execute(text("""
        SELECT cp.*, s.name AS site_name FROM car_parks cp
        LEFT JOIN sites s ON s.id = cp.site_id
        WHERE cp.id = :id AND cp.is_active = TRUE
    """), {"id": str(carpark_id)})).mappings().first()
    if not cp:
        raise HTTPException(404, "Carpark not found")

    zones = (await db.execute(text("""
        SELECT z.*,
               COUNT(b.id)                                             AS bay_count,
               COUNT(b.id) FILTER (WHERE b.status = 'available')      AS available,
               COUNT(b.id) FILTER (WHERE b.status = 'occupied')       AS occupied
        FROM parking_zones z
        LEFT JOIN parking_bays b ON b.zone_id = z.id
        WHERE z.car_park_id = :cid AND z.is_active = TRUE
        GROUP BY z.id ORDER BY z.level, z.name
    """), {"cid": str(carpark_id)})).mappings().all()

    return {**dict(cp), "zones": [dict(z) for z in zones]}


@router.put("/api/v1/carparks/{carpark_id}", dependencies=[Depends(require_permission("parking:manage"))])
async def update_carpark(carpark_id: UUID, body: dict, db: AsyncSession = Depends(get_db_with_tenant)):
    allowed = {"name", "description", "total_capacity", "levels", "address", "is_active", "site_id"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = str(carpark_id)
    await db.execute(text(f"UPDATE car_parks SET {set_clause}, updated_at = now() WHERE id = :id"), updates)
    await db.commit()
    return {"ok": True}


# ── zones ────────────────────────────────────────────────────────────────────

@router.post("/api/v1/carparks/{carpark_id}/zones", dependencies=[Depends(require_permission("parking:manage"))])
async def create_zone(
    carpark_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    zone_type = body.get("zone_type", "regular")
    if zone_type not in VALID_ZONE_TYPES:
        raise HTTPException(400, f"zone_type must be one of {sorted(VALID_ZONE_TYPES)}")

    row = await db.execute(text("""
        INSERT INTO parking_zones (tenant_id, car_park_id, name, zone_type, level, capacity)
        VALUES (:tid, :cid, :name, :ztype, :level, :cap)
        RETURNING *
    """), {
        "tid":   token.tenant_id,
        "cid":   str(carpark_id),
        "name":  name,
        "ztype": zone_type,
        "level": body.get("level", 1),
        "cap":   body.get("capacity", 0),
    })
    await db.commit()
    return dict(row.mappings().one())


# ── bays ─────────────────────────────────────────────────────────────────────

@router.get("/api/v1/carparks/{carpark_id}/bays", dependencies=[Depends(require_permission("parking:read"))])
async def list_bays(
    carpark_id: UUID,
    zone_id: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    filters = "WHERE b.car_park_id = :cid"
    params: dict = {"cid": str(carpark_id)}
    if zone_id:
        filters += " AND b.zone_id = :zid"
        params["zid"] = zone_id
    if status:
        filters += " AND b.status = :status"
        params["status"] = status

    rows = await db.execute(text(f"""
        SELECT b.*, z.name AS zone_name, z.zone_type
        FROM parking_bays b
        JOIN parking_zones z ON z.id = b.zone_id
        {filters}
        ORDER BY z.name, b.bay_number
    """), params)
    return [dict(r._mapping) for r in rows]


@router.post("/api/v1/carparks/{carpark_id}/bays", dependencies=[Depends(require_permission("parking:manage"))])
async def create_bay(
    carpark_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if not body.get("zone_id"):
        raise HTTPException(400, "zone_id is required")
    if not body.get("bay_number"):
        raise HTTPException(400, "bay_number is required")

    row = await db.execute(text("""
        INSERT INTO parking_bays (tenant_id, zone_id, car_park_id, bay_number, notes)
        VALUES (:tid, :zid, :cid, :bnum, :notes)
        RETURNING *
    """), {
        "tid":   token.tenant_id,
        "zid":   body["zone_id"],
        "cid":   str(carpark_id),
        "bnum":  body["bay_number"],
        "notes": body.get("notes"),
    })
    await db.commit()
    return dict(row.mappings().one())


@router.put("/api/v1/parking/bays/{bay_id}", dependencies=[Depends(require_permission("parking:write"))])
async def update_bay_status(bay_id: UUID, body: dict, db: AsyncSession = Depends(get_db_with_tenant)):
    status = body.get("status")
    if status and status not in VALID_BAY_STATUS:
        raise HTTPException(400, f"status must be one of {sorted(VALID_BAY_STATUS)}")
    await db.execute(text("""
        UPDATE parking_bays
        SET status = COALESCE(:status, status),
            notes  = COALESCE(:notes, notes),
            updated_at = now()
        WHERE id = :id
    """), {"status": status, "notes": body.get("notes"), "id": str(bay_id)})
    await db.commit()
    return {"ok": True}


# ── rates ─────────────────────────────────────────────────────────────────────

@router.get("/api/v1/carparks/{carpark_id}/rates", dependencies=[Depends(require_permission("parking:read"))])
async def list_rates(carpark_id: UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    rows = await db.execute(text("""
        SELECT * FROM parking_rates WHERE car_park_id = :cid AND is_active = TRUE ORDER BY zone_type
    """), {"cid": str(carpark_id)})
    return [dict(r._mapping) for r in rows]


@router.post("/api/v1/carparks/{carpark_id}/rates", dependencies=[Depends(require_permission("parking:manage"))])
async def create_rate(
    carpark_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    rate_name = body.get("rate_name") or body.get("name")
    if not rate_name:
        raise HTTPException(400, "rate_name is required")
    row = await db.execute(text("""
        INSERT INTO parking_rates (tenant_id, car_park_id, zone_type, rate_name,
                                   first_hour_rate, subsequent_rate, daily_max_rate, currency)
        VALUES (:tid, :cid, :ztype, :rname, :frate, :srate, :dmax, :cur)
        RETURNING *
    """), {
        "tid":   token.tenant_id,
        "cid":   str(carpark_id),
        "ztype": body.get("zone_type", "regular"),
        "rname": rate_name,
        "frate": Decimal(str(body.get("first_hour_rate", 0))),
        "srate": Decimal(str(body.get("subsequent_rate", 0))),
        "dmax":  Decimal(str(body["daily_max_rate"])) if body.get("daily_max_rate") is not None else None,
        "cur":   body.get("currency", "SGD"),
    })
    await db.commit()
    return dict(row.mappings().one())


# ── sessions ──────────────────────────────────────────────────────────────────

@router.get("/api/v1/parking/sessions", dependencies=[Depends(require_permission("parking:read"))])
async def list_sessions(
    status: Optional[str] = None,
    carpark_id: Optional[str] = None,
    plate: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    filters = "WHERE 1=1"
    params: dict = {"limit": limit + 1, "offset": offset}
    if status:
        filters += " AND ps.status = :status"
        params["status"] = status
    if carpark_id:
        filters += " AND ps.car_park_id = :cid"
        params["cid"] = carpark_id
    if plate:
        filters += " AND ps.vehicle_plate ILIKE :plate"
        params["plate"] = f"%{plate}%"

    rows_raw = await db.execute(text(f"""
        SELECT ps.*, cp.name AS carpark_name, pz.name AS zone_name, pb.bay_number
        FROM parking_sessions ps
        LEFT JOIN car_parks cp ON cp.id = ps.car_park_id
        LEFT JOIN parking_zones pz ON pz.id = ps.zone_id
        LEFT JOIN parking_bays pb ON pb.id = ps.bay_id
        {filters}
        ORDER BY ps.entry_at DESC
        LIMIT :limit OFFSET :offset
    """), params)
    rows = [dict(r._mapping) for r in rows_raw]
    return rows[:limit]


@router.post("/api/v1/parking/sessions/entry", dependencies=[Depends(require_permission("parking:write"))])
async def log_entry(
    request: Request,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    car_park_id = body.get("car_park_id") or body.get("carpark_id")
    if not car_park_id:
        raise HTTPException(400, "car_park_id is required")

    bay_id = body.get("bay_id")
    if bay_id:
        await db.execute(text("""
            UPDATE parking_bays
            SET status = 'occupied', vehicle_plate = :plate, occupied_since = now(), updated_at = now()
            WHERE id = :id
        """), {"plate": body.get("vehicle_plate"), "id": bay_id})

    row = await db.execute(text("""
        INSERT INTO parking_sessions (tenant_id, car_park_id, zone_id, bay_id,
                                      vehicle_plate, vehicle_type, entry_at,
                                      entry_camera_id, operator_notes)
        VALUES (:tid, :cid, :zid, :bid, :plate, :vtype, now(), :cam, :notes)
        RETURNING *
    """), {
        "tid":   token.tenant_id,
        "cid":   car_park_id,
        "zid":   body.get("zone_id"),
        "bid":   bay_id,
        "plate": body.get("vehicle_plate"),
        "vtype": body.get("vehicle_type", "car"),
        "cam":   body.get("entry_camera_id"),
        "notes": body.get("operator_notes"),
    })
    await db.commit()
    result_row = dict(row.mappings().one())
    redis = getattr(request.app.state, "redis", None)
    if redis:
        from datetime import datetime, timezone
        await redis.publish(f"tenant_events:{token.tenant_id}", json.dumps({
            "event_type": "parking_entry",
            "tenant_id": str(token.tenant_id),
            "payload": {
                "session_id": str(result_row.get("id")),
                "car_park_id": str(car_park_id),
                "vehicle_plate": body.get("vehicle_plate"),
            },
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }))
    return result_row


@router.put("/api/v1/parking/sessions/{session_id}/exit", dependencies=[Depends(require_permission("parking:write"))])
async def log_exit(
    request: Request,
    session_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    sess = (await db.execute(text("""
        SELECT ps.*, pr.first_hour_rate, pr.subsequent_rate, pr.daily_max_rate, pr.currency
        FROM parking_sessions ps
        LEFT JOIN parking_rates pr ON pr.car_park_id = ps.car_park_id
            AND pr.is_active = TRUE
            AND pr.zone_type = (SELECT zone_type FROM parking_zones WHERE id = ps.zone_id LIMIT 1)
        WHERE ps.id = :id AND ps.status = 'active'
        LIMIT 1
    """), {"id": str(session_id)})).mappings().first()

    if not sess:
        raise HTTPException(404, "Active session not found")

    # Calculate fee
    from datetime import datetime, timezone
    entry_at = sess["entry_at"]
    if entry_at.tzinfo is None:
        entry_at = entry_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    duration_min = int((now - entry_at).total_seconds() / 60)

    fee = Decimal("0")
    if sess["first_hour_rate"] is not None:
        first_rate = Decimal(str(sess["first_hour_rate"]))
        sub_rate = Decimal(str(sess["subsequent_rate"] or 0))
        daily_max = Decimal(str(sess["daily_max_rate"])) if sess["daily_max_rate"] else None

        hours = Decimal(str(duration_min)) / Decimal("60")
        if hours <= 1:
            fee = first_rate
        else:
            fee = first_rate + (hours - 1).to_integral_value() * sub_rate

        if daily_max and fee > daily_max:
            fee = daily_max

    if sess["bay_id"]:
        await db.execute(text("""
            UPDATE parking_bays
            SET status = 'available', vehicle_plate = NULL, occupied_since = NULL, updated_at = now()
            WHERE id = :id
        """), {"id": str(sess["bay_id"])})

    await db.execute(text("""
        UPDATE parking_sessions
        SET exit_at = now(), duration_minutes = :dur, fee_amount = :fee,
            payment_status = :pstatus, payment_method = :pmethod,
            exit_camera_id = :cam, status = 'completed', updated_at = now()
        WHERE id = :id
    """), {
        "dur":     duration_min,
        "fee":     float(fee),
        "pstatus": body.get("payment_status", "unpaid"),
        "pmethod": body.get("payment_method"),
        "cam":     body.get("exit_camera_id"),
        "id":      str(session_id),
    })
    await db.commit()
    redis = getattr(request.app.state, "redis", None)
    if redis:
        from datetime import datetime, timezone
        await redis.publish(f"tenant_events:{token.tenant_id}", json.dumps({
            "event_type": "parking_exit",
            "tenant_id": str(token.tenant_id),
            "payload": {
                "session_id": str(session_id),
                "duration_minutes": duration_min,
                "fee_amount": float(fee),
            },
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }))
    return {"duration_minutes": duration_min, "fee_amount": float(fee), "ok": True}


@router.put("/api/v1/parking/sessions/{session_id}/payment", dependencies=[Depends(require_permission("parking:write"))])
async def update_payment(session_id: UUID, body: dict, db: AsyncSession = Depends(get_db_with_tenant)):
    ps = body.get("payment_status", "paid")
    if ps not in VALID_PAYMENT_STATUS:
        raise HTTPException(400, f"payment_status must be one of {sorted(VALID_PAYMENT_STATUS)}")
    await db.execute(text("""
        UPDATE parking_sessions
        SET payment_status = :ps, payment_method = COALESCE(:pm, payment_method), updated_at = now()
        WHERE id = :id
    """), {"ps": ps, "pm": body.get("payment_method"), "id": str(session_id)})
    await db.commit()
    return {"ok": True}


@router.get("/api/v1/carparks/{carpark_id}/occupancy", dependencies=[Depends(require_permission("parking:read"))])
async def get_occupancy(carpark_id: UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    zones = (await db.execute(text("""
        SELECT z.id, z.name, z.zone_type, z.level, z.capacity,
               COUNT(b.id)                                                AS total_bays,
               COUNT(b.id) FILTER (WHERE b.status = 'available')          AS available,
               COUNT(b.id) FILTER (WHERE b.status = 'occupied')           AS occupied,
               COUNT(b.id) FILTER (WHERE b.status = 'reserved')           AS reserved,
               COUNT(b.id) FILTER (WHERE b.status = 'blocked')            AS blocked
        FROM parking_zones z
        LEFT JOIN parking_bays b ON b.zone_id = z.id
        WHERE z.car_park_id = :cid AND z.is_active = TRUE
        GROUP BY z.id ORDER BY z.level, z.name
    """), {"cid": str(carpark_id)})).mappings().all()

    bays = (await db.execute(text("""
        SELECT b.id, b.bay_number, b.status, b.vehicle_plate, b.occupied_since, b.notes,
               z.name AS zone_name, z.zone_type
        FROM parking_bays b
        JOIN parking_zones z ON z.id = b.zone_id
        WHERE b.car_park_id = :cid
        ORDER BY z.name, b.bay_number
    """), {"cid": str(carpark_id)})).mappings().all()

    zone_list = [dict(z) for z in zones]
    total_bays = sum(z.get("total_bays", 0) or 0 for z in zone_list)
    total_occupied = sum(z.get("occupied", 0) or 0 for z in zone_list)
    occupancy_pct = round((total_occupied / total_bays * 100), 1) if total_bays > 0 else 0.0
    return {
        "zones": zone_list,
        "bays": [dict(b) for b in bays],
        "total_bays": total_bays,
        "occupancy_pct": occupancy_pct,
    }


# ── LPR camera configuration (P3-D) ──────────────────────────────────────────

VALID_TRIGGER_TYPES = {"entry", "exit", "both"}


@router.get("/api/v1/parking/lpr-cameras", dependencies=[Depends(require_permission("parking:manage"))])
async def list_lpr_cameras(db: AsyncSession = Depends(get_db_with_tenant)):
    """List cameras configured to auto-trigger parking sessions on LPR detection."""
    rows = await db.execute(text("""
        SELECT plc.id, plc.camera_id, plc.car_park_id, plc.trigger_type,
               plc.default_zone_id, plc.is_active, plc.notes, plc.created_at,
               c.name  AS camera_name,
               cp.name AS car_park_name,
               pz.name AS default_zone_name
        FROM parking_lpr_cameras plc
        JOIN cameras c  ON c.id  = plc.camera_id
        JOIN car_parks cp ON cp.id = plc.car_park_id
        LEFT JOIN parking_zones pz ON pz.id = plc.default_zone_id
        ORDER BY cp.name, c.name
    """))
    return [dict(r._mapping) for r in rows]


@router.post("/api/v1/parking/lpr-cameras", dependencies=[Depends(require_permission("parking:manage"))])
async def create_lpr_camera(
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Configure a camera to auto-trigger parking entry/exit on LPR plate detection."""
    camera_id   = body.get("camera_id")
    car_park_id = body.get("car_park_id")
    trigger     = body.get("trigger_type", "both")

    if not camera_id or not car_park_id:
        raise HTTPException(400, "camera_id and car_park_id are required")
    if trigger not in VALID_TRIGGER_TYPES:
        raise HTTPException(400, f"trigger_type must be one of {sorted(VALID_TRIGGER_TYPES)}")

    try:
        row = await db.execute(text("""
            INSERT INTO parking_lpr_cameras
                (tenant_id, camera_id, car_park_id, trigger_type, default_zone_id, notes)
            VALUES
                (CAST(:tid AS uuid), CAST(:cam AS uuid), CAST(:cpid AS uuid),
                 :trigger, CAST(:zid AS uuid), :notes)
            ON CONFLICT (tenant_id, camera_id)
            DO UPDATE SET
                car_park_id     = EXCLUDED.car_park_id,
                trigger_type    = EXCLUDED.trigger_type,
                default_zone_id = EXCLUDED.default_zone_id,
                notes           = EXCLUDED.notes,
                is_active       = TRUE
            RETURNING *
        """), {
            "tid":     token.tenant_id,
            "cam":     camera_id,
            "cpid":    car_park_id,
            "trigger": trigger,
            "zid":     body.get("default_zone_id"),
            "notes":   body.get("notes"),
        })
        await db.commit()
        return dict(row.mappings().one())
    except Exception as exc:
        raise HTTPException(400, str(exc))


@router.delete("/api/v1/parking/lpr-cameras/{config_id}", dependencies=[Depends(require_permission("parking:manage"))])
async def delete_lpr_camera(config_id: UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    """Remove a camera's parking LPR trigger configuration (soft-disable)."""
    result = await db.execute(text("""
        UPDATE parking_lpr_cameras SET is_active = FALSE WHERE id = :id RETURNING id
    """), {"id": str(config_id)})
    if not result.rowcount:
        raise HTTPException(404, "LPR camera config not found")
    await db.commit()
    return {"ok": True}


@router.get("/api/v1/parking/sessions/lpr-triggered", dependencies=[Depends(require_permission("parking:read"))])
async def list_lpr_triggered_sessions(
    db: AsyncSession = Depends(get_db_with_tenant),
    limit: int = 50,
    offset: int = 0,
):
    """Recent parking sessions that were auto-created or auto-closed by LPR."""
    rows = await db.execute(text("""
        SELECT ps.id, ps.vehicle_plate, ps.entry_at, ps.exit_at, ps.status,
               ps.duration_minutes, ps.fee_amount, ps.payment_status,
               ps.lpr_detection_id,
               cp.name AS car_park_name,
               pz.name AS zone_name
        FROM parking_sessions ps
        JOIN car_parks cp ON cp.id = ps.car_park_id
        LEFT JOIN parking_zones pz ON pz.id = ps.zone_id
        WHERE ps.lpr_triggered = TRUE
        ORDER BY ps.entry_at DESC
        LIMIT :limit OFFSET :offset
    """), {"limit": min(limit, 200), "offset": max(offset, 0)})
    return [dict(r._mapping) for r in rows]
