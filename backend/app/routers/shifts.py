"""Guard shift management — create, start, end, list shifts, and handover reports."""

import asyncio
import json
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import InvalidTokenError, decode_access_token
from app.core.uploads import MAX_IMAGE_UPLOAD_BYTES, read_upload_limited
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.alarm_shift import arm_site_panels, disarm_site_panels
from app.services.geofence import is_within_site
from app.services.liveness import check_liveness_sync
from app.services.violations import VIOLATION_POINTS, create_violation

from app.services.sos import raise_guard_sos

router = APIRouter(prefix="/api/v1/shifts", tags=["guard-ops"])

# ── Attendance settings (ShiftSecure Phase 2A) ────────────────────────────────

"""Defaults and the tenant-override lookup now live in
services/attendance_status.py — shifts.py writes is_late using this grace
period and the attendance monitor and Site Map read it back, so a second
copy here would let the writer and the readers drift apart."""
from app.services.attendance_status import (  # noqa: E402
    ATTENDANCE_DEFAULTS as _ATTENDANCE_DEFAULTS,
    get_attendance_setting as _get_attendance_setting,
    get_site_grace_minutes as _get_site_grace_minutes,
)

_LIVENESS_MIN_SCORE_DEFAULT = 0.7


async def _get_liveness_min_score(db: AsyncSession) -> float:
    row = (await db.execute(
        text("SELECT setting_value FROM tenant_settings WHERE setting_key = 'attendance.liveness_min_score'"),
    )).first()
    if row is not None and isinstance(row[0], (int, float)) and not isinstance(row[0], bool):
        return float(row[0])
    return _LIVENESS_MIN_SCORE_DEFAULT




async def _process_checkin_photo(
    db: AsyncSession,
    tenant_id: str,
    shift_id: str,
    which: Literal["check_in", "check_out"],
    photo: UploadFile | None,
    is_mock_location: bool,
) -> tuple[str | None, float | None]:
    """Hard-enforces mock-GPS block + liveness check before a photo is ever
    written to disk. Returns (relative_storage_path, liveness_score) on
    success; raises HTTPException on any failure — callers must not write
    a shifts row unless this returns cleanly.

    A missing photo is a no-op (None, None), not an error — this endpoint
    is shared by two distinct callers: the mobile app's guard self-service
    check-in (always attaches a selfie, wants the full enforcement) and the
    web/desktop admin "GuardOps" override (starts/ends a shift on a guard's
    behalf, never attaches a photo — a supervisor's own selfie couldn't
    attest to the guard's identity or location anyway, so there's nothing
    meaningful to enforce there)."""
    # Checked BEFORE the missing-photo early return on purpose. This flag is
    # the server's independent re-check of a client-reported value, and its
    # whole point is that a modified client might lie — so it must not be
    # skippable by simply omitting the photo. The admin override path is
    # unaffected: it never reports a location at all, so the flag is False
    # there and this is a no-op.
    if is_mock_location:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mock/fake GPS location detected — check-in blocked")

    if photo is None:
        return None, None

    if photo.content_type not in ("image/jpeg", "image/jpg", "image/png"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only JPEG/PNG images are accepted")
    image_bytes = await read_upload_limited(photo, MAX_IMAGE_UPLOAD_BYTES)

    try:
        score = await asyncio.to_thread(check_liveness_sync, image_bytes)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    min_score = await _get_liveness_min_score(db)
    if score < min_score:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Liveness check failed (score {score:.2f} below required {min_score:.2f}) — please retake the photo",
        )

    relative_path = f"{tenant_id}/{shift_id}/{which}.jpg"
    dest = Path(settings.ATTENDANCE_PHOTOS_ROOT) / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(image_bytes)
    return relative_path, score


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
        pass  # realtime push failure must never block the attendance action


async def _publish_violation_event(
    request: Request, tenant_id: str, violation_id: str, violation_type: str, guard_user_id: str,
) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return
    try:
        event = json.dumps({
            "event_type": "violation_created",
            "tenant_id": tenant_id,
            "payload": {"violation_id": violation_id, "violation_type": violation_type, "guard_user_id": guard_user_id},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        })
        await redis.publish(f"tenant_events:{tenant_id}", event)
    except Exception:
        pass  # realtime push failure must never block the shift action


class ShiftCreate(BaseModel):
    guard_user_id: str | None = None
    site_id: str | None = None
    scheduled_start: str
    scheduled_end: str
    notes: str | None = None


class ShiftUpdate(BaseModel):
    guard_user_id: str | None = None
    site_id: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    handover_notes: str | None = None


# Guard-tier roles: security_guard (5) and operator (4). Both are scoped to
# their own shifts. Kept identical to violations.py and leave.py's _GUARD_ROLES
# so "which roles see only their own records" has one answer across the app
# rather than three subtly different ones.
_GUARD_ROLES = {4, 5}


@router.get("", dependencies=[Depends(require_permission("shift:read"))])
async def list_shifts(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    site_id: str | None = None,
    guard_user_id: str | None = None,
    shift_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    where_clauses = []
    params: dict = {"limit": min(limit, 200), "offset": max(offset, 0)}
    if site_id:
        where_clauses.append("sh.site_id = :site_id")
        params["site_id"] = site_id
    if token.role_id in _GUARD_ROLES:
        # Forced, not defaulted: a guard-tier caller passing someone else's
        # guard_user_id — or none at all — still gets only their own shifts.
        # Previously this endpoint returned every shift in the tenant to any
        # shift:read holder, exposing colleagues' names, emails, lateness and
        # geofence results. shift:read is granted to roles 1,2,3,4,5,6,8.
        where_clauses.append("sh.guard_user_id = :guard_user_id")
        params["guard_user_id"] = token.user_id
    elif guard_user_id:
        where_clauses.append("sh.guard_user_id = :guard_user_id")
        params["guard_user_id"] = guard_user_id
    if shift_status:
        where_clauses.append("sh.status = :shift_status")
        params["shift_status"] = shift_status
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    query = f"""
        SELECT sh.id, sh.guard_user_id, sh.site_id, sh.scheduled_start, sh.scheduled_end,
               sh.actual_start, sh.actual_end, sh.status, sh.handover_notes,
               sh.is_late, sh.late_minutes, sh.overtime_minutes, sh.is_within_geofence,
               EXISTS(SELECT 1 FROM shift_breaks b WHERE b.shift_id = sh.id AND b.break_end IS NULL) AS on_break,
               u.full_name AS guard_name, u.email AS guard_email,
               s.name AS site_name
        FROM shifts sh
        LEFT JOIN users u ON u.id = sh.guard_user_id
        LEFT JOIN sites s ON s.id = sh.site_id
        {where}
        ORDER BY sh.scheduled_start DESC LIMIT :limit OFFSET :offset
    """
    result = await db.execute(text(query), params)
    return [dict(row._mapping) for row in result]


@router.post("", dependencies=[Depends(require_permission("shift:manage"))])
async def create_shift(
    body: ShiftCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    guard_id = body.guard_user_id or token.user_id
    result = await db.execute(
        text(
            """
            INSERT INTO shifts (tenant_id, guard_user_id, site_id, scheduled_start, scheduled_end,
                                created_by_user_id)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                CAST(:guard_user_id AS uuid), CAST(:site_id AS uuid),
                :scheduled_start, :scheduled_end,
                CAST(:created_by AS uuid)
            )
            RETURNING id, guard_user_id, site_id, scheduled_start, scheduled_end, status
            """
        ),
        {
            "guard_user_id": guard_id,
            "site_id": body.site_id,
            "scheduled_start": datetime.fromisoformat(body.scheduled_start),
            "scheduled_end": datetime.fromisoformat(body.scheduled_end),
            "created_by": token.user_id,
        },
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


# ──────────────────────────────────────────────────────────
# Roster — recurring shift patterns (Gap 86)
# Declared before /{shift_id} routes so literal paths win.
# ──────────────────────────────────────────────────────────

class PatternCreate(BaseModel):
    site_id: str
    guard_user_id: str
    days_of_week: list[int]
    start_time: str          # "HH:MM"
    duration_minutes: int
    label: str | None = None


class PatternUpdate(BaseModel):
    site_id: str | None = None
    guard_user_id: str | None = None
    days_of_week: list[int] | None = None
    start_time: str | None = None
    duration_minutes: int | None = None
    label: str | None = None
    is_active: bool | None = None


class RosterGenerate(BaseModel):
    days_ahead: int = 7


def _validate_days(days: list[int]) -> list[int]:
    days = sorted(set(days))
    if not days or any(d < 0 or d > 6 for d in days):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "days_of_week must be a non-empty list of 0 (Mon) .. 6 (Sun)")
    return days


def _validate_time(value: str):
    """Parse HH:MM into a datetime.time — asyncpg binds time columns from
    time objects, not strings."""
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "start_time must be HH:MM")


def _validate_duration(minutes: int) -> int:
    if not 1 <= minutes <= 1440:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "duration_minutes must be between 1 and 1440")
    return minutes


_PATTERN_COLS = """p.id, p.site_id, p.guard_user_id, p.label, p.days_of_week,
       p.start_time, p.duration_minutes, p.is_active, p.created_at, p.updated_at"""


@router.get("/roster/patterns", dependencies=[Depends(require_permission("shift:read"))])
async def list_shift_patterns(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(text(f"""
        SELECT {_PATTERN_COLS},
               u.full_name AS guard_name, s.name AS site_name
        FROM shift_patterns p
        JOIN users u ON u.id = p.guard_user_id
        JOIN sites s ON s.id = p.site_id
        ORDER BY s.name, p.start_time
    """))
    rows = []
    for r in result:
        d = dict(r._mapping)
        d["start_time"] = str(d["start_time"])[:5]
        rows.append(d)
    return rows


@router.post("/roster/patterns", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("shift:manage"))])
async def create_shift_pattern(body: PatternCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    days = _validate_days(body.days_of_week)
    start_time = _validate_time(body.start_time)
    _validate_duration(body.duration_minutes)
    site = await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:id AS uuid)"),
                            {"id": body.site_id})
    if site.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    guard = await db.execute(text("SELECT 1 FROM users WHERE id = CAST(:id AS uuid)"),
                             {"id": body.guard_user_id})
    if guard.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Guard user not found")

    result = await db.execute(
        text("""
            INSERT INTO shift_patterns
                (tenant_id, site_id, guard_user_id, label, days_of_week,
                 start_time, duration_minutes)
            VALUES (current_setting('app.current_tenant')::uuid,
                    CAST(:site AS uuid), CAST(:guard AS uuid), :label,
                    :days, :stime, :dur)
            RETURNING id, site_id, guard_user_id, label, days_of_week,
                      start_time, duration_minutes, is_active, created_at, updated_at
        """),
        {"site": body.site_id, "guard": body.guard_user_id, "label": body.label,
         "days": days, "stime": start_time, "dur": body.duration_minutes},
    )
    row = dict(result.mappings().first())
    row["start_time"] = str(row["start_time"])[:5]
    await db.commit()
    return row


@router.put("/roster/patterns/{pattern_id}", dependencies=[Depends(require_permission("shift:manage"))])
async def update_shift_pattern(pattern_id: str, body: PatternUpdate,
                               db: AsyncSession = Depends(get_db_with_tenant)):
    sets, params = [], {"id": pattern_id}
    if body.site_id is not None:
        sets.append("site_id = CAST(:site AS uuid)"); params["site"] = body.site_id
    if body.guard_user_id is not None:
        sets.append("guard_user_id = CAST(:guard AS uuid)"); params["guard"] = body.guard_user_id
    if body.days_of_week is not None:
        sets.append("days_of_week = :days"); params["days"] = _validate_days(body.days_of_week)
    if body.start_time is not None:
        sets.append("start_time = :stime")
        params["stime"] = _validate_time(body.start_time)
    if body.duration_minutes is not None:
        sets.append("duration_minutes = :dur")
        params["dur"] = _validate_duration(body.duration_minutes)
    if body.label is not None:
        sets.append("label = :label"); params["label"] = body.label
    if body.is_active is not None:
        sets.append("is_active = :active"); params["active"] = body.is_active
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No fields to update")
    sets.append("updated_at = now()")
    result = await db.execute(
        text(f"UPDATE shift_patterns SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) "
             "RETURNING id, site_id, guard_user_id, label, days_of_week, "
             "          start_time, duration_minutes, is_active, updated_at"),
        params,
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pattern not found")
    d = dict(row)
    d["start_time"] = str(d["start_time"])[:5]
    await db.commit()
    return d


@router.delete("/roster/patterns/{pattern_id}", dependencies=[Depends(require_permission("shift:manage"))])
async def delete_shift_pattern(pattern_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("DELETE FROM shift_patterns WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": pattern_id},
    )
    if result.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pattern not found")
    await db.commit()
    return {"id": pattern_id, "deleted": True}


@router.post("/roster/generate", dependencies=[Depends(require_permission("shift:manage"))])
async def generate_roster(body: RosterGenerate, db: AsyncSession = Depends(get_db_with_tenant)):
    """Expand active patterns into concrete shifts for the next N days.
    Idempotent — re-running never duplicates a shift."""
    from app.services.roster import clamp_days_ahead, generate_roster_shifts

    days = clamp_days_ahead(body.days_ahead)
    created = await generate_roster_shifts(db, days)
    return {"created": created, "days_ahead": days}


@router.get("/roster/coverage", dependencies=[Depends(require_permission("shift:read"))])
async def roster_coverage(
    db: AsyncSession = Depends(get_db_with_tenant),
    days: int = 7,
    site_id: str | None = None,
):
    """Upcoming shifts window for the roster grid: today .. today+days."""
    days = max(1, min(days, 31))
    params: dict = {"days": days}
    site_clause = ""
    if site_id:
        site_clause = "AND sh.site_id = CAST(:site_id AS uuid)"
        params["site_id"] = site_id
    result = await db.execute(
        text(f"""
            SELECT sh.id, sh.site_id, sh.guard_user_id, sh.scheduled_start,
                   sh.scheduled_end, sh.status, sh.pattern_id,
                   u.full_name AS guard_name, s.name AS site_name
            FROM shifts sh
            LEFT JOIN users u ON u.id = sh.guard_user_id
            LEFT JOIN sites s ON s.id = sh.site_id
            WHERE sh.scheduled_start >= CURRENT_DATE
              AND sh.scheduled_start < CURRENT_DATE + make_interval(days => :days)
              {site_clause}
            ORDER BY sh.scheduled_start, s.name
        """),
        params,
    )
    return {"days": days, "shifts": [dict(r._mapping) for r in result]}


@router.post("/{shift_id}/start", dependencies=[Depends(require_permission("shift:manage"))])
async def start_shift(
    shift_id: str,
    request: Request,
    photo: UploadFile | None = File(None),
    latitude: float | None = None,
    longitude: float | None = None,
    is_mock_location: bool = False,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    shift_row = (await db.execute(
        text("SELECT scheduled_start, site_id, guard_user_id FROM shifts WHERE id = :id AND status = 'scheduled'"),
        {"id": shift_id},
    )).first()
    if shift_row is None:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found or not in scheduled status")

    # Hard-blocks (mock GPS / liveness) happen before any shift-state write.
    photo_path, liveness_score = await _process_checkin_photo(
        db, token.tenant_id, shift_id, "check_in", photo, is_mock_location,
    )

    now = datetime.now(timezone.utc)
    # Per-site grace, falling back to the tenant setting. Resolved through the
    # shared helper so the standard applied here — where is_late is written —
    # is the same one the attendance board applies when it renders it.
    grace_minutes = await _get_site_grace_minutes(db, shift_row.site_id)
    raw_late_minutes = (now - shift_row.scheduled_start).total_seconds() / 60
    is_late = raw_late_minutes > grace_minutes
    late_minutes = max(0, round(raw_late_minutes)) if is_late else 0

    is_within_geofence: bool | None = None
    if latitude is not None and longitude is not None and shift_row.site_id:
        site_row = (await db.execute(
            text(
                "SELECT latitude, longitude, geofence_radius_meters, geofence_polygon "
                "FROM sites WHERE id = :sid"
            ),
            {"sid": shift_row.site_id},
        )).first()
        if site_row is not None:
            # A drawn boundary takes precedence over the radius; is_within_site
            # owns that rule so the check-in writer and any future caller
            # cannot disagree about which shape applies.
            is_within_geofence = is_within_site(
                latitude, longitude,
                site_lat=site_row.latitude,
                site_lon=site_row.longitude,
                radius_meters=site_row.geofence_radius_meters or await _get_attendance_setting(
                    db, "attendance.geofence_radius_meters"
                ),
                polygon=site_row.geofence_polygon,
            )

    # Auto-detected violations (ShiftSecure Phase 3) — logged in the same
    # transaction as the check-in, never allowed to block it.
    new_violations: list[tuple[str, str]] = []
    if shift_row.guard_user_id:
        try:
            if is_late:
                vid = await create_violation(
                    db, token.tenant_id, shift_row.guard_user_id, "late_checkin",
                    shift_id=shift_id, site_id=shift_row.site_id,
                    points=VIOLATION_POINTS["late_checkin"], is_auto_generated=True,
                )
                if vid:
                    new_violations.append((vid, "late_checkin"))
            if is_within_geofence is False:
                vid = await create_violation(
                    db, token.tenant_id, shift_row.guard_user_id, "geofence_failure",
                    shift_id=shift_id, site_id=shift_row.site_id,
                    points=VIOLATION_POINTS["geofence_failure"], is_auto_generated=True,
                )
                if vid:
                    new_violations.append((vid, "geofence_failure"))
        except Exception:
            pass  # violation logging must never block a real check-in

    result = await db.execute(
        text(
            """
            UPDATE shifts SET status = 'active', actual_start = now(),
                   check_in_lat = :lat, check_in_lon = :lon,
                   is_within_geofence = :geo, is_late = :late, late_minutes = :late_min,
                   check_in_photo_path = :photo_path, check_in_liveness_score = :liveness,
                   check_in_is_mock_location = :is_mock
            WHERE id = :id AND status = 'scheduled'
            RETURNING id, status, actual_start, site_id, is_late, late_minutes, is_within_geofence
            """
        ),
        {
            "id": shift_id, "lat": latitude, "lon": longitude,
            "geo": is_within_geofence, "late": is_late, "late_min": late_minutes,
            "photo_path": photo_path, "liveness": liveness_score, "is_mock": is_mock_location,
        },
    )
    row = result.first()
    if row is None:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found or not in scheduled status")
    row_dict = dict(row._mapping)
    site_id = str(row_dict.get("site_id") or "") or None
    # Disarm before commit so app.current_tenant GUC is still set (RLS-safe)
    await disarm_site_panels(db, site_id, shift_id)
    await db.commit()
    await _publish_attendance_event(request, token.tenant_id, shift_id, "checked_in")
    for vid, vtype in new_violations:
        await _publish_violation_event(request, token.tenant_id, vid, vtype, shift_row.guard_user_id)
    return row_dict


@router.post("/{shift_id}/end", dependencies=[Depends(require_permission("shift:manage"))])
async def end_shift(
    shift_id: str,
    request: Request,
    photo: UploadFile | None = File(None),
    handover_notes: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    is_mock_location: bool = False,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    shift_row = (await db.execute(
        text("SELECT scheduled_end, site_id, guard_user_id FROM shifts WHERE id = :id AND status = 'active'"),
        {"id": shift_id},
    )).first()
    if shift_row is None:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found or not active")

    # Hard-blocks (mock GPS / liveness) happen before any shift-state write.
    photo_path, liveness_score = await _process_checkin_photo(
        db, token.tenant_id, shift_id, "check_out", photo, is_mock_location,
    )

    now = datetime.now(timezone.utc)
    overtime_minutes = 0
    is_early_departure = False
    if shift_row.scheduled_end is not None:
        threshold = await _get_attendance_setting(db, "attendance.overtime_threshold_minutes")
        over_minutes = (now - shift_row.scheduled_end).total_seconds() / 60
        if over_minutes > threshold:
            overtime_minutes = max(0, round(over_minutes - threshold))
        # Same per-site grace that decided lateness on the way in — a site
        # given a wider allowance at the start of a shift gets it at the end
        # too, or an early-departure violation contradicts the site's policy.
        grace_minutes = await _get_site_grace_minutes(db, shift_row.site_id)
        is_early_departure = -over_minutes > grace_minutes

    # Safety net: auto-close a break the guard forgot to end.
    await db.execute(
        text("UPDATE shift_breaks SET break_end = now() WHERE shift_id = :id AND break_end IS NULL"),
        {"id": shift_id},
    )

    # Auto-detected violation (ShiftSecure Phase 3) — logged in the same
    # transaction as the check-out, never allowed to block it.
    new_violations: list[tuple[str, str]] = []
    if shift_row.guard_user_id and is_early_departure:
        try:
            vid = await create_violation(
                db, token.tenant_id, shift_row.guard_user_id, "early_departure",
                shift_id=shift_id, site_id=shift_row.site_id,
                points=VIOLATION_POINTS["early_departure"], is_auto_generated=True,
            )
            if vid:
                new_violations.append((vid, "early_departure"))
        except Exception:
            pass  # violation logging must never block a real check-out

    result = await db.execute(
        text(
            """
            UPDATE shifts SET status = 'completed', actual_end = now(), handover_notes = :notes,
                   check_out_lat = :lat, check_out_lon = :lon, overtime_minutes = :ot,
                   check_out_photo_path = :photo_path, check_out_liveness_score = :liveness,
                   check_out_is_mock_location = :is_mock
            WHERE id = :id AND status = 'active'
            RETURNING id, status, actual_end, site_id, overtime_minutes
            """
        ),
        {
            "id": shift_id, "notes": handover_notes, "lat": latitude, "lon": longitude, "ot": overtime_minutes,
            "photo_path": photo_path, "liveness": liveness_score, "is_mock": is_mock_location,
        },
    )
    row = result.first()
    if row is None:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found or not active")
    row_dict = dict(row._mapping)
    site_id = str(row_dict.get("site_id") or "") or None
    # Arm before commit so app.current_tenant GUC is still set (RLS-safe)
    await arm_site_panels(db, site_id, shift_id, mode="away")
    await db.commit()
    await _publish_attendance_event(request, token.tenant_id, shift_id, "checked_out")
    for vid, vtype in new_violations:
        await _publish_violation_event(request, token.tenant_id, vid, vtype, shift_row.guard_user_id)
    return row_dict


@router.get("/{shift_id}/photo/{which}")
async def get_checkin_photo(
    shift_id: str,
    which: Literal["check_in", "check_out"],
    token: str = Query(..., description="JWT access token"),
):
    """Query-param-JWT auth (same pattern as streams.py's live/HLS endpoints)
    — a plain <img src> can't set an Authorization header, so the token
    travels in the URL instead of the standard get_db_with_tenant chain."""
    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))

    tenant_id = payload["tenant_id"]
    role_id = payload["role_id"]
    column = "check_in_photo_path" if which == "check_in" else "check_out_photo_path"

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tenant_id},
        )
        perm = await session.execute(
            text(
                "SELECT 1 FROM role_permissions rp "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE rp.role_id = :role_id AND p.code = 'attendance:read'"
            ),
            {"role_id": role_id},
        )
        if perm.first() is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: attendance:read")

        row = (await session.execute(
            text(f"SELECT {column} AS photo_path FROM shifts WHERE id = :id"), {"id": shift_id},
        )).first()

    if row is None or row.photo_path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found")
    file_path = Path(settings.ATTENDANCE_PHOTOS_ROOT) / row.photo_path
    if not file_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo file not found on disk")
    return FileResponse(str(file_path), media_type="image/jpeg")


# ── Breaks (ShiftSecure Phase 2A) ─────────────────────────────────────────────

@router.post("/{shift_id}/break/start", dependencies=[Depends(require_permission("shift:manage"))])
async def start_break(
    shift_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    shift_row = (await db.execute(
        text("SELECT status FROM shifts WHERE id = :id"), {"id": shift_id}
    )).first()
    if shift_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
    if shift_row.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Shift is not active")
    open_break = (await db.execute(
        text("SELECT id FROM shift_breaks WHERE shift_id = :id AND break_end IS NULL"),
        {"id": shift_id},
    )).first()
    if open_break is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A break is already in progress")

    result = await db.execute(
        text(
            "INSERT INTO shift_breaks (tenant_id, shift_id) "
            "VALUES (current_setting('app.current_tenant')::uuid, :sid) "
            "RETURNING id, break_start"
        ),
        {"sid": shift_id},
    )
    row = result.first()
    await db.commit()
    await _publish_attendance_event(request, token.tenant_id, shift_id, "on_break")
    return dict(row._mapping)


@router.post("/{shift_id}/break/end", dependencies=[Depends(require_permission("shift:manage"))])
async def end_break(
    shift_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    result = await db.execute(
        text(
            "UPDATE shift_breaks SET break_end = now() "
            "WHERE shift_id = :id AND break_end IS NULL "
            "RETURNING id, break_start, break_end"
        ),
        {"id": shift_id},
    )
    row = result.first()
    if row is None:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No break in progress for this shift")
    await db.commit()
    await _publish_attendance_event(request, token.tenant_id, shift_id, "checked_in")
    return dict(row._mapping)


@router.post("/{shift_id}/handover", dependencies=[Depends(require_permission("handover:create"))])
async def generate_handover(
    shift_id: str,
    incoming_guard_id: str | None = None,
    outgoing_notes: str | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Generate a shift handover report: summarises open incidents, alerts, patrol completion.

    Creates a shift_handovers row and returns the full summary. The outgoing guard
    triggers this at end of shift; the incoming guard_id is optional (set if known).
    """
    shift_row = (await db.execute(
        text("SELECT id, guard_user_id, site_id FROM shifts WHERE id = :id"),
        {"id": shift_id},
    )).first()
    if shift_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")

    site_id = shift_row.site_id

    # Count open incidents (site-scoped if shift has a site)
    site_filter_i = "AND c.site_id = :site_id" if site_id else ""
    open_incidents = (await db.execute(
        text(f"""
            SELECT COUNT(*)::int FROM incidents i
            LEFT JOIN cameras c ON c.id = i.camera_id
            WHERE i.status IN ('open','dispatched','en_route','on_scene','investigating')
            {site_filter_i}
        """),
        {"site_id": str(site_id)} if site_id else {},
    )).scalar()

    site_filter_a = "AND c.site_id = :site_id" if site_id else ""
    open_alerts = (await db.execute(
        text(f"""
            SELECT COUNT(*)::int FROM alerts a
            LEFT JOIN cameras c ON c.id = a.camera_id
            WHERE a.status = 'open'
            {site_filter_a}
        """),
        {"site_id": str(site_id)} if site_id else {},
    )).scalar()

    # Patrol completion stats for this shift
    patrol_stats = (await db.execute(
        text("""
            SELECT
                COUNT(DISTINCT ps.route_id)                             AS routes_total,
                COUNT(DISTINCT ps.id) FILTER (WHERE ps.status='completed') AS routes_completed,
                COUNT(cs.id)                                            AS checkpoints_scanned,
                (SELECT COUNT(*) FROM patrol_checkpoints pc2
                 JOIN patrol_routes pr2 ON pr2.id = pc2.route_id
                 JOIN patrol_sessions ps2 ON ps2.route_id = pr2.id
                 WHERE ps2.shift_id = :sid)                             AS checkpoints_total
            FROM patrol_sessions ps
            LEFT JOIN checkpoint_scans cs ON cs.session_id = ps.id
            WHERE ps.shift_id = :sid
        """),
        {"sid": shift_id},
    )).first()

    summary = {
        "shift_id": shift_id,
        "open_incidents": open_incidents,
        "open_alerts": open_alerts,
        "patrol_routes_completed": patrol_stats.routes_completed if patrol_stats else 0,
        "patrol_routes_total": patrol_stats.routes_total if patrol_stats else 0,
        "checkpoints_scanned": patrol_stats.checkpoints_scanned if patrol_stats else 0,
        "checkpoints_total": patrol_stats.checkpoints_total if patrol_stats else 0,
        "outgoing_notes": outgoing_notes,
    }

    result = await db.execute(
        text(
            "INSERT INTO shift_handovers "
            "(tenant_id, shift_id, outgoing_guard_id, incoming_guard_id, "
            " open_incidents_count, open_alerts_count, "
            " patrol_routes_completed, patrol_routes_total, "
            " checkpoints_scanned, checkpoints_total, outgoing_notes, summary_json) "
            "VALUES (current_setting('app.current_tenant')::uuid, :sid, :og, "
            "        :ig, :oi, :oa, :prc, :prt, :cs, :ct, :notes, CAST(:summary AS json)) "
            "RETURNING id, created_at"
        ),
        {
            "sid": shift_id, "og": token.user_id,
            "ig": incoming_guard_id, "oi": open_incidents, "oa": open_alerts,
            "prc": summary["patrol_routes_completed"], "prt": summary["patrol_routes_total"],
            "cs": summary["checkpoints_scanned"], "ct": summary["checkpoints_total"],
            "notes": outgoing_notes, "summary": json.dumps(summary),
        },
    )
    row = result.first()
    await db.commit()
    return {**summary, "id": str(row.id), "created_at": row.created_at.isoformat()}


@router.get("/{shift_id}/handover", dependencies=[Depends(require_permission("handover:read"))])
async def get_handover(shift_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """Retrieve the handover report for a shift."""
    row = (await db.execute(
        text("""
            SELECT h.*, ug.full_name AS outgoing_guard_name, ig.full_name AS incoming_guard_name
            FROM shift_handovers h
            LEFT JOIN users ug ON ug.id = h.outgoing_guard_id
            LEFT JOIN users ig ON ig.id = h.incoming_guard_id
            WHERE h.shift_id = :sid
            ORDER BY h.created_at DESC LIMIT 1
        """),
        {"sid": shift_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No handover found for this shift")
    return dict(row._mapping)


@router.get("/{shift_id}/briefing", dependencies=[Depends(require_permission("shift:read"))])
async def get_shift_briefing(shift_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """Pre-shift situational awareness briefing — aggregates active work permits,
    expected visitors, open alerts, pending deliveries, and the previous shift's
    handover notes so a guard has full context before or at the start of their shift."""
    shift_row = (await db.execute(
        text("""
            SELECT sh.id, sh.guard_user_id, sh.site_id, sh.status,
                   sh.scheduled_start, sh.scheduled_end,
                   sh.actual_start, sh.actual_end, sh.handover_notes,
                   u.full_name AS guard_name, u.email AS guard_email,
                   s.name AS site_name
            FROM shifts sh
            LEFT JOIN users u ON u.id = sh.guard_user_id
            LEFT JOIN sites s ON s.id = sh.site_id
            WHERE sh.id = :id
        """),
        {"id": shift_id},
    )).first()
    if shift_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")

    shift = dict(shift_row._mapping)
    site_id  = shift.get("site_id")
    # Window: prefer actual times, fall back to scheduled, then ±8 h from now.
    # Always Python datetime objects — asyncpg rejects str for timestamptz params.
    _now = datetime.now(timezone.utc)
    win_start: datetime = shift.get("actual_start") or shift.get("scheduled_start") or _now
    win_end:   datetime = shift.get("actual_end")   or shift.get("scheduled_end")   or (_now + timedelta(hours=8))

    # ── Active work permits ──────────────────────────────────────────────────
    site_filter_wp = "AND wp.site_id = CAST(:site_id AS uuid)" if site_id else ""
    work_permits = (await db.execute(
        text(f"""
            SELECT wp.id, wp.permit_number, wp.work_description, wp.work_type,
                   wp.requested_by_name, wp.workers_count, wp.vehicles_count,
                   wp.start_at, wp.end_at, wp.status, wp.safety_briefing_done,
                   c.company_name AS contractor_name
            FROM work_permits wp
            JOIN contractors c ON c.id = wp.contractor_id
            WHERE wp.status IN ('active', 'approved')
              AND wp.start_at <= :win_end
              AND wp.end_at   >= :win_start
              {site_filter_wp}
            ORDER BY wp.start_at
            LIMIT 20
        """),
        {"win_start": win_start, "win_end": win_end,
         **( {"site_id": str(site_id)} if site_id else {} )},
    )).mappings().all()

    # ── Expected visitors ────────────────────────────────────────────────────
    site_filter_v = "AND v.site_id = CAST(:site_id AS uuid)" if site_id else ""
    expected_visitors = (await db.execute(
        text(f"""
            SELECT v.id, v.full_name, v.company, v.host_name, v.purpose,
                   v.vehicle_plate, v.expected_from, v.expected_until,
                   v.status, v.visitor_email, v.qr_token
            FROM visitors v
            WHERE v.status IN ('pending', 'arrived')
              AND v.is_active = TRUE
              AND (
                  v.expected_from::date = CURRENT_DATE
                  OR (
                      v.expected_from <= :win_end
                      AND COALESCE(v.expected_until, v.expected_from + interval '4 hours')
                              >= :win_start
                  )
              )
              {site_filter_v}
            ORDER BY v.expected_from NULLS LAST
            LIMIT 30
        """),
        {"win_start": win_start, "win_end": win_end,
         **( {"site_id": str(site_id)} if site_id else {} )},
    )).mappings().all()

    # ── Open alerts at this site ─────────────────────────────────────────────
    site_filter_a = "AND cam.site_id = CAST(:site_id AS uuid)" if site_id else ""
    open_alerts = (await db.execute(
        text(f"""
            SELECT a.id, a.module_type, a.severity, a.title, a.status,
                   a.created_at, a.alert_code,
                   cam.name AS camera_name
            FROM alerts a
            LEFT JOIN cameras cam ON cam.id = a.camera_id
            WHERE a.status = 'open'
            {site_filter_a}
            ORDER BY a.created_at DESC
            LIMIT 15
        """),
        {"site_id": str(site_id)} if site_id else {},
    )).mappings().all()

    # ── Pending deliveries at this site ─────────────────────────────────────
    site_filter_d = "AND d.site_id = CAST(:site_id AS uuid)" if site_id else ""
    pending_deliveries = (await db.execute(
        text(f"""
            SELECT d.id, d.tracking_number, d.carrier, d.sender_name,
                   d.sender_company, d.recipient_name, d.recipient_department,
                   d.description, d.expected_at, d.status
            FROM deliveries d
            WHERE d.status IN ('pending', 'received')
            {site_filter_d}
            ORDER BY d.expected_at NULLS LAST
            LIMIT 20
        """),
        {"site_id": str(site_id)} if site_id else {},
    )).mappings().all()

    # ── Previous shift handover for the same site ────────────────────────────
    site_filter_h = "AND sh2.site_id = CAST(:site_id AS uuid)" if site_id else ""
    prev_handover_row = (await db.execute(
        text(f"""
            SELECT h.outgoing_notes, h.open_incidents_count, h.open_alerts_count,
                   h.patrol_routes_completed, h.patrol_routes_total,
                   h.checkpoints_scanned, h.checkpoints_total, h.created_at,
                   ug.full_name AS outgoing_guard_name
            FROM shift_handovers h
            JOIN shifts sh2 ON sh2.id = h.shift_id
            LEFT JOIN users ug ON ug.id = h.outgoing_guard_id
            WHERE h.shift_id != :current_shift_id
            {site_filter_h}
            ORDER BY h.created_at DESC
            LIMIT 1
        """),
        {"current_shift_id": shift_id, **( {"site_id": str(site_id)} if site_id else {} )},
    )).first()

    # ── Alarm panel status at this site ─────────────────────────────────────
    site_filter_ap = "AND ap.site_id = CAST(:site_id AS uuid)" if site_id else ""
    alarm_panels_rows = (await db.execute(
        text(f"""
            SELECT ap.id, ap.name, ap.arm_state, ap.status, ap.is_active
            FROM alarm_panels ap
            WHERE ap.is_active = TRUE
            {site_filter_ap}
            ORDER BY ap.name
        """),
        {"site_id": str(site_id)} if site_id else {},
    )).mappings().all()

    return {
        "shift": shift,
        "active_work_permits": [dict(r) for r in work_permits],
        "expected_visitors":   [dict(r) for r in expected_visitors],
        "open_alerts":         [dict(r) for r in open_alerts],
        "pending_deliveries":  [dict(r) for r in pending_deliveries],
        "previous_handover":   dict(prev_handover_row._mapping) if prev_handover_row else None,
        "alarm_panels":        [dict(r) for r in alarm_panels_rows],
        "summary": {
            "work_permits_count":   len(work_permits),
            "visitors_count":       len(expected_visitors),
            "open_alerts_count":    len(open_alerts),
            "deliveries_count":     len(pending_deliveries),
            "has_previous_handover": prev_handover_row is not None,
            "panels_armed":         sum(1 for r in alarm_panels_rows if r["arm_state"] != "disarmed"),
            "panels_total":         len(alarm_panels_rows),
        },
    }


# ── Shift definitions ───────────────────────────────────────────────────────
#
# The named shifts a company runs — Day Shift, Night Shift, and whatever else
# — held once and pointed at, rather than restated as a start time and a
# duration on every pattern.
#
# Stored as start + duration. The UI works in start and end because that is
# how people describe a shift, but an end time cannot distinguish a zero-hour
# shift from a 24-hour one, so the conversion happens here where the rule can
# be stated once.

SHIFT_TYPES = ("day", "night", "general", "split")


class ShiftDefinitionBase(BaseModel):
    name: str
    shift_type: str = "day"
    start_time: str            # "HH:MM"
    end_time: str              # "HH:MM" — converted to a duration on the way in
    grace_minutes: int | None = None
    break_minutes: int = 0
    ot_eligible: bool = False
    colour: str | None = None
    notes: str | None = None


class ShiftDefinitionCreate(ShiftDefinitionBase):
    pass


class ShiftDefinitionUpdate(BaseModel):
    name: str | None = None
    shift_type: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    grace_minutes: int | None = None
    break_minutes: int | None = None
    ot_eligible: bool | None = None
    colour: str | None = None
    notes: str | None = None
    is_active: bool | None = None


def _parse_hhmm(value: str, field: str) -> int:
    """Minutes since midnight, or a 422 naming the field that was wrong."""
    try:
        hh, mm = value.strip().split(":")[:2]
        h, m = int(hh), int(mm)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except Exception:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{field} must be a 24-hour time as HH:MM",
        )
    return h * 60 + m


def _hhmm_to_time(value: str, field: str) -> dtime:
    """asyncpg binds parameters by inferred type before any CAST in the SQL
    runs, so a "07:00" string is rejected with "str object has no attribute
    hour". It has to arrive as a real time object."""
    minutes = _parse_hhmm(value, field)
    return dtime(minutes // 60, minutes % 60)


def _duration_from(start: str, end: str) -> int:
    """Minutes from start to end, wrapping past midnight.

    Equal times mean a full 24 hours, not zero. A zero-length shift is never
    what anyone meant, and 24-hour cover shifts are real.
    """
    s = _parse_hhmm(start, "start_time")
    e = _parse_hhmm(end, "end_time")
    span = (e - s) % (24 * 60)
    return span or 24 * 60


def _shift_definition_row(row) -> dict:
    """Add the fields the UI needs but the table should not store twice."""
    d = dict(row._mapping)
    start = d["start_time"]
    total = start.hour * 60 + start.minute + d["duration_minutes"]
    d["start_time"] = f"{start.hour:02d}:{start.minute:02d}"
    d["end_time"] = f"{(total // 60) % 24:02d}:{total % 60:02d}"
    # True when the shift finishes on a later calendar day — what the grid
    # prints as a "+1" beside the end time.
    d["crosses_midnight"] = total >= 24 * 60
    d["duration_hours"] = round(d["duration_minutes"] / 60, 2)
    return d


@router.get("/definitions", dependencies=[Depends(require_permission("shift:read"))])
async def list_shift_definitions(
    db: AsyncSession = Depends(get_db_with_tenant),
    include_inactive: bool = False,
):
    where = "" if include_inactive else "WHERE is_active = TRUE"
    result = await db.execute(
        text(f"""
            SELECT id, name, shift_type, start_time, duration_minutes, grace_minutes,
                   break_minutes, ot_eligible, colour, notes, is_active,
                   created_at, updated_at
              FROM shift_definitions
            {where}
          ORDER BY is_active DESC, start_time, name
        """)
    )
    return [_shift_definition_row(r) for r in result]


@router.post("/definitions", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("shift:manage"))])
async def create_shift_definition(
    body: ShiftDefinitionCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if body.shift_type not in SHIFT_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"shift_type must be one of {list(SHIFT_TYPES)}")
    if not body.name.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name is required")
    duration = _duration_from(body.start_time, body.end_time)
    if body.break_minutes >= duration:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "break_minutes must be shorter than the shift itself",
        )

    try:
        row = (await db.execute(
            text("""
                INSERT INTO shift_definitions
                    (tenant_id, name, shift_type, start_time, duration_minutes,
                     grace_minutes, break_minutes, ot_eligible, colour, notes,
                     created_by_user_id)
                VALUES (current_setting('app.current_tenant')::uuid,
                        :name, :stype, :start, :dur,
                        :grace, :brk, :ot, :colour, :notes, CAST(:uid AS uuid))
                RETURNING id, name, shift_type, start_time, duration_minutes,
                          grace_minutes, break_minutes, ot_eligible, colour, notes,
                          is_active, created_at, updated_at
            """),
            {
                "name": body.name.strip(), "stype": body.shift_type,
                "start": _hhmm_to_time(body.start_time, "start_time"), "dur": duration,
                "grace": body.grace_minutes, "brk": body.break_minutes,
                "ot": body.ot_eligible, "colour": body.colour,
                "notes": body.notes, "uid": token.user_id,
            },
        )).first()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"A shift called '{body.name.strip()}' already exists")
    await db.commit()
    return _shift_definition_row(row)


@router.put("/definitions/{definition_id}",
            dependencies=[Depends(require_permission("shift:manage"))])
async def update_shift_definition(
    definition_id: str,
    body: ShiftDefinitionUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    existing = (await db.execute(
        text("SELECT start_time, duration_minutes FROM shift_definitions "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": definition_id},
    )).first()
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")

    sets, params = [], {"id": definition_id}
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name cannot be blank")
        sets.append("name = :name"); params["name"] = body.name.strip()
    if body.shift_type is not None:
        if body.shift_type not in SHIFT_TYPES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"shift_type must be one of {list(SHIFT_TYPES)}")
        sets.append("shift_type = :stype"); params["stype"] = body.shift_type

    # Times are recomputed together: changing only one end of a shift still
    # changes its length, so the stored duration has to be derived from
    # whichever half was not sent.
    if body.start_time is not None or body.end_time is not None:
        cur_start = f"{existing.start_time.hour:02d}:{existing.start_time.minute:02d}"
        cur_total = existing.start_time.hour * 60 + existing.start_time.minute + existing.duration_minutes
        cur_end = f"{(cur_total // 60) % 24:02d}:{cur_total % 60:02d}"
        start = body.start_time or cur_start
        end = body.end_time or cur_end
        sets.append("start_time = :start"); params["start"] = _hhmm_to_time(start, "start_time")
        sets.append("duration_minutes = :dur"); params["dur"] = _duration_from(start, end)

    # grace_minutes keys off model_fields_set: null is a real value here,
    # meaning "fall back to the site's grace", and is the only way to clear an
    # override once set.
    if "grace_minutes" in body.model_fields_set:
        sets.append("grace_minutes = :grace"); params["grace"] = body.grace_minutes
    if body.break_minutes is not None:
        sets.append("break_minutes = :brk"); params["brk"] = body.break_minutes
    if body.ot_eligible is not None:
        sets.append("ot_eligible = :ot"); params["ot"] = body.ot_eligible
    if "colour" in body.model_fields_set:
        sets.append("colour = :colour"); params["colour"] = body.colour
    if "notes" in body.model_fields_set:
        sets.append("notes = :notes"); params["notes"] = body.notes
    if body.is_active is not None:
        sets.append("is_active = :active"); params["active"] = body.is_active

    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")

    try:
        row = (await db.execute(
            text(f"""
                UPDATE shift_definitions SET {', '.join(sets)}, updated_at = now()
                 WHERE id = CAST(:id AS uuid)
                RETURNING id, name, shift_type, start_time, duration_minutes,
                          grace_minutes, break_minutes, ot_eligible, colour, notes,
                          is_active, created_at, updated_at
            """),
            params,
        )).first()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A shift with that name already exists")
    await db.commit()
    return _shift_definition_row(row)


@router.delete("/definitions/{definition_id}",
               dependencies=[Depends(require_permission("shift:manage"))])
async def delete_shift_definition(
    definition_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Retire a shift, or delete it outright if nothing ever used it.

    A definition already referenced by a pattern or a rostered shift is
    deactivated rather than removed: deleting it would strip the name off
    historical rosters and leave a report unable to say what shift someone
    worked. One nobody has used yet is genuinely deleted, because a list of
    retired mistakes helps no one.
    """
    in_use = (await db.execute(
        text("""
            SELECT (SELECT count(*) FROM shift_patterns WHERE shift_definition_id = CAST(:id AS uuid))
                 + (SELECT count(*) FROM shifts WHERE shift_definition_id = CAST(:id AS uuid)) AS n
        """),
        {"id": definition_id},
    )).scalar_one()

    if in_use:
        row = (await db.execute(
            text("UPDATE shift_definitions SET is_active = FALSE, updated_at = now() "
                 "WHERE id = CAST(:id AS uuid) RETURNING id"),
            {"id": definition_id},
        )).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
        await db.commit()
        return {"id": definition_id, "retired": True, "in_use_by": int(in_use)}

    row = (await db.execute(
        text("DELETE FROM shift_definitions WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": definition_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
    await db.commit()
    return {"id": definition_id, "deleted": True}


@router.put("/{shift_id}", dependencies=[Depends(require_permission("shift:manage"))])
async def update_shift(shift_id: str, body: ShiftUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    """Manual assign/edit for an already-published shift (ShiftSecure Phase
    2B) — reassign the guard, adjust the time, or update handover notes."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = shift_id
    result = await db.execute(
        text(f"UPDATE shifts SET {set_clause}, updated_at = now() WHERE id = :id "
             "RETURNING id, guard_user_id, site_id, scheduled_start, scheduled_end, status"),
        updates,
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
    await db.commit()
    return dict(row._mapping)


@router.get("/{shift_id}", dependencies=[Depends(require_permission("shift:read"))])
async def get_shift(shift_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text(
            """
            SELECT sh.*, u.full_name AS guard_name, s.name AS site_name
            FROM shifts sh
            LEFT JOIN users u ON u.id = sh.guard_user_id
            LEFT JOIN sites s ON s.id = sh.site_id
            WHERE sh.id = :id
            """
        ),
        {"id": shift_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
    return dict(row._mapping)


# ── SOS Panic ─────────────────────────────────────────────────────────────────

class SOSBody(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    site_id: str | None = None
    description: str | None = None


@router.post("/sos", dependencies=[Depends(require_permission("guard:sos"))])
async def trigger_sos(
    body: SOSBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Guard panic button.

    Writes the occurrence book entry, raises an incident where the tenant has a
    camera to hang one on, and pushes a live SOS to supervisors. The work is in
    services/sos.py so this and POST /patrols/sos cannot drift apart again —
    they used to do different halves of it.
    """
    result = await raise_guard_sos(
        db,
        getattr(request.app.state, "redis", None),
        tenant_id=token.tenant_id,
        user_id=token.user_id,
        latitude=body.latitude,
        longitude=body.longitude,
        description=body.description,
        site_id=body.site_id,
    )

    # Response shape unchanged — the mobile app reads these four keys.
    # incident_id is additive, and is None when the tenant has no camera to
    # attach one to; the occurrence entry and the live push happened either way.
    return {
        "sos_triggered": True,
        "entry_id": result["entry_id"],
        "occurred_at": result["occurred_at"],
        "message": result["message"],
        "incident_id": result["incident_id"],
    }
