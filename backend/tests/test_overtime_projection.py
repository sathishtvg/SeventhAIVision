"""Seeing the overtime cap coming, instead of reading about it afterwards.

payroll.MAX_OT_HOURS_PER_MONTH has been in this codebase all along, and it is
only ever evaluated when a month is totalled — by which time the guard has
worked the hours and the agency is in breach. These tests cover looking forward
from the roster instead.

The arithmetic tests are pure functions, because the failure they guard against
is silent: a projection that is quietly wrong still produces a confident number,
and nobody re-derives it by hand.

Sections:
  A — Overtime in one shift (4 tests)
  B — Where the month stands against the cap (4 tests)
  C — Accrued plus projected, together (3 tests)
  D — Against the database (3 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app  # noqa: F401
from app.services import overtime_projection as ot
from app.services.payroll import MAX_OT_HOURS_PER_MONTH

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

D = Decimal


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


# ─── A. Overtime in one shift ────────────────────────────────────────────────

def test_a_normal_day_contributes_no_overtime():
    assert ot.overtime_in(D("8")) == D("0")


def test_a_twelve_hour_shift_contributes_four():
    assert ot.overtime_in(D("12")) == D("4")


def test_a_short_shift_does_not_offset_a_long_one():
    """The sharp one. If a four-hour shift counted as minus four, a guard
    working six twelves and one four would look compliant while working
    seventy-six hours of overtime. A cap is not a quota to be averaged."""
    assert ot.overtime_in(D("4")) == D("0")
    total = sum((ot.overtime_in(h) for h in [D("12")] * 6 + [D("4")]), D("0"))
    assert total == D("24")


def test_the_normal_day_is_the_one_the_codebase_already_uses():
    """8 comes from pwm.HOURS_PER_DAY, not from a number invented here. Two
    different statutory day-lengths in one system would both look official."""
    from app.services.pwm import HOURS_PER_DAY
    assert ot.overtime_in(HOURS_PER_DAY) == D("0")
    assert ot.overtime_in(HOURS_PER_DAY + D("1")) == D("1")


# ─── B. Where the month stands ───────────────────────────────────────────────

def test_well_under_the_cap_is_within():
    assert ot.classify(D("20")) == ot.WITHIN


def test_over_the_cap_is_over_the_cap():
    assert ot.classify(MAX_OT_HOURS_PER_MONTH + D("1")) == ot.OVER_CAP


def test_exactly_at_the_cap_is_not_yet_a_breach():
    """72 hours is the limit, not one hour past it. Flagging a guard who is
    exactly at the lawful maximum as in breach would be wrong, and would teach
    people to ignore the warning."""
    assert ot.classify(MAX_OT_HOURS_PER_MONTH) != ot.OVER_CAP


def test_one_shift_short_of_the_cap_is_a_warning():
    """The whole point of warning early: there is still a shift to move."""
    assert ot.classify(MAX_OT_HOURS_PER_MONTH - D("4")) == ot.APPROACHING


# ─── C. Accrued plus projected ───────────────────────────────────────────────

def test_the_roster_alone_can_breach_the_cap():
    projected, total, status = ot.project(
        accrued_ot_hours=D("0"), scheduled_shift_hours=[D("20")] * 4)
    assert projected == D("48") and total == D("48")
    assert status == ot.WITHIN


def test_accrued_hours_count_towards_the_same_cap():
    """A guard who has already worked 60 hours of overtime needs only a little
    more roster to breach. Projecting the roster alone would call this fine."""
    projected, total, status = ot.project(
        accrued_ot_hours=D("60"), scheduled_shift_hours=[D("14")] * 4)
    assert projected == D("24")
    assert total == D("84")
    assert status == ot.OVER_CAP


def test_a_guard_with_nothing_scheduled_is_judged_on_what_they_worked():
    projected, total, status = ot.project(
        accrued_ot_hours=D("80"), scheduled_shift_hours=[])
    assert projected == D("0") and total == D("80")
    assert status == ot.OVER_CAP


# ─── D. Against the database ─────────────────────────────────────────────────

async def _world(*, completed_ot_minutes: int, scheduled_hours: list[int]):
    """A tenant with one guard, some worked overtime and some roster ahead."""
    i = {k: uuid.uuid4() for k in ("tenant", "site", "guard")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'OT Co',:s)",
               {"t": i["tenant"], "s": f"ot-{i['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
               {"i": i["site"], "t": i["tenant"]})
    await _sql(
        "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
        "VALUES (:i,:t,5,:e,'x','Busy Guard')",
        {"i": i["guard"], "t": i["tenant"], "e": f"g-{i['guard'].hex[:8]}@ot.test"})

    today = date.today()
    first = today.replace(day=1)

    if completed_ot_minutes:
        start = datetime.combine(first, time(8, 0), tzinfo=timezone.utc)
        await _sql(
            "INSERT INTO shifts (tenant_id, guard_user_id, site_id, scheduled_start, "
            "                    scheduled_end, actual_start, actual_end, status, "
            "                    overtime_minutes) "
            "VALUES (:t,:g,:s,:a,:b,:a,:b,'completed',:m)",
            {"t": i["tenant"], "g": i["guard"], "s": i["site"],
             "a": start, "b": start + timedelta(hours=12), "m": completed_ot_minutes})

    for n, hours in enumerate(scheduled_hours):
        # Spread across later days so each is its own shift.
        start = datetime.combine(first + timedelta(days=10 + n), time(8, 0),
                                 tzinfo=timezone.utc)
        await _sql(
            "INSERT INTO shifts (tenant_id, guard_user_id, site_id, scheduled_start, "
            "                    scheduled_end, status) "
            "VALUES (:t,:g,:s,:a,:b,'scheduled')",
            {"t": i["tenant"], "g": i["guard"], "s": i["site"],
             "a": start, "b": start + timedelta(hours=hours)})
    return i


async def _project(tenant_id):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    today = date.today()
    first = today.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    try:
        async with factory() as s:
            await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                            {"t": str(tenant_id)})
            return await ot.project_month(s, month_start=first, month_end=last)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_guard_with_only_future_shifts_is_still_projected():
    """The two halves are read separately and merged. An inner join would drop
    a guard who has worked nothing yet this month — which is every guard on the
    first of the month."""
    w = await _world(completed_ot_minutes=0, scheduled_hours=[12, 12, 12])
    rows = [p for p in await _project(w["tenant"]) if p.guard_user_id == str(w["guard"])]
    assert rows, "a guard with only scheduled shifts vanished from the projection"
    assert rows[0].projected_ot_hours == D("12")
    assert rows[0].accrued_ot_hours == D("0")


@pytest.mark.asyncio
async def test_a_guard_with_only_worked_shifts_is_still_projected():
    w = await _world(completed_ot_minutes=300, scheduled_hours=[])
    rows = [p for p in await _project(w["tenant"]) if p.guard_user_id == str(w["guard"])]
    assert rows, "a guard with only completed shifts vanished from the projection"
    assert rows[0].accrued_ot_hours == D("5")
    assert rows[0].scheduled_shifts_remaining == 0


@pytest.mark.asyncio
async def test_accrued_and_scheduled_together_cross_the_cap():
    """What the feature is for: neither half breaches on its own, and together
    they do. This is the case payroll cannot see until the month is closed."""
    w = await _world(completed_ot_minutes=60 * 60,          # 60h already worked
                     scheduled_hours=[16, 16, 16])           # 8h OT each = 24h
    rows = [p for p in await _project(w["tenant"]) if p.guard_user_id == str(w["guard"])]
    p = rows[0]
    assert p.accrued_ot_hours == D("60")
    assert p.projected_ot_hours == D("24")
    assert p.total_ot_hours == D("84")
    assert p.status == ot.OVER_CAP
    assert p.as_dict()["over_by_hours"] == 12.0
