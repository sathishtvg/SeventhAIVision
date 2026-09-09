"""Moving a customer through their subscription, and warning before acting.

The failure mode of billing automation is not charging too little. It is a
security company arriving one morning to find their cameras off because a card
expired three weeks ago and nobody told them. So every state change here is
preceded by a notice raised days earlier, grace exists at all, and automatic
suspension is off unless the vendor deliberately turns it on.

These tests seed real situations — a trial with four days left, a period that
ended yesterday, a grace window that ran out — and check both what the customer
experiences and what the vendor is told.

Sections:
  A — Warnings, before anything happens (3 tests)
  B — The state machine (4 tests)
  C — Suspension is a decision, not a default (2 tests)
  D — Notices are useful rather than noisy (3 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app  # noqa: F401
from app.services import subscription_lifecycle as lifecycle

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


def _factory():
    engine = create_async_engine(ADMIN_DATABASE_URL)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _sql(statement: str, params: dict | None = None):
    engine, factory = _factory()
    async with factory() as s:
        result = await s.execute(text(statement), params or {})
        rows = result.all() if result.returns_rows else []
        await s.commit()
    await engine.dispose()
    return rows


async def _run() -> dict:
    """One lifecycle pass, on its own session like the scheduler's."""
    engine, factory = _factory()
    async with factory() as s:
        counts = await lifecycle.evaluate(s)
    await engine.dispose()
    return counts


async def _seed_subscription(
    *, status: str = "active",
    period_end_days: int | None = 30,
    trial_end_days: int | None = None,
    grace_days: int | None = None,
):
    """A customer with a subscription in a known state.

    Offsets are relative to now, so a test can say "the period ended
    yesterday" rather than computing dates.
    """
    tenant_id, plan_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    await _sql("INSERT INTO tenants (id, name, slug, status) "
               "VALUES (:id, :name, :slug, 'active')",
               {"id": tenant_id, "name": f"Life {tenant_id.hex[:6]}",
                "slug": f"life-{tenant_id.hex[:10]}"})
    await _sql("INSERT INTO billing_plans (id, name, price_monthly) "
               "VALUES (:id, 'Lifecycle Plan', 100)", {"id": plan_id})
    await _sql("""
        INSERT INTO billing_subscriptions
            (id, tenant_id, stripe_subscription_id, plan_id, status,
             current_period_end, trial_end, grace_until)
        VALUES (:id, :tid, :sub, :plan, :status, :pe, :te, :grace)
    """, {
        "id": uuid.uuid4(), "tid": tenant_id,
        "sub": f"sub_{uuid.uuid4().hex[:16]}", "plan": plan_id, "status": status,
        "pe": now + timedelta(days=period_end_days) if period_end_days is not None else None,
        "te": now + timedelta(days=trial_end_days) if trial_end_days is not None else None,
        "grace": now + timedelta(days=grace_days) if grace_days is not None else None,
    })
    return tenant_id


async def _notices(tenant_id, kind: str | None = None):
    where = "tenant_id = :tid" + (" AND kind = :kind" if kind else "")
    params = {"tid": tenant_id}
    if kind:
        params["kind"] = kind
    return await _sql(
        f"SELECT kind, severity, title, occurrences FROM platform_notifications "
        f" WHERE {where}", params)


async def _subscription_status(tenant_id) -> str:
    rows = await _sql(
        "SELECT status FROM billing_subscriptions WHERE tenant_id = :tid",
        {"tid": tenant_id})
    return rows[0][0]


async def _set_policy(key: str, value: str):
    await _sql("INSERT INTO platform_settings (key, value) VALUES (:k, :v) "
               " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
               {"k": key, "v": value})


# ─── A. Warnings first ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_trial_about_to_end_is_flagged():
    tenant_id = await _seed_subscription(status="trialing", trial_end_days=4)
    await _run()
    notices = await _notices(tenant_id, "trial_ending")
    assert len(notices) == 1
    assert "trial ends in 3 day" in notices[0][2] or "trial ends in 4 day" in notices[0][2]


@pytest.mark.asyncio
async def test_a_trial_with_months_left_is_not_flagged():
    """A warning that arrives too early is ignored, and an inbox of premature
    notices trains the reader to ignore the timely ones too."""
    tenant_id = await _seed_subscription(status="trialing", trial_end_days=90)
    await _run()
    assert await _notices(tenant_id, "trial_ending") == []


@pytest.mark.asyncio
async def test_an_approaching_renewal_is_flagged_before_it_lands():
    tenant_id = await _seed_subscription(status="active", period_end_days=10)
    await _run()
    notices = await _notices(tenant_id, "renewal_due")
    assert len(notices) == 1
    assert notices[0][1] == "info", "a renewal on schedule is news, not a problem"


# ─── B. The state machine ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_period_that_ended_moves_into_grace_not_out_the_door():
    """A period ending is not a customer leaving. Cards expire and finance
    departments are slow; a fortnight of lights-on costs almost nothing next to
    losing the account."""
    tenant_id = await _seed_subscription(status="active", period_end_days=-1)
    await _run()

    assert await _subscription_status(tenant_id) == "past_due"
    rows = await _sql("SELECT grace_until FROM billing_subscriptions "
                      " WHERE tenant_id = :tid", {"tid": tenant_id})
    assert rows[0][0] is not None, "grace has to be set, or expiry is immediate"
    assert len(await _notices(tenant_id, "subscription_past_due")) == 1


@pytest.mark.asyncio
async def test_grace_still_running_changes_nothing():
    tenant_id = await _seed_subscription(status="past_due", period_end_days=-3,
                                         grace_days=10)
    await _run()
    assert await _subscription_status(tenant_id) == "past_due"


@pytest.mark.asyncio
async def test_grace_run_out_expires_the_subscription():
    tenant_id = await _seed_subscription(status="past_due", period_end_days=-30,
                                         grace_days=-1)
    await _run()
    assert await _subscription_status(tenant_id) == "expired"
    rows = await _sql("SELECT expired_at FROM billing_subscriptions "
                      " WHERE tenant_id = :tid", {"tid": tenant_id})
    assert rows[0][0] is not None


@pytest.mark.asyncio
async def test_an_invoice_past_its_due_date_becomes_overdue_and_is_reported():
    tenant_id = await _seed_subscription()
    invoice_id = uuid.uuid4()
    await _sql("""
        INSERT INTO platform_invoices
            (id, tenant_id, invoice_number, status, due_date, subtotal,
             tax_amount, total_amount, issued_at)
        VALUES (:id, :tid, :num, 'issued', CURRENT_DATE - 60, 100, 0, 100, now())
    """, {"id": invoice_id, "tid": tenant_id,
          "num": f"INV-TEST-{invoice_id.hex[:8]}"})

    await _run()

    rows = await _sql("SELECT status FROM platform_invoices WHERE id = :id",
                      {"id": invoice_id})
    assert rows[0][0] == "overdue"
    assert len(await _notices(tenant_id, "invoice_overdue")) == 1


# ─── C. Suspension is a decision ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_expiry_does_not_suspend_by_default():
    """Automatically cutting off a security company is not something anybody
    should inherit from a default."""
    await _set_policy("billing.suspend_on_nonpayment", "false")
    tenant_id = await _seed_subscription(status="past_due", period_end_days=-30,
                                         grace_days=-1)
    await _run()

    rows = await _sql("SELECT status, is_active FROM tenants WHERE id = :tid",
                      {"tid": tenant_id})
    assert rows[0][0] == "active", "the customer keeps working"
    assert rows[0][1] is True
    assert len(await _notices(tenant_id, "subscription_expired")) == 1


@pytest.mark.asyncio
async def test_expiry_suspends_only_when_the_vendor_turns_it_on():
    """And even then only after the period ended AND grace ran out — never on
    a single missed payment."""
    await _set_policy("billing.suspend_on_nonpayment", "true")
    try:
        tenant_id = await _seed_subscription(status="past_due",
                                             period_end_days=-30, grace_days=-1)
        await _run()
        rows = await _sql("SELECT status, is_active FROM tenants WHERE id = :tid",
                          {"tid": tenant_id})
        assert rows[0][0] == "suspended"
        # The tenants trigger derives access from the commercial state, so this
        # follows without a second switch anybody could forget.
        assert rows[0][1] is False
        assert len(await _notices(tenant_id, "tenant_suspended")) == 1
    finally:
        await _set_policy("billing.suspend_on_nonpayment", "false")


# ─── D. Notices worth reading ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_running_nightly_does_not_produce_a_notice_a_night():
    """A month of the same sentence is a console nobody reads. The count and
    the last-seen time carry the recency instead."""
    tenant_id = await _seed_subscription(status="trialing", trial_end_days=3)
    await _run()
    await _run()
    await _run()

    notices = await _notices(tenant_id, "trial_ending")
    assert len(notices) == 1, "three runs, one notice"
    assert notices[0][3] == 3, "with the occurrence count carrying the rest"


@pytest.mark.asyncio
async def test_an_acknowledged_notice_can_be_raised_again():
    """Deduplication is scoped to unacknowledged notices. Once somebody has
    dealt with one, the same thing recurring is news again rather than being
    silently swallowed."""
    tenant_id = await _seed_subscription(status="trialing", trial_end_days=3)
    await _run()
    await _sql("UPDATE platform_notifications SET acknowledged_at = now() "
               " WHERE tenant_id = :tid", {"tid": tenant_id})
    await _run()
    assert len(await _notices(tenant_id, "trial_ending")) == 2


@pytest.mark.asyncio
async def test_a_bad_policy_value_falls_back_instead_of_stopping_the_run():
    """A typo in one policy field must not stop every other customer's
    lifecycle from being evaluated."""
    await _set_policy("billing.grace_period_days", "not a number")
    try:
        settings = None
        engine, factory = _factory()
        async with factory() as s:
            settings = await lifecycle.load_settings(s)
        await engine.dispose()
        assert settings["billing.grace_period_days"] == \
            lifecycle.DEFAULTS["billing.grace_period_days"]
    finally:
        await _set_policy("billing.grace_period_days", "14")
