"""Per-site recording policy (Phase X-A).

One question this module answers, and it is the whole reason it exists:
*for a given site, how long do we keep footage, and where?*

Until now that answer was one number for the entire tenant
(`recording.retention_days`). That is fine for one customer with one site
and wrong the moment a security company runs a bank branch that keeps 90
days and a construction gate that keeps 7. Retention is a contractual term
per client, so it belongs per site with the tenant setting as the fallback
for sites nobody has configured yet.

Resolution order, everywhere in this module:

    site policy column  ->  tenant setting  ->  env/code default

A NULL column is "inherit", not "zero" — that distinction is why the
retention columns are nullable rather than defaulted. `0` is a legitimate,
different answer meaning "keep nothing centrally", which a local-only site
genuinely wants.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Mirrors the CHECK constraints in migration 0079. Duplicated here so the API
# rejects a bad value with a 422 naming the valid options, instead of letting
# Postgres raise a constraint violation the caller can't act on.
RECORD_MODES = ("continuous", "motion", "ai_event", "scheduled", "off")
SYNC_MODES = ("central", "local_only", "incident_only", "scheduled", "manual")
COMPRESSIONS = ("none", "h264", "h265")

POLICY_COLUMNS = """
    id, tenant_id, site_id, record_mode, sync_mode,
    local_retention_days, central_retention_days,
    sync_window_start, sync_window_end, bandwidth_limit_kbps,
    clip_pre_seconds, clip_post_seconds,
    compression, encrypt_archives, verify_checksums,
    is_active, updated_by_user_id, created_at, updated_at
"""


async def get_policy(session: AsyncSession, site_id: str) -> dict[str, Any] | None:
    """The site's policy row, or None when it has never been configured.

    None is a normal, expected answer — it means "this site inherits
    everything", which is true of every site until an admin sets one.
    """
    row = (
        await session.execute(
            text(f"SELECT {POLICY_COLUMNS} FROM recording_policies "
                 f"WHERE site_id = CAST(:sid AS uuid)"),
            {"sid": site_id},
        )
    ).first()
    return dict(row._mapping) if row is not None else None


async def get_tenant_retention_days(session: AsyncSession, default: int) -> int:
    """The tenant-wide `recording.retention_days` setting, or `default`.

    Kept byte-identical in behaviour to continuous_recording.get_retention_days
    (which now delegates here) so moving this logic changed nothing about how
    an unconfigured tenant behaves.
    """
    row = (
        await session.execute(
            text("SELECT setting_value FROM tenant_settings "
                 "WHERE setting_key = 'recording.retention_days'")
        )
    ).first()
    if row is not None and isinstance(row[0], int) and row[0] >= 1:
        return row[0]
    return default


async def get_effective_retention_days(
    session: AsyncSession, site_id: str | None, default: int
) -> int:
    """How many days central storage keeps this site's footage.

    `site_id=None` (a recording not attached to any site) resolves to the
    tenant setting — the pre-X-A behaviour, unchanged.
    """
    if site_id is not None:
        policy = await get_policy(session, site_id)
        if policy is not None and policy["central_retention_days"] is not None:
            return int(policy["central_retention_days"])
    return await get_tenant_retention_days(session, default)


async def get_effective_clip_bounds(
    session: AsyncSession, site_id: str | None, default_pre: int, default_post: int
) -> tuple[int, int]:
    """(pre_seconds, post_seconds) for event clips at this site.

    Unlike retention, these columns are NOT NULL with defaults, so a policy
    row always answers definitively; only the absence of a row falls back.
    """
    if site_id is not None:
        policy = await get_policy(session, site_id)
        if policy is not None:
            return int(policy["clip_pre_seconds"]), int(policy["clip_post_seconds"])
    return default_pre, default_post


async def should_record(session: AsyncSession, site_id: str | None) -> bool:
    """Whether the continuous supervisor should keep a segment running here.

    Only `record_mode='off'` and `'ai_event'` stop continuous capture:
    'motion' and 'scheduled' still need a continuous pipeline underneath them
    (neither motion gating nor time-window gating is implemented yet — X-A is
    the policy foundation, not the capture engine). Treating them as
    "record continuously" is the honest, non-destructive reading: it captures
    a superset of what the operator asked for, where guessing the other way
    would silently drop footage they expected to have.
    """
    if site_id is None:
        return True
    policy = await get_policy(session, site_id)
    if policy is None or not policy["is_active"]:
        return True
    return policy["record_mode"] not in ("off", "ai_event")


def validate_policy_fields(body: dict[str, Any]) -> None:
    """Raise ValueError on an invalid enum or a nonsensical sync window.

    Router-agnostic on purpose: the caller decides whether that becomes a 422
    or a CLI error.
    """
    for field, allowed in (
        ("record_mode", RECORD_MODES),
        ("sync_mode", SYNC_MODES),
        ("compression", COMPRESSIONS),
    ):
        value = body.get(field)
        if value is not None and value not in allowed:
            raise ValueError(f"{field} must be one of {', '.join(allowed)}")

    start, end = body.get("sync_window_start"), body.get("sync_window_end")
    if (start is None) != (end is None):
        raise ValueError("sync_window_start and sync_window_end must be set together")

    # An overnight window (22:00 -> 06:00) is valid and common — quiet hours
    # cross midnight — so start > end is NOT an error. Only start == end is,
    # since that describes a zero-length window that would never sync.
    if start is not None and start == end:
        raise ValueError("sync window start and end cannot be identical")
