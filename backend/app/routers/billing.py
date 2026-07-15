"""Gap 8 — Stripe billing integration.

Endpoints:
  GET  /api/v1/billing/plans          — list active plans (public)
  GET  /api/v1/billing/subscription   — current subscription (billing:read)
  GET  /api/v1/billing/quota          — usage vs plan limits (auth required)
  POST /api/v1/billing/checkout       — create Stripe checkout session (billing:manage)
  POST /api/v1/billing/portal         — create Stripe customer portal session (billing:manage)
  GET  /api/v1/billing/invoices       — invoice history (billing:read)
  POST /api/v1/billing/webhooks       — Stripe webhook (signature-verified, no JWT)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant, get_raw_db

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

# Configured at startup via env vars; empty in dev/test (all Stripe calls are mocked in tests).
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")


# ── Request bodies ────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan_id: str
    success_url: str
    cancel_url: str


class PortalRequest(BaseModel):
    return_url: str


# ── Plans (public) ────────────────────────────────────────────────────────────

@router.get("/plans")
async def list_plans(db: AsyncSession = Depends(get_raw_db)):
    result = await db.execute(
        text("""
            SELECT id, name, description, price_monthly, price_yearly,
                   max_cameras, max_sites, max_users, ai_modules_allowed, sort_order
            FROM billing_plans
            WHERE is_active = TRUE
            ORDER BY sort_order
        """)
    )
    return [dict(r) for r in result.mappings().all()]


# ── Subscription ──────────────────────────────────────────────────────────────

@router.get("/subscription")
async def get_subscription(
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
    _=Depends(require_permission("billing:read")),
):
    result = await db.execute(
        text("""
            SELECT bs.id, bs.stripe_subscription_id, bs.stripe_price_id, bs.status,
                   bs.current_period_start, bs.current_period_end,
                   bs.cancel_at_period_end, bs.trial_end, bs.created_at,
                   bp.name AS plan_name, bp.max_cameras, bp.max_sites,
                   bp.max_users, bp.ai_modules_allowed
            FROM billing_subscriptions bs
            LEFT JOIN billing_plans bp ON bp.id = bs.plan_id
            WHERE bs.tenant_id = CAST(:tid AS UUID)
            ORDER BY bs.created_at DESC
            LIMIT 1
        """),
        {"tid": token.tenant_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No subscription found")
    return dict(row)


# ── Quota ─────────────────────────────────────────────────────────────────────

@router.get("/quota")
async def get_quota(
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    plan_row = (await db.execute(
        text("""
            SELECT bp.max_cameras, bp.max_sites, bp.max_users, bp.ai_modules_allowed
            FROM billing_subscriptions bs
            JOIN billing_plans bp ON bp.id = bs.plan_id
            WHERE bs.tenant_id = CAST(:tid AS UUID) AND bs.status IN ('active','trialing')
            ORDER BY bs.created_at DESC LIMIT 1
        """),
        {"tid": token.tenant_id},
    )).mappings().first()

    camera_count = (await db.execute(
        text("SELECT COUNT(*) FROM cameras WHERE is_active = TRUE")
    )).scalar() or 0
    user_count = (await db.execute(
        text("SELECT COUNT(*) FROM users WHERE is_active = TRUE")
    )).scalar() or 0
    site_count = (await db.execute(
        text("SELECT COUNT(*) FROM sites WHERE is_active = TRUE")
    )).scalar() or 0

    if plan_row:
        plan_limits = {
            "max_cameras": plan_row["max_cameras"],
            "max_sites": plan_row["max_sites"],
            "max_users": plan_row["max_users"],
            "ai_modules_allowed": plan_row["ai_modules_allowed"],
        }
    else:
        # Free tier — no active subscription
        plan_limits = {"max_cameras": 1, "max_sites": 1, "max_users": 3, "ai_modules_allowed": []}

    return {
        "plan": plan_limits,
        "usage": {"cameras": camera_count, "sites": site_count, "users": user_count},
    }


# ── Checkout ──────────────────────────────────────────────────────────────────

@router.post("/checkout")
async def create_checkout_session(
    body: CheckoutRequest,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
    _=Depends(require_permission("billing:manage")),
):
    plan = (await db.execute(
        text("""
            SELECT id, stripe_price_id FROM billing_plans
            WHERE id = CAST(:pid AS UUID) AND is_active = TRUE
        """),
        {"pid": body.plan_id},
    )).mappings().first()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    if not plan["stripe_price_id"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Plan is not yet configured for Stripe billing")

    customer_id = await _get_or_create_stripe_customer(db, token.tenant_id)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": plan["stripe_price_id"], "quantity": 1}],
        mode="subscription",
        success_url=body.success_url,
        cancel_url=body.cancel_url,
        client_reference_id=token.tenant_id,
        metadata={"tenant_id": token.tenant_id},
    )
    return {"url": session.url, "session_id": session.id}


# ── Portal ────────────────────────────────────────────────────────────────────

@router.post("/portal")
async def create_portal_session(
    body: PortalRequest,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
    _=Depends(require_permission("billing:manage")),
):
    customer = await _get_billing_customer(db, token.tenant_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No billing customer found — complete a checkout first")
    session = stripe.billing_portal.Session.create(
        customer=customer["stripe_customer_id"],
        return_url=body.return_url,
    )
    return {"url": session.url}


# ── Invoices ──────────────────────────────────────────────────────────────────

@router.get("/invoices")
async def list_invoices(
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
    _=Depends(require_permission("billing:read")),
):
    result = await db.execute(
        text("""
            SELECT id, stripe_invoice_id, stripe_subscription_id,
                   amount_due, amount_paid, currency, status,
                   invoice_pdf, hosted_invoice_url,
                   period_start, period_end, paid_at, created_at
            FROM billing_invoices
            WHERE tenant_id = CAST(:tid AS UUID)
            ORDER BY created_at DESC
        """),
        {"tid": token.tenant_id},
    )
    return [dict(r) for r in result.mappings().all()]


# ── Webhooks ──────────────────────────────────────────────────────────────────

@router.post("/webhooks")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_raw_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, _WEBHOOK_SECRET)
    except (stripe.error.SignatureVerificationError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook signature")

    event_id = event["id"]
    event_type = event["type"]

    # Idempotency — skip already-processed events
    existing = (await db.execute(
        text("SELECT id FROM billing_webhook_events WHERE stripe_event_id = :eid"),
        {"eid": event_id},
    )).first()
    if existing is not None:
        return {"status": "already_processed"}

    # Log the event before processing (crash mid-handler ≠ reprocess on next delivery)
    await db.execute(
        text("""
            INSERT INTO billing_webhook_events (stripe_event_id, event_type, payload)
            VALUES (:eid, :etype, CAST(:payload AS JSONB))
        """),
        {"eid": event_id, "etype": event_type, "payload": json.dumps(dict(event))},
    )

    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(db, obj)
    elif event_type in ("customer.subscription.created", "customer.subscription.updated"):
        await _handle_subscription_upsert(db, obj)
    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(db, obj)
    elif event_type == "invoice.payment_succeeded":
        await _handle_invoice_upsert(db, obj, paid=True)
    elif event_type == "invoice.payment_failed":
        await _handle_invoice_failed(db, obj)
    # Unknown event types: logged above, return 200 (Stripe requires 200 for all events)

    await db.commit()
    return {"status": "ok"}


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _get_billing_customer(db: AsyncSession, tenant_id: str):
    result = await db.execute(
        text("SELECT * FROM billing_customers WHERE tenant_id = CAST(:tid AS UUID)"),
        {"tid": tenant_id},
    )
    return result.mappings().first()


async def _get_or_create_stripe_customer(db: AsyncSession, tenant_id: str) -> str:
    existing = await _get_billing_customer(db, tenant_id)
    if existing is not None:
        return existing["stripe_customer_id"]

    customer = stripe.Customer.create(metadata={"tenant_id": tenant_id})
    await db.execute(
        text("""
            INSERT INTO billing_customers (tenant_id, stripe_customer_id)
            VALUES (CAST(:tid AS UUID), :cid)
            ON CONFLICT DO NOTHING
        """),
        {"tid": tenant_id, "cid": customer.id},
    )
    await db.commit()
    return customer.id


async def _handle_checkout_completed(db: AsyncSession, session_obj: dict) -> None:
    tenant_id = (session_obj.get("client_reference_id")
                 or (session_obj.get("metadata") or {}).get("tenant_id"))
    if not tenant_id:
        return

    stripe_customer_id = session_obj.get("customer")
    subscription_id = session_obj.get("subscription")

    await db.execute(
        text("""
            INSERT INTO billing_customers (tenant_id, stripe_customer_id)
            VALUES (CAST(:tid AS UUID), :cid)
            ON CONFLICT DO NOTHING
        """),
        {"tid": tenant_id, "cid": stripe_customer_id},
    )

    if subscription_id:
        sub = stripe.Subscription.retrieve(subscription_id)
        await _upsert_subscription_row(db, tenant_id, sub)


async def _handle_subscription_upsert(db: AsyncSession, sub_obj: dict) -> None:
    cust_row = (await db.execute(
        text("SELECT tenant_id FROM billing_customers WHERE stripe_customer_id = :cid"),
        {"cid": sub_obj.get("customer")},
    )).first()
    if cust_row is None:
        return
    await _upsert_subscription_row(db, str(cust_row[0]), sub_obj)


async def _handle_subscription_deleted(db: AsyncSession, sub_obj: dict) -> None:
    await db.execute(
        text("""
            UPDATE billing_subscriptions
            SET status = 'canceled', updated_at = now()
            WHERE stripe_subscription_id = :sid
        """),
        {"sid": sub_obj.get("id")},
    )


async def _upsert_subscription_row(
    db: AsyncSession, tenant_id: str, sub_obj: dict
) -> None:
    stripe_price_id = None
    items = sub_obj.get("items", {})
    if isinstance(items, dict):
        data = items.get("data", [])
        if data:
            price = data[0].get("price", {})
            stripe_price_id = price.get("id") if isinstance(price, dict) else None

    plan_id = None
    if stripe_price_id:
        plan_row = (await db.execute(
            text("SELECT id FROM billing_plans WHERE stripe_price_id = :pid"),
            {"pid": stripe_price_id},
        )).first()
        if plan_row:
            plan_id = str(plan_row[0])

    def _ts(val) -> datetime | None:
        if val is None:
            return None
        try:
            return datetime.fromtimestamp(int(val), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None

    await db.execute(
        text("""
            INSERT INTO billing_subscriptions
                (tenant_id, stripe_subscription_id, stripe_price_id, plan_id, status,
                 current_period_start, current_period_end, cancel_at_period_end, trial_end)
            VALUES
                (CAST(:tid AS UUID), :sid, :price_id, CAST(:plan_id AS UUID), :status,
                 :period_start, :period_end, :cancel_at, :trial_end)
            ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                status               = EXCLUDED.status,
                stripe_price_id      = EXCLUDED.stripe_price_id,
                plan_id              = EXCLUDED.plan_id,
                current_period_start = EXCLUDED.current_period_start,
                current_period_end   = EXCLUDED.current_period_end,
                cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                trial_end            = EXCLUDED.trial_end,
                updated_at           = now()
        """),
        {
            "tid": tenant_id,
            "sid": sub_obj.get("id"),
            "price_id": stripe_price_id,
            "plan_id": plan_id,
            "status": sub_obj.get("status", "active"),
            "period_start": _ts(sub_obj.get("current_period_start")),
            "period_end": _ts(sub_obj.get("current_period_end")),
            "cancel_at": bool(sub_obj.get("cancel_at_period_end", False)),
            "trial_end": _ts(sub_obj.get("trial_end")),
        },
    )


async def _handle_invoice_upsert(
    db: AsyncSession, inv_obj: dict, paid: bool = False
) -> None:
    stripe_sub_id = inv_obj.get("subscription")

    # Find tenant via subscription first, fall back to customer lookup
    tenant_id = None
    if stripe_sub_id:
        row = (await db.execute(
            text("SELECT tenant_id FROM billing_subscriptions WHERE stripe_subscription_id = :sid"),
            {"sid": stripe_sub_id},
        )).first()
        if row:
            tenant_id = str(row[0])

    if tenant_id is None:
        row = (await db.execute(
            text("SELECT tenant_id FROM billing_customers WHERE stripe_customer_id = :cid"),
            {"cid": inv_obj.get("customer")},
        )).first()
        if row:
            tenant_id = str(row[0])

    if tenant_id is None:
        return

    def _ts(val) -> datetime | None:
        if val is None:
            return None
        try:
            return datetime.fromtimestamp(int(val), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None

    paid_at = None
    if paid:
        st = inv_obj.get("status_transitions") or {}
        paid_at = _ts(st.get("paid_at") if isinstance(st, dict) else None)

    await db.execute(
        text("""
            INSERT INTO billing_invoices
                (tenant_id, stripe_invoice_id, stripe_subscription_id,
                 amount_due, amount_paid, currency, status,
                 invoice_pdf, hosted_invoice_url, period_start, period_end, paid_at)
            VALUES
                (CAST(:tid AS UUID), :iid, :sid,
                 :amount_due, :amount_paid, :currency, :status,
                 :pdf, :url, :ps, :pe, :paid_at)
            ON CONFLICT (stripe_invoice_id) DO UPDATE SET
                status      = EXCLUDED.status,
                amount_paid = EXCLUDED.amount_paid,
                paid_at     = EXCLUDED.paid_at
        """),
        {
            "tid": tenant_id,
            "iid": inv_obj.get("id"),
            "sid": stripe_sub_id,
            "amount_due": (inv_obj.get("amount_due") or 0) / 100,
            "amount_paid": (inv_obj.get("amount_paid") or 0) / 100,
            "currency": inv_obj.get("currency", "usd"),
            "status": inv_obj.get("status", "open"),
            "pdf": inv_obj.get("invoice_pdf"),
            "url": inv_obj.get("hosted_invoice_url"),
            "ps": _ts(inv_obj.get("period_start")),
            "pe": _ts(inv_obj.get("period_end")),
            "paid_at": paid_at,
        },
    )


async def _handle_invoice_failed(db: AsyncSession, inv_obj: dict) -> None:
    stripe_sub_id = inv_obj.get("subscription")
    if stripe_sub_id:
        await db.execute(
            text("""
                UPDATE billing_subscriptions
                SET status = 'past_due', updated_at = now()
                WHERE stripe_subscription_id = :sid
            """),
            {"sid": stripe_sub_id},
        )
    await _handle_invoice_upsert(db, inv_obj, paid=False)
