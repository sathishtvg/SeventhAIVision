import hashlib
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import text

from app.core.security import InvalidTokenError, decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


@dataclass
class TokenPayload:
    user_id: str
    tenant_id: str
    role_id: int
    via_api_key: bool = field(default=False)
    #: Set only on a platform support token, where tenant_id is NOT the user's
    #: own tenant. Its presence is what tells get_db_with_tenant to re-check
    #: that the authorising session is still live before scoping the session
    #: to that tenant.
    support_session_id: str | None = field(default=None)


async def get_token_payload(
    bearer_token: str | None = Depends(oauth2_scheme),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> TokenPayload:
    # ── API key path ─────────────────────────────────────────────────────────
    if x_api_key:
        from app.db.session import AsyncSessionLocal

        key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
        row = None
        async with AsyncSessionLocal() as db:
            # Set the sentinel GUC first so pooled connections carrying '' from a
            # previous SET LOCAL don't cause invalid ''::uuid cast errors in RLS.
            await db.execute(
                text("SELECT set_config('app.current_tenant','00000000-0000-0000-0000-000000000000',true)")
            )
            # authenticate_api_key() is SECURITY DEFINER (runs as superuser) and
            # bypasses api_keys RLS — required because we don't know the tenant yet.
            row = (await db.execute(
                text("SELECT key_id, key_tenant_id FROM authenticate_api_key(:hash)"),
                {"hash": key_hash},
            )).first()
            if row:
                await db.commit()
        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired API key")
        return TokenPayload(
            user_id=str(row.key_id),        # key UUID as synthetic user_id
            tenant_id=str(row.key_tenant_id),
            role_id=2,                      # admin-equivalent access within the tenant
            via_api_key=True,
        )

    # ── JWT Bearer path ───────────────────────────────────────────────────────
    if not bearer_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_access_token(bearer_token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    return TokenPayload(
        user_id=payload["sub"],
        tenant_id=payload["tenant_id"],
        role_id=payload["role_id"],
        support_session_id=payload.get("support_session_id"),
    )
