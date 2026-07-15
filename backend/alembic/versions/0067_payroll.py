"""0067 — Payroll / CPF / IR8A module (ShiftSecure Phase 5)

Adds per-employee pay-rate fields (mirrors how bank_name/bank_account_number
were added directly to users in migration 0060, rather than a side table),
a payroll_runs/payslips pair for monthly pay runs, and permissions tighter
than attendance/leave/violations:manage — payroll:manage excludes
supervisor/operator since this touches real money and tax data.

CPF calculation (services/payroll.py) implements standard full-rate
contributions only (Citizens/PRs from 3rd year) against the Ordinary Wage
ceiling; graduated 1st/2nd-year PR rates and the Additional Wage ceiling
are documented v1 limitations, not implemented here.
"""
from __future__ import annotations

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE users
          ADD COLUMN hourly_rate NUMERIC(8,2),
          ADD COLUMN monthly_salary NUMERIC(10,2)
    """)

    op.execute("""
        CREATE TABLE payroll_runs (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            period_start         DATE NOT NULL,
            period_end           DATE NOT NULL,
            status               VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','finalized')),
            generated_by_user_id UUID REFERENCES users(id),
            generated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            finalized_at         TIMESTAMPTZ,
            UNIQUE (tenant_id, period_start, period_end)
        )
    """)
    op.execute("ALTER TABLE payroll_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payroll_runs FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_payroll_runs ON payroll_runs
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        CREATE TABLE payslips (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            payroll_run_id    UUID NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
            guard_user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            regular_hours     NUMERIC(6,2) NOT NULL DEFAULT 0,
            overtime_hours    NUMERIC(6,2) NOT NULL DEFAULT 0,
            base_pay          NUMERIC(10,2) NOT NULL DEFAULT 0,
            overtime_pay      NUMERIC(10,2) NOT NULL DEFAULT 0,
            gross_pay         NUMERIC(10,2) NOT NULL DEFAULT 0,
            cpf_employee      NUMERIC(10,2) NOT NULL DEFAULT 0,
            cpf_employer      NUMERIC(10,2) NOT NULL DEFAULT 0,
            net_pay           NUMERIC(10,2) NOT NULL DEFAULT 0,
            unpaid_leave_days NUMERIC(5,2) NOT NULL DEFAULT 0,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (payroll_run_id, guard_user_id)
        )
    """)
    op.execute("CREATE INDEX idx_payslips_run ON payslips(payroll_run_id)")
    op.execute("CREATE INDEX idx_payslips_guard ON payslips(tenant_id, guard_user_id)")
    op.execute("ALTER TABLE payslips ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payslips FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_payslips ON payslips
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('payroll:read',   'View own payslips; admins view all',        'guard'),
          ('payroll:manage', 'Run payroll, finalize runs, generate IR8A', 'guard')
        ON CONFLICT (code) DO NOTHING;

        -- payroll:manage deliberately excludes supervisor(3)/operator(4) —
        -- real money + tax data, tighter than attendance/leave/violations:manage.
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 8) AND p.code IN ('payroll:read', 'payroll:manage')
        ON CONFLICT DO NOTHING;

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (3, 4, 5, 6) AND p.code = 'payroll:read'
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS payslips")
    op.execute("DROP TABLE IF EXISTS payroll_runs")
    op.execute("""
        ALTER TABLE users
          DROP COLUMN IF EXISTS hourly_rate,
          DROP COLUMN IF EXISTS monthly_salary
    """)
    op.execute("""
        DELETE FROM role_permissions WHERE permission_id IN (
            SELECT id FROM permissions WHERE code IN ('payroll:read', 'payroll:manage')
        )
    """)
    op.execute("DELETE FROM permissions WHERE code IN ('payroll:read', 'payroll:manage')")
