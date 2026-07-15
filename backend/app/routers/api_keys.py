"""API key management — machine-to-machine authentication for external integrations."""

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])

_KEY_PREFIX = "sav1_"


def _generate_key() -> tuple[str, str, str]:
    """Return (full_key, display_prefix, sha256_hash)."""
    random_hex = secrets.token_hex(32)          # 64 hex chars = 32 bytes
    full_key = f"{_KEY_PREFIX}{random_hex}"
    display_prefix = random_hex[:8]
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, display_prefix, key_hash


class ApiKeyCreate(BaseModel):
    name: str
    expires_at: str | None = None               # ISO-8601 or None = never expires


@router.get("", dependencies=[Depends(require_permission("apikey:manage"))])
async def list_api_keys(db: AsyncSession = Depends(get_db_with_tenant)):
    rows = (await db.execute(
        text("""
            SELECT ak.id, ak.name, ak.key_prefix, ak.is_active,
                   ak.last_used_at, ak.expires_at, ak.created_at,
                   u.email AS created_by_email
            FROM api_keys ak
            LEFT JOIN users u ON u.id = ak.created_by_user_id
            ORDER BY ak.created_at DESC
        """)
    )).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("", dependencies=[Depends(require_permission("apikey:manage"))])
async def create_api_key(
    body: ApiKeyCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Create a new API key. The full key is returned once and never shown again."""
    full_key, display_prefix, key_hash = _generate_key()

    row = (await db.execute(
        text("""
            INSERT INTO api_keys
                (tenant_id, name, key_prefix, key_hash, created_by_user_id, expires_at)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                :name, :prefix, :hash,
                :created_by,
                :expires_at
            )
            RETURNING id, name, key_prefix, created_at, expires_at
        """),
        {
            "name": body.name,
            "prefix": display_prefix,
            "hash": key_hash,
            "created_by": token.user_id,
            "expires_at": datetime.fromisoformat(body.expires_at.replace("Z", "+00:00")) if body.expires_at else None,
        },
    )).first()
    await db.commit()

    return {
        **dict(row._mapping),
        "key": full_key,           # shown ONCE — caller must copy it now
        "key_shown_once": True,
    }


@router.delete("/{key_id}", dependencies=[Depends(require_permission("apikey:manage"))])
async def revoke_api_key(
    key_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    result = await db.execute(
        text("""
            UPDATE api_keys SET is_active = FALSE
            WHERE id = :id AND is_active = TRUE
            RETURNING id
        """),
        {"id": key_id},
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found or already revoked")
    return {"id": key_id, "revoked": True}
