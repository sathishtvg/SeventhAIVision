"""Add SECURITY DEFINER function for alarm panel key lookup.

alarm_panels has FORCE ROW LEVEL SECURITY gated by app.current_tenant.
The ingest webhook endpoint uses get_raw_db (tenant = zero-UUID sentinel)
so RLS hides every panel row. A SECURITY DEFINER function runs as its
owner (postgres superuser) and bypasses RLS, allowing cross-tenant lookup
by api_key before the tenant is known.

Revision ID: 0034
Revises:     0033
Create Date: 2026-06-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION lookup_alarm_panel_by_key(p_key TEXT)
        RETURNS TABLE (id UUID, tenant_id UUID, name VARCHAR, arm_state VARCHAR)
        LANGUAGE plpgsql
        SECURITY DEFINER
        AS $$
        BEGIN
            RETURN QUERY
            SELECT ap.id, ap.tenant_id, ap.name, ap.arm_state
            FROM alarm_panels ap
            WHERE ap.api_key = p_key
              AND ap.is_active = TRUE;
        END;
        $$;
    """)
    op.execute("GRANT EXECUTE ON FUNCTION lookup_alarm_panel_by_key(TEXT) TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS lookup_alarm_panel_by_key(TEXT)")
