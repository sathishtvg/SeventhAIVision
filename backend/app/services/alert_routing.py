"""Site+shift-aware alert routing (Gap 82).

Answers: "which users should be pushed for this alert?"

Routing order for an alert on camera C:
  1. C has no site (or no camera at all)      → None  (tenant-wide fallback)
  2. Site S = C.site_id. Targets =
       guards with an ACTIVE shift at S
       ∪ users assigned to S via user_sites with an operational role
         (3 supervisor / 4 operator / 5 security_guard)
  3. Targets empty                             → None  (tenant-wide fallback)

None always means "fall back to the tenant-wide push set" — an alert must
never be silently dropped because nobody is rostered.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Roles that receive site-routed operational pushes when assigned to the site.
_OPERATIONAL_ROLES = (3, 4, 5)


async def resolve_alert_site_id(
    session: AsyncSession, camera_id: str | None
) -> str | None:
    """Camera → site_id, or None when the camera has no site / doesn't exist.
    Caller must have already set the tenant GUC on the session."""
    if not camera_id:
        return None
    row = (await session.execute(
        text("SELECT site_id FROM cameras WHERE id = CAST(:cid AS uuid)"),
        {"cid": camera_id},
    )).first()
    if row is None or row.site_id is None:
        return None
    return str(row.site_id)


async def resolve_push_targets(
    session: AsyncSession, camera_id: str | None
) -> list[str] | None:
    """Return user_ids to push for an alert on this camera, or None for
    tenant-wide fallback. Caller must have set the tenant GUC."""
    site_id = await resolve_alert_site_id(session, camera_id)
    if site_id is None:
        return None

    result = await session.execute(
        text("""
            SELECT sh.guard_user_id AS user_id
            FROM shifts sh
            WHERE sh.status = 'active' AND sh.site_id = CAST(:sid AS uuid)
            UNION
            SELECT us.user_id
            FROM user_sites us
            JOIN users u ON u.id = us.user_id
            WHERE us.site_id = CAST(:sid AS uuid)
              AND u.is_active = TRUE
              AND u.role_id = ANY(:roles)
        """),
        {"sid": site_id, "roles": list(_OPERATIONAL_ROLES)},
    )
    targets = [str(r.user_id) for r in result if r.user_id is not None]
    return targets or None
