"""Roster generation (Gap 86) — expand recurring shift_patterns into
concrete shifts rows.

Idempotent by construction: every generated shift carries its pattern_id and
a unique (pattern_id, scheduled_start) index turns re-runs into no-ops via
ON CONFLICT DO NOTHING. Called manually from POST /shifts/roster/generate
and daily from the scheduler for every active tenant.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MAX_DAYS_AHEAD = 31
DEFAULT_DAYS_AHEAD = 7

_GENERATE_SQL = text("""
    INSERT INTO shifts (tenant_id, guard_user_id, site_id,
                        scheduled_start, scheduled_end, status, pattern_id)
    SELECT p.tenant_id, p.guard_user_id, p.site_id,
           ((d.day + p.start_time) AT TIME ZONE COALESCE(t.timezone, 'UTC')),
           ((d.day + p.start_time) AT TIME ZONE COALESCE(t.timezone, 'UTC'))
               + make_interval(mins => p.duration_minutes),
           'scheduled', p.id
    FROM shift_patterns p
    JOIN tenants t ON t.id = p.tenant_id
    CROSS JOIN LATERAL (
        SELECT generate_series(
            CURRENT_DATE, CURRENT_DATE + (:days - 1), interval '1 day'
        )::date AS day
    ) d
    WHERE p.is_active = TRUE
      AND (EXTRACT(ISODOW FROM d.day)::int - 1) = ANY(p.days_of_week)
    ON CONFLICT (pattern_id, scheduled_start) DO NOTHING
""")


def clamp_days_ahead(days: int) -> int:
    """Pure: bound the generation window to [1, MAX_DAYS_AHEAD]."""
    return max(1, min(int(days), MAX_DAYS_AHEAD))


async def generate_roster_shifts(session: AsyncSession, days_ahead: int = DEFAULT_DAYS_AHEAD) -> int:
    """Expand the current tenant's active patterns over the next N days.
    Tenant GUC must be set. Returns the number of shifts created. Commits,
    then restores the GUC so the caller's session stays usable."""
    days = clamp_days_ahead(days_ahead)
    result = await session.execute(_GENERATE_SQL, {"days": days})
    created = result.rowcount or 0
    tid = (await session.execute(
        text("SELECT current_setting('app.current_tenant', true)")
    )).scalar()
    await session.commit()
    if tid:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tid}
        )
    return created


async def generate_roster_for_all_tenants(session_factory, days_ahead: int = DEFAULT_DAYS_AHEAD) -> int:
    """Daily scheduler entry point: generate for every active tenant."""
    total = 0
    async with session_factory() as tenants_session:
        tenant_rows = (await tenants_session.execute(
            text("SELECT id FROM tenants WHERE is_active = TRUE")
        )).all()
    for (tenant_id,) in tenant_rows:
        try:
            async with session_factory() as session:
                await session.execute(
                    text("SELECT set_config('app.current_tenant', :tid, true)"),
                    {"tid": str(tenant_id)},
                )
                created = await generate_roster_shifts(session, days_ahead)
                total += created
                if created:
                    logger.info("roster: generated %d shifts tenant=%s", created, tenant_id)
        except Exception as exc:
            logger.error("roster: generation failed tenant=%s: %s", tenant_id, exc)
    return total
