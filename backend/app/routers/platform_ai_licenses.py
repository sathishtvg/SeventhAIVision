"""Super Admin: the AI module's licence for one organisation.

On or off, until when, which features were bought, which chat model the
assistant uses (the free built-in one, or the paid hosted one), and how many
assistant replies a month the organisation may have. The owner decided on
26 October 2026 that the Super Admin chooses the model per organisation and that
the monthly limit is set here and can be raised at any time.

Its own endpoint and table, as Drone Patrol's is (routers/platform_drone_licenses.py
says why). license:manage is held by Super Admin alone and is MFA-gated.

THE PLATFORM TENANT IS REFUSED: the platform owner sells the module to
customers and runs no customer's work itself.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import _client_ip, get_raw_db
from app.services import ai_module
from app.services.audit import write_audit_log

router = APIRouter(prefix="/api/v1/platform", tags=["platform-licenses"])
_MANAGE = Depends(require_permission("license:manage"))


class AiLicenseUpsert(BaseModel):
    is_enabled: bool
    expires_at: datetime | None = None
    features: list[str] = Field(default_factory=list, max_length=len(ai_module.FEATURES))
    assistant_provider: Literal["LOCAL", "HOSTED"] = "LOCAL"
    monthly_reply_limit: int | None = Field(None, ge=0, le=10_000_000)
    notes: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _consistent(self):
        unknown = ai_module.unknown_features(self.features)
        if unknown:
            raise ValueError(f"Unknown feature(s): {', '.join(unknown)}.")
        if self.assistant_provider == "HOSTED" and self.monthly_reply_limit is None:
            raise ValueError("The hosted model is paid per reply: set a monthly reply limit.")
        if self.is_enabled and self.expires_at is not None and self.expires_at <= datetime.now(timezone.utc):
            raise ValueError("An enabled licence cannot already have expired.")
        return self


def catalogue() -> list[dict]:
    """Every feature the licence can include, in the order they are offered."""
    return [{"key": f.key, "title": f.title, "group": f.group, "description": f.description,
             "ready": f.ready, "phase": f.phase} for f in ai_module.FEATURES.values()]


async def _target_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    row = (await db.execute(text(
        "SELECT id, name, slug, is_platform FROM tenants WHERE id = CAST(:id AS uuid)"),
        {"id": str(tenant_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Tenant not found")
    if row["is_platform"]:
        raise HTTPException(422, "The platform tenant does not use the AI module; "
                                 "license it to a customer tenant.")
    # Scope this transaction to the target, so RLS lets us read and write its row.
    await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"),
                     {"tid": str(tenant_id)})
    return dict(row)


async def _describe(db: AsyncSession, tenant: dict) -> dict:
    row = (await db.execute(text(
        "SELECT * FROM ai_module_licenses WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": str(tenant["id"])})).mappings().first()
    lic = dict(row) if row else None
    problem = ai_module.licence_problem(lic)
    return {
        "tenant_id": str(tenant["id"]), "tenant_name": tenant["name"], "tenant_slug": tenant["slug"],
        "licensed": problem is None, "reason": problem,
        "is_enabled": bool(lic and lic["is_enabled"]),
        "licensed_at": lic["licensed_at"] if lic else None,
        "expires_at": lic["expires_at"] if lic else None,
        "features": sorted(lic["features"] or []) if lic else [],
        "assistant_provider": lic["assistant_provider"] if lic else "LOCAL",
        "monthly_reply_limit": lic["monthly_reply_limit"] if lic else None,
        "notes": lic["notes"] if lic else None,
        "updated_at": lic["updated_at"] if lic else None,
        "catalogue": catalogue(),
    }


@router.get("/tenants/{tenant_id}/ai-license", dependencies=[_MANAGE])
async def get_ai_license(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_raw_db)):
    tenant = await _target_tenant(db, tenant_id)
    return await _describe(db, tenant)


@router.put("/tenants/{tenant_id}/ai-license", dependencies=[_MANAGE])
async def put_ai_license(
    tenant_id: uuid.UUID, body: AiLicenseUpsert, request: Request,
    db: AsyncSession = Depends(get_raw_db),
    token: TokenPayload = Depends(get_token_payload),
):
    """Create or replace the organisation's AI licence. licensed_at records when it
    was most recently switched on. Raising the monthly limit takes effect at once."""
    tenant = await _target_tenant(db, tenant_id)
    features = sorted(set(body.features))
    await db.execute(text("""
        INSERT INTO ai_module_licenses
            (tenant_id, is_enabled, licensed_at, expires_at, features, assistant_provider,
             monthly_reply_limit, licensed_by_user_id, notes)
        VALUES (CAST(:tid AS uuid), CAST(:enabled AS boolean),
                CASE WHEN CAST(:enabled AS boolean) THEN now() END, :expires,
                CAST(:features AS text[]), :provider, :limit, CAST(:by AS uuid), :notes)
        ON CONFLICT (tenant_id) DO UPDATE SET
            is_enabled          = EXCLUDED.is_enabled,
            licensed_at         = CASE WHEN EXCLUDED.is_enabled AND NOT ai_module_licenses.is_enabled
                                       THEN now() ELSE ai_module_licenses.licensed_at END,
            expires_at          = EXCLUDED.expires_at,
            features            = EXCLUDED.features,
            assistant_provider  = EXCLUDED.assistant_provider,
            monthly_reply_limit = EXCLUDED.monthly_reply_limit,
            licensed_by_user_id = EXCLUDED.licensed_by_user_id,
            notes               = EXCLUDED.notes,
            updated_at          = now()
    """), {"tid": str(tenant_id), "enabled": body.is_enabled, "expires": body.expires_at,
           "features": features, "provider": body.assistant_provider,
           "limit": body.monthly_reply_limit, "by": token.user_id, "notes": body.notes})
    # In the customer's own audit log, where their administrators look for who
    # changed what their organisation may do.
    await write_audit_log(
        db, tenant_id=str(tenant_id), user_id=token.user_id,
        action="ai.license.set", resource_type="ai_module_license", resource_id=str(tenant_id),
        ip_address=_client_ip(request),
        detail={"is_enabled": body.is_enabled,
                "expires_at": body.expires_at.isoformat() if body.expires_at else None,
                "features": features, "assistant_provider": body.assistant_provider,
                "monthly_reply_limit": body.monthly_reply_limit},
    )
    result = await _describe(db, tenant)
    await db.commit()
    return result
