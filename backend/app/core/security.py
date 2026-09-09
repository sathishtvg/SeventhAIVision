from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings


class InvalidTokenError(Exception):
    pass


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(user_id: str, tenant_id: str, role_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,  # baked in at login from users.tenant_id, never client-supplied
        "role_id": role_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_TTL_MIN),
        "type": "access",
    }
    return jwt.encode(
        payload, settings.JWT_SECRET_KEY_CURRENT, algorithm=settings.JWT_ALGORITHM,
        headers={"kid": settings.JWT_ACTIVE_KID},
    )


# A support session may not outlive this, whatever the caller asks for. A
# platform operator inside a customer's tenant is a temporary condition, and an
# eight-hour "temporary" is a standing grant with extra steps.
SUPPORT_TOKEN_MAX_MINUTES = 60


def create_support_token(
    platform_user_id: str, target_tenant_id: str, session_id: str, minutes: int,
) -> str:
    """Mint a token scoped to a tenant the user does not belong to.

    Every other token in this system bakes tenant_id in from users.tenant_id and
    never from anything the client said — that invariant is what makes RLS
    trustworthy. This function deliberately breaks it, and is the only thing
    that may, so it is worth being explicit about what keeps it safe:

      - it is reachable only through the support-session endpoint, which
        requires support:manage, held by Super Admin alone;
      - the token names the session that authorised it, and every tenant-scoped
        request re-checks that the session is still live (see
        get_db_with_tenant), so ending a session revokes the token immediately
        rather than at expiry;
      - it carries platform_user_id, so audit entries written during the
        session attribute to the human who opened it and not to a borrowed
        admin identity;
      - it expires in an hour at the outside.

    role_id is 2 (Admin) rather than 1: the point of the session is to see what
    the customer's own administrator sees.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": platform_user_id,
        "tenant_id": target_tenant_id,  # NOT users.tenant_id — see docstring
        "role_id": 2,
        "support_session_id": session_id,
        "iat": now,
        "exp": now + timedelta(minutes=min(minutes, SUPPORT_TOKEN_MAX_MINUTES)),
        "type": "access",
    }
    return jwt.encode(
        payload, settings.JWT_SECRET_KEY_CURRENT, algorithm=settings.JWT_ALGORITHM,
        headers={"kid": settings.JWT_ACTIVE_KID},
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """Looks up the verification key by the token's kid header against the set of
    currently-valid signing keys, rather than a single hardcoded secret — this is
    what makes JWT signing-key rotation possible without invalidating every live
    session the instant a key changes (plan §16.4)."""
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise InvalidTokenError("Malformed token header") from exc

    kid = unverified_header.get("kid")
    signing_keys = settings.jwt_signing_keys
    key = signing_keys.get(kid) if kid else None
    if key is None:
        raise InvalidTokenError(f"Unknown signing key id: {kid!r}")

    try:
        payload = jwt.decode(token, key, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise InvalidTokenError("Invalid or expired token") from exc

    if payload.get("type") != "access":
        raise InvalidTokenError("Not an access token")
    return payload
