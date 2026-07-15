import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pyotp
from fastapi import HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import text

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import AsyncSessionLocal
from app.schemas.auth import TokenPair

logger = logging.getLogger(__name__)

_MAX_FAILED_ATTEMPTS = 10
_LOCKOUT_MINUTES = 30


class _TwoFactorRequired(Exception):
    """Internal signal: password OK but 2FA code still needed."""
    def __init__(self, challenge_token: str):
        self.challenge_token = challenge_token


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _generate_refresh_token(tenant_id: UUID) -> str:
    """The opaque refresh token is prefixed with its tenant_id so a refresh
    request can set the RLS context *before* querying the RLS-protected
    refresh_tokens table — the same chicken-and-egg problem login solves via
    the RLS-exempt `tenants` table, solved here a different way since there's
    no equivalent exempt table to look a bare token hash up in. tenant_id isn't
    secret (it's already a plaintext JWT claim), so embedding it doesn't weaken
    anything; only the random component after the prefix is the actual secret,
    and that's what's hashed and compared in the DB."""
    return f"{tenant_id}.{secrets.token_urlsafe(48)}"


def _create_2fa_challenge_token(user_id: str, tenant_id: str) -> str:
    """Short-lived JWT (5 min) proving password was correct but 2FA not yet verified.
    The /auth/2fa-verify endpoint checks this token then issues real tokens."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "type": "2fa_challenge",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY_CURRENT,
        algorithm=settings.JWT_ALGORITHM,
        headers={"kid": settings.JWT_ACTIVE_KID},
    )


def _decode_2fa_challenge_token(token: str) -> dict:
    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        key = settings.jwt_signing_keys.get(kid) if kid else None
        if key is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid challenge token")
        payload = jwt.decode(token, key, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired challenge token")
    if payload.get("type") != "2fa_challenge":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid challenge token type")
    return payload


async def authenticate_and_issue_tokens(
    tenant_slug: str, email: str, password: str,
    device_name: str | None = None, client_ip: str | None = None,
) -> TokenPair:
    """Login can't use get_db_with_tenant: there's no JWT yet to derive a tenant
    context from (chicken-and-egg). Instead: resolve tenant_slug -> tenant_id via
    the `tenants` table (no RLS — it's the root of the tenant hierarchy, not
    itself tenant-scoped), then explicitly set that tenant_id as the session's
    RLS context before querying `users`, which is RLS-protected."""
    async with AsyncSessionLocal() as db:
        tenant_row = (
            await db.execute(
                text("SELECT id, name, slug, subdomain, timezone, branding "
                     "FROM tenants WHERE slug = :slug AND is_active = TRUE"),
                {"slug": tenant_slug},
            )
        ).first()
        if tenant_row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
        tenant_id = tenant_row.id

        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})

        user_row = (
            await db.execute(
                text(
                    "SELECT id, role_id, hashed_password, is_active, totp_enabled, totp_secret, "
                    "       failed_login_count, locked_until, created_at "
                    "FROM users WHERE email = :email"
                ),
                {"email": email},
            )
        ).first()

        # --- Account lockout check (before password verify to avoid timing oracle) ---
        if user_row is not None and user_row.locked_until is not None:
            if user_row.locked_until > datetime.now(timezone.utc):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"Account locked. Try again after {user_row.locked_until.strftime('%H:%M UTC')} "
                    "or contact your administrator.",
                )
            # Lockout expired — clear it
            await db.execute(
                text("UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = :uid"),
                {"uid": user_row.id},
            )

        # --- Password verification ---
        password_ok = (
            user_row is not None
            and user_row.is_active
            and verify_password(password, user_row.hashed_password)
        )
        if not password_ok:
            # Increment failed count and possibly lock the account
            if user_row is not None and user_row.is_active:
                new_count = (user_row.failed_login_count or 0) + 1
                if new_count >= _MAX_FAILED_ATTEMPTS:
                    await db.execute(
                        text(
                            "UPDATE users SET failed_login_count = :count, "
                            "locked_until = now() + INTERVAL '30 minutes' WHERE id = :uid"
                        ),
                        {"count": new_count, "uid": user_row.id},
                    )
                else:
                    await db.execute(
                        text("UPDATE users SET failed_login_count = :count WHERE id = :uid"),
                        {"count": new_count, "uid": user_row.id},
                    )
                await db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

        # Success — reset failure counter
        await db.execute(
            text("UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = :uid"),
            {"uid": user_row.id},
        )

        # 2FA enforcement policy: if the tenant mandates 2FA and the user hasn't
        # set it up, block login entirely with a specific error code so the client
        # can direct the user to the TOTP setup flow.
        policy_row = (await db.execute(
            text("SELECT setting_value FROM tenant_settings "
                 "WHERE setting_key = '2fa.required'"),
        )).first()
        if policy_row and policy_row.setting_value is True:
            if not user_row.totp_enabled:
                # Check grace period: exempt users created within grace_hours of now.
                grace_row = (await db.execute(
                    text("SELECT setting_value FROM tenant_settings "
                         "WHERE setting_key = '2fa.grace_hours'"),
                )).first()
                grace_hours = int(grace_row.setting_value) if grace_row else 0
                in_grace = False
                if grace_hours > 0 and user_row.created_at is not None:
                    cutoff = datetime.now(timezone.utc) - timedelta(hours=grace_hours)
                    in_grace = user_row.created_at > cutoff
                if not in_grace:
                    await db.commit()
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN,
                        detail={
                            "code": "2fa_setup_required",
                            "message": (
                                "This tenant requires two-factor authentication. "
                                "Please set up TOTP before logging in."
                            ),
                        },
                    )

        # 2FA: if enabled, return a short-lived challenge token instead of real tokens
        if user_row.totp_enabled:
            await db.execute(
                text("UPDATE users SET last_login_at = now() WHERE id = :uid"), {"uid": user_row.id}
            )
            await db.commit()
            challenge_token = _create_2fa_challenge_token(str(user_row.id), str(tenant_id))
            raise _TwoFactorRequired(challenge_token)

        access_token = create_access_token(
            user_id=str(user_row.id), tenant_id=str(tenant_id), role_id=user_row.role_id
        )
        refresh_token = _generate_refresh_token(tenant_id)
        expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)
        await db.execute(
            text(
                "INSERT INTO refresh_tokens (tenant_id, user_id, token_hash, expires_at, device_name, last_ip, last_seen_at) "
                "VALUES (:tid, :uid, :hash, :exp, :device, :ip, now())"
            ),
            {
                "tid": tenant_id, "uid": user_row.id, "hash": _hash_token(refresh_token),
                "exp": expires_at, "device": device_name, "ip": client_ip,
            },
        )
        await db.execute(
            text("UPDATE users SET last_login_at = now() WHERE id = :uid"), {"uid": user_row.id}
        )

        # Load licensed products BEFORE commit — the GUC (set with is_local=true)
        # is transaction-scoped and resets to "" after commit, which breaks RLS on
        # tenant_products. Reading here keeps it inside the same transaction.
        from app.routers.platform_licenses import get_tenant_licensed_products
        from app.schemas.auth import TenantInfo
        licensed = await get_tenant_licensed_products(db, tenant_id)
        tenant_info = TenantInfo(
            id=str(tenant_id),
            name=tenant_row.name,
            slug=tenant_row.slug,
            subdomain=tenant_row.subdomain,
            timezone=tenant_row.timezone or "Asia/Singapore",
            branding=tenant_row.branding or {},
        )

        await db.commit()

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        tenant=tenant_info,
        licensed_products=licensed,
    )


async def request_password_reset(tenant_slug: str, email: str) -> None:
    """Always succeeds from the caller's perspective, regardless of whether the
    tenant/email exists — mirrors login's identical-shape-on-failure discipline
    (test_login_wrong_password_and_nonexistent_email_identical_shape) so this
    endpoint can't be used to enumerate valid accounts. A match silently gets
    a one-time reset link emailed; a non-match is a silent no-op."""
    async with AsyncSessionLocal() as db:
        tenant_row = (await db.execute(
            text("SELECT id FROM tenants WHERE slug = :slug AND is_active = TRUE"),
            {"slug": tenant_slug},
        )).first()
        if tenant_row is None:
            return
        tenant_id = tenant_row.id

        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})

        user_row = (await db.execute(
            text("SELECT id FROM users WHERE email = :email AND is_active = TRUE"),
            {"email": email},
        )).first()
        if user_row is None:
            return

        # Opaque token shape mirrors _generate_refresh_token's tenant-prefix
        # trick (see its docstring) so confirm_password_reset can set the RLS
        # context before querying this RLS-protected table.
        raw_token = f"{tenant_id}.{secrets.token_urlsafe(48)}"
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        await db.execute(
            text(
                "INSERT INTO password_reset_tokens (tenant_id, user_id, token_hash, expires_at) "
                "VALUES (:tid, :uid, :hash, :exp)"
            ),
            {"tid": tenant_id, "uid": user_row.id, "hash": _hash_token(raw_token), "exp": expires_at},
        )
        await db.commit()

    base = settings.FRONTEND_URL.rstrip("/") if settings.FRONTEND_URL else ""
    reset_url = f"{base}/reset-password?token={raw_token}"
    body = (
        "A password reset was requested for your Seventh AI Vision account.\n\n"
        f"Reset your password: {reset_url}\n\n"
        "This link expires in 1 hour. If you did not request this, you can safely ignore this email."
    )
    from app.notifications.providers.email import send_raw_email
    try:
        await send_raw_email([email], "Reset your Seventh AI Vision password", body)
    except Exception:
        logger.exception("password reset email failed to send to=%s", email)


async def confirm_password_reset(raw_token: str, new_password: str) -> None:
    """Consumes a one-time reset token: sets the new password and revokes every
    outstanding refresh token for that user, so a session already in an
    attacker's hands (the reason the user is resetting) doesn't survive it."""
    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset link")
    try:
        tenant_id_str, _secret = raw_token.split(".", 1)
        UUID(tenant_id_str)
    except ValueError:
        raise invalid

    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tenant_id_str})

        token_hash = _hash_token(raw_token)
        row = (await db.execute(
            text(
                "SELECT id, user_id, expires_at, used_at FROM password_reset_tokens "
                "WHERE token_hash = :hash"
            ),
            {"hash": token_hash},
        )).first()
        if row is None or row.used_at is not None or row.expires_at < datetime.now(timezone.utc):
            raise invalid

        await db.execute(
            text(
                "UPDATE users SET hashed_password = :hashed, failed_login_count = 0, locked_until = NULL "
                "WHERE id = :uid"
            ),
            {"hashed": hash_password(new_password), "uid": row.user_id},
        )
        await db.execute(
            text("UPDATE password_reset_tokens SET used_at = now() WHERE id = :id"),
            {"id": row.id},
        )
        await db.execute(
            text("UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = :uid AND revoked_at IS NULL"),
            {"uid": row.user_id},
        )
        await db.commit()


async def unlock_user_account(tenant_id: str, user_id: str) -> None:
    """Admin/supervisor action: clear lockout on a user account."""
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tenant_id})
        await db.execute(
            text("UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = :uid"),
            {"uid": user_id},
        )
        await db.commit()


async def rotate_refresh_token(raw_refresh_token: str, device_name: str | None = None, client_ip: str | None = None) -> TokenPair:
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")

    tenant_id_str, _, _secret = raw_refresh_token.partition(".")
    try:
        tenant_id = UUID(tenant_id_str)
    except ValueError:
        raise invalid

    token_hash = _hash_token(raw_refresh_token)
    async with AsyncSessionLocal() as db:
        # Set RLS context from the token's own tenant_id prefix *before* querying
        # refresh_tokens — it's RLS-protected, so querying it unscoped would
        # silently return zero rows (fail-closed) rather than finding the token.
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})

        row = (
            await db.execute(
                text(
                    "SELECT id, tenant_id, user_id, expires_at, revoked_at FROM refresh_tokens "
                    "WHERE token_hash = :hash"
                ),
                {"hash": token_hash},
            )
        ).first()
        if row is None or row.revoked_at is not None or row.expires_at < datetime.now(timezone.utc):
            raise invalid

        user_row = (
            await db.execute(
                text("SELECT role_id, is_active FROM users WHERE id = :uid"), {"uid": row.user_id}
            )
        ).first()
        if user_row is None or not user_row.is_active:
            raise invalid

        # Rotate: revoke the old token, issue a brand new one. A reused (already
        # revoked) refresh token is rejected by the revoked_at check above on its
        # next use, which is what makes rotation actually catch token theft.
        await db.execute(
            text("UPDATE refresh_tokens SET revoked_at = now() WHERE id = :id"), {"id": row.id}
        )
        new_refresh_token = _generate_refresh_token(tenant_id)
        expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)
        await db.execute(
            text(
                "INSERT INTO refresh_tokens (tenant_id, user_id, token_hash, expires_at, device_name, last_ip, last_seen_at) "
                "VALUES (:tid, :uid, :hash, :exp, :device, :ip, now())"
            ),
            {
                "tid": row.tenant_id, "uid": row.user_id, "hash": _hash_token(new_refresh_token),
                "exp": expires_at, "device": device_name, "ip": client_ip,
            },
        )
        await db.commit()

        access_token = create_access_token(
            user_id=str(row.user_id), tenant_id=str(row.tenant_id), role_id=user_row.role_id
        )

    return TokenPair(access_token=access_token, refresh_token=new_refresh_token)


async def verify_2fa_and_issue_tokens(challenge_token: str, totp_code: str) -> TokenPair:
    """Step 2 of 2FA login: validate challenge token + TOTP code → issue real tokens."""
    payload = _decode_2fa_challenge_token(challenge_token)
    user_id = payload["sub"]
    tenant_id = UUID(payload["tenant_id"])

    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})
        user_row = (
            await db.execute(
                text("SELECT id, role_id, is_active, totp_secret, totp_enabled FROM users WHERE id = :uid"),
                {"uid": user_id},
            )
        ).first()
        if user_row is None or not user_row.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
        if not user_row.totp_enabled or not user_row.totp_secret:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA not configured on this account")

        totp = pyotp.TOTP(user_row.totp_secret)
        if not totp.verify(totp_code, valid_window=1):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid TOTP code")

        access_token = create_access_token(
            user_id=str(user_row.id), tenant_id=str(tenant_id), role_id=user_row.role_id
        )
        refresh_token = _generate_refresh_token(tenant_id)
        expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)
        await db.execute(
            text(
                "INSERT INTO refresh_tokens (tenant_id, user_id, token_hash, expires_at) "
                "VALUES (:tid, :uid, :hash, :exp)"
            ),
            {"tid": tenant_id, "uid": user_row.id, "hash": _hash_token(refresh_token), "exp": expires_at},
        )
        await db.commit()

    return TokenPair(access_token=access_token, refresh_token=refresh_token)


async def issue_tokens(
    user_id: str,
    tenant_id: str,
    role_id: int,
) -> TokenPair:
    """Issue a fresh JWT + refresh token pair without password verification.

    Used by SSO/SAML callback after the IdP has already authenticated the user.
    Stores the refresh token in the DB using the same pattern as the password login flow.
    """
    tid = UUID(tenant_id)
    uid = UUID(user_id)
    access_token  = create_access_token(user_id=user_id, tenant_id=tenant_id, role_id=role_id)
    refresh_token = _generate_refresh_token(tid)
    expires_at    = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": tenant_id},
        )
        await db.execute(
            text(
                "INSERT INTO refresh_tokens (tenant_id, user_id, token_hash, expires_at) "
                "VALUES (:tid, :uid, :hash, :exp)"
            ),
            {"tid": tid, "uid": uid, "hash": _hash_token(refresh_token), "exp": expires_at},
        )
        await db.commit()

    return TokenPair(access_token=access_token, refresh_token=refresh_token)
