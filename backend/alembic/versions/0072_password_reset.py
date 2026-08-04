"""0072 — Password reset tokens

Adds self-service "forgot password" support. Mirrors refresh_tokens'
opaque-token-hash pattern exactly: the raw token is prefixed with its
tenant_id (f"{tenant_id}.{secrets.token_urlsafe(32)}") so the confirm step
can set the RLS context before querying this RLS-protected table, and
only a SHA-256 hash of the random component is ever stored — the raw
token is never persisted, only ever emailed to the user once.

A used or expired row is left in place (not deleted) so a replay attempt
gets a clear "already used / expired" signal rather than a generic
"not found", and so there's an audit trail of reset activity per user.
"""
from __future__ import annotations

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE password_reset_tokens (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash VARCHAR(255) NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_password_reset_tokens_tenant_id ON password_reset_tokens(tenant_id)")
    op.execute("CREATE INDEX idx_password_reset_tokens_token_hash ON password_reset_tokens(token_hash)")

    op.execute("ALTER TABLE password_reset_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE password_reset_tokens FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_password_reset_tokens ON password_reset_tokens
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS password_reset_tokens")
