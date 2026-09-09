"""Gap 8 — Billing / Stripe integration tests.

25 tests covering:
  - Plan listing (public, active-only filter, seeded names)
  - Subscription (auth gate, permission gate, 404 on no sub)
  - Invoice (empty list, permission gate)
  - Quota (auth gate, free-tier fallback)
  - Checkout (auth gate, permission gate, invalid plan, success)
  - Portal (permission gate, no-customer 404, success)
  - Webhooks (invalid sig, unknown event, idempotency, checkout→customer,
              subscription upsert, subscription deleted, invoice failed)
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text

from app.core.security import create_access_token


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _platform_headers(tenant_id: uuid.UUID) -> dict:
    """Billing is the vendor's, not the customer's (migration 0103).

    billing:read and billing:manage used to sit on Admin (2), Supervisor (3)
    and Manager (8) — the customer's own roles — so a security company could
    read and change its subscription to Seventh AI while Seventh AI could see
    none of it. The Stripe tables are keyed by tenant_id: they describe what
    each customer OWES, which is the vendor's side of the relationship.

    These tests therefore authenticate as the platform owner now. The tenant_id
    still scopes the request, because a bill is always about one customer.
    """
    token = create_access_token(str(uuid.uuid4()), str(tenant_id), role_id=1)
    return {"Authorization": f"Bearer {token}"}


def _admin_headers(tenant_id: uuid.UUID) -> dict:
    """A tenant's own administrator — who may no longer touch billing."""
    token = create_access_token(str(uuid.uuid4()), str(tenant_id), role_id=2)
    return {"Authorization": f"Bearer {token}"}


def _guard_headers(tenant_id: uuid.UUID) -> dict:
    token = create_access_token(str(uuid.uuid4()), str(tenant_id), role_id=5)
    return {"Authorization": f"Bearer {token}"}


def _operator_headers(tenant_id: uuid.UUID) -> dict:
    token = create_access_token(str(uuid.uuid4()), str(tenant_id), role_id=4)
    return {"Authorization": f"Bearer {token}"}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def b_tenant(admin_session) -> uuid.UUID:
    """Seed a tenant dedicated to billing tests."""
    tid = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": tid, "name": "Billing Tenant", "slug": f"billing-{tid.hex[:8]}"},
    )
    await admin_session.commit()
    return tid


@pytest_asyncio.fixture
async def billing_plan_with_price(admin_session) -> str:
    """Insert a throwaway billing plan with a Stripe price ID for checkout tests."""
    pid = uuid.uuid4()
    await admin_session.execute(
        text("""
            INSERT INTO billing_plans
                (id, name, stripe_price_id, price_monthly, max_cameras, max_sites,
                 max_users, ai_modules_allowed, sort_order)
            VALUES (:id, 'Test Plan', 'price_test_abc123', 99.00, 2, 1, 3, '["lpr"]', 99)
        """),
        {"id": pid},
    )
    await admin_session.commit()
    yield str(pid)
    await admin_session.execute(
        text("DELETE FROM billing_plans WHERE id = CAST(:id AS UUID)"), {"id": str(pid)}
    )
    await admin_session.commit()


@pytest.fixture
def mock_stripe(monkeypatch):
    """Replace app.routers.billing.stripe with a MagicMock.

    Creates a real exception class for SignatureVerificationError so that
    `except (stripe.error.SignatureVerificationError, ValueError):` in the
    webhook handler correctly catches the raised error.
    """
    mock = MagicMock()
    MockSigError = type("SignatureVerificationError", (Exception,), {})
    mock.error.SignatureVerificationError = MockSigError
    monkeypatch.setattr("app.routers.billing.stripe", mock)
    return mock


# ── Plan tests (4) ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_plans_no_auth_required(app_client: AsyncClient):
    """GET /plans is public — no token needed."""
    resp = await app_client.get("/api/v1/billing/plans")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_plans_returns_three_seeded_plans(app_client: AsyncClient):
    """Migration seeds exactly 3 plans (Starter, Professional, Enterprise)."""
    resp = await app_client.get("/api/v1/billing/plans")
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()]
    assert len(names) >= 3


@pytest.mark.asyncio
async def test_list_plans_only_active_returned(app_client: AsyncClient, admin_session):
    """Inactive plans must not appear in the list."""
    plan_id = uuid.uuid4()
    await admin_session.execute(
        text("""
            INSERT INTO billing_plans (id, name, is_active, ai_modules_allowed, sort_order)
            VALUES (:id, 'Hidden Plan', FALSE, '[]', 99)
        """),
        {"id": plan_id},
    )
    await admin_session.commit()

    resp = await app_client.get("/api/v1/billing/plans")
    names = [p["name"] for p in resp.json()]
    assert "Hidden Plan" not in names

    await admin_session.execute(
        text("DELETE FROM billing_plans WHERE id = CAST(:id AS UUID)"), {"id": str(plan_id)}
    )
    await admin_session.commit()


@pytest.mark.asyncio
async def test_billing_plan_seed_names(app_client: AsyncClient):
    """Seeded plans include Starter, Professional, Enterprise."""
    resp = await app_client.get("/api/v1/billing/plans")
    names = {p["name"] for p in resp.json()}
    for expected in ("Starter", "Professional", "Enterprise"):
        assert expected in names, f"Plan '{expected}' not in seeded plans"


# ── Subscription tests (3) ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_subscription_requires_auth(client: AsyncClient):
    """No token → 401."""
    resp = await client.get("/api/v1/billing/subscription")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_subscription_requires_billing_read(app_client: AsyncClient, b_tenant):
    """Security guard (role 5) lacks billing:read → 403."""
    resp = await app_client.get(
        "/api/v1/billing/subscription", headers=_guard_headers(b_tenant)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_subscription_no_subscription_404(app_client: AsyncClient, b_tenant):
    """Admin with no subscription → 404."""
    resp = await app_client.get(
        "/api/v1/billing/subscription", headers=_platform_headers(b_tenant)
    )
    assert resp.status_code == 404


# ── Invoice tests (2) ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_invoices_empty(app_client: AsyncClient, b_tenant):
    """No invoices for a fresh tenant → empty list."""
    resp = await app_client.get(
        "/api/v1/billing/invoices", headers=_platform_headers(b_tenant)
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_invoices_requires_billing_read(app_client: AsyncClient, b_tenant):
    """Security guard lacks billing:read → 403."""
    resp = await app_client.get(
        "/api/v1/billing/invoices", headers=_guard_headers(b_tenant)
    )
    assert resp.status_code == 403


# ── Quota tests (2) ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_quota_requires_auth(client: AsyncClient):
    """No token → 401."""
    resp = await client.get("/api/v1/billing/quota")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_quota_no_subscription_free_tier(app_client: AsyncClient, b_tenant):
    """Tenant with no subscription gets free-tier limits in response."""
    resp = await app_client.get(
        "/api/v1/billing/quota", headers=_admin_headers(b_tenant)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "plan" in body
    assert "usage" in body
    # Free tier caps
    assert body["plan"]["max_cameras"] == 1
    assert body["plan"]["max_sites"] == 1
    assert body["plan"]["max_users"] == 3
    assert body["plan"]["ai_modules_allowed"] == []


# ── Checkout tests (4) ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_checkout_session_requires_auth(client: AsyncClient):
    """No token → 401."""
    resp = await client.post(
        "/api/v1/billing/checkout",
        json={"plan_id": str(uuid.uuid4()), "success_url": "http://ok", "cancel_url": "http://no"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_checkout_session_requires_billing_manage(
    app_client: AsyncClient, b_tenant
):
    """Operator (role 4) lacks billing:manage → 403."""
    resp = await app_client.post(
        "/api/v1/billing/checkout",
        headers=_operator_headers(b_tenant),
        json={"plan_id": str(uuid.uuid4()), "success_url": "http://ok", "cancel_url": "http://no"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_checkout_session_invalid_plan_404(app_client: AsyncClient, b_tenant):
    """Non-existent plan_id → 404."""
    resp = await app_client.post(
        "/api/v1/billing/checkout",
        headers=_platform_headers(b_tenant),
        json={
            "plan_id": str(uuid.uuid4()),
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_checkout_session_success(
    app_client: AsyncClient, b_tenant, billing_plan_with_price, mock_stripe
):
    """Admin + valid plan + mocked Stripe → returns URL and session_id."""
    mock_stripe.Customer.create.return_value = MagicMock(id="cus_test_abc")
    mock_checkout_session = MagicMock(url="https://checkout.stripe.com/test_xyz", id="cs_test_123")
    mock_stripe.checkout.Session.create.return_value = mock_checkout_session

    resp = await app_client.post(
        "/api/v1/billing/checkout",
        headers=_platform_headers(b_tenant),
        json={
            "plan_id": billing_plan_with_price,
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] == "https://checkout.stripe.com/test_xyz"
    assert body["session_id"] == "cs_test_123"


# ── Portal tests (3) ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_portal_session_requires_billing_manage(
    app_client: AsyncClient, b_tenant
):
    """Operator lacks billing:manage → 403."""
    resp = await app_client.post(
        "/api/v1/billing/portal",
        headers=_operator_headers(b_tenant),
        json={"return_url": "https://example.com/billing"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_portal_no_customer_404(app_client: AsyncClient, b_tenant, mock_stripe):
    """Admin with no billing_customer row → 404."""
    resp = await app_client.post(
        "/api/v1/billing/portal",
        headers=_platform_headers(b_tenant),
        json={"return_url": "https://example.com/billing"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_portal_session_success(
    app_client: AsyncClient, b_tenant, admin_session, mock_stripe
):
    """Tenant with a billing_customer → mocked Stripe returns portal URL."""
    cus_id = f"cus_portal_{uuid.uuid4().hex[:12]}"
    await admin_session.execute(
        text("""
            INSERT INTO billing_customers (tenant_id, stripe_customer_id)
            VALUES (CAST(:tid AS UUID), :cid)
            ON CONFLICT DO NOTHING
        """),
        {"tid": str(b_tenant), "cid": cus_id},
    )
    await admin_session.commit()

    mock_portal = MagicMock(url="https://billing.stripe.com/portal/test")
    mock_stripe.billing_portal.Session.create.return_value = mock_portal

    resp = await app_client.post(
        "/api/v1/billing/portal",
        headers=_platform_headers(b_tenant),
        json={"return_url": "https://example.com/billing"},
    )
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://billing.stripe.com/portal/test"


# ── Webhook tests (7) ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_invalid_signature_400(app_client: AsyncClient, mock_stripe):
    """Bad Stripe-Signature header → 400."""
    mock_stripe.Webhook.construct_event.side_effect = (
        mock_stripe.error.SignatureVerificationError("bad sig", "sig")
    )
    resp = await app_client.post(
        "/api/v1/billing/webhooks",
        content=b'{"id":"evt_bad"}',
        headers={"stripe-signature": "invalid"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_webhook_unknown_event_type_200_noop(app_client: AsyncClient, mock_stripe):
    """Unknown event type → logged + 200 returned (Stripe requires 200)."""
    event = {
        "id": f"evt_unknown_{uuid.uuid4().hex[:8]}",
        "type": "some.future.event",
        "data": {"object": {}},
    }
    mock_stripe.Webhook.construct_event.return_value = event

    resp = await app_client.post(
        "/api/v1/billing/webhooks",
        content=json.dumps(event).encode(),
        headers={"stripe-signature": "ok"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_webhook_idempotent_duplicate_event_skipped(
    app_client: AsyncClient, mock_stripe, admin_session
):
    """Sending the same event_id twice → second call returns 'already_processed'."""
    event_id = f"evt_dup_{uuid.uuid4().hex[:8]}"
    event = {
        "id": event_id,
        "type": "some.event",
        "data": {"object": {}},
    }
    mock_stripe.Webhook.construct_event.return_value = event

    # First call → processes and logs
    resp1 = await app_client.post(
        "/api/v1/billing/webhooks",
        content=json.dumps(event).encode(),
        headers={"stripe-signature": "ok"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "ok"

    # Second call with same event_id → skipped
    resp2 = await app_client.post(
        "/api/v1/billing/webhooks",
        content=json.dumps(event).encode(),
        headers={"stripe-signature": "ok"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "already_processed"


@pytest.mark.asyncio
async def test_webhook_checkout_completed_creates_billing_customer(
    app_client: AsyncClient, b_tenant, mock_stripe, admin_session
):
    """checkout.session.completed → billing_customers row inserted for the tenant."""
    cus_checkout_id = f"cus_co_{uuid.uuid4().hex[:12]}"
    sub_checkout_id = f"sub_co_{uuid.uuid4().hex[:12]}"
    sub_data = {
        "id": sub_checkout_id,
        "status": "active",
        "customer": cus_checkout_id,
        "items": {"data": [{"price": {"id": "price_starter"}}]},
        "current_period_start": 1700000000,
        "current_period_end": 1702592000,
        "cancel_at_period_end": False,
        "trial_end": None,
    }
    mock_stripe.Subscription.retrieve.return_value = sub_data

    event = {
        "id": f"evt_co_{uuid.uuid4().hex[:8]}",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": f"cs_{uuid.uuid4().hex[:8]}",
                "customer": cus_checkout_id,
                "subscription": sub_checkout_id,
                "client_reference_id": str(b_tenant),
                "metadata": {"tenant_id": str(b_tenant)},
            }
        },
    }
    mock_stripe.Webhook.construct_event.return_value = event

    resp = await app_client.post(
        "/api/v1/billing/webhooks",
        content=json.dumps(event).encode(),
        headers={"stripe-signature": "ok"},
    )
    assert resp.status_code == 200

    row = (await admin_session.execute(
        text("SELECT stripe_customer_id FROM billing_customers WHERE tenant_id = CAST(:tid AS UUID)"),
        {"tid": str(b_tenant)},
    )).first()
    assert row is not None
    assert row[0] == cus_checkout_id


@pytest.mark.asyncio
async def test_webhook_subscription_updated_creates_subscription(
    app_client: AsyncClient, b_tenant, mock_stripe, admin_session
):
    """customer.subscription.updated → billing_subscriptions row upserted."""
    # Seed a billing_customer so the handler can find the tenant
    await admin_session.execute(
        text("""
            INSERT INTO billing_customers (tenant_id, stripe_customer_id)
            VALUES (CAST(:tid AS UUID), :cid) ON CONFLICT DO NOTHING
        """),
        {"tid": str(b_tenant), "cid": "cus_sub_upd_test"},
    )
    await admin_session.commit()

    sub_id = f"sub_upd_{uuid.uuid4().hex[:8]}"
    sub_obj = {
        "id": sub_id,
        "status": "active",
        "customer": "cus_sub_upd_test",
        "items": {"data": [{"price": {"id": "price_starter"}}]},
        "current_period_start": 1700000000,
        "current_period_end": 1702592000,
        "cancel_at_period_end": False,
        "trial_end": None,
    }
    event = {
        "id": f"evt_su_{uuid.uuid4().hex[:8]}",
        "type": "customer.subscription.updated",
        "data": {"object": sub_obj},
    }
    mock_stripe.Webhook.construct_event.return_value = event

    resp = await app_client.post(
        "/api/v1/billing/webhooks",
        content=json.dumps(event).encode(),
        headers={"stripe-signature": "ok"},
    )
    assert resp.status_code == 200

    row = (await admin_session.execute(
        text("""
            SELECT status FROM billing_subscriptions
            WHERE stripe_subscription_id = :sid
        """),
        {"sid": sub_id},
    )).first()
    assert row is not None
    assert row[0] == "active"


@pytest.mark.asyncio
async def test_webhook_subscription_deleted_cancels(
    app_client: AsyncClient, b_tenant, mock_stripe, admin_session
):
    """customer.subscription.deleted → status set to 'canceled'."""
    # Seed existing subscription row
    sub_id = f"sub_del_{uuid.uuid4().hex[:8]}"
    await admin_session.execute(
        text("""
            INSERT INTO billing_subscriptions
                (tenant_id, stripe_subscription_id, status)
            VALUES (CAST(:tid AS UUID), :sid, 'active')
        """),
        {"tid": str(b_tenant), "sid": sub_id},
    )
    await admin_session.commit()

    event = {
        "id": f"evt_sd_{uuid.uuid4().hex[:8]}",
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": sub_id, "customer": "cus_any"}},
    }
    mock_stripe.Webhook.construct_event.return_value = event

    resp = await app_client.post(
        "/api/v1/billing/webhooks",
        content=json.dumps(event).encode(),
        headers={"stripe-signature": "ok"},
    )
    assert resp.status_code == 200

    row = (await admin_session.execute(
        text("SELECT status FROM billing_subscriptions WHERE stripe_subscription_id = :sid"),
        {"sid": sub_id},
    )).first()
    assert row is not None
    assert row[0] == "canceled"


@pytest.mark.asyncio
async def test_webhook_invoice_payment_failed_updates_past_due(
    app_client: AsyncClient, b_tenant, mock_stripe, admin_session
):
    """invoice.payment_failed → subscription status set to 'past_due'."""
    sub_id = f"sub_fail_{uuid.uuid4().hex[:8]}"
    await admin_session.execute(
        text("""
            INSERT INTO billing_subscriptions
                (tenant_id, stripe_subscription_id, status)
            VALUES (CAST(:tid AS UUID), :sid, 'active')
        """),
        {"tid": str(b_tenant), "sid": sub_id},
    )
    await admin_session.commit()

    # Seed billing_customer so invoice lookup falls back correctly
    await admin_session.execute(
        text("""
            INSERT INTO billing_customers (tenant_id, stripe_customer_id)
            VALUES (CAST(:tid AS UUID), :cid) ON CONFLICT DO NOTHING
        """),
        {"tid": str(b_tenant), "cid": "cus_fail_test"},
    )
    await admin_session.commit()

    inv_id = f"in_fail_{uuid.uuid4().hex[:8]}"
    event = {
        "id": f"evt_if_{uuid.uuid4().hex[:8]}",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": inv_id,
                "customer": "cus_fail_test",
                "subscription": sub_id,
                "amount_due": 29900,
                "amount_paid": 0,
                "currency": "usd",
                "status": "open",
                "period_start": 1700000000,
                "period_end": 1702592000,
                "invoice_pdf": None,
                "hosted_invoice_url": None,
            }
        },
    }
    mock_stripe.Webhook.construct_event.return_value = event

    resp = await app_client.post(
        "/api/v1/billing/webhooks",
        content=json.dumps(event).encode(),
        headers={"stripe-signature": "ok"},
    )
    assert resp.status_code == 200

    sub_row = (await admin_session.execute(
        text("SELECT status FROM billing_subscriptions WHERE stripe_subscription_id = :sid"),
        {"sid": sub_id},
    )).first()
    assert sub_row is not None
    assert sub_row[0] == "past_due"


# ── Billing is the vendor's side of the relationship (migration 0103) ─────────

@pytest.mark.asyncio
async def test_a_tenant_admin_cannot_read_its_own_billing(app_client: AsyncClient, b_tenant):
    """The change that 0103 makes, stated as a test.

    A security company reading — or worse, changing — its own subscription to
    Seventh AI was the customer holding the vendor's ledger. Admin, Supervisor
    and Manager all held billing:read and billing:manage; none of them do now.
    """
    r = await app_client.get("/api/v1/billing/subscription",
                             headers=_admin_headers(b_tenant))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_a_tenant_admin_cannot_start_a_checkout(app_client: AsyncClient, b_tenant):
    """Including the paying half. Self-serve upgrade is the vendor's decision
    to offer, not the customer's to take."""
    r = await app_client.post("/api/v1/billing/checkout",
                              json={"plan_id": str(uuid.uuid4())},
                              headers=_admin_headers(b_tenant))
    assert r.status_code == 403
