"""The Progressive Wage Model floor: what it catches, and what it must not claim.

PWM sets a legally mandated minimum basic wage per security grade, so this is
the payroll rule that decides whether a wage is LAWFUL rather than merely
accurate. That raises the bar for the tests: a compliance feature that reports
"compliant" when it has not actually checked anything is worse than no feature,
because somebody then stops looking.

Sections:
  A — Verdicts, including the one that must never be silent (5 tests)
  B — Pay bases: full-time, daily, hourly, part-time (4 tests)
  C — The floor resolves as of the PERIOD, not today (2 tests)
  D — A tenant may raise its own bar, never lower the law (3 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app  # noqa: F401
from app.services import pwm

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

GRADE = "security_officer"
SUPERVISOR = "security_supervisor"


async def _sql(statement: str, params: dict | None = None):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            result = await s.execute(text(statement), params or {})
            rows = result.all() if result.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


async def _statutory(grade: str, effective_from: date, amount: str):
    await _sql(
        "INSERT INTO pwm_wage_floors (grade, effective_from, monthly_basic_floor, source) "
        "VALUES (:g, :d, :a, 'test fixture') "
        " ON CONFLICT (grade, effective_from) DO UPDATE "
        "   SET monthly_basic_floor = EXCLUDED.monthly_basic_floor",
        {"g": grade, "d": effective_from, "a": amount},
    )


# ─── A. Verdicts ─────────────────────────────────────────────────────────────

def test_a_wage_below_the_floor_is_caught():
    r = pwm.assess(pwm_grade=GRADE, employment_type="full_time",
                   floor=Decimal("2000"), monthly_salary=Decimal("1900"))
    assert r["verdict"] == pwm.BELOW_FLOOR
    assert r["floor_applied"] == Decimal("2000.00")


def test_a_wage_at_the_floor_is_compliant():
    """At the floor, not merely above it. Paying exactly the minimum is lawful,
    and an off-by-one here would flag every agency that pays exactly PWM."""
    r = pwm.assess(pwm_grade=GRADE, employment_type="full_time",
                   floor=Decimal("2000"), monthly_salary=Decimal("2000"))
    assert r["verdict"] == pwm.COMPLIANT


def test_no_grade_is_not_assessed_rather_than_compliant():
    """The test this feature lives or dies by. A guard with no grade has not
    been checked, and reporting that as compliant is how a compliance feature
    ends up certifying something nobody ever looked at."""
    r = pwm.assess(pwm_grade=None, employment_type="full_time",
                   floor=Decimal("2000"), monthly_salary=Decimal("50"))
    assert r["verdict"] == pwm.NOT_ASSESSED
    assert r["verdict"] != pwm.COMPLIANT


def test_no_statutory_rate_on_file_is_not_assessed():
    """Rates are seeded from the published MOM schedule, not invented in code,
    so a period with nothing on file is a real state. It must not resolve to a
    floor of zero, which would make every wage on earth compliant."""
    r = pwm.assess(pwm_grade=GRADE, employment_type="full_time",
                   floor=None, monthly_salary=Decimal("1"))
    assert r["verdict"] == pwm.NOT_ASSESSED


def test_no_wage_set_is_not_assessed():
    r = pwm.assess(pwm_grade=GRADE, employment_type="full_time",
                   floor=Decimal("2000"))
    assert r["verdict"] == pwm.NOT_ASSESSED


# ─── B. Pay bases ────────────────────────────────────────────────────────────

def test_a_daily_rate_is_converted_before_comparison():
    """26 working days, the divisor payroll already uses — so a guard is not
    judged by one convention and paid by another."""
    below = pwm.assess(pwm_grade=GRADE, employment_type="full_time",
                       floor=Decimal("2600"), daily_rate=Decimal("99"))
    at = pwm.assess(pwm_grade=GRADE, employment_type="full_time",
                    floor=Decimal("2600"), daily_rate=Decimal("100"))
    assert below["verdict"] == pwm.BELOW_FLOOR
    assert at["verdict"] == pwm.COMPLIANT


def test_an_hourly_rate_is_converted_before_comparison():
    at = pwm.assess(pwm_grade=GRADE, employment_type="full_time",
                    floor=Decimal("2080"), hourly_rate=Decimal("10"))
    assert at["verdict"] == pwm.COMPLIANT  # 10 * 8 * 26 = 2080


def test_a_correctly_paid_part_timer_is_not_flagged():
    """The noise test. A part-timer earns less per month because they work
    fewer hours; comparing their monthly total against a full-time monthly
    floor would flag every lawfully-paid one, and a warning that is usually
    wrong is a warning people learn to click past."""
    r = pwm.assess(pwm_grade=GRADE, employment_type="part_time",
                   floor=Decimal("2080"), hourly_rate=Decimal("10"))
    assert r["verdict"] == pwm.COMPLIANT
    assert r["basis"] == "hourly"


def test_an_underpaid_part_timer_is_still_caught():
    r = pwm.assess(pwm_grade=GRADE, employment_type="part_time",
                   floor=Decimal("2080"), hourly_rate=Decimal("7"))
    assert r["verdict"] == pwm.BELOW_FLOOR


# ─── C. The floor resolves as of the period ──────────────────────────────────

@pytest.mark.asyncio
async def test_a_rate_rise_does_not_retroactively_fail_last_year():
    """The one most implementations get wrong.

    Restating March in September must judge March against March's floor. If the
    resolver reads "today", every lawful payslip written before a January
    increase turns non-compliant the moment that increase lands — and an agency
    would be chasing violations that never happened.
    """
    await _statutory(GRADE, date(2025, 1, 1), "2000")
    await _statutory(GRADE, date(2026, 1, 1), "2500")

    old = (await _sql(
        "SELECT pwm_effective_floor(:g, CAST(:d AS date))",
        {"g": GRADE, "d": date(2025, 6, 1)}))[0][0]
    new = (await _sql(
        "SELECT pwm_effective_floor(:g, CAST(:d AS date))",
        {"g": GRADE, "d": date(2026, 6, 1)}))[0][0]

    assert Decimal(old) == Decimal("2000.00"), "June 2025 must use the 2025 floor"
    assert Decimal(new) == Decimal("2500.00")


@pytest.mark.asyncio
async def test_a_period_before_any_rate_returns_null():
    """Not zero. A floor of zero makes every wage compliant."""
    await _statutory(SUPERVISOR, date(2026, 1, 1), "3000")
    got = (await _sql(
        "SELECT pwm_effective_floor(:g, CAST(:d AS date))",
        {"g": SUPERVISOR, "d": date(2020, 1, 1)}))[0][0]
    assert got is None


# ─── D. A tenant may raise its own bar, never lower the law ──────────────────

async def _tenant() -> uuid.UUID:
    tid = uuid.uuid4()
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:id, 'PWM Co', :slug)",
               {"id": tid, "slug": f"pwm-{tid.hex[:10]}"})
    return tid


@pytest.mark.asyncio
async def test_a_tenant_floor_below_statutory_is_refused_by_the_database():
    """Enforced by a trigger rather than only in the API, because a floor
    quietly set below statutory would make the system certify an illegal wage
    as compliant — worse than not checking at all."""
    await _statutory(GRADE, date(2026, 1, 1), "2500")
    tid = await _tenant()
    with pytest.raises(Exception) as exc:
        await _sql(
            "INSERT INTO tenant_pwm_floors "
            "  (tenant_id, grade, effective_from, monthly_basic_floor) "
            "VALUES (:t, :g, CAST(:d AS date), 2400)",
            {"t": tid, "g": GRADE, "d": date(2026, 1, 1)},
        )
    assert "statutory" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_a_tenant_may_set_a_higher_floor():
    """Many agencies pay above the minimum under a collective agreement and
    want that enforced rather than the legal bare minimum."""
    await _statutory(GRADE, date(2026, 1, 1), "2500")
    tid = await _tenant()
    await _sql(
        "INSERT INTO tenant_pwm_floors "
        "  (tenant_id, grade, effective_from, monthly_basic_floor) "
        "VALUES (:t, :g, CAST(:d AS date), 2800)",
        {"t": tid, "g": GRADE, "d": date(2026, 1, 1)},
    )
    rows = await _sql(
        "SELECT monthly_basic_floor FROM tenant_pwm_floors "
        " WHERE tenant_id = :t AND grade = :g", {"t": tid, "g": GRADE})
    assert Decimal(rows[0][0]) == Decimal("2800.00")


@pytest.mark.asyncio
async def test_the_statutory_floor_needs_no_tenant_scope_to_read():
    """It is national law with no tenant_id and no RLS, which is why a new
    tenant is protected on day one with nothing to seed. Reading it without any
    tenant GUC set must work — if this ever starts returning nothing, somebody
    has made it tenant-scoped and every new customer is silently unprotected."""
    await _statutory(GRADE, date(2026, 1, 1), "2500")
    rows = await _sql(
        "SELECT monthly_basic_floor FROM pwm_wage_floors "
        " WHERE grade = :g AND effective_from = CAST(:d AS date)",
        {"g": GRADE, "d": date(2026, 1, 1)})
    assert rows and Decimal(rows[0][0]) == Decimal("2500.00")
