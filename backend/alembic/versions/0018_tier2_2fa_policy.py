"""Tier 2 Feature 4: 2FA enforcement policy.

Adds 2fa:policy permission for admins to read/set the tenant-level 2FA mandate.
No schema changes needed — policy values are stored in the existing tenant_settings
table under keys '2fa.required' (bool) and '2fa.grace_hours' (int).

Revision ID: 0018
Revises:     0017
Create Date: 2026-06-23
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES ('2fa:policy', 'Read and set tenant-level 2FA enforcement policy', 'security')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE r.id IN (1, 2) AND p.code = '2fa:policy'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN "
               "(SELECT id FROM permissions WHERE code = '2fa:policy')")
    op.execute("DELETE FROM permissions WHERE code = '2fa:policy'")
