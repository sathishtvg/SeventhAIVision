"""Contractor & Delivery Management router — /api/v1/contractors, /work-permits, /deliveries"""
from datetime import datetime
from typing import Optional
from uuid import UUID

import json


def _parse_dt(s):
    if s is None:
        return None
    if isinstance(s, datetime):
        return s
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(tags=["contractors"])

VALID_VETTING = {"pending", "approved", "suspended", "rejected"}
VALID_PERMIT_STATUS = {"pending", "approved", "rejected", "active", "completed", "cancelled", "expired"}
VALID_DELIVERY_STATUS = {"pending", "received", "collected", "rejected", "returned"}


# ── contractors ───────────────────────────────────────────────────────────────

@router.get("/api/v1/contractors", dependencies=[Depends(require_permission("contractor:read"))])
async def list_contractors(
    vetting_status: Optional[str] = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    filters = "WHERE c.is_active = TRUE"
    params: dict = {}
    if vetting_status:
        filters += " AND c.vetting_status = :vs"
        params["vs"] = vetting_status

    rows = await db.execute(text(f"""
        SELECT c.*,
               u.full_name AS vetted_by_name,
               (SELECT COUNT(*) FROM work_permits wp WHERE wp.contractor_id = c.id AND wp.status IN ('pending','approved','active')) AS active_permits,
               (SELECT COUNT(*) FROM contractor_accreditations ca WHERE ca.contractor_id = c.id) AS accreditation_count
        FROM contractors c
        LEFT JOIN users u ON u.id = c.vetted_by_user_id
        {filters}
        ORDER BY c.company_name
    """), params)
    return [dict(r._mapping) for r in rows]


@router.post("/api/v1/contractors", dependencies=[Depends(require_permission("contractor:write"))])
async def create_contractor(
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    name = (body.get("company_name") or "").strip()
    if not name:
        raise HTTPException(400, "company_name is required")

    row = await db.execute(text("""
        INSERT INTO contractors (tenant_id, company_name, registration_number, contact_name,
                                 contact_phone, contact_email, address, specialization)
        VALUES (:tid, :name, :reg, :cname, :cphone, :cemail, :addr, :spec)
        RETURNING *
    """), {
        "tid":    token.tenant_id,
        "name":   name,
        "reg":    body.get("registration_number"),
        "cname":  body.get("contact_name"),
        "cphone": body.get("contact_phone"),
        "cemail": body.get("contact_email"),
        "addr":   body.get("address"),
        "spec":   body.get("specialization"),
    })
    await db.commit()
    return dict(row.mappings().one())


@router.get("/api/v1/contractors/{contractor_id}", dependencies=[Depends(require_permission("contractor:read"))])
async def get_contractor(contractor_id: UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    row = await db.execute(text("""
        SELECT c.*, u.full_name AS vetted_by_name
        FROM contractors c
        LEFT JOIN users u ON u.id = c.vetted_by_user_id
        WHERE c.id = :id AND c.is_active = TRUE
    """), {"id": str(contractor_id)})
    contractor = row.mappings().first()
    if not contractor:
        raise HTTPException(404, "Contractor not found")

    acc_rows = await db.execute(text("""
        SELECT * FROM contractor_accreditations WHERE contractor_id = :id ORDER BY created_at DESC
    """), {"id": str(contractor_id)})
    accreditations = [dict(r._mapping) for r in acc_rows]

    permit_rows = await db.execute(text("""
        SELECT wp.*, s.name AS site_name
        FROM work_permits wp
        LEFT JOIN sites s ON s.id = wp.site_id
        WHERE wp.contractor_id = :id
        ORDER BY wp.created_at DESC
        LIMIT 10
    """), {"id": str(contractor_id)})
    permits = [dict(r._mapping) for r in permit_rows]

    return {**dict(contractor), "accreditations": accreditations, "recent_permits": permits}


@router.put("/api/v1/contractors/{contractor_id}", dependencies=[Depends(require_permission("contractor:write"))])
async def update_contractor(contractor_id: UUID, body: dict, db: AsyncSession = Depends(get_db_with_tenant)):
    allowed = {"company_name", "registration_number", "contact_name", "contact_phone",
               "contact_email", "address", "specialization", "is_active"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = str(contractor_id)
    await db.execute(text(f"UPDATE contractors SET {set_clause}, updated_at = now() WHERE id = :id"), updates)
    await db.commit()
    return {"ok": True}


@router.put("/api/v1/contractors/{contractor_id}/vet", dependencies=[Depends(require_permission("contractor:approve"))])
async def vet_contractor(
    contractor_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    status = body.get("vetting_status")
    if status not in VALID_VETTING:
        raise HTTPException(400, f"vetting_status must be one of {sorted(VALID_VETTING)}")
    await db.execute(text("""
        UPDATE contractors
        SET vetting_status = :status, vetting_notes = :notes,
            vetted_by_user_id = :uid, vetted_at = now(), updated_at = now()
        WHERE id = :id
    """), {"status": status, "notes": body.get("vetting_notes"), "uid": token.user_id, "id": str(contractor_id)})
    await db.commit()
    return {"ok": True}


@router.post("/api/v1/contractors/{contractor_id}/accreditations",
             dependencies=[Depends(require_permission("contractor:write"))])
async def add_accreditation(
    contractor_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    doc_type = (body.get("document_type") or "").strip()
    if not doc_type:
        raise HTTPException(400, "document_type is required")

    row = await db.execute(text("""
        INSERT INTO contractor_accreditations
            (tenant_id, contractor_id, document_type, document_number, issued_by, issued_at, expires_at, notes)
        VALUES (:tid, :cid, :dtype, :dnum, :issued_by, :issued_at, :expires_at, :notes)
        RETURNING *
    """), {
        "tid":        token.tenant_id,
        "cid":        str(contractor_id),
        "dtype":      doc_type,
        "dnum":       body.get("document_number"),
        "issued_by":  body.get("issued_by"),
        "issued_at":  body.get("issued_at"),
        "expires_at": body.get("expires_at"),
        "notes":      body.get("notes"),
    })
    await db.commit()
    return dict(row.mappings().one())


# ── work permits ──────────────────────────────────────────────────────────────

@router.get("/api/v1/work-permits", dependencies=[Depends(require_permission("contractor:read"))])
async def list_work_permits(
    status: Optional[str] = None,
    contractor_id: Optional[str] = None,
    site_id: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    filters = "WHERE 1=1"
    params: dict = {"limit": limit + 1, "offset": offset}
    if status:
        filters += " AND wp.status = :status"
        params["status"] = status
    if contractor_id:
        filters += " AND wp.contractor_id = :cid"
        params["cid"] = contractor_id
    if site_id:
        filters += " AND wp.site_id = :sid"
        params["sid"] = site_id

    rows_raw = await db.execute(text(f"""
        SELECT wp.*, c.company_name, s.name AS site_name,
               u.full_name AS approved_by_name
        FROM work_permits wp
        JOIN contractors c ON c.id = wp.contractor_id
        LEFT JOIN sites s ON s.id = wp.site_id
        LEFT JOIN users u ON u.id = wp.approved_by_user_id
        {filters}
        ORDER BY wp.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params)
    rows = [dict(r._mapping) for r in rows_raw]
    return rows[:limit]


@router.post("/api/v1/work-permits", dependencies=[Depends(require_permission("contractor:write"))])
async def create_work_permit(
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if not body.get("contractor_id"):
        raise HTTPException(400, "contractor_id is required")
    work_description = body.get("work_description") or body.get("title")
    if not work_description:
        raise HTTPException(400, "work_description is required")
    start_at = body.get("start_at") or body.get("planned_start")
    end_at = body.get("end_at") or body.get("planned_end")
    if not start_at or not end_at:
        raise HTTPException(400, "start_at and end_at are required")

    # verify contractor is approved
    c_row = await db.execute(text(
        "SELECT vetting_status FROM contractors WHERE id = :id"
    ), {"id": body["contractor_id"]})
    contractor = c_row.mappings().first()
    if not contractor:
        raise HTTPException(404, "Contractor not found")
    if contractor["vetting_status"] != "approved":
        raise HTTPException(422, "Contractor must be vetted/approved before a work permit can be created")

    import uuid as _uuid
    permit_num = f"WP-{str(_uuid.uuid4())[:8].upper()}"

    row = await db.execute(text("""
        INSERT INTO work_permits (tenant_id, contractor_id, site_id, permit_number,
                                  work_description, work_type, requested_by_name,
                                  requested_by_email, workers_count, vehicles_count,
                                  start_at, end_at)
        VALUES (:tid, :cid, :sid, :pnum, :desc, :wtype, :rname, :remail, :wcount, :vcount, :start, :end)
        RETURNING *
    """), {
        "tid":    token.tenant_id,
        "cid":    body["contractor_id"],
        "sid":    body.get("site_id"),
        "pnum":   permit_num,
        "desc":   work_description,
        "wtype":  body.get("work_type"),
        "rname":  body.get("requested_by_name"),
        "remail": body.get("requested_by_email"),
        "wcount": body.get("workers_count", 1),
        "vcount": body.get("vehicles_count", 0),
        "start":  _parse_dt(start_at),
        "end":    _parse_dt(end_at),
    })
    await db.commit()
    return dict(row.mappings().one())


@router.put("/api/v1/work-permits/{permit_id}/approve",
            dependencies=[Depends(require_permission("contractor:approve"))])
async def approve_permit(
    request: Request,
    permit_id: UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await db.execute(text("""
        UPDATE work_permits
        SET status = 'approved', approved_by_user_id = :uid, approved_at = now(), updated_at = now()
        WHERE id = :id AND status = 'pending'
    """), {"uid": token.user_id, "id": str(permit_id)})
    await db.commit()
    redis = getattr(request.app.state, "redis", None)
    if redis:
        from datetime import datetime, timezone
        await redis.publish(f"tenant_events:{token.tenant_id}", json.dumps({
            "event_type": "permit_status_changed",
            "tenant_id": token.tenant_id,
            "payload": {"permit_id": str(permit_id), "status": "approved"},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }))
    return {"ok": True}


@router.put("/api/v1/work-permits/{permit_id}/reject",
            dependencies=[Depends(require_permission("contractor:approve"))])
async def reject_permit(
    request: Request,
    permit_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await db.execute(text("""
        UPDATE work_permits
        SET status = 'rejected', rejection_reason = :reason,
            approved_by_user_id = :uid, approved_at = now(), updated_at = now()
        WHERE id = :id AND status = 'pending'
    """), {"reason": body.get("reason", ""), "uid": token.user_id, "id": str(permit_id)})
    await db.commit()
    redis = getattr(request.app.state, "redis", None)
    if redis:
        from datetime import datetime, timezone
        await redis.publish(f"tenant_events:{token.tenant_id}", json.dumps({
            "event_type": "permit_status_changed",
            "tenant_id": token.tenant_id,
            "payload": {"permit_id": str(permit_id), "status": "rejected"},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }))
    return {"ok": True}


@router.put("/api/v1/work-permits/{permit_id}/complete",
            dependencies=[Depends(require_permission("contractor:write"))])
async def complete_permit(permit_id: UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text("""
        UPDATE work_permits SET status = 'completed', updated_at = now()
        WHERE id = :id AND status IN ('approved', 'active')
    """), {"id": str(permit_id)})
    await db.commit()
    return {"ok": True}


@router.put("/api/v1/work-permits/{permit_id}/safety-briefing",
            dependencies=[Depends(require_permission("contractor:write"))])
async def mark_safety_briefing(permit_id: UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text("""
        UPDATE work_permits SET safety_briefing_done = TRUE, updated_at = now()
        WHERE id = :id
    """), {"id": str(permit_id)})
    await db.commit()
    return {"ok": True}


# ── deliveries ────────────────────────────────────────────────────────────────

@router.get("/api/v1/deliveries", dependencies=[Depends(require_permission("contractor:read"))])
async def list_deliveries(
    status: Optional[str] = None,
    site_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    filters = "WHERE 1=1"
    params: dict = {}
    if status:
        filters += " AND d.status = :status"
        params["status"] = status
    if site_id:
        filters += " AND d.site_id = :sid"
        params["sid"] = site_id

    rows = await db.execute(text(f"""
        SELECT d.*, s.name AS site_name, u.full_name AS received_by_name
        FROM deliveries d
        LEFT JOIN sites s ON s.id = d.site_id
        LEFT JOIN users u ON u.id = d.received_by_user_id
        {filters}
        ORDER BY d.created_at DESC
        LIMIT 100
    """), params)
    return [dict(r._mapping) for r in rows]


@router.post("/api/v1/deliveries", dependencies=[Depends(require_permission("contractor:write"))])
async def create_delivery(
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    row = await db.execute(text("""
        INSERT INTO deliveries (tenant_id, site_id, tracking_number, carrier,
                                sender_name, sender_company, recipient_name,
                                recipient_department, description, expected_at, notes)
        VALUES (:tid, :sid, :track, :carrier, :sname, :sco, :rname, :rdept, :desc, :exp, :notes)
        RETURNING *
    """), {
        "tid":     token.tenant_id,
        "sid":     body.get("site_id"),
        "track":   body.get("tracking_number"),
        "carrier": body.get("carrier"),
        "sname":   body.get("sender_name"),
        "sco":     body.get("sender_company"),
        "rname":   body.get("recipient_name") or "Unknown",
        "rdept":   body.get("recipient_department"),
        "desc":    body.get("description"),
        "exp":     _parse_dt(body.get("expected_at")),
        "notes":   body.get("notes"),
    })
    await db.commit()
    return dict(row.mappings().one())


@router.put("/api/v1/deliveries/{delivery_id}/receive",
            dependencies=[Depends(require_permission("contractor:write"))])
async def receive_delivery(
    delivery_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await db.execute(text("""
        UPDATE deliveries
        SET status = 'received', received_at = now(),
            received_by_user_id = :uid, notes = COALESCE(:notes, notes), updated_at = now()
        WHERE id = :id AND status = 'pending'
    """), {"uid": token.user_id, "notes": body.get("notes"), "id": str(delivery_id)})
    await db.commit()
    return {"ok": True}


@router.put("/api/v1/deliveries/{delivery_id}/collect",
            dependencies=[Depends(require_permission("contractor:write"))])
async def collect_delivery(delivery_id: UUID, body: dict, db: AsyncSession = Depends(get_db_with_tenant)):
    collected_by = (body.get("collected_by_name") or body.get("collected_by") or "").strip()
    if not collected_by:
        raise HTTPException(400, "collected_by_name is required")
    await db.execute(text("""
        UPDATE deliveries
        SET status = 'collected', collected_at = now(), collected_by_name = :name, updated_at = now()
        WHERE id = :id AND status = 'received'
    """), {"name": collected_by, "id": str(delivery_id)})
    await db.commit()
    return {"ok": True}


@router.put("/api/v1/deliveries/{delivery_id}/reject",
            dependencies=[Depends(require_permission("contractor:write"))])
async def reject_delivery(delivery_id: UUID, body: dict, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text("""
        UPDATE deliveries
        SET status = 'rejected', rejection_reason = :reason, updated_at = now()
        WHERE id = :id AND status = 'pending'
    """), {"reason": body.get("reason", ""), "id": str(delivery_id)})
    await db.commit()
    return {"ok": True}


@router.get("/api/v1/contractors-dashboard", dependencies=[Depends(require_permission("contractor:read"))])
async def contractors_dashboard(db: AsyncSession = Depends(get_db_with_tenant)):
    summary = (await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM contractors WHERE is_active = TRUE)                                          AS total_contractors,
            (SELECT COUNT(*) FROM contractors WHERE vetting_status = 'pending' AND is_active = TRUE)          AS pending_vetting,
            (SELECT COUNT(*) FROM contractors WHERE vetting_status = 'approved' AND is_active = TRUE)         AS approved_contractors,
            (SELECT COUNT(*) FROM work_permits WHERE status = 'pending')                                       AS pending_permits,
            (SELECT COUNT(*) FROM work_permits WHERE status IN ('approved','active'))                          AS active_permits,
            (SELECT COUNT(*) FROM deliveries WHERE status = 'pending')                                         AS pending_deliveries,
            (SELECT COUNT(*) FROM deliveries WHERE status = 'received')                                        AS received_deliveries
    """))).mappings().one()
    return dict(summary)
