"""0074 — RLS on billing + SCIM tables (gap-scan finding)

billing_customers/billing_subscriptions/billing_invoices (0045) and
scim_tokens/scim_sync_log (0046) were deliberately left without RLS because
their entry points (Stripe webhook, SCIM bearer token) must resolve which
tenant an external identifier belongs to before app.current_tenant can be
set — the same "chicken and egg" problem already solved elsewhere in this
schema (ip_allowlist via get_tenant_ip_allowlist, alarm_panels via
lookup_alarm_panel_by_key) with a SECURITY DEFINER lookup function that
bypasses RLS for exactly that one pre-tenant-known query. This migration
applies the identical, already-proven pattern here instead of leaving these
5 tables with no database-level backstop at all.

Revision ID: 0074
Revises: 0073
"""
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None

_RLS = """
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_{t} ON {t}
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
"""


def upgrade() -> None:
    for t in ("billing_customers", "billing_subscriptions", "billing_invoices",
              "scim_tokens", "scim_sync_log"):
        op.execute(_RLS.format(t=t))

    # Stripe webhook: resolve tenant_id from a stripe_customer_id before the
    # GUC is set (billing.py's _handle_subscription_upsert / _handle_invoice_upsert).
    op.execute("""
        CREATE OR REPLACE FUNCTION lookup_billing_tenant_by_customer(p_stripe_customer_id TEXT)
        RETURNS UUID
        LANGUAGE sql
        SECURITY DEFINER
        AS $$
            SELECT tenant_id FROM billing_customers
            WHERE stripe_customer_id = p_stripe_customer_id;
        $$;
    """)
    op.execute("GRANT EXECUTE ON FUNCTION lookup_billing_tenant_by_customer(TEXT) TO svc_app")

    # Same, keyed by stripe_subscription_id (billing.py's _handle_invoice_upsert /
    # _handle_subscription_deleted / _handle_invoice_failed all need this before
    # they know which tenant's subscription row to touch).
    op.execute("""
        CREATE OR REPLACE FUNCTION lookup_billing_tenant_by_subscription(p_stripe_subscription_id TEXT)
        RETURNS UUID
        LANGUAGE sql
        SECURITY DEFINER
        AS $$
            SELECT tenant_id FROM billing_subscriptions
            WHERE stripe_subscription_id = p_stripe_subscription_id;
        $$;
    """)
    op.execute("GRANT EXECUTE ON FUNCTION lookup_billing_tenant_by_subscription(TEXT) TO svc_app")

    # SCIM Bearer-token auth: the very first query of every SCIM request, run
    # before we know which tenant's token this is (scim.py's _get_scim_context).
    op.execute("""
        CREATE OR REPLACE FUNCTION lookup_scim_token(p_token_hash TEXT)
        RETURNS TABLE (id UUID, tenant_id UUID, is_active BOOLEAN, expires_at TIMESTAMPTZ)
        LANGUAGE sql
        SECURITY DEFINER
        AS $$
            SELECT id, tenant_id, is_active, expires_at FROM scim_tokens
            WHERE token_hash = p_token_hash AND is_active = TRUE;
        $$;
    """)
    op.execute("GRANT EXECUTE ON FUNCTION lookup_scim_token(TEXT) TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS lookup_scim_token(TEXT)")
    op.execute("DROP FUNCTION IF EXISTS lookup_billing_tenant_by_subscription(TEXT)")
    op.execute("DROP FUNCTION IF EXISTS lookup_billing_tenant_by_customer(TEXT)")
    for t in ("billing_customers", "billing_subscriptions", "billing_invoices",
              "scim_tokens", "scim_sync_log"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{t} ON {t}")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
