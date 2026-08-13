"""Licence key verification — the server half of the format defined in
keygen/lib/licence.js.

WHY THIS SHAPE
    A key is  SAV1.<base64url(payload JSON)>.<base64url(Ed25519 sig)>  and
    the signature covers the ASCII bytes of "SAV1.<base64url(payload)>" —
    the already-encoded string, not a re-serialised object.

    That detail is load-bearing. If each side re-serialised the JSON before
    checking the signature, Node and Python would have to agree byte for
    byte on key order, separator spacing and non-ASCII escaping — and they
    do not by default (json.dumps escapes non-ASCII, JSON.stringify does
    not). A customer named "Sécurité Montréal" would then produce a key
    that verifies in the generator and fails on the server, which is the
    worst possible failure mode: it only shows up at a customer site.
    Signing the encoded form removes that entire class of bug, the same way
    JWS compact serialisation does.

    Only the public key ever reaches this side. It can check signatures and
    cannot create them, so shipping it in every deployment is safe.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

PREFIX = "SAV1"
KEY_TYPES = ("server", "desktop")


class LicenceError(Exception):
    """A key that cannot be trusted. The message is shown to an admin, so it
    explains what to do rather than what failed internally."""


@dataclass(frozen=True)
class Licence:
    """A verified key. Reaching this type means the signature checked out —
    it says nothing about whether the licence is still in date."""
    version: int
    type: str
    licence_id: str
    customer: str
    bind: str
    issued: date
    expires: date
    seats: int | None
    modules: list[str] | None
    raw: str

    def is_expired(self, today: date) -> bool:
        return today > self.expires

    def days_remaining(self, today: date) -> int:
        return (self.expires - today).days


def _b64u_decode(segment: str) -> bytes:
    # base64url without padding is what both sides emit; Python's decoder
    # insists on padding, so put it back rather than emitting a
    # non-standard padded key that other tools would choke on.
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def load_public_key(raw_b64u: str) -> ed25519.Ed25519PublicKey:
    """Build a key object from the 32-byte base64url value the generator
    shows under "Public key"."""
    if not raw_b64u:
        raise LicenceError("No licence public key is configured on this server")
    try:
        raw = _b64u_decode(raw_b64u.strip())
    except Exception as exc:
        raise LicenceError("Licence public key is not valid base64url") from exc
    if len(raw) != 32:
        raise LicenceError(
            f"Licence public key must be 32 bytes, got {len(raw)} — "
            "copy it from the generator's Public key panel"
        )
    return ed25519.Ed25519PublicKey.from_public_bytes(raw)


def verify_licence(key: str, public_key: ed25519.Ed25519PublicKey) -> Licence:
    """Signature and structure only.

    Expiry is deliberately NOT checked here so the caller can tell an admin
    "your licence lapsed on 3 March" instead of the far less useful "invalid
    key" — a lapsed licence and a forged one need very different responses.
    """
    if not isinstance(key, str) or not key.strip():
        raise LicenceError("No licence key supplied")

    parts = key.strip().split(".")
    if len(parts) != 3:
        raise LicenceError("Licence key is malformed — copy the whole key, including the SAV1 prefix")
    prefix, payload_b64, sig_b64 = parts
    if prefix != PREFIX:
        raise LicenceError(f"Unsupported licence key version '{prefix}'")

    try:
        signature = _b64u_decode(sig_b64)
    except Exception as exc:
        raise LicenceError("Licence key signature is not valid base64url") from exc

    try:
        public_key.verify(signature, f"{prefix}.{payload_b64}".encode("ascii"))
    except InvalidSignature:
        raise LicenceError(
            "Licence key signature does not match — the key was altered, "
            "or it was not issued for this product"
        ) from None

    try:
        payload = json.loads(_b64u_decode(payload_b64).decode("utf-8"))
    except Exception as exc:
        raise LicenceError("Licence key payload is unreadable") from exc

    return _to_licence(payload, key)


def _to_licence(payload: dict, raw: str) -> Licence:
    try:
        key_type = payload["typ"]
        if key_type not in KEY_TYPES:
            raise LicenceError(f"Unknown licence type '{key_type}'")
        return Licence(
            version=int(payload.get("v", 1)),
            type=key_type,
            licence_id=payload.get("lic", ""),
            customer=payload.get("cust", ""),
            bind=payload.get("bind", ""),
            issued=date.fromisoformat(payload["iat"]),
            expires=date.fromisoformat(payload["exp"]),
            seats=payload.get("seats"),
            modules=payload.get("mods"),
            raw=raw,
        )
    except LicenceError:
        raise
    except (KeyError, ValueError) as exc:
        raise LicenceError(f"Licence key is missing or has a bad field: {exc}") from exc


def check_binding(licence: Licence, install_id: str) -> None:
    """An unbound key (empty `bind`) runs anywhere — that is the generator's
    documented trial behaviour, not an oversight. A bound key must match."""
    if not licence.bind:
        return
    if licence.bind != install_id:
        raise LicenceError(
            "This licence key was issued for a different installation. "
            f"It is locked to '{licence.bind}', this system is '{install_id}'."
        )
