"""Two-Factor Authentication (TOTP) self-service endpoints.

Flow:
1. GET  /api/v1/2fa/setup   → generates secret + QR code URI (not yet enabled)
2. POST /api/v1/2fa/enable  → verify first TOTP code → enable 2FA on account
3. DELETE /api/v1/2fa       → verify TOTP code → disable 2FA
4. GET  /api/v1/2fa/status  → returns {enabled: bool}

Login with 2FA active (in auth_service.py):
- Correct password but 2FA enabled → 200 with {requires_2fa: true, challenge_token: "..."}
- POST /api/v1/auth/2fa-verify {challenge_token, totp_code} → full TokenPair
"""

import io
import base64

import pyotp
import qrcode
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/2fa", tags=["2fa"])

APP_NAME = "7th AI Vision"


# ── Tenant-level enforcement policy ──────────────────────────────────────────

class TwoFAPolicyBody(BaseModel):
    required: bool
    grace_hours: int = 0  # 0 = immediate enforcement; >0 = new users exempt for N hours


@router.get("/policy", dependencies=[Depends(require_permission("2fa:policy"))])
async def get_2fa_policy(db: AsyncSession = Depends(get_db_with_tenant)):
    """Return current 2FA enforcement policy for this tenant."""
    rows = (await db.execute(
        text("""
            SELECT setting_key, setting_value
            FROM tenant_settings
            WHERE setting_key IN ('2fa.required', '2fa.grace_hours')
        """)
    )).fetchall()
    kv = {r.setting_key: r.setting_value for r in rows}
    return {
        "required": bool(kv.get("2fa.required", False)),
        "grace_hours": int(kv.get("2fa.grace_hours", 0)),
    }


@router.put("/policy", dependencies=[Depends(require_permission("2fa:policy"))])
async def set_2fa_policy(
    body: TwoFAPolicyBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Enable or disable tenant-level 2FA enforcement."""
    import json as _json
    for key, value in [("2fa.required", body.required), ("2fa.grace_hours", body.grace_hours)]:
        await db.execute(
            text("""
                INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id)
                VALUES (current_setting('app.current_tenant')::uuid, :key, CAST(:value AS jsonb), :uid)
                ON CONFLICT (tenant_id, setting_key)
                DO UPDATE SET setting_value = EXCLUDED.setting_value,
                              updated_by_user_id = EXCLUDED.updated_by_user_id,
                              updated_at = now()
            """),
            {"key": key, "value": _json.dumps(value), "uid": token.user_id},
        )
    await db.commit()
    return {"required": body.required, "grace_hours": body.grace_hours}


class EnableBody(BaseModel):
    totp_code: str


class DisableBody(BaseModel):
    totp_code: str


def _get_qr_data_uri(secret: str, email: str) -> str:
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=APP_NAME)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


@router.get("/status", dependencies=[Depends(require_permission("2fa:manage"))])
async def get_2fa_status(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    row = (
        await db.execute(
            text("SELECT totp_enabled FROM users WHERE id = :uid"),
            {"uid": token.user_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return {"enabled": bool(row.totp_enabled)}


@router.get("/setup", dependencies=[Depends(require_permission("2fa:manage"))])
async def setup_2fa(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Generate a new TOTP secret and return the QR code URI (but don't enable yet)."""
    row = (
        await db.execute(
            text("SELECT email, totp_enabled FROM users WHERE id = :uid"),
            {"uid": token.user_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if row.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "2FA is already enabled — disable it first")

    secret = pyotp.random_base32()
    await db.execute(
        text("UPDATE users SET totp_secret = :secret WHERE id = :uid"),
        {"secret": secret, "uid": token.user_id},
    )
    await db.commit()

    qr_data_uri = _get_qr_data_uri(secret, row.email)
    return {
        "secret": secret,
        "qr_code_uri": qr_data_uri,
        "manual_entry_key": secret,
        "instructions": "Scan the QR code with Google Authenticator / Authy, then call POST /api/v1/2fa/enable with your first code.",
    }


@router.post("/enable", dependencies=[Depends(require_permission("2fa:manage"))])
async def enable_2fa(
    body: EnableBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    row = (
        await db.execute(
            text("SELECT totp_secret, totp_enabled FROM users WHERE id = :uid"),
            {"uid": token.user_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if row.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "2FA is already enabled")
    if not row.totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Call GET /2fa/setup first to generate a secret")

    totp = pyotp.TOTP(row.totp_secret)
    if not totp.verify(body.totp_code, valid_window=1):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid TOTP code")

    await db.execute(
        text("UPDATE users SET totp_enabled = TRUE, totp_verified_at = now() WHERE id = :uid"),
        {"uid": token.user_id},
    )
    await db.commit()
    return {"enabled": True, "message": "Two-factor authentication is now active on your account"}


@router.delete("", dependencies=[Depends(require_permission("2fa:manage"))])
async def disable_2fa(
    body: DisableBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    row = (
        await db.execute(
            text("SELECT totp_secret, totp_enabled FROM users WHERE id = :uid"),
            {"uid": token.user_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not row.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA is not enabled")

    totp = pyotp.TOTP(row.totp_secret)
    if not totp.verify(body.totp_code, valid_window=1):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid TOTP code")

    await db.execute(
        text("UPDATE users SET totp_enabled = FALSE, totp_secret = NULL, totp_verified_at = NULL WHERE id = :uid"),
        {"uid": token.user_id},
    )
    await db.commit()
    return {"enabled": False, "message": "Two-factor authentication has been disabled"}
