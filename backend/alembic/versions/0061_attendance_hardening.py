"""0061 — Attendance hardening: geofence, breaks, late/OT, corrections
(ShiftSecure Phase 2A)

Adds the pieces ShiftSecure's Attendance module needs that shifts.py's
existing check-in/out lifecycle didn't have: a per-site geofence radius,
computed is_within_geofence/is_late/late_minutes/overtime_minutes on each
shift, a shift_breaks table (a shift can have zero or more breaks), and an
attendance_corrections table for the guard-submits/admin-approves workflow.

Not built here: violation generation on failed geofence — that's Phase 3
(Violations) once the violations table exists; this migration only makes
the underlying data queryable.
"""
from __future__ import annotations

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sites ADD COLUMN geofence_radius_meters INTEGER")

    op.execute("""
        ALTER TABLE shifts
          ADD COLUMN is_within_geofence BOOLEAN,
          ADD COLUMN is_late BOOLEAN NOT NULL DEFAULT FALSE,
          ADD COLUMN late_minutes INTEGER,
          ADD COLUMN overtime_minutes INTEGER
    """)

    op.execute("""
        CREATE TABLE shift_breaks (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            shift_id    UUID NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
            break_start TIMESTAMPTZ NOT NULL DEFAULT now(),
            break_end   TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_shift_breaks_shift ON shift_breaks(shift_id)")
    op.execute("ALTER TABLE shift_breaks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shift_breaks FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_shift_breaks ON shift_breaks
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        CREATE TABLE attendance_corrections (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            shift_id              UUID NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
            guard_user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            requested_check_in    TIMESTAMPTZ,
            requested_check_out   TIMESTAMPTZ,
            reason                TEXT NOT NULL,
            status                VARCHAR(20) NOT NULL DEFAULT 'pending'
                                      CHECK (status IN ('pending','approved','rejected')),
            reviewed_by_user_id   UUID REFERENCES users(id),
            reviewed_at           TIMESTAMPTZ,
            review_notes          TEXT,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_attendance_corrections_shift ON attendance_corrections(shift_id)")
    op.execute("CREATE INDEX idx_attendance_corrections_status ON attendance_corrections(tenant_id, status)")
    op.execute("ALTER TABLE attendance_corrections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE attendance_corrections FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_attendance_corrections ON attendance_corrections
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('attendance:read',    'View live attendance monitor and corrections', 'guard'),
          ('attendance:request', 'Request an attendance correction',              'guard'),
          ('attendance:manage',  'Approve / reject attendance corrections',       'guard')
        ON CONFLICT (code) DO NOTHING;

        -- super_admin (1), admin (2), supervisor (3): read + manage + request
        -- (request is granted here too so an admin/supervisor can file a
        -- correction on a guard's behalf, per attendance.py's
        -- request_correction logic, which already allows roles 1-3 to
        -- target any shift, not just their own)
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3) AND p.code IN ('attendance:read', 'attendance:manage', 'attendance:request')
        ON CONFLICT DO NOTHING;

        -- operator (4), security_guard (5): read + request
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (4, 5) AND p.code IN ('attendance:read', 'attendance:request')
        ON CONFLICT DO NOTHING;

        -- viewer (6): read only
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 6 AND p.code = 'attendance:read'
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS attendance_corrections")
    op.execute("DROP TABLE IF EXISTS shift_breaks")
    op.execute("""
        ALTER TABLE shifts
          DROP COLUMN IF EXISTS is_within_geofence,
          DROP COLUMN IF EXISTS is_late,
          DROP COLUMN IF EXISTS late_minutes,
          DROP COLUMN IF EXISTS overtime_minutes
    """)
    op.execute("ALTER TABLE sites DROP COLUMN IF EXISTS geofence_radius_meters")
