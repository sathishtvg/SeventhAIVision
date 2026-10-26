"""The AI module, phase 8: what was read from an IC, held for an hour while the new person is created.

The PHOTOS are not here and are not kept anywhere (services/ai_ic.py). What is
held is the text read from them, so the full NRIC/FIN stays on our server: the
assistant (and a hosted model) is told only its last four characters, and the
full value is filled in when the person confirms the change that saves it.

A draft lasts an hour, belongs to the person who scanned the card, and is
deleted when used or when it runs out.

Revision ID: 0174
Revises: 0173
"""
from alembic import op

revision = "0174"
down_revision = "0173"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ai_ic_drafts (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            fields      JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at  TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_aiic_user ON ai_ic_drafts (tenant_id, user_id, created_at DESC)")
    op.execute("ALTER TABLE ai_ic_drafts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_ic_drafts FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_ai_ic_drafts ON ai_ic_drafts
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("REVOKE ALL ON ai_ic_drafts FROM svc_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ai_ic_drafts TO svc_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_ic_drafts")
