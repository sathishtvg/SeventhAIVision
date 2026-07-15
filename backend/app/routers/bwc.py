"""Body Worn Camera (BWC) Management router"""
from typing import Optional
from uuid import UUID

import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(tags=["bwc"])

VALID_STATUS     = {"available", "assigned", "recording", "docked", "low_battery", "fault", "retired"}
VALID_TRIGGERS   = {"manual", "pre_event", "auto_incident", "panic", "scheduled"}
VALID_REC_STATUS = {"recording", "completed", "failed", "deleted"}


# ── dashboard ────────────────────────────────────────────────────────────────

@router.get("/api/v1/bwc/dashboard", dependencies=[Depends(require_permission("bwc:read"))])
async def bwc_dashboard(db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM body_cameras WHERE is_active = TRUE)                              AS total_cameras,
            (SELECT COUNT(*) FROM body_cameras WHERE status = 'available' AND is_active = TRUE)     AS available,
            (SELECT COUNT(*) FROM body_cameras WHERE status = 'assigned'  AND is_active = TRUE)     AS assigned,
            (SELECT COUNT(*) FROM body_cameras WHERE is_recording = TRUE  AND is_active = TRUE)     AS recording,
            (SELECT COUNT(*) FROM body_cameras WHERE battery_pct <= 20    AND is_active = TRUE)     AS low_battery,
            (SELECT COUNT(*) FROM body_cameras
             WHERE storage_used_gb / NULLIF(storage_total_gb, 0) >= 0.9   AND is_active = TRUE)    AS storage_warning,
            (SELECT COUNT(*) FROM bwc_recordings WHERE status = 'recording')                       AS active_recordings,
            (SELECT COUNT(*) FROM bwc_recordings
             WHERE started_at >= now()::date)                                                       AS recordings_today
    """))).mappings().one()
    return dict(row)


# ── cameras ──────────────────────────────────────────────────────────────────

@router.get("/api/v1/bwc/cameras", dependencies=[Depends(require_permission("bwc:read"))])
async def list_cameras(
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    filters = "WHERE bc.is_active = TRUE"
    params: dict = {}
    if status:
        filters += " AND bc.status = :status"
        params["status"] = status

    rows = await db.execute(text(f"""
        SELECT bc.*,
               u.full_name AS assigned_user_name,
               (SELECT COUNT(*) FROM bwc_recordings r WHERE r.camera_id = bc.id) AS total_recordings
        FROM body_cameras bc
        LEFT JOIN users u ON u.id = bc.assigned_user_id
        {filters}
        ORDER BY bc.serial_number
    """), params)
    return [dict(r._mapping) for r in rows]


@router.post("/api/v1/bwc/cameras", dependencies=[Depends(require_permission("bwc:manage"))])
async def register_camera(
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    serial = (body.get("serial_number") or "").strip()
    if not serial:
        raise HTTPException(400, "serial_number is required")

    row = await db.execute(text("""
        INSERT INTO body_cameras (tenant_id, serial_number, model, firmware_version,
                                  storage_total_gb, notes)
        VALUES (:tid, :serial, :model, :fw, :storage, :notes)
        RETURNING *
    """), {
        "tid":     token.tenant_id,
        "serial":  serial,
        "model":   body.get("model"),
        "fw":      body.get("firmware_version"),
        "storage": body.get("storage_total_gb", 64),
        "notes":   body.get("notes"),
    })
    await db.commit()
    return dict(row.mappings().one())


@router.get("/api/v1/bwc/cameras/{camera_id}", dependencies=[Depends(require_permission("bwc:read"))])
async def get_camera(camera_id: UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    cam = (await db.execute(text("""
        SELECT bc.*, u.full_name AS assigned_user_name
        FROM body_cameras bc
        LEFT JOIN users u ON u.id = bc.assigned_user_id
        WHERE bc.id = :id AND bc.is_active = TRUE
    """), {"id": str(camera_id)})).mappings().first()
    if not cam:
        raise HTTPException(404, "Camera not found")

    recent_events = (await db.execute(text("""
        SELECT e.*, u.full_name AS user_name FROM bwc_events e
        LEFT JOIN users u ON u.id = e.user_id
        WHERE e.camera_id = :id ORDER BY e.occurred_at DESC LIMIT 20
    """), {"id": str(camera_id)})).mappings().all()

    recent_recs = (await db.execute(text("""
        SELECT r.*, u.full_name AS user_name FROM bwc_recordings r
        LEFT JOIN users u ON u.id = r.user_id
        WHERE r.camera_id = :id ORDER BY r.started_at DESC LIMIT 10
    """), {"id": str(camera_id)})).mappings().all()

    return {
        **dict(cam),
        "recent_events": [dict(e) for e in recent_events],
        "recent_recordings": [dict(r) for r in recent_recs],
    }


@router.put("/api/v1/bwc/cameras/{camera_id}", dependencies=[Depends(require_permission("bwc:manage"))])
async def update_camera(camera_id: UUID, body: dict, db: AsyncSession = Depends(get_db_with_tenant)):
    allowed = {"model", "firmware_version", "storage_total_gb", "notes", "is_active"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = str(camera_id)
    await db.execute(text(f"UPDATE body_cameras SET {set_clause}, updated_at = now() WHERE id = :id"), updates)
    await db.commit()
    return {"ok": True}


@router.put("/api/v1/bwc/cameras/{camera_id}/telemetry", dependencies=[Depends(require_permission("bwc:write"))])
async def update_telemetry(camera_id: UUID, body: dict, db: AsyncSession = Depends(get_db_with_tenant)):
    """Receive battery % and storage stats from camera sync."""
    battery = body.get("battery_pct")
    storage_used = body.get("storage_used_gb")

    new_status = None
    if battery is not None and battery <= 15:
        new_status = "low_battery"

    await db.execute(text("""
        UPDATE body_cameras
        SET battery_pct    = COALESCE(:batt, battery_pct),
            storage_used_gb = COALESCE(:used, storage_used_gb),
            last_sync_at   = now(),
            status = COALESCE(CAST(:new_status AS VARCHAR), status),
            updated_at = now()
        WHERE id = :id
    """), {"batt": battery, "used": storage_used, "new_status": new_status, "id": str(camera_id)})

    # Log telemetry event if concerning
    if battery is not None and battery <= 20:
        cam = (await db.execute(text(
            "SELECT tenant_id, assigned_user_id FROM body_cameras WHERE id = :id"
        ), {"id": str(camera_id)})).mappings().first()
        if cam:
            await db.execute(text("""
                INSERT INTO bwc_events (tenant_id, camera_id, user_id, event_type, detail, battery_pct)
                VALUES (:tid, :cid, :uid, 'low_battery', :detail, :batt)
            """), {
                "tid":    cam["tenant_id"],
                "cid":    str(camera_id),
                "uid":    cam["assigned_user_id"],
                "detail": f"Battery at {battery}%",
                "batt":   battery,
            })

    await db.commit()
    return {"ok": True}


# ── assignments ───────────────────────────────────────────────────────────────

@router.post("/api/v1/bwc/cameras/{camera_id}/assign", dependencies=[Depends(require_permission("bwc:write"))])
async def assign_camera(
    camera_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    user_id = body.get("user_id")
    if not user_id:
        raise HTTPException(400, "user_id is required")

    cam = (await db.execute(text(
        "SELECT status FROM body_cameras WHERE id = :id AND is_active = TRUE"
    ), {"id": str(camera_id)})).mappings().first()
    if not cam:
        raise HTTPException(404, "Camera not found")
    if cam["status"] not in ("available", "docked"):
        raise HTTPException(422, f"Camera is currently '{cam['status']}' and cannot be assigned")

    await db.execute(text("""
        UPDATE body_cameras
        SET status = 'assigned', assigned_user_id = :uid, assigned_at = now(), updated_at = now()
        WHERE id = :id
    """), {"uid": user_id, "id": str(camera_id)})

    await db.execute(text("""
        INSERT INTO bwc_assignments (tenant_id, camera_id, user_id, assigned_by, shift_id, notes)
        VALUES (:tid, :cid, :uid, :by, :sid, :notes)
    """), {
        "tid":   token.tenant_id,
        "cid":   str(camera_id),
        "uid":   user_id,
        "by":    token.user_id,
        "sid":   body.get("shift_id"),
        "notes": body.get("notes"),
    })

    await db.execute(text("""
        INSERT INTO bwc_events (tenant_id, camera_id, user_id, event_type, detail)
        VALUES (:tid, :cid, :uid, 'assigned', :detail)
    """), {
        "tid":    token.tenant_id,
        "cid":    str(camera_id),
        "uid":    user_id,
        "detail": f"Assigned by user {token.user_id}",
    })

    await db.commit()
    return {"ok": True}


@router.post("/api/v1/bwc/cameras/{camera_id}/unassign", dependencies=[Depends(require_permission("bwc:write"))])
async def unassign_camera(
    camera_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    cam = (await db.execute(text(
        "SELECT tenant_id, assigned_user_id FROM body_cameras WHERE id = :id"
    ), {"id": str(camera_id)})).mappings().first()
    if not cam:
        raise HTTPException(404, "Camera not found")

    await db.execute(text("""
        UPDATE body_cameras
        SET status = 'available', assigned_user_id = NULL, assigned_at = NULL, updated_at = now()
        WHERE id = :id
    """), {"id": str(camera_id)})

    await db.execute(text("""
        UPDATE bwc_assignments SET returned_at = now()
        WHERE camera_id = :cid AND returned_at IS NULL
    """), {"cid": str(camera_id)})

    await db.execute(text("""
        INSERT INTO bwc_events (tenant_id, camera_id, user_id, event_type, detail)
        VALUES (:tid, :cid, :uid, 'returned', :detail)
    """), {
        "tid":    cam["tenant_id"],
        "cid":    str(camera_id),
        "uid":    cam["assigned_user_id"],
        "detail": body.get("notes", "Camera returned to dock"),
    })

    await db.commit()
    return {"ok": True}


@router.get("/api/v1/bwc/cameras/{camera_id}/assignments", dependencies=[Depends(require_permission("bwc:read"))])
async def list_assignments(camera_id: UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    rows = await db.execute(text("""
        SELECT a.*, u.full_name AS user_name, ab.full_name AS assigned_by_name
        FROM bwc_assignments a
        JOIN users u ON u.id = a.user_id
        LEFT JOIN users ab ON ab.id = a.assigned_by
        WHERE a.camera_id = :id
        ORDER BY a.assigned_at DESC
        LIMIT 50
    """), {"id": str(camera_id)})
    return [dict(r._mapping) for r in rows]


# ── recordings ────────────────────────────────────────────────────────────────

@router.get("/api/v1/bwc/recordings", dependencies=[Depends(require_permission("bwc:read"))])
async def list_recordings(
    camera_id: Optional[str] = None,
    user_id: Optional[str] = None,
    status: Optional[str] = None,
    incident_id: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    filters = "WHERE 1=1"
    params: dict = {"limit": limit + 1, "offset": offset}
    if camera_id:
        filters += " AND r.camera_id = :cid"
        params["cid"] = camera_id
    if user_id:
        filters += " AND r.user_id = :uid"
        params["uid"] = user_id
    if status:
        filters += " AND r.status = :status"
        params["status"] = status
    if incident_id:
        filters += " AND r.incident_id = :iid"
        params["iid"] = incident_id

    rows_raw = await db.execute(text(f"""
        SELECT r.*, bc.serial_number, u.full_name AS user_name
        FROM bwc_recordings r
        JOIN body_cameras bc ON bc.id = r.camera_id
        LEFT JOIN users u ON u.id = r.user_id
        {filters}
        ORDER BY r.started_at DESC
        LIMIT :limit OFFSET :offset
    """), params)
    rows = [dict(r._mapping) for r in rows_raw]
    return {"items": rows[:limit], "has_more": len(rows) > limit}


@router.post("/api/v1/bwc/cameras/{camera_id}/recordings/start",
             dependencies=[Depends(require_permission("bwc:write"))])
async def start_recording(
    request: Request,
    camera_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    cam = (await db.execute(text(
        "SELECT status, assigned_user_id, tenant_id FROM body_cameras WHERE id = :id AND is_active = TRUE"
    ), {"id": str(camera_id)})).mappings().first()
    if not cam:
        raise HTTPException(404, "Camera not found")
    if cam["status"] not in ("assigned", "available"):
        raise HTTPException(422, f"Camera status '{cam['status']}' cannot start recording")

    trigger = body.get("trigger_type", "manual")
    if trigger not in VALID_TRIGGERS:
        trigger = "manual"

    row = await db.execute(text("""
        INSERT INTO bwc_recordings (tenant_id, camera_id, user_id, incident_id, title,
                                    trigger_type, latitude, longitude, notes)
        VALUES (:tid, :cid, :uid, :iid, :title, :trigger, :lat, :lon, :notes)
        RETURNING *
    """), {
        "tid":     cam["tenant_id"],
        "cid":     str(camera_id),
        "uid":     cam["assigned_user_id"] or token.user_id,
        "iid":     body.get("incident_id"),
        "title":   body.get("title"),
        "trigger": trigger,
        "lat":     body.get("latitude"),
        "lon":     body.get("longitude"),
        "notes":   body.get("notes"),
    })

    await db.execute(text("""
        UPDATE body_cameras SET is_recording = TRUE, status = 'recording', updated_at = now()
        WHERE id = :id
    """), {"id": str(camera_id)})

    await db.execute(text("""
        INSERT INTO bwc_events (tenant_id, camera_id, user_id, event_type, detail)
        VALUES (:tid, :cid, :uid, 'recording_started', :detail)
    """), {
        "tid":    cam["tenant_id"],
        "cid":    str(camera_id),
        "uid":    cam["assigned_user_id"],
        "detail": f"Trigger: {trigger}",
    })

    await db.commit()
    rec_row = dict(row.mappings().one())
    redis = getattr(request.app.state, "redis", None)
    if redis:
        from datetime import datetime, timezone
        await redis.publish(f"tenant_events:{cam['tenant_id']}", json.dumps({
            "event_type": "bwc_recording_started",
            "tenant_id": str(cam["tenant_id"]),
            "payload": {"recording_id": rec_row.get("id"), "camera_id": str(camera_id), "trigger_type": trigger},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }))
    return rec_row


@router.post("/api/v1/bwc/cameras/{camera_id}/recordings/{recording_id}/stop",
             dependencies=[Depends(require_permission("bwc:write"))])
async def stop_recording(
    request: Request,
    camera_id: UUID,
    recording_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    rec = (await db.execute(text(
        "SELECT tenant_id, user_id FROM bwc_recordings WHERE id = :id AND camera_id = :cid AND status = 'recording'"
    ), {"id": str(recording_id), "cid": str(camera_id)})).mappings().first()
    if not rec:
        raise HTTPException(404, "Active recording not found")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    await db.execute(text("""
        UPDATE bwc_recordings
        SET ended_at = now(), status = 'completed',
            duration_seconds = :dur,
            file_size_mb = :fsize,
            notes = COALESCE(:notes, notes)
        WHERE id = :id
    """), {
        "dur":   body.get("duration_seconds"),
        "fsize": body.get("file_size_mb"),
        "notes": body.get("notes"),
        "id":    str(recording_id),
    })

    await db.execute(text("""
        UPDATE body_cameras
        SET is_recording = FALSE,
            status = CASE WHEN assigned_user_id IS NOT NULL THEN 'assigned' ELSE 'available' END,
            updated_at = now()
        WHERE id = :id
    """), {"id": str(camera_id)})

    await db.execute(text("""
        INSERT INTO bwc_events (tenant_id, camera_id, user_id, event_type, detail)
        VALUES (:tid, :cid, :uid, 'recording_stopped', :detail)
    """), {
        "tid":    rec["tenant_id"],
        "cid":    str(camera_id),
        "uid":    rec["user_id"],
        "detail": f"Recording {str(recording_id)[:8]} completed",
    })

    await db.commit()
    redis = getattr(request.app.state, "redis", None)
    if redis:
        from datetime import datetime, timezone
        await redis.publish(f"tenant_events:{rec['tenant_id']}", json.dumps({
            "event_type": "bwc_recording_stopped",
            "tenant_id": str(rec["tenant_id"]),
            "payload": {"recording_id": str(recording_id), "camera_id": str(camera_id)},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }))
    return {"ok": True}


@router.put("/api/v1/bwc/recordings/{recording_id}/link-incident",
            dependencies=[Depends(require_permission("bwc:write"))])
async def link_incident(recording_id: UUID, body: dict, db: AsyncSession = Depends(get_db_with_tenant)):
    rec = (await db.execute(text(
        "SELECT id, tenant_id, started_at FROM bwc_recordings WHERE id = :id"
    ), {"id": str(recording_id)})).mappings().first()
    if not rec:
        raise HTTPException(404, "Recording not found")

    incident_id = body.get("incident_id")
    if incident_id:
        inc = (await db.execute(text(
            "SELECT id FROM incidents WHERE id = CAST(:iid AS uuid)"
        ), {"iid": incident_id})).mappings().first()
        if not inc:
            raise HTTPException(404, "Incident not found")

    await db.execute(text(
        "UPDATE bwc_recordings SET incident_id = :iid WHERE id = :id"
    ), {"iid": incident_id, "id": str(recording_id)})

    if incident_id:
        await db.execute(text("""
            INSERT INTO evidence (tenant_id, incident_id, media_type, storage_path, captured_at)
            VALUES (CAST(:tid AS uuid), CAST(:iid AS uuid), 'video', :path, :ts)
        """), {
            "tid":  str(rec["tenant_id"]),
            "iid":  incident_id,
            "path": f"bwc/{str(recording_id)}",
            "ts":   rec["started_at"],
        })

    await db.commit()
    return {"ok": True, "recording_id": str(recording_id), "incident_id": incident_id}


# ── events ────────────────────────────────────────────────────────────────────

@router.get("/api/v1/bwc/events", dependencies=[Depends(require_permission("bwc:read"))])
async def list_events(
    camera_id: Optional[str] = None,
    event_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    filters = "WHERE 1=1"
    params: dict = {}
    if camera_id:
        filters += " AND e.camera_id = :cid"
        params["cid"] = camera_id
    if event_type:
        filters += " AND e.event_type = :etype"
        params["etype"] = event_type

    rows = await db.execute(text(f"""
        SELECT e.*, bc.serial_number, u.full_name AS user_name
        FROM bwc_events e
        JOIN body_cameras bc ON bc.id = e.camera_id
        LEFT JOIN users u ON u.id = e.user_id
        {filters}
        ORDER BY e.occurred_at DESC
        LIMIT 200
    """), params)
    return [dict(r._mapping) for r in rows]
