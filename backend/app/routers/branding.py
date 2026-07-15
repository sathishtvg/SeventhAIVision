import json as _json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_raw_db

router = APIRouter(prefix="/api/v1/branding", tags=["branding"])


@router.get("")
async def get_branding(
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_raw_db),
):
    """Return the calling tenant's name, branding config, and timezone.
    Called by logged-in users to populate in-app white-label appearance."""
    row = (await db.execute(
        text("SELECT name, branding, timezone FROM tenants WHERE id = :id"),
        {"id": token.tenant_id},
    )).first()
    if row is None:
        return {"name": "", "branding": {}, "timezone": "Asia/Singapore"}
    d = dict(row._mapping)
    return {
        "name": d["name"],
        "branding": d["branding"] or {},
        "timezone": d["timezone"],
    }


class BrandingUpdate(BaseModel):
    branding: dict[str, str]


@router.put("", dependencies=[Depends(require_permission("settings:write"))])
async def update_branding(
    body: BrandingUpdate,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_raw_db),
):
    """Tenant admin self-service: update branding keys on their own tenant row.
    Only touches the branding JSONB column — name, slug, subdomain are
    unchanged (those require super-admin via PUT /api/v1/tenants/{id})."""
    await db.execute(
        text(
            "UPDATE tenants SET branding = CAST(:branding AS jsonb), updated_at = now() "
            "WHERE id = :id"
        ),
        {"branding": _json.dumps(body.branding), "id": token.tenant_id},
    )
    await db.commit()
    return {"branding": body.branding}
