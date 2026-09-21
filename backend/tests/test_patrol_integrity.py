"""Checking that patrol evidence is still what was recorded.

THE TEST THAT MATTERS IS THE ONE WHERE A FILE CHANGES. A verifier that has only
ever returned PASSED has not been verified — it would pass just as happily if it
compared nothing at all. So the central case here corrupts a real file on disk
by one byte and asserts the mismatch is caught, and a companion asserts the same
file passes before and after.

Every patrol snapshot on this system has carried a SHA-256 since 0116, and not
one had ever been read back and compared. That is what these cover.

Sections:
  A — Deciding a status from a hash (5 tests)
  B — Hashing a file (2 tests)
  C — A whole session, including a corrupted one (4 tests)
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app  # noqa: F401
from app.core.config import settings
from app.services import patrol_integrity as pi

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

JPEG = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01"
        b"\x00\x00 patrol evidence \xff\xd9")


async def _sql(stmt: str, params: dict | None = None):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            r = await s.execute(text(stmt), params or {})
            rows = r.mappings().all() if r.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


# ─── A. Deciding a status ────────────────────────────────────────────────────

def test_matching_hashes_pass():
    assert pi.classify("abc", "abc") == pi.PASSED


def test_different_hashes_are_a_mismatch():
    assert pi.classify("abc", "def") == pi.MISMATCH


def test_no_file_is_not_a_mismatch():
    """A file that has gone is an operational problem, not evidence of
    tampering. Reporting it as a mismatch sends somebody hunting the wrong
    thing."""
    assert pi.classify("abc", None) == pi.FILE_MISSING


def test_no_recorded_hash_is_not_a_pass():
    """A report written before 0121 has no checksum. It is unverifiable, which
    is a gap in coverage — calling it PASSED would claim an assurance nobody
    has."""
    assert pi.classify(None, "abc") == pi.NOT_RECORDED
    assert pi.classify(None, None) == pi.NOT_RECORDED


def test_a_missing_hash_wins_over_a_missing_file():
    """Both are absent; the honest answer is that nothing was ever recorded to
    check against, not that the file vanished."""
    assert pi.classify("", None) == pi.NOT_RECORDED


# ─── B. Hashing a file ───────────────────────────────────────────────────────

def test_a_files_hash_matches_hashlib(tmp_path):
    f = tmp_path / "x.jpg"
    f.write_bytes(JPEG)
    assert pi.sha256_of(f) == hashlib.sha256(JPEG).hexdigest()


def test_a_file_that_is_not_there_hashes_to_nothing(tmp_path):
    assert pi.sha256_of(tmp_path / "nope.jpg") is None


# ─── C. A whole session ──────────────────────────────────────────────────────

async def _session_with_snapshot(*, correct_checksum: bool = True):
    """A completed patrol with one real file on disk."""
    i = {k: uuid.uuid4() for k in ("tenant", "site", "session", "cam")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'Integrity Co',:s)",
               {"t": i["tenant"], "s": f"intg-{i['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
               {"i": i["site"], "t": i["tenant"]})
    await _sql(
        "INSERT INTO virtual_patrol_sessions "
        "  (id, tenant_id, site_id, patrol_number, schedule_name, scheduled_for, "
        "   status, completed_at) "
        "VALUES (:i,:t,:s,:n,'Morning Patrol',:w,'COMPLETED', now())",
        {"i": i["session"], "t": i["tenant"], "s": i["site"],
         "n": f"VP-INT-{i['session'].hex[:8]}",
         "w": datetime.now(timezone.utc) - timedelta(hours=2)})

    rel = f"integrity-test/{i['cam'].hex}.jpg"
    disk = Path(settings.EVIDENCE_ROOT) / rel
    disk.parent.mkdir(parents=True, exist_ok=True)
    disk.write_bytes(JPEG)

    checksum = (hashlib.sha256(JPEG).hexdigest() if correct_checksum
                else hashlib.sha256(b"something else").hexdigest())
    await _sql(
        "INSERT INTO virtual_patrol_session_cameras "
        "  (id, tenant_id, session_id, sequence_no, camera_name, snapshot_path, "
        "   snapshot_checksum, snapshot_bytes, status) "
        "VALUES (:i,:t,:se,1,'Main Gate',:p,:c,:b,'COMPLETED')",
        {"i": i["cam"], "t": i["tenant"], "se": i["session"], "p": rel,
         "c": checksum, "b": len(JPEG)})
    i["path"] = disk
    return i


async def _verify(tenant_id, session_id):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                            {"t": str(tenant_id)})
            return await pi.verify_session(s, str(session_id))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_untouched_snapshot_passes():
    w = await _session_with_snapshot()
    r = await _verify(w["tenant"], w["session"])
    assert r["verdict"] == pi.PASSED, r
    assert r["checked"] == 1


@pytest.mark.asyncio
async def test_a_file_changed_by_one_byte_is_caught():
    """THE CENTRAL TEST. One appended byte is the smallest thing a truncated or
    partial write does, and without this the verifier could be comparing
    nothing and still look healthy."""
    w = await _session_with_snapshot()
    backup = w["path"].with_suffix(".bak")
    shutil.copy2(w["path"], backup)
    try:
        with w["path"].open("ab") as fh:
            fh.write(b"\x00")
        r = await _verify(w["tenant"], w["session"])
    finally:
        shutil.move(str(backup), str(w["path"]))

    assert r["verdict"] == pi.MISMATCH, r
    bad = [c for c in r["checks"] if c["status"] == pi.MISMATCH]
    assert bad and bad[0]["label"] == "Main Gate"
    # Both hashes are surfaced, but only on the row that disagrees.
    assert bad[0]["expected_sha256"] != bad[0]["actual_sha256"]


@pytest.mark.asyncio
async def test_the_same_file_passes_again_once_restored():
    """Proves the test above is detecting the change rather than the fixture
    being broken from the start."""
    w = await _session_with_snapshot()
    before = await _verify(w["tenant"], w["session"])
    assert before["verdict"] == pi.PASSED

    backup = w["path"].with_suffix(".bak")
    shutil.copy2(w["path"], backup)
    with w["path"].open("ab") as fh:
        fh.write(b"\x00")
    during = await _verify(w["tenant"], w["session"])
    shutil.move(str(backup), str(w["path"]))
    after = await _verify(w["tenant"], w["session"])

    assert (before["verdict"], during["verdict"], after["verdict"]) == (
        pi.PASSED, pi.MISMATCH, pi.PASSED)


@pytest.mark.asyncio
async def test_a_deleted_file_reports_missing_not_mismatch():
    w = await _session_with_snapshot()
    backup = w["path"].with_suffix(".bak")
    shutil.move(str(w["path"]), str(backup))
    try:
        r = await _verify(w["tenant"], w["session"])
    finally:
        shutil.move(str(backup), str(w["path"]))
    assert r["verdict"] == pi.FILE_MISSING, r


@pytest.mark.asyncio
async def test_the_response_states_what_it_does_not_prove():
    """The scope sentence is part of the contract. Answering the narrow
    question and letting it be heard as the broad one would be worse than not
    answering."""
    w = await _session_with_snapshot()
    r = await _verify(w["tenant"], w["session"])
    assert "database" in r["scope"].lower(), r["scope"]
