"""Is this guard's basic wage at or above the Progressive Wage Model floor?

PWM sets a legally mandated minimum basic wage per security grade. An agency
paying below it is in breach and risks PLRD licence action, so this is the one
payroll rule that decides whether a wage is LAWFUL rather than merely accurate.

This module only ever judges a wage. It never alters one. Auto-correcting pay to
the floor would guarantee compliant-looking payslips while hiding the thing that
actually went wrong — nearly always a wrong grade or a stale rate — and an agency
would rather be told than quietly corrected.

THREE VERDICTS, AND THE THIRD IS THE IMPORTANT ONE. A guard with no grade set,
or a period with no statutory rate on file, is NOT_ASSESSED. It is never
COMPLIANT. Silence reading as approval is exactly how a compliance feature comes
to give false assurance, and a feature that reassures wrongly is worse than one
that says nothing.

The floor itself is resolved in SQL by pwm_effective_floor(grade, period_start)
— see migration 0115 — which returns the greater of the statutory floor and any
tenant floor, both as of the PAYROLL PERIOD rather than today. This module takes
that number as an argument so it stays pure and testable without a database.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# The conventions already used by services/payroll.py to move between pay bases
# (monthly_salary / 26 for a day, hourly_rate * 8 for a day). Reused rather than
# reinvented so a guard is not judged by one divisor and paid by another.
WORKING_DAYS_PER_MONTH = Decimal("26")
HOURS_PER_DAY = Decimal("8")

COMPLIANT = "compliant"
BELOW_FLOOR = "below_floor"
NOT_ASSESSED = "not_assessed"

_CENTS = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def monthly_equivalent(
    *,
    monthly_salary: Decimal | None,
    daily_rate: Decimal | None,
    hourly_rate: Decimal | None,
) -> Decimal | None:
    """The guard's basic wage expressed as a monthly figure, or None.

    Order matters and mirrors payroll's own precedence: an explicit monthly
    salary wins, then a daily rate, then hourly.
    """
    if monthly_salary is not None:
        return _money(Decimal(monthly_salary))
    if daily_rate is not None:
        return _money(Decimal(daily_rate) * WORKING_DAYS_PER_MONTH)
    if hourly_rate is not None:
        return _money(Decimal(hourly_rate) * HOURS_PER_DAY * WORKING_DAYS_PER_MONTH)
    return None


def hourly_equivalent(
    *,
    monthly_salary: Decimal | None,
    daily_rate: Decimal | None,
    hourly_rate: Decimal | None,
) -> Decimal | None:
    """The guard's basic wage expressed as an hourly figure, or None."""
    if hourly_rate is not None:
        return _money(Decimal(hourly_rate))
    if daily_rate is not None:
        return _money(Decimal(daily_rate) / HOURS_PER_DAY)
    if monthly_salary is not None:
        return _money(
            Decimal(monthly_salary) / WORKING_DAYS_PER_MONTH / HOURS_PER_DAY
        )
    return None


def assess(
    *,
    pwm_grade: str | None,
    employment_type: str | None,
    floor: Decimal | None,
    monthly_salary: Decimal | None = None,
    daily_rate: Decimal | None = None,
    hourly_rate: Decimal | None = None,
) -> dict:
    """Judge one guard's basic wage against one floor.

    `floor` is the monthly statutory/tenant floor from pwm_effective_floor, or
    None when no rate is on file for that grade and period.

    A part-timer is compared on an HOURLY basis. Comparing their monthly total
    against a full-time monthly floor would flag every lawfully-paid part-timer
    as underpaid — they earn less per month because they work fewer hours — and
    a warning that is usually wrong is a warning people learn to click past.

    NOTE: the part-time hourly derivation below is this codebase's own
    convention (floor / 26 / 8), not a figure quoted from MOM. It is a legal
    question rather than an arithmetic one and should be checked against
    published guidance before an agency relies on it.
    """
    if not pwm_grade:
        return _verdict(NOT_ASSESSED, "No PWM grade is set for this guard.")
    if floor is None:
        return _verdict(
            NOT_ASSESSED,
            "No statutory PWM rate is on file for this grade and period.",
        )

    floor = Decimal(floor)
    part_time = employment_type == "part_time"

    if part_time:
        actual = hourly_equivalent(
            monthly_salary=monthly_salary, daily_rate=daily_rate,
            hourly_rate=hourly_rate,
        )
        applied = _money(floor / WORKING_DAYS_PER_MONTH / HOURS_PER_DAY)
        basis = "hourly"
    else:
        # full_time and contract are both compared on a full-time monthly basis.
        actual = monthly_equivalent(
            monthly_salary=monthly_salary, daily_rate=daily_rate,
            hourly_rate=hourly_rate,
        )
        applied = _money(floor)
        basis = "monthly"

    if actual is None:
        return _verdict(NOT_ASSESSED, "No basic wage is set for this guard.")

    if actual < applied:
        return _verdict(
            BELOW_FLOOR,
            f"Basic wage {actual} is below the {basis} PWM floor {applied}.",
            floor_applied=applied, actual=actual, basis=basis,
        )
    return _verdict(
        COMPLIANT, f"Basic wage {actual} meets the {basis} PWM floor {applied}.",
        floor_applied=applied, actual=actual, basis=basis,
    )


def _verdict(
    verdict: str, reason: str, *,
    floor_applied: Decimal | None = None,
    actual: Decimal | None = None,
    basis: str | None = None,
) -> dict:
    return {
        "verdict": verdict,
        "reason": reason,
        "floor_applied": floor_applied,
        "actual": actual,
        "basis": basis,
    }
