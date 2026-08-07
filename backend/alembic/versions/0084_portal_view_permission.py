"""Make `portal:view` a real permission.

The Sidebar gates the Client Portal nav item on `portal:view`, and the
frontend's fallback permission matrix grants it to role 7. The database had
no such permission row, so the two sources of truth disagreed.

That mattered because permission resolution prefers the backend-fetched set
and only falls back to the matrix. Once the fetch lands, a code that exists
solely in the matrix resolves to false — so the Client Portal link would
vanish for the very role the portal is built for, while appearing fine in
any environment where the fetch had not completed. A gate that depends on a
race is worse than one that is simply wrong.

Granted to the client role plus the three roles that hold everything
(super_admin, admin, manager), which is what the frontend matrix already
implies via ALL_PERMISSIONS.
"""
from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES ('portal:view', 'Access the read-only client portal', 'portal')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 7, 8) AND p.code = 'portal:view'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
        WHERE permission_id = (SELECT id FROM permissions WHERE code = 'portal:view')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'portal:view'")
