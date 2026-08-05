"""Per-site recording policy (Phase X-A).

CRUD over one policy row per site, plus a resolved "what actually applies
here" endpoint the UI uses to show an admin the effective values without
making it re-implement the inheritance chain in TypeScript.

Two permissions, deliberately split (migration 0079):
  recording_policy:read   — roles 1,2,3,8 (incl. supervisor)
  recording_policy:manage — roles 1,2,8    (supervisor excluded)

Retention length is an evidentiary and contractual commitment. A supervisor
should be able to see how long footage is kept at their site without being
able to shorten it — shortening retention destroys evidence, and that is an
admin decision with a paper trail, not an operational one.
"""
from __future__ import annotations

from datetime import time as _time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.continuous_recording import DEFAULT_RETENTION_DAYS
from app.services.recording_policy import (
    COMPRESSIONS,
    POLICY_COLUMNS,
    RECORD_MODES,
    SYNC_MODES,
    get_effective_retention_days,
    get_policy,
    get_tenant_retention_days,
    validate_policy_fields,
)

router = APIRouter(prefix="/api/v1/recording-policies", tags=["recording-policies"])


class PolicyUpsert(BaseModel):
    record_mode: str = "continuous"
    sync_mode: str = "central"
    # Explicit None means "inherit the tenant setting" — the whole reason these
    # are nullable rather than defaulted. 0 is a different, valid answer.
    local_retention_days: int | None = Field(default=None, ge=0)
    central_retention_days: int | None = Field(default=None, ge=0)
    sync_window_start: _time | None = None
    sync_window_end: _time | None = None
    bandwidth_limit_kbps: int | None = Field(default=None, gt=0)
    clip_pre_seconds: int = Field(default=20, ge=0, le=600)
    clip_post_seconds: int = Field(default=60, ge=0, le=600)
    compression: str = "none"
    encrypt_archives: bool = False
    verify_checksums: bool = True
    is_active: bool = True


async def _require_site(db: AsyncSession, site_id: str) -> None:
    """404 on a site this tenant can't see.

    RLS already makes another tenant's site invisible, so a missing row and a
    forbidden row are indistinguishable here — 404 for both, matching
    sites.py's existing not-found-not-forbidden convention (a 403 would
    confirm the site exists to a caller who shouldn't know that).
    """
    exists = (
        await db.execute(
            text("SELECT 1 FROM sites WHERE id = CAST(:sid AS uuid)"), {"sid": site_id}
        )
    ).first()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "site not found")


@router.get("", dependencies=[Depends(require_permission("recording_policy:read"))])
async def list_policies(db: AsyncSession = Depends(get_db_with_tenant)):
    """Every configured policy, with its site name for display.

    Sites with no policy are intentionally absent rather than returned as
    synthetic default rows — "not configured" is a state the UI shows
    differently from "configured to the default values".
    """
    result = await db.execute(
        text(f"""
            SELECT {', '.join('p.' + c.strip() for c in POLICY_COLUMNS.split(','))},
                   s.name AS site_name
            FROM recording_policies p
            JOIN sites s ON s.id = p.site_id
            ORDER BY s.name
        """)
    )
    return [dict(r._mapping) for r in result]


@router.get(
    "/sites/{site_id}",
    dependencies=[Depends(require_permission("recording_policy:read"))],
)
async def get_site_policy(site_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await _require_site(db, site_id)
    policy = await get_policy(db, site_id)
    if policy is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "no recording policy configured for this site"
        )
    return policy


@router.get(
    "/sites/{site_id}/effective",
    dependencies=[Depends(require_permission("recording_policy:read"))],
)
async def get_effective_policy(
    site_id: str, db: AsyncSession = Depends(get_db_with_tenant)
):
    """What actually applies at this site, inheritance already resolved.

    Exists so the UI can render "90 days (from site policy)" vs "7 days
    (inherited from tenant)" without duplicating the resolution order —
    a second implementation of that chain would eventually disagree with
    this one, and the disagreement would be about how long evidence is kept.
    """
    await _require_site(db, site_id)
    policy = await get_policy(db, site_id)
    tenant_days = await get_tenant_retention_days(db, DEFAULT_RETENTION_DAYS)
    effective_days = await get_effective_retention_days(
        db, site_id, DEFAULT_RETENTION_DAYS
    )
    inherited = policy is None or policy["central_retention_days"] is None
    return {
        "site_id": site_id,
        "has_policy": policy is not None,
        "record_mode": policy["record_mode"] if policy else "continuous",
        "sync_mode": policy["sync_mode"] if policy else "central",
        "central_retention_days": effective_days,
        "central_retention_inherited": inherited,
        "tenant_retention_days": tenant_days,
        "local_retention_days": policy["local_retention_days"] if policy else None,
        "clip_pre_seconds": policy["clip_pre_seconds"] if policy else 20,
        "clip_post_seconds": policy["clip_post_seconds"] if policy else 60,
    }


@router.put(
    "/sites/{site_id}",
    dependencies=[Depends(require_permission("recording_policy:manage"))],
)
async def upsert_site_policy(
    site_id: str,
    body: PolicyUpsert,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Create or replace this site's policy.

    Upsert rather than separate POST/PUT: a site has at most one policy
    (enforced by uq_recording_policy_site), so "create" and "update" are the
    same operation from the caller's point of view, and making them two
    endpoints just means the UI has to track which one applies.
    """
    await _require_site(db, site_id)
    try:
        validate_policy_fields(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    params = body.model_dump()
    params["sid"] = site_id
    params["uid"] = token.user_id
    params["tid"] = token.tenant_id

    row = (
        await db.execute(
            text(f"""
                INSERT INTO recording_policies (
                    tenant_id, site_id, record_mode, sync_mode,
                    local_retention_days, central_retention_days,
                    sync_window_start, sync_window_end, bandwidth_limit_kbps,
                    clip_pre_seconds, clip_post_seconds,
                    compression, encrypt_archives, verify_checksums,
                    is_active, updated_by_user_id
                ) VALUES (
                    CAST(:tid AS uuid), CAST(:sid AS uuid), :record_mode, :sync_mode,
                    :local_retention_days, :central_retention_days,
                    :sync_window_start, :sync_window_end, :bandwidth_limit_kbps,
                    :clip_pre_seconds, :clip_post_seconds,
                    :compression, :encrypt_archives, :verify_checksums,
                    :is_active, CAST(:uid AS uuid)
                )
                ON CONFLICT (site_id) DO UPDATE SET
                    record_mode            = EXCLUDED.record_mode,
                    sync_mode              = EXCLUDED.sync_mode,
                    local_retention_days   = EXCLUDED.local_retention_days,
                    central_retention_days = EXCLUDED.central_retention_days,
                    sync_window_start      = EXCLUDED.sync_window_start,
                    sync_window_end        = EXCLUDED.sync_window_end,
                    bandwidth_limit_kbps   = EXCLUDED.bandwidth_limit_kbps,
                    clip_pre_seconds       = EXCLUDED.clip_pre_seconds,
                    clip_post_seconds      = EXCLUDED.clip_post_seconds,
                    compression            = EXCLUDED.compression,
                    encrypt_archives       = EXCLUDED.encrypt_archives,
                    verify_checksums       = EXCLUDED.verify_checksums,
                    is_active              = EXCLUDED.is_active,
                    updated_by_user_id     = EXCLUDED.updated_by_user_id,
                    updated_at             = now()
                RETURNING {POLICY_COLUMNS}
            """),
            params,
        )
    ).first()
    await db.commit()
    return dict(row._mapping)


@router.delete(
    "/sites/{site_id}",
    dependencies=[Depends(require_permission("recording_policy:manage"))],
)
async def delete_site_policy(
    site_id: str, db: AsyncSession = Depends(get_db_with_tenant)
):
    """Remove the policy so the site inherits the tenant setting again.

    A hard delete, not is_active=FALSE: "no policy" and "inactive policy"
    resolve identically everywhere in services/recording_policy.py, so
    keeping a dead row would only create a second way to express one state.
    """
    result = await db.execute(
        text("DELETE FROM recording_policies WHERE site_id = CAST(:sid AS uuid)"),
        {"sid": site_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no policy for this site")
    await db.commit()
    return {"deleted": True, "site_id": site_id}


@router.get("/options", dependencies=[Depends(require_permission("recording_policy:read"))])
async def get_options():
    """Valid enum values, served rather than hardcoded in the frontend so a
    new mode added to the CHECK constraint shows up in the UI's dropdowns
    without a second edit in TypeScript."""
    return {
        "record_modes": list(RECORD_MODES),
        "sync_modes": list(SYNC_MODES),
        "compressions": list(COMPRESSIONS),
    }
