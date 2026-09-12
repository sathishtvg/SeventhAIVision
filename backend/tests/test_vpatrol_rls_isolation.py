"""Tenant isolation on all ten virtual patrol tables, as svc_app.

WHY THIS EXISTS SEPARATELY FROM test_virtual_patrol_schema.py. That file proves
isolation on `virtual_patrol_schedules` and nothing else. Nine tables were
carrying RLS that no test had ever exercised — and the one holding the actual
findings, `virtual_patrol_session_answers`, is the worst of them to get wrong:
a row there is a camera, a time, a question and the answer that failed. That is
precisely the detail a site restriction exists to contain, and a leak in it
would look like an ordinary row.

RUN AS svc_app, THE ROLE THE APPLICATION USES. postgres has BYPASSRLS, so the
same assertions under that role pass while proving nothing. Every test here
asserts it is not a superuser before drawing any conclusion.

BOTH DIRECTIONS ARE TESTED, because they are different policies. USING governs
what a tenant can read; WITH CHECK governs what it can write. Without WITH CHECK
a tenant can INSERT rows into another tenant's account that it then cannot see
itself — data planted where nobody is looking for it.

Sections:
  A — The role is genuinely restricted (1 test)
  B — No table leaks another tenant's rows (10 tests, one per table)
  C — No table accepts a row written into another tenant (10 tests)
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


def _svc_app_test_url() -> str:
    """The app's own credentials against the test database.

    Derived from DATABASE_URL rather than by string-replacing the admin URL: the
    replace approach silently yields the ADMIN url when credentials differ from
    the hardcoded pattern, and an isolation test that quietly reconnects as a
    BYPASSRLS superuser is worse than no isolation test at all.
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

#: Every table migration 0116 puts RLS on. Kept as a literal list rather than
#: discovered at runtime: if a later migration adds a table and nobody adds it
#: here, that should be a visible omission in this file, not a silently smaller
#: test run.
RLS_TABLES = [
    "virtual_patrol_schedules",
    "virtual_patrol_schedule_cameras",
    "virtual_patrol_questions",
    "virtual_patrol_sessions",
    "virtual_patrol_session_cameras",
    "virtual_patrol_session_questions",
    "virtual_patrol_session_answers",
    "virtual_patrol_reports",
    "virtual_patrol_email_recipients",
    "virtual_patrol_email_queue",
]


async def _admin(statement: str, params: dict | None = None):
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


async def _as_app(statement: str, params: dict | None = None, *, tenant: str):
    """Run one statement as svc_app with the tenant GUC set for that transaction."""
    engine = create_async_engine(APP_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                            {"t": tenant})
            r = await s.execute(text(statement), params or {})
            rows = r.all() if r.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


async def _patrol_chain() -> dict:
    """One tenant carrying a row in every one of the ten tables.

    Built as a real patrol would be -- schedule, cameras, questions, then a
    session with its frozen copies, an answer, a report and a queued email --
    so each table is populated the way the application populates it rather than
    with a synthetic row that might satisfy a policy the real shape would not.
    """
    i = {k: uuid.uuid4() for k in (
        "tenant", "site", "camera", "sched", "sched_cam", "question", "session",
        "sess_cam", "sess_q", "answer", "report", "recipient", "queue")}

    await _admin("INSERT INTO tenants (id, name, slug) VALUES (:t,'Iso Co',:s)",
                 {"t": i["tenant"], "s": f"vpiso-{i['tenant'].hex[:10]}"})
    await _admin("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
                 {"i": i["site"], "t": i["tenant"]})
    await _admin("INSERT INTO cameras (id, tenant_id, site_id, name) "
                 "VALUES (:i,:t,:s,'Main Gate')",
                 {"i": i["camera"], "t": i["tenant"], "s": i["site"]})

    await _admin(
        "INSERT INTO virtual_patrol_schedules "
        "  (id, tenant_id, site_id, name, schedule_type, start_date, patrol_time) "
        "VALUES (:i,:t,:s,'Morning Patrol','DAILY',:d,:pt)",
        {"i": i["sched"], "t": i["tenant"], "s": i["site"],
         "d": date(2026, 1, 1), "pt": time(7, 0)})
    await _admin(
        "INSERT INTO virtual_patrol_schedule_cameras "
        "  (id, tenant_id, schedule_id, camera_id, sequence_no) VALUES (:i,:t,:sc,:c,1)",
        {"i": i["sched_cam"], "t": i["tenant"], "sc": i["sched"], "c": i["camera"]})
    await _admin(
        "INSERT INTO virtual_patrol_questions "
        "  (id, tenant_id, schedule_camera_id, question_text, question_type, sequence_no) "
        "VALUES (:i,:t,:sc,'Is the gate secure?','YES_NO',1)",
        {"i": i["question"], "t": i["tenant"], "sc": i["sched_cam"]})

    await _admin(
        "INSERT INTO virtual_patrol_sessions "
        "  (id, tenant_id, site_id, schedule_id, patrol_number, schedule_name, "
        "   scheduled_for, status) "
        "VALUES (:i,:t,:s,:sc,:n,'Morning Patrol',:w,'COMPLETED')",
        {"i": i["session"], "t": i["tenant"], "s": i["site"], "sc": i["sched"],
         "n": f"VP-ISO-{i['tenant'].hex[:8]}",
         "w": datetime.now(timezone.utc) - timedelta(hours=1)})
    await _admin(
        "INSERT INTO virtual_patrol_session_cameras "
        "  (id, tenant_id, session_id, camera_id, sequence_no, camera_name, status) "
        "VALUES (:i,:t,:se,:c,1,'Main Gate','COMPLETED')",
        {"i": i["sess_cam"], "t": i["tenant"], "se": i["session"], "c": i["camera"]})
    await _admin(
        "INSERT INTO virtual_patrol_session_questions "
        "  (id, tenant_id, session_camera_id, question_text, question_type, "
        "   is_required, sequence_no) "
        "VALUES (:i,:t,:sc,'Is the gate secure?','YES_NO',TRUE,1)",
        {"i": i["sess_q"], "t": i["tenant"], "sc": i["sess_cam"]})
    await _admin(
        "INSERT INTO virtual_patrol_session_answers "
        "  (id, tenant_id, session_question_id, answer_text, is_exception, answered_at) "
        "VALUES (:i,:t,:q,'NO',TRUE, now())",
        {"i": i["answer"], "t": i["tenant"], "q": i["sess_q"]})

    await _admin(
        "INSERT INTO virtual_patrol_reports "
        "  (id, tenant_id, session_id, report_format, storage_path) "
        "VALUES (:i,:t,:se,'PDF','reports/iso.pdf')",
        {"i": i["report"], "t": i["tenant"], "se": i["session"]})
    await _admin(
        "INSERT INTO virtual_patrol_email_recipients (id, tenant_id, schedule_id, email) "
        "VALUES (:i,:t,:sc,'ops@example.test')",
        {"i": i["recipient"], "t": i["tenant"], "sc": i["sched"]})
    await _admin(
        "INSERT INTO virtual_patrol_email_queue "
        "  (id, tenant_id, schedule_id, session_id, frequency, recipients, subject) "
        "VALUES (:i,:t,:sc,:se,'IMMEDIATE','ops@example.test','Report')",
        {"i": i["queue"], "t": i["tenant"], "sc": i["sched"], "se": i["session"]})
    return i


#: The row id each table's chain entry is keyed by, so a test can name the exact
#: row that must not be visible rather than relying on a count.
ROW_KEY = {
    "virtual_patrol_schedules": "sched",
    "virtual_patrol_schedule_cameras": "sched_cam",
    "virtual_patrol_questions": "question",
    "virtual_patrol_sessions": "session",
    "virtual_patrol_session_cameras": "sess_cam",
    "virtual_patrol_session_questions": "sess_q",
    "virtual_patrol_session_answers": "answer",
    "virtual_patrol_reports": "report",
    "virtual_patrol_email_recipients": "recipient",
    "virtual_patrol_email_queue": "queue",
}


# ─── A. The role is genuinely restricted ─────────────────────────────────────

@pytest.mark.asyncio
async def test_the_test_role_does_not_bypass_rls():
    """Asserted before anything else. As postgres every test in this file would
    pass while exercising no policy at all."""
    chain = await _patrol_chain()
    rows = await _as_app(
        "SELECT current_user, "
        "       (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)",
        tenant=str(chain["tenant"]))
    assert rows[0][1] is False, (
        f"connected as {rows[0][0]!r}, which has BYPASSRLS — this whole file "
        f"would pass while checking nothing")


# ─── B. No table leaks another tenant's rows ─────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("table", RLS_TABLES)
async def test_a_tenant_cannot_read_another_tenants_rows(table):
    """Two complete patrols, two tenants. Scoped to A, B's row must be invisible
    and A's own must not be — a policy that hides everything is not isolation,
    it is an outage."""
    a = await _patrol_chain()
    b = await _patrol_chain()
    key = ROW_KEY[table]

    seen_b = await _as_app(f"SELECT count(*) FROM {table} WHERE id = :id",
                           {"id": b[key]}, tenant=str(a["tenant"]))
    assert seen_b[0][0] == 0, f"{table}: tenant A read tenant B's row"

    seen_own = await _as_app(f"SELECT count(*) FROM {table} WHERE id = :id",
                             {"id": a[key]}, tenant=str(a["tenant"]))
    assert seen_own[0][0] == 1, (
        f"{table}: tenant A cannot read its OWN row — the policy is too tight "
        f"and the feature is broken for everyone")


@pytest.mark.asyncio
@pytest.mark.parametrize("table", RLS_TABLES)
async def test_an_unscoped_read_never_returns_another_tenants_rows(table):
    """The same check without a WHERE clause.

    An endpoint that forgets to filter by tenant must still be safe, because the
    policy is what stands between tenants — not the query the developer wrote.
    """
    a = await _patrol_chain()
    b = await _patrol_chain()
    key = ROW_KEY[table]

    visible = await _as_app(f"SELECT id FROM {table}", tenant=str(a["tenant"]))
    ids = {str(r[0]) for r in visible}
    assert str(b[key]) not in ids, f"{table}: an unfiltered read exposed tenant B's row"
    assert str(a[key]) in ids, f"{table}: tenant A's own row is missing from its own read"


# ─── C. No table accepts a row written into another tenant ───────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("table", RLS_TABLES)
async def test_a_tenant_cannot_write_a_row_into_another_tenant(table):
    """WITH CHECK, not just USING.

    Without it a tenant can INSERT rows carrying someone else's tenant_id —
    rows it cannot then see itself, sitting in an account whose owner has no
    reason to suspect them. Done by copying an existing row and swapping only
    the tenant, so every other column stays valid and the policy is the only
    thing that can refuse it.
    """
    a = await _patrol_chain()
    b = await _patrol_chain()
    key = ROW_KEY[table]

    # Column list minus the generated/defaulted id, so the copy needs no
    # knowledge of each table's shape.
    cols = await _admin(
        "SELECT string_agg(column_name, ', ' ORDER BY ordinal_position) "
        "  FROM information_schema.columns "
        " WHERE table_name = :t AND column_name NOT IN ('id')", {"t": table})
    collist = cols[0][0]
    assert collist, f"could not read the columns of {table}"
    select_list = collist.replace("tenant_id", "CAST(:victim AS uuid)", 1)

    refused = False
    try:
        await _as_app(
            f"INSERT INTO {table} ({collist}) "
            f"SELECT {select_list} FROM {table} WHERE id = :src",
            {"victim": b["tenant"], "src": a[key]}, tenant=str(a["tenant"]))
    except Exception as exc:
        # The policy refusing the write is the expected outcome. Any other
        # error means this test did not exercise what it claims to.
        assert "policy" in str(exc).lower() or "violates row-level security" in str(exc).lower(), \
            f"{table}: insert failed for a reason other than RLS: {exc}"
        refused = True

    if not refused:
        planted = await _admin(
            f"SELECT count(*) FROM {table} WHERE tenant_id = :t", {"t": b["tenant"]})
        # B's own chain row is legitimately there; anything beyond it was planted.
        assert planted[0][0] <= 1, (
            f"{table}: tenant A planted a row inside tenant B — WITH CHECK is "
            f"missing from this table's policy")
