"""Overtime requests: a guard asks for extra hours on a shift; a supervisor or admin decides.

Design: docs/plans/2026-10-25-roster-autoclose-overtime-design.md, section 3,
agreed with the owner on 25 October 2026. Asking (attendance:request) is for the
guard on the shift, while it is open or up to 48 hours after it ended; one
request waits for a decision at a time. Deciding is attendance:manage, the pair
the correction requests already use. A pending or approved request moves the
shift's automatic close (services/shift_autoclose.py); an approval or a
rejection of a closed shift recomputes its overtime by the one rule
(services/overtime_rules.py). Every request and decision is audited, the
approvers are told of a request and the guard of the decision, by push, after
the commit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.attendance_status import get_attendance_setting
from app.services.audit import write_audit_log
from app.services.overtime_rules import overtime_minutes, request_hours, unapproved_extra_minutes

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/overtime-requests", tags=["guard-ops"])

#: A guard may still ask this long after the shift ended (forgot to ask while on duty).
ASK_AFTER_END = timedelta(hours=48)
#: Who is told of a new request: the roles that hold attendance:manage by default.
APPROVER_ROLES = (2, 3, 8)
PUSH_TITLE = "Overtime"


class OvertimeAsk(BaseModel):
    shift_id: str
    hours: Decimal = Field(gt=0, le=12, decimal_places=2)
    reason: str = Field(min_length=1, max_length=500)


class Decision(BaseModel):
    note: str | None = Field(default=None, max_length=500)


async def _push(request: Request, tenant_id: str, user_ids: list[str], body: str, data: dict) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis is None or not user_ids:
        return
    try:
        from app.realtime.redis_listener import _send_expo_push  # noqa: PLC0415
        from app.services.response_notify import tokens_of  # noqa: PLC0415

        tokens = await tokens_of(redis, tenant_id, user_ids)
        if tokens:
            await _send_expo_push(tokens, PUSH_TITLE, body, data)
    except Exception:  # noqa: BLE001 - a push that fails changes nothing that was decided
        logger.exception("overtime: push failed for tenant %s", tenant_id)


def _row(r) -> dict:
    out = dict(r)
    out["hours_requested"] = float(out["hours_requested"])
    return out


_SELECT = """
    SELECT r.id, r.shift_id, r.guard_user_id, r.hours_requested, r.reason, r.status, r.decided_by_user_id,
           r.decided_at, r.decision_note, r.created_at, u.full_name AS guard_name, d.full_name AS decided_by_name,
           sh.scheduled_start, sh.scheduled_end, sh.actual_start, sh.actual_end, sh.status AS shift_status,
           sh.auto_closed_at, s.name AS site_name
      FROM overtime_requests r
      JOIN shifts sh ON sh.id = r.shift_id
      LEFT JOIN users u ON u.id = r.guard_user_id
      LEFT JOIN users d ON d.id = r.decided_by_user_id
      LEFT JOIN sites s ON s.id = sh.site_id
"""


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("attendance:request"))])
async def ask(body: OvertimeAsk, request: Request, db: AsyncSession = Depends(get_db_with_tenant),
              token: TokenPayload = Depends(get_token_payload)):
    shift = (await db.execute(text(
        "SELECT id, guard_user_id, status, actual_end, site_id FROM shifts WHERE id = CAST(:id AS uuid)"
    ), {"id": body.shift_id})).mappings().first()
    if shift is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
    if str(shift["guard_user_id"]) != str(token.user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may ask for overtime only on your own shift")
    now = datetime.now(timezone.utc)
    if shift["status"] == "active":
        pass
    elif shift["status"] == "completed" and shift["actual_end"] and now - shift["actual_end"] <= ASK_AFTER_END:
        pass
    else:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Overtime can be asked for while the shift is on, or up to 48 hours after it ended")
    waiting = (await db.execute(text(
        "SELECT 1 FROM overtime_requests WHERE shift_id = CAST(:id AS uuid) AND status = 'pending'"
    ), {"id": body.shift_id})).first()
    if waiting is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A request on this shift is already waiting for a decision")
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Say why the overtime is needed")
    row = (await db.execute(text("""
        INSERT INTO overtime_requests (tenant_id, shift_id, guard_user_id, hours_requested, reason)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:s AS uuid), CAST(:g AS uuid), :h, :r)
        RETURNING id
    """), {"s": body.shift_id, "g": token.user_id, "h": body.hours, "r": reason})).first()
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="overtime.requested", resource_type="shift", resource_id=body.shift_id,
                          detail={"hours": float(body.hours), "reason": reason})
    approvers = [str(r[0]) for r in (await db.execute(text(
        "SELECT id FROM users WHERE is_active AND role_id = ANY(:roles)"), {"roles": list(APPROVER_ROLES)})).all()]
    me = (await db.execute(text("SELECT full_name, email FROM users WHERE id = CAST(:u AS uuid)"),
                           {"u": token.user_id})).first()
    created = _row((await db.execute(text(_SELECT + " WHERE r.id = :id"), {"id": row.id})).mappings().first())
    await db.commit()
    who = (me.full_name or me.email) if me else "A guard"
    await _push(request, str(token.tenant_id), approvers,
                f"{who} asks for {float(body.hours):g} h overtime: {reason[:80]}",
                {"type": "overtime_request", "request_id": str(row.id)})
    return created


@router.get("", dependencies=[Depends(require_permission("attendance:read"))])
async def list_requests(request_status: str | None = None, shift_id: str | None = None,
                        db: AsyncSession = Depends(get_db_with_tenant),
                        token: TokenPayload = Depends(get_token_payload)):
    """Guards see their own; whoever may decide sees everyone's."""
    manager = (await db.execute(text(
        "SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
        "WHERE rp.role_id = :r AND p.code = 'attendance:manage'"), {"r": token.role_id})).first() is not None
    where, params = [], {}
    if not manager:
        where.append("r.guard_user_id = CAST(:me AS uuid)")
        params["me"] = token.user_id
    if request_status:
        where.append("r.status = :st")
        params["st"] = request_status
    if shift_id:
        where.append("r.shift_id = CAST(:sid AS uuid)")
        params["sid"] = shift_id
    sql = _SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY r.created_at DESC LIMIT 200"
    return [_row(r) for r in (await db.execute(text(sql), params)).mappings().all()]


async def _decide(request_id: str, approve: bool, note: str | None, request: Request, db: AsyncSession,
                  token: TokenPayload) -> dict:
    if not approve and not (note and note.strip()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Say why the request is rejected")
    row = (await db.execute(text("""
        UPDATE overtime_requests SET status = :st, decided_by_user_id = CAST(:u AS uuid), decided_at = now(),
               decision_note = :note
         WHERE id = CAST(:id AS uuid) AND status = 'pending'
        RETURNING id, shift_id, guard_user_id, hours_requested
    """), {"st": "approved" if approve else "rejected", "u": token.user_id, "note": (note or "").strip() or None,
           "id": request_id})).mappings().first()
    if row is None:
        exists = (await db.execute(text("SELECT status FROM overtime_requests WHERE id = CAST(:id AS uuid)"),
                                   {"id": request_id})).first()
        raise HTTPException(status.HTTP_404_NOT_FOUND if exists is None else status.HTTP_409_CONFLICT,
                            "No such request" if exists is None else f"This request was already {exists[0]}")
    shift = (await db.execute(text(
        "SELECT status, scheduled_end, actual_end, overtime_needs_approval FROM shifts WHERE id = :id"
    ), {"id": row["shift_id"]})).mappings().first()
    paid = None
    if shift and shift["status"] == "completed":
        # A closed shift's overtime follows the decision at once, by the one rule.
        _, approved_hours = await request_hours(db, str(row["shift_id"]))
        threshold = await get_attendance_setting(db, "attendance.overtime_threshold_minutes")
        paid = overtime_minutes(needs_approval=shift["overtime_needs_approval"], scheduled_end=shift["scheduled_end"],
                                actual_end=shift["actual_end"], approved_minutes=approved_hours * 60,
                                threshold_minutes=threshold)
        await db.execute(text("UPDATE shifts SET overtime_minutes = :ot WHERE id = :id"),
                         {"ot": paid, "id": row["shift_id"]})
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="overtime.approved" if approve else "overtime.rejected", resource_type="shift",
                          resource_id=str(row["shift_id"]),
                          detail={"request_id": str(row["id"]), "hours": float(row["hours_requested"]),
                                  "note": note, "overtime_minutes_now": paid})
    decided = _row((await db.execute(text(_SELECT + " WHERE r.id = :id"), {"id": row["id"]})).mappings().first())
    await db.commit()
    words = (f"Your {float(row['hours_requested']):g} h overtime was approved."
             if approve else f"Your {float(row['hours_requested']):g} h overtime was not approved: {note.strip()}")
    await _push(request, str(token.tenant_id), [str(row["guard_user_id"])], words,
                {"type": "overtime_decision", "request_id": str(row["id"])})
    return decided


@router.post("/{request_id}/approve", dependencies=[Depends(require_permission("attendance:manage"))])
async def approve(request_id: str, request: Request, body: Decision | None = None,
                  db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload)):
    return await _decide(request_id, True, body.note if body else None, request, db, token)


@router.post("/{request_id}/reject", dependencies=[Depends(require_permission("attendance:manage"))])
async def reject(request_id: str, body: Decision, request: Request,
                 db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload)):
    return await _decide(request_id, False, body.note, request, db, token)


@router.get("/unapproved-extra", dependencies=[Depends(require_permission("attendance:manage"))])
async def unapproved_extra(days: int = 14, db: AsyncSession = Depends(get_db_with_tenant)):
    """Closed shifts on the approval rule with time worked past the planned end that no approval covers -
    shown for review, not paid (section 5)."""
    rows = (await db.execute(text("""
        SELECT sh.id AS shift_id, sh.guard_user_id, u.full_name AS guard_name, s.name AS site_name,
               sh.scheduled_end, sh.actual_end, sh.auto_closed_at, sh.overtime_needs_approval,
               COALESCE((SELECT SUM(r.hours_requested) FROM overtime_requests r
                          WHERE r.shift_id = sh.id AND r.status = 'approved'), 0) AS approved_hours
          FROM shifts sh LEFT JOIN users u ON u.id = sh.guard_user_id LEFT JOIN sites s ON s.id = sh.site_id
         WHERE sh.status = 'completed' AND sh.overtime_needs_approval AND sh.actual_end > sh.scheduled_end
           AND sh.actual_end >= now() - make_interval(days => :d)
         ORDER BY sh.actual_end DESC LIMIT 200
    """), {"d": max(1, min(days, 90))})).mappings().all()
    out = []
    for r in rows:
        extra = unapproved_extra_minutes(needs_approval=True, scheduled_end=r["scheduled_end"],
                                         actual_end=r["actual_end"], approved_minutes=float(r["approved_hours"]) * 60)
        if extra > 0:
            out.append({**{k: r[k] for k in ("shift_id", "guard_user_id", "guard_name", "site_name", "scheduled_end",
                                              "actual_end", "auto_closed_at")},
                        "approved_hours": float(r["approved_hours"]), "unapproved_extra_minutes": extra})
    return out
