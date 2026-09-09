"""The vendor's ledger: what a customer owes, and what they have paid.

Two things this has to get right, and they are different kinds of right.

THE MONEY. Priced from what the customer actually runs, itemised so the total
can be taken apart, and frozen once the customer has seen it. The pricing
arithmetic is tested separately and purely in test_pricing.py; what is tested
here is that the right numbers reach the invoice and stay there.

THE VISIBILITY. Every cross-tenant read in this router goes through a SECURITY
DEFINER function, because billing_subscriptions and tenant_module_licenses are
both RLS-protected and the router has no tenant scope. Read directly they
return nothing — and nothing prices as "No plan", raises an invoice for $0.00,
and looks exactly like a customer who owes nothing. That failure happened, it
was silent, and the test below is what would have caught it.

Sections:
  A — Only the vendor sees the ledger (2 tests)
  B — Pricing sees what it is pricing (3 tests)
  C — The invoice lifecycle (7 tests)
  D — Payments and credit notes (4 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app
from app.core.security import create_access_token

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

SUPER_ADMIN, ADMIN, GUARD = 1, 2, 5


def _engine():
    return create_async_engine(ADMIN_DATABASE_URL)


async def _sql(statement: str, params: dict | None = None):
    engine = _engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        result = await s.execute(text(statement), params or {})
        rows = result.all() if result.returns_rows else []
        await s.commit()
    await engine.dispose()
    return rows


async def _token(role_id: int) -> str:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Bill', :slug)",
               {"id": tenant_id, "slug": f"bill-{tenant_id.hex[:10]}"})
    await _sql(
        "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
        "VALUES (:id, :tid, :role, :email, 'hashed')",
        {"id": user_id, "tid": tenant_id, "role": role_id,
         "email": f"bill-{user_id.hex[:8]}@test.local"})
    return create_access_token(str(user_id), str(tenant_id), role_id)


async def _client(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


async def _customer_on_a_plan(cameras: int = 10, sites: int = 2, users: int = 5):
    """A tenant with known usage, subscribed to a known plan.

    Seeded into a tenant nobody authenticates against, so a figure that came
    back right because the caller happened to be inside it would not pass.
    """
    tenant_id, plan_id = uuid.uuid4(), uuid.uuid4()
    await _sql("INSERT INTO tenants (id, name, slug, status) "
               "VALUES (:id, 'Billed Co', :slug, 'active')",
               {"id": tenant_id, "slug": f"billed-{tenant_id.hex[:10]}"})
    for i in range(users):
        await _sql(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
            "VALUES (:id, :tid, :role, :email, 'hashed')",
            {"id": uuid.uuid4(), "tid": tenant_id, "role": GUARD,
             "email": f"u{i}-{tenant_id.hex[:8]}@test.local"})
    site_ids = []
    for i in range(sites):
        sid = uuid.uuid4()
        site_ids.append(sid)
        await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :n)",
                   {"id": sid, "tid": tenant_id, "n": f"Site {i}"})
    for i in range(cameras):
        await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) "
                   "VALUES (:id, :tid, :sid, :n)",
                   {"id": uuid.uuid4(), "tid": tenant_id,
                    "sid": site_ids[i % len(site_ids)] if site_ids else None,
                    "n": f"Cam {i}"})

    await _sql("""
        INSERT INTO billing_plans
            (id, name, price_monthly, billing_cycle, included_cameras,
             included_sites, included_users, price_per_camera, price_per_site,
             price_per_user)
        VALUES (:id, 'Test Starter', 500, 'monthly', 5, 1, 3, 5, 50, 2)
    """, {"id": plan_id})
    await _sql("""
        INSERT INTO billing_subscriptions
            (tenant_id, stripe_subscription_id, plan_id, status, current_period_end)
        VALUES (:tid, :sub, :plan, 'active', now() + interval '20 days')
    """, {"tid": tenant_id, "sub": f"sub_{uuid.uuid4().hex[:16]}", "plan": plan_id})
    return tenant_id, plan_id


# ─── A. Who sees the ledger ──────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/api/v1/platform/modules",
    "/api/v1/platform/plans",
    "/api/v1/platform/invoices",
    "/api/v1/platform/outstanding",
])
async def test_a_tenant_admin_cannot_reach_the_vendors_ledger(path):
    """What every customer is charged, and what they owe, is not a customer's
    business. No RLS confines these queries, so the permission is all there is."""
    async with await _client(await _token(ADMIN)) as c:
        assert (await c.get(path)).status_code == 403, path


@pytest.mark.asyncio
async def test_the_catalogue_is_seeded():
    async with await _client(await _token(SUPER_ADMIN)) as c:
        r = await c.get("/api/v1/platform/modules")
    assert r.status_code == 200
    codes = {m["code"] for m in r.json()}
    assert {"lpr", "face", "windows"} <= codes


# ─── B. Pricing sees what it is pricing ──────────────────────────────────────

@pytest.mark.asyncio
async def test_the_quote_finds_the_customers_plan():
    """The bug this file exists because of.

    billing_subscriptions is RLS-protected and this router is unscoped, so
    reading it directly returned nothing — and "no subscription" prices as
    "No plan", which looks like a customer who owes nothing rather than a query
    that could not see.
    """
    tenant_id, _ = await _customer_on_a_plan()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        r = await c.post("/api/v1/platform/pricing/preview",
                         json={"tenant_id": str(tenant_id)})
    assert r.status_code == 200, r.text
    assert r.json()["plan"] == "Test Starter", "the plan must be visible to pricing"


@pytest.mark.asyncio
async def test_the_quote_uses_the_customers_real_usage():
    """Counted through the SECURITY DEFINER function too. Billing everyone for
    zero cameras is the same failure wearing a different hat."""
    tenant_id, _ = await _customer_on_a_plan(cameras=10, sites=2, users=5)
    async with await _client(await _token(SUPER_ADMIN)) as c:
        body = (await c.post("/api/v1/platform/pricing/preview",
                             json={"tenant_id": str(tenant_id)})).json()
    assert body["usage"] == {"cameras": 10, "sites": 2, "users": 5}
    # 500 base + 5 cameras over × $5 + 1 site over × $50 + 2 users over × $2
    assert Decimal(str(body["subtotal"])) == Decimal("579.00")


@pytest.mark.asyncio
async def test_a_customer_with_no_plan_prices_at_nothing_and_says_so():
    """Rather than erroring. Previewing a bill for somebody not yet put on a
    plan is reasonable, and "No plan" is more useful than a 404."""
    tenant_id = uuid.uuid4()
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Unplanned', :slug)",
               {"id": tenant_id, "slug": f"unpl-{tenant_id.hex[:10]}"})
    async with await _client(await _token(SUPER_ADMIN)) as c:
        body = (await c.post("/api/v1/platform/pricing/preview",
                             json={"tenant_id": str(tenant_id)})).json()
    assert body["plan"] == "No plan"
    assert Decimal(str(body["total_amount"])) == 0


# ─── C. The invoice lifecycle ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_invoice_is_raised_as_a_draft_with_its_lines():
    """A draft, never issued straight away: somebody looks at it before the
    customer does, which is the whole reason the two states differ."""
    tenant_id, _ = await _customer_on_a_plan()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        created = await c.post("/api/v1/platform/invoices",
                               json={"tenant_id": str(tenant_id), "tax_rate": "0.09"})
        assert created.status_code == 201, created.text
        assert created.json()["status"] == "draft"
        full = (await c.get(f"/api/v1/platform/invoices/{created.json()['id']}")).json()

    assert len(full["items"]) >= 1
    assert (sum(Decimal(str(i["line_total"])) for i in full["items"])
            == Decimal(str(full["subtotal"]))), "the lines must sum to the subtotal"


@pytest.mark.asyncio
async def test_a_draft_cannot_be_paid():
    tenant_id, _ = await _customer_on_a_plan()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        inv = (await c.post("/api/v1/platform/invoices",
                            json={"tenant_id": str(tenant_id)})).json()
        r = await c.post(f"/api/v1/platform/invoices/{inv['id']}/payments",
                         json={"amount": "10.00"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_the_database_refuses_to_edit_an_issued_invoice():
    """Enforced by a trigger, not by the endpoint. "The API checks" is a
    promise every future endpoint has to keep, and one of them will not."""
    tenant_id, _ = await _customer_on_a_plan()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        inv = (await c.post("/api/v1/platform/invoices",
                            json={"tenant_id": str(tenant_id)})).json()
        await c.post(f"/api/v1/platform/invoices/{inv['id']}/status",
                     json={"status": "issued"})

    with pytest.raises(Exception) as caught:
        await _sql("UPDATE platform_invoices SET total_amount = 1 "
                   " WHERE id = CAST(:id AS uuid)", {"id": inv["id"]})
    assert "cannot be edited" in str(caught.value)


@pytest.mark.asyncio
async def test_an_issued_invoice_cannot_be_cancelled():
    """Correcting one the customer has already seen means a credit note, so the
    correction is visible instead of the original quietly disappearing."""
    tenant_id, _ = await _customer_on_a_plan()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        inv = (await c.post("/api/v1/platform/invoices",
                            json={"tenant_id": str(tenant_id)})).json()
        await c.post(f"/api/v1/platform/invoices/{inv['id']}/status",
                     json={"status": "issued"})
        r = await c.post(f"/api/v1/platform/invoices/{inv['id']}/status",
                         json={"status": "cancelled"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_a_draft_can_be_cancelled():
    tenant_id, _ = await _customer_on_a_plan()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        inv = (await c.post("/api/v1/platform/invoices",
                            json={"tenant_id": str(tenant_id)})).json()
        r = await c.post(f"/api/v1/platform/invoices/{inv['id']}/status",
                         json={"status": "cancelled"})
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_invoice_numbers_are_unique():
    """A customer quotes this back on the telephone. Two invoices sharing one
    is a conversation nobody can resolve."""
    tenant_id, _ = await _customer_on_a_plan()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        numbers = []
        for _ in range(3):
            r = await c.post("/api/v1/platform/invoices",
                             json={"tenant_id": str(tenant_id)})
            numbers.append(r.json()["invoice_number"])
    assert len(set(numbers)) == 3, numbers


@pytest.mark.asyncio
async def test_an_invoice_cannot_be_raised_for_the_platform_tenant():
    """The vendor does not invoice itself."""
    tenant_id = uuid.uuid4()
    await _sql("INSERT INTO tenants (id, name, slug, is_platform) "
               "VALUES (:id, 'Seventh AI', :slug, TRUE)",
               {"id": tenant_id, "slug": f"plat-{tenant_id.hex[:10]}"})
    async with await _client(await _token(SUPER_ADMIN)) as c:
        r = await c.post("/api/v1/platform/invoices", json={"tenant_id": str(tenant_id)})
    assert r.status_code == 404


# ─── D. Payments and credit notes ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_part_payment_leaves_the_invoice_unsettled():
    """Paid is a consequence of the money, not a status somebody sets. A flag
    set by hand drifts from what was actually received."""
    tenant_id, _ = await _customer_on_a_plan()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        inv = (await c.post("/api/v1/platform/invoices",
                            json={"tenant_id": str(tenant_id)})).json()
        await c.post(f"/api/v1/platform/invoices/{inv['id']}/status",
                     json={"status": "issued"})
        half = (Decimal(str(inv["total_amount"])) / 2).quantize(Decimal("0.01"))
        r = await c.post(f"/api/v1/platform/invoices/{inv['id']}/payments",
                         json={"amount": str(half)})
    assert r.status_code == 201
    assert r.json()["status"] == "issued"


@pytest.mark.asyncio
async def test_settling_in_full_marks_it_paid():
    tenant_id, _ = await _customer_on_a_plan()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        inv = (await c.post("/api/v1/platform/invoices",
                            json={"tenant_id": str(tenant_id)})).json()
        await c.post(f"/api/v1/platform/invoices/{inv['id']}/status",
                     json={"status": "issued"})
        r = await c.post(f"/api/v1/platform/invoices/{inv['id']}/payments",
                         json={"amount": str(inv["total_amount"])})
        full = (await c.get(f"/api/v1/platform/invoices/{inv['id']}")).json()
    assert r.json()["status"] == "paid"
    assert Decimal(str(full["amount_outstanding"])) == 0


@pytest.mark.asyncio
async def test_a_refund_is_a_negative_payment_not_a_deletion():
    """A part payment, a second one and a refund are three facts. Storing only
    a running total throws away the history that answers "when did they pay"."""
    tenant_id, _ = await _customer_on_a_plan()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        inv = (await c.post("/api/v1/platform/invoices",
                            json={"tenant_id": str(tenant_id)})).json()
        await c.post(f"/api/v1/platform/invoices/{inv['id']}/status",
                     json={"status": "issued"})
        total = Decimal(str(inv["total_amount"]))
        await c.post(f"/api/v1/platform/invoices/{inv['id']}/payments",
                     json={"amount": str(total)})
        await c.post(f"/api/v1/platform/invoices/{inv['id']}/payments",
                     json={"amount": str(-total), "method": "bank_transfer",
                           "notes": "refunded, billed in error"})
        full = (await c.get(f"/api/v1/platform/invoices/{inv['id']}")).json()

    assert len(full["payments"]) == 2, "both movements stay on record"
    assert Decimal(str(full["amount_paid"])) == 0
    assert Decimal(str(full["amount_outstanding"])) == total


@pytest.mark.asyncio
async def test_a_credit_note_reverses_without_erasing():
    """Deleting the original would leave a gap in the numbering and no record
    that anything was corrected — precisely what an auditor looks for."""
    tenant_id, _ = await _customer_on_a_plan()
    async with await _client(await _token(SUPER_ADMIN)) as c:
        inv = (await c.post("/api/v1/platform/invoices",
                            json={"tenant_id": str(tenant_id), "tax_rate": "0.09"})).json()
        await c.post(f"/api/v1/platform/invoices/{inv['id']}/status",
                     json={"status": "issued"})
        note = await c.post(f"/api/v1/platform/invoices/{inv['id']}/credit")

    assert note.status_code == 201, note.text
    assert (Decimal(str(note.json()["total_amount"]))
            == -Decimal(str(inv["total_amount"])))

    original = (await _sql(
        "SELECT status FROM platform_invoices WHERE id = CAST(:id AS uuid)",
        {"id": inv["id"]}))[0][0]
    assert original == "credited", "the original stays, marked"
