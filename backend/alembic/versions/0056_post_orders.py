"""0056 — Post orders / site documents (Gap 87)

Per-site standing instructions (a guarding-industry staple): access
procedures, emergency contacts, patrol requirements. Guards must read and
acknowledge; acknowledgments are tracked PER VERSION — editing the content
bumps the version, which automatically invalidates earlier acknowledgments
so supervisors can see who still needs to re-read.

Deletion is soft (is_active = FALSE): post orders are compliance documents
and their acknowledgment history must survive.
"""
from __future__ import annotations

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS post_orders (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id                UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id                  UUID NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,
            title                    VARCHAR(200) NOT NULL,
            body                     TEXT NOT NULL,
            category                 VARCHAR(30) NOT NULL DEFAULT 'general',
            version                  INTEGER NOT NULL DEFAULT 1,
            is_active                BOOLEAN NOT NULL DEFAULT TRUE,
            requires_acknowledgment  BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id       UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_post_orders_tenant_site ON post_orders(tenant_id, site_id)")

    op.execute("ALTER TABLE post_orders ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE post_orders FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_post_orders ON post_orders
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS post_order_acks (
            post_order_id   UUID NOT NULL REFERENCES post_orders(id) ON DELETE CASCADE,
            user_id         UUID NOT NULL REFERENCES users(id)       ON DELETE CASCADE,
            tenant_id       UUID NOT NULL REFERENCES tenants(id)     ON DELETE CASCADE,
            version         INTEGER NOT NULL,
            acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (post_order_id, user_id, version)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_post_order_acks_tenant ON post_order_acks(tenant_id)")

    op.execute("ALTER TABLE post_order_acks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE post_order_acks FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_post_order_acks ON post_order_acks
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS post_order_acks")
    op.execute("DROP TABLE IF EXISTS post_orders")
