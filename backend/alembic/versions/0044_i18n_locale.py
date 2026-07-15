"""Gap 6: i18n — locale preference on users and tenants.

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

SUPPORTED_LOCALES = ("en", "zh", "ms", "ta")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS locale VARCHAR(10) NOT NULL DEFAULT 'en'
        CHECK (locale IN ('en', 'zh', 'ms', 'ta'))
    """)
    op.execute("""
        ALTER TABLE tenants
        ADD COLUMN IF NOT EXISTS default_locale VARCHAR(10) NOT NULL DEFAULT 'en'
        CHECK (default_locale IN ('en', 'zh', 'ms', 'ta'))
    """)
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES ('i18n:manage', 'Manage tenant default locale', 'i18n')
        ON CONFLICT (code) DO NOTHING
    """)
    # Grant i18n:manage to super_admin (1) and admin (2)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE r.id IN (1, 2) AND p.code = 'i18n:manage'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS locale")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS default_locale")
    op.execute("""
        DELETE FROM role_permissions
        WHERE permission_id = (SELECT id FROM permissions WHERE code = 'i18n:manage')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'i18n:manage'")
