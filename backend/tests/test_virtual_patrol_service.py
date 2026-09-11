"""Session creation freezes its configuration, and the rules that decide "done".

The test this file exists for is section 4: a supervisor edits the schedule
while a patrol is running, and the running patrol does not change. Everything
else here is the validation an officer's browser also does — re-tested on the
server, because a patrol is evidence and anybody with curl can skip a browser.

Sections:
  A — Answer validation (6 tests)
  B — What counts as an exception (3 tests)
  C — What blocks a camera from completing (4 tests)
  D — What "done" means for a session (4 tests)
  E — Freezing the configuration, against a real database (4 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, time, timezone

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


# ─── A. Answer validation ────────────────────────────────────────────────────

def test_a_required_question_cannot_be_left_blank():
    assert vp.validate_answer(question_type=vp.TEXT, is_required=True,
                              options=None, answer="   ")


def test_an_optional_question_may_be_left_blank():
    assert vp.validate_answer(question_type=vp.TEXT, is_required=False,
                              options=None, answer=None) is None


def test_a_yes_no_question_refuses_anything_else():
    assert vp.validate_answer(question_type=vp.YES_NO, is_required=True,
                              options=None, answer="MAYBE")
    assert vp.validate_answer(question_type=vp.YES_NO, is_required=True,
                              options=None, answer="no") is None


def test_a_number_question_refuses_words():
    assert vp.validate_answer(question_type=vp.NUMBER, is_required=True,
                              options=None, answer="several")
    assert vp.validate_answer(question_type=vp.NUMBER, is_required=True,
                              options=None, answer="3") is None


def test_a_single_choice_answer_must_be_one_of_the_options():
    opts = ["Clear", "Blocked"]
    assert vp.validate_answer(question_type=vp.SINGLE_CHOICE, is_required=True,
                              options=opts, answer="Ajar")
    assert vp.validate_answer(question_type=vp.SINGLE_CHOICE, is_required=True,
                              options=opts, answer="Clear") is None


def test_multi_choice_rejects_any_option_it_does_not_know():
    opts = ["Lock", "Sign", "Light"]
    assert vp.validate_answer(question_type=vp.MULTI_CHOICE, is_required=True,
                              options=opts, answer=["Lock", "Alarm"])
    assert vp.validate_answer(question_type=vp.MULTI_CHOICE, is_required=True,
                              options=opts, answer=["Lock", "Sign"]) is None


# ─── B. Exceptions ───────────────────────────────────────────────────────────

def test_no_on_a_yes_no_question_is_an_exception():
    assert vp.is_exception(question_type=vp.YES_NO, answer="NO") is True
    assert vp.is_exception(question_type=vp.YES_NO, answer="YES") is False


def test_fail_on_a_pass_fail_question_is_an_exception():
    assert vp.is_exception(question_type=vp.PASS_FAIL, answer="FAIL") is True


def test_free_text_and_numbers_never_raise_an_exception_by_themselves():
    """There is no universally wrong number or sentence. Treating one as an
    exception raises incidents nobody configured, and an alert people learn to
    dismiss is worse than no alert."""
    assert vp.is_exception(question_type=vp.TEXT, answer="NO") is False
    assert vp.is_exception(question_type=vp.NUMBER, answer="0") is False


# ─── C. What blocks a camera ─────────────────────────────────────────────────

def test_a_camera_with_no_snapshot_cannot_be_completed():
    assert vp.camera_blockers(snapshot_path=None, snapshot_error=None,
                              required_unanswered=0)


def test_a_failed_snapshot_must_be_retried_not_waved_through():
    """Section 40. A completed camera with no image is a patrol that proves
    nothing while claiming otherwise."""
    blockers = vp.camera_blockers(snapshot_path=None,
                                  snapshot_error="ffmpeg timed out",
                                  required_unanswered=0)
    assert blockers and "retried" in blockers[0]


def test_unanswered_required_questions_block_completion():
    blockers = vp.camera_blockers(snapshot_path="a/b.jpg", snapshot_error=None,
                                  required_unanswered=2)
    assert any("2 required" in b for b in blockers)


def test_a_snapshot_and_every_required_answer_clears_the_camera():
    assert vp.camera_blockers(snapshot_path="a/b.jpg", snapshot_error=None,
                              required_unanswered=0) == []


# ─── D. What "done" means ────────────────────────────────────────────────────

def test_every_camera_inspected_is_completed():
    assert vp.derive_session_status(camera_count=3, completed=3, unavailable=0) \
        == vp.STATUS_COMPLETED


def test_an_offline_camera_makes_the_patrol_partial_not_complete():
    """COMPLETED asserts the site was seen. A camera nobody could reach was not
    seen, and the officer is not at fault either — hence PARTIALLY_COMPLETED
    rather than COMPLETED or a failure."""
    assert vp.derive_session_status(camera_count=3, completed=2, unavailable=1) \
        == vp.STATUS_PARTIALLY_COMPLETED


def test_a_patrol_with_work_left_is_still_in_progress():
    assert vp.derive_session_status(camera_count=3, completed=1, unavailable=0) \
        == vp.STATUS_IN_PROGRESS


def test_a_schedule_with_no_cameras_is_not_stuck_forever():
    assert vp.derive_session_status(camera_count=0, completed=0, unavailable=0) \
        == vp.STATUS_COMPLETED


# ─── E. Freezing the configuration ───────────────────────────────────────────

async def _session_factory():
    engine = create_async_engine(ADMIN_DATABASE_URL)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _seed_schedule_with_one_camera(question_text="Is the gate locked?"):
    """A tenant, a site, a camera, a schedule, one camera on it, one question."""
    engine, factory = await _session_factory()
    ids = {k: uuid.uuid4() for k in ("tenant", "site", "camera", "schedule", "sc")}
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:t,'VP','"
                             "' || :slug)"), {"t": ids["tenant"], "slug": f"vps-{ids['tenant'].hex[:8]}"})
        await s.execute(text("INSERT INTO sites (id, tenant_id, name) VALUES (:s,:t,'Site')"),
                        {"s": ids["site"], "t": ids["tenant"]})
        await s.execute(text("INSERT INTO cameras (id, tenant_id, site_id, name, location) "
                             "VALUES (:c,:t,:s,'Main Gate','North wall')"),
                        {"c": ids["camera"], "t": ids["tenant"], "s": ids["site"]})
        await s.execute(text(
            "INSERT INTO virtual_patrol_schedules "
            "  (id, tenant_id, site_id, name, schedule_type, start_date, patrol_time) "
            "VALUES (:id,:t,:s,'Morning Patrol','DAILY',:d,:tm)"),
            {"id": ids["schedule"], "t": ids["tenant"], "s": ids["site"],
             "d": date(2026, 1, 1), "tm": time(7, 0)})
        await s.execute(text(
            "INSERT INTO virtual_patrol_schedule_cameras "
            "  (id, tenant_id, schedule_id, camera_id, sequence_no) "
            "VALUES (:id,:t,:sc,:c,1)"),
            {"id": ids["sc"], "t": ids["tenant"], "sc": ids["schedule"], "c": ids["camera"]})
        await s.execute(text(
            "INSERT INTO virtual_patrol_questions "
            "  (tenant_id, schedule_camera_id, question_text, question_type, sequence_no) "
            "VALUES (:t,:sc,:q,'YES_NO',1)"),
            {"t": ids["tenant"], "sc": ids["sc"], "q": question_text})
        await s.commit()
    await engine.dispose()
    return ids


@pytest.mark.asyncio
async def test_creating_a_session_copies_the_cameras_and_questions():
    ids = await _seed_schedule_with_one_camera()
    engine, factory = await _session_factory()
    try:
        async with factory() as s:
            result = await vp.create_session(
                s, schedule_id=str(ids["schedule"]),
                scheduled_for=datetime(2026, 4, 1, 7, 0, tzinfo=timezone.utc))
            await s.commit()
        assert result["camera_count"] == 1
        assert result["question_count"] == 1
        assert result["patrol_number"].startswith("VP-20260401-")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_editing_the_schedule_afterwards_does_not_change_the_session():
    """Section 4, and the reason the session tables carry their own copies.

    The supervisor renames the camera and rewrites the question AFTER the patrol
    exists. The session must still show what the officer was actually asked —
    otherwise a report quietly disagrees with itself between one viewing and the
    next, which is the one thing an evidence trail may never do.
    """
    ids = await _seed_schedule_with_one_camera("Is the gate locked?")
    engine, factory = await _session_factory()
    try:
        async with factory() as s:
            created = await vp.create_session(
                s, schedule_id=str(ids["schedule"]),
                scheduled_for=datetime(2026, 4, 2, 7, 0, tzinfo=timezone.utc))
            await s.commit()

        async with factory() as s:
            await s.execute(text("UPDATE cameras SET name = 'RENAMED LATER' WHERE id = :c"),
                            {"c": ids["camera"]})
            await s.execute(text("UPDATE virtual_patrol_questions "
                                 "   SET question_text = 'A COMPLETELY DIFFERENT QUESTION'"),
                            {})
            await s.commit()

        async with factory() as s:
            cam = (await s.execute(text(
                "SELECT camera_name FROM virtual_patrol_session_cameras "
                " WHERE session_id = CAST(:id AS uuid)"),
                {"id": created["session_id"]})).scalar()
            q = (await s.execute(text(
                "SELECT q.question_text FROM virtual_patrol_session_questions q "
                "  JOIN virtual_patrol_session_cameras c ON c.id = q.session_camera_id "
                " WHERE c.session_id = CAST(:id AS uuid)"),
                {"id": created["session_id"]})).scalar()

        assert cam == "Main Gate", "the session followed a later rename"
        assert q == "Is the gate locked?", "the session followed a later question edit"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_location_is_preserved_because_cameras_have_no_code():
    """`cameras` has no code column, so camera_code stays NULL rather than being
    filled with something that merely looks like one. Location carries the
    identifying detail instead."""
    ids = await _seed_schedule_with_one_camera()
    engine, factory = await _session_factory()
    try:
        async with factory() as s:
            created = await vp.create_session(
                s, schedule_id=str(ids["schedule"]),
                scheduled_for=datetime(2026, 4, 3, 7, 0, tzinfo=timezone.utc))
            await s.commit()
        async with factory() as s:
            row = (await s.execute(text(
                "SELECT camera_code, camera_metadata->>'location' AS loc "
                "  FROM virtual_patrol_session_cameras "
                " WHERE session_id = CAST(:id AS uuid)"),
                {"id": created["session_id"]})).mappings().first()
        assert row["camera_code"] is None
        assert row["loc"] == "North wall"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_same_execution_twice_is_refused_by_the_database():
    """What makes the scheduler idempotent: the second attempt raises rather
    than quietly creating somebody a second morning patrol."""
    ids = await _seed_schedule_with_one_camera()
    when = datetime(2026, 4, 4, 7, 0, tzinfo=timezone.utc)
    engine, factory = await _session_factory()
    try:
        async with factory() as s:
            await vp.create_session(s, schedule_id=str(ids["schedule"]), scheduled_for=when)
            await s.commit()
        with pytest.raises(Exception):
            async with factory() as s:
                await vp.create_session(s, schedule_id=str(ids["schedule"]), scheduled_for=when)
                await s.commit()
    finally:
        await engine.dispose()
