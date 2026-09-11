"""The constraints Virtual Patrolling depends on, tested at the database.

Phase 2 is schema only — no services or endpoints yet — so these tests exercise
the guarantees the later phases will assume. Each one is a rule that, if it ever
stopped holding, would fail quietly rather than loudly:

  * a duplicate session looks like an extra patrol nobody was asked to do
  * a leaked schedule looks like a normal row
  * a weekly patrol with no weekday looks enabled and never runs
  * a choice question with no options is discovered by the officer, standing in
    front of the camera, mid-patrol

Sections:
  A — Idempotency: one execution, one session (2 tests)
  B — Tenant isolation (2 tests)
  C — Configuration that cannot be half-made (3 tests)
  D — History survives its configuration (2 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app  # noqa: F401

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)
# svc_app is the role the application runs as, and the only one RLS applies to.
# postgres has BYPASSRLS, so an isolation test run as postgres proves nothing.
APP_DATABASE_URL = ADMIN_DATABASE_URL.replace(
    "postgres:change_me_dev_only", "svc_app:change_me_dev_only"
)


async def _sql(statement: str, params: dict | None = None, *, url: str | None = None,
               tenant: str | None = None):
    engine = create_async_engine(url or ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            if tenant is not None:
                # SET LOCAL — scoped to this transaction, which is why it is set
                # inside the same session that runs the statement below.
                await s.execute(
                    text("SELECT set_config('app.current_tenant', :t, true)"),
                    {"t": tenant},
                )
            result = await s.execute(text(statement), params or {})
            rows = result.all() if result.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


async def _tenant_site() -> tuple[uuid.UUID, uuid.UUID]:
    tid, sid = uuid.uuid4(), uuid.uuid4()
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t, 'VP Co', :slug)",
               {"t": tid, "slug": f"vp-{tid.hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:s, :t, 'Main Site')",
               {"s": sid, "t": tid})
    return tid, sid


async def _schedule(tenant_id, site_id, *, stype="DAILY",
                    weekdays: list[int] | None = None) -> uuid.UUID:
    # asyncpg maps a Python list onto smallint[]; a '{}' string is a str and it
    # says so rather than guessing.
    sched = uuid.uuid4()
    await _sql(
        "INSERT INTO virtual_patrol_schedules "
        "  (id, tenant_id, site_id, name, schedule_type, start_date, patrol_time, weekdays) "
        "VALUES (:id, :t, :s, 'Morning Patrol', :ty, :sd, :pt, :wd)",
        {"id": sched, "t": tenant_id, "s": site_id, "ty": stype,
         "sd": date(2026, 1, 1), "pt": time(7, 0), "wd": weekdays or []},
    )
    return sched


# ─── A. One execution, one session ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_same_execution_cannot_create_two_sessions():
    """Idempotency is a constraint, not an if statement.

    Two scheduler workers, a restart mid-run, or a retry after a timeout must
    not produce two sessions for one execution. An application-level check loses
    this race; the database does not.
    """
    tid, sid = await _tenant_site()
    sched = await _schedule(tid, sid)
    when = datetime(2026, 3, 1, 7, 0, tzinfo=timezone.utc)

    async def make():
        await _sql(
            "INSERT INTO virtual_patrol_sessions "
            "  (tenant_id, site_id, schedule_id, patrol_number, schedule_name, scheduled_for) "
            "VALUES (:t, :s, :sc, 'VP-1', 'Morning Patrol', :w)",
            {"t": tid, "s": sid, "sc": sched, "w": when},
        )

    await make()
    with pytest.raises(Exception) as exc:
        await make()
    assert "uq_vpsess_execution" in str(exc.value) or "duplicate" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_a_different_time_is_a_different_patrol():
    """The constraint must not be so eager that tomorrow's patrol is refused."""
    tid, sid = await _tenant_site()
    sched = await _schedule(tid, sid)
    for day in (1, 2):
        await _sql(
            "INSERT INTO virtual_patrol_sessions "
            "  (tenant_id, site_id, schedule_id, patrol_number, schedule_name, scheduled_for) "
            "VALUES (:t, :s, :sc, :n, 'Morning Patrol', :w)",
            {"t": tid, "s": sid, "sc": sched, "n": f"VP-{day}",
             "w": datetime(2026, 3, day, 7, 0, tzinfo=timezone.utc)},
        )
    rows = await _sql("SELECT count(*) FROM virtual_patrol_sessions WHERE schedule_id = :sc",
                      {"sc": sched})
    assert rows[0][0] == 2


# ─── B. Tenant isolation ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_tenant_cannot_see_another_tenants_schedules():
    """Run as svc_app, the role the application actually uses. As postgres this
    test would pass while proving nothing — that role has BYPASSRLS."""
    tid_a, sid_a = await _tenant_site()
    tid_b, sid_b = await _tenant_site()
    await _schedule(tid_a, sid_a)
    await _schedule(tid_b, sid_b)

    seen = await _sql("SELECT count(*) FROM virtual_patrol_schedules",
                      url=APP_DATABASE_URL, tenant=str(tid_a))
    mine = await _sql("SELECT count(*) FROM virtual_patrol_schedules WHERE tenant_id = :t",
                      {"t": tid_a}, url=APP_DATABASE_URL, tenant=str(tid_a))
    assert seen[0][0] == mine[0][0], "tenant A saw rows that are not tenant A's"
    assert seen[0][0] >= 1, "tenant A cannot see its own schedule either — RLS is too tight"


@pytest.mark.asyncio
async def test_a_tenant_cannot_write_a_row_into_another_tenant():
    """WITH CHECK, not just USING. Without it a tenant can INSERT rows it then
    cannot see — data it has planted in somebody else's account."""
    tid_a, sid_a = await _tenant_site()
    tid_b, sid_b = await _tenant_site()
    with pytest.raises(Exception):
        await _sql(
            "INSERT INTO virtual_patrol_schedules "
            "  (tenant_id, site_id, name, schedule_type, start_date, patrol_time) "
            "VALUES (:tb, :sb, 'Planted', 'DAILY', :sd, :pt)",
            {"tb": tid_b, "sb": sid_b, "sd": date(2026, 1, 1), "pt": time(7, 0)},
            url=APP_DATABASE_URL, tenant=str(tid_a),
        )


# ─── C. Configuration that cannot be half-made ───────────────────────────────

@pytest.mark.asyncio
async def test_a_weekly_patrol_must_name_at_least_one_day():
    """Otherwise it sits in the list looking enabled and never runs once."""
    tid, sid = await _tenant_site()
    with pytest.raises(Exception) as exc:
        await _schedule(tid, sid, stype="WEEKLY", weekdays=[])
    assert "ck_vps_weekdays" in str(exc.value)


@pytest.mark.asyncio
async def test_a_choice_question_must_have_options():
    """A choice question with nothing to choose is discovered by the officer,
    standing in front of the camera, halfway through a patrol."""
    tid, sid = await _tenant_site()
    sched = await _schedule(tid, sid)
    cam_row = uuid.uuid4()
    await _sql(
        "INSERT INTO virtual_patrol_schedule_cameras "
        "  (id, tenant_id, schedule_id, camera_id, sequence_no) "
        "SELECT :id, :t, :sc, c.id, 1 FROM cameras c LIMIT 1",
        {"id": cam_row, "t": tid, "sc": sched},
    )
    rows = await _sql("SELECT count(*) FROM virtual_patrol_schedule_cameras WHERE id = :id",
                      {"id": cam_row})
    if rows[0][0] == 0:
        pytest.skip("no camera rows available in the test database")

    with pytest.raises(Exception) as exc:
        await _sql(
            "INSERT INTO virtual_patrol_questions "
            "  (tenant_id, schedule_camera_id, question_text, question_type, sequence_no) "
            "VALUES (:t, :c, 'Pick one', 'SINGLE_CHOICE', 1)",
            {"t": tid, "c": cam_row},
        )
    assert "ck_vpq_options" in str(exc.value)


@pytest.mark.asyncio
async def test_a_camera_cannot_appear_twice_in_one_schedule():
    tid, sid = await _tenant_site()
    sched = await _schedule(tid, sid)
    inserted = await _sql(
        "INSERT INTO virtual_patrol_schedule_cameras "
        "  (tenant_id, schedule_id, camera_id, sequence_no) "
        "SELECT :t, :sc, c.id, 1 FROM cameras c LIMIT 1 RETURNING camera_id",
        {"t": tid, "sc": sched},
    )
    if not inserted:
        pytest.skip("no camera rows available in the test database")
    with pytest.raises(Exception) as exc:
        await _sql(
            "INSERT INTO virtual_patrol_schedule_cameras "
            "  (tenant_id, schedule_id, camera_id, sequence_no) "
            "VALUES (:t, :sc, :cam, 2)",
            {"t": tid, "sc": sched, "cam": inserted[0][0]},
        )
    assert "uq_vpsc_camera" in str(exc.value)


# ─── D. History survives its configuration ───────────────────────────────────

@pytest.mark.asyncio
async def test_deleting_a_schedule_does_not_delete_the_patrols_that_ran():
    """Section 41. A patrol that happened is a historical fact; deleting the
    configuration it ran under must not erase the record of it."""
    tid, sid = await _tenant_site()
    sched = await _schedule(tid, sid)
    sess = uuid.uuid4()
    await _sql(
        "INSERT INTO virtual_patrol_sessions "
        "  (id, tenant_id, site_id, schedule_id, patrol_number, schedule_name, "
        "   scheduled_for, status) "
        "VALUES (:id, :t, :s, :sc, 'VP-9', 'Morning Patrol', :w, 'COMPLETED')",
        {"id": sess, "t": tid, "s": sid, "sc": sched,
         "w": datetime(2026, 3, 5, 7, 0, tzinfo=timezone.utc)},
    )
    await _sql("DELETE FROM virtual_patrol_schedules WHERE id = :sc", {"sc": sched})

    rows = await _sql(
        "SELECT schedule_id, schedule_name, status FROM virtual_patrol_sessions "
        " WHERE id = :id", {"id": sess})
    assert rows, "the session was deleted along with its schedule"
    assert rows[0][0] is None, "schedule_id should be nulled, not cascade-deleted"
    assert rows[0][1] == "Morning Patrol", "the name it ran under must survive the delete"


@pytest.mark.asyncio
async def test_the_session_keeps_its_own_copy_of_the_camera_name():
    """The camera may be renamed or deleted. A report has to say what was
    inspected at the time, not what that camera is called today."""
    tid, sid = await _tenant_site()
    sched = await _schedule(tid, sid)
    sess = uuid.uuid4()
    await _sql(
        "INSERT INTO virtual_patrol_sessions "
        "  (id, tenant_id, site_id, schedule_id, patrol_number, schedule_name, scheduled_for) "
        "VALUES (:id, :t, :s, :sc, 'VP-10', 'Morning Patrol', :w)",
        {"id": sess, "t": tid, "s": sid, "sc": sched,
         "w": datetime(2026, 3, 6, 7, 0, tzinfo=timezone.utc)},
    )
    await _sql(
        "INSERT INTO virtual_patrol_session_cameras "
        "  (tenant_id, session_id, camera_id, sequence_no, camera_name, camera_code) "
        "VALUES (:t, :se, NULL, 1, 'Fire Exit', 'CAM-004')",
        {"t": tid, "se": sess},
    )
    rows = await _sql(
        "SELECT camera_name, camera_code, camera_id FROM virtual_patrol_session_cameras "
        " WHERE session_id = :se", {"se": sess})
    assert rows[0][0] == "Fire Exit"
    assert rows[0][1] == "CAM-004"
    assert rows[0][2] is None, "a deleted camera must not take the record with it"
