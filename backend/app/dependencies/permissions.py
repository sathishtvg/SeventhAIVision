from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.tenant import get_db_with_tenant

PLATFORM_ADMIN_ROLE_ID = 1

#: Withheld from a Super Admin who has not enrolled in 2FA. Everything the
#: platform owner can actually do — reach any tenant, change every customer's
#: price, read the whole ledger — and nothing they need in order to enrol.
_MFA_GATED_PERMISSIONS = frozenset({
    "tenant:manage", "license:manage", "support:manage",
    "platform:read", "billing:read", "billing:manage", "audit:read",
})


async def _platform_owner_needs_mfa(db: AsyncSession, token: TokenPayload,
                                    code: str) -> bool:
    """Is this a platform owner reaching for a platform power without 2FA?

    Deliberately NOT a login refusal. Blocking the sign-in of a Super Admin who
    has not enrolled would be correct and unusable — they cannot enrol without
    signing in — so the login succeeds and the POWERS are withheld. They can
    reach their own account and the 2FA setup pages, which is exactly the way
    out, visible from where they are standing.

    Read through a SECURITY DEFINER function because the platform owner sits in
    its own tenant and this check runs before any tenant scope is settled;
    reading users directly would return nothing and, being a boolean, would
    read as "not enrolled" — locking out the very people it is meant to protect.
    """
    if token.role_id != PLATFORM_ADMIN_ROLE_ID or code not in _MFA_GATED_PERMISSIONS:
        return False

    required = (await db.execute(text(
        "SELECT value FROM platform_settings "
        " WHERE key = 'security.require_mfa_for_platform_owner'"
    ))).scalar()
    # Absent means required. A policy row somebody deleted should not silently
    # switch a security control off.
    if required is not None and str(required).strip().lower() not in ("true", "1", "yes"):
        return False

    row = (await db.execute(
        text("SELECT * FROM platform_user_mfa_state(CAST(:uid AS uuid))"),
        {"uid": token.user_id},
    )).mappings().first()
    if row is None:
        # An unknown subject is not a reason to hand out platform powers.
        return True
    return not row["totp_enabled"]


def require_permission(code: str):
    """Checks the caller's role against role_permissions — the same table the
    Phase 2 web dashboard and mobile app will read permission codes from, so
    there's never a parallel authorization model to invent for a new client."""

    async def checker(
        db: AsyncSession = Depends(get_db_with_tenant),
        token: TokenPayload = Depends(get_token_payload),
    ) -> None:
        result = await db.execute(
            text(
                """
                SELECT 1 FROM role_permissions rp
                JOIN permissions p ON p.id = rp.permission_id
                WHERE rp.role_id = :role_id AND p.code = :code
                """
            ),
            {"role_id": token.role_id, "code": code},
        )
        if result.first() is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {code}")

        # Holding the permission is not the whole question for the one account
        # that can reach every customer.
        if await _platform_owner_needs_mfa(db, token, code):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Two-factor authentication is required for the platform owner. "
                "Set it up under My Account before using this.",
            )

    return checker
