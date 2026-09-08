"""Public holidays and shift allowances — two things pay could not see.

Payroll computed basic plus overtime at 1.5x plus CPF, and nothing else. Two
consequences, both of which underpay real guards on real shifts:

A SHIFT ON A GAZETTED HOLIDAY PAID LIKE A TUESDAY. Under the Employment Act a
covered employee who works a gazetted public holiday is owed an extra day's
salary at the basic rate, or a day off in lieu. The system had no idea which
days those were, so there was nothing to owe it against.

A NIGHT SHIFT PAID LIKE A DAY SHIFT. Night allowances are standard in
Singapore guarding contracts. shift_definitions already describes a shift's
hours, break and overtime eligibility; the money that attaches to standing it
belongs in the same place, so the allowance is a column there rather than a
separate rate table nobody would keep in step.

The holiday calendar is per tenant with a UNIQUE on (tenant, date). Gazetted
dates are national, but the row is tenant-scoped for the same reason every
other table here is: one tenant editing a shared calendar would change what
another tenant's payroll owes.

Dates are NOT seeded. Singapore's gazetted holidays shift each year — several
follow lunar and Islamic calendars — and a list hardcoded here would be
authoritative-looking and wrong the moment it aged. The API seeds a year on
request from a list the operator confirms, which is the only version of this
that stays correct.

Revision ID: 0095
Revises: 0094
"""
from alembic import op

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public_holidays (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            holiday_date DATE NOT NULL,
            name        VARCHAR(120) NOT NULL,
            -- A gazetted holiday carries the Employment Act entitlement. A
            -- company holiday (a founder's day, a shutdown) does not, and
            -- paying the premium on one because it sat in the same table
            -- would be a costly and invisible mistake.
            is_gazetted BOOLEAN NOT NULL DEFAULT TRUE,
            notes       TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_public_holiday_date UNIQUE (tenant_id, holiday_date)
        )
    """)
    op.execute("""
        CREATE INDEX idx_public_holidays_date
            ON public_holidays (tenant_id, holiday_date)
    """)
    op.execute("ALTER TABLE public_holidays ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public_holidays FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_public_holidays ON public_holidays
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # Money that attaches to standing a particular shift, held with the shift.
    op.execute("""
        ALTER TABLE shift_definitions
            ADD COLUMN IF NOT EXISTS allowance_amount NUMERIC(8,2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS allowance_label  VARCHAR(60)
    """)
    op.execute("""
        ALTER TABLE shift_definitions ADD CONSTRAINT ck_shift_allowance_non_negative
            CHECK (allowance_amount >= 0)
    """)

    # Payslips gain their own lines rather than the amounts being folded into
    # gross. A guard querying their pay asks "what was the holiday worth" and
    # "where is my night allowance", and a single number cannot answer either.
    op.execute("""
        ALTER TABLE payslips
            ADD COLUMN IF NOT EXISTS public_holiday_days  NUMERIC(6,2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS public_holiday_pay   NUMERIC(12,2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS allowance_pay        NUMERIC(12,2) NOT NULL DEFAULT 0
    """)

    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('holiday:manage', 'Maintain the public holiday calendar', 'settings')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 8) AND p.code = 'holiday:manage'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'holiday:manage')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'holiday:manage'")
    for col in ("allowance_pay", "public_holiday_pay", "public_holiday_days"):
        op.execute(f"ALTER TABLE payslips DROP COLUMN IF EXISTS {col}")
    op.execute("ALTER TABLE shift_definitions DROP CONSTRAINT IF EXISTS ck_shift_allowance_non_negative")
    op.execute("ALTER TABLE shift_definitions DROP COLUMN IF EXISTS allowance_label")
    op.execute("ALTER TABLE shift_definitions DROP COLUMN IF EXISTS allowance_amount")
    op.execute("DROP TABLE IF EXISTS public_holidays")
