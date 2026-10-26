"""The AI module, phase 6: the assistant's conversations.

The owner's decision (26 October 2026): conversations are kept in our own
database for as long as the company exists. They go with the person's account if
the account is deleted (ON DELETE CASCADE), and with the company.

ai_messages.payload holds the turn exactly as the model produced it (for Claude,
its content blocks including thinking; for the built-in model, its chat
messages), replayed unchanged on the next turn: the history is append-only.
`text` is what the person saw. Each assistant row is one reply, which is what
the monthly reply limit counts.

Revision ID: 0172
Revises: 0171
"""
from alembic import op

revision = "0172"
down_revision = "0171"
branch_labels = None
depends_on = None

TABLES = ("ai_conversations", "ai_messages")


def _secure(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute(f"REVOKE ALL ON {table} FROM svc_app")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO svc_app")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ai_conversations (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title       VARCHAR(200) NOT NULL,
            channel     VARCHAR(10) NOT NULL DEFAULT 'WEB',
            provider    VARCHAR(10) NOT NULL,
            model       VARCHAR(100) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_aic_channel CHECK (channel IN ('WEB', 'PHONE')),
            CONSTRAINT ck_aic_provider CHECK (provider IN ('LOCAL', 'HOSTED'))
        )
    """)
    op.execute("CREATE INDEX idx_aic_user ON ai_conversations (tenant_id, user_id, updated_at DESC)")
    op.execute("""
        CREATE TABLE ai_messages (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            conversation_id UUID NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
            role            VARCHAR(10) NOT NULL,
            text            TEXT NOT NULL DEFAULT '',
            payload         JSONB NOT NULL DEFAULT '[]'::jsonb,
            provider        VARCHAR(10) NOT NULL,
            model           VARCHAR(100) NOT NULL,
            used            JSONB NOT NULL DEFAULT '[]'::jsonb,
            stop            VARCHAR(12),
            tokens_in       INTEGER NOT NULL DEFAULT 0,
            tokens_out      INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_aim_role CHECK (role IN ('user', 'assistant'))
        )
    """)
    op.execute("CREATE INDEX idx_aimsg_conversation ON ai_messages (conversation_id, created_at)")
    op.execute("CREATE INDEX idx_aimsg_month ON ai_messages (tenant_id, created_at) WHERE role = 'assistant'")
    for table in TABLES:
        _secure(table)


def downgrade() -> None:
    for table in ("ai_messages", "ai_conversations"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
