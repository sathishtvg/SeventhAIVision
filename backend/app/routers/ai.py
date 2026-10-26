"""The AI module inside an organisation: what it has, and the admin's controls.

  GET /api/v1/ai/status                 any signed-in member: what is licensed, on, and theirs to use
  GET /api/v1/ai/settings               ai:configure: every feature with the admin's choices
  PUT /api/v1/ai/settings/{feature}     ai:configure: switch one on or off, choose roles, sites,
                                        sensitivity and whether it learns

The licence itself is the Super Admin's (routers/platform_ai_licenses.py); the
admin here can switch on only what the licence includes, and can always switch
anything off. services/ai_module.py holds the rules.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import _client_ip, get_db_with_tenant
from app.services import ai_module
from app.services.audit import write_audit_log

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])
_CONFIGURE = Depends(require_permission("ai:configure"))


class FeatureSettingBody(BaseModel):
    enabled: bool
    role_ids: list[int] = Field(default_factory=list, max_length=20)
    #: None is every site; a list is those sites only.
    site_ids: list[uuid.UUID] | None = Field(None, max_length=1000)
    sensitivity: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    learning_enabled: bool = True


@router.get("/status")
async def ai_status(db: AsyncSession = Depends(get_db_with_tenant),
                    token: TokenPayload = Depends(get_token_payload)):
    """For the menu and the screens: never refused, so a member of an unlicensed
    organisation is told why rather than shown an error."""
    return await ai_module.status_for(db, token.role_id)


def _feature_out(f: ai_module.Feature, s: dict, licensed: set[str]) -> dict:
    return {"key": f.key, "title": f.title, "group": f.group, "description": f.description,
            "ready": f.ready, "phase": f.phase, "licensed": f.key in licensed,
            "allowed_roles": list(f.allowed_roles),
            "enabled": bool(s["enabled"]), "role_ids": sorted(s["role_ids"] or []),
            "site_ids": [str(x) for x in s["site_ids"]] if s["site_ids"] is not None else None,
            "sensitivity": s["sensitivity"], "learning_enabled": bool(s["learning_enabled"]),
            "updated_at": s["updated_at"]}


async def _settings_out(db: AsyncSession) -> dict:
    lic = await ai_module.load_licence(db)
    problem = ai_module.licence_problem(lic)
    licensed = ai_module.licensed_features(lic)
    saved = await ai_module.load_settings(db)
    return {
        "licensed": problem is None, "reason": problem,
        "expires_at": lic["expires_at"] if lic else None,
        "assistant_provider": lic["assistant_provider"] if lic else None,
        "monthly_reply_limit": lic["monthly_reply_limit"] if lic else None,
        "features": [_feature_out(f, saved.get(f.key) or ai_module.default_setting(f), licensed)
                     for f in ai_module.FEATURES.values()],
    }


@router.get("/settings", dependencies=[_CONFIGURE])
async def get_ai_settings(db: AsyncSession = Depends(get_db_with_tenant)):
    return await _settings_out(db)


@router.put("/settings/{feature_key}", dependencies=[_CONFIGURE])
async def put_ai_setting(
    feature_key: str, body: FeatureSettingBody, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    feature = ai_module.FEATURES.get(feature_key)
    if feature is None:
        raise HTTPException(404, "No such AI feature")
    licensed = ai_module.licensed_features(await ai_module.load_licence(db))
    role_ids = sorted(set(body.role_ids))
    problem = ai_module.setting_problem(feature, enabled=body.enabled, role_ids=role_ids,
                                        sensitivity=body.sensitivity, licensed=licensed)
    if problem:
        raise HTTPException(422, problem)

    site_ids = None if body.site_ids is None else sorted({str(s) for s in body.site_ids})
    if site_ids is not None:
        if not site_ids:
            raise HTTPException(422, "Choose at least one site, or every site.")
        # RLS shows only this organisation's sites, so a site of another
        # organisation simply is not found.
        found = (await db.execute(text(
            "SELECT count(*) FROM sites WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": site_ids})).scalar()
        if found != len(site_ids):
            raise HTTPException(422, "One or more of those sites is not in this organisation.")

    row_id = (await db.execute(text("""
        INSERT INTO ai_feature_settings
            (tenant_id, feature_key, enabled, role_ids, site_ids, sensitivity, learning_enabled,
             updated_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :key, :enabled,
                CAST(:roles AS smallint[]), CAST(:sites AS uuid[]), :sens, :learn, CAST(:by AS uuid))
        ON CONFLICT (tenant_id, feature_key) DO UPDATE SET
            enabled            = EXCLUDED.enabled,
            role_ids           = EXCLUDED.role_ids,
            site_ids           = EXCLUDED.site_ids,
            sensitivity        = EXCLUDED.sensitivity,
            learning_enabled   = EXCLUDED.learning_enabled,
            updated_by_user_id = EXCLUDED.updated_by_user_id,
            updated_at         = now()
        RETURNING id
    """), {"key": feature.key, "enabled": body.enabled, "roles": role_ids, "sites": site_ids,
           "sens": body.sensitivity, "learn": body.learning_enabled, "by": token.user_id})).scalar_one()
    await write_audit_log(
        db, tenant_id=token.tenant_id, user_id=token.user_id,
        action="ai.setting.set", resource_type="ai_feature_setting", resource_id=str(row_id),
        ip_address=_client_ip(request),
        detail={"feature": feature.key, "enabled": body.enabled, "role_ids": role_ids, "site_ids": site_ids,
                "sensitivity": body.sensitivity, "learning_enabled": body.learning_enabled},
    )
    out = await _settings_out(db)
    await db.commit()
    return next(f for f in out["features"] if f["key"] == feature.key)
