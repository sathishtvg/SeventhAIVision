"""Client portal: role_id=7 'client' with read-only access to security events.
Adds primary_site_id to users so a client user's portal auto-filters to their site.

Revision ID: 0012
Revises:     0011
Create Date: 2026-06-22
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Client role ──────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO roles (id, code, name, description)
        VALUES (7, 'client', 'Client', 'Building owner / third-party client read-only access')
        ON CONFLICT (id) DO NOTHING
    """)

    # ── 2. Client-visible permissions ────────────────────────────────────────
    # Permissions that already exist:  alert:read, incident:read, camera:read,
    # detection:read, evidence:read, recording:read, site:manage (no — read version needed)
    # New: site:read (read-only site view, distinct from site:manage)
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES ('site:read', 'View sites and their camera counts', 'site')
        ON CONFLICT (code) DO NOTHING
    """)

    # Grant site:read to all roles that can already do site:manage + viewers + client
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        CROSS JOIN permissions p
        WHERE p.code = 'site:read'
          AND r.id IN (1, 2, 3, 4, 5, 6, 7)
        ON CONFLICT DO NOTHING
    """)

    # ── 3. Grant read-only permissions to client role ─────────────────────────
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 7, p.id
        FROM permissions p
        WHERE p.code IN (
            'alert:read',
            'incident:read',
            'camera:read',
            'detection:read',
            'evidence:read',
            'recording:read',
            'dob:read',
            'pdpa:read',
            'tampering:read',
            'abandoned:read',
            'fall:read'
        )
        ON CONFLICT DO NOTHING
    """)

    # ── 4. primary_site_id on users ──────────────────────────────────────────
    op.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS primary_site_id UUID
            REFERENCES sites(id) ON DELETE SET NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_primary_site
        ON users(tenant_id, primary_site_id)
        WHERE primary_site_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_users_primary_site")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS primary_site_id")
    op.execute("DELETE FROM role_permissions WHERE role_id = 7")
    op.execute("DELETE FROM roles WHERE id = 7")
    op.execute("""
        DELETE FROM role_permissions
        WHERE permission_id = (SELECT id FROM permissions WHERE code = 'site:read')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'site:read'")
