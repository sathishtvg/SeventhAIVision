"""Admin-editable per-tenant runtime configuration (plan §8) — directly
answers "easy configuration": AI thresholds/cooldowns/retention are no longer
redeploy-only. AI workers pick up a changed value within their 30s cache TTL
(ai-worker/worker/common/tenant_settings_cache.py)."""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config_keys import SETTING_VALIDATORS
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.schemas.settings import TenantSettingOut, TenantSettingUpsert

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@router.get("", response_model=list[TenantSettingOut], dependencies=[Depends(require_permission("settings:read"))])
async def list_settings(db: AsyncSession = Depends(get_db_with_tenant)) -> list[TenantSettingOut]:
    result = await db.execute(
        text("SELECT setting_key, setting_value, updated_by_user_id, updated_at FROM tenant_settings")
    )
    return [TenantSettingOut(**dict(row._mapping)) for row in result]


@router.put("/{setting_key}", response_model=TenantSettingOut, dependencies=[Depends(require_permission("settings:write"))])
async def upsert_setting(
    setting_key: str,
    body: TenantSettingUpsert,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
) -> TenantSettingOut:
    validator = SETTING_VALIDATORS.get(setting_key)
    if validator is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown setting_key: {setting_key}")
    try:
        validator(body.setting_value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    result = await db.execute(
        text(
            """
            INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, :key, CAST(:value AS jsonb), :uid)
            ON CONFLICT (tenant_id, setting_key)
            DO UPDATE SET setting_value = EXCLUDED.setting_value,
                          updated_by_user_id = EXCLUDED.updated_by_user_id,
                          updated_at = now()
            RETURNING setting_key, setting_value, updated_by_user_id, updated_at
            """
        ),
        {"key": setting_key, "value": json.dumps(body.setting_value), "uid": token.user_id},
    )
    row = result.first()
    await db.commit()
    return TenantSettingOut(**dict(row._mapping))
