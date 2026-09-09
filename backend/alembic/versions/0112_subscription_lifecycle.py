"""A subscription has a life, and somebody has to be told about it.

§15 wants trial → active → renewal → grace → expired → suspended → cancelled,
with notice at each turn. §16 wants licensing tied to that state, so a customer
who has stopped paying eventually stops being served. §17 wants the platform
owner told about billing and licence problems and NOT about a customer's
intruder alarm.

Three things here.

GRACE, which billing_subscriptions could not express. A period ending is not
the same as a customer leaving: cards expire, finance departments are slow, and
switching off a security company's cameras the morning after a missed renewal
is a way to lose them permanently. grace_until is how long the lights stay on
after the period ends, and it defaults from a setting rather than being
hard-coded, because the right answer differs by market.

PLATFORM NOTIFICATIONS, which are not platform_errors. An error is something
broken; these are things that happened and need a decision — a trial ending, a
payment overdue, a licence about to lapse. Mixing them would bury one in the
other, and the two are read at different times by people in different moods.

    Deduplicated by (kind, tenant, subject) while unacknowledged, so a nightly
    job that runs for thirty days produces one notice about a trial ending
    rather than thirty. The last_seen_at column carries the recency instead.

PLATFORM SETTINGS, a small key/value table, because every number in the
lifecycle is a policy decision the vendor should be able to change without a
deploy: how long grace lasts, how many days' warning a trial gets, and above
all whether non-payment suspends a customer at all. That last one defaults to
OFF. Automatically cutting off a security company is not a default anybody
should inherit by accident; it is a decision somebody makes deliberately, with
their name on it.

Revision ID: 0112
Revises: 0111
"""
from alembic import op

revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None

#: Conservative on purpose. Suspension is off, and the warning windows are
#: generous — a notice that arrives too early is ignored once; one that arrives
#: too late has already cost the relationship.
DEFAULT_SETTINGS = [
    ("billing.grace_period_days", "14",
     "Days a subscription keeps working after its period ends"),
    ("billing.trial_warning_days", "7",
     "How much notice before a trial ends"),
    ("billing.renewal_warning_days", "30",
     "How much notice before a renewal falls due"),
    ("billing.invoice_overdue_days", "7",
     "Days past the due date before an invoice is called overdue"),
    ("billing.suspend_on_nonpayment", "false",
     "Whether an expired subscription suspends the tenant automatically. Off "
     "by default: cutting off a security company is a decision somebody makes, "
     "not one they inherit."),
    ("billing.module_expiry_warning_days", "14",
     "How much notice before a module licence lapses"),
]


def upgrade() -> None:
    # ── Grace, and the end of the line ───────────────────────────────────────
    op.execute("""
        ALTER TABLE billing_subscriptions
            ADD COLUMN IF NOT EXISTS grace_until TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS expired_at TIMESTAMPTZ
    """)
    op.execute("""
        COMMENT ON COLUMN billing_subscriptions.grace_until IS
            'How long the lights stay on after the period ends. A period ending '
            'is not a customer leaving — cards expire and finance departments '
            'are slow.'
    """)

    # ── Policy, not constants ────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS platform_settings (
            key         VARCHAR(100) PRIMARY KEY,
            value       TEXT        NOT NULL,
            description TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by  UUID
        )
    """)
    op.execute("""
        COMMENT ON TABLE platform_settings IS
            'Vendor policy the lifecycle reads. Every number in it is a '
            'commercial decision that should change without a deploy.'
    """)
    for key, value, description in DEFAULT_SETTINGS:
        escaped = description.replace("'", "''")
        op.execute(f"""
            INSERT INTO platform_settings (key, value, description)
            VALUES ('{key}', '{value}', '{escaped}')
            ON CONFLICT (key) DO NOTHING
        """)

    # ── Things that happened, as opposed to things that broke ────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS platform_notifications (
            id              UUID PRIMARY KEY,
            kind            VARCHAR(50) NOT NULL,
            severity        VARCHAR(20) NOT NULL DEFAULT 'info',
            tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
            -- What within the tenant this is about: a subscription id, a module
            -- code, an invoice number. Part of the deduplication key, so two
            -- modules expiring are two notices and the same one seen nightly
            -- is one.
            subject         VARCHAR(120),
            title           TEXT        NOT NULL,
            detail          JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            occurrences     INTEGER     NOT NULL DEFAULT 1,
            acknowledged_at TIMESTAMPTZ,
            acknowledged_by UUID,
            CONSTRAINT ck_platform_notification_severity
                CHECK (severity IN ('info', 'warning', 'critical'))
        )
    """)
    op.execute("""
        COMMENT ON TABLE platform_notifications IS
            'Business events needing a decision — a trial ending, a payment '
            'overdue, a licence lapsing. Not platform_errors, which is things '
            'that broke; mixing the two buries one in the other.'
    """)
    # One live notice per subject. A nightly job running for thirty days
    # produces one notice about a trial ending, not thirty.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_notification_open
            ON platform_notifications (kind, COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid), COALESCE(subject, ''))
         WHERE acknowledged_at IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_notifications_open
            ON platform_notifications (severity, last_seen_at DESC)
         WHERE acknowledged_at IS NULL
    """)

    # ── Reading subscriptions across tenants ─────────────────────────────────
    #
    # billing_subscriptions is RLS-protected and the lifecycle runs unscoped,
    # so it needs the same treatment as everything else that crosses tenants.
    # Stated for the fourth time because forgetting it does not fail loudly: it
    # returns nothing and looks like a platform with no subscriptions at all.
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_subscription_states()
        RETURNS TABLE (
            subscription_id UUID, tenant_id UUID, tenant_name VARCHAR,
            tenant_status VARCHAR, status VARCHAR, plan_name VARCHAR,
            current_period_end TIMESTAMPTZ, trial_end TIMESTAMPTZ,
            grace_until TIMESTAMPTZ, cancel_at_period_end BOOLEAN
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT s.id, t.id, t.name, t.status, s.status, p.name,
                   s.current_period_end, s.trial_end, s.grace_until,
                   s.cancel_at_period_end
              FROM billing_subscriptions s
              JOIN tenants t ON t.id = s.tenant_id AND NOT t.is_platform
         LEFT JOIN billing_plans p ON p.id = s.plan_id
        $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_expiring_modules(p_days INTEGER)
        RETURNS TABLE (
            tenant_id UUID, tenant_name VARCHAR, module_type VARCHAR,
            expires_at TIMESTAMPTZ
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT t.id, t.name, l.module_type, l.expires_at
              FROM tenant_module_licenses l
              JOIN tenants t ON t.id = l.tenant_id AND NOT t.is_platform
             WHERE l.is_enabled
               AND l.expires_at IS NOT NULL
               AND l.expires_at BETWEEN now() AND now() + make_interval(days => p_days)
          ORDER BY l.expires_at
        $$;
    """)
    # Advancing a subscription's state has to cross tenants too, and a function
    # is the only sanctioned way in.
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_set_subscription_state(
            p_subscription UUID, p_status VARCHAR, p_grace_until TIMESTAMPTZ
        ) RETURNS VOID
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            UPDATE billing_subscriptions
               SET status      = p_status,
                   grace_until = COALESCE(p_grace_until, grace_until),
                   expired_at  = CASE WHEN p_status = 'expired'
                                      THEN COALESCE(expired_at, now()) END
             WHERE id = p_subscription
        $$;
    """)

    for fn in (
        "platform_subscription_states()",
        "platform_expiring_modules(INTEGER)",
        "platform_set_subscription_state(UUID, VARCHAR, TIMESTAMPTZ)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS platform_set_subscription_state(UUID, VARCHAR, TIMESTAMPTZ)")
    op.execute("DROP FUNCTION IF EXISTS platform_expiring_modules(INTEGER)")
    op.execute("DROP FUNCTION IF EXISTS platform_subscription_states()")
    op.execute("DROP TABLE IF EXISTS platform_notifications")
    op.execute("DROP TABLE IF EXISTS platform_settings")
    op.execute("""
        ALTER TABLE billing_subscriptions
            DROP COLUMN IF EXISTS grace_until,
            DROP COLUMN IF EXISTS cancelled_at,
            DROP COLUMN IF EXISTS expired_at
    """)
