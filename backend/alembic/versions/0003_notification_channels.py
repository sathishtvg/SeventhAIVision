"""Phase 4: notification_channels, notification_rules, notification_logs tables.
Also adds the 'notification:manage' permission to the permissions catalogue.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-19
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def _rls(table: str) -> str:
    return f"""
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation_{table} ON {table}
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """


NEW_RLS_TABLES = [
    "notification_channels",
    "notification_rules",
    "notification_logs",
]


def upgrade() -> None:
    # ------------------------------------------------------------------
    # notification_channels — one row per delivery endpoint per tenant.
    # config JSONB holds channel-type-specific fields (SMTP creds, webhook
    # URL, Twilio numbers, etc.) so the schema doesn't fork per channel type.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE notification_channels (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name        VARCHAR(255) NOT NULL,
            channel_type VARCHAR(20) NOT NULL CHECK (channel_type IN ('email','sms','webhook')),
            config      JSONB NOT NULL DEFAULT '{}',
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_notification_channels_tenant ON notification_channels(tenant_id)")

    # ------------------------------------------------------------------
    # notification_rules — maps an alert condition to a delivery channel.
    # min_severity: alert must be >= this severity to trigger delivery.
    # module_types: empty array = all modules; non-empty = allowlist filter.
    # alert_codes:  empty array = all codes; non-empty = allowlist filter.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE notification_rules (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            channel_id   UUID NOT NULL REFERENCES notification_channels(id) ON DELETE CASCADE,
            min_severity VARCHAR(10) NOT NULL DEFAULT 'medium'
                         CHECK (min_severity IN ('info','low','medium','high','critical')),
            module_types JSONB NOT NULL DEFAULT '[]',
            alert_codes  JSONB NOT NULL DEFAULT '[]',
            is_active    BOOLEAN NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_notification_rules_tenant ON notification_rules(tenant_id)")
    op.execute("CREATE INDEX idx_notification_rules_channel ON notification_rules(channel_id)")

    # ------------------------------------------------------------------
    # notification_logs — delivery attempt record (not partitioned; volume
    # is proportional to matched alerts, orders of magnitude below detections).
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE notification_logs (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            alert_id     UUID NOT NULL,
            channel_id   UUID REFERENCES notification_channels(id) ON DELETE SET NULL,
            channel_type VARCHAR(20) NOT NULL,
            status       VARCHAR(10) NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','sent','failed')),
            error_detail TEXT,
            sent_at      TIMESTAMPTZ,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_notification_logs_tenant_time ON notification_logs(tenant_id, created_at DESC)")
    op.execute("CREATE INDEX idx_notification_logs_alert ON notification_logs(alert_id)")

    # Apply RLS to all three new tables
    for table in NEW_RLS_TABLES:
        op.execute(_rls(table))

    # Add notification:manage permission (admin + super_admin only).
    # Cannot reuse 0001's INSERT block since it already ran; INSERT here instead.
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES ('notification:manage', 'Create/edit/delete notification channels and rules', 'notifications')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r, permissions p
        WHERE r.code IN ('super_admin','admin')
          AND p.code = 'notification:manage'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
        WHERE permission_id = (SELECT id FROM permissions WHERE code = 'notification:manage')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'notification:manage'")

    for table in reversed(NEW_RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")

    op.execute("DROP TABLE IF EXISTS notification_logs CASCADE")
    op.execute("DROP TABLE IF EXISTS notification_rules CASCADE")
    op.execute("DROP TABLE IF EXISTS notification_channels CASCADE")
