"""Platform products, tenant licensing, subdomain routing

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── 1. Extend tenants table ─────────────────────────────────────────────
    op.add_column("tenants", sa.Column("subdomain", sa.String(100), nullable=True))
    op.add_column("tenants", sa.Column("custom_domain", sa.String(255), nullable=True))
    op.add_column("tenants", sa.Column("branding", JSONB, server_default="{}", nullable=False))
    op.add_column("tenants", sa.Column("timezone", sa.String(50), server_default="Asia/Singapore", nullable=False))
    op.add_column("tenants", sa.Column("platform_config", JSONB, server_default="{}", nullable=False))

    op.create_unique_constraint("uq_tenants_subdomain", "tenants", ["subdomain"])
    op.create_unique_constraint("uq_tenants_custom_domain", "tenants", ["custom_domain"])

    # Backfill subdomain from existing slug
    op.execute("UPDATE tenants SET subdomain = slug WHERE subdomain IS NULL")

    # ─── 2. Products catalog (global — no tenant_id, no RLS) ─────────────────
    op.execute("""
        CREATE TABLE products (
            id          VARCHAR(50) PRIMARY KEY,
            name        VARCHAR(255) NOT NULL,
            description TEXT,
            icon        VARCHAR(100),
            color       VARCHAR(20)  NOT NULL DEFAULT '#6C63FF',
            is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
            sort_order  INTEGER      NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)

    # ─── 3. Product modules catalog (global) ─────────────────────────────────
    op.execute("""
        CREATE TABLE product_modules (
            id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id   VARCHAR(50) NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            module_code  VARCHAR(50) NOT NULL,
            module_name  VARCHAR(100) NOT NULL,
            description  TEXT,
            icon         VARCHAR(100),
            is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
            sort_order   INTEGER     NOT NULL DEFAULT 0,
            UNIQUE (product_id, module_code)
        )
    """)
    op.execute("CREATE INDEX idx_product_modules_product ON product_modules(product_id)")

    # ─── 4. Tenant product licenses (RLS-protected) ──────────────────────────
    op.execute("""
        CREATE TABLE tenant_products (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            product_id          VARCHAR(50) NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            is_enabled          BOOLEAN     NOT NULL DEFAULT TRUE,
            licensed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at          TIMESTAMPTZ,
            seat_limit          INTEGER,
            licensed_by_user_id UUID        REFERENCES users(id) ON DELETE SET NULL,
            notes               TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, product_id)
        )
    """)
    op.execute("CREATE INDEX idx_tenant_products_tenant ON tenant_products(tenant_id)")

    op.execute("ALTER TABLE tenant_products ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_products FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_tenant_products ON tenant_products
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ─── 5. Tenant product module overrides (RLS-protected) ──────────────────
    # Allows disabling specific modules within a purchased product per tenant
    op.execute("""
        CREATE TABLE tenant_product_modules (
            id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            product_id   VARCHAR(50) NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            module_code  VARCHAR(50) NOT NULL,
            is_enabled   BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, product_id, module_code)
        )
    """)
    op.execute("CREATE INDEX idx_tenant_product_modules_tenant ON tenant_product_modules(tenant_id)")

    op.execute("ALTER TABLE tenant_product_modules ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_product_modules FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_tenant_product_modules ON tenant_product_modules
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ─── 6. Seed products catalog ─────────────────────────────────────────────
    op.execute("""
        INSERT INTO products (id, name, description, icon, color, sort_order) VALUES
        ('seventh_ai_vision', '7th AI Vision',
         'AI-powered video surveillance, multi-module detection, alerts and incidents management',
         'VideocamOutlined', '#6C63FF', 1),
        ('shift_secure', 'Shift Secure',
         'Guard operations platform — shift scheduling, patrol tracking, checkpoints and DOB',
         'SecurityOutlined', '#00D9C0', 2)
    """)

    op.execute("""
        INSERT INTO product_modules (product_id, module_code, module_name, description, icon, sort_order) VALUES
        -- 7th AI Vision modules
        ('seventh_ai_vision', 'vms',          'Video Management',       'Sites, cameras, live wall and recordings',              'VideocamOutlined',   1),
        ('seventh_ai_vision', 'detections',   'AI Detections',          'LPR, face, intrusion, PPE, crowd and more',             'TrackChanges',       2),
        ('seventh_ai_vision', 'operations',   'Security Operations',    'Alerts, incidents, evidence and dispatch',              'NotificationsActive',3),
        ('seventh_ai_vision', 'analytics',    'Analytics & Reports',    'Analytics dashboard, heatmap and PDF reports',          'BarChart',           4),
        ('seventh_ai_vision', 'compliance',   'Compliance & Audit',     'Audit logs, PDPA/DSAR, scheduled reports, alert dedup', 'Gavel',              5),
        ('seventh_ai_vision', 'client_portal','Client Portal',          'Read-only portal for end clients',                      'AccountCircle',      6),
        -- Shift Secure modules
        ('shift_secure', 'shifts',     'Shift Management',    'Create and manage guard shifts and rosters',        'Schedule',     1),
        ('shift_secure', 'patrols',    'Patrol & Checkpoints','Route planning, QR checkpoints and patrol logs',    'DirectionsWalk',2),
        ('shift_secure', 'dob',        'Occurrence Book',     'Daily occurrence book entries and handover notes',  'MenuBook',     3),
        ('shift_secure', 'visitors',   'Visitor Management',  'Visitor registration, access and overstay alerts',  'People',       4),
        ('shift_secure', 'sos',        'SOS & Emergency',     'Guard SOS panic button and emergency dispatch',     'Emergency',    5),
        ('shift_secure', 'dispatch',   'Dispatch',            'Incident dispatch, SLA tracking and custody chain', 'LocalPolice',  6)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenant_product_modules CASCADE")
    op.execute("DROP TABLE IF EXISTS tenant_products CASCADE")
    op.execute("DROP TABLE IF EXISTS product_modules CASCADE")
    op.execute("DROP TABLE IF EXISTS products CASCADE")

    op.drop_constraint("uq_tenants_custom_domain", "tenants", type_="unique")
    op.drop_constraint("uq_tenants_subdomain", "tenants", type_="unique")
    op.drop_column("tenants", "platform_config")
    op.drop_column("tenants", "timezone")
    op.drop_column("tenants", "branding")
    op.drop_column("tenants", "custom_domain")
    op.drop_column("tenants", "subdomain")
