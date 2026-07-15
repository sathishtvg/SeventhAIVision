"""Access Control / Door Management router — /api/v1/access"""
import json
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/access", tags=["access_control"])

VALID_DOOR_TYPES = {"card_reader", "biometric", "pin", "combined", "manual"}
VALID_CRED_TYPES = {"card", "pin", "fingerprint", "face", "qr"}
VALID_EVENT_TYPES = {"granted", "denied", "forced", "held_open", "door_opened", "door_closed", "tamper"}
_ALERT_EVENT_TYPES = {"denied", "forced", "tamper"}


# ── Pydantic models ───────────────────────────────────────────────────────────

class DoorCreate(BaseModel):
    name: str
    location: str | None = None
    door_type: str = "card_reader"
    site_id: str | None = None
    camera_id: str | None = None

class DoorUpdate(BaseModel):
    name: str | None = None
    location: str | None = None
    door_type: str | None = None
    site_id: str | None = None
    camera_id: str | None = None
    is_active: bool | None = None

class CredentialCreate(BaseModel):
    holder_name: str | None = None
    user_id: str | None = None
    credential_type: str = "card"
    credential_ref: str
    expires_at: str | None = None

class CredentialUpdate(BaseModel):
    holder_name: str | None = None
    credential_type: str | None = None
    credential_ref: str | None = None
    is_active: bool | None = None
    expires_at: str | None = None

class RuleCreate(BaseModel):
    credential_id: str
    door_id: str
    schedule_days: str = "1234567"
    time_from: str | None = None
    time_to: str | None = None

class EventIngest(BaseModel):
    event_type: str
    credential_ref: str | None = None
    denial_reason: str | None = None
    occurred_at: str | None = None


# ── Doors ─────────────────────────────────────────────────────────────────────

@router.get("/doors", dependencies=[Depends(require_permission("access:read"))])
async def list_doors(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    is_active: bool | None = None,
    limit: int = 100,
    offset: int = 0,
):
    where = ["1=1"]
    params: dict = {"limit": min(limit, 500), "offset": max(offset, 0)}
    if site_id:
        where.append("d.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    if is_active is not None:
        where.append("d.is_active = :is_active")
        params["is_active"] = is_active
    w = " AND ".join(where)
    rows = await db.execute(text(f"""
        SELECT d.id, d.name, d.location, d.door_type, d.is_active,
               d.site_id, d.camera_id, d.created_at, d.updated_at,
               s.name AS site_name, c.name AS camera_name
        FROM access_doors d
        LEFT JOIN sites s ON s.id = d.site_id
        LEFT JOIN cameras c ON c.id = d.camera_id
        WHERE {w}
        ORDER BY d.name
        LIMIT :limit OFFSET :offset
    """), params)
    return [dict(r._mapping) for r in rows]


@router.post("/doors", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("access:write"))])
async def create_door(body: DoorCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    if body.door_type not in VALID_DOOR_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"door_type must be one of {sorted(VALID_DOOR_TYPES)}")
    row = (await db.execute(text("""
        INSERT INTO access_doors (tenant_id, name, location, door_type, site_id, camera_id)
        VALUES (current_setting('app.current_tenant')::uuid,
                :name, :loc, :dtype,
                CAST(:site_id AS uuid), CAST(:cam_id AS uuid))
        RETURNING id
    """), {
        "name": body.name, "loc": body.location, "dtype": body.door_type,
        "site_id": body.site_id, "cam_id": body.camera_id,
    })).first()
    await db.commit()
    return {"id": str(row.id)}


@router.put("/doors/{door_id}", dependencies=[Depends(require_permission("access:write"))])
async def update_door(door_id: str, body: DoorUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    if body.door_type and body.door_type not in VALID_DOOR_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"door_type must be one of {sorted(VALID_DOOR_TYPES)}")
    sets = []
    params: dict = {"id": door_id}
    if body.name is not None:       sets.append("name = :name");       params["name"] = body.name
    if body.location is not None:   sets.append("location = :loc");    params["loc"] = body.location
    if body.door_type is not None:  sets.append("door_type = :dtype"); params["dtype"] = body.door_type
    if body.site_id is not None:    sets.append("site_id = CAST(:site_id AS uuid)"); params["site_id"] = body.site_id
    if body.camera_id is not None:  sets.append("camera_id = CAST(:cam_id AS uuid)"); params["cam_id"] = body.camera_id
    if body.is_active is not None:  sets.append("is_active = :active"); params["active"] = body.is_active
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    sets.append("updated_at = now()")
    result = await db.execute(
        text(f"UPDATE access_doors SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) RETURNING id"), params)
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Door not found")
    await db.commit()
    return {"ok": True}


@router.delete("/doors/{door_id}", dependencies=[Depends(require_permission("access:write"))])
async def deactivate_door(door_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("UPDATE access_doors SET is_active = FALSE, updated_at = now() WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": door_id})
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Door not found")
    await db.commit()
    return {"ok": True}


# ── Credentials ───────────────────────────────────────────────────────────────

@router.get("/credentials", dependencies=[Depends(require_permission("access:read"))])
async def list_credentials(
    db: AsyncSession = Depends(get_db_with_tenant),
    is_active: bool | None = None,
    user_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    where = ["1=1"]
    params: dict = {"limit": min(limit, 500), "offset": max(offset, 0)}
    if is_active is not None:
        where.append("c.is_active = :is_active"); params["is_active"] = is_active
    if user_id:
        where.append("c.user_id = CAST(:user_id AS uuid)"); params["user_id"] = user_id
    w = " AND ".join(where)
    rows = await db.execute(text(f"""
        SELECT c.id, c.holder_name, c.user_id, c.credential_type, c.credential_ref,
               c.is_active, c.expires_at, c.created_at, c.updated_at,
               u.full_name AS user_full_name, u.email AS user_email
        FROM access_credentials c
        LEFT JOIN users u ON u.id = c.user_id
        WHERE {w}
        ORDER BY c.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params)
    return [dict(r._mapping) for r in rows]


@router.post("/credentials", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("access:write"))])
async def create_credential(body: CredentialCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    if body.credential_type not in VALID_CRED_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"credential_type must be one of {sorted(VALID_CRED_TYPES)}")
    if not body.holder_name and not body.user_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Either holder_name or user_id is required")
    row = (await db.execute(text("""
        INSERT INTO access_credentials
            (tenant_id, user_id, holder_name, credential_type, credential_ref, expires_at)
        VALUES (current_setting('app.current_tenant')::uuid,
                CAST(:uid AS uuid), :holder, :ctype, :cref,
                :exp)
        RETURNING id
    """), {
        "uid": body.user_id, "holder": body.holder_name,
        "ctype": body.credential_type, "cref": body.credential_ref,
        "exp": datetime.fromisoformat(body.expires_at) if body.expires_at else None,
    })).first()
    await db.commit()
    return {"id": str(row.id)}


@router.put("/credentials/{credential_id}",
            dependencies=[Depends(require_permission("access:write"))])
async def update_credential(
    credential_id: str, body: CredentialUpdate, db: AsyncSession = Depends(get_db_with_tenant)
):
    sets = []
    params: dict = {"id": credential_id}
    if body.holder_name is not None:    sets.append("holder_name = :holder");     params["holder"] = body.holder_name
    if body.credential_type is not None:
        if body.credential_type not in VALID_CRED_TYPES:
            raise HTTPException(422, f"credential_type must be one of {sorted(VALID_CRED_TYPES)}")
        sets.append("credential_type = :ctype"); params["ctype"] = body.credential_type
    if body.credential_ref is not None: sets.append("credential_ref = :cref");   params["cref"] = body.credential_ref
    if body.is_active is not None:      sets.append("is_active = :active");      params["active"] = body.is_active
    if body.expires_at is not None:     sets.append("expires_at = :exp"); params["exp"] = datetime.fromisoformat(body.expires_at)
    if not sets:
        raise HTTPException(422, "No fields to update")
    sets.append("updated_at = now()")
    result = await db.execute(
        text(f"UPDATE access_credentials SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) RETURNING id"), params)
    if result.first() is None:
        raise HTTPException(404, "Credential not found")
    await db.commit()
    return {"ok": True}


@router.delete("/credentials/{credential_id}",
               dependencies=[Depends(require_permission("access:write"))])
async def deactivate_credential(credential_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("UPDATE access_credentials SET is_active = FALSE, updated_at = now() WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": credential_id})
    if result.first() is None:
        raise HTTPException(404, "Credential not found")
    await db.commit()
    return {"ok": True}


# ── Rules ─────────────────────────────────────────────────────────────────────

@router.get("/rules", dependencies=[Depends(require_permission("access:read"))])
async def list_rules(
    db: AsyncSession = Depends(get_db_with_tenant),
    door_id: str | None = None,
    credential_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
):
    where = ["1=1"]
    params: dict = {"limit": min(limit, 1000), "offset": max(offset, 0)}
    if door_id:
        where.append("r.door_id = CAST(:door_id AS uuid)"); params["door_id"] = door_id
    if credential_id:
        where.append("r.credential_id = CAST(:cred_id AS uuid)"); params["cred_id"] = credential_id
    w = " AND ".join(where)
    rows = await db.execute(text(f"""
        SELECT r.id, r.credential_id, r.door_id, r.schedule_days,
               r.time_from, r.time_to, r.is_active, r.created_at,
               d.name AS door_name, c.holder_name AS credential_holder,
               c.credential_type
        FROM access_rules r
        JOIN access_doors d ON d.id = r.door_id
        JOIN access_credentials c ON c.id = r.credential_id
        WHERE {w}
        ORDER BY d.name, c.holder_name
        LIMIT :limit OFFSET :offset
    """), params)
    return [dict(r._mapping) for r in rows]


@router.post("/rules", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("access:write"))])
async def create_rule(body: RuleCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(text("""
        INSERT INTO access_rules (tenant_id, credential_id, door_id, schedule_days, time_from, time_to)
        VALUES (current_setting('app.current_tenant')::uuid,
                CAST(:cred_id AS uuid), CAST(:door_id AS uuid), :days,
                :tf, :tt)
        RETURNING id
    """), {
        "cred_id": body.credential_id, "door_id": body.door_id,
        "days": body.schedule_days,
        "tf": time.fromisoformat(body.time_from) if body.time_from else None,
        "tt": time.fromisoformat(body.time_to) if body.time_to else None,
    })).first()
    await db.commit()
    return {"id": str(row.id)}


@router.delete("/rules/{rule_id}", dependencies=[Depends(require_permission("access:write"))])
async def delete_rule(rule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("DELETE FROM access_rules WHERE id = CAST(:id AS uuid) RETURNING id"), {"id": rule_id})
    if result.first() is None:
        raise HTTPException(404, "Rule not found")
    await db.commit()
    return {"ok": True}


# ── Event ingestion ───────────────────────────────────────────────────────────

@router.post("/doors/{door_id}/events", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("access:ingest"))])
async def ingest_event(
    door_id: str,
    body: EventIngest,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if body.event_type not in VALID_EVENT_TYPES:
        raise HTTPException(422, f"event_type must be one of {sorted(VALID_EVENT_TYPES)}")

    # Verify door exists and get tenant_id
    door_row = (await db.execute(text("""
        SELECT id, tenant_id, name, camera_id FROM access_doors
        WHERE id = CAST(:id AS uuid) AND is_active = TRUE
    """), {"id": door_id})).first()
    if door_row is None:
        raise HTTPException(404, "Door not found or inactive")

    tenant_id = str(door_row.tenant_id)

    # Resolve credential_id from credential_ref if provided
    cred_id = None
    if body.credential_ref:
        cred_row = (await db.execute(text("""
            SELECT id FROM access_credentials
            WHERE credential_ref = :ref AND is_active = TRUE
            LIMIT 1
        """), {"ref": body.credential_ref})).first()
        if cred_row:
            cred_id = str(cred_row.id)

    if body.occurred_at:
        occurred_at = datetime.fromisoformat(body.occurred_at)
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    else:
        occurred_at = datetime.now(timezone.utc)

    # Insert access event
    evt_row = (await db.execute(text("""
        INSERT INTO access_events
            (tenant_id, door_id, credential_id, event_type, denial_reason, occurred_at)
        VALUES (CAST(:tid AS uuid), CAST(:did AS uuid), CAST(:cid AS uuid), :etype, :dreason, :occ)
        RETURNING id
    """), {
        "tid": tenant_id, "did": door_id, "cid": cred_id,
        "etype": body.event_type, "dreason": body.denial_reason,
        "occ": occurred_at,
    })).first()

    # For security-relevant events: insert into main alerts table + publish alert_created
    if body.event_type in _ALERT_EVENT_TYPES:
        sev = "critical" if body.event_type == "forced" else "high"
        code = f"access.{body.event_type}"
        title_map = {
            "denied":  f"Access denied at {door_row.name}",
            "forced":  f"Door forced open: {door_row.name}",
            "tamper":  f"Access reader tamper: {door_row.name}",
        }
        title = title_map.get(body.event_type, f"Access event at {door_row.name}")

        # Use the door's linked camera, falling back to any tenant camera
        cam_source = (
            f"'{door_row.camera_id}'" if door_row.camera_id
            else "(SELECT id FROM cameras WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1)"
        )
        alert_row = (await db.execute(text(f"""
            INSERT INTO alerts (tenant_id, camera_id, module_type, severity,
                                alert_code, message_params, title, message, status)
            SELECT CAST(:tid AS uuid),
                   {cam_source},
                   'access', :sev, :code,
                   CAST(:params AS jsonb), :title, :msg, 'open'
            WHERE {cam_source} IS NOT NULL
            RETURNING id
        """), {
            "tid": tenant_id, "sev": sev, "code": code,
            "params": json.dumps({
                "door_id": door_id,
                "door_name": door_row.name,
                "event_type": body.event_type,
                "credential_ref": body.credential_ref,
                "denial_reason": body.denial_reason,
            }),
            "title": title, "msg": title,
        })).first()

        if alert_row:
            _redis = getattr(request.app.state, "redis", None)
            if _redis:
                await _redis.publish(f"tenant_events:{tenant_id}", json.dumps({
                    "event_type": "alert_created",
                    "tenant_id": tenant_id,
                    "payload": {
                        "alert_id": str(alert_row.id),
                        "alert_code": code,
                        "severity": sev,
                        "module_type": "access",
                    },
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                }))

    await db.commit()
    return {"id": str(evt_row.id), "event_type": body.event_type}


# ── Events list ───────────────────────────────────────────────────────────────

@router.get("/events", dependencies=[Depends(require_permission("access:read"))])
async def list_events(
    db: AsyncSession = Depends(get_db_with_tenant),
    door_id: str | None = None,
    event_type: str | None = None,
    hours: int = 24,
    limit: int = 100,
    offset: int = 0,
):
    where = ["e.occurred_at >= now() - (:hours * INTERVAL '1 hour')"]
    params: dict = {"hours": min(hours, 720), "limit": min(limit, 500), "offset": max(offset, 0)}
    if door_id:
        where.append("e.door_id = CAST(:door_id AS uuid)"); params["door_id"] = door_id
    if event_type:
        where.append("e.event_type = :event_type"); params["event_type"] = event_type
    w = " AND ".join(where)
    rows = await db.execute(text(f"""
        SELECT e.id, e.door_id, e.credential_id, e.event_type, e.denial_reason,
               e.occurred_at, d.name AS door_name, d.location AS door_location,
               c.holder_name, c.credential_type, c.credential_ref
        FROM access_events e
        JOIN access_doors d ON d.id = e.door_id
        LEFT JOIN access_credentials c ON c.id = e.credential_id
        WHERE {w}
        ORDER BY e.occurred_at DESC
        LIMIT :limit OFFSET :offset
    """), params)
    return [dict(r._mapping) for r in rows]


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/dashboard", dependencies=[Depends(require_permission("access:read"))])
async def get_dashboard(db: AsyncSession = Depends(get_db_with_tenant)):
    summary = (await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE TRUE)                       AS total_doors,
            COUNT(*) FILTER (WHERE is_active)                  AS active_doors
        FROM access_doors
    """))).first()

    cred_summary = (await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE TRUE)      AS total_credentials,
            COUNT(*) FILTER (WHERE is_active) AS active_credentials
        FROM access_credentials
    """))).first()

    event_summary = (await db.execute(text("""
        SELECT
            COUNT(*)                                              AS events_today,
            COUNT(*) FILTER (WHERE event_type = 'granted')       AS granted_today,
            COUNT(*) FILTER (WHERE event_type = 'denied')        AS denied_today,
            COUNT(*) FILTER (WHERE event_type = 'forced')        AS forced_today,
            COUNT(*) FILTER (WHERE event_type = 'tamper')        AS tamper_today
        FROM access_events
        WHERE occurred_at >= CURRENT_DATE
    """))).first()

    recent = await db.execute(text("""
        SELECT e.id, e.event_type, e.denial_reason, e.occurred_at,
               d.name AS door_name, c.holder_name, c.credential_type
        FROM access_events e
        JOIN access_doors d ON d.id = e.door_id
        LEFT JOIN access_credentials c ON c.id = e.credential_id
        ORDER BY e.occurred_at DESC
        LIMIT 10
    """))

    return {
        "door_summary": dict(summary._mapping),
        "credential_summary": dict(cred_summary._mapping),
        "event_summary": dict(event_summary._mapping),
        "recent_events": [dict(r._mapping) for r in recent],
    }
