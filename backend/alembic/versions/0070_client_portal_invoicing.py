"""0070 — Client Portal Invoice Viewing

Grants invoicing:read to role 7 (client portal). The link from a
client-portal user to "which invoices are theirs" is derived transitively
through user_sites -> sites.client_id -> billing_clients, the same way
role 7's visibility into alerts/incidents/cameras is already derived
transitively through user_sites (Gap 81) — no new table or column needed.
See dependencies/sites.py::get_allowed_client_ids for the read-time scoping.

invoicing:manage stays deliberately ungranted to role 7 — a client views
invoices, never creates/finalizes/voids them.
"""
from __future__ import annotations

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 7, id FROM permissions WHERE code = 'invoicing:read'
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
        WHERE role_id = 7
          AND permission_id = (SELECT id FROM permissions WHERE code = 'invoicing:read')
    """)
