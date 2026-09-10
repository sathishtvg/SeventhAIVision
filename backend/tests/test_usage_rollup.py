"""Snapshots you can trust, and a score somebody would act on.

Two failure modes, both quiet.

A DOUBLE COUNT. In an operational table that is a bug somebody notices; in an
analytics table it is a lie that survives forever, because nobody recomputes
history. So the snapshot is keyed on (tenant, day) and upserted, and the test
below runs it three times and requires one row.

A SCORE NOBODY BELIEVES. A single number that cannot distinguish "nobody has
logged in for a month" from "they are three invoices behind" is a number people
learn to ignore, so the components are scored separately and tested separately.

Sections:
  A — The snapshot (4 tests)
  B — The monthly rollup (2 tests)
  C — Scoring (7 tests)
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
from app.services import usage_rollup

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

GUARD = 5


def _factory():
    engine = create_async_engine(ADMIN_DATABASE_URL)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _sql(statement: str, params: dict | None = None):
    engine, factory = _factory()
    async with factory() as s:
        result = await s.execute(text(statement), params or {})
        rows = result.all() if result.returns_rows else []
        await s.commit()
    await engine.dispose()
    return rows


async def _snapshot(when: date | None = None) -> int:
    engine, factory = _factory()
    async with factory() as s:
        count = await usage_rollup.snapshot_usage(s, when)
        await s.commit()
    await engine.dispose()
    return count


async def _seed_tenant(users: int = 3, cameras: int = 4, active_cameras: int | None = None):
    tenant_id = uuid.uuid4()
    await _sql("INSERT INTO tenants (id, name, slug, status) "
               "VALUES (:id, 'Rollup Co', :slug, 'active')",
               {"id": tenant_id, "slug": f"roll-{tenant_id.hex[:10]}"})
    for i in range(users):
        await _sql(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
            "                   last_login_at) "
            "VALUES (:id, :tid, :role, :email, 'hashed', now())",
            {"id": uuid.uuid4(), "tid": tenant_id, "role": GUARD,
             "email": f"r{i}-{tenant_id.hex[:8]}@test.local"})
    on = cameras if active_cameras is None else active_cameras
    for i in range(cameras):
        await _sql("INSERT INTO cameras (id, tenant_id, name, is_active) "
                   "VALUES (:id, :tid, :n, :active)",
                   {"id": uuid.uuid4(), "tid": tenant_id, "n": f"Cam {i}",
                    "active": i < on})
    return tenant_id


# ─── A. The snapshot ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_snapshot_records_what_the_customer_runs():
    tenant_id = await _seed_tenant(users=3, cameras=4)
    await _snapshot(date.today())
    rows = await _sql("SELECT users, cameras FROM platform_usage_daily "
                      " WHERE tenant_id = :tid AND day = CURRENT_DATE",
                      {"tid": tenant_id})
    assert rows[0][0] == 3
    assert rows[0][1] == 4


@pytest.mark.asyncio
async def test_running_it_three_times_produces_one_row():
    """A scheduler restart, a catch-up after an outage, somebody running it by
    hand. All three must correct rather than double-count."""
    tenant_id = await _seed_tenant(users=2, cameras=2)
    for _ in range(3):
        await _snapshot(date.today())
    rows = await _sql("SELECT count(*) FROM platform_usage_daily "
                      " WHERE tenant_id = :tid AND day = CURRENT_DATE",
                      {"tid": tenant_id})
    assert rows[0][0] == 1


@pytest.mark.asyncio
async def test_a_rerun_corrects_a_stale_figure():
    """Which is the reason it is an upsert rather than an insert-if-absent:
    re-running after fixing bad data should fix the snapshot too."""
    tenant_id = await _seed_tenant(users=1, cameras=1)
    await _snapshot(date.today())
    await _sql("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, 'Extra')",
               {"id": uuid.uuid4(), "tid": tenant_id})
    await _snapshot(date.today())
    rows = await _sql("SELECT cameras FROM platform_usage_daily "
                      " WHERE tenant_id = :tid AND day = CURRENT_DATE",
                      {"tid": tenant_id})
    assert rows[0][0] == 2


@pytest.mark.asyncio
async def test_the_platform_tenant_is_never_snapshotted():
    """It is not a customer, and counting it would put the vendor in its own
    growth chart."""
    tenant_id = uuid.uuid4()
    await _sql("INSERT INTO tenants (id, name, slug, is_platform) "
               "VALUES (:id, 'Seventh AI', :slug, TRUE)",
               {"id": tenant_id, "slug": f"plat-{tenant_id.hex[:10]}"})
    await _snapshot(date.today())
    rows = await _sql("SELECT count(*) FROM platform_usage_daily WHERE tenant_id = :tid",
                      {"tid": tenant_id})
    assert rows[0][0] == 0


# ─── B. The monthly rollup ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_counts_roll_up_as_the_latest_value_not_a_sum():
    """A customer with fifty cameras every day for thirty days has fifty
    cameras, not fifteen hundred. Summing a level is the classic rollup bug and
    it produces figures that look plausible until somebody checks one."""
    tenant_id = await _seed_tenant(users=1, cameras=1)
    today = date.today().replace(day=15)
    for offset, cameras in ((2, 10), (1, 20), (0, 30)):
        await _sql("""
            INSERT INTO platform_usage_daily (tenant_id, day, cameras, ai_events)
            VALUES (:tid, :day, :cameras, 5)
            ON CONFLICT (tenant_id, day) DO UPDATE
               SET cameras = EXCLUDED.cameras, ai_events = EXCLUDED.ai_events
        """, {"tid": tenant_id, "day": today - timedelta(days=offset),
              "cameras": cameras})

    engine, factory = _factory()
    async with factory() as s:
        await usage_rollup.roll_up_month(s, today)
        await s.commit()
    await engine.dispose()

    rows = await _sql("SELECT cameras, ai_events FROM platform_usage_monthly "
                      " WHERE tenant_id = :tid", {"tid": tenant_id})
    assert rows[0][0] == 30, "cameras is the latest value"
    assert rows[0][1] == 15, "events really did each happen, so they sum"


@pytest.mark.asyncio
async def test_the_monthly_rollup_is_also_idempotent():
    tenant_id = await _seed_tenant(users=1, cameras=1)
    today = date.today().replace(day=10)
    await _sql("INSERT INTO platform_usage_daily (tenant_id, day, cameras, ai_events) "
               "VALUES (:tid, :day, 7, 3) "
               " ON CONFLICT (tenant_id, day) DO UPDATE SET cameras = 7",
               {"tid": tenant_id, "day": today})

    engine, factory = _factory()
    async with factory() as s:
        await usage_rollup.roll_up_month(s, today)
        await usage_rollup.roll_up_month(s, today)
        await s.commit()
    await engine.dispose()

    rows = await _sql("SELECT count(*), max(ai_events) FROM platform_usage_monthly "
                      " WHERE tenant_id = :tid", {"tid": tenant_id})
    assert rows[0][0] == 1
    assert rows[0][1] == 3, "twice through must not double the events"


# ─── C. Scoring ──────────────────────────────────────────────────────────────

def test_the_weights_add_up_to_one_hundred():
    """A scoring change that quietly stops adding up produces numbers nobody
    can reason about — 'is 80 good' has no answer if the maximum drifted."""
    assert sum(usage_rollup.WEIGHTS.values()) == 100


def test_a_customer_signing_in_today_scores_full_marks_for_it():
    assert usage_rollup._login_score(0) == usage_rollup.WEIGHTS["login"]


def test_a_customer_nobody_has_signed_into_for_months_scores_nothing():
    """Whatever else the figures say. Logging in is the strongest single signal
    that somebody still wants the product."""
    assert usage_rollup._login_score(120) == 0


def test_never_having_logged_in_is_zero_not_ignored():
    """None has to mean the worst case. Treating "no data" as neutral would
    score a customer who has never signed in above one who signed in a month
    ago, which is backwards."""
    assert usage_rollup._login_score(None) == 0


def test_cameras_bought_but_switched_off_lower_the_score():
    """A customer with two hundred cameras of which nine are on is paying for
    something they are not getting, which is a cancellation waiting for a
    budget review."""
    healthy = usage_rollup._usage_score(cameras=10, cameras_active=10,
                                        users=5, users_active=5)
    idle = usage_rollup._usage_score(cameras=10, cameras_active=1,
                                     users=5, users_active=1)
    assert healthy > idle
    assert healthy == usage_rollup.WEIGHTS["usage"]


def test_an_expired_subscription_scores_nothing_for_billing():
    """However happily they are using the product."""
    assert usage_rollup._billing_score("expired", 0) == 0
    assert usage_rollup._billing_score("active", 0) == usage_rollup.WEIGHTS["billing"]


def test_each_overdue_invoice_costs_the_customer_billing_score():
    a = usage_rollup._billing_score("active", 0)
    b = usage_rollup._billing_score("active", 1)
    c = usage_rollup._billing_score("active", 4)
    assert a > b > c
    assert c == 0, "four overdue invoices is not a healthy account"


@pytest.mark.asyncio
async def test_scoring_a_real_customer_stores_the_parts():
    """The total tells somebody to worry; the parts tell them what about."""
    tenant_id = await _seed_tenant(users=2, cameras=4, active_cameras=4)
    await _snapshot(date.today())

    engine, factory = _factory()
    async with factory() as s:
        await usage_rollup.score_health(s)
        await s.commit()
    await engine.dispose()

    rows = await _sql(
        "SELECT score, login_score, usage_score, billing_score, adoption_score "
        "  FROM tenant_health WHERE tenant_id = :tid", {"tid": tenant_id})
    assert rows, "the customer must be scored"
    score, login, usage, billing, adoption = rows[0]
    assert 0 <= score <= 100
    assert score == min(100, login + usage + billing + adoption)
