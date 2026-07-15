"""Emergency Mass Notification:
- emergency_broadcasts: mass notifications sent by admins/supervisors to all staff
- broadcast_recipients: per-user delivery + acknowledgement tracking

New permissions: broadcast:send (1-3), broadcast:read (1-7), broadcast:acknowledge (1-7)

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rls(table: str) -> str:
    return f"""
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation_{table} ON {table}
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE emergency_broadcasts (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            title               VARCHAR(255) NOT NULL,
            message             TEXT NOT NULL,
            severity            VARCHAR(20) NOT NULL DEFAULT 'warning',
            -- info | warning | critical | drill
            broadcast_type      VARCHAR(20) NOT NULL DEFAULT 'all',
            -- all | role (role_ids in target_role_ids)
            target_role_ids     JSONB,
            recipient_count     INTEGER NOT NULL DEFAULT 0,
            acknowledged_count  INTEGER NOT NULL DEFAULT 0,
            created_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            sent_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            status              VARCHAR(20) NOT NULL DEFAULT 'sent',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_emergency_broadcasts_tenant ON emergency_broadcasts(tenant_id, created_at DESC);
        """
    )
    op.execute(_rls("emergency_broadcasts"))

    op.execute(
        """
        CREATE TABLE broadcast_recipients (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            broadcast_id    UUID NOT NULL REFERENCES emergency_broadcasts(id) ON DELETE CASCADE,
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            delivered_at    TIMESTAMPTZ DEFAULT now(),
            acknowledged_at TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (broadcast_id, user_id)
        );
        CREATE INDEX idx_broadcast_recipients_broadcast ON broadcast_recipients(broadcast_id);
        CREATE INDEX idx_broadcast_recipients_user ON broadcast_recipients(tenant_id, user_id);
        """
    )
    op.execute(_rls("broadcast_recipients"))

    op.execute(
        """
        INSERT INTO permissions (code, description, category) VALUES
            ('broadcast:send',        'Send emergency mass notifications to all staff', 'emergency'),
            ('broadcast:read',        'View emergency broadcasts and recipient lists',  'emergency'),
            ('broadcast:acknowledge', 'Acknowledge receipt of an emergency broadcast',  'emergency')
        ON CONFLICT (code) DO NOTHING;

        -- broadcast:send → admin+ (roles 1-3)
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3) AND p.code = 'broadcast:send'
        ON CONFLICT DO NOTHING;

        -- broadcast:read → all roles (1-7)
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3,4,5,6,7) AND p.code = 'broadcast:read'
        ON CONFLICT DO NOTHING;

        -- broadcast:acknowledge → all roles (1-7)
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3,4,5,6,7) AND p.code = 'broadcast:acknowledge'
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS broadcast_recipients;
        DROP TABLE IF EXISTS emergency_broadcasts;
        """
    )
