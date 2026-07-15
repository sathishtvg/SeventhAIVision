"""Gap 8 — Billing / Stripe integration.

Revision ID: 0045
Revises: 0044
Create Date: 2026-07-02
"""
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── billing_plans (global — no tenant_id, no RLS) ─────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS billing_plans (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            stripe_price_id   VARCHAR(100),
            stripe_product_id VARCHAR(100),
            name              VARCHAR(100) NOT NULL,
            description       TEXT,
            price_monthly     NUMERIC(10,2),
            price_yearly      NUMERIC(10,2),
            max_cameras       INTEGER,
            max_sites         INTEGER,
            max_users         INTEGER,
            ai_modules_allowed JSONB NOT NULL DEFAULT '[]',
            is_active         BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order        INTEGER NOT NULL DEFAULT 0,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── billing_customers (tenant-scoped, no RLS — webhook needs cross-tenant lookup) ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS billing_customers (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            stripe_customer_id VARCHAR(100) NOT NULL UNIQUE,
            email              VARCHAR(255),
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_billing_customers_tenant ON billing_customers(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_billing_customers_stripe_id ON billing_customers(stripe_customer_id)")

    # ── billing_subscriptions (tenant-scoped, no RLS — webhook queries cross-tenant) ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS billing_subscriptions (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            stripe_subscription_id  VARCHAR(100) NOT NULL UNIQUE,
            stripe_price_id         VARCHAR(100),
            plan_id                 UUID REFERENCES billing_plans(id),
            status                  VARCHAR(30) NOT NULL DEFAULT 'active',
            current_period_start    TIMESTAMPTZ,
            current_period_end      TIMESTAMPTZ,
            cancel_at_period_end    BOOLEAN NOT NULL DEFAULT FALSE,
            trial_end               TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_billing_subscriptions_tenant ON billing_subscriptions(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_billing_subscriptions_stripe_id ON billing_subscriptions(stripe_subscription_id)")

    # ── billing_invoices (tenant-scoped, no RLS — webhook queries cross-tenant) ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS billing_invoices (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id              UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            stripe_invoice_id      VARCHAR(100) NOT NULL UNIQUE,
            stripe_subscription_id VARCHAR(100),
            amount_due             NUMERIC(10,2),
            amount_paid            NUMERIC(10,2),
            currency               VARCHAR(10) NOT NULL DEFAULT 'usd',
            status                 VARCHAR(30) NOT NULL,
            invoice_pdf            VARCHAR(500),
            hosted_invoice_url     VARCHAR(500),
            period_start           TIMESTAMPTZ,
            period_end             TIMESTAMPTZ,
            paid_at                TIMESTAMPTZ,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_billing_invoices_tenant ON billing_invoices(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_billing_invoices_stripe_sub ON billing_invoices(stripe_subscription_id)")

    # ── billing_webhook_events (global — idempotency log, no RLS) ─────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS billing_webhook_events (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            stripe_event_id VARCHAR(100) NOT NULL UNIQUE,
            event_type      VARCHAR(100) NOT NULL,
            processed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            payload         JSONB
        )
    """)

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
            ('billing:read',   'View subscription status and invoice history', 'billing'),
            ('billing:manage', 'Create Stripe checkout and customer portal sessions', 'billing')
        ON CONFLICT (code) DO NOTHING
    """)
    # billing:manage → super_admin (1), admin (2)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM (VALUES (1), (2)) AS r(id)
        CROSS JOIN (SELECT id FROM permissions WHERE code = 'billing:manage') p
        ON CONFLICT DO NOTHING
    """)
    # billing:read → super_admin (1), admin (2), supervisor (3)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM (VALUES (1), (2), (3)) AS r(id)
        CROSS JOIN (SELECT id FROM permissions WHERE code = 'billing:read') p
        ON CONFLICT DO NOTHING
    """)

    # ── seed plans ────────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO billing_plans
            (name, description, price_monthly, max_cameras, max_sites, max_users,
             ai_modules_allowed, sort_order)
        VALUES
            ('Starter',
             'Up to 5 cameras, 1 site, 5 users and 3 AI modules',
             299.00, 5, 1, 5,
             '["lpr","face","intrusion"]', 1),
            ('Professional',
             'Up to 25 cameras, 3 sites, 20 users and 8 AI modules',
             799.00, 25, 3, 20,
             '["lpr","face","intrusion","ppe","crowd","fire_smoke","weapon","behavior"]', 2),
            ('Enterprise',
             'Unlimited cameras, sites, users and all 11 AI modules',
             NULL, NULL, NULL, NULL,
             '["lpr","face","intrusion","ppe","crowd","fire_smoke","weapon","behavior","tampering","abandoned","fall"]',
             3)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS billing_webhook_events")
    op.execute("DROP TABLE IF EXISTS billing_invoices")
    op.execute("DROP TABLE IF EXISTS billing_subscriptions")
    op.execute("DROP TABLE IF EXISTS billing_customers")
    op.execute("DROP TABLE IF EXISTS billing_plans")
    op.execute("""
        DELETE FROM role_permissions WHERE permission_id IN (
            SELECT id FROM permissions WHERE code IN ('billing:read','billing:manage')
        )
    """)
    op.execute("DELETE FROM permissions WHERE code IN ('billing:read','billing:manage')")
