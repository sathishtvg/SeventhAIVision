"""History, because a business question is always about a direction.

§20 to §24 want growth, adoption, churn and a health score. Every one of those
is a comparison with a week or a month ago, and nothing in this database
remembers what yesterday looked like. Counting live tells you there are 128
tenants; it cannot tell you whether that is good news.

So a nightly snapshot, and the two rollup tables §29 names.

    platform_usage_daily     one row per customer per day
    platform_usage_monthly   the same, rolled up, kept for years

WHY BOTH. Daily is what a chart of the last quarter is drawn from and it grows
by one row per customer per day — a hundred customers is thirty-six thousand
rows a year, which is nothing now and unpleasant at five years. Monthly is what
survives the pruning, because "how did we grow in 2026" is a question somebody
asks in 2029 and nobody needs the daily detail to answer it.

SNAPSHOTS ARE IDEMPOTENT. The primary key is (tenant, day), and the write is an
upsert. A scheduler that runs twice, a catch-up after an outage, or somebody
re-running it by hand all produce the same row rather than a double count —
which in an analytics table is a lie that survives forever because nobody
recomputes history.

TENANT HEALTH (§23) is stored rather than computed on demand, for one reason:
the interesting version is the trend. A customer at 62% is a fact; a customer
who was at 91% last month is a phone call.

WHAT IS MEASURED IS WHAT EXISTS. Users, sites, cameras, enabled modules,
detections and recording bytes are all countable today. API calls and bandwidth
are not measured anywhere yet, so they are absent rather than zero: a column of
zeroes in an analytics table reads as "nothing happened", which is a different
claim from "nobody counted".

Revision ID: 0113
Revises: 0112
"""
from alembic import op

revision = "0113"
down_revision = "0112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS platform_usage_daily (
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            day             DATE NOT NULL,
            users           INTEGER NOT NULL DEFAULT 0,
            users_active    INTEGER NOT NULL DEFAULT 0,
            sites           INTEGER NOT NULL DEFAULT 0,
            cameras         INTEGER NOT NULL DEFAULT 0,
            cameras_active  INTEGER NOT NULL DEFAULT 0,
            modules_enabled INTEGER NOT NULL DEFAULT 0,
            ai_events       BIGINT  NOT NULL DEFAULT 0,
            storage_bytes   BIGINT  NOT NULL DEFAULT 0,
            recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, day)
        )
    """)
    op.execute("""
        COMMENT ON TABLE platform_usage_daily IS
            'One snapshot per customer per day. The key is (tenant, day) and '
            'writes are upserts, so a rerun corrects rather than double-counts.'
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_daily_day
            ON platform_usage_daily (day DESC)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS platform_usage_monthly (
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            month           DATE NOT NULL,
            users           INTEGER NOT NULL DEFAULT 0,
            sites           INTEGER NOT NULL DEFAULT 0,
            cameras         INTEGER NOT NULL DEFAULT 0,
            modules_enabled INTEGER NOT NULL DEFAULT 0,
            ai_events       BIGINT  NOT NULL DEFAULT 0,
            storage_bytes   BIGINT  NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, month)
        )
    """)
    op.execute("""
        COMMENT ON TABLE platform_usage_monthly IS
            'What survives pruning of the daily table. "How did we grow in '
            '2026" is asked in 2029, and nobody needs the daily detail then.'
    """)

    # ── §23 Tenant health ────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS tenant_health (
            tenant_id      UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            score          INTEGER NOT NULL DEFAULT 0,
            -- The parts, kept separately. A single number tells somebody to
            -- worry; the components tell them what about.
            login_score    INTEGER NOT NULL DEFAULT 0,
            usage_score    INTEGER NOT NULL DEFAULT 0,
            billing_score  INTEGER NOT NULL DEFAULT 0,
            adoption_score INTEGER NOT NULL DEFAULT 0,
            detail         JSONB,
            computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_tenant_health_score CHECK (score BETWEEN 0 AND 100)
        )
    """)
    op.execute("""
        COMMENT ON TABLE tenant_health IS
            'Stored rather than computed on demand, because the interesting '
            'version is the trend: 62%% is a fact, 62%% after 91%% last month '
            'is a phone call.'
    """)

    # ── The snapshot, across tenants ─────────────────────────────────────────
    #
    # users, sites, cameras, licences, detections and recordings are all
    # RLS-protected and the rollup runs unscoped. Fifth time; the rule holds.
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_usage_snapshot()
        RETURNS TABLE (
            tenant_id UUID, users BIGINT, users_active BIGINT, sites BIGINT,
            cameras BIGINT, cameras_active BIGINT, modules_enabled BIGINT,
            ai_events BIGINT, storage_bytes BIGINT
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT t.id,
                   (SELECT count(*) FROM users u WHERE u.tenant_id = t.id),
                   (SELECT count(*) FROM users u
                     WHERE u.tenant_id = t.id
                       AND u.last_login_at >= now() - interval '7 days'),
                   (SELECT count(*) FROM sites s WHERE s.tenant_id = t.id),
                   (SELECT count(*) FROM cameras c WHERE c.tenant_id = t.id),
                   (SELECT count(*) FROM cameras c
                     WHERE c.tenant_id = t.id AND c.is_active),
                   (SELECT count(*) FROM tenant_module_licenses l
                     WHERE l.tenant_id = t.id AND l.is_enabled),
                   -- Yesterday's detections, not all of them: this is a daily
                   -- figure, and a running total would make every chart a
                   -- monotonic line that says nothing.
                   (SELECT count(*) FROM detections d
                     WHERE d.tenant_id = t.id
                       AND d.created_at >= CURRENT_DATE - 1
                       AND d.created_at <  CURRENT_DATE),
                   (SELECT COALESCE(sum(r.file_size_bytes), 0) FROM recordings r
                     WHERE r.tenant_id = t.id)
              FROM tenants t
             WHERE NOT t.is_platform
        $$;
    """)

    # Adoption (§22): how many customers actually switched each module on.
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_module_adoption()
        RETURNS TABLE (
            code VARCHAR, name VARCHAR, billing_type VARCHAR,
            unit_price NUMERIC, tenants BIGINT, cameras BIGINT
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT m.code, m.name, m.billing_type, m.unit_price,
                   count(DISTINCT l.tenant_id),
                   COALESCE(sum((SELECT count(*) FROM cameras c
                                  WHERE c.tenant_id = l.tenant_id)), 0)
              FROM billing_modules m
         LEFT JOIN tenant_module_licenses l
                ON l.module_type = m.code
               AND l.is_enabled
               AND EXISTS (SELECT 1 FROM tenants t
                            WHERE t.id = l.tenant_id AND NOT t.is_platform)
             WHERE m.is_active
          GROUP BY m.code, m.name, m.billing_type, m.unit_price, m.sort_order
          ORDER BY count(DISTINCT l.tenant_id) DESC, m.sort_order
        $$;
    """)

    for fn in ("platform_usage_snapshot()", "platform_module_adoption()"):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS platform_module_adoption()")
    op.execute("DROP FUNCTION IF EXISTS platform_usage_snapshot()")
    op.execute("DROP TABLE IF EXISTS tenant_health")
    op.execute("DROP TABLE IF EXISTS platform_usage_monthly")
    op.execute("DROP TABLE IF EXISTS platform_usage_daily")
