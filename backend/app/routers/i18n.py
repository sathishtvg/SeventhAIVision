"""i18n API — locale catalog and alert message translation.

Public endpoints (no auth required — frontend fetches before login):
  GET /api/v1/i18n/locales                   → supported locales list
  GET /api/v1/i18n/{locale}/alerts           → all alert-code templates for a locale

Authenticated endpoints:
  GET  /api/v1/i18n/{locale}/alerts/{code}  → single rendered message
  PUT  /api/v1/i18n/me/locale               → update current user's preferred locale
  GET  /api/v1/i18n/tenant/locale           → get tenant default locale
  PUT  /api/v1/i18n/tenant/locale           → set tenant default locale (admin only)
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.i18n.alert_translations import (
    FALLBACK_LOCALE,
    SUPPORTED_LOCALES,
    TRANSLATIONS,
    translate_alert,
)

router = APIRouter(prefix="/api/v1/i18n", tags=["i18n"])


class LocaleBody(BaseModel):
    locale: str


# ── Public ────────────────────────────────────────────────────────────────────

@router.get("/locales")
async def list_locales():
    """Return supported locales and the fallback locale."""
    return {"locales": SUPPORTED_LOCALES, "fallback": FALLBACK_LOCALE}


@router.get("/{locale}/alerts")
async def get_alert_templates(locale: str):
    """Return all alert-code translation templates for a locale.

    Falls back to English if the locale is unknown — never 404.
    """
    effective = locale if locale in TRANSLATIONS else FALLBACK_LOCALE
    return {"locale": effective, "templates": TRANSLATIONS[effective]}


# ── Authenticated ─────────────────────────────────────────────────────────────

@router.get("/{locale}/alerts/{alert_code}")
async def translate_alert_code(
    locale: str,
    alert_code: str,
    params: str | None = None,
    _token: TokenPayload = Depends(get_token_payload),
):
    """Render a single translated alert message.

    `params` is an optional URL-encoded JSON string of message_params,
    e.g. `?params={"plate":"SGB1234X","confidence":0.91}`.
    """
    mp: dict = {}
    if params:
        try:
            mp = json.loads(params)
        except json.JSONDecodeError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "params must be valid JSON")

    effective_locale = locale if locale in TRANSLATIONS else FALLBACK_LOCALE
    locale_map = TRANSLATIONS[effective_locale]
    if alert_code not in locale_map and alert_code not in TRANSLATIONS[FALLBACK_LOCALE]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown alert_code: {alert_code}")

    message = translate_alert(alert_code, mp, effective_locale)
    return {"locale": effective_locale, "alert_code": alert_code, "message": message}


@router.put("/me/locale")
async def set_my_locale(
    body: LocaleBody,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Update the authenticated user's preferred locale."""
    if body.locale not in SUPPORTED_LOCALES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unsupported locale. Supported: {SUPPORTED_LOCALES}",
        )
    result = await db.execute(
        text("UPDATE users SET locale = :locale, updated_at = now() WHERE id = CAST(:uid AS uuid) RETURNING id, locale"),
        {"locale": body.locale, "uid": token.user_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await db.commit()
    return {"user_id": str(row.id), "locale": row.locale}


@router.get("/tenant/locale")
async def get_tenant_locale(
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Return the tenant's default locale (no special permission — any logged-in user)."""
    row = (await db.execute(
        text("SELECT default_locale FROM tenants WHERE id = CAST(:tid AS uuid)"),
        {"tid": token.tenant_id},
    )).first()
    if row is None:
        return {"locale": FALLBACK_LOCALE}
    return {"locale": row.default_locale}


@router.put("/tenant/locale", dependencies=[Depends(require_permission("i18n:manage"))])
async def set_tenant_locale(
    body: LocaleBody,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Set the tenant's default locale (admin/super-admin only)."""
    if body.locale not in SUPPORTED_LOCALES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unsupported locale. Supported: {SUPPORTED_LOCALES}",
        )
    await db.execute(
        text("UPDATE tenants SET default_locale = :locale WHERE id = CAST(:tid AS uuid)"),
        {"locale": body.locale, "tid": token.tenant_id},
    )
    await db.commit()
    return {"tenant_id": token.tenant_id, "locale": body.locale}
