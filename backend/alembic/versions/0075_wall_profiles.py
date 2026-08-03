"""0075 — Multi-screen Live Wall profiles

A "profile" groups N saved wall_layouts rows together as one named,
launchable set — e.g. a 3-monitor control room where screen 1 shows the
front gate + lobby cameras, screen 2 shows the perimeter, screen 3 shows
loading-dock intrusion analytics. Extends the existing wall_layouts table
(Gap 84, migration 0053) rather than a parallel schema: a standalone
layout (today's feature) keeps profile_id NULL and is unaffected; a
profile screen is just a wall_layouts row with profile_id set and a
screen_index for ordering. analytics_modules lets each screen show a
different AI-overlay filter (extends the wall-wide filter from the "True
Operator Control Room" round to per-screen granularity).
"""
from __future__ import annotations

from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS wall_profiles (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id      UUID NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
            name         VARCHAR(100) NOT NULL,
            screen_count INTEGER NOT NULL,
            is_shared    BOOLEAN NOT NULL DEFAULT FALSE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, user_id, name)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_wall_profiles_tenant_user ON wall_profiles(tenant_id, user_id)")

    op.execute("ALTER TABLE wall_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE wall_profiles FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_wall_profiles ON wall_profiles
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        ALTER TABLE wall_layouts
          ADD COLUMN IF NOT EXISTS profile_id UUID REFERENCES wall_profiles(id) ON DELETE CASCADE,
          ADD COLUMN IF NOT EXISTS screen_index INTEGER,
          ADD COLUMN IF NOT EXISTS analytics_modules JSONB NOT NULL DEFAULT '[]'
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_wall_layouts_profile
            ON wall_layouts(profile_id, screen_index) WHERE profile_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_wall_layouts_profile")
    op.execute("""
        ALTER TABLE wall_layouts
          DROP COLUMN IF EXISTS profile_id,
          DROP COLUMN IF EXISTS screen_index,
          DROP COLUMN IF EXISTS analytics_modules
    """)
    op.execute("DROP TABLE IF EXISTS wall_profiles")
