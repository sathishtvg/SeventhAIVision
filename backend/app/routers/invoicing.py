"""Client Billing / Invoicing (ShiftSecure final phase).

Distinct from billing.py's Stripe SaaS subscription billing (the tenant
paying for Seventh AI Vision) — this is the tenant invoicing their own
clients for guard services delivered at a billing_client's sites. Hours
computation mirrors payroll.py's shift-hours query exactly, grouped by
site instead of by guard.
"""
import io
from datetime import date, datetime, timedelta, timezone
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
from app.dependencies.sites import client_scope_clause, get_allowed_client_ids, is_client_allowed
from app.dependencies.tenant import get_db_with_tenant
from app.services.payroll import OT_MULTIPLIER

_CLIENT_ROLE = 7

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

router = APIRouter(prefix="/api/v1/invoicing", tags=["guard-ops"])

_DEFAULT_TAX_RATE = Decimal("0.09")


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
    return styles, title_style


# ── Billing clients ──────────────────────────────────────────────────────

class BillingClientCreate(BaseModel):
    name: str
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    billing_address: str | None = None


class BillingClientUpdate(BaseModel):
    name: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    billing_address: str | None = None
    is_active: bool | None = None


@router.get("/clients", dependencies=[Depends(require_permission("invoicing:read"))])
async def list_clients(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_clients: list[str] | None = Depends(get_allowed_client_ids),
):
    params: dict = {}
    scope = client_scope_clause(allowed_clients, "id", params)
    where = f"WHERE {scope}" if scope else ""
    result = await db.execute(
        text(f"""
            SELECT id, name, contact_name, contact_email, contact_phone, billing_address,
                   is_active, created_at, updated_at
            FROM billing_clients {where} ORDER BY name
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


@router.post("/clients", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("invoicing:manage"))])
async def create_client(body: BillingClientCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(
        text("""
            INSERT INTO billing_clients (tenant_id, name, contact_name, contact_email, contact_phone, billing_address)
            VALUES (current_setting('app.current_tenant')::uuid, :name, :cname, :cemail, :cphone, :addr)
            RETURNING id
        """),
        {
            "name": body.name, "cname": body.contact_name, "cemail": body.contact_email,
            "cphone": body.contact_phone, "addr": body.billing_address,
        },
    )).first()
    await db.commit()
    return {"id": row.id, "name": body.name}


@router.put("/clients/{client_id}", dependencies=[Depends(require_permission("invoicing:manage"))])
async def update_client(client_id: str, body: BillingClientUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    sets, params = [], {"id": client_id}
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.contact_name is not None:
        sets.append("contact_name = :cname"); params["cname"] = body.contact_name
    if body.contact_email is not None:
        sets.append("contact_email = :cemail"); params["cemail"] = body.contact_email
    if body.contact_phone is not None:
        sets.append("contact_phone = :cphone"); params["cphone"] = body.contact_phone
    if body.billing_address is not None:
        sets.append("billing_address = :addr"); params["addr"] = body.billing_address
    if body.is_active is not None:
        sets.append("is_active = :is_active"); params["is_active"] = body.is_active

    if not sets:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")

    sets.append("updated_at = now()")
    row = (await db.execute(
        text(f"UPDATE billing_clients SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) RETURNING id"),
        params,
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Billing client not found")
    await db.commit()
    return {"id": client_id}


@router.delete("/clients/{client_id}", dependencies=[Depends(require_permission("invoicing:manage"))])
async def deactivate_client(client_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(
        text("UPDATE billing_clients SET is_active = FALSE, updated_at = now() WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": client_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Billing client not found")
    await db.commit()
    return {"id": client_id, "is_active": False}


# ── Invoices ──────────────────────────────────────────────────────────────

class InvoiceCreate(BaseModel):
    client_id: str
    period_start: date
    period_end: date
    tax_rate: float | None = None
    due_date: date | None = None


@router.post("/invoices", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("invoicing:manage"))])
async def create_invoice(
    body: InvoiceCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if body.period_end < body.period_start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "period_end must not be before period_start")

    tax_rate = Decimal(str(body.tax_rate)) if body.tax_rate is not None else _DEFAULT_TAX_RATE

    sites = await db.execute(
        text("SELECT id, name, bill_rate FROM sites WHERE client_id = CAST(:cid AS uuid) AND is_active = TRUE"),
        {"cid": body.client_id},
    )
    sites = sites.fetchall()

    line_items = []
    warnings = []
    subtotal = Decimal("0")
    for s in sites:
        if s.bill_rate is None:
            warnings.append(f"{s.name} has no bill_rate configured — skipped")
            continue

        hours_row = (await db.execute(
            text("""
                SELECT
                    COALESCE(SUM(EXTRACT(EPOCH FROM (actual_end - actual_start)) / 3600.0), 0) AS total_hours,
                    COALESCE(SUM(overtime_minutes), 0) / 60.0 AS ot_hours
                FROM shifts
                WHERE site_id = :sid AND status = 'completed'
                  AND actual_start::date >= :ps AND actual_start::date <= :pe
            """),
            {"sid": s.id, "ps": body.period_start, "pe": body.period_end},
        )).first()
        total_hours = Decimal(str(hours_row.total_hours or 0))
        ot_hours = Decimal(str(hours_row.ot_hours or 0))
        regular_hours = max(total_hours - ot_hours, Decimal("0"))
        bill_rate = Decimal(str(s.bill_rate))

        regular_amount = (regular_hours * bill_rate).quantize(Decimal("0.01"))
        overtime_amount = (ot_hours * bill_rate * OT_MULTIPLIER).quantize(Decimal("0.01"))
        line_total = regular_amount + overtime_amount
        subtotal += line_total

        line_items.append({
            "site_id": s.id, "site_name": s.name, "regular_hours": regular_hours,
            "overtime_hours": ot_hours, "bill_rate": bill_rate,
            "regular_amount": regular_amount, "overtime_amount": overtime_amount, "line_total": line_total,
        })

    tax_amount = (subtotal * tax_rate).quantize(Decimal("0.01"))
    total_amount = subtotal + tax_amount

    invoice_row = (await db.execute(
        text("""
            INSERT INTO invoices (
                tenant_id, client_id, period_start, period_end, subtotal, tax_rate,
                tax_amount, total_amount, due_date, generated_by_user_id
            ) VALUES (
                current_setting('app.current_tenant')::uuid, CAST(:cid AS uuid), :ps, :pe, :subtotal, :tax_rate,
                :tax_amount, :total_amount, :due_date, CAST(:uid AS uuid)
            )
            RETURNING id, client_id, invoice_number, period_start, period_end, status,
                      subtotal, tax_rate, tax_amount, total_amount, due_date, generated_at
        """),
        {
            "cid": body.client_id, "ps": body.period_start, "pe": body.period_end,
            "subtotal": subtotal, "tax_rate": tax_rate, "tax_amount": tax_amount,
            "total_amount": total_amount, "due_date": body.due_date, "uid": token.user_id,
        },
    )).first()
    invoice_id = invoice_row.id

    created_items = []
    for li in line_items:
        row = (await db.execute(
            text("""
                INSERT INTO invoice_line_items (
                    tenant_id, invoice_id, site_id, site_name, regular_hours, overtime_hours,
                    bill_rate, regular_amount, overtime_amount, line_total
                ) VALUES (
                    current_setting('app.current_tenant')::uuid, :inv_id, :site_id, :site_name, :reg, :ot,
                    :rate, :reg_amt, :ot_amt, :total
                )
                RETURNING id, site_id, site_name, regular_hours, overtime_hours, bill_rate,
                          regular_amount, overtime_amount, line_total
            """),
            {
                "inv_id": invoice_id, "site_id": li["site_id"], "site_name": li["site_name"],
                "reg": li["regular_hours"], "ot": li["overtime_hours"], "rate": li["bill_rate"],
                "reg_amt": li["regular_amount"], "ot_amt": li["overtime_amount"], "total": li["line_total"],
            },
        )).first()
        created_items.append(dict(row._mapping))

    await db.commit()
    return {**dict(invoice_row._mapping), "line_items": created_items, "warnings": warnings}


@router.get("/invoices", dependencies=[Depends(require_permission("invoicing:read"))])
async def list_invoices(
    client_id: str | None = None,
    invoice_status: str | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_clients: list[str] | None = Depends(get_allowed_client_ids),
):
    where, params = [], {}
    if client_id:
        where.append("i.client_id = CAST(:cid AS uuid)"); params["cid"] = client_id
    if invoice_status:
        where.append("i.status = :status"); params["status"] = invoice_status
    scope = client_scope_clause(allowed_clients, "i.client_id", params)
    if scope:
        where.append(scope)
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(
        text(f"""
            SELECT i.id, i.client_id, c.name AS client_name, i.invoice_number, i.period_start, i.period_end,
                   i.status, i.subtotal, i.tax_rate, i.tax_amount, i.total_amount, i.due_date,
                   i.generated_at, i.finalized_at, i.paid_at, i.voided_at
            FROM invoices i JOIN billing_clients c ON c.id = i.client_id
            {where_clause}
            ORDER BY i.generated_at DESC
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


@router.get("/invoices/{invoice_id}", dependencies=[Depends(require_permission("invoicing:read"))])
async def get_invoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_clients: list[str] | None = Depends(get_allowed_client_ids),
):
    invoice_row = (await db.execute(
        text("""
            SELECT i.id, i.client_id, c.name AS client_name, c.contact_name, c.contact_email,
                   c.billing_address, i.invoice_number, i.period_start, i.period_end, i.status,
                   i.subtotal, i.tax_rate, i.tax_amount, i.total_amount, i.due_date,
                   i.generated_at, i.finalized_at, i.paid_at, i.voided_at
            FROM invoices i JOIN billing_clients c ON c.id = i.client_id
            WHERE i.id = CAST(:id AS uuid)
        """),
        {"id": invoice_id},
    )).first()
    if invoice_row is None or not is_client_allowed(allowed_clients, invoice_row.client_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")

    items = await db.execute(
        text("""
            SELECT id, site_id, site_name, regular_hours, overtime_hours, bill_rate,
                   regular_amount, overtime_amount, line_total
            FROM invoice_line_items WHERE invoice_id = CAST(:id AS uuid) ORDER BY site_name
        """),
        {"id": invoice_id},
    )
    return {**dict(invoice_row._mapping), "line_items": [dict(r._mapping) for r in items]}


@router.put("/invoices/{invoice_id}/finalize", dependencies=[Depends(require_permission("invoicing:manage"))])
async def finalize_invoice(invoice_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    inv = (await db.execute(
        text("SELECT id, due_date, period_start FROM invoices WHERE id = CAST(:id AS uuid) AND status = 'draft'"),
        {"id": invoice_id},
    )).first()
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found or not in draft status")

    year = datetime.now(timezone.utc).year
    seq_row = (await db.execute(
        text("""
            SELECT COUNT(*) AS n FROM invoices
            WHERE tenant_id = current_setting('app.current_tenant')::uuid
              AND status != 'draft' AND EXTRACT(YEAR FROM finalized_at) = :yr
        """),
        {"yr": year},
    )).first()
    seq = (seq_row.n or 0) + 1
    invoice_number = f"INV-{year}-{seq:04d}"
    due_date = inv.due_date or (date.today() + timedelta(days=30))

    row = (await db.execute(
        text("""
            UPDATE invoices
            SET status = 'finalized', invoice_number = :num, due_date = :due, finalized_at = now()
            WHERE id = CAST(:id AS uuid)
            RETURNING id, status, invoice_number, due_date
        """),
        {"num": invoice_number, "due": due_date, "id": invoice_id},
    )).first()
    await db.commit()
    return dict(row._mapping)


@router.put("/invoices/{invoice_id}/mark-paid", dependencies=[Depends(require_permission("invoicing:manage"))])
async def mark_invoice_paid(invoice_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(
        text("""
            UPDATE invoices SET status = 'paid', paid_at = now()
            WHERE id = CAST(:id AS uuid) AND status = 'finalized'
            RETURNING id, status
        """),
        {"id": invoice_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found or not finalized")
    await db.commit()
    return dict(row._mapping)


@router.put("/invoices/{invoice_id}/void", dependencies=[Depends(require_permission("invoicing:manage"))])
async def void_invoice(invoice_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    existing = (await db.execute(
        text("SELECT status FROM invoices WHERE id = CAST(:id AS uuid)"), {"id": invoice_id},
    )).first()
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    if existing.status == "paid":
        raise HTTPException(status.HTTP_409_CONFLICT, "A paid invoice cannot be voided")
    if existing.status != "finalized":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a finalized invoice can be voided")

    row = (await db.execute(
        text("""
            UPDATE invoices SET status = 'void', voided_at = now()
            WHERE id = CAST(:id AS uuid)
            RETURNING id, status
        """),
        {"id": invoice_id},
    )).first()
    await db.commit()
    return dict(row._mapping)


@router.delete("/invoices/{invoice_id}", dependencies=[Depends(require_permission("invoicing:manage"))])
async def delete_invoice(invoice_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    existing = (await db.execute(
        text("SELECT status FROM invoices WHERE id = CAST(:id AS uuid)"), {"id": invoice_id},
    )).first()
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    if existing.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft invoice can be deleted")

    await db.execute(text("DELETE FROM invoices WHERE id = CAST(:id AS uuid)"), {"id": invoice_id})
    await db.commit()
    return {"id": invoice_id, "deleted": True}


@router.get("/invoices/{invoice_id}/pdf")
async def get_invoice_pdf(invoice_id: str, token: str = Query(..., description="JWT access token")):
    """Query-param-JWT auth from the start — a plain download link can't
    carry an Authorization header (same pattern as shifts.py's check-in
    photo endpoint and payroll.py's payslip PDF endpoint)."""
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
                "WHERE rp.role_id = :role_id AND p.code = 'invoicing:read'"
            ),
            {"role_id": role_id},
        )
        if perm.first() is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: invoicing:read")

        invoice_row = (await session.execute(
            text("""
                SELECT i.*, c.name AS client_name, c.contact_name, c.contact_email, c.billing_address,
                       t.name AS tenant_name
                FROM invoices i
                JOIN billing_clients c ON c.id = i.client_id
                JOIN tenants t ON t.id = i.tenant_id
                WHERE i.id = CAST(:id AS uuid)
            """),
            {"id": invoice_id},
        )).first()
        if invoice_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")

        if role_id == _CLIENT_ROLE:
            user_id = payload["sub"]
            allowed = await session.execute(
                text(
                    "SELECT DISTINCT s.client_id FROM user_sites us "
                    "JOIN sites s ON s.id = us.site_id "
                    "WHERE us.user_id = CAST(:uid AS uuid) AND s.client_id IS NOT NULL"
                ),
                {"uid": user_id},
            )
            allowed_client_ids = {str(r.client_id) for r in allowed}
            if str(invoice_row.client_id) not in allowed_client_ids:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only view your own invoices")

        items = await session.execute(
            text("""
                SELECT site_name, regular_hours, overtime_hours, bill_rate,
                       regular_amount, overtime_amount, line_total
                FROM invoice_line_items WHERE invoice_id = CAST(:id AS uuid) ORDER BY site_name
            """),
            {"id": invoice_id},
        )
        items = items.fetchall()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                             topMargin=15 * mm, bottomMargin=15 * mm)
    styles, title_style = _make_styles()
    elements = [
        Paragraph(invoice_row.tenant_name or "7th AI Vision", title_style),
        Paragraph("Invoice", styles["Heading1"]),
        Paragraph(f"Invoice No: {invoice_row.invoice_number or 'DRAFT'}", styles["Normal"]),
        Paragraph(f"Bill To: {invoice_row.client_name}", styles["Normal"]),
        Paragraph(f"Period: {invoice_row.period_start} to {invoice_row.period_end}", styles["Normal"]),
        Paragraph(f"Due Date: {invoice_row.due_date or '—'}", styles["Normal"]),
        Paragraph(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", styles["Normal"]),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#6C63FF")),
        Spacer(1, 4 * mm),
    ]

    data = [["Site", "Regular Hrs", "OT Hrs", "Rate", "Regular Amt", "OT Amt", "Line Total"]]
    for it in items:
        data.append([
            it.site_name, f"{it.regular_hours:.2f}", f"{it.overtime_hours:.2f}", f"${it.bill_rate:.2f}",
            f"${it.regular_amount:.2f}", f"${it.overtime_amount:.2f}", f"${it.line_total:.2f}",
        ])
    tbl = Table(data, colWidths=[35 * mm, 20 * mm, 18 * mm, 18 * mm, 25 * mm, 22 * mm, 25 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6C63FF")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5FF")]),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 4 * mm))

    summary = [
        ["Subtotal", f"${invoice_row.subtotal:.2f}"],
        [f"Tax ({float(invoice_row.tax_rate) * 100:.1f}%)", f"${invoice_row.tax_amount:.2f}"],
        ["Total", f"${invoice_row.total_amount:.2f}"],
    ]
    summary_tbl = Table(summary, colWidths=[100 * mm, 40 * mm])
    summary_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#F5F5FF")),
    ]))
    elements.append(summary_tbl)
    doc.build(elements)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="invoice_{invoice_row.invoice_number or invoice_id}.pdf"'},
    )
