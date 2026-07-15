"""Violation point values + shared insert helper (ShiftSecure Phase 3).

Point values are a fixed constant rather than a tenant_settings key —
config_keys.py's SETTING_VALIDATORS only validates scalars, and a
per-type points dict doesn't fit that shape for a single feature. Easy to
make configurable later if asked.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

VIOLATION_POINTS = {
    "no_show": 10,
    "late_checkin": 3,
    "geofence_failure": 2,
    "early_departure": 5,
    "manual": 0,  # admin sets custom points explicitly on manual entries
}


async def create_violation(
    db: AsyncSession,
    tenant_id: str,
    guard_user_id: str,
    violation_type: str,
    *,
    shift_id: str | None = None,
    site_id: str | None = None,
    description: str | None = None,
    points: int | None = None,
    is_auto_generated: bool = False,
    reported_by_user_id: str | None = None,
) -> str | None:
    """Inserts a violations row. Auto-generated violations are deduped
    against any existing violation of the same type for the same shift
    (regardless of its review status) — prevents a single late check-in or
    a repeatedly-run no-show scheduler pass from producing duplicate rows.
    Returns the new violation id, or None if deduped."""
    if is_auto_generated and shift_id is not None:
        existing = (await db.execute(
            text(
                "SELECT 1 FROM violations WHERE shift_id = :sid AND violation_type = :vt"
            ),
            {"sid": shift_id, "vt": violation_type},
        )).first()
        if existing is not None:
            return None

    row = (await db.execute(
        text("""
            INSERT INTO violations (
                tenant_id, guard_user_id, shift_id, site_id, violation_type,
                description, points, is_auto_generated, reported_by_user_id
            ) VALUES (
                :tid, :guard, :shift, :site, :vtype,
                :desc, :points, :auto, :reporter
            )
            RETURNING id
        """),
        {
            "tid": tenant_id,
            "guard": guard_user_id,
            "shift": shift_id,
            "site": site_id,
            "vtype": violation_type,
            "desc": description,
            "points": points if points is not None else VIOLATION_POINTS.get(violation_type, 0),
            "auto": is_auto_generated,
            "reporter": reported_by_user_id,
        },
    )).first()
    return str(row.id) if row is not None else None
