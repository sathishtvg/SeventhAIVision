"""IoT Smart Facilities Monitoring — sensors, readings, threshold alerts."""

import json as _json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/iot", tags=["iot"])

VALID_SENSOR_TYPES = {
    "water_tank", "pump", "electricity", "generator",
    "temperature", "humidity", "air_quality", "water_quality", "custom",
}

SENSOR_TYPE_UNITS = {
    "water_tank":    "%",
    "pump":          "L/min",
    "electricity":   "kW",
    "generator":     "%",
    "temperature":   "°C",
    "humidity":      "%",
    "air_quality":   "AQI",
    "water_quality": "pH",
    "custom":        "",
}


# ── Schemas ───────────────────────────────────────────────────────────────────

class SensorCreate(BaseModel):
    name: str
    sensor_type: str
    unit: str | None = None
    location: str | None = None
    description: str | None = None
    site_id: str | None = None
    threshold_warning_low: float | None = None
    threshold_warning_high: float | None = None
    threshold_critical_low: float | None = None
    threshold_critical_high: float | None = None
    expected_interval_seconds: int = 300


class SensorUpdate(BaseModel):
    name: str | None = None
    unit: str | None = None
    location: str | None = None
    description: str | None = None
    site_id: str | None = None
    is_active: bool | None = None
    threshold_warning_low: float | None = None
    threshold_warning_high: float | None = None
    threshold_critical_low: float | None = None
    threshold_critical_high: float | None = None
    expected_interval_seconds: int | None = None


class ReadingIngest(BaseModel):
    value: float
    recorded_at: str | None = None
    raw_data: dict | None = None


# ── Sensors CRUD ──────────────────────────────────────────────────────────────

@router.get("/sensors", dependencies=[Depends(require_permission("iot:read"))])
async def list_sensors(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    sensor_type: str | None = None,
    is_active: bool = True,
):
    where = ["s.is_active = :is_active"]
    params: dict = {"is_active": is_active}
    if site_id:
        where.append("s.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    if sensor_type:
        where.append("s.sensor_type = :sensor_type")
        params["sensor_type"] = sensor_type

    result = await db.execute(text(f"""
        SELECT s.*,
               si.name AS site_name,
               (SELECT COUNT(*) FROM iot_alerts ia WHERE ia.sensor_id = s.id AND ia.status = 'open') AS open_alerts
        FROM iot_sensors s
        LEFT JOIN sites si ON si.id = s.site_id
        WHERE {' AND '.join(where)}
        ORDER BY s.sensor_type, s.name
    """), params)
    rows = []
    for r in result:
        d = dict(r._mapping)
        d["id"] = str(d["id"])
        d["tenant_id"] = str(d["tenant_id"])
        if d.get("site_id"): d["site_id"] = str(d["site_id"])
        if d.get("last_reading_at"): d["last_reading_at"] = d["last_reading_at"].isoformat()
        if d.get("created_at"): d["created_at"] = d["created_at"].isoformat()
        if d.get("updated_at"): d["updated_at"] = d["updated_at"].isoformat()
        rows.append(d)
    return rows


@router.post("/sensors", dependencies=[Depends(require_permission("iot:write"))])
async def create_sensor(body: SensorCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    if body.sensor_type not in VALID_SENSOR_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"sensor_type must be one of: {sorted(VALID_SENSOR_TYPES)}")
    unit = body.unit or SENSOR_TYPE_UNITS.get(body.sensor_type, "")
    result = await db.execute(text("""
        INSERT INTO iot_sensors (
            tenant_id, site_id, name, sensor_type, unit, location, description,
            threshold_warning_low, threshold_warning_high,
            threshold_critical_low, threshold_critical_high,
            expected_interval_seconds
        ) VALUES (
            current_setting('app.current_tenant')::uuid,
            CAST(:site_id AS uuid), :name, :sensor_type, :unit, :location, :description,
            :twl, :twh, :tcl, :tch, :interval
        )
        RETURNING id, name, sensor_type, unit, location, current_status, created_at
    """), {
        "site_id": body.site_id, "name": body.name, "sensor_type": body.sensor_type,
        "unit": unit, "location": body.location, "description": body.description,
        "twl": body.threshold_warning_low, "twh": body.threshold_warning_high,
        "tcl": body.threshold_critical_low, "tch": body.threshold_critical_high,
        "interval": body.expected_interval_seconds,
    })
    row = result.first()
    await db.commit()
    d = dict(row._mapping)
    d["id"] = str(d["id"])
    if d.get("created_at"): d["created_at"] = d["created_at"].isoformat()
    return d


@router.get("/sensors/{sensor_id}", dependencies=[Depends(require_permission("iot:read"))])
async def get_sensor(sensor_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(text("""
        SELECT s.*, si.name AS site_name
        FROM iot_sensors s
        LEFT JOIN sites si ON si.id = s.site_id
        WHERE s.id = CAST(:id AS uuid)
    """), {"id": sensor_id})).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sensor not found")

    # Last 50 readings
    readings = (await db.execute(text("""
        SELECT id, value, recorded_at
        FROM iot_readings
        WHERE sensor_id = :sid
        ORDER BY recorded_at DESC
        LIMIT 50
    """), {"sid": sensor_id})).fetchall()

    d = dict(row._mapping)
    d["id"] = str(d["id"])
    d["tenant_id"] = str(d["tenant_id"])
    if d.get("site_id"): d["site_id"] = str(d["site_id"])
    if d.get("last_reading_at"): d["last_reading_at"] = d["last_reading_at"].isoformat()
    if d.get("created_at"): d["created_at"] = d["created_at"].isoformat()
    if d.get("updated_at"): d["updated_at"] = d["updated_at"].isoformat()
    d["readings"] = [
        {"id": str(r.id), "value": float(r.value),
         "recorded_at": r.recorded_at.isoformat()}
        for r in readings
    ]
    return d


@router.put("/sensors/{sensor_id}", dependencies=[Depends(require_permission("iot:write"))])
async def update_sensor(sensor_id: str, body: SensorUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    fields = []
    params: dict = {"id": sensor_id}
    for field, col in [
        ("name", "name"), ("unit", "unit"), ("location", "location"),
        ("description", "description"), ("is_active", "is_active"),
        ("threshold_warning_low", "threshold_warning_low"),
        ("threshold_warning_high", "threshold_warning_high"),
        ("threshold_critical_low", "threshold_critical_low"),
        ("threshold_critical_high", "threshold_critical_high"),
        ("expected_interval_seconds", "expected_interval_seconds"),
    ]:
        val = getattr(body, field)
        if val is not None:
            fields.append(f"{col} = :{field}")
            params[field] = val
    if body.site_id is not None:
        fields.append("site_id = CAST(:site_id AS uuid)")
        params["site_id"] = body.site_id

    if not fields:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    fields.append("updated_at = now()")
    await db.execute(text(f"UPDATE iot_sensors SET {', '.join(fields)} WHERE id = CAST(:id AS uuid)"), params)
    await db.commit()
    return {"ok": True}


@router.delete("/sensors/{sensor_id}", dependencies=[Depends(require_permission("iot:write"))])
async def deactivate_sensor(sensor_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(
        text("UPDATE iot_sensors SET is_active = FALSE, updated_at = now() WHERE id = CAST(:id AS uuid)"),
        {"id": sensor_id},
    )
    await db.commit()
    return {"ok": True}


# ── Readings ─────────────────────────────────────────────────────────────────

@router.post("/sensors/{sensor_id}/readings", dependencies=[Depends(require_permission("iot:ingest"))])
async def ingest_reading(
    sensor_id: str,
    body: ReadingIngest,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    sensor_row = (await db.execute(text("""
        SELECT id, tenant_id, sensor_type, unit,
               threshold_warning_low, threshold_warning_high,
               threshold_critical_low, threshold_critical_high,
               is_active
        FROM iot_sensors WHERE id = CAST(:id AS uuid)
    """), {"id": sensor_id})).first()

    if sensor_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sensor not found")
    if not sensor_row.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sensor is deactivated")

    if body.recorded_at:
        recorded_at = datetime.fromisoformat(body.recorded_at)
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    else:
        recorded_at = datetime.now(timezone.utc)

    # Insert reading
    reading_row = (await db.execute(text("""
        INSERT INTO iot_readings (tenant_id, sensor_id, value, raw_data, recorded_at)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:sid AS uuid),
                :value, CAST(:raw AS jsonb), :recorded_at)
        RETURNING id
    """), {
        "sid": sensor_id, "value": body.value,
        "raw": _json.dumps(body.raw_data) if body.raw_data else "{}",
        "recorded_at": recorded_at,
    })).first()

    # Determine new status and whether to raise an alert
    v = body.value
    tcl = sensor_row.threshold_critical_low
    tch = sensor_row.threshold_critical_high
    twl = sensor_row.threshold_warning_low
    twh = sensor_row.threshold_warning_high

    new_status = "normal"
    alert_severity = None
    alert_msg = None

    if (tcl is not None and v < float(tcl)) or (tch is not None and v > float(tch)):
        new_status = "critical"
        alert_severity = "critical"
        if tcl is not None and v < float(tcl):
            alert_msg = f"Value {v} {sensor_row.unit or ''} is below critical threshold {tcl}"
        else:
            alert_msg = f"Value {v} {sensor_row.unit or ''} exceeds critical threshold {tch}"
    elif (twl is not None and v < float(twl)) or (twh is not None and v > float(twh)):
        new_status = "warning"
        alert_severity = "medium"
        if twl is not None and v < float(twl):
            alert_msg = f"Value {v} {sensor_row.unit or ''} is below warning threshold {twl}"
        else:
            alert_msg = f"Value {v} {sensor_row.unit or ''} exceeds warning threshold {twh}"

    # Update sensor's last reading and status
    await db.execute(text("""
        UPDATE iot_sensors
        SET last_reading_at = :ts, last_reading_value = :value,
            current_status  = :status, updated_at = now()
        WHERE id = CAST(:id AS uuid)
    """), {"ts": recorded_at, "value": body.value, "status": new_status, "id": sensor_id})

    # Create alert if threshold breached (deduplicate: skip if there's an open alert of same severity)
    if alert_severity:
        existing = (await db.execute(text("""
            SELECT id FROM iot_alerts
            WHERE sensor_id = CAST(:sid AS uuid) AND status = 'open' AND severity = :sev
            LIMIT 1
        """), {"sid": sensor_id, "sev": alert_severity})).first()

        if existing is None:
            await db.execute(text("""
                INSERT INTO iot_alerts
                    (tenant_id, sensor_id, alert_type, severity, value, message)
                VALUES (current_setting('app.current_tenant')::uuid,
                        CAST(:sid AS uuid), 'threshold_breach', :sev, :val, :msg)
            """), {"sid": sensor_id, "sev": alert_severity, "val": body.value, "msg": alert_msg})

            _tid = str(sensor_row.tenant_id)
            _title = f"IoT threshold breach: {sensor_row.sensor_type or 'sensor'} — {(alert_msg or '')[:100]}"
            _alert_row = (await db.execute(text("""
                INSERT INTO alerts (tenant_id, camera_id, module_type, severity,
                                    alert_code, message_params, title, message, status)
                SELECT CAST(:tid AS uuid),
                       (SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1),
                       'iot', :sev, 'iot.threshold_breach',
                       CAST(:params AS jsonb), :title, :msg, 'open'
                WHERE (SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1) IS NOT NULL
                RETURNING id
            """), {
                "tid": _tid, "sev": alert_severity,
                "params": _json.dumps({"sensor_id": sensor_id, "value": body.value}),
                "title": _title, "msg": alert_msg,
            })).first()

            if _alert_row:
                _redis = getattr(request.app.state, "redis", None)
                if _redis:
                    await _redis.publish(f"tenant_events:{_tid}", _json.dumps({
                        "event_type": "alert_created",
                        "tenant_id": _tid,
                        "payload": {
                            "alert_id": str(_alert_row.id),
                            "alert_code": "iot.threshold_breach",
                            "severity": alert_severity,
                            "module_type": "iot",
                        },
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                    }))

    await db.commit()
    return {
        "id": str(reading_row.id),
        "status": new_status,
        "alert_created": alert_severity is not None,
        "alert_severity": alert_severity,
    }


@router.get("/sensors/{sensor_id}/readings", dependencies=[Depends(require_permission("iot:read"))])
async def get_readings(
    sensor_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    limit: int = 200,
    hours: int = 24,
):
    result = await db.execute(text("""
        SELECT id, value, recorded_at
        FROM iot_readings
        WHERE sensor_id = :sid
          AND recorded_at > now() - (:hours * INTERVAL '1 hour')
        ORDER BY recorded_at DESC
        LIMIT :limit
    """), {"sid": sensor_id, "hours": min(hours, 720), "limit": min(limit, 1000)})
    return [
        {"id": str(r.id), "value": float(r.value), "recorded_at": r.recorded_at.isoformat()}
        for r in result
    ]


# ── IoT Alerts ────────────────────────────────────────────────────────────────

@router.get("/alerts", dependencies=[Depends(require_permission("iot:read"))])
async def list_iot_alerts(
    db: AsyncSession = Depends(get_db_with_tenant),
    status_filter: str | None = None,
    sensor_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    where = []
    params: dict = {"limit": min(limit, 200), "offset": max(offset, 0)}
    if status_filter:
        where.append("ia.status = :status")
        params["status"] = status_filter
    if sensor_id:
        where.append("ia.sensor_id = CAST(:sensor_id AS uuid)")
        params["sensor_id"] = sensor_id
    w = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(text(f"""
        SELECT ia.id, ia.sensor_id, ia.alert_type, ia.severity, ia.value,
               ia.message, ia.status, ia.created_at, ia.acknowledged_at,
               s.name AS sensor_name, s.sensor_type, s.unit,
               si.name AS site_name
        FROM iot_alerts ia
        JOIN iot_sensors s ON s.id = ia.sensor_id
        LEFT JOIN sites si ON si.id = s.site_id
        {w}
        ORDER BY ia.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params)
    rows = []
    for r in result:
        d = dict(r._mapping)
        d["id"] = str(d["id"])
        d["sensor_id"] = str(d["sensor_id"])
        if d.get("created_at"): d["created_at"] = d["created_at"].isoformat()
        if d.get("acknowledged_at"): d["acknowledged_at"] = d["acknowledged_at"].isoformat()
        rows.append(d)
    return rows


@router.put("/alerts/{alert_id}/acknowledge", dependencies=[Depends(require_permission("iot:read"))])
async def acknowledge_iot_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    result = await db.execute(text("""
        UPDATE iot_alerts
        SET status = 'acknowledged', acknowledged_by_user_id = CAST(:uid AS uuid),
            acknowledged_at = now()
        WHERE id = CAST(:id AS uuid) AND status = 'open'
        RETURNING id
    """), {"id": alert_id, "uid": token.user_id})
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found or already acknowledged")
    await db.commit()
    return {"ok": True}


@router.put("/alerts/{alert_id}/resolve", dependencies=[Depends(require_permission("iot:write"))])
async def resolve_iot_alert(alert_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(
        text("UPDATE iot_alerts SET status = 'resolved' WHERE id = CAST(:id AS uuid)"),
        {"id": alert_id},
    )
    await db.commit()
    return {"ok": True}


# ── Dashboard summary ─────────────────────────────────────────────────────────

@router.get("/dashboard", dependencies=[Depends(require_permission("iot:read"))])
async def iot_dashboard(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
):
    """Return a summary suitable for the IoT monitoring dashboard tile."""
    where = "WHERE s.is_active = TRUE"
    params: dict = {}
    if site_id:
        where += " AND s.site_id = CAST(:site_id AS uuid)"
        params["site_id"] = site_id

    sensors = (await db.execute(text(f"""
        SELECT s.id, s.name, s.sensor_type, s.unit, s.location,
               s.last_reading_at, s.last_reading_value, s.current_status,
               s.threshold_warning_low, s.threshold_warning_high,
               s.threshold_critical_low, s.threshold_critical_high,
               s.expected_interval_seconds,
               si.name AS site_name,
               (SELECT COUNT(*) FROM iot_alerts ia WHERE ia.sensor_id = s.id AND ia.status = 'open') AS open_alerts
        FROM iot_sensors s
        LEFT JOIN sites si ON si.id = s.site_id
        {where}
        ORDER BY s.sensor_type, s.name
    """), params)).fetchall()

    result = []
    for r in sensors:
        d = dict(r._mapping)
        d["id"] = str(d["id"])
        if d.get("last_reading_at"): d["last_reading_at"] = d["last_reading_at"].isoformat()

        # Mark offline if last reading is overdue
        if d["current_status"] == "normal" and d.get("last_reading_at"):
            from datetime import datetime, timezone as tz
            last = datetime.fromisoformat(d["last_reading_at"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=tz.utc)
            age_s = (datetime.now(tz.utc) - last).total_seconds()
            if age_s > d["expected_interval_seconds"] * 2:
                d["current_status"] = "offline"
        result.append(d)

    summary = {
        "total": len(result),
        "normal":   sum(1 for s in result if s["current_status"] == "normal"),
        "warning":  sum(1 for s in result if s["current_status"] == "warning"),
        "critical": sum(1 for s in result if s["current_status"] == "critical"),
        "offline":  sum(1 for s in result if s["current_status"] in ("offline", "unknown")),
        "open_alerts": sum(int(s.get("open_alerts", 0)) for s in result),
    }
    return {
        "total_sensors": summary["total"],
        "active_sensors": summary["normal"],
        "summary": summary,
        "sensors": result,
    }
