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
