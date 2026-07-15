"""Gap 12 — Webhook event subscriptions + delivery log.

revision: 0047
down_revision: 0046
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

_RLS = """
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_{safe} ON {t}
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
"""


def upgrade():
    # ── webhook_subscriptions ─────────────────────────────────────────────────
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("secret", sa.String(255), nullable=False),
        sa.Column("event_types", postgresql.JSONB, nullable=False,
                  server_default=sa.text('\'["alert_created","incident_created"]\'')),
        sa.Column("filters", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("max_retries", sa.Integer, nullable=False, server_default=sa.text("3")),
        sa.Column("timeout_seconds", sa.Integer, nullable=False, server_default=sa.text("10")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("idx_webhook_subscriptions_tenant",
                    "webhook_subscriptions", ["tenant_id"])
    op.execute(_RLS.format(t="webhook_subscriptions",
                           safe="webhook_subscriptions"))

    # ── webhook_delivery_log ──────────────────────────────────────────────────
    op.create_table(
        "webhook_delivery_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("response_status_code", sa.Integer, nullable=True),
        sa.Column("response_body", sa.Text, nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("idx_webhook_delivery_log_tenant_sub",
                    "webhook_delivery_log", ["tenant_id", "subscription_id"])
    op.create_index("idx_webhook_delivery_log_status_retry",
                    "webhook_delivery_log",
                    ["status", "next_retry_at"],
                    postgresql_where=sa.text("status IN ('pending', 'retrying')"))
    op.execute(_RLS.format(t="webhook_delivery_log",
                           safe="webhook_delivery_log"))

    # ── new permissions ────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES
          ('webhook:manage', 'Create / edit / delete webhook subscriptions', 'webhook'),
          ('webhook:read',   'View webhook subscriptions and delivery logs',  'webhook')
        ON CONFLICT (code) DO NOTHING
    """)
    # webhook:manage → super_admin (1), admin (2), supervisor (3)
    # webhook:read   → super_admin (1), admin (2), supervisor (3), operator (4)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
        FROM (VALUES
          (1, 'webhook:manage'), (2, 'webhook:manage'), (3, 'webhook:manage'),
          (1, 'webhook:read'),   (2, 'webhook:read'),   (3, 'webhook:read'),
          (4, 'webhook:read')
        ) AS r(role_id, pcode)
        JOIN permissions p ON p.code = r.pcode
        ON CONFLICT DO NOTHING
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS webhook_delivery_log CASCADE")
    op.execute("DROP TABLE IF EXISTS webhook_subscriptions CASCADE")
    op.execute("DELETE FROM permissions WHERE code IN ('webhook:manage','webhook:read')")
