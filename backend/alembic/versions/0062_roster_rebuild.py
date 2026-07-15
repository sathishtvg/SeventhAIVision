"""0062 — Roster rebuild: shift types, leave blocks, preferences, AI
auto-scheduler draft/publish staging (ShiftSecure Phase 2B)

Draft output from the auto-scheduler lives in roster_batches/
roster_draft_shifts, structurally separate from the live `shifts` table —
this means zero changes to any already-shipped shift-reading endpoint
(list_shifts, roster_coverage, attendance/live); publishing is an explicit
INSERT FROM the draft table into `shifts`.

guard_leave_blocks is intentionally minimal (date range + reason only) —
a precursor to the richer Leave Management module (Phase 4), not a
replacement for it. It exists now only so the auto-scheduler's "respect
leave" rule has real data to read.
"""
from __future__ import annotations

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE shifts ADD COLUMN shift_type VARCHAR(10) "
        "CHECK (shift_type IN ('day','night','split'))"
    )
    op.execute("ALTER TABLE sites ADD COLUMN min_guards_per_shift INTEGER")

    op.execute("""
        CREATE TABLE guard_leave_blocks (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            guard_user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            start_date          DATE NOT NULL,
            end_date            DATE NOT NULL CHECK (end_date >= start_date),
            reason              TEXT,
            created_by_user_id  UUID REFERENCES users(id),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX idx_guard_leave_blocks_guard ON guard_leave_blocks(guard_user_id, start_date, end_date)"
    )
    op.execute("ALTER TABLE guard_leave_blocks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE guard_leave_blocks FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_guard_leave_blocks ON guard_leave_blocks
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        CREATE TABLE guard_shift_preferences (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            guard_user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE UNIQUE,
            preferred_shift_type  VARCHAR(10) CHECK (preferred_shift_type IN ('day','night')),
            preferred_off_days    INTEGER[],
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("ALTER TABLE guard_shift_preferences ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE guard_shift_preferences FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_guard_shift_preferences ON guard_shift_preferences
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        CREATE TABLE roster_batches (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id               UUID REFERENCES sites(id) ON DELETE SET NULL,
            period_start          DATE NOT NULL,
            period_end            DATE NOT NULL,
            status                VARCHAR(10) NOT NULL DEFAULT 'draft'
                                      CHECK (status IN ('draft','published','discarded')),
            rules_summary         JSONB,
            generated_by_user_id  UUID REFERENCES users(id),
            generated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            published_by_user_id  UUID REFERENCES users(id),
            published_at          TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX idx_roster_batches_tenant ON roster_batches(tenant_id, status)")
    op.execute("ALTER TABLE roster_batches ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE roster_batches FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_roster_batches ON roster_batches
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        CREATE TABLE roster_draft_shifts (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            batch_id         UUID NOT NULL REFERENCES roster_batches(id) ON DELETE CASCADE,
            guard_user_id    UUID REFERENCES users(id),
            site_id          UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            scheduled_start  TIMESTAMPTZ NOT NULL,
            scheduled_end    TIMESTAMPTZ NOT NULL,
            shift_type       VARCHAR(10) NOT NULL CHECK (shift_type IN ('day','night','split')),
            warnings         JSONB NOT NULL DEFAULT '[]'
        )
    """)
    op.execute("CREATE INDEX idx_roster_draft_shifts_batch ON roster_draft_shifts(batch_id)")
    op.execute("ALTER TABLE roster_draft_shifts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE roster_draft_shifts FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_roster_draft_shifts ON roster_draft_shifts
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('roster:autoschedule', 'Run AI auto-scheduler and manage roster drafts', 'guard')
        ON CONFLICT (code) DO NOTHING;

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3) AND p.code = 'roster:autoschedule'
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS roster_draft_shifts")
    op.execute("DROP TABLE IF EXISTS roster_batches")
    op.execute("DROP TABLE IF EXISTS guard_shift_preferences")
    op.execute("DROP TABLE IF EXISTS guard_leave_blocks")
    op.execute("ALTER TABLE sites DROP COLUMN IF EXISTS min_guards_per_shift")
    op.execute("ALTER TABLE shifts DROP COLUMN IF EXISTS shift_type")
