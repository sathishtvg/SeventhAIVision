"""Evidence digitally signed: the installation's key, the seal's and each export's signature, and checking an export.

Phase 7 of closing the feature reference's known limits (docs/plans/2026-11-05-evidence-watermark-and-signing-design.md).

  current()        the installation's signing key: from EVIDENCE_SIGNING_KEY when it is given, else from a file on the
                   signing_keys volume, made the first time it is needed. None, with why, when it cannot be read.
  record_key()     the key's public half kept in evidence_signing_keys; the one before it marked retired.
  sign_seal()      a package's seal statement signed and kept - or kept unsigned, saying why.
  export_files()   what an export carries to be checked: the statement, its signature, the public key, the seal's.
  record_export()  an export's statement and signature kept.
  check_upload()   an uploaded export checked (evidence_signature_check.check) and told what the platform knows of
                   it: whether the key is this installation's, and whether the package's sealed record agrees.

THE PRIVATE KEY IS NEVER IN THE DATABASE, never logged and never returned. Only its public half and fingerprint are.

ONE KEY PROBLEM NEVER HOLDS UP EVIDENCE. A key that cannot be read leaves sealing and exporting as they were; what
they make says "not signed: the signing key could not be read", and the error is logged. Nothing is ever signed after
the fact: a package sealed before signing began says so.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import evidence_signature_check as checker

logger = logging.getLogger(__name__)

ENV = "EVIDENCE_SIGNING_KEY"
KEY_FILE = os.environ.get("EVIDENCE_SIGNING_KEY_FILE", "/data/signing/evidence_ed25519.pem")
NOT_READ = "the signing key could not be read"
EXPORT_FORMAT, SEAL_FORMAT = checker.EXPORT_FORMAT, checker.SEAL_FORMAT
SEAL_STATES = ("SIGNED", "NOT_SIGNED", "BEFORE_SIGNING")
SEAL_WORDS = {"SIGNED": "Sealed and signed", "NOT_SIGNED": f"Sealed, not signed: {NOT_READ}",
              "BEFORE_SIGNING": "Sealed before signing began"}


@dataclass(frozen=True)
class Signer:
    """The installation's key, as the rest of the platform may hold it: it signs, and says which key it is."""
    _key: Any
    source: str
    fingerprint: str
    public_pem: str

    def sign(self, payload: bytes) -> bytes:
        return self._key.sign(payload)


_lock = threading.Lock()
_cached: dict[str, Any] = {}


def _from_text(value: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    value = value.strip()
    if value.startswith("-----BEGIN"):
        key = load_pem_private_key(value.encode("ascii"), password=None)
    else:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(value, validate=True))
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("not an Ed25519 key")
    return key


def _pem(key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def make_key_file(path: str | Path) -> bool:
    """Make a new key in `path` if there is none, readable by its owner alone. False when one was there already."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "wb") as f:
        f.write(_pem(Ed25519PrivateKey.generate()))
    return True


def _signer(key, source: str) -> Signer:
    from cryptography.hazmat.primitives import serialization

    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    return Signer(key, source, checker.fingerprint(public), public.decode("ascii"))


def current(key_file: str | Path | None = None) -> tuple[Signer | None, str | None]:
    """(the signer, None), or (None, why). Read again when the file changes, so a replaced key is used at once."""
    given = os.environ.get(ENV, "").strip()
    path = Path(key_file or KEY_FILE)
    with _lock:
        try:
            if given:
                if _cached.get("from") != ("SECRET", given):
                    _cached.update({"from": ("SECRET", given), "signer": _signer(_from_text(given), "SECRET")})
                return _cached["signer"], None
            if not path.exists():
                make_key_file(path)
            stamp = path.stat()
            marker = ("FILE", str(path), stamp.st_mtime_ns, stamp.st_ino, stamp.st_size)
            if _cached.get("from") != marker:
                _cached.update({"from": marker, "signer": _signer(_from_text(path.read_text("ascii")), "FILE")})
            return _cached["signer"], None
        except (OSError, ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
            _cached.clear()
            logger.error("evidence signing: %s (%s)", NOT_READ, type(exc).__name__)
            return None, NOT_READ


def forget() -> None:
    """Drop the key held in memory (tests, and a key replaced by hand)."""
    with _lock:
        _cached.clear()


async def record_key(db: AsyncSession, signer: Signer) -> None:
    """Keep the key's public half; when it is new, the one in use before it is retired from now."""
    made = (await db.execute(text("""
        INSERT INTO evidence_signing_keys (fingerprint, public_key_pem, source) VALUES (:fp, :pem, :src)
        ON CONFLICT (fingerprint) DO NOTHING
    """), {"fp": signer.fingerprint, "pem": signer.public_pem, "src": signer.source})).rowcount
    if made:
        await db.execute(text("""
            UPDATE evidence_signing_keys SET retired_at = now() WHERE retired_at IS NULL AND fingerprint <> :fp
        """), {"fp": signer.fingerprint})


async def keys(db: AsyncSession) -> list[dict]:
    rows = await db.execute(text("""
        SELECT fingerprint, source, first_used_at, retired_at FROM evidence_signing_keys ORDER BY first_used_at DESC
    """))
    return [{**dict(r), "spaced": checker.spaced(r["fingerprint"]), "in_use": r["retired_at"] is None}
            for r in rows.mappings()]


def _stamp(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


async def sign_seal(db: AsyncSession, package: Mapping, manifest_sha256: str, sealed_at: datetime,
                    sealed_by: str, *, key_file: str | Path | None = None) -> dict:
    """Sign a package's seal and keep it - or keep that it is not signed, and why. The caller commits."""
    signer, why = current(key_file)
    statement = {"format": SEAL_FORMAT, "package_id": str(package["id"]), "package_number": package["package_number"],
                 "manifest_sha256": manifest_sha256,
                 "sealed_at": _stamp(sealed_at), "sealed_by": sealed_by,
                 "key_fingerprint": signer.fingerprint if signer else None}
    body = checker.canonical(statement)
    signature = base64.b64encode(signer.sign(body)).decode("ascii") if signer else None
    if signer:
        await record_key(db, signer)
    await db.execute(text("""
        INSERT INTO evidence_package_signatures (tenant_id, package_id, kind, statement, signature, key_fingerprint,
                                                 not_signed_because, signed_at)
        VALUES (current_setting('app.current_tenant')::uuid, :p, 'SEAL', :st, :sig, :fp, :why, :at)
    """), {"p": package["id"], "st": body.decode("utf-8"), "sig": signature,
           "fp": signer.fingerprint if signer else None, "why": why, "at": sealed_at})
    return {"signed": bool(signer), "key_fingerprint": signer.fingerprint if signer else None, "not_signed_because": why}


async def seal_of(db: AsyncSession, package_id: Any) -> dict | None:
    """A package's seal signature as kept, with the public key that made it; None when it was sealed before signing."""
    row = (await db.execute(text("""
        SELECT s.statement, s.signature, s.key_fingerprint, s.not_signed_because, s.signed_at, k.public_key_pem
          FROM evidence_package_signatures s LEFT JOIN evidence_signing_keys k ON k.fingerprint = s.key_fingerprint
         WHERE s.package_id = CAST(:p AS uuid) AND s.kind = 'SEAL'
    """), {"p": str(package_id)})).mappings().first()
    return dict(row) if row else None


def seal_state(seal: Mapping | None) -> dict:
    """What a package's page says of its seal."""
    state = "BEFORE_SIGNING" if seal is None else "SIGNED" if seal["signature"] else "NOT_SIGNED"
    fp = seal["key_fingerprint"] if seal else None
    return {"state": state, "words": SEAL_WORDS[state], "key_fingerprint": fp,
            "key_spaced": checker.spaced(fp) if fp else None, "signed_at": seal["signed_at"] if seal else None}


def export_statement(package: Mapping, signer: Signer | None, seal: Mapping | None, *, export_json: bytes,
                     exported_at: datetime, exported_by: str) -> bytes:
    """The statement an export's signature is made over: the package, its sealed manifest, export.json, who and when."""
    import hashlib

    return checker.canonical({
        "format": EXPORT_FORMAT, "package_id": str(package["id"]), "package_number": package["package_number"],
        "manifest_sha256": package["manifest_sha256"],
        "export_json_sha256": hashlib.sha256(export_json).hexdigest(), "exported_at": _stamp(exported_at),
        "exported_by": exported_by, "seal": seal_state(seal)["state"],
        "key_fingerprint": signer.fingerprint if signer else None})


def export_files(statement: bytes, signer: Signer | None, seal: Mapping | None) -> tuple[dict[str, bytes], bytes | None]:
    """The files an export carries to be checked, and the signature made (None when there is no key)."""
    if signer is None:
        return {}, None
    signature = signer.sign(statement)
    files = {"signature.json": statement, "signature.bin": signature, "public_key.pem": signer.public_pem.encode("ascii"),
             "check_signature.py": Path(checker.__file__).read_bytes()}
    if seal and seal.get("signature") and seal.get("public_key_pem"):
        files["seal_signature.json"] = json.dumps({
            "statement": seal["statement"], "signature": seal["signature"], "public_key_pem": seal["public_key_pem"],
            "key_fingerprint": seal["key_fingerprint"]}, indent=2, ensure_ascii=False).encode("utf-8")
    return files, signature


async def record_export(db: AsyncSession, package: Mapping, statement: bytes, signature: bytes | None,
                        signer: Signer | None, why: str | None, exported_at: datetime) -> None:
    """Keep an export's statement and signature, or that it is not signed and why. The caller commits."""
    if signer:
        await record_key(db, signer)
    await db.execute(text("""
        INSERT INTO evidence_package_signatures (tenant_id, package_id, kind, statement, signature, key_fingerprint,
                                                 not_signed_because, signed_at)
        VALUES (current_setting('app.current_tenant')::uuid, :p, 'EXPORT', :st, :sig, :fp, :why, :at)
    """), {"p": package["id"], "st": statement.decode("utf-8"),
           "sig": base64.b64encode(signature).decode("ascii") if signature else None,
           "fp": signer.fingerprint if signature else None, "why": None if signature else (why or NOT_READ),
           "at": exported_at})


async def check_upload(db: AsyncSession, path: str | Path) -> dict:
    """An uploaded export checked, with what the platform knows of it. The upload itself is the caller's to delete."""
    import asyncio

    result = await asyncio.to_thread(checker.check, str(path))
    platform: list[dict] = []
    fp = result.get("key_fingerprint")
    if fp:
        known = (await db.execute(text("SELECT first_used_at, retired_at FROM evidence_signing_keys WHERE fingerprint = :fp"),
                                  {"fp": fp})).mappings().first()
        if known is None:
            platform.append({"what": "The key is this installation's", "ok": False,
                             "detail": "This installation has never signed with this key."})
        else:
            since = f"in use since {known['first_used_at']:%Y-%m-%d}"
            platform.append({"what": "The key is this installation's", "ok": True,
                             "detail": since + (f", retired {known['retired_at']:%Y-%m-%d}" if known["retired_at"] else
                                                ", and still in use")})
    number = result.get("package_number")
    if number and result.get("signed"):
        try:
            statement = json.loads(str((await asyncio.to_thread(_read_statement, str(path))) or "{}"))
        except ValueError:
            statement = {}
        row = (await db.execute(text("""
            SELECT id, manifest_sha256 FROM evidence_packages WHERE id = CAST(:p AS uuid)
        """), {"p": statement.get("package_id")})).mappings().first() if statement.get("package_id") else None
        if row is None:
            platform.append({"what": "The package is one of your organisation's", "ok": None,
                             "detail": "It is not, or it has been disposed of, so it was not compared with the "
                                       "platform's record."})
        else:
            same = row["manifest_sha256"] == statement.get("manifest_sha256")
            platform.append({"what": "The platform's record of the sealed package agrees", "ok": same,
                             "detail": "" if same else "The package as the platform holds it was sealed with another "
                                                       "contents list."})
    result["platform"] = platform
    result["genuine"] = result["genuine"] and all(p["ok"] is not False for p in platform)
    return result


def _read_statement(path: str) -> str | None:
    e = checker.Export(path)
    try:
        data = e.read("signature.json")
        return data.decode("utf-8") if data else None
    finally:
        e.close()
