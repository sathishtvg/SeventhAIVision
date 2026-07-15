"""Gap 33 — Credential Encryption at Rest.

Symmetric encryption for persisted secrets (RTSP passwords, NVR credentials)
using Fernet (AES-128-CBC + HMAC-SHA256).

Production setup:
    Generate a key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    Set env var:    CREDENTIALS_ENCRYPTION_KEY=<generated key>

Development: a fixed constant key is used automatically when the env var is absent.
Never use the dev key in production.
"""
from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

# Exactly 32 ASCII bytes → valid Fernet key via urlsafe_b64encode (44 chars output)
_DEV_KEY_BYTES: bytes = b"seventh-ai-dev-cred-enc-key00000"
_DEV_FERNET_KEY: bytes = base64.urlsafe_b64encode(_DEV_KEY_BYTES)


def _get_fernet() -> Fernet:
    raw = settings.CREDENTIALS_ENCRYPTION_KEY
    if raw:
        key = raw.encode() if isinstance(raw, str) else raw
        return Fernet(key)
    return Fernet(_DEV_FERNET_KEY)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a credential string. Returns a URL-safe Fernet token (ASCII str)."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    """Decrypt a Fernet token back to plaintext.

    Raises ValueError on failure (wrong key or corrupted token).
    """
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Credential decryption failed — wrong key or corrupted token") from exc
