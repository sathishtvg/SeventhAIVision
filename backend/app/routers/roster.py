"""Roster rebuild — AI auto-scheduler draft/publish workflow, leave blocks,
and shift preferences (ShiftSecure Phase 2B).

Draft output lives in roster_batches/roster_draft_shifts, structurally
separate from the live `shifts` table — every existing shift-reading
endpoint (list_shifts, roster_coverage, attendance/live, mobile "My
Shifts") is completely unaffected by drafts; publishing is an explicit
copy into `shifts`.
"""
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.realtime.redis_listener import _send_expo_push
from app.services.roster_autoschedule import generate_draft

router = APIRouter(prefix="/api/v1/roster", tags=["guard-ops"])

# Supervisor(3), Operator(4), Security Guard(5) and Manager(8) — the same
# set the manual-assignment pickers offer. Wider than the auto-scheduler's
# candidate pool, which excludes Manager: a planner may put a Manager on a
# shift for oversight, but the scheduler should not choose to.
GUARD_ROLE_IDS_FOR_GRID = (3, 4, 5, 8)


async def _publish_roster_event(request: Request, tenant_id: str, batch_id: str) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return
    try:
        event = json.dumps({
            "event_type": "roster_published",
            "tenant_id": tenant_id,
            "payload": {"batch_id": batch_id},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        })
        await redis.publish(f"tenant_events:{tenant_id}", event)
    except Exception:
        pass


# ── Auto-schedule draft/publish ───────────────────────────────────────────────

class AutoScheduleBody(BaseModel):
    site_id: str | None = None
    period_start: date
    period_end: date


@router.post("/auto-schedule", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("roster:autoschedule"))])
async def auto_schedule(
    body: AutoScheduleBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if body.period_end < body.period_start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "period_end must not be before period_start")
    if (body.period_end - body.period_start).days > 62:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "period must not exceed 62 days")

    batch_id = await generate_draft(db, token.user_id, body.site_id, body.period_start, body.period_end)
    return await _get_batch_detail(db, batch_id)


async def _get_batch_detail(db: AsyncSession, batch_id: str) -> dict:
    batch_row = (await db.execute(
        text("""
            SELECT b.id, b.site_id, b.period_start, b.period_end, b.status, b.rules_summary,
                   b.generated_at, b.published_at, s.name AS site_name
            FROM roster_batches b LEFT JOIN sites s ON s.id = b.site_id
            WHERE b.id = CAST(:id AS uuid)
        """),
        {"id": batch_id},
    )).first()
    if batch_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Roster batch not found")

    shift_rows = (await db.execute(
        text("""
            SELECT ds.id, ds.guard_user_id, ds.site_id, ds.scheduled_start, ds.scheduled_end,
                   ds.shift_type, ds.warnings, u.full_name AS guard_name, s.name AS site_name
            FROM roster_draft_shifts ds
            LEFT JOIN users u ON u.id = ds.guard_user_id
            JOIN sites s ON s.id = ds.site_id
            WHERE ds.batch_id = CAST(:id AS uuid)
            ORDER BY ds.scheduled_start
        """),
        {"id": batch_id},
    )).mappings().all()

    return {**dict(batch_row._mapping), "draft_shifts": [dict(r) for r in shift_rows]}


@router.get("/batches/{batch_id}", dependencies=[Depends(require_permission("roster:autoschedule"))])
async def get_batch(batch_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    return await _get_batch_detail(db, batch_id)


class DraftShiftUpdate(BaseModel):
    guard_user_id: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None


@router.put("/draft-shifts/{draft_shift_id}", dependencies=[Depends(require_permission("roster:autoschedule"))])
async def update_draft_shift(draft_shift_id: str, body: DraftShiftUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    # Reassigning clears any stale warnings — the human just resolved them manually.
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = draft_shift_id
    result = await db.execute(
        text(f"UPDATE roster_draft_shifts SET {set_clause}, warnings = '[]'::jsonb "
             "WHERE id = CAST(:id AS uuid) RETURNING id, guard_user_id, scheduled_start, scheduled_end"),
        updates,
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft shift not found")
    await db.commit()
    return dict(row._mapping)


@router.post("/batches/{batch_id}/publish", dependencies=[Depends(require_permission("roster:autoschedule"))])
async def publish_batch(
    batch_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    batch_row = (await db.execute(
        text("SELECT status FROM roster_batches WHERE id = CAST(:id AS uuid)"), {"id": batch_id}
    )).first()
    if batch_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Roster batch not found")
    if batch_row.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Batch is already {batch_row.status}")

    filled_rows = (await db.execute(
        text("""
            SELECT guard_user_id, site_id, scheduled_start, scheduled_end, shift_type
            FROM roster_draft_shifts
            WHERE batch_id = CAST(:id AS uuid) AND guard_user_id IS NOT NULL
        """),
        {"id": batch_id},
    )).mappings().all()

    guard_ids: set[str] = set()
    for r in filled_rows:
        await db.execute(
            text("""
                INSERT INTO shifts (tenant_id, guard_user_id, site_id, scheduled_start, scheduled_end,
                                    shift_type, status, created_by_user_id)
                VALUES (current_setting('app.current_tenant')::uuid, :gid, :sid, :ss, :se, :stype, 'scheduled', :uid)
            """),
            {
                "gid": str(r["guard_user_id"]), "sid": str(r["site_id"]),
                "ss": r["scheduled_start"], "se": r["scheduled_end"],
                "stype": r["shift_type"], "uid": token.user_id,
            },
        )
        guard_ids.add(str(r["guard_user_id"]))

    await db.execute(
        text("UPDATE roster_batches SET status = 'published', published_by_user_id = :uid, published_at = now() "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": batch_id, "uid": token.user_id},
    )
    await db.commit()

    redis = getattr(request.app.state, "redis", None)
    if redis is not None and guard_ids:
        tokens: list[str] = []
        for gid in guard_ids:
            try:
                member_tokens = await redis.smembers(f"push_tokens:{token.tenant_id}:{gid}")
                tokens.extend(member_tokens)
            except Exception:
                continue
        await _send_expo_push(tokens, "Roster Published", "Your shift schedule has been updated.", {"type": "roster_published"})

    await _publish_roster_event(request, token.tenant_id, batch_id)
    return {"id": batch_id, "status": "published", "shifts_created": len(filled_rows)}


@router.delete("/batches/{batch_id}", dependencies=[Depends(require_permission("roster:autoschedule"))])
async def discard_batch(batch_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("UPDATE roster_batches SET status = 'discarded' WHERE id = CAST(:id AS uuid) AND status = 'draft' "
             "RETURNING id"),
        {"id": batch_id},
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft batch not found or already resolved")
    await db.commit()
    return {"id": batch_id, "status": "discarded"}


# ── Leave blocks ───────────────────────────────────────────────────────────────

class LeaveBlockCreate(BaseModel):
    guard_user_id: str
    start_date: date
    end_date: date
    reason: str | None = None


@router.get("/leave-blocks", dependencies=[Depends(require_permission("shift:read"))])
async def list_leave_blocks(db: AsyncSession = Depends(get_db_with_tenant), guard_user_id: str | None = None):
    where = "WHERE lb.guard_user_id = CAST(:gid AS uuid)" if guard_user_id else ""
    result = await db.execute(
        text(f"""
            SELECT lb.id, lb.guard_user_id, lb.start_date, lb.end_date, lb.reason, lb.created_at,
                   u.full_name AS guard_name
            FROM guard_leave_blocks lb JOIN users u ON u.id = lb.guard_user_id
            {where}
            ORDER BY lb.start_date DESC
        """),
        {"gid": guard_user_id} if guard_user_id else {},
    )
    return [dict(r._mapping) for r in result]


@router.post("/leave-blocks", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("shift:manage"))])
async def create_leave_block(body: LeaveBlockCreate, db: AsyncSession = Depends(get_db_with_tenant),
                              token: TokenPayload = Depends(get_token_payload)):
    if body.end_date < body.start_date:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "end_date must not be before start_date")
    result = await db.execute(
        text("""
            INSERT INTO guard_leave_blocks (tenant_id, guard_user_id, start_date, end_date, reason, created_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:gid AS uuid), :sd, :ed, :reason, CAST(:uid AS uuid))
            RETURNING id, guard_user_id, start_date, end_date, reason, created_at
        """),
        {"gid": body.guard_user_id, "sd": body.start_date, "ed": body.end_date, "reason": body.reason, "uid": token.user_id},
    )
    row = result.first()

    affected = await db.execute(
        text("""
            SELECT sh.id, sh.scheduled_start, sh.scheduled_end, s.name AS site_name
            FROM shifts sh LEFT JOIN sites s ON s.id = sh.site_id
            WHERE sh.guard_user_id = CAST(:gid AS uuid)
              AND sh.status = 'scheduled'
              AND sh.scheduled_start::date >= :sd AND sh.scheduled_start::date <= :ed
            ORDER BY sh.scheduled_start
        """),
        {"gid": body.guard_user_id, "sd": body.start_date, "ed": body.end_date},
    )
    affected_shifts = [dict(r._mapping) for r in affected]

    # Same treatment as an approved leave request: a supervisor blocking leave
    # out directly here leaves exactly the same posts uncovered, so it belongs
    # in the same queue rather than relying on them remembering.
    await _open_cover_requests_for(
        db, body.guard_user_id, [s["id"] for s in affected_shifts], leave_block_id=row.id,
    )

    await db.commit()
    result_dict = dict(row._mapping)
    result_dict["affected_shifts"] = affected_shifts
    result_dict["cover_requests_opened"] = len(affected_shifts)
    return result_dict


@router.delete("/leave-blocks/{leave_block_id}", dependencies=[Depends(require_permission("shift:manage"))])
async def delete_leave_block(leave_block_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("DELETE FROM guard_leave_blocks WHERE id = CAST(:id AS uuid) RETURNING id"), {"id": leave_block_id}
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leave block not found")
    await db.commit()
    return {"id": leave_block_id, "deleted": True}


# ── Shift preferences ────────────────────────────────────────────────────────

class PreferencesUpsert(BaseModel):
    preferred_shift_type: str | None = None  # 'day' | 'night' | None
    preferred_off_days: list[int] | None = None  # 0=Mon..6=Sun


@router.get("/preferences/{guard_user_id}", dependencies=[Depends(require_permission("shift:read"))])
async def get_preferences(guard_user_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(
        text("SELECT guard_user_id, preferred_shift_type, preferred_off_days, updated_at "
             "FROM guard_shift_preferences WHERE guard_user_id = CAST(:gid AS uuid)"),
        {"gid": guard_user_id},
    )).first()
    if row is None:
        return {"guard_user_id": guard_user_id, "preferred_shift_type": None, "preferred_off_days": None}
    return dict(row._mapping)


_GUARD_ROLES = {4, 5}


@router.put("/preferences/{guard_user_id}", dependencies=[Depends(require_permission("shift:read"))])
async def set_preferences(
    guard_user_id: str,
    body: PreferencesUpsert,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    # shift:read (held by guards themselves) gates this endpoint, not a write
    # permission — so without this check any guard could overwrite any other
    # guard's preferences. Mirrors the self-ownership check already used in
    # leave.py/attendance.py for the same class of guard-tier write.
    if token.role_id in _GUARD_ROLES and guard_user_id != str(token.user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot set another guard's preferences")
    if body.preferred_shift_type is not None and body.preferred_shift_type not in ("day", "night"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "preferred_shift_type must be 'day' or 'night'")
    result = await db.execute(
        text("""
            INSERT INTO guard_shift_preferences (tenant_id, guard_user_id, preferred_shift_type, preferred_off_days)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:gid AS uuid), :stype, :off_days)
            ON CONFLICT (guard_user_id) DO UPDATE
                SET preferred_shift_type = EXCLUDED.preferred_shift_type,
                    preferred_off_days = EXCLUDED.preferred_off_days,
                    updated_at = now()
            RETURNING guard_user_id, preferred_shift_type, preferred_off_days, updated_at
        """),
        {"gid": guard_user_id, "stype": body.preferred_shift_type, "off_days": body.preferred_off_days},
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


# ── Cover requests ──────────────────────────────────────────────────────────
#
# When approved leave lands on a shift that is already published, the post
# needs somebody else. These are that queue: one open row per uncovered post,
# resolved only by a human choosing a replacement or saying none is needed.
#
# The supervisor assigns. Nothing here picks a guard automatically — the
# auto-scheduler builds rosters, but filling a hole left by leave is a
# judgement about who is actually available and willing, and getting it wrong
# means somebody finds out at handover.


class CoverAssign(BaseModel):
    guard_user_id: str
    note: str | None = None


class CoverDismiss(BaseModel):
    note: str | None = None


async def _open_cover_requests_for(
    db: AsyncSession, guard_user_id: str, shift_ids: list,
    leave_block_id=None, leave_request_id: str | None = None,
) -> int:
    """One open request per clashing shift. Idempotent via the partial unique
    index, so overlapping leave cannot queue the same post twice."""
    for shift_id in shift_ids:
        await db.execute(
            text("""
                INSERT INTO roster_cover_requests
                    (tenant_id, shift_id, absent_user_id, leave_request_id, leave_block_id)
                VALUES (current_setting('app.current_tenant')::uuid,
                        :sid, CAST(:gid AS uuid), CAST(:rid AS uuid), :bid)
                ON CONFLICT (shift_id) WHERE status = 'open' DO NOTHING
            """),
            {"sid": shift_id, "gid": guard_user_id, "rid": leave_request_id, "bid": leave_block_id},
        )
    return len(shift_ids)


@router.get("/cover-requests", dependencies=[Depends(require_permission("shift:read"))])
async def list_cover_requests(
    db: AsyncSession = Depends(get_db_with_tenant),
    status_filter: str = "open",
):
    """Posts left uncovered by approved leave.

    Ordered by when the shift starts, not when the request was raised: the one
    that begins tonight matters more than the one raised first.
    """
    if status_filter not in ("open", "filled", "dismissed", "all"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "status_filter must be open, filled, dismissed or all")
    where = "" if status_filter == "all" else "WHERE cr.status = :st"
    result = await db.execute(
        text(f"""
            SELECT cr.id, cr.shift_id, cr.status, cr.note, cr.created_at, cr.resolved_at,
                   cr.absent_user_id, absent.full_name AS absent_guard_name,
                   cr.filled_with_user_id, filler.full_name AS filled_with_name,
                   sh.scheduled_start, sh.scheduled_end, sh.shift_type,
                   sh.site_id, s.name AS site_name,
                   sh.guard_user_id AS current_guard_user_id,
                   current_guard.full_name AS current_guard_name,
                   lb.start_date AS leave_start, lb.end_date AS leave_end, lb.reason AS leave_reason
              FROM roster_cover_requests cr
              JOIN shifts sh ON sh.id = cr.shift_id
              JOIN users absent ON absent.id = cr.absent_user_id
         LEFT JOIN users filler ON filler.id = cr.filled_with_user_id
         LEFT JOIN users current_guard ON current_guard.id = sh.guard_user_id
         LEFT JOIN sites s ON s.id = sh.site_id
         LEFT JOIN guard_leave_blocks lb ON lb.id = cr.leave_block_id
            {where}
          ORDER BY sh.scheduled_start
        """),
        {} if status_filter == "all" else {"st": status_filter},
    )
    return [dict(r._mapping) for r in result]


@router.post("/cover-requests/{cover_id}/assign",
             dependencies=[Depends(require_permission("shift:manage"))])
async def assign_cover(
    cover_id: str,
    body: CoverAssign,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Hand the post to a replacement.

    This UPDATES the existing shift rather than creating a new one. The shift
    is the post; only who stands it changes. Creating a replacement and
    cancelling the original would leave a pattern-generated shift to be
    regenerated on the next run, re-assigning the guard who is on leave.
    """
    cover = (await db.execute(
        text("""
            SELECT cr.id, cr.shift_id, cr.status, sh.scheduled_start, sh.scheduled_end
              FROM roster_cover_requests cr JOIN shifts sh ON sh.id = cr.shift_id
             WHERE cr.id = CAST(:id AS uuid)
        """),
        {"id": cover_id},
    )).first()
    if cover is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cover request not found")
    if cover.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cover request is already {cover.status}")

    guard = (await db.execute(
        text("SELECT id, full_name FROM users WHERE id = CAST(:id AS uuid) AND is_active = TRUE"),
        {"id": body.guard_user_id},
    )).first()
    if guard is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Guard not found")

    # Refuse to solve one hole by digging another. Both checks are the reason
    # this is a real endpoint and not a bare UPDATE from the client.
    on_leave = (await db.execute(
        text("""
            SELECT 1 FROM guard_leave_blocks
             WHERE guard_user_id = CAST(:gid AS uuid)
               AND :d BETWEEN start_date AND end_date
        """),
        {"gid": body.guard_user_id, "d": cover.scheduled_start.date()},
    )).first()
    if on_leave:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "That guard is on approved leave for the date of this shift")

    clash = (await db.execute(
        text("""
            SELECT 1 FROM shifts
             WHERE guard_user_id = CAST(:gid AS uuid)
               AND id <> :sid
               AND status IN ('scheduled', 'active')
               AND scheduled_start < :shift_end AND scheduled_end > :shift_start
        """),
        {"gid": body.guard_user_id, "sid": cover.shift_id,
         "shift_start": cover.scheduled_start, "shift_end": cover.scheduled_end},
    )).first()
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "That guard is already rostered on an overlapping shift")

    await db.execute(
        text("UPDATE shifts SET guard_user_id = CAST(:gid AS uuid), updated_at = now() WHERE id = :sid"),
        {"gid": body.guard_user_id, "sid": cover.shift_id},
    )
    await db.execute(
        text("""
            UPDATE roster_cover_requests
               SET status = 'filled', filled_with_user_id = CAST(:gid AS uuid),
                   resolved_by_user_id = CAST(:uid AS uuid), resolved_at = now(),
                   note = COALESCE(:note, note), updated_at = now()
             WHERE id = CAST(:id AS uuid)
        """),
        {"gid": body.guard_user_id, "uid": token.user_id, "note": body.note, "id": cover_id},
    )
    await db.commit()
    return {
        "id": cover_id,
        "status": "filled",
        "shift_id": str(cover.shift_id),
        "filled_with_user_id": body.guard_user_id,
        "filled_with_name": guard.full_name,
    }


@router.post("/cover-requests/{cover_id}/dismiss",
             dependencies=[Depends(require_permission("shift:manage"))])
async def dismiss_cover(
    cover_id: str,
    body: CoverDismiss,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """No cover needed — the site can run short, or it was handled elsewhere.

    Kept distinct from filled so a monthly report can tell "we covered it"
    apart from "we decided not to", which are very different answers to a
    client asking why a post was empty.
    """
    result = await db.execute(
        text("""
            UPDATE roster_cover_requests
               SET status = 'dismissed', resolved_by_user_id = CAST(:uid AS uuid),
                   resolved_at = now(), note = COALESCE(:note, note), updated_at = now()
             WHERE id = CAST(:id AS uuid) AND status = 'open'
            RETURNING id
        """),
        {"uid": token.user_id, "note": body.note, "id": cover_id},
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Open cover request not found")
    await db.commit()
    return {"id": cover_id, "status": "dismissed"}


# ── Roster grid ─────────────────────────────────────────────────────────────
#
# One employee-by-day matrix for a period, across every site unless one is
# named. /shifts/roster/coverage returns a flat list of shifts in a fixed
# today-forward window; this returns the grid a planner actually reads —
# who is on, what shift, at which site, who is on leave, and how much of the
# required strength is actually covered.
#
# Assembled from four queries rather than one join: a month of shifts across
# forty guards multiplied by their leave blocks and their sites produces a
# result set mostly made of repetition. Fetching each set once and stitching
# them in Python keeps the payload proportional to what is displayed.

GRID_MAX_DAYS = 62  # two months; beyond that the grid stops being readable


@router.get("/grid", dependencies=[Depends(require_permission("shift:read"))])
async def roster_grid(
    start_date: date,
    end_date: date,
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
):
    if end_date < start_date:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "end_date must not be before start_date")
    span = (end_date - start_date).days + 1
    if span > GRID_MAX_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Range is {span} days; the grid covers at most {GRID_MAX_DAYS}",
        )

    days = [(start_date + timedelta(days=i)).isoformat() for i in range(span)]

    site_clause = "AND sh.site_id = CAST(:site_id AS uuid)" if site_id else ""
    params: dict = {"start": start_date, "end": end_date}
    if site_id:
        params["site_id"] = site_id

    # ── Who can appear on the grid ───────────────────────────────────────────
    #
    # Everyone schedulable, not only those already rostered — an empty row is
    # the point: it is where a planner sees somebody free and clicks.
    guards = [dict(r) for r in (await db.execute(
        text("""
            SELECT u.id, u.full_name, u.email, u.role_id, u.designation,
                   u.employment_type, u.profile_photo_path,
                   p.preferred_shift_type
              FROM users u
         LEFT JOIN guard_shift_preferences p ON p.guard_user_id = u.id
             WHERE u.is_active = TRUE AND u.role_id = ANY(:roles)
          ORDER BY u.full_name NULLS LAST, u.email
        """),
        {"roles": list(GUARD_ROLE_IDS_FOR_GRID)},
    )).mappings().all()]

    # ── What they are rostered on ────────────────────────────────────────────
    shifts = [dict(r) for r in (await db.execute(
        text(f"""
            SELECT sh.id, sh.guard_user_id, sh.site_id, sh.status,
                   sh.scheduled_start, sh.scheduled_end,
                   (sh.scheduled_start AT TIME ZONE COALESCE(t.timezone, 'UTC'))::date AS on_date,
                   s.name AS site_name,
                   d.id AS shift_definition_id, d.name AS shift_name,
                   d.shift_type AS definition_type, d.colour
              FROM shifts sh
              JOIN tenants t ON t.id = sh.tenant_id
         LEFT JOIN sites s ON s.id = sh.site_id
         LEFT JOIN shift_definitions d ON d.id = sh.shift_definition_id
             WHERE (sh.scheduled_start AT TIME ZONE COALESCE(t.timezone, 'UTC'))::date
                   BETWEEN :start AND :end
               {site_clause}
          ORDER BY sh.scheduled_start
        """),
        params,
    )).mappings().all()]

    # ── Who is off ───────────────────────────────────────────────────────────
    leave = [dict(r) for r in (await db.execute(
        text("""
            SELECT guard_user_id, start_date, end_date, reason
              FROM guard_leave_blocks
             WHERE start_date <= :end AND end_date >= :start
        """),
        {"start": start_date, "end": end_date},
    )).mappings().all()]

    # ── What the estate is meant to be staffed at ────────────────────────────
    site_rows = [dict(r) for r in (await db.execute(
        text(f"""
            SELECT id, name, day_guards_required, night_guards_required
              FROM sites
             WHERE is_active = TRUE
               {"AND id = CAST(:site_id AS uuid)" if site_id else ""}
          ORDER BY name
        """),
        {"site_id": site_id} if site_id else {},
    )).mappings().all()]

    # ── Stitch ───────────────────────────────────────────────────────────────
    by_guard: dict[str, dict] = {}
    for g in guards:
        by_guard[str(g["id"])] = {
            "guard_user_id": str(g["id"]),
            "full_name": g["full_name"] or g["email"],
            "designation": g["designation"],
            "employment_type": g["employment_type"],
            "role_id": g["role_id"],
            "profile_photo_path": g["profile_photo_path"],
            "preferred_shift_type": g["preferred_shift_type"],
            "cells": {},
            "leave_days": [],
            "site_names": [],
        }

    day_count = night_count = 0
    for sh in shifts:
        row = by_guard.get(str(sh["guard_user_id"]))
        if row is None:
            continue  # rostered but no longer schedulable; not a grid row
        on = sh["on_date"].isoformat()
        # A definition wins where the shift has one. Where it does not — every
        # shift predating shift_definitions — fall back to the same start-hour
        # rule the scheduler uses, so an old roster still colours correctly
        # instead of rendering as an unclassified blank.
        kind = sh["definition_type"] or (
            "day" if 5 <= sh["scheduled_start"].hour < 17 else "night"
        )
        if kind == "day":
            day_count += 1
        elif kind == "night":
            night_count += 1
        row["cells"].setdefault(on, []).append({
            "shift_id": str(sh["id"]),
            "site_id": str(sh["site_id"]) if sh["site_id"] else None,
            "site_name": sh["site_name"],
            "shift_definition_id": str(sh["shift_definition_id"]) if sh["shift_definition_id"] else None,
            "shift_name": sh["shift_name"],
            "shift_type": kind,
            "colour": sh["colour"],
            "start": sh["scheduled_start"].isoformat(),
            "end": sh["scheduled_end"].isoformat(),
            "status": sh["status"],
        })
        if sh["site_name"] and sh["site_name"] not in row["site_names"]:
            row["site_names"].append(sh["site_name"])

    for lv in leave:
        row = by_guard.get(str(lv["guard_user_id"]))
        if row is None:
            continue
        d = max(lv["start_date"], start_date)
        last = min(lv["end_date"], end_date)
        while d <= last:
            iso = d.isoformat()
            if iso not in row["leave_days"]:
                row["leave_days"].append(iso)
            d += timedelta(days=1)

    # Coverage is filled posts against required posts for the whole window.
    # Required counts every active site every day, because a site that is
    # staffed for two officers is short on a day nobody was rostered — leaving
    # those days out would flatter the number precisely where it matters.
    required = sum(
        (int(s["day_guards_required"] or 0) + int(s["night_guards_required"] or 0))
        for s in site_rows
    ) * span
    filled = len(shifts)
    coverage = round(min(filled / required, 1.0) * 100) if required else 0

    days_off = sum(
        1
        for row in by_guard.values()
        for d in days
        if d not in row["cells"] and d not in row["leave_days"]
    )

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": days,
        "employees": list(by_guard.values()),
        "sites": [
            {
                "id": str(s["id"]), "name": s["name"],
                "day_guards_required": s["day_guards_required"],
                "night_guards_required": s["night_guards_required"],
            }
            for s in site_rows
        ],
        "stats": {
            "staff": len(by_guard),
            "day_shifts": day_count,
            "night_shifts": night_count,
            "days_off": days_off,
            "required_posts": required,
            "filled_posts": filled,
            "coverage_pct": coverage,
        },
    }


class GridAssign(BaseModel):
    guard_user_id: str
    site_id: str
    shift_definition_id: str
    on_date: date


@router.post("/grid/assign", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("shift:manage"))])
async def assign_grid_cell(
    body: GridAssign,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Put a named shift on one person on one day.

    Distinct from POST /shifts, which takes raw timestamps: here the times come
    from the shift definition, so a cell cannot be filled with something that
    is not one of the shifts the company actually runs, and the start and end
    cannot drift from the definition they claim to be.

    The timestamp is built in the TENANT's timezone. Doing it in the browser
    would make the same click produce a different shift depending on where the
    planner happened to be sitting.
    """
    definition = (await db.execute(
        text("SELECT id, name, shift_type, start_time, duration_minutes "
             "FROM shift_definitions WHERE id = CAST(:id AS uuid) AND is_active = TRUE"),
        {"id": body.shift_definition_id},
    )).first()
    if definition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")

    guard = (await db.execute(
        text("SELECT id, full_name FROM users WHERE id = CAST(:id AS uuid) AND is_active = TRUE"),
        {"id": body.guard_user_id},
    )).first()
    if guard is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Guard not found")

    on_leave = (await db.execute(
        text("SELECT 1 FROM guard_leave_blocks WHERE guard_user_id = CAST(:gid AS uuid) "
             "AND :d BETWEEN start_date AND end_date"),
        {"gid": body.guard_user_id, "d": body.on_date},
    )).first()
    if on_leave:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "That guard is on approved leave for this date")

    # Built and range-checked in one statement so the overlap test uses exactly
    # the timestamps that will be stored, rather than a Python reconstruction
    # of them that could round differently.
    row = (await db.execute(
        text("""
            WITH bounds AS (
                SELECT (CAST(:d AS date) + CAST(:st AS time))
                         AT TIME ZONE COALESCE(t.timezone, 'UTC') AS starts_at,
                       (CAST(:d AS date) + CAST(:st AS time) + make_interval(mins => :dur))
                         AT TIME ZONE COALESCE(t.timezone, 'UTC') AS ends_at
                  FROM tenants t
                 WHERE t.id = current_setting('app.current_tenant')::uuid
            )
            SELECT starts_at, ends_at,
                   EXISTS (
                       SELECT 1 FROM shifts sh, bounds b
                        WHERE sh.guard_user_id = CAST(:gid AS uuid)
                          AND sh.status IN ('scheduled', 'active')
                          AND sh.scheduled_start < b.ends_at
                          AND sh.scheduled_end > b.starts_at
                   ) AS clashes
              FROM bounds
        """),
        {"d": body.on_date, "st": definition.start_time,
         "dur": definition.duration_minutes, "gid": body.guard_user_id},
    )).first()

    if row.clashes:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "That guard already has an overlapping shift")

    created = (await db.execute(
        text("""
            INSERT INTO shifts
                (tenant_id, guard_user_id, site_id, scheduled_start, scheduled_end,
                 shift_type, shift_definition_id, status, created_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid,
                    CAST(:gid AS uuid), CAST(:sid AS uuid), :ss, :se,
                    :stype, CAST(:def AS uuid), 'scheduled', CAST(:uid AS uuid))
            RETURNING id, guard_user_id, site_id, scheduled_start, scheduled_end, status
        """),
        {
            "gid": body.guard_user_id, "sid": body.site_id,
            "ss": row.starts_at, "se": row.ends_at,
            # 'general' and 'split' definitions store their own type on the
            # shift so reporting can tell them apart, even though the
            # scheduler's day/night balance only counts the first two.
            "stype": definition.shift_type,
            "def": body.shift_definition_id, "uid": token.user_id,
        },
    )).first()
    await db.commit()
    return {**dict(created._mapping), "shift_name": definition.name}


@router.delete("/grid/assign/{shift_id}",
               dependencies=[Depends(require_permission("shift:manage"))])
async def clear_grid_cell(shift_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """Take a shift off the roster.

    Refused once the shift has started: a guard who has checked in has an
    attendance record, and removing the shift underneath it would orphan that
    record and quietly erase hours somebody worked and expects to be paid for.
    """
    existing = (await db.execute(
        text("SELECT status FROM shifts WHERE id = CAST(:id AS uuid)"),
        {"id": shift_id},
    )).first()
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
    if existing.status != "scheduled":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This shift is {existing.status} and can no longer be removed from the roster",
        )

    await db.execute(text("DELETE FROM shifts WHERE id = CAST(:id AS uuid)"), {"id": shift_id})
    await db.commit()
    return {"id": shift_id, "removed": True}


# ── Bulk assign ─────────────────────────────────────────────────────────────

DAY_PATTERNS = ("all", "weekdays", "alternate")


class BulkAssign(BaseModel):
    shift_definition_id: str
    site_id: str
    start_date: date
    end_date: date
    day_pattern: str = "all"
    # None means everyone schedulable. A list narrows it, which is what the
    # dialog sends once a planner has deselected somebody.
    guard_user_ids: list[str] | None = None


@router.post("/grid/bulk-assign", dependencies=[Depends(require_permission("shift:manage"))])
async def bulk_assign(
    body: BulkAssign,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Put one shift on many people across a date range.

    SKIPS RATHER THAN FAILS. Applying a month of shifts to a whole team will
    always collide with something — somebody has leave, somebody is already
    rostered that night. Rejecting the batch for that would make the feature
    useless precisely when it is most wanted, so every clash is skipped and
    counted, and the response says exactly who and why.

    Reads the whole picture once rather than validating per insert: a month
    across a team is a few hundred candidate shifts, and three queries each
    would be a thousand round trips for work that is one comparison.
    """
    if body.day_pattern not in DAY_PATTERNS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"day_pattern must be one of {list(DAY_PATTERNS)}")
    if body.end_date < body.start_date:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "end_date must not be before start_date")
    span = (body.end_date - body.start_date).days + 1
    if span > GRID_MAX_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Range is {span} days; at most {GRID_MAX_DAYS}")

    definition = (await db.execute(
        text("SELECT id, name, shift_type, start_time, duration_minutes "
             "FROM shift_definitions WHERE id = CAST(:id AS uuid) AND is_active = TRUE"),
        {"id": body.shift_definition_id},
    )).first()
    if definition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")

    site = (await db.execute(
        text("SELECT id, name FROM sites WHERE id = CAST(:id AS uuid) AND is_active = TRUE"),
        {"id": body.site_id},
    )).first()
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")

    guard_filter = ""
    params: dict = {"roles": list(GUARD_ROLE_IDS_FOR_GRID)}
    if body.guard_user_ids is not None:
        if not body.guard_user_ids:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "guard_user_ids was empty; omit it to mean everyone")
        guard_filter = "AND u.id = ANY(CAST(:ids AS uuid[]))"
        params["ids"] = body.guard_user_ids

    guards = [dict(r) for r in (await db.execute(
        text(f"""
            SELECT u.id, u.full_name, u.email
              FROM users u
             WHERE u.is_active = TRUE AND u.role_id = ANY(:roles) {guard_filter}
          ORDER BY u.full_name NULLS LAST, u.email
        """),
        params,
    )).mappings().all()]
    if not guards:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No schedulable staff matched")

    guard_ids = [str(g["id"]) for g in guards]

    tz_name = (await db.execute(
        text("SELECT COALESCE(timezone, 'UTC') FROM tenants "
             "WHERE id = current_setting('app.current_tenant')::uuid")
    )).scalar_one()
    tz = ZoneInfo(tz_name)

    # Everything that could block an assignment, fetched once.
    leave_rows = [dict(r) for r in (await db.execute(
        text("SELECT guard_user_id, start_date, end_date FROM guard_leave_blocks "
             "WHERE guard_user_id = ANY(CAST(:ids AS uuid[])) "
             "AND start_date <= :end AND end_date >= :start"),
        {"ids": guard_ids, "start": body.start_date, "end": body.end_date},
    )).mappings().all()]
    leave_by_guard: dict[str, list[tuple]] = {}
    for lv in leave_rows:
        leave_by_guard.setdefault(str(lv["guard_user_id"]), []).append(
            (lv["start_date"], lv["end_date"])
        )

    # A margin either side of the window, because a night shift starting the
    # evening before the range still overlaps its first morning.
    existing = [dict(r) for r in (await db.execute(
        text("""
            SELECT guard_user_id, scheduled_start, scheduled_end
              FROM shifts
             WHERE guard_user_id = ANY(CAST(:ids AS uuid[]))
               AND status IN ('scheduled', 'active')
               AND scheduled_start < CAST(:end AS date) + INTERVAL '2 days'
               AND scheduled_end   > CAST(:start AS date) - INTERVAL '2 days'
        """),
        {"ids": guard_ids, "start": body.start_date, "end": body.end_date},
    )).mappings().all()]
    busy: dict[str, list[tuple]] = {}
    for sh in existing:
        busy.setdefault(str(sh["guard_user_id"]), []).append(
            (sh["scheduled_start"], sh["scheduled_end"])
        )

    dates: list[date] = []
    for i in range(span):
        d = body.start_date + timedelta(days=i)
        if body.day_pattern == "weekdays" and d.weekday() >= 5:
            continue
        # Alternate days counts from the start of the range, not from the
        # calendar, so "every other day" means what the planner just chose.
        if body.day_pattern == "alternate" and i % 2:
            continue
        dates.append(d)

    created = 0
    skipped_leave = 0
    skipped_clash = 0
    per_guard: list[dict] = []

    for g in guards:
        gid = str(g["id"])
        g_created = g_leave = g_clash = 0
        for d in dates:
            if any(s <= d <= e for s, e in leave_by_guard.get(gid, [])):
                g_leave += 1
                continue

            starts_at = datetime.combine(d, definition.start_time, tzinfo=tz)
            ends_at = starts_at + timedelta(minutes=definition.duration_minutes)

            # Compared against shifts created earlier in this same run as well
            # as pre-existing ones, or a run covering two shifts a day would
            # happily double-book somebody against itself.
            if any(s < ends_at and e > starts_at for s, e in busy.get(gid, [])):
                g_clash += 1
                continue

            await db.execute(
                text("""
                    INSERT INTO shifts
                        (tenant_id, guard_user_id, site_id, scheduled_start, scheduled_end,
                         shift_type, shift_definition_id, status, created_by_user_id)
                    VALUES (current_setting('app.current_tenant')::uuid,
                            CAST(:gid AS uuid), CAST(:sid AS uuid), :ss, :se,
                            :stype, CAST(:def AS uuid), 'scheduled', CAST(:uid AS uuid))
                """),
                {
                    "gid": gid, "sid": body.site_id, "ss": starts_at, "se": ends_at,
                    "stype": definition.shift_type,
                    "def": body.shift_definition_id, "uid": token.user_id,
                },
            )
            busy.setdefault(gid, []).append((starts_at, ends_at))
            g_created += 1

        created += g_created
        skipped_leave += g_leave
        skipped_clash += g_clash
        per_guard.append({
            "guard_user_id": gid,
            "full_name": g["full_name"] or g["email"],
            "created": g_created,
            "skipped_on_leave": g_leave,
            "skipped_clash": g_clash,
        })

    await db.commit()
    return {
        "shift_name": definition.name,
        "site_name": site.name,
        "days_targeted": len(dates),
        "staff_targeted": len(guards),
        "created": created,
        "skipped_on_leave": skipped_leave,
        "skipped_clash": skipped_clash,
        "per_guard": per_guard,
    }
