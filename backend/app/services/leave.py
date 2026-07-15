"""Leave day-count helper + default leave-type seeding (ShiftSecure Phase 4)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_DEFAULT_LEAVE_TYPES = [
    ("Annual Leave", 14, False),
    ("Medical Leave", 14, True),
    ("Compassionate Leave", 3, False),
    ("Unpaid Leave", 0, False),
]


def compute_days_count(start_date: date, end_date: date) -> int:
    """Inclusive calendar-day count — no weekend/holiday awareness, matching
    guard_leave_blocks' existing date-range simplicity."""
    return (end_date - start_date).days + 1


async def seed_default_leave_types(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Mirrors licenses.py::seed_tenant_licenses — inserts the same 4 default
    leave types migration 0066 backfills for existing tenants."""
    for name, days, requires_doc in _DEFAULT_LEAVE_TYPES:
        await db.execute(
            text(
                "INSERT INTO leave_types (tenant_id, name, default_annual_days, requires_document) "
                "VALUES (:tid, :name, :days, :doc) "
                "ON CONFLICT (tenant_id, name) DO NOTHING"
            ),
            {"tid": tenant_id, "name": name, "days": days, "doc": requires_doc},
        )
