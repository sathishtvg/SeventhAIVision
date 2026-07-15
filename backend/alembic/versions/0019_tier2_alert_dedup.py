"""Tier 2 Feature 5: alert deduplication rules

Revision ID: 0019
Revises: 0018
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE alert_dedup_rules (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            module_type         VARCHAR(30),
            camera_id           UUID REFERENCES cameras(id) ON DELETE CASCADE,
            window_seconds      INTEGER NOT NULL DEFAULT 300
                                    CHECK (window_seconds > 0 AND window_seconds <= 86400),
            is_active           BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_alert_dedup_rules_tenant_active
            ON alert_dedup_rules(tenant_id) WHERE is_active = TRUE
    """)

    op.execute("ALTER TABLE alert_dedup_rules ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE alert_dedup_rules FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_alert_dedup_rules ON alert_dedup_rules
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # Permissions
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES
            ('alert:dedup:manage', 'Create, edit, delete alert deduplication rules', 'alerts'),
            ('alert:create',       'Manually create new alerts',                     'alerts')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r, permissions p
        WHERE r.id IN (1, 2)     AND p.code = 'alert:dedup:manage'
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r, permissions p
        WHERE r.id IN (1, 2, 3)  AND p.code = 'alert:create'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alert_dedup_rules CASCADE")
    op.execute("""
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions
            WHERE code IN ('alert:dedup:manage', 'alert:create')
        )
    """)
    op.execute("""
        DELETE FROM permissions
        WHERE code IN ('alert:dedup:manage', 'alert:create')
    """)
