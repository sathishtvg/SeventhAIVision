"""The AI module, phase 7: changes the assistant prepares, waiting for a person's Confirm.

One row per prepared change. It does nothing until the person who asked presses
Confirm; it is then sent to the app with THEIR sign-in at that moment, so their
permissions then are the ones that count. A change not decided within 30 minutes
expires. Each confirmed change is written to the audit log as done through the
assistant and confirmed by that person.

`body` may hold a temporary password the server made (the model is never given
one, and never sees one); it is removed from the row once the change is sent.

Revision ID: 0173
Revises: 0172
"""
from alembic import op

revision = "0173"
down_revision = "0172"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ai_pending_actions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            conversation_id UUID NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            operation_id    VARCHAR(200) NOT NULL,
            method          VARCHAR(8) NOT NULL,
            path            TEXT NOT NULL,
            query           JSONB NOT NULL DEFAULT '{}'::jsonb,
            body            JSONB NOT NULL DEFAULT '{}'::jsonb,
            title           TEXT NOT NULL,
            fields          JSONB NOT NULL DEFAULT '[]'::jsonb,
            status          VARCHAR(10) NOT NULL DEFAULT 'PENDING',
            result          JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at      TIMESTAMPTZ NOT NULL,
            decided_at      TIMESTAMPTZ,
            CONSTRAINT ck_aipa_status CHECK (status IN ('PENDING', 'CONFIRMED', 'FAILED', 'CANCELLED', 'EXPIRED')),
            CONSTRAINT ck_aipa_method CHECK (method IN ('POST', 'PUT', 'PATCH', 'DELETE'))
        )
    """)
    op.execute("CREATE INDEX idx_aipa_conversation ON ai_pending_actions (conversation_id, created_at)")
    op.execute("ALTER TABLE ai_pending_actions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_pending_actions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_ai_pending_actions ON ai_pending_actions
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("REVOKE ALL ON ai_pending_actions FROM svc_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ai_pending_actions TO svc_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_pending_actions")
