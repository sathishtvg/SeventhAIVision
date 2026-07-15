"""Payroll pay calculation + Singapore CPF contribution (ShiftSecure Phase 5).

Standard full-rate CPF only (Singapore Citizens / PRs from their 3rd year),
capped at the Ordinary Wage ceiling. NOT implemented (documented v1
limitations): graduated 1st/2nd-year PR rates, and the Additional Wage
ceiling (a once-a-year cumulative calculation against bonus-type pay,
which this system has no concept of).
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CPF_OW_CEILING = Decimal("7400")  # monthly Ordinary Wage ceiling (SGD)
OT_MULTIPLIER = Decimal("1.5")    # standard MOM Singapore overtime rate

# (min_age_inclusive, max_age_exclusive, employee_rate, employer_rate)
CPF_RATE_TABLE: list[tuple[int, int, Decimal, Decimal]] = [
    (0, 55, Decimal("0.20"), Decimal("0.17")),
    (55, 60, Decimal("0.15"), Decimal("0.145")),
    (60, 65, Decimal("0.075"), Decimal("0.11")),
    (65, 70, Decimal("0.05"), Decimal("0.085")),
    (70, 200, Decimal("0.05"), Decimal("0.075")),
]

_CENTS = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def is_cpf_eligible(work_pass_type: str | None) -> bool:
    return work_pass_type in ("citizen", "pr")


def compute_age(date_of_birth: date, as_of: date) -> int:
    years = as_of.year - date_of_birth.year
    if (as_of.month, as_of.day) < (date_of_birth.month, date_of_birth.day):
        years -= 1
    return years


def _cpf_rates_for_age(age: int) -> tuple[Decimal, Decimal]:
    for lo, hi, employee_rate, employer_rate in CPF_RATE_TABLE:
        if lo <= age < hi:
            return employee_rate, employer_rate
    return CPF_RATE_TABLE[-1][2], CPF_RATE_TABLE[-1][3]


def compute_cpf(ordinary_wage: Decimal, age: int, work_pass_type: str | None) -> tuple[Decimal, Decimal]:
    """Returns (employee_contribution, employer_contribution), both 0 if
    the guard isn't CPF-eligible. Wage is capped at CPF_OW_CEILING before
    the rate lookup."""
    if not is_cpf_eligible(work_pass_type):
        return Decimal("0"), Decimal("0")
    capped_wage = min(ordinary_wage, CPF_OW_CEILING)
    employee_rate, employer_rate = _cpf_rates_for_age(age)
    return _money(capped_wage * employee_rate), _money(capped_wage * employer_rate)


def compute_payslip(
    regular_hours: Decimal,
    overtime_hours: Decimal,
    days_worked: Decimal,
    hourly_rate: Decimal | None,
    daily_rate: Decimal | None,
    monthly_salary: Decimal | None,
    age: int,
    work_pass_type: str | None,
) -> dict:
    """Pure function: base_pay/overtime_pay/gross_pay/cpf_employee/
    cpf_employer/net_pay. Base-pay precedence: monthly_salary wins, then
    daily_rate * days_worked, then hourly_rate * regular_hours. OT pay is
    only computed when hourly_rate is present, independent of which field
    drove base pay — this is not a new rule, it's the same behavior a
    monthly_salary + hourly_rate guard already gets (salaried base pay,
    hourly-driven OT on top). A day-rate-only guard's overtime_hours are
    tracked (for visibility) but unpaid — a per-day rate has no natural
    hourly-equivalent baseline to multiply an OT premium against, a
    documented v1 simplification, same spirit as the pure-salary case."""
    if monthly_salary is not None:
        base_pay = _money(monthly_salary)
    elif daily_rate is not None:
        base_pay = _money(daily_rate * days_worked)
    elif hourly_rate is not None:
        base_pay = _money(regular_hours * hourly_rate)
    else:
        base_pay = Decimal("0")

    overtime_pay = _money(overtime_hours * hourly_rate * OT_MULTIPLIER) if hourly_rate is not None else Decimal("0")
    gross_pay = base_pay + overtime_pay
    cpf_employee, cpf_employer = compute_cpf(gross_pay, age, work_pass_type)
    net_pay = gross_pay - cpf_employee

    return {
        "base_pay": base_pay,
        "overtime_pay": overtime_pay,
        "gross_pay": gross_pay,
        "cpf_employee": cpf_employee,
        "cpf_employer": cpf_employer,
        "net_pay": net_pay,
    }
