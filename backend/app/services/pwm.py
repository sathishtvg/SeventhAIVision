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


# ── MOM's whole schedule (migration 0175, 3 November 2026) ─────────────────────
#
# Outsourced full-time officers are judged on monthly basic wage (pwm_wage_floors, with a company's own higher floor),
# outsourced part-timers on MOM's own hourly basic wage, in-house officers on GROSS wage (basic + allowances +
# incentives, without overtime). In-house part-time is the full-time gross pro-rated on a 44-hour week, as MOM says.
# The old hourly derivation for part-timers above (floor / 26 / 8) was this codebase's own; MOM publishes the hourly
# figures, and they are what `assess_schedule` uses.

IN_HOUSE_WEEKLY_HOURS = Decimal("44")
WEEKS_PER_YEAR = Decimal("52")

BASIS_LABEL = {
    "monthly_basic": "monthly basic wage (outsourced, full-time)",
    "hourly_basic": "hourly basic wage (outsourced, part-time)",
    "monthly_gross": "monthly gross wage without overtime (in-house, full-time)",
    "hourly_gross": "hourly gross wage without overtime (in-house, part-time, pro-rated on a 44-hour week)",
}


def floor_kind(arrangement: str | None, employment_type: str | None) -> tuple[str, str]:
    """(hours, basis) for a guard: which of MOM's schedules applies. Contract staff are judged as full-time, as
    before."""
    part_time = employment_type == "part_time"
    if (arrangement or "outsourced") == "in_house":
        return ("part_time", "hourly_gross") if part_time else ("full_time", "monthly_gross")
    return ("part_time", "hourly_basic") if part_time else ("full_time", "monthly_basic")


def in_house_hourly(monthly_gross_floor: Decimal) -> Decimal:
    """The in-house full-time monthly gross floor pro-rated on a 44-hour week: monthly x 12 / (44 x 52)."""
    return _money(Decimal(monthly_gross_floor) * 12 / (IN_HOUSE_WEEKLY_HOURS * WEEKS_PER_YEAR))


async def resolve_floor(db, *, grade: str | None, arrangement: str | None, employment_type: str | None,
                        on) -> dict:
    """The floor for one guard on one date: {"amount", "basis", "source"}; amount None when MOM sets none for that
    grade, arrangement and hours (Chief Security Officer, the older site-supervisor grades, an in-house Senior
    Security Supervisor), which callers must show as "not assessed"."""
    from sqlalchemy import text  # local: the module stays importable by the pure tests without a database

    hours, basis = floor_kind(arrangement, employment_type)
    if not grade:
        return {"amount": None, "basis": basis, "source": None}
    if basis == "monthly_basic":
        amount = (await db.execute(text("SELECT pwm_effective_floor(CAST(:g AS varchar), CAST(:d AS date))"),
                                   {"g": grade, "d": on})).scalar()
        source = (await db.execute(text(
            "SELECT source FROM pwm_wage_floors WHERE grade = :g AND effective_from <= CAST(:d AS date) "
            "ORDER BY effective_from DESC LIMIT 1"), {"g": grade, "d": on})).scalar()
        return {"amount": Decimal(amount) if amount is not None else None, "basis": basis, "source": source}
    schedule_hours = "full_time" if basis == "hourly_gross" else hours
    row = (await db.execute(text(
        "SELECT amount, source FROM pwm_wage_schedules WHERE grade = :g AND arrangement = :a AND hours = :h "
        "AND effective_from <= CAST(:d AS date) ORDER BY effective_from DESC LIMIT 1"),
        {"g": grade, "a": arrangement or "outsourced", "h": schedule_hours, "d": on})).first()
    if row is None:
        return {"amount": None, "basis": basis, "source": None}
    amount = Decimal(row.amount)
    if basis == "hourly_gross":
        amount = in_house_hourly(amount)
    return {"amount": amount, "basis": basis, "source": row.source}


def assess_schedule(
    *,
    pwm_grade: str | None,
    floor: Decimal | None,
    basis: str,
    monthly_salary: Decimal | None = None,
    daily_rate: Decimal | None = None,
    hourly_rate: Decimal | None = None,
    gross_monthly: Decimal | None = None,
    gross_hourly: Decimal | None = None,
) -> dict:
    """Judge one guard against the floor `resolve_floor` found, on its basis.

    In-house floors are on GROSS wage. A payroll run knows it (gross_monthly / gross_hourly, without overtime); a
    profile does not, because allowances vary by month. Without it a basic wage at or above the floor is enough to
    say compliant, and one below it is "not assessed" until payroll, never refused: allowances may make up the gap.
    """
    if not pwm_grade:
        return _verdict(NOT_ASSESSED, "No PWM grade is set for this guard.", basis=basis)
    if floor is None:
        return _verdict(NOT_ASSESSED, "MOM sets no PWM floor for this grade, arrangement and hours.", basis=basis)
    floor = _money(Decimal(floor))
    label = BASIS_LABEL[basis]
    if basis == "monthly_basic":
        actual = monthly_equivalent(monthly_salary=monthly_salary, daily_rate=daily_rate, hourly_rate=hourly_rate)
    elif basis == "hourly_basic":
        actual = hourly_equivalent(monthly_salary=monthly_salary, daily_rate=daily_rate, hourly_rate=hourly_rate)
    else:
        known = gross_monthly if basis == "monthly_gross" else gross_hourly
        if known is not None:
            actual = _money(Decimal(known))
        else:
            basic = (monthly_equivalent if basis == "monthly_gross" else hourly_equivalent)(
                monthly_salary=monthly_salary, daily_rate=daily_rate, hourly_rate=hourly_rate)
            if basic is None:
                return _verdict(NOT_ASSESSED, "No wage is set for this guard.", basis=basis)
            if basic >= floor:
                return _verdict(COMPLIANT, f"Basic wage {basic} alone meets the {label} floor {floor}.",
                                floor_applied=floor, actual=basic, basis=basis)
            return _verdict(NOT_ASSESSED, f"Basic wage {basic} is below the {label} floor {floor}; allowances may "
                            "make it up, so it is judged on the gross wage in the payroll run.",
                            floor_applied=floor, actual=basic, basis=basis)
    if actual is None:
        return _verdict(NOT_ASSESSED, "No wage is set for this guard.", basis=basis)
    if actual < floor:
        return _verdict(BELOW_FLOOR, f"{label[0].upper()}{label[1:]} {actual} is below the PWM floor {floor}.",
                        floor_applied=floor, actual=actual, basis=basis)
    return _verdict(COMPLIANT, f"{label[0].upper()}{label[1:]} {actual} meets the PWM floor {floor}.",
                    floor_applied=floor, actual=actual, basis=basis)
