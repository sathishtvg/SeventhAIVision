"""Counting across tenants, without a hole to count through.

The platform console has to answer "how many users does each customer have".
Every table it needs — users, sites, cameras, licences, subscriptions — has
FORCE ROW LEVEL SECURITY, and svc_app does not have BYPASSRLS. So the console's
queries, run the ordinary way, return zero. Correctly: that is tenant isolation
doing its job.

THREE WAYS OUT, AND WHY THIS ONE.

  Grant svc_app BYPASSRLS. One line, and it disables tenant isolation for the
  entire application forever. Not a trade-off, just a mistake.

  Add a bypass clause to every table's policy — "or when the platform GUC is
  set". Touches every policy in the schema, and it puts the exemption in
  fifty places where the next person has to notice all of them.

  SECURITY DEFINER functions, which is what this does, and what the codebase
  already does for get_tenant_ip_allowlist. The exemption lives in one file,
  each function returns AGGREGATES rather than rows, and the surface is exactly
  what is written below and nothing else.

WHAT THESE CANNOT DO IS LEAK A RECORD. Every function returns counts, sums and
tenant names. None returns a guard's name, an address, a pay rate or a camera
feed. A platform owner who wants to look at a customer's actual data still has
to open a support session and be logged doing it (migration 0102). Knowing that
ABC Security has 48 users is running a business; reading those 48 people's
records is not, and these functions cannot be talked into the second.

LOCKED DOWN THE WAY SECURITY DEFINER MUST BE. search_path is pinned so nothing
can be shadowed by a schema earlier on the path, EXECUTE is revoked from PUBLIC
and granted only to svc_app, and the API layer additionally gates every caller
on platform:read, which Super Admin alone holds.

Revision ID: 0106
Revises: 0105
"""
from alembic import op

revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── The KPI row ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_kpis()
        RETURNS TABLE (
            tenants_total BIGINT, tenants_active BIGINT, tenants_trial BIGINT,
            tenants_suspended BIGINT, tenants_expired BIGINT,
            tenants_new_this_month BIGINT,
            users_total BIGINT, users_active BIGINT, users_new_this_month BIGINT,
            users_active_today BIGINT,
            sites_total BIGINT, sites_active BIGINT, sites_new_this_month BIGINT,
            cameras_total BIGINT, cameras_active BIGINT,
            modules_licensed BIGINT, modules_active BIGINT
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            WITH c AS (SELECT * FROM tenants WHERE NOT is_platform)
            SELECT
              (SELECT count(*) FROM c),
              (SELECT count(*) FROM c WHERE status = 'active'),
              (SELECT count(*) FROM c WHERE status = 'trial'),
              (SELECT count(*) FROM c WHERE status = 'suspended'),
              (SELECT count(*) FROM c WHERE status = 'expired'),
              (SELECT count(*) FROM c WHERE created_at >= date_trunc('month', now())),
              (SELECT count(*) FROM users u JOIN c ON c.id = u.tenant_id),
              (SELECT count(*) FROM users u JOIN c ON c.id = u.tenant_id WHERE u.is_active),
              (SELECT count(*) FROM users u JOIN c ON c.id = u.tenant_id
                WHERE u.created_at >= date_trunc('month', now())),
              (SELECT count(*) FROM users u JOIN c ON c.id = u.tenant_id
                WHERE u.last_login_at >= date_trunc('day', now())),
              (SELECT count(*) FROM sites s JOIN c ON c.id = s.tenant_id),
              (SELECT count(*) FROM sites s JOIN c ON c.id = s.tenant_id WHERE s.is_active),
              (SELECT count(*) FROM sites s JOIN c ON c.id = s.tenant_id
                WHERE s.created_at >= date_trunc('month', now())),
              (SELECT count(*) FROM cameras k JOIN c ON c.id = k.tenant_id),
              (SELECT count(*) FROM cameras k JOIN c ON c.id = k.tenant_id WHERE k.is_active),
              (SELECT count(*) FROM tenant_module_licenses l JOIN c ON c.id = l.tenant_id),
              (SELECT count(*) FROM tenant_module_licenses l JOIN c ON c.id = l.tenant_id
                WHERE l.is_enabled)
        $$;
    """)

    # ── One row per customer ─────────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_tenant_usage()
        RETURNS TABLE (
            id UUID, name VARCHAR, slug VARCHAR, status VARCHAR,
            is_active BOOLEAN, created_at TIMESTAMPTZ,
            contact_name VARCHAR, contact_email VARCHAR,
            trial_ends_at TIMESTAMPTZ,
            users BIGINT, users_active BIGINT, sites BIGINT, cameras BIGINT,
            modules BIGINT, last_login_at TIMESTAMPTZ,
            subscription_status VARCHAR, renews_at TIMESTAMPTZ, plan_name VARCHAR
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            -- Correlated subqueries rather than a fan of joins: joining users,
            -- sites and cameras in one statement multiplies the rows and every
            -- count comes out wrong.
            SELECT t.id, t.name, t.slug, t.status, t.is_active, t.created_at,
                   t.contact_name, t.contact_email, t.trial_ends_at,
                   (SELECT count(*) FROM users u WHERE u.tenant_id = t.id),
                   (SELECT count(*) FROM users u WHERE u.tenant_id = t.id AND u.is_active),
                   (SELECT count(*) FROM sites s WHERE s.tenant_id = t.id),
                   (SELECT count(*) FROM cameras k WHERE k.tenant_id = t.id),
                   (SELECT count(*) FROM tenant_module_licenses l
                     WHERE l.tenant_id = t.id AND l.is_enabled),
                   (SELECT max(u.last_login_at) FROM users u WHERE u.tenant_id = t.id),
                   sub.status, sub.current_period_end, plan.name
              FROM tenants t
         LEFT JOIN billing_subscriptions sub ON sub.tenant_id = t.id
         LEFT JOIN billing_plans plan ON plan.id = sub.plan_id
             WHERE NOT t.is_platform
          ORDER BY t.name
        $$;
    """)

    # ── One customer's user statistics ───────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_tenant_user_stats(p_tenant UUID)
        RETURNS TABLE (
            total BIGINT, active BIGINT, inactive BIGINT,
            created_this_month BIGINT, active_last_7_days BIGINT,
            last_login_at TIMESTAMPTZ
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT count(*),
                   count(*) FILTER (WHERE is_active),
                   count(*) FILTER (WHERE NOT is_active),
                   count(*) FILTER (WHERE created_at >= date_trunc('month', now())),
                   count(*) FILTER (WHERE last_login_at >= now() - interval '7 days'),
                   max(last_login_at)
              FROM users WHERE tenant_id = p_tenant
        $$;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION platform_tenant_role_counts(p_tenant UUID)
        RETURNS TABLE (role_id SMALLINT, role_name VARCHAR, count BIGINT)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT u.role_id, r.name, count(*)
              FROM users u
         LEFT JOIN roles r ON r.id = u.role_id
             WHERE u.tenant_id = p_tenant
          GROUP BY u.role_id, r.name
          ORDER BY u.role_id
        $$;
    """)

    # ── One customer in detail ───────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_tenant_counts(p_tenant UUID)
        RETURNS TABLE (
            users BIGINT, sites BIGINT, cameras BIGINT,
            cameras_active BIGINT, recordings BIGINT
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT (SELECT count(*) FROM users   WHERE tenant_id = p_tenant),
                   (SELECT count(*) FROM sites   WHERE tenant_id = p_tenant),
                   (SELECT count(*) FROM cameras WHERE tenant_id = p_tenant),
                   (SELECT count(*) FROM cameras WHERE tenant_id = p_tenant AND is_active),
                   (SELECT count(*) FROM recordings WHERE tenant_id = p_tenant)
        $$;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION platform_tenant_modules(p_tenant UUID)
        RETURNS TABLE (
            module_type VARCHAR, is_enabled BOOLEAN,
            max_cameras INTEGER, expires_at TIMESTAMPTZ
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT module_type, is_enabled, max_cameras, expires_at
              FROM tenant_module_licenses
             WHERE tenant_id = p_tenant
          ORDER BY module_type
        $$;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION platform_tenant_subscription(p_tenant UUID)
        RETURNS TABLE (
            status VARCHAR, current_period_start TIMESTAMPTZ,
            current_period_end TIMESTAMPTZ, cancel_at_period_end BOOLEAN,
            trial_end TIMESTAMPTZ, plan_name VARCHAR,
            price_monthly NUMERIC, price_yearly NUMERIC,
            max_cameras INTEGER, max_sites INTEGER, max_users INTEGER
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT s.status, s.current_period_start, s.current_period_end,
                   s.cancel_at_period_end, s.trial_end,
                   p.name, p.price_monthly, p.price_yearly,
                   p.max_cameras, p.max_sites, p.max_users
              FROM billing_subscriptions s
         LEFT JOIN billing_plans p ON p.id = s.plan_id
             WHERE s.tenant_id = p_tenant
          ORDER BY s.created_at DESC LIMIT 1
        $$;
    """)

    # ── What the customers are worth ─────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_revenue()
        RETURNS TABLE (
            mrr NUMERIC, subscriptions_active BIGINT,
            subscriptions_trial BIGINT, renewals_next_30_days BIGINT
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            -- Yearly prices divided by twelve so annual and monthly customers
            -- land in the same figure. An MRR that mixes the two is not an MRR.
            WITH live AS (
                SELECT s.status, s.current_period_end, p.price_monthly, p.price_yearly
                  FROM billing_subscriptions s
                  JOIN tenants t ON t.id = s.tenant_id AND NOT t.is_platform
             LEFT JOIN billing_plans p ON p.id = s.plan_id
                 WHERE s.status IN ('active', 'trialing')
            )
            SELECT COALESCE(SUM(
                       CASE WHEN price_monthly IS NOT NULL THEN price_monthly
                            WHEN price_yearly  IS NOT NULL THEN price_yearly / 12
                            ELSE 0 END), 0),
                   count(*) FILTER (WHERE status = 'active'),
                   count(*) FILTER (WHERE status = 'trialing'),
                   count(*) FILTER (WHERE status = 'active'
                       AND current_period_end BETWEEN now() AND now() + interval '30 days')
              FROM live
        $$;
    """)

    # ── Locked down ──────────────────────────────────────────────────────────
    for fn in (
        "platform_kpis()",
        "platform_tenant_usage()",
        "platform_tenant_user_stats(UUID)",
        "platform_tenant_role_counts(UUID)",
        "platform_tenant_counts(UUID)",
        "platform_tenant_modules(UUID)",
        "platform_tenant_subscription(UUID)",
        "platform_revenue()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO svc_app")


def downgrade() -> None:
    for fn in (
        "platform_kpis()",
        "platform_tenant_usage()",
        "platform_tenant_user_stats(UUID)",
        "platform_tenant_role_counts(UUID)",
        "platform_tenant_counts(UUID)",
        "platform_tenant_modules(UUID)",
        "platform_tenant_subscription(UUID)",
        "platform_revenue()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
