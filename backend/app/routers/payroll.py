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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import InvalidTokenError, decode_access_token
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.payroll import compute_age, compute_payslip

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
    run_id = run_row.id

    guards = await db.execute(
        text("""
            SELECT id, full_name, date_of_birth, work_pass_type, hourly_rate, daily_rate, monthly_salary
            FROM users WHERE role_id = ANY(:roles) AND is_active = TRUE
        """),
        {"roles": list(_PAYROLL_GUARD_ROLES)},
    )

    payslips = []
    warnings = []
    for g in guards:
        if g.hourly_rate is None and g.monthly_salary is None and g.daily_rate is None:
            warnings.append(f"{g.full_name or g.id} has no hourly_rate, daily_rate, or monthly_salary configured — skipped")
            continue

        hours_row = (await db.execute(
            text("""
                SELECT
                    COALESCE(SUM(EXTRACT(EPOCH FROM (actual_end - actual_start)) / 3600.0), 0) AS total_hours,
                    COALESCE(SUM(overtime_minutes), 0) / 60.0 AS ot_hours,
                    COUNT(DISTINCT actual_start::date) AS days_worked
                FROM shifts
                WHERE guard_user_id = :gid AND status = 'completed' AND actual_end IS NOT NULL
                  AND actual_start::date >= :ps AND actual_start::date <= :pe
            """),
            {"gid": g.id, "ps": body.period_start, "pe": body.period_end},
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
            {"gid": g.id, "ps": body.period_start, "pe": body.period_end},
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
            {"gid": g.id, "ps": body.period_start, "pe": body.period_end},
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
            {"gid": g.id, "ps": body.period_start, "pe": body.period_end},
        )).first()
        allowance_pay = Decimal(str(allowance_row.total or 0))

        # The human check between a scan and a payslip.
        timesheet = (await db.execute(
            text("""
                SELECT id, status FROM timesheets
                 WHERE guard_user_id = :gid AND period_start = :ps AND period_end = :pe
            """),
            {"gid": g.id, "ps": body.period_start, "pe": body.period_end},
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

        age = compute_age(g.date_of_birth, body.period_end) if g.date_of_birth else 30
        calc = compute_payslip(
            regular_hours, ot_hours, days_worked,
            Decimal(str(g.hourly_rate)) if g.hourly_rate is not None else None,
            Decimal(str(g.daily_rate)) if g.daily_rate is not None else None,
            Decimal(str(g.monthly_salary)) if g.monthly_salary is not None else None,
            age, g.work_pass_type,
            public_holiday_days=public_holiday_days,
            allowance_pay=allowance_pay,
        )

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
                    public_holiday_days, public_holiday_pay, allowance_pay, timesheet_id
                ) VALUES (
                    current_setting('app.current_tenant')::uuid, :run_id, :gid, :reg, :ot, :days,
                    :base, :otpay, :gross, :cpfe, :cpfr, :net, :unpaid,
                    :phdays, :phpay, :allow, CAST(:ts AS uuid)
                )
                RETURNING id, guard_user_id, regular_hours, overtime_hours, days_worked, base_pay, overtime_pay,
                          gross_pay, cpf_employee, cpf_employer, net_pay, unpaid_leave_days,
                          public_holiday_days, public_holiday_pay, allowance_pay
            """),
            {
                "run_id": run_id, "gid": g.id, "reg": regular_hours, "ot": ot_hours, "days": days_worked,
                "base": calc["base_pay"], "otpay": calc["overtime_pay"], "gross": calc["gross_pay"],
                "cpfe": calc["cpf_employee"], "cpfr": calc["cpf_employer"], "net": calc["net_pay"],
                "unpaid": unpaid_leave_days,
                "phdays": public_holiday_days, "phpay": calc["public_holiday_pay"],
                "allow": calc["allowance_pay"],
                "ts": str(timesheet.id) if timesheet else None,
            },
        )).first()
        payslips.append(dict(row._mapping))

    await db.commit()
    return {**dict(run_row._mapping), "payslips": payslips, "warnings": warnings}


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
        text("SELECT id, period_start, period_end, status, generated_at, finalized_at FROM payroll_runs WHERE id = CAST(:id AS uuid)"),
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
                   p.unpaid_leave_days, u.full_name AS guard_name
            FROM payslips p JOIN users u ON u.id = p.guard_user_id
            {where}
            ORDER BY u.full_name
        """),
        params,
    )
    return {**dict(run_row._mapping), "payslips": [dict(r._mapping) for r in payslips]}


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
    result = await db.execute(
        text("""
            SELECT u.id AS guard_user_id, u.full_name, u.nric_fin,
                   SUM(p.gross_pay) AS annual_gross, SUM(p.cpf_employer) AS annual_employer_cpf
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


_TIMESHEET_HOURS_SQL = """
    SELECT COALESCE(SUM(EXTRACT(EPOCH FROM (actual_end - actual_start)) / 3600.0), 0) AS total_hours,
           COALESCE(SUM(overtime_minutes), 0) / 60.0 AS ot_hours,
           COUNT(DISTINCT actual_start::date) AS days_worked,
           COUNT(*) AS shift_count
      FROM shifts
     WHERE guard_user_id = :gid AND status = 'completed' AND actual_end IS NOT NULL
       AND actual_start::date >= :ps AND actual_start::date <= :pe
"""


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
