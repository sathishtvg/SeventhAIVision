"""Alert assignment — add assigned_to_user_id to alerts table.

Revision ID: 0039
Revises: 0038
"""
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE alerts
            ADD COLUMN assigned_to_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN assigned_at TIMESTAMPTZ
    """)
    op.execute("""
        CREATE INDEX idx_alerts_assigned_to ON alerts(assigned_to_user_id)
        WHERE assigned_to_user_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_alerts_assigned_to")
    op.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS assigned_at")
    op.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS assigned_to_user_id")
