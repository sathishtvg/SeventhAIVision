"""Evidence video watermarked and evidence digitally signed (Phase 7 of the known limits, 5 November 2026).

A. The key: made once, readable by its owner alone; a secret used instead when given; one that cannot be read leaves
   evidence unsigned and says so; a new key retires the one before it; the rotation script keeps the old one.
B. The seal: signed, kept, and said on the package's page; a package sealed before signing began says that.
C. The export: signed; its marked copies of video made, capped and named in export.json; checked offline by the
   included script, with the library and with openssl, and on the staff page; one byte changed in an original, in
   export.json or in the manifest is caught; another key is named as not this installation's; a retired key checks.
D. As the application's role, an organisation sees only its own signatures.
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import text

from app.main import app  # noqa: F401 - imported first, so the first test does not absorb the import
from app.db.session import AsyncSessionLocal
from app.services import evidence_packages as packages
from app.services import evidence_signature_check as checker
from app.services import evidence_signing as signing
from tests.test_drone_api import ADMIN, GUARD, SUPERVISOR, VIEWER, _client, _sql, _world
from tests.test_evidence_packages import (BASE, _candidates, _export, _investigation, _kept, _package, _post, _refs,
                                          _sha, roots)  # noqa: F401 - roots is a fixture
from tests.test_investigation_search import _audit, _seed

CHECK, KEYS = "/api/v1/evidence-signatures/check", "/api/v1/evidence-signatures/keys"


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    """The API's own key file, in the test's own directory; no secret given."""
    monkeypatch.delenv(signing.ENV, raising=False)
    path = tmp_path / "signing" / "evidence_ed25519.pem"
    path.parent.mkdir()
    monkeypatch.setattr(signing, "KEY_FILE", str(path))
    signing.forget()
    yield path
    signing.forget()


def _video(seconds: float, size: str = "320x240") -> bytes:
    with tempfile.TemporaryDirectory() as where:
        out = Path(where) / "v.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=10",
                        "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)],
                       check=True)
        return out.read_bytes()


def _probe(data: bytes) -> dict:
    with tempfile.TemporaryDirectory() as where:
        path = Path(where) / "v.mp4"
        path.write_bytes(data)
        done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=width,height:format=duration", "-of", "json", str(path)],
                              capture_output=True, text=True, check=True)
    got = json.loads(done.stdout)
    return {**got["streams"][0], "duration": float(got["format"]["duration"])}


async def _sealed_with_video(c, w, roots, *, clip_s: float = 3, recording_s: float = 4, seal: bool = True) -> dict:
    """A package with everything of an investigation, its clip and recording real video."""
    seed = await _seed(w)
    kept = await _kept(w, seed, roots)
    for name, seconds in (("clip", clip_s), ("recording", recording_s)):
        if seconds is None:
            continue
        data = _video(seconds)
        kept["files"][name].write_bytes(data)
        kept["data"][name] = data
    await _sql("UPDATE evidence SET checksum_sha256 = :s WHERE id = :i", {"s": _sha(kept["data"]["clip"]),
                                                                       "i": kept["ids"]["clip"]})
    await _sql("UPDATE recordings SET checksum_sha256 = :s, file_size_bytes = :n WHERE id = :i",
               {"s": _sha(kept["data"]["recording"]), "n": len(kept["data"]["recording"]), "i": kept["ids"]["recording"]})
    file_id = await _investigation(c, w, seed)
    package = await _package(c, w, investigation_id=file_id)
    offered = (await _candidates(c, w, package["id"]))["items"]
    assert (await _post(c, w, f"{package['id']}/items", {"items": _refs(offered)})).status_code == 201
    sealed = None
    if seal:
        r = await _post(c, w, f"{package['id']}/seal")
        assert r.status_code == 200, r.text
        sealed = r.json()
    return {"id": package["id"], "number": package["package_number"], "kept": kept, "sealed": sealed}


def _zip_bytes(archive: zipfile.ZipFile, change: dict[str, bytes] | None = None, drop: tuple[str, ...] = ()) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as made:
        for name in archive.namelist():
            if name in drop:
                continue
            made.writestr(name, (change or {}).get(name, archive.read(name)))
    return out.getvalue()


def _check_bytes(data: bytes) -> dict:
    with tempfile.TemporaryDirectory() as where:
        path = Path(where) / "x.zip"
        path.write_bytes(data)
        return checker.check(str(path))


def _said(result: dict) -> dict:
    return {c["what"]: c["ok"] for c in result["checks"] + result.get("platform", [])}


# ─── A. The key ─────────────────────────────────────────────────────────────

def test_a_key_is_made_once_for_its_owner_alone_and_a_secret_is_used_instead_when_given(key_file, monkeypatch):
    first, why = signing.current()
    assert why is None and first.source == "FILE" and key_file.exists()
    assert oct(key_file.stat().st_mode & 0o777) == "0o600"
    made = key_file.read_bytes()
    signing.forget()
    again, _ = signing.current()
    assert again.fingerprint == first.fingerprint and key_file.read_bytes() == made, "made once, then read"
    assert len(first.fingerprint) == 64 and first.public_pem.startswith("-----BEGIN PUBLIC KEY-----")
    assert "PRIVATE" not in first.public_pem

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    given = Ed25519PrivateKey.generate()
    raw = given.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                              serialization.NoEncryption())
    for value in (base64.b64encode(raw).decode(), signing._pem(given).decode()):
        monkeypatch.setenv(signing.ENV, value)
        secret, _ = signing.current()
        assert secret.source == "SECRET" and secret.fingerprint != first.fingerprint
    monkeypatch.setenv(signing.ENV, "not a key")
    assert signing.current() == (None, signing.NOT_READ)
    monkeypatch.delenv(signing.ENV)
    assert signing.current(key_file.parent / "missing" / "k.pem") == (None, signing.NOT_READ), \
        "no disk for the key: not signed, said why"


async def test_a_new_key_retires_the_one_before_it_and_the_rotation_script_keeps_the_old_one(key_file, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "ops"))
    import rotate_evidence_signing_key as rotate

    old, _ = signing.current()
    assert rotate.main([]) == 0 and signing.current()[0].fingerprint == old.fingerprint, "a dry run changes nothing"
    assert rotate.main(["--commit"]) == 0
    kept = [p for p in key_file.parent.iterdir() if ".retired-" in p.name]
    assert len(kept) == 1 and oct(kept[0].stat().st_mode & 0o777) == "0o600"
    new, _ = signing.current()
    assert new.fingerprint != old.fingerprint
    monkeypatch.setenv(signing.ENV, "anything")
    assert rotate.main(["--commit"]) == 2, "a key given as a secret is replaced as a secret"
    monkeypatch.delenv(signing.ENV)
    async with AsyncSessionLocal() as db:
        for signer in (old, new):
            await signing.record_key(db, signer)
        await db.commit()
        rows = {r["fingerprint"]: r for r in await signing.keys(db)}
    assert rows[new.fingerprint]["in_use"] and not rows[old.fingerprint]["in_use"]


# ─── B. The seal ────────────────────────────────────────────────────────────

async def test_a_package_is_signed_when_it_is_sealed_and_its_page_says_so(roots, key_file):
    w = await _world()
    async with _client() as c:
        p = await _sealed_with_video(c, w, roots)
        page = (await c.get(f"{BASE}/{p['id']}", headers=w["h"][VIEWER])).json()
    signer, _ = signing.current()
    assert p["sealed"]["signature"]["state"] == "SIGNED" == page["signature"]["state"]
    assert page["signature"]["words"] == "Sealed and signed" and page["signature"]["key_fingerprint"] == signer.fingerprint
    (row,) = await _sql("SELECT statement, signature, key_fingerprint FROM evidence_package_signatures "
                        "WHERE package_id = :p AND kind = 'SEAL'", {"p": p["id"]})
    statement = json.loads(row["statement"])
    assert statement == {"format": signing.SEAL_FORMAT, "package_id": p["id"], "package_number": p["number"],
                         "manifest_sha256": p["sealed"]["manifest_sha256"], "sealed_at": statement["sealed_at"],
                         "sealed_by": "Role 2 User", "key_fingerprint": signer.fingerprint}
    assert checker.verify(signer.public_pem.encode(), row["statement"].encode(), base64.b64decode(row["signature"]))[0]
    custody = await _sql("SELECT detail FROM evidence_custody_events WHERE package_id = :p AND step = 'SEALED'",
                         {"p": p["id"]})
    detail = custody[0]["detail"] if isinstance(custody[0]["detail"], dict) else json.loads(custody[0]["detail"])
    assert detail["signed_with"] == signer.fingerprint and detail["not_signed_because"] is None


async def test_without_a_readable_key_evidence_is_sealed_and_exported_unsigned_and_says_so(roots, key_file, monkeypatch):
    monkeypatch.setattr(signing, "KEY_FILE", str(key_file.parent / "no-such-disk" / "k.pem"))
    signing.forget()
    w = await _world()
    async with _client() as c:
        p = await _sealed_with_video(c, w, roots, clip_s=None, recording_s=None)
        page = (await c.get(f"{BASE}/{p['id']}", headers=w["h"][ADMIN])).json()
        r, archive = await _export(c, w, p["id"])
    assert page["signature"]["words"] == "Sealed, not signed: the signing key could not be read"
    assert r.status_code == 200 and r.headers["x-evidence-signed"] == "no"
    assert "signature.json" not in archive.namelist() and "check_signature.py" not in archive.namelist()
    export = json.loads(archive.read("export.json"))
    assert (export["signed_with"], export["not_signed_because"], export["seal"]) == (
        None, "the signing key could not be read", "NOT_SIGNED")
    assert "NOT SIGNED" in archive.read("README.txt").decode()
    result = _check_bytes(r.content)
    assert result["genuine"] is False and result["signed"] is False
    assert result["checks"][0]["what"] == "The export carries a signature"


async def test_a_package_sealed_before_signing_began_says_so_and_its_exports_are_signed(roots, key_file):
    w = await _world()
    async with _client() as c:
        p = await _sealed_with_video(c, w, roots, clip_s=None, recording_s=None)
        await _sql("DELETE FROM evidence_package_signatures WHERE package_id = :p", {"p": p["id"]})
        page = (await c.get(f"{BASE}/{p['id']}", headers=w["h"][ADMIN])).json()
        r, archive = await _export(c, w, p["id"])
    assert page["signature"] == {"state": "BEFORE_SIGNING", "words": "Sealed before signing began",
                                 "key_fingerprint": None, "key_spaced": None, "signed_at": None}
    assert "seal_signature.json" not in archive.namelist()
    result = _check_bytes(r.content)
    assert result["genuine"] is True and result["seal"] == "BEFORE_SIGNING"
    assert _said(result)["The package was signed when it was sealed"] is None
    assert "sealed before signing began" in archive.read("README.txt").decode()


# ─── C. The export ──────────────────────────────────────────────────────────

async def test_an_export_carries_marked_copies_of_video_with_their_checksums_and_the_originals_untouched(roots, key_file):
    w = await _world()
    async with _client() as c:
        p = await _sealed_with_video(c, w, roots, clip_s=3, recording_s=4)
        r, archive = await _export(c, w, p["id"], SUPERVISOR)
    assert r.status_code == 200, r.text
    export = json.loads(archive.read("export.json"))
    by_kind = {i["kind"]: i for i in export["items"]}
    for kind, name, seconds in (("CLIP", "clip", 3), ("RECORDING", "recording", 4)):
        item = by_kind[kind]
        assert archive.read(item["file"]) == p["kept"]["data"][name], "the original, byte for byte"
        copy = archive.read(item["viewing_copy"])
        assert item["viewing_copy"].startswith("viewing/") and item["viewing_copy"].endswith(".mp4")
        assert _sha(copy) == item["viewing_copy_sha256"] and copy != p["kept"]["data"][name]
        seen = _probe(copy)
        assert seen["width"] == packages.MARKED_VIDEO_WIDTH and seen["height"] == 480 + 44
        assert abs(seen["duration"] - seconds) < 0.5
    pictures = [i for i in export["items"] if i["viewing_copy"] and i["viewing_copy"].endswith(".jpg")]
    assert len(pictures) == 3 and all(i["viewing_copy_sha256"] for i in pictures)
    readme = archive.read("README.txt").decode()
    assert "each video up to an hour" in readme and "NOT the originals" in readme


async def test_marked_video_is_capped_and_a_video_that_cannot_be_read_gets_no_copy(roots, key_file, monkeypatch):
    monkeypatch.setattr(packages, "MARKED_VIDEO_S", 5)
    w = await _world()
    async with _client() as c:
        p = await _sealed_with_video(c, w, roots, clip_s=3, recording_s=4)
        r, archive = await _export(c, w, p["id"])
        q = await _sealed_with_video(c, w, roots, clip_s=None, recording_s=None)
        r2, fake = await _export(c, w, q["id"])
    videos = [i for i in json.loads(archive.read("export.json"))["items"] if i["kind"] in ("CLIP", "RECORDING")]
    marked = [i for i in videos if i["viewing_copy"]]
    over = [i for i in videos if not i["viewing_copy"]]
    assert len(marked) == 1 and len(over) == 1, "the first fits in five seconds, the second does not"
    assert over[0]["viewing_copy_note"].startswith("No marked copy: over the")
    assert over[0]["included"] is True, "its original still goes"
    unreadable = {i["kind"]: i for i in json.loads(fake.read("export.json"))["items"]}
    assert unreadable["CLIP"]["viewing_copy_note"] == "No marked copy: it could not be read."


async def test_an_export_is_signed_and_checks_offline_with_the_library_with_openssl_and_by_its_own_script(roots, key_file):
    w = await _world()
    async with _client() as c:
        p = await _sealed_with_video(c, w, roots, clip_s=2, recording_s=2)
        r, archive = await _export(c, w, p["id"])
    signer, _ = signing.current()
    assert r.headers["x-evidence-signed"] == signer.fingerprint
    names = set(archive.namelist())
    assert {"signature.json", "signature.bin", "public_key.pem", "seal_signature.json", "check_signature.py"} <= names
    statement = json.loads(archive.read("signature.json"))
    assert statement["export_json_sha256"] == _sha(archive.read("export.json"))
    assert statement["manifest_sha256"] == _sha(archive.read("manifest.json")) == p["sealed"]["manifest_sha256"]
    assert statement["seal"] == "SIGNED" and statement["key_fingerprint"] == signer.fingerprint
    assert archive.read("check_signature.py") == Path(checker.__file__).read_bytes()
    readme = archive.read("README.txt").decode()
    assert checker.spaced(signer.fingerprint) in readme and "openssl pkeyutl -verify" in readme

    result = _check_bytes(r.content)
    assert result["genuine"] is True and all(c["ok"] is not False for c in result["checks"]), result
    assert _said(result)["The package was signed when it was sealed, and its contents list has not changed since"]
    ok, how = checker._verify_with_openssl(archive.read("public_key.pem"), archive.read("signature.json"),
                                           archive.read("signature.bin"))
    assert (ok, how) == (True, "openssl")
    with tempfile.TemporaryDirectory() as where:
        archive.extractall(where)
        done = subprocess.run([sys.executable, "check_signature.py"], cwd=where, capture_output=True, text=True)
        assert done.returncode == 0 and done.stdout.startswith("GENUINE"), done.stdout
        by_hand = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", "public_key.pem", "-rawin",
                                  "-in", "signature.json", "-sigfile", "signature.bin"], cwd=where,
                                 capture_output=True, text=True)
        assert by_hand.returncode == 0 and "Signature Verified Successfully" in by_hand.stdout
    (row,) = await _sql("SELECT signature, key_fingerprint FROM evidence_package_signatures "
                        "WHERE package_id = :p AND kind = 'EXPORT'", {"p": p["id"]})
    assert base64.b64decode(row["signature"]) == archive.read("signature.bin")


async def test_one_changed_byte_is_caught_and_the_check_page_says_so_without_keeping_anything(roots, key_file):
    w = await _world()
    async with _client() as c:
        p = await _sealed_with_video(c, w, roots, clip_s=2, recording_s=2)
        r, archive = await _export(c, w, p["id"])
        export = json.loads(archive.read("export.json"))
        original = next(i["file"] for i in export["items"] if i["kind"] == "SNAPSHOT")
        changed = bytearray(archive.read(original))
        changed[-10] ^= 0xFF
        forged_export = archive.read("export.json").replace(b'"reason": "', b'"reason": "Not ')
        forged_manifest = archive.read("manifest.json").replace(b'"title"', b'"Title"')
        cases = {
            "Every file is the one that was exported": _zip_bytes(archive, {original: bytes(changed)}),
            "export.json is the one that was signed": _zip_bytes(archive, {"export.json": forged_export}),
            "manifest.json is the one that was sealed": _zip_bytes(archive, {"manifest.json": forged_manifest}),
        }
        for failing, data in cases.items():
            assert _said(_check_bytes(data))[failing] is False, failing
        before = {p.name for p in Path(tempfile.gettempdir()).iterdir() if p.name.startswith("evidence-check-")}
        genuine = (await c.post(CHECK, headers=w["h"][VIEWER], files={"file": ("e.zip", r.content)})).json()
        bad = (await c.post(CHECK, headers=w["h"][VIEWER],
                            files={"file": ("e.zip", cases["Every file is the one that was exported"])})).json()
        after = {p.name for p in Path(tempfile.gettempdir()).iterdir() if p.name.startswith("evidence-check-")}
        not_zip = await c.post(CHECK, headers=w["h"][VIEWER], files={"file": ("e.zip", b"hello")})
        empty = await c.post(CHECK, headers=w["h"][VIEWER], files={"file": ("e.zip", b"")})
        guard = await c.post(CHECK, headers=w["h"][GUARD], files={"file": ("e.zip", r.content)})
        keys = (await c.get(KEYS, headers=w["h"][VIEWER])).json()
    assert genuine["genuine"] is True and genuine["kept"] is False
    assert _said(genuine)["The key is this installation's"] is True
    assert _said(genuine)["The platform's record of the sealed package agrees"] is True
    assert bad["genuine"] is False and before == after, "nothing of an upload is left behind"
    assert (not_zip.status_code, empty.status_code, guard.status_code) == (422, 422, 403)
    signer, _ = signing.current()
    assert keys["in_use"] == signer.fingerprint and keys["keys"][0]["spaced"] == checker.spaced(signer.fingerprint)
    checks = await _audit(w, "evidence.export.check")
    assert [a["detail"]["result"] for a in checks] == ["genuine", "not_genuine"]


async def test_another_key_is_named_as_not_this_installations_and_a_retired_key_still_checks(roots, key_file):
    w = await _world()
    async with _client() as c:
        p = await _sealed_with_video(c, w, roots, clip_s=None, recording_s=None)
        r, archive = await _export(c, w, p["id"])
        # Somebody re-signs a changed export with a key of their own: their signature is good, their key is not ours.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        other = signing._signer(Ed25519PrivateKey.generate(), "FILE")
        statement = json.loads(archive.read("signature.json"))
        statement["key_fingerprint"] = other.fingerprint
        body = checker.canonical(statement)
        forged = _zip_bytes(archive, {"signature.json": body, "signature.bin": other.sign(body),
                                      "public_key.pem": other.public_pem.encode()}, drop=("seal_signature.json",))
        theirs = (await c.post(CHECK, headers=w["h"][ADMIN], files={"file": ("e.zip", forged)})).json()
        # The key is replaced; an export signed with the old one still checks, and says the key is retired.
        os.replace(key_file, key_file.with_name("old.pem"))
        signing.forget()
        q = await _sealed_with_video(c, w, roots, clip_s=None, recording_s=None)
        await _export(c, w, q["id"])
        old = (await c.post(CHECK, headers=w["h"][ADMIN], files={"file": ("e.zip", r.content)})).json()
    assert _said(theirs)["The signature was made by the key in public_key.pem"] is True
    assert _said(theirs)["The key is this installation's"] is False and theirs["genuine"] is False
    key = next(x for x in old["platform"] if x["what"] == "The key is this installation's")
    assert old["genuine"] is True and key["ok"] is True and "retired" in key["detail"]


# ─── D. Organisations kept apart ────────────────────────────────────────────

async def test_as_the_application_role_an_organisation_sees_only_its_own_signatures(roots, key_file):
    w, other = await _world(), await _world()
    async with _client() as c:
        mine = await _sealed_with_video(c, w, roots, clip_s=None, recording_s=None)
        await _sealed_with_video(c, other, roots, clip_s=None, recording_s=None)
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles "
                                          "WHERE rolname = current_user"))).scalar()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        seen = (await db.execute(text("SELECT package_id FROM evidence_package_signatures"))).scalars().all()
        assert [str(s) for s in seen] == [mine["id"]]
        try:
            await db.execute(text("DELETE FROM evidence_package_signatures"))
            raise AssertionError("the application removed a signature")
        except Exception as exc:
            assert "permission denied" in str(exc)
        await db.rollback()


def test_the_script_in_the_zip_needs_nothing_of_the_platform():
    source = Path(checker.__file__).read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source
    assert shutil.which("openssl"), "the README's commands are openssl's"
