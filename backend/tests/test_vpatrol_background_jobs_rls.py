"""The three background jobs, run as the role the scheduler actually uses.

THIS FILE EXISTS BECAUSE EVERY OTHER TEST IN THE SUITE CONNECTS AS postgres,
WHICH HAS BYPASSRLS. Under that role a cross-tenant sweep with no tenant set
reads every row happily, so the whole suite went green while all three jobs were
failing on the real scheduler with:

    invalid input syntax for type uuid: ""

The policies cast current_setting('app.current_tenant', true) to uuid. With
nothing set that is the empty string, and the cast does not return no rows — it
raises, and takes the entire job down. None of that is visible to a superuser,
so it has to be tested as svc_app or not at all.

These tests assert the roughest possible thing — that the job returns instead of
raising — because that is precisely what was broken. Behaviour is covered in
test_vpatrol_scheduler.py and test_vpatrol_email.py.

Sections:
  A — The role is genuinely restricted (1 test)
  B — Each job survives a cross-tenant run (3 tests)
  C — The commit inside the loop does not strip the tenant (1 test)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app  # noqa: F401
from app.services import vpatrol_email as mail
from app.services import vpatrol_scheduler as sched

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


def _svc_app_test_url() -> str:
    """The app's own credentials, pointed at the test database.

    Derived from DATABASE_URL rather than by string-replacing the admin URL: the
    replace approach silently yields the ADMIN url whenever the credentials
    differ from the hardcoded pattern, and a test that quietly reconnects as a
    BYPASSRLS superuser is worse than no test at all.
    """
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        return ADMIN_DATABASE_URL
    base, _, db = raw.rpartition("/")
    db = db.split("?")[0]
    if not db.endswith("_test"):
        db = f"{db}_test"
    return f"{base}/{db}"


APP_DATABASE_URL = _svc_app_test_url()


async def _admin_sql(statement: str, params: dict | None = None):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            r = await s.execute(text(statement), params or {})
            rows = r.all() if r.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


async def _as_app(fn):
    """Run one coroutine against a session opened as svc_app, no tenant set.

    No tenant is set on purpose: that is the state the scheduler begins every
    cycle in, and the state in which all three jobs were raising.
    """
    engine = create_async_engine(APP_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            return await fn(s)
    finally:
        await engine.dispose()


async def _two_tenants_each_with_a_due_patrol():
    """Two tenants, so the second iteration runs after the first has committed.

    One tenant cannot catch this: the GUC survives until the first commit, so a
    single-tenant run passes while a two-tenant run fails on the second.
    """
    made = []
    for _ in range(2):
        ids = {k: uuid.uuid4() for k in ("tenant", "site", "cam", "sched")}
        await _admin_sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'RLS Co',:s)",
                         {"t": ids["tenant"], "s": f"vprls-{ids['tenant'].hex[:10]}"})
        await _admin_sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
                         {"i": ids["site"], "t": ids["tenant"]})
        await _admin_sql("INSERT INTO cameras (id, tenant_id, site_id, name) "
                         "VALUES (:i,:t,:s,'Main Gate')",
                         {"i": ids["cam"], "t": ids["tenant"], "s": ids["site"]})
        # Ten minutes ago in the schedule's own zone, so it is reliably due
        # whenever this runs rather than only during one part of the day.
        due_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        await _admin_sql(
            "INSERT INTO virtual_patrol_schedules "
            "  (id, tenant_id, site_id, name, schedule_type, start_date, patrol_time, timezone) "
            "VALUES (:i,:t,:s,'Morning Patrol','DAILY',:d,:pt,'UTC')",
            {"i": ids["sched"], "t": ids["tenant"], "s": ids["site"],
             "d": date(2026, 1, 1),
             "pt": due_at.time().replace(second=0, microsecond=0)})
        await _admin_sql(
            "INSERT INTO virtual_patrol_schedule_cameras "
            "  (tenant_id, schedule_id, camera_id, sequence_no) VALUES (:t,:sc,:c,1)",
            {"t": ids["tenant"], "sc": ids["sched"], "c": ids["cam"]})
        made.append(ids)
    return made


# ─── A. The role is genuinely restricted ─────────────────────────────────────

@pytest.mark.asyncio
async def test_the_test_role_does_not_bypass_rls():
    """Asserted first, and loudly. If this connection turns out to be postgres,
    every other test in this file proves nothing and goes green anyway."""
    async def check(s):
        return (await s.execute(text(
            "SELECT current_user, "
            "       (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"
        ))).first()

    user, bypasses = await _as_app(check)
    assert bypasses is False, (
        f"connected as {user!r}, which has BYPASSRLS — this file would pass "
        f"while exercising nothing"
    )


# ─── B. Each job survives a cross-tenant run ─────────────────────────────────

@pytest.mark.asyncio
async def test_session_creation_runs_across_tenants_under_rls():
    made = await _two_tenants_each_with_a_due_patrol()
    counts = await _as_app(sched.create_due_sessions)
    assert counts["failed"] == 0, counts

    for ids in made:
        rows = await _admin_sql("SELECT count(*) FROM virtual_patrol_sessions "
                                " WHERE schedule_id = :s", {"s": ids["sched"]})
        assert rows[0][0] == 1, (
            f"tenant {ids['tenant']} got {rows[0][0]} sessions — the second "
            f"tenant in the loop is the one that fails when the GUC is lost")


@pytest.mark.asyncio
async def test_the_missed_sweep_runs_across_tenants_under_rls():
    made = await _two_tenants_each_with_a_due_patrol()
    for ids in made:
        await _admin_sql(
            "INSERT INTO virtual_patrol_sessions "
            "  (tenant_id, site_id, schedule_id, patrol_number, schedule_name, "
            "   scheduled_for, status) "
            "VALUES (:t,:s,:sc,:n,'Morning Patrol',:w,'SCHEDULED')",
            {"t": ids["tenant"], "s": ids["site"], "sc": ids["sched"],
             "n": f"VP-{ids['tenant'].hex[:8]}",
             "w": datetime.now(timezone.utc) - timedelta(hours=3)})

    await _as_app(sched.sweep_missed_sessions)

    for ids in made:
        rows = await _admin_sql(
            "SELECT status FROM virtual_patrol_sessions WHERE schedule_id = :s",
            {"s": ids["sched"]})
        assert all(r[0] == "MISSED" for r in rows), rows


@pytest.mark.asyncio
async def test_the_email_queue_runs_across_tenants_under_rls():
    made = await _two_tenants_each_with_a_due_patrol()
    for ids in made:
        sess = uuid.uuid4()
        await _admin_sql(
            "INSERT INTO virtual_patrol_sessions "
            "  (id, tenant_id, site_id, schedule_id, patrol_number, schedule_name, "
            "   scheduled_for, status) "
            "VALUES (:i,:t,:s,:sc,:n,'Morning Patrol',:w,'COMPLETED')",
            {"i": sess, "t": ids["tenant"], "s": ids["site"], "sc": ids["sched"],
             "n": f"VPM-{ids['tenant'].hex[:8]}",
             "w": datetime.now(timezone.utc) - timedelta(hours=1)})
        await _admin_sql(
            "INSERT INTO virtual_patrol_email_queue "
            "  (tenant_id, schedule_id, session_id, frequency, recipients, subject) "
            "VALUES (:t,:sc,:se,'IMMEDIATE','ops@example.com','Report')",
            {"t": ids["tenant"], "sc": ids["sched"], "se": sess})
        ids["session"] = sess

    sent_to = []

    async def fake_send(db, *, session_id, recipients, subject, **kw):
        sent_to.append(session_id)

    async def run(s):
        return await mail.process_queue(s, send=fake_send)

    result = await _as_app(run)
    assert result["failed"] == 0, result
    wanted = {str(ids["session"]) for ids in made}
    assert wanted <= set(sent_to), (
        f"only {len(wanted & set(sent_to))} of 2 tenants' reports were sent")


# ─── C. The commit inside the loop does not strip the tenant ─────────────────

@pytest.mark.asyncio
async def test_a_sent_report_is_marked_sent_and_not_retried():
    """The subtler half of the same bug, and the expensive one.

    _claim() commits, which ends the transaction the tenant GUC was set in. The
    UPDATE that marks the row SENT then runs with no tenant, raises, and lands
    in the failure branch — so a report that was delivered is recorded FAILED and
    delivered again on the next tick, five times over. As postgres this is
    invisible: the row goes SENT and the test passes.
    """
    made = await _two_tenants_each_with_a_due_patrol()
    ids = made[0]
    sess = uuid.uuid4()
    await _admin_sql(
        "INSERT INTO virtual_patrol_sessions "
        "  (id, tenant_id, site_id, schedule_id, patrol_number, schedule_name, "
        "   scheduled_for, status) "
        "VALUES (:i,:t,:s,:sc,:n,'Morning Patrol',:w,'COMPLETED')",
        {"i": sess, "t": ids["tenant"], "s": ids["site"], "sc": ids["sched"],
         "n": f"VPS-{ids['tenant'].hex[:8]}",
         "w": datetime.now(timezone.utc) - timedelta(hours=1)})
    await _admin_sql(
        "INSERT INTO virtual_patrol_email_queue "
        "  (tenant_id, schedule_id, session_id, frequency, recipients, subject) "
        "VALUES (:t,:sc,:se,'IMMEDIATE','ops@example.com','Report')",
        {"t": ids["tenant"], "sc": ids["sched"], "se": sess})

    calls = []

    async def fake_send(db, *, session_id, recipients, subject, **kw):
        if session_id == str(sess):
            calls.append(session_id)

    async def run(s):
        return await mail.process_queue(s, send=fake_send)

    await _as_app(run)
    rows = await _admin_sql(
        "SELECT status, attempts, last_error FROM virtual_patrol_email_queue "
        " WHERE session_id = :s", {"s": sess})
    assert rows[0][0] == "SENT", (
        f"a delivered report is recorded as {rows[0][0]} "
        f"(error: {rows[0][2]!r}) — it will be sent again")

    # And the next tick must not send it a second time.
    await _as_app(run)
    assert len(calls) == 1, f"the same report was sent {len(calls)} times"
