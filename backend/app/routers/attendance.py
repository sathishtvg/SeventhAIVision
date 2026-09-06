"""Attendance — live monitor and correction requests (ShiftSecure Phase 2A).

Reads shift/break state written by shifts.py's start/end/break endpoints;
owns the correction-request workflow (guard requests a fix, admin/supervisor
approves/rejects). Approve applies the requested times directly onto the
shifts row.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.attendance_status import (
    get_attendance_setting,
    live_status as _live_status,
    monitor_status,
)

router = APIRouter(prefix="/api/v1/attendance", tags=["guard-ops"])

_GUARD_ROLES = {4, 5}


async def _publish_attendance_event(request: Request, tenant_id: str, shift_id: str, status_label: str) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return
    try:
        event = json.dumps({
            "event_type": "attendance_status_changed",
            "tenant_id": tenant_id,
            "payload": {"shift_id": shift_id, "status": status_label},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        })
        await redis.publish(f"tenant_events:{tenant_id}", event)
    except Exception:
        pass


@router.get("/live", dependencies=[Depends(require_permission("attendance:read"))])
async def get_live_attendance(db: AsyncSession = Depends(get_db_with_tenant), site_id: str | None = None):
    """The command office's live board: every guard rostered today, grouped by
    site, with the company-wide counts above them.

    Rostered guards stay on the board for the whole duty period whether or not
    they have checked in — an absence is the single most important thing this
    screen has to show, and a row that only appears once someone turns up can
    never show it.

    Leave is joined in from guard_leave_blocks (the same table the roster
    scheduler treats as authority on availability) so an excused guard reads
    as excused rather than missing.
    """
    site_clause = "AND sh.site_id = CAST(:site_id AS uuid)" if site_id else ""
    params: dict = {"site_id": site_id} if site_id else {}
    grace_minutes = await get_attendance_setting(db, "attendance.late_grace_minutes")

    result = await db.execute(
        text(f"""
            SELECT sh.id, sh.guard_user_id, sh.site_id, sh.scheduled_start, sh.scheduled_end,
                   sh.actual_start, sh.actual_end, sh.status, sh.shift_type,
                   sh.is_late, sh.late_minutes, sh.overtime_minutes, sh.is_within_geofence,
                   sh.check_in_photo_path, sh.check_out_photo_path,
                   sh.check_in_liveness_score, sh.check_out_liveness_score,
                   sh.check_in_is_mock_location, sh.check_out_is_mock_location,
                   EXISTS(SELECT 1 FROM shift_breaks b
                          WHERE b.shift_id = sh.id AND b.break_end IS NULL) AS on_break,
                   u.full_name AS guard_name, u.phone AS guard_phone,
                   u.employment_type, u.designation,
                   -- Permanent photo, shown until the shift's own check-in
                   -- selfie exists. Path only; the board builds the URL.
                   u.profile_photo_path,
                   s.name AS site_name,
                   -- Per-site grace, NULL meaning "use the tenant default".
                   -- Resolved per row below so a board spanning sites with
                   -- different allowances judges each by its own.
                   s.late_grace_minutes AS site_grace_minutes,
                   lv.reason AS leave_reason,
                   (lv.guard_user_id IS NOT NULL) AS on_leave
            FROM shifts sh
            JOIN tenants t ON t.id = sh.tenant_id
            LEFT JOIN users u ON u.id = sh.guard_user_id
            LEFT JOIN sites s ON s.id = sh.site_id
            -- Approved leave covering the tenant-local today. LATERAL keeps it
            -- to one row per shift even where overlapping blocks exist.
            LEFT JOIN LATERAL (
                SELECT gl.guard_user_id, gl.reason
                FROM guard_leave_blocks gl
                WHERE gl.guard_user_id = sh.guard_user_id
                  AND (now() AT TIME ZONE COALESCE(t.timezone, 'UTC'))::date
                      BETWEEN gl.start_date AND gl.end_date
                LIMIT 1
            ) lv ON TRUE
            WHERE (sh.scheduled_start AT TIME ZONE COALESCE(t.timezone, 'UTC'))::date
                = (now() AT TIME ZONE COALESCE(t.timezone, 'UTC'))::date
            {site_clause}
            ORDER BY s.name, sh.scheduled_start
        """),
        params,
    )

    # One instant for the whole refresh: rows judged microseconds apart must
    # not land on opposite sides of a grace boundary.
    now = datetime.now(timezone.utc)

    shifts: list[dict] = []
    sites: dict[str, dict] = {}
    summary = {k: 0 for k in (
        "rostered", "checked_in", "reported", "late", "not_reported",
        "on_leave", "not_yet_on_duty", "on_duty",
    )}

    for r in result.mappings():
        row = dict(r)
        # `is not None` rather than `or`: a site deliberately set to 0 means
        # "no grace at all", which `or` would quietly turn back into the
        # tenant default.
        site_grace = row.pop("site_grace_minutes", None)
        effective_grace = site_grace if site_grace is not None else grace_minutes
        row["effective_grace_minutes"] = effective_grace
        state = monitor_status(row, now, effective_grace)
        row["monitor_status"] = state
        # Kept so anything still reading the old field keeps working.
        row["live_status"] = _live_status(row)

        summary["rostered"] += 1
        if row["actual_start"] is not None:
            summary["reported"] += 1
            if row["actual_end"] is None:
                summary["checked_in"] += 1
        if row["is_late"]:
            summary["late"] += 1
        if state == "not_reported":
            summary["not_reported"] += 1
        if state == "on_leave":
            summary["on_leave"] += 1
        if state == "not_yet_on_duty":
            summary["not_yet_on_duty"] += 1
        if row["status"] == "active":
            summary["on_duty"] += 1

        key = str(row["site_id"]) if row["site_id"] else "unassigned"
        site = sites.setdefault(key, {
            "site_id": row["site_id"],
            "site_name": row["site_name"] or "Unassigned site",
            "counts": {k: 0 for k in ("rostered", "checked_in", "late",
                                      "not_reported", "on_leave", "not_yet_on_duty")},
            "guards": [],
        })
        site["counts"]["rostered"] += 1
        if row["actual_start"] is not None and row["actual_end"] is None:
            site["counts"]["checked_in"] += 1
        if row["is_late"]:
            site["counts"]["late"] += 1
        if state in ("not_reported", "on_leave", "not_yet_on_duty"):
            site["counts"][state] += 1
        site["guards"].append(row)
        shifts.append(row)

    return {
        # Lets the board say when it last heard from the server, so a frozen
        # dashboard is visibly frozen instead of quietly wrong.
        "generated_at": now.isoformat(),
        "grace_minutes": grace_minutes,
        "summary": summary,
        "sites": sorted(sites.values(), key=lambda s: s["site_name"]),
        "shifts": shifts,
    }


@router.get("/guard/{guard_user_id}/recent",
            dependencies=[Depends(require_permission("attendance:read"))])
async def get_guard_recent_attendance(
    guard_user_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    limit: int = 7,
):
    """Recent shifts for one guard — the history panel of the detail popup.

    Excludes today's shift: the popup already shows today in full above this,
    and repeating it as the first history row reads like a duplicate.
    """
    result = await db.execute(
        text("""
            SELECT sh.id, sh.scheduled_start, sh.scheduled_end,
                   sh.actual_start, sh.actual_end, sh.status,
                   sh.is_late, sh.late_minutes, sh.overtime_minutes,
                   s.name AS site_name
            FROM shifts sh
            JOIN tenants t ON t.id = sh.tenant_id
            LEFT JOIN sites s ON s.id = sh.site_id
            WHERE sh.guard_user_id = CAST(:gid AS uuid)
              AND (sh.scheduled_start AT TIME ZONE COALESCE(t.timezone, 'UTC'))::date
                < (now() AT TIME ZONE COALESCE(t.timezone, 'UTC'))::date
            ORDER BY sh.scheduled_start DESC
            LIMIT :limit
        """),
        {"gid": guard_user_id, "limit": min(max(limit, 1), 30)},
    )
    return [dict(r) for r in result.mappings()]


# ── Correction requests ───────────────────────────────────────────────────────

class CorrectionCreate(BaseModel):
    shift_id: str
    requested_check_in: datetime | None = None
    requested_check_out: datetime | None = None
    reason: str


@router.post("/corrections", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("attendance:request"))])
async def request_correction(
    body: CorrectionCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    shift_row = (await db.execute(
        text("SELECT guard_user_id FROM shifts WHERE id = :id"), {"id": body.shift_id}
    )).first()
    if shift_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
    if token.role_id in _GUARD_ROLES and str(shift_row.guard_user_id) != str(token.user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only request corrections for your own shifts")

    result = await db.execute(
        text("""
            INSERT INTO attendance_corrections
                (tenant_id, shift_id, guard_user_id, requested_check_in, requested_check_out, reason)
            VALUES (current_setting('app.current_tenant')::uuid, :sid, :gid, :cin, :cout, :reason)
            RETURNING id, shift_id, status, created_at
        """),
        {
            "sid": body.shift_id, "gid": str(shift_row.guard_user_id),
            "cin": body.requested_check_in, "cout": body.requested_check_out,
            "reason": body.reason,
        },
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@router.get("/corrections", dependencies=[Depends(require_permission("attendance:read"))])
async def list_corrections(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    correction_status: str | None = None,
):
    where = []
    params: dict = {}
    if token.role_id in _GUARD_ROLES:
        where.append("ac.guard_user_id = :uid")
        params["uid"] = token.user_id
    if correction_status:
        where.append("ac.status = :cstatus")
        params["cstatus"] = correction_status
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    result = await db.execute(
        text(f"""
            SELECT ac.id, ac.shift_id, ac.guard_user_id, ac.requested_check_in, ac.requested_check_out,
                   ac.reason, ac.status, ac.reviewed_by_user_id, ac.reviewed_at, ac.review_notes,
                   ac.created_at, u.full_name AS guard_name,
                   sh.scheduled_start, sh.scheduled_end, s.name AS site_name
            FROM attendance_corrections ac
            JOIN shifts sh ON sh.id = ac.shift_id
            JOIN users u ON u.id = ac.guard_user_id
            LEFT JOIN sites s ON s.id = sh.site_id
            {where_clause}
            ORDER BY ac.created_at DESC
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


@router.put("/corrections/{correction_id}/approve", dependencies=[Depends(require_permission("attendance:manage"))])
async def approve_correction(
    correction_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    row = (await db.execute(
        text("""
            UPDATE attendance_corrections
            SET status = 'approved', reviewed_by_user_id = :rid, reviewed_at = now()
            WHERE id = :id AND status = 'pending'
            RETURNING id, shift_id, requested_check_in, requested_check_out
        """),
        {"id": correction_id, "rid": token.user_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Correction not found or already reviewed")

    await db.execute(
        text("""
            UPDATE shifts SET
                actual_start = COALESCE(:cin, actual_start),
                actual_end   = COALESCE(:cout, actual_end)
            WHERE id = :sid
        """),
        {"cin": row.requested_check_in, "cout": row.requested_check_out, "sid": row.shift_id},
    )
    await db.commit()
    await _publish_attendance_event(request, token.tenant_id, str(row.shift_id), "correction_approved")
    return {"id": correction_id, "status": "approved"}


class CorrectionReject(BaseModel):
    review_notes: str | None = None


@router.put("/corrections/{correction_id}/reject", dependencies=[Depends(require_permission("attendance:manage"))])
async def reject_correction(
    correction_id: str,
    body: CorrectionReject,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    row = (await db.execute(
        text("""
            UPDATE attendance_corrections
            SET status = 'rejected', reviewed_by_user_id = :rid, reviewed_at = now(), review_notes = :notes
            WHERE id = :id AND status = 'pending'
            RETURNING id, shift_id
        """),
        {"id": correction_id, "rid": token.user_id, "notes": body.review_notes},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Correction not found or already reviewed")
    await db.commit()
    await _publish_attendance_event(request, token.tenant_id, str(row.shift_id), "correction_rejected")
    return {"id": correction_id, "status": "rejected"}
