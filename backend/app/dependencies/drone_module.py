"""The Drone Patrol licence gate, and the limits that come with the licence.

WHY THIS READS drone_module_licenses. The obvious home for a module licence is
tenant_module_licenses, and it is unsafe: /licenses/me/enabled-modules and
cameras._check_module_licenses both treat a tenant with no licence rows as
licensed for every AI module. One drone row would switch that fallback off and
take all eleven away. See DRONE_PATROL_GAP_ANALYSIS.md §19.1.

WRITES ARE GATED; READING HISTORY AND HANDLING EVENTS ARE NOT. Without the
module a tenant cannot register a drone, plan or change a mission, or fly one.
It can still read what already happened and close out the events it raised: a
licence that lapses at midnight must not lock an operator out of an intrusion
the drone reported at 23:58, and footage that is evidence does not stop being
evidence because an invoice went unpaid.

LIMITS ARE COUNTED UNDER A LOCK. Two administrators registering the last
licensed drone at the same moment would both see a free slot. A transaction-level
advisory lock per tenant makes the count and the insert one step; it is released
at commit.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.tenant import get_db_with_tenant

MODULE = "drone_patrol"


async def load_entitlement(db: AsyncSession) -> dict | None:
    """This tenant's licence row, or None. RLS scopes the read to the tenant,
    and tenant_id is UNIQUE, so there is at most one row."""
    row = (await db.execute(text(
        "SELECT * FROM drone_module_licenses "
        " WHERE tenant_id = current_setting('app.current_tenant')::uuid"
    ))).mappings().first()
    return dict(row) if row else None


def entitlement_problem(ent: dict | None, now: datetime) -> str | None:
    """Why the module is unavailable, in words an administrator can act on —
    or None when it is available."""
    if ent is None or not ent["is_enabled"]:
        return ("Drone Patrol is not licensed for this organisation. "
                "Ask your platform administrator to enable it.")
    expires = ent.get("expires_at")
    if expires is not None and expires <= now:
        return (f"The Drone Patrol licence expired on {expires:%d %b %Y}. "
                "Ask your platform administrator to renew it.")
    return None


async def require_drone_module(db: AsyncSession = Depends(get_db_with_tenant)) -> dict:
    """Dependency for every endpoint that creates, changes or flies something."""
    ent = await load_entitlement(db)
    problem = entitlement_problem(ent, datetime.now(timezone.utc))
    if problem:
        raise HTTPException(status.HTTP_403_FORBIDDEN, problem)
    return ent  # type: ignore[return-value]


async def _lock_limits(db: AsyncSession) -> None:
    await db.execute(text(
        "SELECT pg_advisory_xact_lock(hashtext('drone_limits:' || current_setting('app.current_tenant')))"
    ))


async def usage(db: AsyncSession) -> dict:
    """What counts against the licence. A DISABLED drone does not: disabling is
    how an administrator frees a slot without losing the drone's history."""
    row = (await db.execute(text("""
        SELECT (SELECT count(*) FROM drones WHERE status <> 'DISABLED') AS drones,
               (SELECT count(*) FROM drone_missions)                     AS missions,
               (SELECT count(*) FROM (
                    SELECT site_id FROM drones
                     WHERE site_id IS NOT NULL AND status <> 'DISABLED'
                    UNION
                    SELECT site_id FROM drone_missions
               ) s)                                                      AS sites
    """))).mappings().first()
    return dict(row)


async def _sites_in_use(db: AsyncSession) -> set[str]:
    rows = (await db.execute(text("""
        SELECT site_id FROM drones WHERE site_id IS NOT NULL AND status <> 'DISABLED'
        UNION
        SELECT site_id FROM drone_missions
    """))).scalars().all()
    return {str(r) for r in rows}


def _refuse(message: str) -> None:
    raise HTTPException(status.HTTP_403_FORBIDDEN, message)


async def enforce_drone_limit(db: AsyncSession, ent: dict) -> None:
    """Call before adding (or re-enabling) a drone."""
    limit = ent.get("max_drones")
    if limit is None:
        return
    await _lock_limits(db)
    n = (await db.execute(text("SELECT count(*) FROM drones WHERE status <> 'DISABLED'"))).scalar()
    if n >= limit:
        _refuse(f"This licence allows {limit} active drone(s) and {n} are registered. "
                "Disable one, or ask your platform administrator to raise the limit.")


async def enforce_mission_limit(db: AsyncSession, ent: dict) -> None:
    limit = ent.get("max_missions")
    if limit is None:
        return
    await _lock_limits(db)
    n = (await db.execute(text("SELECT count(*) FROM drone_missions"))).scalar()
    if n >= limit:
        _refuse(f"This licence allows {limit} mission(s) and {n} exist. "
                "Delete one, or ask your platform administrator to raise the limit.")


async def enforce_site_limit(db: AsyncSession, ent: dict, site_id) -> None:
    """Call before a drone or mission is placed on a site. A site already in
    use costs nothing more."""
    limit = ent.get("max_sites")
    if limit is None or site_id is None:
        return
    await _lock_limits(db)
    used = await _sites_in_use(db)
    if str(site_id) in used:
        return
    if len(used) >= limit:
        _refuse(f"This licence covers {limit} site(s) and all are in use. "
                "Ask your platform administrator to raise the limit.")
