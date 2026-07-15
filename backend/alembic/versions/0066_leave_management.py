"""0066 — Leave Management module (ShiftSecure Phase 4)

guard_leave_blocks (migration 0062) was deliberately minimal: a flat
date-range unavailability signal for the roster auto-scheduler, with no
leave type, entitlement, or approval workflow. This migration adds the
real request/approval/balance model on top; guard_leave_blocks becomes a
*derived* table (a row is inserted when a leave request is approved, and
removed if later cancelled) so roster_autoschedule.py's read contract
(is_guard_on_leave reading guard_leave_blocks) needs zero changes.
"""
from __future__ import annotations

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE leave_types (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name                 VARCHAR(100) NOT NULL,
            default_annual_days  INTEGER NOT NULL DEFAULT 0,
            requires_document    BOOLEAN NOT NULL DEFAULT FALSE,
            is_active            BOOLEAN NOT NULL DEFAULT TRUE,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, name)
        )
    """)
    op.execute("ALTER TABLE leave_types ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE leave_types FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_leave_types ON leave_types
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        CREATE TABLE leave_balances (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            guard_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            leave_type_id  UUID NOT NULL REFERENCES leave_types(id) ON DELETE CASCADE,
            year           INTEGER NOT NULL,
            entitled_days  INTEGER NOT NULL,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (guard_user_id, leave_type_id, year)
        )
    """)
    op.execute("ALTER TABLE leave_balances ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE leave_balances FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_leave_balances ON leave_balances
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        CREATE TABLE leave_requests (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            guard_user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            leave_type_id        UUID NOT NULL REFERENCES leave_types(id) ON DELETE RESTRICT,
            start_date           DATE NOT NULL,
            end_date             DATE NOT NULL CHECK (end_date >= start_date),
            days_count           INTEGER NOT NULL,
            reason               TEXT,
            document_path        TEXT,
            status               VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','approved','rejected','cancelled')),
            reviewed_by_user_id  UUID REFERENCES users(id),
            reviewed_at          TIMESTAMPTZ,
            review_notes         TEXT,
            created_by_user_id   UUID REFERENCES users(id),
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_leave_requests_tenant_guard ON leave_requests(tenant_id, guard_user_id)")
    op.execute("CREATE INDEX idx_leave_requests_tenant_status ON leave_requests(tenant_id, status)")
    op.execute("ALTER TABLE leave_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE leave_requests FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_leave_requests ON leave_requests
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        ALTER TABLE guard_leave_blocks
            ADD COLUMN leave_request_id UUID REFERENCES leave_requests(id) ON DELETE CASCADE
    """)

    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('leave:read',    'View leave requests and balances',               'guard'),
          ('leave:request', 'Submit or cancel a leave request',                'guard'),
          ('leave:manage',  'Approve/reject requests, manage types+balances',  'guard')
        ON CONFLICT (code) DO NOTHING;

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 8) AND p.code IN ('leave:read', 'leave:manage', 'leave:request')
        ON CONFLICT DO NOTHING;

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (4, 5) AND p.code IN ('leave:read', 'leave:request')
        ON CONFLICT DO NOTHING;

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 6 AND p.code = 'leave:read'
        ON CONFLICT DO NOTHING;
    """)

    op.execute("""
        INSERT INTO leave_types (tenant_id, name, default_annual_days, requires_document)
        SELECT t.id, v.name, v.days, v.doc
        FROM tenants t
        CROSS JOIN (VALUES
            ('Annual Leave', 14, FALSE),
            ('Medical Leave', 14, TRUE),
            ('Compassionate Leave', 3, FALSE),
            ('Unpaid Leave', 0, FALSE)
        ) AS v(name, days, doc)
        ON CONFLICT (tenant_id, name) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE guard_leave_blocks DROP COLUMN IF EXISTS leave_request_id")
    op.execute("DROP TABLE IF EXISTS leave_requests")
    op.execute("DROP TABLE IF EXISTS leave_balances")
    op.execute("DROP TABLE IF EXISTS leave_types")
    op.execute("""
        DELETE FROM role_permissions WHERE permission_id IN (
            SELECT id FROM permissions WHERE code IN ('leave:read', 'leave:request', 'leave:manage')
        )
    """)
    op.execute("DELETE FROM permissions WHERE code IN ('leave:read', 'leave:request', 'leave:manage')")
