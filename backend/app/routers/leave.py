"""Leave Management — types, guard-submitted requests, approval workflow,
and per-guard balances (ShiftSecure Phase 4).

Approval syncs a row into guard_leave_blocks so roster_autoschedule.py's
existing is_guard_on_leave read contract picks it up with zero scheduler
changes; cancellation of an approved request removes that synced row.
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.uploads import MAX_DOCUMENT_UPLOAD_BYTES, read_upload_limited
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.leave import compute_days_count

router = APIRouter(prefix="/api/v1/leave", tags=["guard-ops"])

_GUARD_ROLES = {4, 5}


async def _publish_leave_event(request: Request, tenant_id: str, request_id: str, status_label: str) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return
    try:
        event = json.dumps({
            "event_type": "leave_status_changed",
            "tenant_id": tenant_id,
            "payload": {"request_id": request_id, "status": status_label},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        })
        await redis.publish(f"tenant_events:{tenant_id}", event)
    except Exception:
        pass


# ── Leave types ────────────────────────────────────────────────────────────────

class LeaveTypeCreate(BaseModel):
    name: str
    default_annual_days: int = 0
    requires_document: bool = False


class LeaveTypeUpdate(BaseModel):
    name: str | None = None
    default_annual_days: int | None = None
    requires_document: bool | None = None
    is_active: bool | None = None


@router.get("/types", dependencies=[Depends(require_permission("leave:read"))])
async def list_leave_types(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("""
            SELECT id, name, default_annual_days, requires_document, is_active, created_at
            FROM leave_types WHERE is_active = TRUE ORDER BY name
        """)
    )
    return [dict(r._mapping) for r in result]


@router.post("/types", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("leave:manage"))])
async def create_leave_type(body: LeaveTypeCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("""
            INSERT INTO leave_types (tenant_id, name, default_annual_days, requires_document)
            VALUES (current_setting('app.current_tenant')::uuid, :name, :days, :doc)
            RETURNING id, name, default_annual_days, requires_document, is_active, created_at
        """),
        {"name": body.name, "days": body.default_annual_days, "doc": body.requires_document},
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@router.put("/types/{type_id}", dependencies=[Depends(require_permission("leave:manage"))])
async def update_leave_type(type_id: str, body: LeaveTypeUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = type_id
    result = await db.execute(
        text(f"UPDATE leave_types SET {set_clause} WHERE id = CAST(:id AS uuid) "
             f"RETURNING id, name, default_annual_days, requires_document, is_active, created_at"),
        updates,
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leave type not found")
    await db.commit()
    return dict(row._mapping)


@router.delete("/types/{type_id}", dependencies=[Depends(require_permission("leave:manage"))])
async def deactivate_leave_type(type_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("UPDATE leave_types SET is_active = FALSE WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": type_id},
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leave type not found")
    await db.commit()
    return {"id": type_id, "is_active": False}


# ── Requests ───────────────────────────────────────────────────────────────────

async def _guard_balance(db: AsyncSession, guard_user_id: str, leave_type_id: str, year: int) -> dict:
    row = (await db.execute(
        text("""
            SELECT
                COALESCE(
                    (SELECT entitled_days FROM leave_balances
                     WHERE guard_user_id = :gid AND leave_type_id = :ltid AND year = :yr),
                    (SELECT default_annual_days FROM leave_types WHERE id = :ltid)
                ) AS entitled_days,
                COALESCE(
                    (SELECT SUM(days_count) FROM leave_requests
                     WHERE guard_user_id = :gid AND leave_type_id = :ltid AND status = 'approved'
                       AND EXTRACT(YEAR FROM start_date) = :yr),
                    0
                ) AS used_days
        """),
        {"gid": guard_user_id, "ltid": leave_type_id, "yr": year},
    )).first()
    entitled = int(row.entitled_days or 0)
    used = int(row.used_days or 0)
    return {"entitled_days": entitled, "used_days": used, "remaining_days": entitled - used}


@router.get("/requests", dependencies=[Depends(require_permission("leave:read"))])
async def list_leave_requests(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    guard_user_id: str | None = None,
    leave_type_id: str | None = None,
    request_status: str | None = None,
):
    where = []
    params: dict = {}
    if token.role_id in _GUARD_ROLES:
        where.append("lr.guard_user_id = :uid")
        params["uid"] = token.user_id
    elif guard_user_id:
        where.append("lr.guard_user_id = CAST(:gid AS uuid)")
        params["gid"] = guard_user_id
    if leave_type_id:
        where.append("lr.leave_type_id = CAST(:ltid AS uuid)")
        params["ltid"] = leave_type_id
    if request_status:
        where.append("lr.status = :rstatus")
        params["rstatus"] = request_status
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(
        text(f"""
            SELECT lr.id, lr.guard_user_id, lr.leave_type_id, lr.start_date, lr.end_date,
                   lr.days_count, lr.reason, lr.document_path, lr.status,
                   lr.reviewed_by_user_id, lr.reviewed_at, lr.review_notes, lr.created_at,
                   u.full_name AS guard_name, lt.name AS leave_type_name
            FROM leave_requests lr
            JOIN users u ON u.id = lr.guard_user_id
            JOIN leave_types lt ON lt.id = lr.leave_type_id
            {where_clause}
            ORDER BY lr.created_at DESC
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


class LeaveRequestCreate(BaseModel):
    guard_user_id: str
    leave_type_id: str
    start_date: date
    end_date: date
    reason: str | None = None


@router.post("/requests", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("leave:request"))])
async def create_leave_request(
    body: LeaveRequestCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if token.role_id in _GUARD_ROLES and body.guard_user_id != token.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only request leave for your own account")
    if body.end_date < body.start_date:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "end_date must not be before start_date")

    overlap = (await db.execute(
        text("""
            SELECT 1 FROM leave_requests
            WHERE guard_user_id = CAST(:gid AS uuid)
              AND status NOT IN ('rejected', 'cancelled')
              AND start_date <= :ed AND end_date >= :sd
        """),
        {"gid": body.guard_user_id, "sd": body.start_date, "ed": body.end_date},
    )).first()
    if overlap is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An overlapping leave request already exists")

    days_count = compute_days_count(body.start_date, body.end_date)
    result = await db.execute(
        text("""
            INSERT INTO leave_requests
                (tenant_id, guard_user_id, leave_type_id, start_date, end_date, days_count, reason, created_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:gid AS uuid), CAST(:ltid AS uuid),
                    :sd, :ed, :days, :reason, CAST(:uid AS uuid))
            RETURNING id, guard_user_id, leave_type_id, start_date, end_date, days_count, reason, status, created_at
        """),
        {
            "gid": body.guard_user_id, "ltid": body.leave_type_id, "sd": body.start_date, "ed": body.end_date,
            "days": days_count, "reason": body.reason, "uid": token.user_id,
        },
    )
    row = result.first()
    # commit clears the transaction-scoped app.current_tenant GUC (SET LOCAL
    # semantics) — capture it, commit, restore it so _guard_balance's RLS-scoped
    # queries below still see this tenant.
    tid = (await db.execute(text("SELECT current_setting('app.current_tenant', true)"))).scalar()
    await db.commit()
    if tid:
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tid})
    result_dict = dict(row._mapping)
    result_dict["balance"] = await _guard_balance(db, body.guard_user_id, body.leave_type_id, body.start_date.year)
    return result_dict


@router.post("/requests/{request_id}/document", dependencies=[Depends(require_permission("leave:request"))])
async def upload_leave_document(
    request_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    row = (await db.execute(
        text("SELECT guard_user_id FROM leave_requests WHERE id = CAST(:id AS uuid)"), {"id": request_id}
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leave request not found")
    if token.role_id in _GUARD_ROLES and str(row.guard_user_id) != str(token.user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only attach documents to your own requests")

    ext = Path(file.filename).suffix or ".bin" if file.filename else ".bin"
    relative_path = f"{token.tenant_id}/{row.guard_user_id}/leave/{request_id}{ext}"
    dest = Path(settings.EMPLOYEE_DOCS_ROOT) / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await read_upload_limited(file, MAX_DOCUMENT_UPLOAD_BYTES))

    await db.execute(
        text("UPDATE leave_requests SET document_path = :path WHERE id = CAST(:id AS uuid)"),
        {"path": relative_path, "id": request_id},
    )
    await db.commit()
    return {"id": request_id, "document_path": relative_path}


@router.put("/requests/{request_id}/approve", dependencies=[Depends(require_permission("leave:manage"))])
async def approve_leave_request(
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    row = (await db.execute(
        text("""
            UPDATE leave_requests
            SET status = 'approved', reviewed_by_user_id = :rid, reviewed_at = now()
            WHERE id = CAST(:id AS uuid) AND status = 'pending'
            RETURNING id, guard_user_id, start_date, end_date, reason
        """),
        {"id": request_id, "rid": token.user_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leave request not found or already reviewed")

    await db.execute(
        text("""
            INSERT INTO guard_leave_blocks
                (tenant_id, guard_user_id, start_date, end_date, reason, created_by_user_id, leave_request_id)
            VALUES (current_setting('app.current_tenant')::uuid, :gid, :sd, :ed, :reason, :uid, CAST(:rid AS uuid))
        """),
        {
            "gid": row.guard_user_id, "sd": row.start_date, "ed": row.end_date,
            "reason": row.reason, "uid": token.user_id, "rid": request_id,
        },
    )

    affected = await db.execute(
        text("""
            SELECT sh.id, sh.scheduled_start, sh.scheduled_end, s.name AS site_name
            FROM shifts sh LEFT JOIN sites s ON s.id = sh.site_id
            WHERE sh.guard_user_id = :gid
              AND sh.status = 'scheduled'
              AND sh.scheduled_start::date >= :sd AND sh.scheduled_start::date <= :ed
            ORDER BY sh.scheduled_start
        """),
        {"gid": row.guard_user_id, "sd": row.start_date, "ed": row.end_date},
    )
    affected_shifts = [dict(r._mapping) for r in affected]

    await db.commit()
    await _publish_leave_event(request, token.tenant_id, request_id, "approved")
    return {"id": request_id, "status": "approved", "affected_shifts": affected_shifts}


class LeaveReview(BaseModel):
    review_notes: str | None = None


@router.put("/requests/{request_id}/reject", dependencies=[Depends(require_permission("leave:manage"))])
async def reject_leave_request(
    request_id: str,
    body: LeaveReview,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    row = (await db.execute(
        text("""
            UPDATE leave_requests
            SET status = 'rejected', reviewed_by_user_id = :rid, reviewed_at = now(), review_notes = :notes
            WHERE id = CAST(:id AS uuid) AND status = 'pending'
            RETURNING id
        """),
        {"id": request_id, "rid": token.user_id, "notes": body.review_notes},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leave request not found or already reviewed")
    await db.commit()
    await _publish_leave_event(request, token.tenant_id, request_id, "rejected")
    return {"id": request_id, "status": "rejected"}


@router.put("/requests/{request_id}/cancel", dependencies=[Depends(require_permission("leave:request"))])
async def cancel_leave_request(
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    existing = (await db.execute(
        text("SELECT guard_user_id, status FROM leave_requests WHERE id = CAST(:id AS uuid)"), {"id": request_id}
    )).first()
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leave request not found")
    if token.role_id in _GUARD_ROLES and str(existing.guard_user_id) != str(token.user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only cancel your own requests")
    if existing.status not in ("pending", "approved"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending or approved requests can be cancelled")

    was_approved = existing.status == "approved"
    await db.execute(
        text("UPDATE leave_requests SET status = 'cancelled' WHERE id = CAST(:id AS uuid)"), {"id": request_id}
    )
    if was_approved:
        await db.execute(
            text("DELETE FROM guard_leave_blocks WHERE leave_request_id = CAST(:id AS uuid)"), {"id": request_id}
        )
    await db.commit()
    await _publish_leave_event(request, token.tenant_id, request_id, "cancelled")
    return {"id": request_id, "status": "cancelled"}


# ── Balances ───────────────────────────────────────────────────────────────────

@router.get("/balances", dependencies=[Depends(require_permission("leave:read"))])
async def get_leave_balances(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    guard_user_id: str | None = None,
    year: int | None = None,
):
    target_guard = token.user_id if token.role_id in _GUARD_ROLES else (guard_user_id or token.user_id)
    yr = year or datetime.now(timezone.utc).year

    types = await db.execute(
        text("SELECT id, name FROM leave_types WHERE is_active = TRUE ORDER BY name")
    )
    out = []
    for t in types:
        bal = await _guard_balance(db, target_guard, str(t.id), yr)
        out.append({"leave_type_id": str(t.id), "name": t.name, "year": yr, **bal})
    return out


class LeaveBalanceUpsert(BaseModel):
    guard_user_id: str
    leave_type_id: str
    year: int
    entitled_days: int


@router.put("/balances", dependencies=[Depends(require_permission("leave:manage"))])
async def set_leave_balance(body: LeaveBalanceUpsert, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("""
            INSERT INTO leave_balances (tenant_id, guard_user_id, leave_type_id, year, entitled_days)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:gid AS uuid), CAST(:ltid AS uuid), :yr, :days)
            ON CONFLICT (guard_user_id, leave_type_id, year)
            DO UPDATE SET entitled_days = :days, updated_at = now()
            RETURNING id, guard_user_id, leave_type_id, year, entitled_days
        """),
        {"gid": body.guard_user_id, "ltid": body.leave_type_id, "yr": body.year, "days": body.entitled_days},
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)
