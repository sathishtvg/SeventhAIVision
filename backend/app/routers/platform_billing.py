"""Plans, modules, invoices and payments — the vendor's side of the ledger.

Billing moved to Super Admin in migration 0103, because the Stripe tables are
keyed by tenant_id: they describe what each CUSTOMER owes, which is the
vendor's business and not the customer's.

WHAT THIS IS NOT. /api/v1/billing is the Stripe integration — checkout,
portal, webhooks, and a mirror of what Stripe issued. This is the ledger the
vendor writes: an invoice for a customer paying by bank transfer, which in this
market is most of them, and Stripe has no row for.

NO RLS ANYWHERE, deliberately. These tables span tenants by nature, so
require_permission is the only thing standing between them and every customer's
commercial terms — which is why it is on every route rather than on the router.

AN ISSUED INVOICE CANNOT BE EDITED, and that is enforced by a trigger rather
than here. The API refuses first for a readable error; the database refuses
regardless, because "the endpoint checks" is a promise every future endpoint
has to keep and one of them will not.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_raw_db
from app.services import pricing

router = APIRouter(prefix="/api/v1/platform", tags=["platform-billing"])

_READ = Depends(require_permission("billing:read"))
_MANAGE = Depends(require_permission("billing:manage"))

#: How long a customer has to pay, when nothing else is said.
DEFAULT_PAYMENT_TERMS_DAYS = 30


# ── Catalogue (§12) ──────────────────────────────────────────────────────────

@router.get("/modules", dependencies=[_READ])
async def list_modules(db: AsyncSession = Depends(get_raw_db)):
    """The price list. What each module is, and how it is charged."""
    rows = (await db.execute(text(
        "SELECT code, name, description, billing_type, unit_price, is_active, "
        "       sort_order "
        "  FROM billing_modules ORDER BY sort_order, name"
    ))).mappings().all()
    return [dict(r) for r in rows]


class ModuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    billing_type: str | None = Field(
        default=None, pattern="^(included|flat|per_camera|per_site|per_user)$")
    unit_price: Decimal | None = Field(default=None, ge=0)
    is_active: bool | None = None


@router.put("/modules/{code}", dependencies=[_MANAGE])
async def update_module(
    code: str, body: ModuleUpdate, db: AsyncSession = Depends(get_raw_db),
):
    """Change a module's list price or how it is sold.

    Changes the price for every plan that has not deliberately negotiated
    something else — plan and tenant overrides are NULL by default precisely so
    a list-price change moves them.
    """
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items()
               if v is not None}
    if not updates:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["code"] = code
    row = (await db.execute(
        text(f"UPDATE billing_modules SET {clause}, updated_at = now() "
             f" WHERE code = :code RETURNING *"),
        updates,
    )).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Module not found")
    await db.commit()
    return dict(row)


# ── Plans (§11) ──────────────────────────────────────────────────────────────

@router.get("/plans", dependencies=[_READ])
async def list_plans(db: AsyncSession = Depends(get_raw_db)):
    rows = (await db.execute(text("""
        SELECT p.*,
               (SELECT count(*) FROM billing_subscriptions s
                 WHERE s.plan_id = p.id AND s.status IN ('active', 'trialing'))
                   AS subscribers
          FROM billing_plans p
      ORDER BY p.price_monthly NULLS FIRST, p.name
    """))).mappings().all()
    return [dict(r) for r in rows]


class PlanUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    billing_cycle: str = Field(default="monthly",
                               pattern="^(monthly|quarterly|half_yearly|yearly|custom)$")
    price_monthly: Decimal | None = Field(default=None, ge=0)
    price_yearly: Decimal | None = Field(default=None, ge=0)
    included_cameras: int = Field(default=0, ge=0)
    included_sites: int = Field(default=0, ge=0)
    included_users: int = Field(default=0, ge=0)
    included_storage_gb: int = Field(default=0, ge=0)
    price_per_camera: Decimal = Field(default=Decimal("0"), ge=0)
    price_per_site: Decimal = Field(default=Decimal("0"), ge=0)
    price_per_user: Decimal = Field(default=Decimal("0"), ge=0)
    price_per_gb: Decimal = Field(default=Decimal("0"), ge=0)
    max_cameras: int | None = Field(default=None, ge=0)
    max_sites: int | None = Field(default=None, ge=0)
    max_users: int | None = Field(default=None, ge=0)
    support_level: str = "standard"
    is_active: bool = True


_PLAN_COLUMNS = (
    "name", "description", "billing_cycle", "price_monthly", "price_yearly",
    "included_cameras", "included_sites", "included_users", "included_storage_gb",
    "price_per_camera", "price_per_site", "price_per_user", "price_per_gb",
    "max_cameras", "max_sites", "max_users", "support_level", "is_active",
)


@router.post("/plans", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGE])
async def create_plan(body: PlanUpsert, db: AsyncSession = Depends(get_raw_db)):
    data = body.model_dump()
    data["id"] = str(uuid.uuid4())
    cols = ", ".join(_PLAN_COLUMNS)
    binds = ", ".join(f":{c}" for c in _PLAN_COLUMNS)
    row = (await db.execute(
        text(f"INSERT INTO billing_plans (id, {cols}) "
             f"VALUES (CAST(:id AS uuid), {binds}) RETURNING *"),
        data,
    )).mappings().first()
    await db.commit()
    return dict(row)


@router.put("/plans/{plan_id}", dependencies=[_MANAGE])
async def update_plan(
    plan_id: str, body: PlanUpsert, db: AsyncSession = Depends(get_raw_db),
):
    """Changing a plan does not re-bill anybody.

    Invoices already issued are frozen, and the next one is priced from the
    plan as it stands then. A price change that silently rewrote history would
    be the worst possible behaviour here.
    """
    data = body.model_dump()
    data["id"] = plan_id
    clause = ", ".join(f"{c} = :{c}" for c in _PLAN_COLUMNS)
    row = (await db.execute(
        text(f"UPDATE billing_plans SET {clause} WHERE id = CAST(:id AS uuid) RETURNING *"),
        data,
    )).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    await db.commit()
    return dict(row)


# ── Pricing (§13) ────────────────────────────────────────────────────────────

async def _plan_for(db: AsyncSession, tenant_id: str) -> pricing.Plan:
    """The plan a tenant is on, or an empty one if they are not subscribed.

    An unsubscribed customer prices at zero rather than erroring: previewing a
    bill for somebody who has not been put on a plan yet is a reasonable thing
    to do, and answering "nothing, they have no plan" is more useful than a
    404 that says nothing about why.
    """
    # Through the SECURITY DEFINER function, not the tables. Both
    # billing_subscriptions and billing_plans are reached from a session with
    # no tenant scope, and billing_subscriptions has RLS — read directly it
    # returns nothing, prices every customer at "No plan", and raises invoices
    # for $0.00 that look like a customer who owes nothing.
    row = (await db.execute(
        text("SELECT * FROM platform_tenant_plan(CAST(:tid AS uuid))"),
        {"tid": tenant_id},
    )).mappings().first()
    if row is None:
        return pricing.Plan(name="No plan")
    return pricing.Plan(
        name=row["plan_name"],
        base_price=Decimal(str(row["base_price"] or 0)),
        included_cameras=row["included_cameras"] or 0,
        included_sites=row["included_sites"] or 0,
        included_users=row["included_users"] or 0,
        included_storage_gb=row["included_storage_gb"] or 0,
        price_per_camera=Decimal(str(row["price_per_camera"] or 0)),
        price_per_site=Decimal(str(row["price_per_site"] or 0)),
        price_per_user=Decimal(str(row["price_per_user"] or 0)),
        price_per_gb=Decimal(str(row["price_per_gb"] or 0)),
    )


async def _modules_for(db: AsyncSession, tenant_id: str) -> list[pricing.Module]:
    """What the customer has switched on, priced.

    Joined from tenant_module_licenses to the catalogue, so a module that is
    licensed but not sellable simply does not appear — and the tenant's own
    price_override wins where one has been negotiated.
    """
    # Same reason as _plan_for: tenant_module_licenses is RLS-protected, and
    # read directly from an unscoped session it reports that the customer has
    # no modules rather than that the query could not see them.
    rows = (await db.execute(
        text("SELECT * FROM platform_tenant_billable_modules(CAST(:tid AS uuid))"),
        {"tid": tenant_id},
    )).mappings().all()
    return [
        pricing.Module(
            code=r["code"], name=r["name"], billing_type=r["billing_type"],
            unit_price=Decimal(str(r["unit_price"] or 0)),
        )
        for r in rows
    ]


async def _usage_for(db: AsyncSession, tenant_id: str) -> pricing.Usage:
    """What the customer actually runs.

    Through the SECURITY DEFINER function, because users, sites and cameras are
    all RLS'd and this router has no tenant scope — counting them directly
    would return zero and bill everybody for nothing.
    """
    row = (await db.execute(
        text("SELECT * FROM platform_tenant_counts(CAST(:tid AS uuid))"),
        {"tid": tenant_id},
    )).mappings().first()
    return pricing.Usage(
        cameras=int(row["cameras"] or 0),
        sites=int(row["sites"] or 0),
        users=int(row["users"] or 0),
        # Per-tenant storage is not measured yet, so it is zero rather than
        # guessed. Billing for a number nobody computed is worse than not
        # billing for it.
        storage_gb=Decimal("0"),
    )


class QuoteRequest(BaseModel):
    tenant_id: str
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)


@router.post("/pricing/preview", dependencies=[_READ])
async def preview_price(body: QuoteRequest, db: AsyncSession = Depends(get_raw_db)):
    """What this customer would be billed today, itemised.

    Read-only on purpose. Seeing the bill before raising it is how a mistake
    gets caught while it is still free to fix.
    """
    plan = await _plan_for(db, body.tenant_id)
    usage = await _usage_for(db, body.tenant_id)
    modules = await _modules_for(db, body.tenant_id)
    q = pricing.quote(plan, usage, modules,
                      discount_amount=body.discount_amount, tax_rate=body.tax_rate)
    return {
        "plan": plan.name,
        "usage": {"cameras": usage.cameras, "sites": usage.sites,
                  "users": usage.users},
        "lines": [vars(line) for line in q.lines],
        "subtotal": q.subtotal, "discount_amount": q.discount_amount,
        "tax_rate": q.tax_rate, "tax_amount": q.tax_amount,
        "total_amount": q.total_amount,
    }


# ── Invoices (§14) ───────────────────────────────────────────────────────────

async def _next_invoice_number(db: AsyncSession) -> str:
    """INV-YYYYMM-0001, sequential within the month.

    A customer quotes this back when they telephone, so it has to be short,
    orderable and unique — the unique index is the real guarantee; this only
    has to be a good guess, and the caller retries once if it loses a race.
    """
    prefix = f"INV-{datetime.now(timezone.utc):%Y%m}-"
    last = (await db.execute(
        text("SELECT invoice_number FROM platform_invoices "
             " WHERE invoice_number LIKE :p ORDER BY invoice_number DESC LIMIT 1"),
        {"p": f"{prefix}%"},
    )).scalar()
    nxt = (int(last.rsplit("-", 1)[1]) + 1) if last else 1
    return f"{prefix}{nxt:04d}"


class InvoiceCreate(BaseModel):
    tenant_id: str
    period_start: date | None = None
    period_end: date | None = None
    due_date: date | None = None
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    currency: str = "SGD"
    notes: str | None = None


@router.post("/invoices", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGE])
async def create_invoice(
    body: InvoiceCreate,
    db: AsyncSession = Depends(get_raw_db),
    token: TokenPayload = Depends(get_token_payload),
):
    """Raise a draft invoice from what the customer actually runs.

    A draft, never an issued one. Somebody looks at it before the customer
    does, which is the whole reason the two states are different.
    """
    tenant = (await db.execute(
        text("SELECT id, name FROM tenants "
             " WHERE id = CAST(:tid AS uuid) AND NOT is_platform"),
        {"tid": body.tenant_id},
    )).mappings().first()
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    plan = await _plan_for(db, body.tenant_id)
    usage = await _usage_for(db, body.tenant_id)
    modules = await _modules_for(db, body.tenant_id)
    q = pricing.quote(plan, usage, modules,
                      discount_amount=body.discount_amount, tax_rate=body.tax_rate)

    invoice_id = str(uuid.uuid4())
    due = body.due_date or (date.today() + timedelta(days=DEFAULT_PAYMENT_TERMS_DAYS))

    for attempt in (1, 2):
        number = await _next_invoice_number(db)
        try:
            await db.execute(text("""
                INSERT INTO platform_invoices
                    (id, tenant_id, invoice_number, status, currency,
                     period_start, period_end, due_date, subtotal,
                     discount_amount, tax_rate, tax_amount, total_amount,
                     notes, created_by_user_id)
                VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :number, 'draft',
                        :currency, :ps, :pe, :due, :subtotal, :discount, :rate,
                        :tax, :total, :notes, CAST(:uid AS uuid))
            """), {
                "id": invoice_id, "tid": body.tenant_id, "number": number,
                "currency": body.currency, "ps": body.period_start,
                "pe": body.period_end, "due": due, "subtotal": q.subtotal,
                "discount": q.discount_amount, "rate": q.tax_rate,
                "tax": q.tax_amount, "total": q.total_amount,
                "notes": body.notes, "uid": token.user_id,
            })
            break
        except IntegrityError:
            # Two invoices raised in the same second. Roll back, re-scope and
            # take the next number; a second collision would be a real problem
            # rather than a race, so it is allowed to surface.
            await db.rollback()
            if attempt == 2:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Could not allocate an invoice number; try again",
                )

    for i, line in enumerate(q.lines):
        await db.execute(text("""
            INSERT INTO platform_invoice_items
                (id, invoice_id, kind, module_code, description,
                 quantity, unit_price, line_total, sort_order)
            VALUES (CAST(:id AS uuid), CAST(:inv AS uuid), :kind, :code, :desc,
                    :qty, :unit, :total, :sort)
        """), {
            "id": str(uuid.uuid4()), "inv": invoice_id, "kind": line.kind,
            "code": line.module_code, "desc": line.description,
            "qty": line.quantity, "unit": line.unit_price,
            "total": line.line_total, "sort": i,
        })

    await db.commit()
    return {"id": invoice_id, "invoice_number": number, "status": "draft",
            "tenant_name": tenant["name"], "total_amount": q.total_amount}


@router.get("/invoices", dependencies=[_READ])
async def list_invoices(
    db: AsyncSession = Depends(get_raw_db),
    tenant_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
):
    where, params = ["1 = 1"], {"limit": limit}
    if tenant_id:
        where.append("i.tenant_id = CAST(:tid AS uuid)")
        params["tid"] = tenant_id
    if status_filter:
        where.append("i.status = :status")
        params["status"] = status_filter

    rows = (await db.execute(text(f"""
        SELECT i.*, t.name AS tenant_name,
               COALESCE((SELECT sum(p.amount) FROM platform_payments p
                          WHERE p.invoice_id = i.id), 0) AS amount_paid
          FROM platform_invoices i
          JOIN tenants t ON t.id = i.tenant_id
         WHERE {' AND '.join(where)}
      ORDER BY i.created_at DESC
         LIMIT :limit
    """), params)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/invoices/{invoice_id}", dependencies=[_READ])
async def get_invoice(invoice_id: str, db: AsyncSession = Depends(get_raw_db)):
    inv = (await db.execute(text("""
        SELECT i.*, t.name AS tenant_name, t.slug AS tenant_slug
          FROM platform_invoices i JOIN tenants t ON t.id = i.tenant_id
         WHERE i.id = CAST(:id AS uuid)
    """), {"id": invoice_id})).mappings().first()
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")

    items = (await db.execute(text(
        "SELECT * FROM platform_invoice_items WHERE invoice_id = CAST(:id AS uuid) "
        " ORDER BY sort_order"), {"id": invoice_id})).mappings().all()
    payments = (await db.execute(text(
        "SELECT * FROM platform_payments WHERE invoice_id = CAST(:id AS uuid) "
        " ORDER BY received_at DESC"), {"id": invoice_id})).mappings().all()

    paid = sum((Decimal(str(p["amount"])) for p in payments), Decimal("0"))
    return {
        **dict(inv),
        "items": [dict(i) for i in items],
        "payments": [dict(p) for p in payments],
        "amount_paid": paid,
        "amount_outstanding": Decimal(str(inv["total_amount"])) - paid,
    }


class StatusChange(BaseModel):
    status: str = Field(pattern="^(issued|cancelled)$")


@router.post("/invoices/{invoice_id}/status", dependencies=[_MANAGE])
async def change_invoice_status(
    invoice_id: str, body: StatusChange, db: AsyncSession = Depends(get_raw_db),
):
    """Issue a draft, or cancel one.

    Only from draft. An invoice the customer has already been sent is corrected
    with a credit note, not by being quietly withdrawn — the trigger enforces
    the same thing, and this exists so the refusal is readable.
    """
    current = (await db.execute(
        text("SELECT status FROM platform_invoices WHERE id = CAST(:id AS uuid)"),
        {"id": invoice_id},
    )).scalar()
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    if current != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This invoice is {current}. Only a draft can be issued or cancelled — "
            "raise a credit note to reverse one that has been sent.",
        )
    row = (await db.execute(text("""
        UPDATE platform_invoices
           SET status = :status,
               issued_at = CASE WHEN :issuing THEN now() ELSE issued_at END,
               updated_at = now()
         WHERE id = CAST(:id AS uuid)
     RETURNING id, invoice_number, status, issued_at
    """), {"id": invoice_id, "status": body.status,
           "issuing": body.status == "issued"})).mappings().first()
    await db.commit()
    return dict(row)


class PaymentCreate(BaseModel):
    #: Negative for a refund. One field rather than a flag, so the sum of the
    #: rows is what has been settled without anyone remembering to subtract.
    amount: Decimal
    method: str = Field(default="bank_transfer",
                        pattern="^(bank_transfer|card|cheque|cash|stripe|credit_note|other)$")
    reference: str | None = None
    notes: str | None = None


@router.post("/invoices/{invoice_id}/payments", status_code=status.HTTP_201_CREATED,
             dependencies=[_MANAGE])
async def record_payment(
    invoice_id: str,
    body: PaymentCreate,
    db: AsyncSession = Depends(get_raw_db),
    token: TokenPayload = Depends(get_token_payload),
):
    """Record a payment, or a refund.

    Marking the invoice paid is a consequence, not a separate action: when the
    payments sum to the total it is paid, and if a refund later takes it below
    it is not. A status somebody sets by hand drifts from the money.
    """
    if body.amount == 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A payment of zero records nothing")
    inv = (await db.execute(text(
        "SELECT id, tenant_id, total_amount, status FROM platform_invoices "
        " WHERE id = CAST(:id AS uuid)"), {"id": invoice_id})).mappings().first()
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    if inv["status"] == "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This invoice has not been issued yet, so there is nothing to pay.",
        )

    await db.execute(text("""
        INSERT INTO platform_payments
            (id, invoice_id, tenant_id, amount, method, reference,
             recorded_by_user_id, notes)
        VALUES (CAST(:id AS uuid), CAST(:inv AS uuid), CAST(:tid AS uuid),
                :amount, :method, :ref, CAST(:uid AS uuid), :notes)
    """), {"id": str(uuid.uuid4()), "inv": invoice_id,
           "tid": str(inv["tenant_id"]), "amount": body.amount,
           "method": body.method, "ref": body.reference,
           "uid": token.user_id, "notes": body.notes})

    paid = (await db.execute(text(
        "SELECT COALESCE(sum(amount), 0) FROM platform_payments "
        " WHERE invoice_id = CAST(:id AS uuid)"), {"id": invoice_id})).scalar()
    settled = Decimal(str(paid)) >= Decimal(str(inv["total_amount"]))
    await db.execute(text("""
        UPDATE platform_invoices
           SET status = CASE WHEN :settled THEN 'paid' ELSE 'issued' END,
               paid_at = CASE WHEN :settled THEN now() ELSE NULL END,
               updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"id": invoice_id, "settled": settled})
    await db.commit()
    return {"invoice_id": invoice_id, "amount_paid": paid,
            "status": "paid" if settled else "issued"}


@router.post("/invoices/{invoice_id}/credit", status_code=status.HTTP_201_CREATED,
             dependencies=[_MANAGE])
async def credit_invoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_raw_db),
    token: TokenPayload = Depends(get_token_payload),
):
    """Reverse an issued invoice with a credit note.

    Both stay in the ledger. Deleting the original would leave a gap in the
    numbering and no record that anything was corrected, which is precisely
    what an auditor looks for.
    """
    inv = (await db.execute(text(
        "SELECT * FROM platform_invoices WHERE id = CAST(:id AS uuid)"),
        {"id": invoice_id})).mappings().first()
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    if inv["status"] in ("draft", "cancelled", "credited"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"An invoice that is {inv['status']} cannot be credited.",
        )

    note_id = str(uuid.uuid4())
    number = await _next_invoice_number(db)
    await db.execute(text("""
        INSERT INTO platform_invoices
            (id, tenant_id, invoice_number, status, currency, period_start,
             period_end, issued_at, subtotal, discount_amount, tax_rate,
             tax_amount, total_amount, notes, credits_invoice_id,
             created_by_user_id)
        VALUES (CAST(:id AS uuid), :tid, :number, 'issued', :currency, :ps, :pe,
                now(), :subtotal, 0, :rate, :tax, :total, :notes,
                CAST(:credits AS uuid), CAST(:uid AS uuid))
    """), {
        "id": note_id, "tid": inv["tenant_id"], "number": number,
        "currency": inv["currency"], "ps": inv["period_start"],
        "pe": inv["period_end"],
        # Negative, so the two sum to nothing owed.
        "subtotal": -Decimal(str(inv["subtotal"])),
        "rate": inv["tax_rate"], "tax": -Decimal(str(inv["tax_amount"])),
        "total": -Decimal(str(inv["total_amount"])),
        "notes": f"Credit note for {inv['invoice_number']}",
        "credits": invoice_id, "uid": token.user_id,
    })
    await db.execute(text(
        "UPDATE platform_invoices SET status = 'credited', updated_at = now() "
        " WHERE id = CAST(:id AS uuid)"), {"id": invoice_id})
    await db.commit()
    return {"id": note_id, "invoice_number": number,
            "credits": inv["invoice_number"], "total_amount": -Decimal(str(inv["total_amount"]))}


@router.get("/outstanding", dependencies=[_READ])
async def outstanding(db: AsyncSession = Depends(get_raw_db)):
    """What is owed, and by whom. The number a platform owner checks weekly."""
    rows = (await db.execute(text("""
        SELECT t.id AS tenant_id, t.name AS tenant_name,
               count(*)                                             AS invoices,
               sum(i.total_amount)                                  AS billed,
               COALESCE(sum((SELECT COALESCE(sum(p.amount), 0)
                               FROM platform_payments p
                              WHERE p.invoice_id = i.id)), 0)       AS paid,
               count(*) FILTER (WHERE i.due_date < CURRENT_DATE)    AS overdue
          FROM platform_invoices i
          JOIN tenants t ON t.id = i.tenant_id
         WHERE i.status IN ('issued', 'overdue')
      GROUP BY t.id, t.name
      ORDER BY sum(i.total_amount) DESC
    """))).mappings().all()
    out = []
    for r in rows:
        billed = Decimal(str(r["billed"] or 0))
        paid = Decimal(str(r["paid"] or 0))
        out.append({**dict(r), "outstanding": billed - paid})
    return out
