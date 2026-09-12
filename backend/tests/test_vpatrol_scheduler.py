"""When a patrol is due, and why it must be created exactly once.

The timezone tests are the point of this file. A patrol set for 07:00 means
07:00 where the site is; a scheduler that reads the server clock works perfectly
until the stack moves, and then every patrol in the system shifts silently.

Sections:
  A — Which days a schedule runs (5 tests)
  B — Time, in the schedule's own zone (5 tests)
  C — Creating sessions, exactly once (3 tests)
  D — A missed patrol is recorded (2 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app  # noqa: F401
from app.services import vpatrol_scheduler as sched

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

SGT = ZoneInfo("Asia/Singapore")
START = date(2026, 1, 1)


# ─── A. Which days a schedule runs ───────────────────────────────────────────

def test_a_daily_schedule_runs_every_day():
    assert sched.occurs_on(schedule_type="DAILY", start_date=START, end_date=None,
                           weekdays=None, day=date(2026, 6, 17))


def test_nothing_runs_before_its_start_date():
    assert not sched.occurs_on(schedule_type="DAILY", start_date=date(2026, 6, 1),
                               end_date=None, weekdays=None, day=date(2026, 5, 31))


def test_nothing_runs_after_its_end_date():
    """A schedule with an end date that keeps firing is a patrol nobody expects
    and nobody is rostered for."""
    assert not sched.occurs_on(schedule_type="DAILY", start_date=START,
                               end_date=date(2026, 6, 30), weekdays=None,
                               day=date(2026, 7, 1))


def test_a_once_schedule_runs_on_exactly_one_day():
    for day, expected in ((START, True), (date(2026, 1, 2), False)):
        assert sched.occurs_on(schedule_type="ONCE", start_date=START, end_date=None,
                               weekdays=None, day=day) is expected


def test_a_weekly_schedule_runs_only_on_its_days():
    # 2026-06-17 is a Wednesday (isoweekday 3).
    wednesday = date(2026, 6, 17)
    assert sched.occurs_on(schedule_type="WEEKLY", start_date=START, end_date=None,
                           weekdays=[3], day=wednesday)
    assert not sched.occurs_on(schedule_type="WEEKLY", start_date=START, end_date=None,
                               weekdays=[1, 2], day=wednesday)


# ─── B. Time, in the schedule's own zone ─────────────────────────────────────

def _due(now_local, tz="Asia/Singapore", patrol_time=time(7, 0), **over):
    kwargs = dict(schedule_type="DAILY", start_date=START, end_date=None,
                  weekdays=None, patrol_time=patrol_time, tz_name=tz,
                  now_utc=now_local.astimezone(timezone.utc))
    kwargs.update(over)
    return sched.latest_due(**kwargs)


def test_a_patrol_is_not_due_before_its_time():
    assert _due(datetime(2026, 6, 17, 6, 59, tzinfo=SGT)) is None


def test_a_patrol_is_due_once_its_time_has_passed():
    got = _due(datetime(2026, 6, 17, 7, 5, tzinfo=SGT))
    assert got == datetime(2026, 6, 17, 7, 0, tzinfo=SGT).astimezone(timezone.utc)


def test_the_time_means_the_schedules_zone_not_the_servers():
    """07:00 Singapore is 23:00 UTC the previous day. A scheduler reading the
    server clock would fire this patrol seven hours late — or seven hours early,
    depending which way the host is offset."""
    due = _due(datetime(2026, 6, 17, 7, 5, tzinfo=SGT))
    assert due.hour == 23 and due.day == 16, due


def test_an_evening_patrol_is_still_found_after_midnight():
    """At 00:30 local the occurrence that matters is last night's. Looking only
    at today would skip every late-evening patrol, every night."""
    due = _due(datetime(2026, 6, 18, 0, 30, tzinfo=SGT), patrol_time=time(22, 0))
    assert due == datetime(2026, 6, 17, 22, 0, tzinfo=SGT).astimezone(timezone.utc)


def test_an_occurrence_older_than_the_lookback_is_not_resurrected():
    """Enabling a schedule that started in January must not manufacture two
    hundred patrols nobody could have carried out."""
    assert _due(datetime(2026, 6, 17, 23, 59, tzinfo=SGT)) is None


# ─── C. Creating sessions, exactly once ──────────────────────────────────────

async def _sql(stmt: str, params: dict | None = None):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            r = await s.execute(text(stmt), params or {})
            rows = r.all() if r.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


def _minutes_ago_in_sgt(minutes: int) -> time:
    """A patrol time that is reliably just-past in the schedule's zone.

    A fixed hour cannot work: the lookback deliberately refuses occurrences more
    than six hours old, so a hardcoded 00:01 is "due" at 06:00 and silently not
    due by lunchtime — a test that passes or fails depending on when it is run.
    """
    return (datetime.now(SGT) - timedelta(minutes=minutes)).time().replace(
        second=0, microsecond=0)


async def _seed_due_schedule(patrol_time=time(7, 0)):
    """A tenant, site, camera and an enabled daily schedule."""
    ids = {k: uuid.uuid4() for k in ("tenant", "site", "cam", "sched", "sc")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'Sched Co',:s)",
               {"t": ids["tenant"], "s": f"vpsch-{ids['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
               {"i": ids["site"], "t": ids["tenant"]})
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) "
               "VALUES (:i,:t,:s,'Main Gate')",
               {"i": ids["cam"], "t": ids["tenant"], "s": ids["site"]})
    await _sql(
        "INSERT INTO virtual_patrol_schedules "
        "  (id, tenant_id, site_id, name, schedule_type, start_date, patrol_time, timezone) "
        "VALUES (:i,:t,:s,'Morning Patrol','DAILY',:d,:pt,'Asia/Singapore')",
        {"i": ids["sched"], "t": ids["tenant"], "s": ids["site"],
         "d": date(2026, 1, 1), "pt": patrol_time})
    await _sql(
        "INSERT INTO virtual_patrol_schedule_cameras "
        "  (id, tenant_id, schedule_id, camera_id, sequence_no) VALUES (:i,:t,:sc,:c,1)",
        {"i": ids["sc"], "t": ids["tenant"], "sc": ids["sched"], "c": ids["cam"]})
    return ids


async def _run_creation():
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            return await sched.create_due_sessions(s)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_due_schedule_produces_a_session():
    ids = await _seed_due_schedule(patrol_time=_minutes_ago_in_sgt(10))  # due every day
    await _run_creation()
    rows = await _sql("SELECT count(*) FROM virtual_patrol_sessions "
                      " WHERE schedule_id = :s", {"s": ids["sched"]})
    assert rows[0][0] >= 1, "the scheduler created nothing for a due schedule"


@pytest.mark.asyncio
async def test_running_the_scheduler_twice_does_not_double_up():
    """The heart of it. Two workers, a restart, or a retry must not give an
    officer two identical morning patrols."""
    ids = await _seed_due_schedule(patrol_time=_minutes_ago_in_sgt(10))
    await _run_creation()
    await _run_creation()
    rows = await _sql("SELECT count(*) FROM virtual_patrol_sessions "
                      " WHERE schedule_id = :s", {"s": ids["sched"]})
    assert rows[0][0] == 1, f"expected exactly one session, found {rows[0][0]}"


@pytest.mark.asyncio
async def test_a_disabled_schedule_produces_nothing():
    ids = await _seed_due_schedule(patrol_time=_minutes_ago_in_sgt(10))
    await _sql("UPDATE virtual_patrol_schedules SET enabled = FALSE WHERE id = :s",
               {"s": ids["sched"]})
    await _run_creation()
    rows = await _sql("SELECT count(*) FROM virtual_patrol_sessions "
                      " WHERE schedule_id = :s", {"s": ids["sched"]})
    assert rows[0][0] == 0


# ─── D. A missed patrol is recorded ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unstarted_patrol_past_its_grace_is_marked_missed():
    """Silence would let a site go uninspected with nothing to show for it."""
    ids = await _seed_due_schedule()
    sess = uuid.uuid4()
    await _sql(
        "INSERT INTO virtual_patrol_sessions "
        "  (id, tenant_id, site_id, schedule_id, patrol_number, schedule_name, "
        "   scheduled_for, status) "
        "VALUES (:i,:t,:s,:sc,'VP-M','Morning Patrol',:w,'SCHEDULED')",
        {"i": sess, "t": ids["tenant"], "s": ids["site"], "sc": ids["sched"],
         "w": datetime.now(timezone.utc) - timedelta(hours=3)})

    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await sched.sweep_missed_sessions(s)
    finally:
        await engine.dispose()

    rows = await _sql("SELECT status FROM virtual_patrol_sessions WHERE id = :i",
                      {"i": sess})
    assert rows[0][0] == "MISSED"


@pytest.mark.asyncio
async def test_a_patrol_already_under_way_is_never_marked_missed():
    """STARTED and abandoned is a different fact from never begun, and
    flattening the two would hide the officer who started and stopped."""
    ids = await _seed_due_schedule()
    sess = uuid.uuid4()
    await _sql(
        "INSERT INTO virtual_patrol_sessions "
        "  (id, tenant_id, site_id, schedule_id, patrol_number, schedule_name, "
        "   scheduled_for, status) "
        "VALUES (:i,:t,:s,:sc,'VP-P','Morning Patrol',:w,'IN_PROGRESS')",
        {"i": sess, "t": ids["tenant"], "s": ids["site"], "sc": ids["sched"],
         "w": datetime.now(timezone.utc) - timedelta(hours=3)})

    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await sched.sweep_missed_sessions(s)
    finally:
        await engine.dispose()

    rows = await _sql("SELECT status FROM virtual_patrol_sessions WHERE id = :i",
                      {"i": sess})
    assert rows[0][0] == "IN_PROGRESS"
