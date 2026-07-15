"""Add SECURITY DEFINER function for API key hash lookup.

The api_keys table has RLS that gates visibility by app.current_tenant.
In get_token_payload we don't know the tenant yet — we're deriving it FROM the key.
A SECURITY DEFINER function runs as owner (superuser), bypassing RLS, which lets
svc_app do the bootstrap hash lookup without superuser credentials in app code.

Revision ID: 0015
Revises:     0014
Create Date: 2026-06-23
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION authenticate_api_key(p_hash TEXT)
        RETURNS TABLE (key_id UUID, key_tenant_id UUID)
        LANGUAGE plpgsql
        SECURITY DEFINER
        AS $$
        BEGIN
            RETURN QUERY
            UPDATE api_keys
            SET last_used_at = now()
            WHERE key_hash = p_hash
              AND is_active = TRUE
              AND (expires_at IS NULL OR expires_at > now())
            RETURNING id, tenant_id;
        END;
        $$;
    """)
    op.execute("GRANT EXECUTE ON FUNCTION authenticate_api_key(TEXT) TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS authenticate_api_key(TEXT)")
