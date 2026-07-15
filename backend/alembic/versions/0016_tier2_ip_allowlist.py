"""Tier 2 Feature 2: IP allowlist for per-tenant access control.

If a tenant has any active ip_allowlist entries, every authenticated request
from an IP that doesn't match one of the listed CIDRs is rejected with 403.
If no active entries exist, the tenant is unrestricted (default open).

Revision ID: 0016
Revises:     0015
Create Date: 2026-06-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ip_allowlist",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cidr", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by_user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "cidr", name="uq_ip_allowlist_tenant_cidr"),
    )
    op.create_index("idx_ip_allowlist_tenant_active", "ip_allowlist", ["tenant_id"],
                    postgresql_where=sa.text("is_active = TRUE"))

    # RLS
    op.execute("ALTER TABLE ip_allowlist ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ip_allowlist FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_ip_allowlist ON ip_allowlist
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # Permission
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES ('iplist:manage', 'Manage IP allowlist for tenant access control', 'security')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE r.id IN (1, 2) AND p.code = 'iplist:manage'
        ON CONFLICT DO NOTHING
    """)

    # SECURITY DEFINER function so get_db_with_tenant can check the allowlist
    # for a known tenant without hitting the RLS chicken-and-egg issue.
    op.execute("""
        CREATE OR REPLACE FUNCTION get_tenant_ip_allowlist(p_tenant_id UUID)
        RETURNS TABLE (cidr TEXT)
        LANGUAGE plpgsql
        SECURITY DEFINER
        AS $$
        BEGIN
            RETURN QUERY
            SELECT ip_allowlist.cidr::TEXT
            FROM ip_allowlist
            WHERE tenant_id = p_tenant_id
              AND is_active = TRUE;
        END;
        $$;
    """)
    op.execute("GRANT EXECUTE ON FUNCTION get_tenant_ip_allowlist(UUID) TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS get_tenant_ip_allowlist(UUID)")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN "
               "(SELECT id FROM permissions WHERE code = 'iplist:manage')")
    op.execute("DELETE FROM permissions WHERE code = 'iplist:manage'")
    op.drop_table("ip_allowlist")
