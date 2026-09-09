"""A customer is more than on or off.

`tenants.is_active` is a boolean, and a SaaS business does not have two states.
A trial that has not converted, a customer three weeks into a grace period, and
a company that cancelled last year are all "inactive" under a boolean — which
means the platform owner cannot tell the difference between somebody about to
pay them and somebody who never will. Those are opposite sales situations.

    trial       evaluating, not yet paying
    pending     created, not yet started — provisioning or awaiting signature
    active      paying, in good standing
    suspended   switched off deliberately, usually for non-payment
    expired     the subscription ran out and nobody renewed
    cancelled   they left

is_active IS KEPT AND KEPT AUTHORITATIVE for access. Every existing query that
gates on it — logins, the tenant catalogue, the support-session check — keeps
working untouched, and a status column that quietly became the real switch
would be a security change disguised as a reporting one. status answers "what
is this customer to the business"; is_active answers "can they log in". The
trigger below keeps them from contradicting each other in the direction that
matters: a tenant that is suspended, expired or cancelled cannot be active.

BACKFILL. Everything currently active becomes 'active' and everything inactive
becomes 'suspended', which is the honest reading of a boolean that was never
asked to mean more.

Revision ID: 0105
Revises: 0104
"""
from alembic import op

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None

#: The states in which a tenant must not be able to log in.
BLOCKING_STATUSES = ("suspended", "expired", "cancelled")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active',
            ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS contact_name VARCHAR(200),
            ADD COLUMN IF NOT EXISTS contact_email VARCHAR(255),
            ADD COLUMN IF NOT EXISTS contact_phone VARCHAR(50),
            ADD COLUMN IF NOT EXISTS country VARCHAR(100),
            ADD COLUMN IF NOT EXISTS address TEXT
    """)
    op.execute("""
        UPDATE tenants SET status = 'suspended' WHERE NOT is_active
    """)
    op.execute("""
        ALTER TABLE tenants
            ADD CONSTRAINT ck_tenant_status CHECK (
                status IN ('trial', 'pending', 'active',
                           'suspended', 'expired', 'cancelled')
            )
    """)
    op.execute("""
        COMMENT ON COLUMN tenants.status IS
            'What this customer is to the business. is_active remains the '
            'access switch; this is the commercial state.'
    """)

    # The two must not be able to disagree in the dangerous direction. Reading
    # a status of "cancelled" next to is_active = true would make every
    # revenue figure a lie and, worse, leave a cancelled customer logged in.
    blocking = ", ".join(f"'{s}'" for s in BLOCKING_STATUSES)
    op.execute(f"""
        CREATE OR REPLACE FUNCTION enforce_tenant_status_access() RETURNS trigger AS $$
        BEGIN
            IF NEW.status IN ({blocking}) THEN
                NEW.is_active := FALSE;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_tenant_status_access
            BEFORE INSERT OR UPDATE ON tenants
            FOR EACH ROW EXECUTE FUNCTION enforce_tenant_status_access()
    """)

    # The dashboard counts by status constantly; the table is small but the
    # query runs on every load.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants (status)
    """)

    # The platform's own tenant is not a customer and must never appear in a
    # revenue figure, a churn number or a tenant count.
    op.execute("""
        ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS is_platform BOOLEAN NOT NULL DEFAULT FALSE
    """)
    op.execute("""
        UPDATE tenants SET is_platform = TRUE WHERE slug = 'seventhaivision'
    """)
    op.execute("""
        COMMENT ON COLUMN tenants.is_platform IS
            'The vendor''s own tenant. Excluded from every customer count, '
            'usage figure and revenue total — counting yourself as a customer '
            'is how a dashboard starts lying.'
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_tenant_status_access ON tenants")
    op.execute("DROP FUNCTION IF EXISTS enforce_tenant_status_access()")
    op.execute("ALTER TABLE tenants DROP CONSTRAINT IF EXISTS ck_tenant_status")
    op.execute("""
        ALTER TABLE tenants
            DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS trial_ends_at,
            DROP COLUMN IF EXISTS contact_name,
            DROP COLUMN IF EXISTS contact_email,
            DROP COLUMN IF EXISTS contact_phone,
            DROP COLUMN IF EXISTS country,
            DROP COLUMN IF EXISTS address,
            DROP COLUMN IF EXISTS is_platform
    """)
