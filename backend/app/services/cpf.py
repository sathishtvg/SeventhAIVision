"""CPF contributions worked out as CPF Board's published tables say (migration 0175, Phase 1 of closing the known
limits; docs/plans/2026-11-03-known-limits-programme-and-sg-payroll-design.md).

The rates are data (cpf_rate_tables, cpf_wage_ceilings), loaded from CPF's own documents and chosen by the payroll
month. Everything here except `load_rates` is a pure function, so the tests can hold it to CPF's figures row by row.

Every published row follows one rule, from the employer's rate (ER) and the employee's rate (EE) for the age band:

    Total wages (TW = OW + AW)   Total contribution                          Employee's share
    $50 or less                  nil                                         nil
    above $50 to $500            ER x TW                                     nil
    above $500 to $750           ER x TW + 3 x EE x (TW - $500)              3 x EE x (TW - $500)
    above $750                   (ER + EE) x OW + (ER + EE) x AW             EE x OW + EE x AW

with OW capped at the month's Ordinary Wage ceiling and AW at what is left of the year's Additional Wage ceiling.
CPF's rounding: the total to the nearest dollar (50 cents up), the employee's share down to the dollar, the
employer's share the difference.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ZERO = Decimal("0")
_DOLLAR = Decimal("1")
_CENTS = Decimal("0.01")

#: Who pays CPF: Singapore Citizens and PRs (users.work_pass_type).
CPF_RESIDENCIES = ("citizen", "pr")

#: The age bands, by the age an employee is ABOVE. 0 is "55 and below".
AGE_BANDS = (0, 55, 60, 65, 70)

AGE_BAND_LABEL = {0: "55 and below", 55: "above 55 to 60", 60: "above 60 to 65", 65: "above 65 to 70",
                  70: "above 70"}
SCHEME_LABEL = {
    "full": "Citizen / PR from the 3rd year (full rates)",
    "graduated_1": "PR 1st year, graduated rates (G/G)",
    "graduated_2": "PR 2nd year, graduated rates (G/G)",
    "fg_1": "PR 1st year, full employer & graduated employee (F/G)",
    "fg_2": "PR 2nd year, full employer & graduated employee (F/G)",
}


@dataclass(frozen=True)
class Rates:
    """The rates in force for one month: per scheme, per age band, (employer, employee)."""
    effective_from: date
    ow_ceiling: Decimal
    annual_wage_ceiling: Decimal
    bands: dict[str, dict[int, tuple[Decimal, Decimal]]]
    source: str


@dataclass(frozen=True)
class Who:
    """Which table and band apply to one person in one month, or why CPF is not worked out."""
    eligible: bool
    scheme: str | None = None
    age_above: int | None = None
    note: str | None = None        # set when CPF cannot be worked out (not when it is simply not due)


@dataclass(frozen=True)
class Contribution:
    total: Decimal
    employee: Decimal
    employer: Decimal
    ow_subject: Decimal
    aw_subject: Decimal


def _month_index(year: int, month: int) -> int:
    return year * 12 + month


def age_band(date_of_birth: date, year: int, month: int, bands: tuple[int, ...] = AGE_BANDS) -> int:
    """The age band for a payroll month. A new band applies from the first day of the month AFTER the 55th, 60th,
    65th or 70th birthday, so a person turning 55 in March is "55 and below" for March and "above 55" from April."""
    current = _month_index(year, month)
    band = 0
    for threshold in bands:
        if threshold == 0:
            continue
        if current > _month_index(date_of_birth.year + threshold, date_of_birth.month):
            band = threshold
    return band


def pr_year(pr_since: date, year: int, month: int) -> int:
    """1, 2, or 3 (full rates). Year 1 runs to the end of the month of the first anniversary of PR status; year 2
    from the month after, to the end of the month of the second; full rates from the month after that."""
    current = _month_index(year, month)
    if current <= _month_index(pr_since.year + 1, pr_since.month):
        return 1
    if current <= _month_index(pr_since.year + 2, pr_since.month):
        return 2
    return 3


def who_pays(*, work_pass_type: str | None, date_of_birth: date | None, pr_since: date | None,
             cpf_rate_type: str | None, year: int, month: int, rates: Rates | None) -> Who:
    """Which CPF table applies to this person this month. A foreigner pays none. A citizen or PR without the details
    CPF needs is NOT guessed: `note` says what is missing."""
    if work_pass_type not in CPF_RESIDENCIES:
        return Who(eligible=False)
    if rates is None:
        return Who(eligible=True, note=f"No CPF rate table is on file for {year}-{month:02d}")
    if date_of_birth is None:
        return Who(eligible=True, note="No date of birth: CPF depends on age")
    scheme = "full"
    if work_pass_type == "pr":
        if pr_since is None:
            return Who(eligible=True, note="No 'PR since' date: CPF for a PR depends on the PR year")
        year_of_pr = pr_year(pr_since, year, month)
        kind = cpf_rate_type or "graduated"
        if year_of_pr < 3 and kind != "full":
            scheme = f"{'graduated' if kind == 'graduated' else 'fg'}_{year_of_pr}"
    available = tuple(sorted(rates.bands.get(scheme, {})))
    if not available:
        return Who(eligible=True, note=f"No '{scheme}' table is on file for {year}-{month:02d}")
    return Who(eligible=True, scheme=scheme, age_above=age_band(date_of_birth, year, month, available))


def _round_total(amount: Decimal) -> Decimal:
    return amount.quantize(_DOLLAR, rounding=ROUND_HALF_UP)


def _round_employee(amount: Decimal) -> Decimal:
    return amount.quantize(_DOLLAR, rounding=ROUND_DOWN)


def contribution(*, ordinary_wage: Decimal, additional_wage: Decimal, employer_rate: Decimal, employee_rate: Decimal,
                 ow_ceiling: Decimal, aw_ceiling_left: Decimal | None = None) -> Contribution:
    """One month's CPF. `aw_ceiling_left` is what remains of the year's Additional Wage ceiling (None: no AW cap
    applies, as when there is no AW)."""
    ow = max(Decimal(ordinary_wage), ZERO)
    aw = max(Decimal(additional_wage), ZERO)
    total_wages = ow + aw
    ow_subject = min(ow, Decimal(ow_ceiling))
    aw_subject = aw if aw_ceiling_left is None else min(aw, max(Decimal(aw_ceiling_left), ZERO))
    er, ee = Decimal(employer_rate), Decimal(employee_rate)

    if total_wages <= Decimal("50"):
        return Contribution(ZERO, ZERO, ZERO, ow_subject, aw_subject)
    if total_wages <= Decimal("500"):
        total = _round_total(er * total_wages)
        return Contribution(total, ZERO, total, ow_subject, aw_subject)
    if total_wages <= Decimal("750"):
        employee_exact = 3 * ee * (total_wages - Decimal("500"))
        total = _round_total(er * total_wages + employee_exact)
        employee = _round_employee(employee_exact)
        return Contribution(total, employee, total - employee, ow_subject, aw_subject)
    total = _round_total((er + ee) * ow_subject + (er + ee) * aw_subject)
    employee = _round_employee(ee * ow_subject + ee * aw_subject)
    return Contribution(total, employee, total - employee, ow_subject, aw_subject)


def aw_ceiling_left(*, annual_wage_ceiling: Decimal, ow_subject_this_year: Decimal,
                    aw_subject_so_far: Decimal) -> Decimal:
    """What is left of the year's Additional Wage ceiling: the annual ceiling ($102,000) less the year's Ordinary
    Wages subject to CPF (as estimated by the caller) and the Additional Wages already contributed on."""
    return max(Decimal(annual_wage_ceiling) - Decimal(ow_subject_this_year) - Decimal(aw_subject_so_far), ZERO)


async def load_rates(db: AsyncSession, on: date) -> Rates | None:
    """The CPF tables and ceilings in force on `on` (the payroll month), or None when none is on file."""
    ceiling = (await db.execute(text(
        "SELECT effective_from, ow_ceiling_monthly, annual_wage_ceiling, source FROM cpf_wage_ceilings "
        "WHERE effective_from <= :d ORDER BY effective_from DESC LIMIT 1"), {"d": on})).first()
    effective = (await db.execute(text(
        "SELECT max(effective_from) FROM cpf_rate_tables WHERE effective_from <= :d"), {"d": on})).scalar()
    if ceiling is None or effective is None:
        return None
    rows = (await db.execute(text(
        "SELECT scheme, age_above, employer_rate, employee_rate, source FROM cpf_rate_tables "
        "WHERE effective_from = :e"), {"e": effective})).all()
    bands: dict[str, dict[int, tuple[Decimal, Decimal]]] = {}
    for scheme, age_above, er, ee, _source in rows:
        bands.setdefault(scheme, {})[int(age_above)] = (Decimal(er), Decimal(ee))
    return Rates(effective_from=effective, ow_ceiling=Decimal(ceiling.ow_ceiling_monthly),
                 annual_wage_ceiling=Decimal(ceiling.annual_wage_ceiling), bands=bands,
                 source=rows[0].source if rows else ceiling.source)


def percent(rate: Decimal) -> str:
    """0.125 -> '12.5%'."""
    return f"{(Decimal(rate) * 100).normalize():f}%"


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(_CENTS, rounding=ROUND_HALF_UP)
