"""Alarm arm/disarm automation tied to shift lifecycle.

Called from shifts.py when a shift starts or ends.  Site-scoped: only panels
whose site_id matches the shift's site_id are affected.  If the shift has no
site_id this module is a no-op (tenant-wide shifts have no site to target).
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def disarm_site_panels(
    db: AsyncSession,
    site_id: str | None,
    shift_id: str,
    triggered_by: str = "shift_start",
) -> int:
    """Disarm all active alarm panels at the given site.

    Returns the number of panels updated.  Called when a shift starts so guards
    can patrol without triggering false alarms.
    """
    if not site_id:
        return 0

    result = await db.execute(
        text(
            "SELECT id FROM alarm_panels "
            "WHERE site_id = CAST(:site_id AS uuid) "
            "AND is_active = TRUE "
            "AND arm_state != 'disarmed'"
        ),
        {"site_id": site_id},
    )
    panels = [str(row.id) for row in result]
    if not panels:
        return 0

    await db.execute(
        text(
            "UPDATE alarm_panels SET arm_state = 'disarmed', updated_at = now() "
            "WHERE site_id = CAST(:site_id AS uuid) AND is_active = TRUE"
        ),
        {"site_id": site_id},
    )

    for panel_id in panels:
        await db.execute(
            text(
                "INSERT INTO alarm_events "
                "(tenant_id, panel_id, event_type, severity, description) "
                "VALUES (current_setting('app.current_tenant')::uuid, "
                "CAST(:pid AS uuid), 'panel_disarmed', 'info', :desc)"
            ),
            {
                "pid": panel_id,
                "desc": f"Auto-disarmed by shift start (shift_id={shift_id})",
            },
        )

    logger.info(
        "alarm_shift.disarm site_id=%s shift_id=%s panels_disarmed=%d",
        site_id, shift_id, len(panels),
    )
    return len(panels)


async def arm_site_panels(
    db: AsyncSession,
    site_id: str | None,
    shift_id: str,
    mode: str = "away",
) -> int:
    """Arm all active alarm panels at the given site.

    Returns the number of panels updated.  Called when a shift ends so the site
    is automatically protected after guards leave.
    """
    if not site_id:
        return 0

    if mode not in ("away", "stay", "night"):
        mode = "away"
    arm_state = f"armed_{mode}"
    event_type = f"panel_armed_{mode}"

    result = await db.execute(
        text(
            "SELECT id FROM alarm_panels "
            "WHERE site_id = CAST(:site_id AS uuid) "
            "AND is_active = TRUE"
        ),
        {"site_id": site_id},
    )
    panels = [str(row.id) for row in result]
    if not panels:
        return 0

    await db.execute(
        text(
            "UPDATE alarm_panels "
            "SET arm_state = :arm_state, updated_at = now() "
            "WHERE site_id = CAST(:site_id AS uuid) AND is_active = TRUE"
        ),
        {"arm_state": arm_state, "site_id": site_id},
    )

    for panel_id in panels:
        await db.execute(
            text(
                "INSERT INTO alarm_events "
                "(tenant_id, panel_id, event_type, severity, description) "
                "VALUES (current_setting('app.current_tenant')::uuid, "
                "CAST(:pid AS uuid), :etype, 'info', :desc)"
            ),
            {
                "pid": panel_id,
                "etype": event_type,
                "desc": f"Auto-armed ({mode}) by shift end (shift_id={shift_id})",
            },
        )

    logger.info(
        "alarm_shift.arm site_id=%s shift_id=%s mode=%s panels_armed=%d",
        site_id, shift_id, mode, len(panels),
    )
    return len(panels)
