"""Alarm Panel Integration — panels, zones, events, arm/disarm, webhook ingest."""

import json
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limiter import limiter
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant, get_raw_db

router = APIRouter(prefix="/api/v1/alarms", tags=["alarms"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class PanelCreate(BaseModel):
    name: str
    site_id: str | None = None
    model: str | None = None
    serial_number: str | None = None
    protocol: str = "webhook"  # webhook | contact_id_tcp | mqtt | sdk
    host: str | None = None
    port: int | None = None
    notes: str | None = None


class PanelUpdate(BaseModel):
    name: str | None = None
    site_id: str | None = None
    model: str | None = None
    serial_number: str | None = None
    protocol: str | None = None
    host: str | None = None
    port: int | None = None
    notes: str | None = None
    is_active: bool | None = None


class ArmRequest(BaseModel):
    mode: str = "away"  # away | stay | night
    user_code: str | None = None


class ZoneCreate(BaseModel):
    zone_number: int
    name: str
    zone_type: str = "motion"
    linked_camera_id: str | None = None


class ZoneUpdate(BaseModel):
    name: str | None = None
    zone_type: str | None = None
    linked_camera_id: str | None = None
    is_active: bool | None = None


# Webhook ingest payload (from panel middleware or panel firmware)
class AlarmEventIngest(BaseModel):
    event_type: str
    zone_number: int | None = None
    description: str | None = None
    occurred_at: datetime | None = None
    raw_payload: dict | None = None


# ── Severity mapping ──────────────────────────────────────────────────────────

_EVENT_SEVERITY: dict[str, str] = {
    "zone_alarm": "high",
    "zone_tamper": "high",
    "panel_tamper": "critical",
    "panel_ac_fail": "medium",
    "panel_battery_low": "medium",
    "panel_offline": "medium",
    "panel_armed_away": "info",
    "panel_armed_stay": "info",
    "panel_armed_night": "info",
    "panel_disarmed": "info",
    "zone_restore": "info",
    "zone_bypass": "low",
    "zone_unbypass": "info",
    "panel_online": "info",
    "test_signal": "info",
    "unknown": "low",
}

_ALARM_ZONE_STATE: dict[str, str] = {
    "zone_alarm": "alarm",
    "zone_tamper": "tamper",
    "zone_restore": "normal",
    "zone_bypass": "bypass",
    "zone_unbypass": "normal",
}

_PANEL_ARM_STATE: dict[str, str] = {
    "panel_armed_away": "armed_away",
    "panel_armed_stay": "armed_stay",
    "panel_armed_night": "armed_night",
    "panel_disarmed": "disarmed",
    "zone_alarm": "alarm",
}

_CREATE_ALERT_EVENTS = {"zone_alarm", "zone_tamper", "panel_tamper", "panel_ac_fail", "panel_battery_low"}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/dashboard", dependencies=[Depends(require_permission("alarm:read"))])
async def get_alarm_dashboard(db: AsyncSession = Depends(get_db_with_tenant)):
    panels_result = await db.execute(text("""
        SELECT
            COUNT(*)                                                  AS total_panels,
            COUNT(*) FILTER (WHERE status = 'online')                AS online,
            COUNT(*) FILTER (WHERE status = 'offline')               AS offline,
            COUNT(*) FILTER (WHERE arm_state = 'alarm')             AS in_alarm,
            COUNT(*) FILTER (WHERE arm_state LIKE 'armed_%')        AS armed,
            COUNT(*) FILTER (WHERE arm_state = 'disarmed')          AS disarmed
        FROM alarm_panels WHERE is_active = TRUE
    """))
    panel_summary = dict(panels_result.first()._mapping)

    zones_result = await db.execute(text("""
        SELECT
            COUNT(*)                                                  AS total_zones,
            COUNT(*) FILTER (WHERE current_state = 'alarm')          AS zones_in_alarm,
            COUNT(*) FILTER (WHERE current_state = 'tamper')         AS zones_tampered,
            COUNT(*) FILTER (WHERE current_state = 'bypass')         AS zones_bypassed,
            COUNT(*) FILTER (WHERE current_state = 'normal')         AS zones_normal
        FROM alarm_zones WHERE is_active = TRUE
    """))
    zone_summary = dict(zones_result.first()._mapping)

    events_result = await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE occurred_at >= now() - INTERVAL '24 hours') AS events_24h,
            COUNT(*) FILTER (WHERE occurred_at >= now() - INTERVAL '24 hours'
                AND event_type IN ('zone_alarm','zone_tamper','panel_tamper'))  AS alarms_24h,
            COUNT(*) FILTER (WHERE occurred_at >= now() - INTERVAL '1 hour'
                AND event_type = 'zone_alarm')                                  AS alarms_1h
        FROM alarm_events
    """))
    event_summary = dict(events_result.first()._mapping)

    # Recent critical events
    recent_result = await db.execute(text("""
        SELECT ae.id, ae.event_type, ae.severity, ae.description, ae.occurred_at,
               ap.name AS panel_name, s.name AS site_name,
               az.name AS zone_name, az.zone_number
        FROM alarm_events ae
        JOIN alarm_panels ap ON ap.id = ae.panel_id
        LEFT JOIN sites s ON s.id = ap.site_id
        LEFT JOIN alarm_zones az ON az.id = ae.zone_id
        WHERE ae.severity IN ('high','critical')
        ORDER BY ae.occurred_at DESC
        LIMIT 10
    """))
    recent_alarms = [dict(r._mapping) for r in recent_result]

    # Panel list with zone counts
    panel_list_result = await db.execute(text("""
        SELECT ap.id, ap.name, ap.status, ap.arm_state,
               ap.last_contact_at, s.name AS site_name,
               COUNT(az.id) FILTER (WHERE az.is_active)               AS total_zones,
               COUNT(az.id) FILTER (WHERE az.current_state = 'alarm') AS alarm_zones,
               COUNT(az.id) FILTER (WHERE az.current_state = 'tamper') AS tamper_zones,
               COUNT(az.id) FILTER (WHERE az.current_state = 'bypass') AS bypass_zones
        FROM alarm_panels ap
        LEFT JOIN sites s ON s.id = ap.site_id
        LEFT JOIN alarm_zones az ON az.panel_id = ap.id
        WHERE ap.is_active = TRUE
        GROUP BY ap.id, s.name
        ORDER BY ap.name
    """))
    panels = [dict(r._mapping) for r in panel_list_result]

    return {
        "panel_summary": panel_summary,
        "zone_summary": zone_summary,
        "event_summary": event_summary,
        "recent_alarms": recent_alarms,
        "panels": panels,
    }


# ── Alarm Panels ──────────────────────────────────────────────────────────────

@router.get("/panels", dependencies=[Depends(require_permission("alarm:read"))])
async def list_panels(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(text("""
        SELECT ap.*, s.name AS site_name,
               COUNT(az.id) FILTER (WHERE az.is_active)               AS total_zones,
               COUNT(az.id) FILTER (WHERE az.current_state = 'alarm') AS alarm_zones
        FROM alarm_panels ap
        LEFT JOIN sites s ON s.id = ap.site_id
        LEFT JOIN alarm_zones az ON az.panel_id = ap.id
        GROUP BY ap.id, s.name
        ORDER BY ap.name
    """))
    rows = [dict(r._mapping) for r in result]
    # Never expose api_key in list view
    for row in rows:
        row.pop("api_key", None)
    return rows


@router.post("/panels", dependencies=[Depends(require_permission("alarm:manage"))])
async def create_panel(
    body: PanelCreate,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if body.protocol not in ("webhook", "contact_id_tcp", "mqtt", "sdk"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "protocol must be webhook|contact_id_tcp|mqtt|sdk")
    # Generate a unique API key for webhook authentication
    api_key = f"pak_{secrets.token_urlsafe(32)}"

    result = await db.execute(text("""
        INSERT INTO alarm_panels (
            tenant_id, site_id, name, model, serial_number,
            protocol, host, port, api_key, notes
        ) VALUES (
            current_setting('app.current_tenant')::uuid,
            CAST(:site_id AS uuid), :name, :model, :serial,
            :protocol, :host, :port, :api_key, :notes
        )
        RETURNING id, site_id, name, model, serial_number, protocol,
                  host, port, api_key, status, arm_state, is_active, created_at
    """), {
        "site_id": body.site_id,
        "name": body.name,
        "model": body.model,
        "serial": body.serial_number,
        "protocol": body.protocol,
        "host": body.host,
        "port": body.port,
        "api_key": api_key,
        "notes": body.notes,
    })
    row = result.first()
    await db.commit()
    return dict(row._mapping)  # api_key exposed only on creation


@router.get("/panels/{panel_id}", dependencies=[Depends(require_permission("alarm:read"))])
async def get_panel(panel_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    panel_result = await db.execute(text("""
        SELECT ap.id, ap.name, ap.model, ap.serial_number, ap.protocol,
               ap.host, ap.port, ap.status, ap.arm_state, ap.last_contact_at,
               ap.notes, ap.is_active, ap.created_at, ap.site_id,
               s.name AS site_name
        FROM alarm_panels ap
        LEFT JOIN sites s ON s.id = ap.site_id
        WHERE ap.id = CAST(:id AS uuid)
    """), {"id": panel_id})
    panel = panel_result.first()
    if not panel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Panel not found")

    zones_result = await db.execute(text("""
        SELECT az.*, c.name AS camera_name
        FROM alarm_zones az
        LEFT JOIN cameras c ON c.id = az.linked_camera_id
        WHERE az.panel_id = CAST(:pid AS uuid)
        ORDER BY az.zone_number
    """), {"pid": panel_id})
    zones = [dict(r._mapping) for r in zones_result]

    events_result = await db.execute(text("""
        SELECT ae.id, ae.event_type, ae.severity, ae.description,
               ae.zone_number, ae.occurred_at, az.name AS zone_name
        FROM alarm_events ae
        LEFT JOIN alarm_zones az ON az.id = ae.zone_id
        WHERE ae.panel_id = CAST(:pid AS uuid)
        ORDER BY ae.occurred_at DESC
        LIMIT 20
    """), {"pid": panel_id})
    recent_events = [dict(r._mapping) for r in events_result]

    return {**dict(panel._mapping), "zones": zones, "recent_events": recent_events}


@router.put("/panels/{panel_id}", dependencies=[Depends(require_permission("alarm:manage"))])
async def update_panel(
    panel_id: str,
    body: PanelUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    chk = await db.execute(
        text("SELECT id FROM alarm_panels WHERE id = CAST(:id AS uuid)"), {"id": panel_id}
    )
    if not chk.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Panel not found")

    sets, params = [], {"id": panel_id}
    for field, col in [("name", "name"), ("model", "model"), ("serial_number", "serial_number"),
                       ("protocol", "protocol"), ("host", "host"), ("port", "port"),
                       ("notes", "notes"), ("is_active", "is_active")]:
        val = getattr(body, field)
        if val is not None:
            sets.append(f"{col} = :{field}")
            params[field] = val
    if body.site_id is not None:
        sets.append("site_id = CAST(:site_id AS uuid)")
        params["site_id"] = body.site_id

    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")

    sets.append("updated_at = now()")
    result = await db.execute(
        text(f"UPDATE alarm_panels SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) RETURNING id, name, status, arm_state, is_active"),
        params,
    )
    await db.commit()
    return dict(result.first()._mapping)


# ── Arm / Disarm ──────────────────────────────────────────────────────────────

@router.put("/panels/{panel_id}/arm", dependencies=[Depends(require_permission("alarm:arm"))])
async def arm_panel(
    panel_id: str,
    body: ArmRequest,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if body.mode not in ("away", "stay", "night"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "mode must be away|stay|night")

    arm_state = f"armed_{body.mode}"
    event_type = f"panel_armed_{body.mode}"

    chk = await db.execute(
        text("SELECT id, arm_state FROM alarm_panels WHERE id = CAST(:id AS uuid)"), {"id": panel_id}
    )
    panel = chk.first()
    if not panel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Panel not found")

    await db.execute(text("""
        UPDATE alarm_panels SET arm_state = :arm_state, updated_at = now()
        WHERE id = CAST(:id AS uuid)
    """), {"arm_state": arm_state, "id": panel_id})

    await db.execute(text("""
        INSERT INTO alarm_events (tenant_id, panel_id, event_type, severity, description)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:pid AS uuid), :etype, 'info', :desc)
    """), {
        "pid": panel_id,
        "etype": event_type,
        "desc": f"Panel armed ({body.mode}) via dashboard by user {token.user_id}",
    })
    await db.commit()
    return {"panel_id": panel_id, "arm_state": arm_state}


@router.put("/panels/{panel_id}/disarm", dependencies=[Depends(require_permission("alarm:arm"))])
async def disarm_panel(
    panel_id: str,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    chk = await db.execute(
        text("SELECT id FROM alarm_panels WHERE id = CAST(:id AS uuid)"), {"id": panel_id}
    )
    if not chk.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Panel not found")

    await db.execute(text("""
        UPDATE alarm_panels SET arm_state = 'disarmed', updated_at = now()
        WHERE id = CAST(:id AS uuid)
    """), {"id": panel_id})

    await db.execute(text("""
        INSERT INTO alarm_events (tenant_id, panel_id, event_type, severity, description)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:pid AS uuid), 'panel_disarmed', 'info', :desc)
    """), {
        "pid": panel_id,
        "desc": f"Panel disarmed via dashboard by user {token.user_id}",
    })
    await db.commit()
    return {"panel_id": panel_id, "arm_state": "disarmed"}


@router.get("/panels/{panel_id}/events", dependencies=[Depends(require_permission("alarm:read"))])
async def list_panel_events(
    panel_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Return the most recent alarm_events for a specific panel."""
    result = await db.execute(text("""
        SELECT ae.id, ae.event_type, ae.severity, ae.description, ae.occurred_at,
               ae.zone_id, ae.zone_number, ae.alert_id
        FROM alarm_events ae
        WHERE ae.panel_id = CAST(:pid AS uuid)
        ORDER BY ae.occurred_at DESC
        LIMIT :limit
    """), {"pid": panel_id, "limit": min(limit, 200)})
    return [dict(r._mapping) for r in result]


@router.post("/panels/{panel_id}/rotate-key",
             dependencies=[Depends(require_permission("alarm:manage"))])
async def rotate_panel_key(
    panel_id: str,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Generate a new API key for a panel.

    The new key is returned **once** in this response and never exposed again.
    The old key is invalidated immediately — update the physical panel before
    calling this endpoint if the panel is currently sending events.
    """
    chk = await db.execute(
        text("SELECT id, name FROM alarm_panels WHERE id = CAST(:id AS uuid)"),
        {"id": panel_id},
    )
    panel_row = chk.first()
    if not panel_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Panel not found")

    new_key = f"pak_{secrets.token_urlsafe(32)}"

    await db.execute(
        text("UPDATE alarm_panels SET api_key = :key, updated_at = now() WHERE id = CAST(:id AS uuid)"),
        {"key": new_key, "id": panel_id},
    )
    await db.execute(text("""
        INSERT INTO alarm_events (tenant_id, panel_id, event_type, severity, description)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:pid AS uuid),
                'panel_key_rotated', 'info', :desc)
    """), {
        "pid": panel_id,
        "desc": f"API key rotated by user {token.user_id}",
    })
    await db.commit()

    return {
        "panel_id": panel_id,
        "api_key": new_key,
        "warning": "This key is shown only once. Update your panel immediately.",
    }


# ── Zones ─────────────────────────────────────────────────────────────────────

@router.post("/panels/{panel_id}/zones", dependencies=[Depends(require_permission("alarm:manage"))])
async def create_zone(
    panel_id: str,
    body: ZoneCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    valid_types = {"motion", "door", "window", "glass_break", "smoke", "heat",
                   "panic", "tamper", "24hr", "vibration"}
    if body.zone_type not in valid_types:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"zone_type must be one of: {', '.join(sorted(valid_types))}")

    chk = await db.execute(
        text("SELECT id FROM alarm_panels WHERE id = CAST(:id AS uuid)"), {"id": panel_id}
    )
    if not chk.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Panel not found")

    result = await db.execute(text("""
        INSERT INTO alarm_zones (
            tenant_id, panel_id, zone_number, name, zone_type, linked_camera_id
        ) VALUES (
            current_setting('app.current_tenant')::uuid,
            CAST(:pid AS uuid), :znum, :name, :ztype, CAST(:cam_id AS uuid)
        )
        ON CONFLICT (panel_id, zone_number) DO NOTHING
        RETURNING id, panel_id, zone_number, name, zone_type, current_state, is_active
    """), {
        "pid": panel_id,
        "znum": body.zone_number,
        "name": body.name,
        "ztype": body.zone_type,
        "cam_id": body.linked_camera_id,
    })
    row = result.first()
    if not row:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Zone {body.zone_number} already exists on this panel")
    await db.commit()
    return dict(row._mapping)


@router.put("/zones/{zone_id}", dependencies=[Depends(require_permission("alarm:manage"))])
async def update_zone(
    zone_id: str,
    body: ZoneUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    chk = await db.execute(
        text("SELECT id FROM alarm_zones WHERE id = CAST(:id AS uuid)"), {"id": zone_id}
    )
    if not chk.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")

    sets, params = [], {"id": zone_id}
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.zone_type is not None:
        sets.append("zone_type = :ztype"); params["ztype"] = body.zone_type
    if body.linked_camera_id is not None:
        sets.append("linked_camera_id = CAST(:cam AS uuid)"); params["cam"] = body.linked_camera_id
    if body.is_active is not None:
        sets.append("is_active = :active"); params["active"] = body.is_active

    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")

    sets.append("updated_at = now()")
    result = await db.execute(
        text(f"UPDATE alarm_zones SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) RETURNING *"),
        params,
    )
    await db.commit()
    return dict(result.first()._mapping)


@router.put("/zones/{zone_id}/bypass", dependencies=[Depends(require_permission("alarm:arm"))])
async def bypass_zone(
    zone_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    result = await db.execute(
        text("SELECT id, panel_id, zone_number, current_state FROM alarm_zones WHERE id = CAST(:id AS uuid)"),
        {"id": zone_id},
    )
    zone = result.first()
    if not zone:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")

    bypass = zone.current_state != "bypass"
    new_state = "bypass" if bypass else "normal"
    event_type = "zone_bypass" if bypass else "zone_unbypass"

    await db.execute(
        text("UPDATE alarm_zones SET current_state = :s, updated_at = now() WHERE id = CAST(:id AS uuid)"),
        {"s": new_state, "id": zone_id},
    )
    await db.execute(text("""
        INSERT INTO alarm_events (tenant_id, panel_id, zone_id, zone_number, event_type, severity)
        VALUES (
            current_setting('app.current_tenant')::uuid,
            CAST(:pid AS uuid), CAST(:zid AS uuid), :znum, :etype, 'low'
        )
    """), {
        "pid": str(zone.panel_id),
        "zid": zone_id,
        "znum": zone.zone_number,
        "etype": event_type,
    })
    await db.commit()
    return {"zone_id": zone_id, "current_state": new_state}


# ── Event Ingest (Webhook) ────────────────────────────────────────────────────

@router.post("/events/ingest")
@limiter.limit("120/minute")
async def ingest_event(
    request: Request,
    body: AlarmEventIngest,
    x_panel_key: str = Header(..., alias="X-Panel-Key"),
    db: AsyncSession = Depends(get_raw_db),
):
    """
    Webhook endpoint called by alarm panel middleware.
    Authenticated via X-Panel-Key header matching alarm_panels.api_key.
    Uses raw DB (bypasses RLS) to look up panel by api_key across tenants.
    """
    panel_result = await db.execute(
        text("SELECT id, tenant_id, name, arm_state FROM lookup_alarm_panel_by_key(:key)"),
        {"key": x_panel_key},
    )
    panel = panel_result.first()
    if not panel:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid panel key")

    tenant_id = str(panel.tenant_id)
    panel_id = str(panel.id)

    # Set tenant context for RLS-aware inserts
    await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tenant_id})

    event_type = body.event_type
    severity = _EVENT_SEVERITY.get(event_type, "info")
    occurred_at = body.occurred_at or datetime.now(timezone.utc)

    # Find zone if zone_number provided
    zone_id = None
    zone_camera_id = None
    if body.zone_number is not None:
        zone_result = await db.execute(text("""
            SELECT id, linked_camera_id FROM alarm_zones
            WHERE panel_id = CAST(:pid AS uuid) AND zone_number = :znum
        """), {"pid": panel_id, "znum": body.zone_number})
        zone_row = zone_result.first()
        if zone_row:
            zone_id = str(zone_row.id)
            zone_camera_id = str(zone_row.linked_camera_id) if zone_row.linked_camera_id else None
            # Update zone state
            new_zone_state = _ALARM_ZONE_STATE.get(event_type)
            if new_zone_state:
                await db.execute(text("""
                    UPDATE alarm_zones SET current_state = :s, updated_at = now()
                    WHERE id = CAST(:zid AS uuid)
                """), {"s": new_zone_state, "zid": zone_id})

    # Update panel arm state / last_contact
    new_arm_state = _PANEL_ARM_STATE.get(event_type)
    arm_update = f", arm_state = '{new_arm_state}'" if new_arm_state else ""
    panel_status = "online"
    if event_type == "panel_offline":
        panel_status = "offline"
    elif event_type in ("zone_alarm",):
        panel_status = "online"

    await db.execute(text(f"""
        UPDATE alarm_panels
        SET last_contact_at = :ts, status = :pstatus {arm_update}, updated_at = now()
        WHERE id = CAST(:pid AS uuid)
    """), {"ts": occurred_at, "pstatus": panel_status, "pid": panel_id})

    # Insert event
    event_result = await db.execute(text("""
        INSERT INTO alarm_events (
            tenant_id, panel_id, zone_id, zone_number,
            event_type, severity, description, raw_payload, occurred_at
        ) VALUES (
            CAST(:tid AS uuid), CAST(:pid AS uuid), CAST(:zid AS uuid), :znum,
            :etype, :sev, :desc, CAST(:payload AS jsonb), :ts
        )
        RETURNING id
    """), {
        "tid": tenant_id,
        "pid": panel_id,
        "zid": zone_id,
        "znum": body.zone_number,
        "etype": event_type,
        "sev": severity,
        "desc": body.description,
        "payload": str(body.raw_payload) if body.raw_payload else None,
        "ts": occurred_at,
    })
    event_id = str(event_result.scalar())

    # Auto-create alert for high-severity events
    alert_id = None
    if event_type in _CREATE_ALERT_EVENTS:
        zone_name = f"Zone {body.zone_number}" if body.zone_number else "panel"
        alert_title = {
            "zone_alarm": f"Alarm triggered: {zone_name} — {panel.name}",
            "zone_tamper": f"Zone tamper detected: {zone_name} — {panel.name}",
            "panel_tamper": f"Panel tamper detected: {panel.name}",
            "panel_ac_fail": f"AC power failure: {panel.name}",
            "panel_battery_low": f"Low battery: {panel.name}",
        }.get(event_type, f"Alarm event: {panel.name}")

        alert_result = await db.execute(text("""
            INSERT INTO alerts (
                tenant_id, camera_id, module_type, severity,
                alert_code, message_params, title, message, status
            )
            SELECT CAST(:tid AS uuid),
                   COALESCE(az.linked_camera_id, (
                       SELECT id FROM cameras
                       WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1
                   )),
                   'alarm', :sev,
                   :code, CAST(:params AS jsonb), :title, :desc, 'open'
            FROM alarm_panels ap
            LEFT JOIN alarm_zones az ON az.id = CAST(:zid AS uuid)
            WHERE ap.id = CAST(:pid AS uuid)
              AND COALESCE(az.linked_camera_id, (
                      SELECT id FROM cameras
                      WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1
                  )) IS NOT NULL
            RETURNING id
        """), {
            "tid": tenant_id,
            "sev": severity,
            "code": f"alarm.{event_type}",
            "params": f'{{"panel": "{panel.name}", "zone": {body.zone_number}}}',
            "title": alert_title,
            "desc": body.description or alert_title,
            "zid": zone_id,
            "pid": panel_id,
        })
        alert_row = alert_result.first()
        if alert_row:
            alert_id = str(alert_row.id)
            # Link alert to event
            await db.execute(text(
                "UPDATE alarm_events SET alert_id = CAST(:aid AS uuid) WHERE id = CAST(:eid AS uuid)"
            ), {"aid": alert_id, "eid": event_id})

            # Auto-create incident for critical / high alarms
            incident_id = None
            if severity in ("critical", "high"):
                msg_params = json.dumps({"panel": panel.name, "zone": body.zone_number})
                inc_result = await db.execute(text("""
                    INSERT INTO incidents (
                        tenant_id, alert_id, camera_id, title, description,
                        alert_code, message_params, severity, status, is_auto_created
                    )
                    SELECT CAST(:tid AS uuid), CAST(:aid AS uuid),
                           COALESCE(az.linked_camera_id, (
                               SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1
                           )),
                           :title, :desc,
                           :code, CAST(:params AS jsonb), :sev, 'open', TRUE
                    FROM alarm_panels ap
                    LEFT JOIN alarm_zones az ON az.id = CAST(:zid AS uuid)
                    WHERE ap.id = CAST(:pid AS uuid)
                      AND COALESCE(az.linked_camera_id, (
                              SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1
                          )) IS NOT NULL
                    RETURNING id
                """), {
                    "tid": tenant_id,
                    "aid": alert_id,
                    "title": alert_title,
                    "desc": body.description or alert_title,
                    "code": f"alarm.{event_type}",
                    "params": msg_params,
                    "sev": severity,
                    "zid": zone_id,
                    "pid": panel_id,
                })
                inc_row = inc_result.first()
                incident_id = str(inc_row.id) if inc_row else None

            # Auto-snapshot: create evidence record linking zone camera to this alarm
            if event_type == "zone_alarm" and zone_camera_id:
                await db.execute(text("""
                    INSERT INTO evidence (tenant_id, incident_id, media_type, storage_path, captured_at)
                    VALUES (CAST(:tid AS uuid), CAST(:iid AS uuid), 'image', :path, :ts)
                """), {
                    "tid":  tenant_id,
                    "iid":  incident_id,
                    "path": f"alarm_snapshot/alert_{alert_id}",
                    "ts":   occurred_at,
                })

            # Publish real-time event for dashboard / WebSocket clients
            redis = getattr(request.app.state, "redis", None)
            if redis:
                now_str = datetime.now(timezone.utc).isoformat()
                await redis.publish(f"tenant_events:{tenant_id}", json.dumps({
                    "event_type": "alert_created",
                    "tenant_id": tenant_id,
                    "payload": {
                        "alert_id": alert_id,
                        "alert_code": f"alarm.{event_type}",
                        "severity": severity,
                        "panel_name": panel.name,
                    },
                    "occurred_at": now_str,
                }))

    await db.commit()
    return {"event_id": event_id, "alert_id": alert_id, "severity": severity}


# ── Events List ───────────────────────────────────────────────────────────────

@router.get("/events", dependencies=[Depends(require_permission("alarm:read"))])
async def list_events(
    panel_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    wheres, params = [], {"limit": limit + 1, "offset": offset}
    if panel_id:
        wheres.append("ae.panel_id = CAST(:panel_id AS uuid)"); params["panel_id"] = panel_id
    if event_type:
        wheres.append("ae.event_type = :event_type"); params["event_type"] = event_type
    if severity:
        wheres.append("ae.severity = :severity"); params["severity"] = severity

    where_clause = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    result = await db.execute(text(f"""
        SELECT ae.id, ae.event_type, ae.severity, ae.description,
               ae.zone_number, ae.occurred_at, ae.alert_id,
               ap.name AS panel_name,
               az.name AS zone_name,
               s.name  AS site_name
        FROM alarm_events ae
        JOIN alarm_panels ap ON ap.id = ae.panel_id
        LEFT JOIN alarm_zones az ON az.id = ae.zone_id
        LEFT JOIN sites s ON s.id = ap.site_id
        {where_clause}
        ORDER BY ae.occurred_at DESC
        LIMIT :limit OFFSET :offset
    """), params)
    rows = [dict(r._mapping) for r in result]
    return {"items": rows[:limit], "has_more": len(rows) > limit}
