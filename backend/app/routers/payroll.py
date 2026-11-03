"""Payroll runs, payslips, and IR8A annual reporting (ShiftSecure Phase 5).

Pay is computed from signals that already exist: shifts.actual_start/
actual_end and overtime_minutes (Phase 2A), users.work_pass_type/
date_of_birth (Phase 1) for CPF eligibility and age-banded rates, and
users.hourly_rate/monthly_salary (this round). See services/payroll.py
for the CPF calculation and its documented v1 limitations.
"""
import io
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import InvalidTokenError, decode_access_token
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services import cpf, pwm
from app.services.audit import write_audit_log
from app.services.payroll import compute_payslip

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

router = APIRouter(prefix="/api/v1/payroll", tags=["guard-ops"])

_GUARD_ONLY_ROLES = {3, 4, 5}
_PAYROLL_GUARD_ROLES = (3, 4, 5, 8)
GUARD_ROLE_IDS_FOR_GRID = _PAYROLL_GUARD_ROLES  # timesheets cover the same people

# Off by default. A hard gate switched on during an upgrade would break the
# next run for every tenant, none of whom have a timesheet yet — so the gap
# is reported from the first run and enforced only when somebody asks.
REQUIRE_APPROVED_TIMESHEETS_KEY = 'payroll.require_approved_timesheets'


def _check_reportlab():
    if not REPORTLAB_AVAILABLE:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "reportlab is not installed — rebuild the container with reportlab>=4.2",
        )


def _make_styles():
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BrandTitle", parent=styles["Title"], fontSize=20,
        textColor=colors.HexColor("#6C63FF"), spaceAfter=6,
    )
    h2_style = ParagraphStyle(
        "BrandH2", parent=styles["Heading2"], fontSize=13,
        textColor=colors.HexColor("#00D9C0"), spaceBefore=10, spaceAfter=4,
    )
    return styles, title_style, h2_style


class PayrollRunCreate(BaseModel):
    period_start: date
    period_end: date


@router.post("/runs", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("payroll:manage"))])
async def create_payroll_run(
    body: PayrollRunCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if body.period_end < body.period_start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "period_end must not be before period_start")

    # Read once per run, not per guard: whether hours must be approved is a
    # property of the run, and re-reading it mid-loop would let a setting
    # changed during a long run pay half the team under each rule.
    setting = (await db.execute(
        text("SELECT setting_value FROM tenant_settings WHERE setting_key = :k"),
        {"k": REQUIRE_APPROVED_TIMESHEETS_KEY},
    )).first()
    require_timesheets = bool(setting[0]) if setting is not None else False

    run_row = (await db.execute(
        text("""
            INSERT INTO payroll_runs (tenant_id, period_start, period_end, generated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, :ps, :pe, CAST(:uid AS uuid))
            RETURNING id, period_start, period_end, status, generated_at
        """),
        {"ps": body.period_start, "pe": body.period_end, "uid": token.user_id},
    )).first()
    worked = await _work_out_run(db, run_row.id, body.period_start, body.period_end, require_timesheets)
    await db.commit()
    return {**dict(run_row._mapping), **worked}


async def _require_timesheets(db: AsyncSession) -> bool:
    setting = (await db.execute(
        text("SELECT setting_value FROM tenant_settings WHERE setting_key = :k"),
        {"k": REQUIRE_APPROVED_TIMESHEETS_KEY},
    )).first()
    return bool(setting[0]) if setting is not None else False


async def _year_so_far(db: AsyncSession, guard_id, year: int, run_id) -> tuple[Decimal, Decimal]:
    """This guard's Ordinary and Additional Wages already contributed on this calendar year, from FINALISED runs
    other than this one. A payslip from before 0175 has no ow_subject_to_cpf: its gross, capped at that month's
    ceiling, stands in (those runs had no Additional Wages)."""
    row = (await db.execute(text("""
        SELECT COALESCE(SUM(COALESCE(p.ow_subject_to_cpf,
                   LEAST(p.gross_pay, (SELECT c.ow_ceiling_monthly FROM cpf_wage_ceilings c
                                        WHERE c.effective_from <= r.period_end
                                        ORDER BY c.effective_from DESC LIMIT 1)))), 0) AS ow,
               COALESCE(SUM(COALESCE(p.aw_subject_to_cpf, 0)), 0) AS aw
          FROM payslips p JOIN payroll_runs r ON r.id = p.payroll_run_id
         WHERE p.guard_user_id = :gid AND r.status = 'finalized' AND r.id <> CAST(:run AS uuid)
           AND EXTRACT(YEAR FROM r.period_end) = :yr
    """), {"gid": guard_id, "run": str(run_id), "yr": year})).first()
    return Decimal(str(row.ow or 0)), Decimal(str(row.aw or 0))


async def _work_out_run(db: AsyncSession, run_id, period_start: date, period_end: date,
                        require_timesheets: bool) -> dict:
    """Every payslip of a run, from the shifts, the guard's pay, the run's Additional Wages and CPF's tables for the
    month. Used by creating a run and by recalculating a draft one, so the two cannot differ (3 November 2026).

    CPF is worked out for the calendar month of `period_end` (services/cpf.py): the rate table in force then, the
    guard's age band and PR year in that month. A citizen or PR without the details CPF needs is not guessed: the
    payslip says why its CPF is nil (cpf_note) and the run lists it."""
    year, month = period_end.year, period_end.month
    rates = await cpf.load_rates(db, period_end)

    guards = await db.execute(
        text("""
            SELECT id, full_name, date_of_birth, work_pass_type, hourly_rate, daily_rate, monthly_salary,
                   employment_type, pwm_grade, pr_since, cpf_rate_type, employment_arrangement
            FROM users WHERE role_id = ANY(:roles) AND is_active = TRUE
        """),
        {"roles": list(_PAYROLL_GUARD_ROLES)},
    )
    additional = {
        str(r.guard_user_id): Decimal(str(r.total))
        for r in (await db.execute(text(
            "SELECT guard_user_id, SUM(amount) AS total FROM payroll_additional_wages "
            "WHERE payroll_run_id = CAST(:run AS uuid) GROUP BY guard_user_id"), {"run": str(run_id)})).all()
    }

    payslips = []
    warnings = []
    if (period_start.year, period_start.month) != (year, month):
        warnings.append(f"The period spans more than one calendar month: CPF is worked out once, for "
                        f"{period_end:%B %Y}, with that month's ceiling")

    # Below-floor payslips are REPORTED, not refused. Payroll's job is to say
    # what happened — the same reasoning as MAX_OT_HOURS_PER_MONTH — and a run
    # that refuses to finalise means guards are not paid on time, which is its
    # own violation. The wage is blocked earlier, when it is set, where the
    # mistake is cheap to fix.
    #
    # Kept separate from `warnings`: "skipped, no rate configured" is an
    # operator's housekeeping, while a below-floor wage is a legal exposure an
    # agency may have to answer for. Mixing them buries the second in the first.
    pwm_exceptions: list[dict] = []
    _floors: dict[tuple, dict] = {}

    async def _floor_for(grade: str, arrangement: str | None, employment_type: str | None) -> dict:
        """Resolved as of the PERIOD, not today, and once per grade, arrangement and hours rather than once per
        guard — a restated month must be judged by the rates that were in force then."""
        key = (grade, arrangement, pwm.floor_kind(arrangement, employment_type))
        if key not in _floors:
            _floors[key] = await pwm.resolve_floor(db, grade=grade, arrangement=arrangement,
                                                   employment_type=employment_type, on=period_start)
        return _floors[key]

    for g in guards:
        if g.hourly_rate is None and g.monthly_salary is None and g.daily_rate is None:
            if str(g.id) in additional:
                warnings.append(f"{g.full_name or g.id} has additional pay on this run but no pay rate — skipped")
            warnings.append(f"{g.full_name or g.id} has no hourly_rate, daily_rate, or monthly_salary configured — skipped")
            continue

        hours_row = (await db.execute(
            text("""
                SELECT
                    COALESCE(SUM(GREATEST(EXTRACT(EPOCH FROM (CASE WHEN overtime_needs_approval AND scheduled_end IS NOT NULL THEN LEAST(actual_end, scheduled_end + make_interval(mins => COALESCE(overtime_minutes, 0))) ELSE actual_end END - actual_start)), 0) / 3600.0), 0) AS total_hours,
                    COALESCE(SUM(overtime_minutes), 0) / 60.0 AS ot_hours,
                    COUNT(DISTINCT actual_start::date) AS days_worked
                FROM shifts
                WHERE guard_user_id = :gid AND status = 'completed' AND actual_end IS NOT NULL
                  AND actual_start::date >= :ps AND actual_start::date <= :pe
            """),
            {"gid": g.id, "ps": period_start, "pe": period_end},
        )).first()
        total_hours = Decimal(str(hours_row.total_hours or 0))
        ot_hours = Decimal(str(hours_row.ot_hours or 0))
        regular_hours = max(total_hours - ot_hours, Decimal("0"))
        days_worked = Decimal(str(hours_row.days_worked or 0))

        unpaid_row = (await db.execute(
            text("""
                SELECT COALESCE(SUM(lr.days_count), 0) AS days
                FROM leave_requests lr JOIN leave_types lt ON lt.id = lr.leave_type_id
                WHERE lr.guard_user_id = :gid AND lr.status = 'approved' AND lt.name = 'Unpaid Leave'
                  AND lr.start_date <= :pe AND lr.end_date >= :ps
            """),
            {"gid": g.id, "ps": period_start, "pe": period_end},
        )).first()
        unpaid_leave_days = Decimal(str(unpaid_row.days or 0))

        # Gazetted holidays this guard actually worked. Counted from
        # completed shifts joined to the calendar rather than from the calendar
        # alone: a holiday nobody was rostered on carries no premium, and a
        # guard on leave that day did not work it.
        ph_row = (await db.execute(
            text("""
                SELECT COUNT(DISTINCT sh.actual_start::date) AS days
                  FROM shifts sh
                  JOIN public_holidays ph
                    ON ph.holiday_date = sh.actual_start::date
                   AND ph.is_gazetted = TRUE
                 WHERE sh.guard_user_id = :gid AND sh.status = 'completed'
                   AND sh.actual_end IS NOT NULL
                   AND sh.actual_start::date >= :ps AND sh.actual_start::date <= :pe
            """),
            {"gid": g.id, "ps": period_start, "pe": period_end},
        )).first()
        public_holiday_days = Decimal(str(ph_row.days or 0))

        # Allowances come from the shift definition each shift was rostered
        # against, so a guard paid for standing nights is paid per night stood
        # rather than by a flat monthly figure somebody has to remember to set.
        allowance_row = (await db.execute(
            text("""
                SELECT COALESCE(SUM(d.allowance_amount), 0) AS total
                  FROM shifts sh
                  JOIN shift_definitions d ON d.id = sh.shift_definition_id
                 WHERE sh.guard_user_id = :gid AND sh.status = 'completed'
                   AND sh.actual_end IS NOT NULL
                   AND sh.actual_start::date >= :ps AND sh.actual_start::date <= :pe
            """),
            {"gid": g.id, "ps": period_start, "pe": period_end},
        )).first()
        allowance_pay = Decimal(str(allowance_row.total or 0))

        # The human check between a scan and a payslip.
        timesheet = (await db.execute(
            text("""
                SELECT id, status FROM timesheets
                 WHERE guard_user_id = :gid AND period_start = :ps AND period_end = :pe
            """),
            {"gid": g.id, "ps": period_start, "pe": period_end},
        )).first()
        approved = timesheet is not None and timesheet.status == "approved"

        if not approved:
            state = timesheet.status if timesheet else "no timesheet"
            if require_timesheets:
                warnings.append(
                    f"{g.full_name or g.id} skipped — hours are not approved ({state})"
                )
                continue
            warnings.append(
                f"{g.full_name or g.id} paid from unapproved hours ({state})"
            )

        calc = compute_payslip(
            regular_hours, ot_hours, days_worked,
            Decimal(str(g.hourly_rate)) if g.hourly_rate is not None else None,
            Decimal(str(g.daily_rate)) if g.daily_rate is not None else None,
            Decimal(str(g.monthly_salary)) if g.monthly_salary is not None else None,
            public_holiday_days=public_holiday_days,
            allowance_pay=allowance_pay,
        )
        ordinary_wage = calc["ordinary_wage"]
        additional_wage = additional.get(str(g.id), Decimal("0"))

        # CPF from CPF Board's table for this month (services/cpf.py), never guessed.
        who = cpf.who_pays(work_pass_type=g.work_pass_type, date_of_birth=g.date_of_birth, pr_since=g.pr_since,
                           cpf_rate_type=g.cpf_rate_type, year=year, month=month, rates=rates)
        cpf_employee = cpf_employer = Decimal("0")
        ow_subject = aw_subject = ceiling_left = None
        if who.scheme:
            employer_rate, employee_rate = rates.bands[who.scheme][who.age_above]
            if additional_wage > 0:
                ow_year, aw_year = await _year_so_far(db, g.id, year, run_id)
                this_month = min(ordinary_wage, rates.ow_ceiling)
                ceiling_left = cpf.aw_ceiling_left(
                    annual_wage_ceiling=rates.annual_wage_ceiling,
                    ow_subject_this_year=ow_year + this_month * (13 - month),   # this month and the rest of the year
                    aw_subject_so_far=aw_year,
                )
            paid = cpf.contribution(ordinary_wage=ordinary_wage, additional_wage=additional_wage,
                                    employer_rate=employer_rate, employee_rate=employee_rate,
                                    ow_ceiling=rates.ow_ceiling, aw_ceiling_left=ceiling_left)
            cpf_employee, cpf_employer = paid.employee, paid.employer
            ow_subject, aw_subject = paid.ow_subject, paid.aw_subject
        elif who.note:
            warnings.append(f"{g.full_name or g.id}: CPF not worked out — {who.note}")
        gross_pay = ordinary_wage + additional_wage
        net_pay = gross_pay - cpf_employee

        # Surfaced on the run, not blocked: refusing to pay hours somebody has
        # already worked would be the wrong correction, but a month over the
        # Employment Act limit needs an MOM exemption and somebody has to know.
        if calc["ot_hours_over_statutory_cap"] > 0:
            warnings.append(
                f"{g.full_name or g.id} exceeded the 72-hour monthly overtime limit by "
                f"{calc['ot_hours_over_statutory_cap']:.1f}h — this requires an MOM exemption"
            )

        row = (await db.execute(
            text("""
                INSERT INTO payslips (
                    tenant_id, payroll_run_id, guard_user_id, regular_hours, overtime_hours, days_worked,
                    base_pay, overtime_pay, gross_pay, cpf_employee, cpf_employer, net_pay, unpaid_leave_days,
                    public_holiday_days, public_holiday_pay, allowance_pay, timesheet_id,
                    ordinary_wage, additional_wage, ow_subject_to_cpf, aw_subject_to_cpf, aw_ceiling_left,
                    cpf_scheme, cpf_age_above, cpf_rates_from, cpf_note
                ) VALUES (
                    current_setting('app.current_tenant')::uuid, :run_id, :gid, :reg, :ot, :days,
                    :base, :otpay, :gross, :cpfe, :cpfr, :net, :unpaid,
                    :phdays, :phpay, :allow, CAST(:ts AS uuid),
                    :ow, :aw, :ows, :aws, :awleft, :scheme, :age, :ratesfrom, :note
                )
                RETURNING id, guard_user_id, regular_hours, overtime_hours, days_worked, base_pay, overtime_pay,
                          gross_pay, cpf_employee, cpf_employer, net_pay, unpaid_leave_days,
                          public_holiday_days, public_holiday_pay, allowance_pay,
                          ordinary_wage, additional_wage, ow_subject_to_cpf, aw_subject_to_cpf, aw_ceiling_left,
                          cpf_scheme, cpf_age_above, cpf_rates_from, cpf_note
            """),
            {
                "run_id": run_id, "gid": g.id, "reg": regular_hours, "ot": ot_hours, "days": days_worked,
                "base": calc["base_pay"], "otpay": calc["overtime_pay"], "gross": gross_pay,
                "cpfe": cpf_employee, "cpfr": cpf_employer, "net": net_pay,
                "unpaid": unpaid_leave_days,
                "phdays": public_holiday_days, "phpay": calc["public_holiday_pay"],
                "allow": calc["allowance_pay"],
                "ts": str(timesheet.id) if timesheet else None,
                "ow": ordinary_wage, "aw": additional_wage, "ows": ow_subject, "aws": aw_subject,
                "awleft": ceiling_left, "scheme": who.scheme, "age": who.age_above,
                "ratesfrom": rates.effective_from if who.scheme else None, "note": who.note,
            },
        )).first()
        payslips.append(dict(row._mapping))

        # Against MOM's schedule for this guard's arrangement and hours (services/pwm.py). Outsourced officers are
        # judged on BASIC wage: overtime and allowances are not basic pay, and counting them would let a guard paid
        # below the floor look compliant purely by working more hours. In-house officers are judged on GROSS wage
        # without overtime, as MOM sets it: basic, holiday pay and allowances.
        if g.pwm_grade:
            floor = await _floor_for(g.pwm_grade, g.employment_arrangement, g.employment_type)
            gross_without_ot = calc["base_pay"] + calc["public_holiday_pay"] + calc["allowance_pay"]
            assessment = pwm.assess_schedule(
                pwm_grade=g.pwm_grade,
                floor=floor["amount"],
                basis=floor["basis"],
                monthly_salary=g.monthly_salary,
                daily_rate=g.daily_rate,
                hourly_rate=g.hourly_rate,
                gross_monthly=gross_without_ot,
                gross_hourly=(gross_without_ot / regular_hours) if regular_hours > 0 else None,
            )
            if assessment["verdict"] == pwm.BELOW_FLOOR:
                pwm_exceptions.append({
                    "user_id": str(g.id),
                    "full_name": g.full_name,
                    "pwm_grade": g.pwm_grade,
                    "floor_applied": assessment["floor_applied"],
                    "actual": assessment["actual"],
                    "basis": assessment["basis"],
                    "source": floor["source"],
                    "detail": assessment["reason"],
                })

    return {
        "cpf_rates_from": rates.effective_from if rates else None,
        "payslips": payslips,
        "warnings": warnings,
        "pwm_exceptions": pwm_exceptions,
    }


@router.get("/runs", dependencies=[Depends(require_permission("payroll:read"))])
async def list_payroll_runs(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("""
            SELECT id, period_start, period_end, status, generated_at, finalized_at
            FROM payroll_runs ORDER BY period_start DESC
        """)
    )
    return [dict(r._mapping) for r in result]


@router.get("/runs/{run_id}", dependencies=[Depends(require_permission("payroll:read"))])
async def get_payroll_run(
    run_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    run_row = (await db.execute(
        text("SELECT id, period_start, period_end, status, generated_at, finalized_at, recalculated_at, "
             "additional_wages_changed_at FROM payroll_runs WHERE id = CAST(:id AS uuid)"),
        {"id": run_id},
    )).first()
    if run_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payroll run not found")

    where = "WHERE p.payroll_run_id = CAST(:id AS uuid)"
    params: dict = {"id": run_id}
    if token.role_id in _GUARD_ONLY_ROLES:
        where += " AND p.guard_user_id = :uid"
        params["uid"] = token.user_id

    payslips = await db.execute(
        text(f"""
            SELECT p.id, p.guard_user_id, p.regular_hours, p.overtime_hours, p.days_worked, p.base_pay,
                   p.overtime_pay, p.gross_pay, p.cpf_employee, p.cpf_employer, p.net_pay,
                   p.unpaid_leave_days, u.full_name AS guard_name,
                   p.ordinary_wage, p.additional_wage, p.ow_subject_to_cpf, p.aw_subject_to_cpf, p.aw_ceiling_left,
                   p.cpf_scheme, p.cpf_age_above, p.cpf_rates_from, p.cpf_note
            FROM payslips p JOIN users u ON u.id = p.guard_user_id
            {where}
            ORDER BY u.full_name
        """),
        params,
    )
    rows = []
    for r in payslips:
        d = dict(r._mapping)
        d["cpf_scheme_label"] = cpf.SCHEME_LABEL.get(d["cpf_scheme"]) if d["cpf_scheme"] else None
        d["cpf_age_band_label"] = cpf.AGE_BAND_LABEL.get(d["cpf_age_above"]) if d["cpf_age_above"] is not None else None
        rows.append(d)
    # Additional pay changed after the payslips were worked out: they no longer show it until the run is recalculated.
    changed = run_row.additional_wages_changed_at
    worked_at = run_row.recalculated_at or run_row.generated_at
    return {**dict(run_row._mapping), "payslips": rows,
            "needs_recalculation": run_row.status == "draft" and changed is not None and changed > worked_at}


async def _additional_wages_changed(db: AsyncSession, run_id: str) -> None:
    await db.execute(text("UPDATE payroll_runs SET additional_wages_changed_at = clock_timestamp() "
                          "WHERE id = CAST(:run AS uuid)"), {"run": run_id})


async def _draft_run(db: AsyncSession, run_id: str):
    run = (await db.execute(text(
        "SELECT id, period_start, period_end, status FROM payroll_runs WHERE id = CAST(:id AS uuid)"),
        {"id": run_id})).first()
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payroll run not found")
    if run.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "This payroll run is finalised: it cannot be changed")
    return run


@router.post("/runs/{run_id}/recalculate", dependencies=[Depends(require_permission("payroll:manage"))])
async def recalculate_payroll_run(
    run_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Work a DRAFT run out again: its shifts, the pay on file now, its Additional Wages and CPF's table for the
    month. A finalised run is never changed (3 November 2026)."""
    run = await _draft_run(db, run_id)
    require_timesheets = await _require_timesheets(db)
    await db.execute(text("DELETE FROM payslips WHERE payroll_run_id = CAST(:id AS uuid)"), {"id": run_id})
    worked = await _work_out_run(db, run.id, run.period_start, run.period_end, require_timesheets)
    row = (await db.execute(text(
        "UPDATE payroll_runs SET recalculated_at = clock_timestamp() WHERE id = CAST(:id AS uuid) "
        "RETURNING id, period_start, period_end, status, generated_at, recalculated_at"), {"id": run_id})).first()
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="payroll.run.recalculated", resource_type="payroll_run", resource_id=run_id,
                          detail={"payslips": len(worked["payslips"])})
    await db.commit()
    return {**dict(row._mapping), **worked}


# ── Additional Wages: bonus-type pay on a draft run (CPF's AW) ──────────────────

AdditionalWageKind = Literal["bonus", "aws", "leave_encashment", "incentive", "other"]
AW_KIND_LABEL = {"bonus": "Bonus", "aws": "13th month (AWS)", "leave_encashment": "Leave encashment",
                 "incentive": "Incentive", "other": "Other"}


class AdditionalWageIn(BaseModel):
    guard_user_id: str
    kind: AdditionalWageKind
    amount: Decimal = Field(gt=0, le=Decimal("1000000"), decimal_places=2)
    note: str | None = Field(default=None, max_length=500)


class AdditionalWageChange(BaseModel):
    kind: AdditionalWageKind | None = None
    amount: Decimal | None = Field(default=None, gt=0, le=Decimal("1000000"), decimal_places=2)
    note: str | None = Field(default=None, max_length=500)


@router.get("/runs/{run_id}/additional-wages", dependencies=[Depends(require_permission("payroll:read"))])
async def list_additional_wages(run_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    rows = (await db.execute(text("""
        SELECT a.id, a.guard_user_id, u.full_name AS guard_name, a.kind, a.amount, a.note, a.created_at, a.updated_at
          FROM payroll_additional_wages a JOIN users u ON u.id = a.guard_user_id
         WHERE a.payroll_run_id = CAST(:id AS uuid)
         ORDER BY u.full_name, a.created_at
    """), {"id": run_id})).mappings().all()
    return [{**dict(r), "kind_label": AW_KIND_LABEL[r["kind"]]} for r in rows]


@router.post("/runs/{run_id}/additional-wages", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("payroll:manage"))])
async def add_additional_wage(
    run_id: str,
    body: AdditionalWageIn,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _draft_run(db, run_id)
    guard = (await db.execute(text(
        "SELECT id FROM users WHERE id = CAST(:g AS uuid) AND role_id = ANY(:roles)"),
        {"g": body.guard_user_id, "roles": list(_PAYROLL_GUARD_ROLES)})).first()
    if guard is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No employee on payroll with that id")
    row = (await db.execute(text("""
        INSERT INTO payroll_additional_wages (tenant_id, payroll_run_id, guard_user_id, kind, amount, note,
                                              created_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:run AS uuid), CAST(:g AS uuid), :k, :a, :n,
                CAST(:u AS uuid))
        RETURNING id, guard_user_id, kind, amount, note, created_at, updated_at
    """), {"run": run_id, "g": body.guard_user_id, "k": body.kind, "a": body.amount,
           "n": (body.note or "").strip() or None, "u": token.user_id})).mappings().first()
    await _additional_wages_changed(db, run_id)
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="payroll.additional_wage.added", resource_type="payroll_run", resource_id=run_id,
                          detail={"line": str(row["id"]), "guard_user_id": body.guard_user_id, "kind": body.kind,
                                  "amount": str(body.amount)})
    await db.commit()
    return {**dict(row), "kind_label": AW_KIND_LABEL[row["kind"]]}


@router.put("/runs/{run_id}/additional-wages/{line_id}", dependencies=[Depends(require_permission("payroll:manage"))])
async def change_additional_wage(
    run_id: str,
    line_id: str,
    body: AdditionalWageChange,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _draft_run(db, run_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nothing to change")
    if "amount" in changes and changes["amount"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "An amount is required")
    if "kind" in changes and changes["kind"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A kind is required")
    if "note" in changes:
        changes["note"] = (changes["note"] or "").strip() or None
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    row = (await db.execute(text(f"""
        UPDATE payroll_additional_wages SET {sets}, updated_at = clock_timestamp()
         WHERE id = CAST(:id AS uuid) AND payroll_run_id = CAST(:run AS uuid)
        RETURNING id, guard_user_id, kind, amount, note, created_at, updated_at
    """), {**changes, "id": line_id, "run": run_id})).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such additional pay on this run")
    await _additional_wages_changed(db, run_id)
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="payroll.additional_wage.changed", resource_type="payroll_run", resource_id=run_id,
                          detail={"line": line_id, **{k: str(v) for k, v in changes.items()}})
    await db.commit()
    return {**dict(row), "kind_label": AW_KIND_LABEL[row["kind"]]}


@router.delete("/runs/{run_id}/additional-wages/{line_id}", dependencies=[Depends(require_permission("payroll:manage"))])
async def remove_additional_wage(
    run_id: str,
    line_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _draft_run(db, run_id)
    row = (await db.execute(text(
        "DELETE FROM payroll_additional_wages WHERE id = CAST(:id AS uuid) AND payroll_run_id = CAST(:run AS uuid) "
        "RETURNING guard_user_id, kind, amount"), {"id": line_id, "run": run_id})).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such additional pay on this run")
    await _additional_wages_changed(db, run_id)
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="payroll.additional_wage.removed", resource_type="payroll_run", resource_id=run_id,
                          detail={"line": line_id, "guard_user_id": str(row.guard_user_id), "kind": row.kind,
                                  "amount": str(row.amount)})
    await db.commit()
    return {"removed": line_id}


@router.put("/runs/{run_id}/finalize", dependencies=[Depends(require_permission("payroll:manage"))])
async def finalize_payroll_run(run_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(
        text("""
            UPDATE payroll_runs SET status = 'finalized', finalized_at = now()
            WHERE id = CAST(:id AS uuid) AND status = 'draft'
            RETURNING id, status
        """),
        {"id": run_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payroll run not found or already finalized")
    await db.commit()
    return dict(row._mapping)


@router.get("/payslips/{payslip_id}/pdf")
async def get_payslip_pdf(
    payslip_id: str,
    token: str = Query(..., description="JWT access token"),
):
    """Query-param-JWT auth (same pattern as shifts.py's check-in photo
    endpoint) — a plain download link/<a href> can't set an Authorization
    header, so the token travels in the URL instead of the standard
    get_db_with_tenant chain."""
    _check_reportlab()
    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))

    tenant_id = payload["tenant_id"]
    role_id = payload["role_id"]
    user_id = payload["sub"]

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tenant_id},
        )
        perm = await session.execute(
            text(
                "SELECT 1 FROM role_permissions rp "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE rp.role_id = :role_id AND p.code = 'payroll:read'"
            ),
            {"role_id": role_id},
        )
        if perm.first() is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: payroll:read")

        row = (await session.execute(
            text("""
                SELECT p.*, u.full_name AS guard_name, r.period_start, r.period_end, t.name AS tenant_name
                FROM payslips p
                JOIN users u ON u.id = p.guard_user_id
                JOIN payroll_runs r ON r.id = p.payroll_run_id
                JOIN tenants t ON t.id = p.tenant_id
                WHERE p.id = CAST(:id AS uuid)
            """),
            {"id": payslip_id},
        )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payslip not found")
    if role_id in _GUARD_ONLY_ROLES and str(row.guard_user_id) != str(user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only view your own payslip")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                             topMargin=15 * mm, bottomMargin=15 * mm)
    styles, title_style, h2_style = _make_styles()
    elements = [
        Paragraph(row.tenant_name or "7th AI Vision", title_style),
        Paragraph("Payslip", styles["Heading1"]),
        Paragraph(f"Employee: {row.guard_name}", styles["Normal"]),
        Paragraph(f"Period: {row.period_start} to {row.period_end}", styles["Normal"]),
        Paragraph(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", styles["Normal"]),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#6C63FF")),
        Spacer(1, 4 * mm),
    ]
    data = [
        ["Regular Hours", f"{row.regular_hours:.2f}"],
        ["Overtime Hours", f"{row.overtime_hours:.2f}"],
        ["Days Worked", f"{row.days_worked:.2f}"],
        ["Base Pay", f"${row.base_pay:.2f}"],
        ["Overtime Pay", f"${row.overtime_pay:.2f}"],
        ["Gross Pay", f"${row.gross_pay:.2f}"],
        ["CPF (Employee)", f"-${row.cpf_employee:.2f}"],
        ["Net Pay", f"${row.net_pay:.2f}"],
        ["CPF (Employer, informational)", f"${row.cpf_employer:.2f}"],
        ["Unpaid Leave Days", f"{row.unpaid_leave_days:.2f}"],
    ]
    # Since 0175: what the CPF was worked out on, as CPF Board's statement of account would show it.
    if row.ordinary_wage is not None:
        data += [
            ["Ordinary Wages", f"${row.ordinary_wage:.2f}"],
            ["Additional Wages (bonus)", f"${row.additional_wage:.2f}"],
        ]
        if row.cpf_scheme:
            data.append(["CPF table", f"{cpf.SCHEME_LABEL[row.cpf_scheme]}, "
                                      f"{cpf.AGE_BAND_LABEL[row.cpf_age_above]}, rates from {row.cpf_rates_from}"])
        if row.cpf_note:
            data.append(["CPF not worked out", row.cpf_note])
    tbl = Table(data, colWidths=[100 * mm, 60 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6C63FF")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 5), (-1, 5), "Helvetica-Bold"),
        ("FONTNAME", (0, 7), (-1, 7), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#F5F5FF")]),
    ]))
    elements.append(tbl)
    doc.build(elements)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="payslip_{row.guard_name}_{row.period_start}.pdf"'},
    )


@router.get("/ir8a", dependencies=[Depends(require_permission("payroll:manage"))])
async def get_ir8a_summary(year: int, db: AsyncSession = Depends(get_db_with_tenant)):
    # Additional Wages (bonuses) are in the gross since 0175, and listed apart with the employee's CPF.
    result = await db.execute(
        text("""
            SELECT u.id AS guard_user_id, u.full_name, u.nric_fin,
                   SUM(p.gross_pay) AS annual_gross, SUM(p.cpf_employer) AS annual_employer_cpf,
                   SUM(p.additional_wage) AS annual_additional_wages, SUM(p.cpf_employee) AS annual_employee_cpf
            FROM payslips p
            JOIN payroll_runs r ON r.id = p.payroll_run_id
            JOIN users u ON u.id = p.guard_user_id
            WHERE r.status = 'finalized' AND EXTRACT(YEAR FROM r.period_start) = :yr
            GROUP BY u.id, u.full_name, u.nric_fin
            ORDER BY u.full_name
        """),
        {"yr": year},
    )
    return [dict(r._mapping) for r in result]


@router.get("/ir8a/pdf")
async def get_ir8a_pdf(year: int, token: str = Query(..., description="JWT access token")):
    """Query-param-JWT auth — same rationale as get_payslip_pdf above."""
    _check_reportlab()
    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))

    tenant_id = payload["tenant_id"]
    role_id = payload["role_id"]

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tenant_id},
        )
        perm = await session.execute(
            text(
                "SELECT 1 FROM role_permissions rp "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE rp.role_id = :role_id AND p.code = 'payroll:manage'"
            ),
            {"role_id": role_id},
        )
        if perm.first() is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: payroll:manage")
        rows = await get_ir8a_summary(year, session)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                             topMargin=15 * mm, bottomMargin=15 * mm)
    styles, title_style, h2_style = _make_styles()
    elements = [
        Paragraph("7th AI Vision", title_style),
        Paragraph(f"IR8A Annual Employment Income Summary — {year}", styles["Heading1"]),
        Paragraph(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", styles["Normal"]),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#6C63FF")),
        Spacer(1, 4 * mm),
    ]
    if not rows:
        elements.append(Paragraph("No finalized payroll runs found for this year.", styles["Normal"]))
    else:
        data = [["Name", "NRIC/FIN", "Annual Gross", "Annual Employer CPF"]]
        for r in rows:
            data.append([
                r["full_name"] or "—", r["nric_fin"] or "—",
                f"${r['annual_gross']:.2f}", f"${r['annual_employer_cpf']:.2f}",
            ])
        tbl = Table(data, colWidths=[50 * mm, 35 * mm, 35 * mm, 40 * mm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A0828")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5FF")]),
        ]))
        elements.append(tbl)
    doc.build(elements)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="ir8a_{year}.pdf"'},
    )


# ── Timesheets ──────────────────────────────────────────────────────────────
#
# The human check between a check-in scan and a payslip. A run reads
# shifts.actual_start/actual_end and turns them into money; these establish
# that somebody looked at a period's hours as a whole and found them right.


class TimesheetGenerate(BaseModel):
    period_start: date
    period_end: date
    # None means everyone with hours in the period.
    guard_user_ids: list[str] | None = None


class TimesheetReview(BaseModel):
    review_notes: str | None = None


# Paid hours: a shift on the overtime-approval rule (0168) counts to its planned end plus the overtime it
# carries - approved, up to the time worked; unapproved extra time is not paid. Shifts from before count
# check-in to check-out, as they always did. The payroll run above uses the same expression.
_TIMESHEET_HOURS_SQL = """
    SELECT COALESCE(SUM(GREATEST(EXTRACT(EPOCH FROM (CASE WHEN overtime_needs_approval AND scheduled_end IS NOT NULL THEN LEAST(actual_end, scheduled_end + make_interval(mins => COALESCE(overtime_minutes, 0))) ELSE actual_end END - actual_start)), 0) / 3600.0), 0) AS total_hours,
           COALESCE(SUM(overtime_minutes), 0) / 60.0 AS ot_hours,
           COUNT(DISTINCT actual_start::date) AS days_worked,
           COUNT(*) AS shift_count
      FROM shifts
     WHERE guard_user_id = :gid AND status = 'completed' AND actual_end IS NOT NULL
       AND actual_start::date >= :ps AND actual_start::date <= :pe
"""


@router.get("/pwm-compliance", dependencies=[Depends(require_permission("payroll:read"))])
async def pwm_compliance_report(
    as_of: date | None = Query(
        None, description="Judge against the floors in force on this date "
                          "(default: today)."),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Every guard, their grade, their pay, the floor, and the verdict.

    This exists because blocking a below-floor wage on save only protects wages
    set from now on. It finds nobody who was ALREADY below the floor when the
    check was switched on — and on day one that is the entire workforce. Without
    this report the feature would prevent future mistakes while silently sitting
    on present ones.

    `not_assessed` is reported as its own count rather than folded into
    "compliant". A guard with no grade, or a grade with no rate on file, has not
    been checked, and an agency reading "0 below floor" needs to know how much of
    that is a clean bill of health and how much is nobody having looked yet.
    """
    when = as_of or date.today()

    guards = (await db.execute(
        text("""
            SELECT id, full_name, employee_code, pwm_grade, employment_type, employment_arrangement,
                   monthly_salary, daily_rate, hourly_rate
              FROM users
             WHERE role_id = ANY(:roles) AND is_active = TRUE
             ORDER BY pwm_grade NULLS FIRST, full_name
        """),
        {"roles": list(_PAYROLL_GUARD_ROLES)},
    )).mappings().all()

    # Against MOM's schedule for each guard's arrangement and hours (0175). In-house officers are judged on gross
    # wage, which only a payroll run knows: here a basic wage below the floor is "not assessed", not "below".
    floors: dict[tuple, dict] = {}
    rows = []
    for g in guards:
        grade = g["pwm_grade"]
        key = (grade, g["employment_arrangement"], pwm.floor_kind(g["employment_arrangement"], g["employment_type"]))
        if key not in floors:
            floors[key] = await pwm.resolve_floor(db, grade=grade, arrangement=g["employment_arrangement"],
                                                  employment_type=g["employment_type"], on=when)
        floor = floors[key]
        assessment = pwm.assess_schedule(
            pwm_grade=grade,
            floor=floor["amount"],
            basis=floor["basis"],
            monthly_salary=g["monthly_salary"],
            daily_rate=g["daily_rate"],
            hourly_rate=g["hourly_rate"],
        )
        rows.append({
            "user_id": str(g["id"]),
            "full_name": g["full_name"],
            "employee_code": g["employee_code"],
            "pwm_grade": grade,
            "employment_type": g["employment_type"],
            "employment_arrangement": g["employment_arrangement"],
            "verdict": assessment["verdict"],
            "floor_applied": assessment["floor_applied"],
            "actual": assessment["actual"],
            "basis": assessment["basis"],
            "basis_label": pwm.BASIS_LABEL[floor["basis"]],
            "source": floor["source"],
            "detail": assessment["reason"],
        })

    return {
        "as_of": when,
        "summary": {
            "total": len(rows),
            "compliant": sum(1 for r in rows if r["verdict"] == pwm.COMPLIANT),
            "below_floor": sum(1 for r in rows if r["verdict"] == pwm.BELOW_FLOOR),
            "not_assessed": sum(1 for r in rows if r["verdict"] == pwm.NOT_ASSESSED),
        },
        "guards": rows,
    }


@router.get("/aw-ceiling-check", dependencies=[Depends(require_permission("payroll:read"))])
async def additional_wage_ceiling_check(year: int = Query(..., ge=2000, le=2100),
                                        db: AsyncSession = Depends(get_db_with_tenant)):
    """The year-end check of CPF's Additional Wage ceiling, from FINALISED runs: each employee's Ordinary Wages
    subject to CPF, their Additional Wages, the ceiling those leave ($102,000 less the OW), and whether the AW
    contributed on is over or under it - as when pay changed later in the year than a bonus was paid. It reports
    and changes nothing (3 November 2026)."""
    ceiling = (await db.execute(text(
        "SELECT annual_wage_ceiling FROM cpf_wage_ceilings WHERE effective_from <= make_date(:y, 12, 31) "
        "ORDER BY effective_from DESC LIMIT 1"), {"y": year})).scalar()
    rows = (await db.execute(text("""
        SELECT u.id AS guard_user_id, u.full_name,
               SUM(COALESCE(p.ow_subject_to_cpf, 0)) AS ow_subject,
               SUM(p.additional_wage) AS additional_wages,
               SUM(COALESCE(p.aw_subject_to_cpf, 0)) AS aw_subject,
               bool_or(p.cpf_scheme IS NOT NULL) AS pays_cpf
          FROM payslips p
          JOIN payroll_runs r ON r.id = p.payroll_run_id
          JOIN users u ON u.id = p.guard_user_id
         WHERE r.status = 'finalized' AND EXTRACT(YEAR FROM r.period_end) = :y
         GROUP BY u.id, u.full_name
        HAVING SUM(p.additional_wage) > 0
         ORDER BY u.full_name
    """), {"y": year})).mappings().all()
    out = []
    for r in rows:
        if not r["pays_cpf"] or ceiling is None:
            continue
        allowed = max(Decimal(ceiling) - Decimal(r["ow_subject"]), Decimal("0"))
        should = min(Decimal(r["additional_wages"]), allowed)
        difference = Decimal(r["aw_subject"]) - should
        out.append({
            "guard_user_id": str(r["guard_user_id"]), "full_name": r["full_name"],
            "ow_subject_to_cpf": Decimal(r["ow_subject"]), "additional_wages": Decimal(r["additional_wages"]),
            "aw_ceiling": allowed, "aw_contributed_on": Decimal(r["aw_subject"]), "aw_due_on": should,
            "difference": difference,
            "verdict": "over" if difference > 0 else "under" if difference < 0 else "right",
        })
    return {"year": year, "annual_wage_ceiling": ceiling, "employees": out,
            "summary": {"checked": len(out), "over": sum(1 for o in out if o["verdict"] == "over"),
                        "under": sum(1 for o in out if o["verdict"] == "under")}}


class CpfPreviewIn(BaseModel):
    work_pass_type: Literal["citizen", "pr", "ep", "sp", "wp"]
    date_of_birth: date | None = None
    pr_since: date | None = None
    cpf_rate_type: Literal["graduated", "fg", "full"] | None = None
    monthly_salary: Decimal = Field(ge=0, le=Decimal("1000000"))
    month: date | None = None


@router.post("/cpf-preview", dependencies=[Depends(require_permission("user:read"))])
async def cpf_preview(body: CpfPreviewIn, db: AsyncSession = Depends(get_db_with_tenant)):
    """What CPF a salary carries for a person, from the same rules payroll uses (services/cpf.py), so the employee
    form and the payslip cannot disagree. Shown while an employee is created or edited (3 November 2026). Reads no
    record: only the CPF tables and what was sent."""
    on = body.month or date.today()
    rates = await cpf.load_rates(db, on)
    who = cpf.who_pays(work_pass_type=body.work_pass_type, date_of_birth=body.date_of_birth, pr_since=body.pr_since,
                       cpf_rate_type=body.cpf_rate_type, year=on.year, month=on.month, rates=rates)
    salary = cpf.money(body.monthly_salary)
    answer: dict = {"eligible": who.eligible, "monthly_salary": salary, "note": who.note,
                    "month": date(on.year, on.month, 1)}
    if not who.eligible:
        return {**answer, "note": "No CPF for foreign employees.", "employee_cpf": Decimal("0"),
                "employer_cpf": Decimal("0"), "total_cpf": Decimal("0"), "take_home": salary}
    if not who.scheme:
        return answer
    employer_rate, employee_rate = rates.bands[who.scheme][who.age_above]
    paid = cpf.contribution(ordinary_wage=salary, additional_wage=Decimal("0"), employer_rate=employer_rate,
                            employee_rate=employee_rate, ow_ceiling=rates.ow_ceiling)
    return {
        **answer,
        "scheme": who.scheme, "scheme_label": cpf.SCHEME_LABEL[who.scheme],
        "age_band": who.age_above, "age_band_label": cpf.AGE_BAND_LABEL[who.age_above],
        "employer_rate": employer_rate, "employee_rate": employee_rate,
        "employer_percent": cpf.percent(employer_rate), "employee_percent": cpf.percent(employee_rate),
        "total_percent": cpf.percent(employer_rate + employee_rate),
        "employee_cpf": paid.employee, "employer_cpf": paid.employer, "total_cpf": paid.total,
        "take_home": salary - paid.employee,
        "ow_ceiling": rates.ow_ceiling, "rates_from": rates.effective_from, "source": rates.source,
        "below_full_rates": salary <= Decimal("750"),
    }


@router.post("/timesheets/generate", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("timesheet:manage"))])
async def generate_timesheets(
    body: TimesheetGenerate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Build a draft timesheet per guard for a period, from the shifts as they
    stand right now.

    Re-running REFRESHES drafts and leaves reviewed ones alone. Corrections
    arrive after a period closes and before payroll runs, so regenerating has
    to pick them up — but silently rewriting something a supervisor already
    approved would make the approval meaningless, so those are skipped and
    counted.
    """
    if body.period_end < body.period_start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "period_end must not be before period_start")

    guard_filter = ""
    params: dict = {"roles": list(GUARD_ROLE_IDS_FOR_GRID)}
    if body.guard_user_ids:
        guard_filter = "AND u.id = ANY(CAST(:ids AS uuid[]))"
        params["ids"] = body.guard_user_ids

    guards = [dict(r) for r in (await db.execute(
        text(f"""
            SELECT u.id, u.full_name, u.email, u.employee_code
              FROM users u
             WHERE u.is_active = TRUE AND u.role_id = ANY(:roles) {guard_filter}
          ORDER BY u.full_name NULLS LAST, u.email
        """),
        params,
    )).mappings().all()]

    created = refreshed = locked = 0
    rows = []
    for g in guards:
        h = (await db.execute(
            text(_TIMESHEET_HOURS_SQL),
            {"gid": str(g["id"]), "ps": body.period_start, "pe": body.period_end},
        )).first()

        total = Decimal(str(h.total_hours or 0))
        ot = Decimal(str(h.ot_hours or 0))
        regular = max(total - ot, Decimal("0"))

        # Somebody with no completed shifts has nothing to approve. Creating an
        # empty timesheet for them would fill the review queue with rows that
        # say nothing and train people to approve without looking.
        if h.shift_count == 0:
            continue

        existing = (await db.execute(
            text("SELECT id, status FROM timesheets "
                 "WHERE guard_user_id = CAST(:gid AS uuid) "
                 "AND period_start = :ps AND period_end = :pe"),
            {"gid": str(g["id"]), "ps": body.period_start, "pe": body.period_end},
        )).first()

        if existing and existing.status in ("approved", "rejected"):
            locked += 1
            continue

        row = (await db.execute(
            text("""
                INSERT INTO timesheets
                    (tenant_id, guard_user_id, period_start, period_end,
                     regular_hours, overtime_hours, days_worked, shift_count)
                VALUES (current_setting('app.current_tenant')::uuid,
                        CAST(:gid AS uuid), :ps, :pe, :reg, :ot, :days, :cnt)
                ON CONFLICT (guard_user_id, period_start, period_end) DO UPDATE
                    SET regular_hours = EXCLUDED.regular_hours,
                        overtime_hours = EXCLUDED.overtime_hours,
                        days_worked = EXCLUDED.days_worked,
                        shift_count = EXCLUDED.shift_count,
                        updated_at = now()
                RETURNING id, guard_user_id, period_start, period_end,
                          regular_hours, overtime_hours, days_worked, shift_count, status
            """),
            {"gid": str(g["id"]), "ps": body.period_start, "pe": body.period_end,
             "reg": regular, "ot": ot, "days": h.days_worked, "cnt": h.shift_count},
        )).first()

        if existing:
            refreshed += 1
        else:
            created += 1
        rows.append({**dict(row._mapping),
                     "full_name": g["full_name"] or g["email"],
                     "employee_code": g["employee_code"]})

    await db.commit()
    return {
        "created": created,
        "refreshed": refreshed,
        "left_alone_already_reviewed": locked,
        "timesheets": rows,
    }


@router.get("/timesheets", dependencies=[Depends(require_permission("timesheet:read"))])
async def list_timesheets(
    db: AsyncSession = Depends(get_db_with_tenant),
    period_start: date | None = None,
    period_end: date | None = None,
    status_filter: str | None = None,
):
    where, params = [], {}
    if period_start:
        where.append("t.period_start >= :ps"); params["ps"] = period_start
    if period_end:
        where.append("t.period_end <= :pe"); params["pe"] = period_end
    if status_filter:
        if status_filter not in ("draft", "submitted", "approved", "rejected"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown status")
        where.append("t.status = :st"); params["st"] = status_filter
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(
        text(f"""
            SELECT t.id, t.guard_user_id, t.period_start, t.period_end,
                   t.regular_hours, t.overtime_hours, t.days_worked, t.shift_count,
                   t.status, t.submitted_at, t.reviewed_at, t.review_notes,
                   u.full_name, u.employee_code,
                   reviewer.full_name AS reviewed_by_name
              FROM timesheets t
              JOIN users u ON u.id = t.guard_user_id
         LEFT JOIN users reviewer ON reviewer.id = t.reviewed_by_user_id
            {clause}
          ORDER BY t.period_start DESC, u.full_name
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


async def _move_timesheet(db, timesheet_id, new_status, user_id, notes, allowed_from):
    row = (await db.execute(
        text("SELECT status FROM timesheets WHERE id = CAST(:id AS uuid)"),
        {"id": timesheet_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Timesheet not found")
    if row.status not in allowed_from:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A {row.status} timesheet cannot be {new_status}",
        )

    stamp = ("submitted_by_user_id = CAST(:uid AS uuid), submitted_at = now()"
             if new_status == "submitted"
             else "reviewed_by_user_id = CAST(:uid AS uuid), reviewed_at = now()")
    result = await db.execute(
        text(f"""
            UPDATE timesheets
               SET status = :st, {stamp},
                   review_notes = COALESCE(:notes, review_notes), updated_at = now()
             WHERE id = CAST(:id AS uuid)
            RETURNING id, guard_user_id, period_start, period_end, status,
                      regular_hours, overtime_hours, days_worked
        """),
        {"st": new_status, "uid": user_id, "notes": notes, "id": timesheet_id},
    )
    await db.commit()
    return dict(result.first()._mapping)


@router.post("/timesheets/{timesheet_id}/submit",
             dependencies=[Depends(require_permission("timesheet:manage"))])
async def submit_timesheet(
    timesheet_id: str,
    body: TimesheetReview,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    return await _move_timesheet(db, timesheet_id, "submitted", token.user_id,
                                 body.review_notes, ("draft", "rejected"))


@router.post("/timesheets/{timesheet_id}/approve",
             dependencies=[Depends(require_permission("timesheet:manage"))])
async def approve_timesheet(
    timesheet_id: str,
    body: TimesheetReview,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Approving a draft directly is allowed. A small agency where the person
    checking the hours is the person signing them off should not have to click
    submit and then approve to satisfy a workflow nobody else participates in.
    """
    return await _move_timesheet(db, timesheet_id, "approved", token.user_id,
                                 body.review_notes, ("draft", "submitted"))


@router.post("/timesheets/{timesheet_id}/reject",
             dependencies=[Depends(require_permission("timesheet:manage"))])
async def reject_timesheet(
    timesheet_id: str,
    body: TimesheetReview,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if not (body.review_notes or "").strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A reason is required when rejecting — somebody has to act on this",
        )
    return await _move_timesheet(db, timesheet_id, "rejected", token.user_id,
                                 body.review_notes, ("draft", "submitted"))


@router.get("/overtime-projection",
            dependencies=[Depends(require_permission("payroll:read"))])
async def overtime_projection_report(
    month: str | None = Query(
        None, description="Month to project, as YYYY-MM. Defaults to the "
                          "current one, which is the only one still fixable."),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Which guards the roster is about to put past the statutory overtime cap.

    THE SIBLING OF pwm-compliance, AND FOR THE SAME REASON. That report exists
    because blocking a below-floor wage on save protects only wages set from now
    on. This one exists because MAX_OT_HOURS_PER_MONTH is checked when payroll
    totals a month -- by which point the guard has worked the hours and the
    agency is already in breach. Nothing looked at the roster ahead of it.

    Accrued and projected are returned separately and never summed away: one is
    what payroll will bill from completed shifts, the other is an estimate of
    shifts nobody has worked yet. An operator deciding whether to move a shift
    needs to know which half is which.
    """
    from calendar import monthrange

    from app.services import overtime_projection

    if month:
        try:
            year, mon = (int(x) for x in month.split("-", 1))
            first = date(year, mon, 1)
        except (ValueError, TypeError):
            raise HTTPException(422, "month must look like 2026-09")
    else:
        first = date.today().replace(day=1)
    last = date(first.year, first.month, monthrange(first.year, first.month)[1])

    rows = await overtime_projection.project_month(db, month_start=first,
                                                   month_end=last)
    actionable = [r for r in rows
                  if r.status in (overtime_projection.OVER_CAP,
                                  overtime_projection.APPROACHING)]
    return {
        "month": f"{first:%Y-%m}",
        "cap_hours": float(overtime_projection.MAX_OT_HOURS_PER_MONTH),
        "normal_hours_per_day": float(overtime_projection.HOURS_PER_DAY),
        "over_cap": sum(1 for r in rows
                        if r.status == overtime_projection.OVER_CAP),
        "approaching": sum(1 for r in rows
                           if r.status == overtime_projection.APPROACHING),
        "guards_assessed": len(rows),
        "guards": [r.as_dict() for r in actionable],
    }
