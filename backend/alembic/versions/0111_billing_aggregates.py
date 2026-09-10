"""Pricing could not see what it was pricing.

The engine asked billing_subscriptions for the customer's plan and
tenant_module_licenses for what they have switched on. Both have row level
security; the platform console runs unscoped on a sentinel tenant. So both
came back empty, every quote priced at "No plan", and every invoice raised was
for nothing at all.

Which is the third time this has bitten in this feature — the recording health
probe and the console counts before it — so it is worth stating as a rule
rather than a fix:

    ANY cross-tenant read of an RLS-protected table goes through a
    SECURITY DEFINER function. There is no other sanctioned route, and code
    that forgets does not fail loudly: it returns nothing and reports it as
    a legitimate zero.

That silence is what makes it dangerous. A permission error would have been
caught in a minute; an invoice for $0.00 looks like a customer who owes
nothing.

The two functions here return one customer's commercial terms — a plan row and
their enabled modules with the price that applies to them. Locked down the same
way as migration 0106: pinned search_path, EXECUTE revoked from PUBLIC and
granted to svc_app alone, with billing:read or billing:manage gating every
caller above.

Revision ID: 0111
Revises: 0110
"""
from alembic import op

revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_tenant_plan(p_tenant UUID)
        RETURNS TABLE (
            plan_name VARCHAR, base_price NUMERIC,
            included_cameras INTEGER, included_sites INTEGER,
            included_users INTEGER, included_storage_gb INTEGER,
            price_per_camera NUMERIC, price_per_site NUMERIC,
            price_per_user NUMERIC, price_per_gb NUMERIC,
            billing_cycle VARCHAR, subscription_status VARCHAR
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT p.name, COALESCE(p.price_monthly, 0),
                   p.included_cameras, p.included_sites,
                   p.included_users, p.included_storage_gb,
                   p.price_per_camera, p.price_per_site,
                   p.price_per_user, p.price_per_gb,
                   p.billing_cycle, s.status
              FROM billing_subscriptions s
              JOIN billing_plans p ON p.id = s.plan_id
             WHERE s.tenant_id = p_tenant
               AND s.status IN ('active', 'trialing')
          ORDER BY s.created_at DESC
             LIMIT 1
        $$;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION platform_tenant_billable_modules(p_tenant UUID)
        RETURNS TABLE (
            code VARCHAR, name VARCHAR, billing_type VARCHAR, unit_price NUMERIC
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            -- The tenant's own negotiated price wins over the catalogue's.
            -- COALESCE rather than a CASE so "no special arrangement" is the
            -- absence of a value rather than a magic number.
            SELECT l.module_type, m.name, m.billing_type,
                   COALESCE(l.price_override, m.unit_price)
              FROM tenant_module_licenses l
              JOIN billing_modules m ON m.code = l.module_type
             WHERE l.tenant_id = p_tenant
               AND l.is_enabled
               AND m.is_active
               AND (l.expires_at IS NULL OR l.expires_at > now())
          ORDER BY m.sort_order
        $$;
    """)

    for fn in ("platform_tenant_plan(UUID)", "platform_tenant_billable_modules(UUID)"):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS platform_tenant_plan(UUID)")
    op.execute("DROP FUNCTION IF EXISTS platform_tenant_billable_modules(UUID)")
