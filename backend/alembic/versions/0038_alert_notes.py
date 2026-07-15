"""Alert notes — allow operators to annotate alerts with free-text comments.

Revision ID: 0038
Revises: 0037
"""
from alembic import op
import sqlalchemy as sa

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE alert_notes (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            alert_id    UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
            author_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            note        TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_alert_notes_alert_id ON alert_notes(alert_id, created_at DESC)")
    op.execute("CREATE INDEX idx_alert_notes_tenant_id ON alert_notes(tenant_id)")

    # RLS — same triplet applied to every tenant table
    op.execute("ALTER TABLE alert_notes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE alert_notes FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_alert_notes ON alert_notes
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alert_notes CASCADE")
