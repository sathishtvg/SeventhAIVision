"""0065 — Violations module (ShiftSecure Phase 3)

Adds a violations table covering both auto-detected conduct issues
(no-show, late check-in, geofence failure, early departure — all derived
from signals shifts.py already computes in Phase 2A) and free-text manual
entries logged by a supervisor/admin. A guard's cumulative points over a
rolling window are surfaced via the new /violations/summary endpoint, not
computed/stored here — points are summed on read, not maintained as a
running total column, so a later waive/dispute never requires a backfill.

Not built here: any escalation/suspension workflow — that's an HR process
this system observes, not automates.
"""
from __future__ import annotations

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE violations (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            guard_user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            shift_id            UUID REFERENCES shifts(id) ON DELETE SET NULL,
            site_id             UUID REFERENCES sites(id) ON DELETE SET NULL,
            violation_type      VARCHAR(30) NOT NULL
                CHECK (violation_type IN ('no_show','late_checkin','geofence_failure','early_departure','manual')),
            description         TEXT,
            points              INTEGER NOT NULL DEFAULT 0,
            status              VARCHAR(20) NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','acknowledged','disputed','waived')),
            is_auto_generated   BOOLEAN NOT NULL DEFAULT FALSE,
            reported_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            reviewed_by_user_id UUID REFERENCES users(id),
            reviewed_at         TIMESTAMPTZ,
            review_notes        TEXT,
            occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_violations_tenant_guard ON violations(tenant_id, guard_user_id)")
    op.execute("CREATE INDEX idx_violations_tenant_status ON violations(tenant_id, status)")
    op.execute("CREATE INDEX idx_violations_shift ON violations(shift_id)")
    op.execute("ALTER TABLE violations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE violations FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_violations ON violations
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('violation:read',   'View violations and guard points summary',              'guard'),
          ('violation:manage', 'Log manual violations and review/waive existing ones',  'guard')
        ON CONFLICT (code) DO NOTHING;

        -- super_admin (1), admin (2), supervisor (3), operator (4), manager (8):
        -- read + manage. Manager (8) is included explicitly here — role 8 only
        -- inherited admin's *existing* grants at migration 0063 (a one-time
        -- clone), it does not automatically pick up new permissions added
        -- afterward.
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 4, 8) AND p.code IN ('violation:read', 'violation:manage')
        ON CONFLICT DO NOTHING;

        -- security_guard (5), viewer (6): read only (a guard sees their own
        -- rows via the router's self-scoping, not a broader grant)
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (5, 6) AND p.code = 'violation:read'
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS violations")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('violation:read', 'violation:manage'))")
    op.execute("DELETE FROM permissions WHERE code IN ('violation:read', 'violation:manage')")
