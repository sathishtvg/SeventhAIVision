"""0055 — Guard roster: recurring shift patterns (Gap 86)

shift_patterns defines a weekly recurring roster line (guard × site ×
days-of-week × start time × duration). Generation expands active patterns
into concrete shifts rows for the next N days; shifts.pattern_id +
a unique (pattern_id, scheduled_start) index make generation idempotent
(re-runs and overlapping windows never duplicate a shift). Manual one-off
shifts keep pattern_id NULL and are unaffected.

days_of_week uses 0=Monday .. 6=Sunday (ISO weekday - 1). start_time is
interpreted in the tenant's timezone at generation time, so an 08:00 shift
stays 08:00 local across DST-free SEA deployments and converts correctly
elsewhere.
"""
from __future__ import annotations

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS shift_patterns (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id          UUID NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,
            guard_user_id    UUID NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
            label            VARCHAR(100),
            days_of_week     INTEGER[] NOT NULL,
            start_time       TIME NOT NULL,
            duration_minutes INTEGER NOT NULL CHECK (duration_minutes BETWEEN 1 AND 1440),
            is_active        BOOLEAN NOT NULL DEFAULT TRUE,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_shift_patterns_tenant ON shift_patterns(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_shift_patterns_site ON shift_patterns(site_id)")

    op.execute("ALTER TABLE shift_patterns ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shift_patterns FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_shift_patterns ON shift_patterns
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute(
        "ALTER TABLE shifts ADD COLUMN IF NOT EXISTS pattern_id UUID "
        "REFERENCES shift_patterns(id) ON DELETE SET NULL"
    )
    # NULLs are distinct in unique indexes, so manual shifts never conflict.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_shifts_pattern_start "
        "ON shifts(pattern_id, scheduled_start)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_shifts_pattern_start")
    op.execute("ALTER TABLE shifts DROP COLUMN IF EXISTS pattern_id")
    op.execute("DROP TABLE IF EXISTS shift_patterns")
