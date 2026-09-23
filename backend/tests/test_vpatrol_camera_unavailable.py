"""An officer reporting a camera that is not working, and finishing the patrol.

THE HOLE THIS CLOSES. A camera the snapshot service finds offline is marked
CAMERA_UNAVAILABLE: current-camera skips it, derive_session_status counts it,
and the patrol ends PARTIALLY_COMPLETED. A camera whose capture *errors* gets
SNAPSHOT_FAILED, which neither skips nor counts — so the officer was stuck on
it with no way forward and the patrol could never be finished. Found by running
a real patrol on a phone against a camera that would not stream.

The fix must not become a way to skip a camera that works, which is what the
last test here is for.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app  # noqa: F401
from app.services import virtual_patrol as vp

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


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


# ─── The pure rule ───────────────────────────────────────────────────────────

def test_a_patrol_that_reached_every_reachable_camera_is_partially_completed():
    # Not COMPLETED: that is an assertion the site was seen, and one camera
    # was not. The distinction is the whole reason this is a separate status.
    assert vp.derive_session_status(camera_count=3, completed=2, unavailable=1) \
        == vp.STATUS_PARTIALLY_COMPLETED


def test_a_patrol_with_every_camera_seen_is_completed():
    assert vp.derive_session_status(camera_count=3, completed=3, unavailable=0) \
        == vp.STATUS_COMPLETED


def test_a_camera_still_outstanding_leaves_the_patrol_in_progress():
    assert vp.derive_session_status(camera_count=3, completed=1, unavailable=1) \
        == vp.STATUS_IN_PROGRESS


def test_a_failed_snapshot_still_blocks_completing_that_camera():
    # Unchanged and deliberate: marking a camera done with no image would make
    # the patrol claim evidence it does not have. The officer's way past it is
    # to report the camera as not working, not to complete it.
    blockers = vp.camera_blockers(
        snapshot_path=None, snapshot_error="Error opening input files",
        required_unanswered=0)
    assert blockers and "must be retried" in blockers[0]


# ─── Against the database ────────────────────────────────────────────────────

async def _patrol_with_a_broken_camera():
    """A session whose only camera failed to capture — the state a phone reaches
    when the stream is down."""
    i = {k: uuid.uuid4() for k in ("tenant", "site", "camera", "officer", "session", "cam")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'VP Co',:s)",
               {"t": i["tenant"], "s": f"vp-{i['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
               {"i": i["site"], "t": i["tenant"]})
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Lobby')",
               {"i": i["camera"], "t": i["tenant"], "s": i["site"]})
    await _sql(
        "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
        "VALUES (:i,:t,5,:e,'x','Patrol Officer')",
        {"i": i["officer"], "t": i["tenant"], "e": f"o-{i['officer'].hex[:8]}@vp.test"})
    await _sql(
        "INSERT INTO virtual_patrol_sessions "
        "  (id, tenant_id, site_id, patrol_number, schedule_name, scheduled_for, "
        "   officer_user_id, status, camera_count, completed_camera_count) "
        "VALUES (:i,:t,:s,'VP-TEST','Test Patrol', now(), :o, 'IN_PROGRESS', 1, 0)",
        {"i": i["session"], "t": i["tenant"], "s": i["site"], "o": i["officer"]})
    await _sql(
        "INSERT INTO virtual_patrol_session_cameras "
        "  (id, tenant_id, session_id, camera_id, sequence_no, camera_name, status, snapshot_error) "
        "VALUES (:i,:t,:s,:c,1,'Lobby','SNAPSHOT_FAILED','Error opening input files')",
        {"i": i["cam"], "t": i["tenant"], "s": i["session"], "c": i["camera"]})
    return i


@pytest.mark.asyncio
async def test_a_broken_camera_leaves_the_patrol_unfinishable_until_it_is_reported():
    """The state an officer was stranded in: the camera cannot be completed, and
    while it sits there the patrol is neither done nor abandonable."""
    w = await _patrol_with_a_broken_camera()
    rows = await _sql("SELECT status FROM virtual_patrol_session_cameras WHERE id = :i",
                      {"i": w["cam"]})
    assert rows[0]["status"] == "SNAPSHOT_FAILED"
    # Neither completed nor unavailable, so the session cannot leave IN_PROGRESS.
    assert vp.derive_session_status(camera_count=1, completed=0, unavailable=0) \
        == vp.STATUS_IN_PROGRESS


@pytest.mark.asyncio
async def test_reporting_it_not_working_records_the_reason_and_frees_the_patrol():
    w = await _patrol_with_a_broken_camera()
    await _sql(
        "UPDATE virtual_patrol_session_cameras "
        "   SET status='CAMERA_UNAVAILABLE', completed_at=now(), "
        "       officer_notes='Camera not working: Error opening input files' "
        " WHERE id = :i", {"i": w["cam"]})

    row = (await _sql("SELECT status, officer_notes FROM virtual_patrol_session_cameras "
                      " WHERE id = :i", {"i": w["cam"]}))[0]
    assert row["status"] == "CAMERA_UNAVAILABLE"
    assert "not working" in row["officer_notes"]
    # And now the patrol can be finished — honestly, as partially completed.
    assert vp.derive_session_status(camera_count=1, completed=0, unavailable=1) \
        == vp.STATUS_PARTIALLY_COMPLETED
