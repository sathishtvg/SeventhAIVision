"""Making verify_checksums mean what it has always claimed.

recording_policies.verify_checksums defaults to TRUE, is stored per site, and is
shown to whoever configures that site. Before this it appeared in the
application exactly once — in a SELECT column list. Nothing read it, and
`recordings` had no checksum column, so there was never anything to verify. An
operator saw integrity checking switched on everywhere and none had ever
happened.

THE TWO TESTS THAT MATTER ARE THE ONES WHERE THE FLAG DECIDES SOMETHING. A
sweep that verifies everything would pass a test asserting a corrupted file is
caught, and would still be ignoring the setting. So one test turns the flag OFF
and asserts the recording is deliberately left alone, and another leaves it ON
and asserts the same file is caught. The pair is what proves the flag is read.

Sections:
  A — Hashing (2 tests)
  B — Recording a checksum at finalisation (2 tests)
  C — The sweep, and the flag deciding (4 tests)
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app  # noqa: F401
from app.services import recording_integrity as ri

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

CLIP = b"\x00\x00\x00\x18ftypmp42" + b"not really video, but bytes are bytes" * 4


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


# ─── A. Hashing ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_clip_hashes_to_the_same_thing_as_hashlib(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(CLIP)
    assert await ri.hash_file(f) == hashlib.sha256(CLIP).hexdigest()


@pytest.mark.asyncio
async def test_a_missing_clip_hashes_to_nothing(tmp_path):
    assert await ri.hash_file(tmp_path / "gone.mp4") is None


# ─── B. Recording a checksum ─────────────────────────────────────────────────

async def _world(*, verify: bool | None, write_file: bool = True,
                 with_checksum: bool = True):
    """A tenant, a site with (or without) a policy, and one completed recording."""
    i = {k: uuid.uuid4() for k in ("tenant", "site", "camera", "stream", "rec")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'Rec Co',:s)",
               {"t": i["tenant"], "s": f"rec-{i['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
               {"i": i["site"], "t": i["tenant"]})
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) "
               "VALUES (:i,:t,:s,'Gate')",
               {"i": i["camera"], "t": i["tenant"], "s": i["site"]})
    await _sql("INSERT INTO streams (tenant_id, id, camera_id, protocol, url, status) "
               "VALUES (:t,:i,:c,'rtsp','rtsp://x/1','active')",
               {"t": i["tenant"], "i": i["stream"], "c": i["camera"]})

    if verify is not None:
        await _sql(
            "INSERT INTO recording_policies (tenant_id, site_id, record_mode, "
            "                                sync_mode, verify_checksums, is_active) "
            "VALUES (:t,:s,'continuous','scheduled',:v,TRUE)",
            {"t": i["tenant"], "s": i["site"], "v": verify})

    rel = f"integrity-test/{i['rec'].hex}.mp4"
    disk = Path(ri.RECORDINGS_ROOT) / rel
    if write_file:
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_bytes(CLIP)

    await _sql(
        "INSERT INTO recordings (id, tenant_id, camera_id, stream_id, site_id, "
        "                        status, file_path, file_size_bytes, checksum_sha256) "
        "VALUES (:i,:t,:c,:st,:s,'completed',:p,:n,:sum)",
        {"i": i["rec"], "t": i["tenant"], "c": i["camera"], "st": i["stream"],
         "s": i["site"], "p": rel, "n": len(CLIP),
         "sum": hashlib.sha256(CLIP).hexdigest() if with_checksum else None})
    i["path"] = disk
    i["rel"] = rel
    return i


@pytest.mark.asyncio
async def test_a_checksum_can_be_recorded_for_a_finished_file():
    w = await _world(verify=True, with_checksum=False)
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                            {"t": str(w["tenant"])})
            digest = await ri.record_checksum(s, str(w["rec"]), w["path"])
            await s.commit()
    finally:
        await engine.dispose()
    assert digest == hashlib.sha256(CLIP).hexdigest()

    rows = await _sql("SELECT checksum_sha256 FROM recordings WHERE id = :i",
                      {"i": w["rec"]})
    assert rows[0]["checksum_sha256"] == digest


@pytest.mark.asyncio
async def test_an_unhashable_file_does_not_cost_the_recording():
    """A segment that cannot be read keeps its row with a NULL checksum. Losing
    footage because the integrity feature stumbled would be the worse
    outcome."""
    w = await _world(verify=True, write_file=False, with_checksum=False)
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                            {"t": str(w["tenant"])})
            digest = await ri.record_checksum(s, str(w["rec"]), w["path"])
            await s.commit()
    finally:
        await engine.dispose()

    assert digest is None
    rows = await _sql("SELECT id, checksum_sha256 FROM recordings WHERE id = :i",
                      {"i": w["rec"]})
    assert rows, "the recording row was lost because hashing failed"
    assert rows[0]["checksum_sha256"] is None


# ─── C. The sweep, and the flag deciding ─────────────────────────────────────

async def _sweep():
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            return await ri.verify_recent(s)
    finally:
        await engine.dispose()


async def _status_of(rec_id):
    rows = await _sql("SELECT checksum_status, checksum_verified_at "
                      "  FROM recordings WHERE id = :i", {"i": rec_id})
    return rows[0]


@pytest.mark.asyncio
async def test_an_intact_recording_passes():
    w = await _world(verify=True)
    await _sweep()
    assert (await _status_of(w["rec"]))["checksum_status"] == ri.PASSED


@pytest.mark.asyncio
async def test_a_tampered_recording_is_caught_when_the_policy_asks():
    w = await _world(verify=True)
    with w["path"].open("ab") as fh:
        fh.write(b"\x00")
    await _sweep()
    assert (await _status_of(w["rec"]))["checksum_status"] == ri.MISMATCH


@pytest.mark.asyncio
async def test_the_same_tampered_recording_is_left_alone_when_the_policy_says_no():
    """THE TEST THAT PROVES THE FLAG IS READ. Identical corruption to the case
    above; the only difference is verify_checksums = FALSE. A sweep that
    verified everything would pass that test and still be ignoring the setting,
    which is the bug this whole change exists to fix — in the other direction.
    """
    w = await _world(verify=False)
    with w["path"].open("ab") as fh:
        fh.write(b"\x00")
    result = await _sweep()

    row = await _status_of(w["rec"])
    assert row["checksum_status"] is None, (
        "a site with verify_checksums=false had its recording verified anyway")
    assert row["checksum_verified_at"] is None
    assert result["skipped_by_policy"] >= 1


@pytest.mark.asyncio
async def test_a_site_with_no_policy_is_verified_by_default():
    """verify_checksums defaults to TRUE in the policy model, so a site that has
    never been configured should behave as though it is on. Defaulting to off
    would silently stop checking for every site nobody has touched."""
    w = await _world(verify=None)
    with w["path"].open("ab") as fh:
        fh.write(b"\x00")
    await _sweep()
    assert (await _status_of(w["rec"]))["checksum_status"] == ri.MISMATCH
