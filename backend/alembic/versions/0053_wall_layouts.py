"""0053 — Server-side saved wall layouts (Gap 84)

Named LiveWall configurations that follow the operator between machines,
replacing localStorage-only persistence. Every layout has an owner; the
is_shared flag exposes it read-only to the rest of the tenant (control-room
standard layouts). Only the owner or an admin can modify/delete.
"""
from __future__ import annotations

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS wall_layouts (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id  UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id    UUID NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
            name       VARCHAR(100) NOT NULL,
            grid_size  INTEGER NOT NULL DEFAULT 4,
            cells      JSONB NOT NULL DEFAULT '[]',
            is_shared  BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, user_id, name)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_wall_layouts_tenant_user ON wall_layouts(tenant_id, user_id)")

    op.execute("ALTER TABLE wall_layouts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE wall_layouts FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_wall_layouts ON wall_layouts
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS wall_layouts")
