#!/usr/bin/env python3
"""Check a Seventh AI Vision evidence export: that the platform signed it, and that every file in it is as it was.

    python3 check_signature.py                  in the folder the export was unpacked into
    python3 check_signature.py PKG-0004.zip     or given the export itself, or its folder

It says "GENUINE" and why, or exactly what does not match. It changes nothing.

It needs Python 3.8 or later. The signature is checked with the `cryptography` library when it is installed
(pip install cryptography), and otherwise with openssl 3, which the README also shows how to use by hand. Before
relying on the answer, compare the key fingerprint it prints with the one the organisation that sent the export gives
you: anybody can make a key; only the organisation can tell you which key is theirs.

This one file is also what the platform's own "Check an export" page runs (backend/app/services/evidence_signing.py),
so the two can never say different things.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile

EXPORT_FORMAT = "seventh-evidence-export-signature/1"
SEAL_FORMAT = "seventh-evidence-seal-signature/1"
#: The DER prefix of an Ed25519 public key (SubjectPublicKeyInfo); the 32 bytes after it are the key.
_ED25519_SPKI = bytes.fromhex("302a300506032b6570032100")
_CHUNK = 1024 * 1024


def canonical(statement: dict) -> bytes:
    """A statement written one way only: the bytes that are signed."""
    return json.dumps(statement, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def raw_public_key(pem: bytes) -> bytes:
    body = b"".join(line.strip() for line in pem.splitlines() if line.strip() and not line.startswith(b"-----"))
    der = base64.b64decode(body)
    if len(der) != 44 or not der.startswith(_ED25519_SPKI):
        raise ValueError("not an Ed25519 public key")
    return der[len(_ED25519_SPKI):]


def fingerprint(pem: bytes) -> str:
    """The SHA-256 of the raw public key, in hex: what the platform names a key by."""
    return hashlib.sha256(raw_public_key(pem)).hexdigest()


def spaced(fp: str) -> str:
    """A fingerprint as it is read out: groups of four."""
    return " ".join(fp[i:i + 4] for i in range(0, len(fp), 4))


def verify(pem: bytes, payload: bytes, signature: bytes):
    """True or False, or None with why when nothing here can check an Ed25519 signature."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
    except ImportError:
        return _verify_with_openssl(pem, payload, signature)
    try:
        load_pem_public_key(pem).verify(signature, payload)
        return True, "cryptography"
    except InvalidSignature:
        return False, "cryptography"
    except (ValueError, TypeError):
        return False, "the public key could not be read"


def _verify_with_openssl(pem: bytes, payload: bytes, signature: bytes):
    with tempfile.TemporaryDirectory() as where:
        paths = {}
        for name, data in (("key.pem", pem), ("payload", payload), ("sig", signature)):
            paths[name] = os.path.join(where, name)
            with open(paths[name], "wb") as f:
                f.write(data)
        try:
            done = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", paths["key.pem"], "-rawin",
                                   "-in", paths["payload"], "-sigfile", paths["sig"]], capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None, "neither the cryptography library nor openssl 3 is available here"
        return done.returncode == 0, "openssl"


class Export:
    """An export as a .zip or as the folder it was unpacked into."""

    def __init__(self, source: str):
        self.zip = zipfile.ZipFile(source) if os.path.isfile(source) else None
        self.root = None if self.zip else source

    def read(self, name: str):
        try:
            if self.zip:
                return self.zip.read(name)
            with open(os.path.join(self.root, *name.split("/")), "rb") as f:
                return f.read()
        except (KeyError, OSError):
            return None

    def sha256(self, name: str):
        h = hashlib.sha256()
        try:
            stream = self.zip.open(name) if self.zip else open(os.path.join(self.root, *name.split("/")), "rb")
        except (KeyError, OSError):
            return None
        with stream:
            for chunk in iter(lambda: stream.read(_CHUNK), b""):
                h.update(chunk)
        return h.hexdigest()

    def close(self):
        if self.zip:
            self.zip.close()


def check(source: str) -> dict:
    """What the export says of itself, checked. `genuine` only when the signature is the one its key made and every
    file is the one that was signed."""
    export = Export(source)
    try:
        return _check(export)
    finally:
        export.close()


def _check(e: Export) -> dict:
    checks = []

    def said(what, ok, detail=""):
        checks.append({"what": what, "ok": ok, "detail": detail})

    out = {"genuine": False, "signed": False, "key_fingerprint": None, "package_number": None, "exported_at": None,
           "exported_by": None, "seal": None, "checks": checks}
    statement_bytes, signature, pem = e.read("signature.json"), e.read("signature.bin"), e.read("public_key.pem")
    if statement_bytes is None:
        said("The export carries a signature", False,
             "There is no signature.json: it was made before exports were signed, or it was not signed.")
        return out
    try:
        statement = json.loads(statement_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        said("signature.json can be read", False, "It is not the platform's statement.")
        return out
    if statement.get("format") != EXPORT_FORMAT:
        said("signature.json is an export's statement", False, f"Its format is {statement.get('format')!r}.")
        return out
    out.update(signed=True, package_number=statement.get("package_number"), exported_at=statement.get("exported_at"),
               exported_by=statement.get("exported_by"), seal=statement.get("seal"))
    if signature is None or pem is None:
        said("The signature and the public key are there", False, "signature.bin or public_key.pem is missing.")
        return out
    try:
        fp = fingerprint(pem)
    except ValueError:
        said("public_key.pem is an Ed25519 public key", False, "It is not.")
        return out
    out["key_fingerprint"] = fp
    ok, how = verify(pem, statement_bytes, signature)
    said("The signature was made by the key in public_key.pem", ok,
         f"Key {spaced(fp)}. Checked with {how}." if ok is not None else how)
    said("public_key.pem is the key the statement names", fp == statement.get("key_fingerprint"),
         "" if fp == statement.get("key_fingerprint") else "The statement names another key.")

    export_bytes, manifest_bytes = e.read("export.json"), e.read("manifest.json")
    said("export.json is the one that was signed",
         export_bytes is not None and hashlib.sha256(export_bytes).hexdigest() == statement.get("export_json_sha256"),
         "" if export_bytes is not None else "export.json is missing.")
    said("manifest.json is the one that was sealed",
         manifest_bytes is not None and hashlib.sha256(manifest_bytes).hexdigest() == statement.get("manifest_sha256"),
         "" if manifest_bytes is not None else "manifest.json is missing.")

    wrong, counted = [], 0
    try:
        listed = json.loads(export_bytes.decode("utf-8"))["items"] if export_bytes else []
    except (UnicodeDecodeError, ValueError, KeyError):
        listed = []
        wrong.append("export.json cannot be read")
    for item in listed:
        for name, expected in ((item.get("file"), item.get("sha256_now")),
                               (item.get("viewing_copy"), item.get("viewing_copy_sha256"))):
            if not name or not expected:
                continue
            counted += 1
            if e.sha256(name) != expected:
                wrong.append(name)
        if item.get("file") and item.get("sha256_in_manifest") and item.get("sha256_now") \
                and item["sha256_now"] != item["sha256_in_manifest"]:
            wrong.append(f"{item['file']} (does not match the checksum recorded when it was captured)")
    said("Every file is the one that was exported", not wrong,
         f"{counted} file(s) compared." if not wrong else "Not as exported: " + "; ".join(wrong[:20]))

    seal_bytes = e.read("seal_signature.json")
    if seal_bytes is not None:
        try:
            seal = json.loads(seal_bytes.decode("utf-8"))
            seal_statement = seal["statement"].encode("utf-8")
            seal_pem = seal["public_key_pem"].encode("ascii")
            seal_ok, seal_how = verify(seal_pem, seal_statement, base64.b64decode(seal["signature"]))
            sealed = json.loads(seal_statement)
            same = (sealed.get("format") == SEAL_FORMAT and sealed.get("manifest_sha256") == statement.get("manifest_sha256")
                    and fingerprint(seal_pem) == sealed.get("key_fingerprint"))
            said("The package was signed when it was sealed, and its contents list has not changed since",
                 bool(seal_ok) and same if seal_ok is not None else None,
                 f"Sealed {sealed.get('sealed_at')} by {sealed.get('sealed_by')}, key "
                 f"{spaced(sealed.get('key_fingerprint') or '')}." if seal_ok is not None else seal_how)
        except (UnicodeDecodeError, ValueError, KeyError, TypeError):
            said("seal_signature.json can be read", False, "It is not the platform's seal signature.")
    elif statement.get("seal") == "BEFORE_SIGNING":
        said("The package was signed when it was sealed", None, "It was sealed before signing began.")
    elif statement.get("seal") == "NOT_SIGNED":
        said("The package was signed when it was sealed", None,
             "It was not: the signing key could not be read when it was sealed.")

    out["genuine"] = ok is True and all(c["ok"] is not False for c in checks)
    return out


def main(argv) -> int:
    source = argv[1] if len(argv) > 1 else "."
    result = check(source)
    print("GENUINE" if result["genuine"] else "NOT SHOWN TO BE GENUINE")
    if result["package_number"]:
        print(f"Package {result['package_number']}, exported {result['exported_at']} by {result['exported_by']}")
    if result["key_fingerprint"]:
        print(f"Signed with the key {spaced(result['key_fingerprint'])}")
        print("Compare it with the fingerprint the organisation that sent this gives you.")
    for c in result["checks"]:
        mark = {True: "ok  ", False: "FAIL", None: "--  "}[c["ok"]]
        print(f"  {mark} {c['what']}" + (f": {c['detail']}" if c["detail"] else ""))
    return 0 if result["genuine"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
