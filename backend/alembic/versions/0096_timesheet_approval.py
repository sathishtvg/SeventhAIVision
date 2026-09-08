"""Put a human between a check-in scan and a payslip.

A payroll run reads shifts where status = 'completed' and computes pay
straight from actual_start and actual_end. Nobody approves anything. A guard
who forgot to check out, a supervisor who corrected the wrong row, a phone
that recorded a scan twice — each becomes real money, and the first anyone
knows is a payslip somebody disputes or an auditor asking who signed it.

attendance_corrections exists but is a queue for fixing individual rows, not a
gate. Nothing establishes that a period's hours were looked at as a whole and
found right.

So a timesheet: one per guard per pay period, holding the hours as they stood
when it was submitted, moving draft -> submitted -> approved or rejected, with
who did it and when.

THE HOURS ARE SNAPSHOTTED, NOT RECOMPUTED. What a supervisor approved must
stay legible afterwards even though shifts keep changing underneath — a
correction filed in week three would otherwise silently alter what was
approved in week one, and the approval would be a signature on a moving
document.

ENFORCEMENT IS OPT-IN, and deliberately so. Turning a hard gate on during an
upgrade would break the next payroll run for every existing tenant, none of
whom have a single timesheet. The setting defaults to off; payroll always
REPORTS which guards have no approved timesheet, so the gap is visible from
the first run, and switching payroll.require_approved_timesheets on makes it
refuse to pay them. Visible by default, blocking by choice.

Revision ID: 0096
Revises: 0095
"""
from alembic import op

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE timesheets (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            guard_user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            period_start    DATE NOT NULL,
            period_end      DATE NOT NULL CHECK (period_end >= period_start),

            -- The hours as they stood when this was submitted. Kept so an
            -- approval remains a signature on a fixed document rather than on
            -- whatever the shifts table happens to say later.
            regular_hours   NUMERIC(8,2) NOT NULL DEFAULT 0,
            overtime_hours  NUMERIC(8,2) NOT NULL DEFAULT 0,
            days_worked     INTEGER NOT NULL DEFAULT 0,
            shift_count     INTEGER NOT NULL DEFAULT 0,

            status          VARCHAR(20) NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'submitted', 'approved', 'rejected')),
            submitted_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            submitted_at    TIMESTAMPTZ,
            reviewed_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            reviewed_at     TIMESTAMPTZ,
            review_notes    TEXT,

            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- One timesheet per guard per period. Two would mean two answers
            -- to "how many hours did this person work", and payroll picking
            -- whichever it read first.
            CONSTRAINT uq_timesheet_period UNIQUE (guard_user_id, period_start, period_end)
        )
    """)
    op.execute("""
        CREATE INDEX idx_timesheets_period
            ON timesheets (tenant_id, period_start, period_end, status)
    """)
    op.execute("""
        CREATE INDEX idx_timesheets_pending
            ON timesheets (tenant_id, status) WHERE status = 'submitted'
    """)
    op.execute("ALTER TABLE timesheets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE timesheets FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_timesheets ON timesheets
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # Which timesheet a payslip was paid from. Nullable, because every payslip
    # generated before this migration was paid from no timesheet at all and
    # inventing a reference for them would be a lie about what was approved.
    op.execute("""
        ALTER TABLE payslips
            ADD COLUMN IF NOT EXISTS timesheet_id UUID
            REFERENCES timesheets(id) ON DELETE SET NULL
    """)

    # Approving hours is a supervisor's job, the same authority that already
    # reviews attendance corrections — not a separate one that would have to be
    # granted again to every role that can already fix a shift.
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('timesheet:read',   'View timesheets and their approval state', 'guard'),
          ('timesheet:manage', 'Submit, approve and reject timesheets', 'guard')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 8) AND p.code IN ('timesheet:read', 'timesheet:manage')
        ON CONFLICT DO NOTHING
    """)
    # Operators and guards can see their own hours but not sign them off.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (4, 5) AND p.code = 'timesheet:read'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions
                                  WHERE code IN ('timesheet:read', 'timesheet:manage'))
    """)
    op.execute("DELETE FROM permissions WHERE code IN ('timesheet:read', 'timesheet:manage')")
    op.execute("ALTER TABLE payslips DROP COLUMN IF EXISTS timesheet_id")
    op.execute("DROP TABLE IF EXISTS timesheets")
