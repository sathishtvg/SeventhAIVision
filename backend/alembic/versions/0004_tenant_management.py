"""Phase 7: tenant:manage permission for super_admin CRUD over tenants.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-19
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO permissions (code, description, category)
        VALUES ('tenant:manage', 'Create, update and deactivate tenants (super_admin only)', 'admin')
        ON CONFLICT (code) DO NOTHING
        """
    )
    # Grant only to super_admin (role_id = 1)
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 1, id FROM permissions WHERE code = 'tenant:manage'
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_id = (SELECT id FROM permissions WHERE code = 'tenant:manage')
        """
    )
    op.execute("DELETE FROM permissions WHERE code = 'tenant:manage'")
