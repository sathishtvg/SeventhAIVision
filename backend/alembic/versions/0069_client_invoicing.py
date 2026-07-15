"""0069 — Client Billing / Invoicing (ShiftSecure final phase)

Distinct from billing.py's existing Stripe SaaS subscription billing
(the tenant paying for Seventh AI Vision) — this is the tenant invoicing
their own clients for guard services delivered. Deliberately namespaced
as invoicing:read/manage (not billing:*) to avoid future collision with
a Stripe-subscription frontend.

One rate per site (sites.bill_rate), mirroring how payroll's guard rate
lives directly on users rather than a join table. Draft -> finalized ->
paid/void workflow; invoice_number assigned only at finalize so a
discarded draft never leaves a gap in the visible sequence.
"""
from __future__ import annotations

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE billing_clients (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name            VARCHAR(255) NOT NULL,
            contact_name    VARCHAR(255),
            contact_email   VARCHAR(255),
            contact_phone   VARCHAR(30),
            billing_address TEXT,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("ALTER TABLE billing_clients ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE billing_clients FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_billing_clients ON billing_clients
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        ALTER TABLE sites
          ADD COLUMN client_id UUID REFERENCES billing_clients(id) ON DELETE SET NULL,
          ADD COLUMN bill_rate NUMERIC(8,2)
    """)

    op.execute("""
        CREATE TABLE invoices (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            client_id            UUID NOT NULL REFERENCES billing_clients(id) ON DELETE CASCADE,
            invoice_number       VARCHAR(30),
            period_start         DATE NOT NULL,
            period_end           DATE NOT NULL,
            status               VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','finalized','paid','void')),
            subtotal             NUMERIC(12,2) NOT NULL DEFAULT 0,
            tax_rate             NUMERIC(5,4) NOT NULL DEFAULT 0,
            tax_amount           NUMERIC(12,2) NOT NULL DEFAULT 0,
            total_amount         NUMERIC(12,2) NOT NULL DEFAULT 0,
            due_date             DATE,
            generated_by_user_id UUID REFERENCES users(id),
            generated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            finalized_at         TIMESTAMPTZ,
            paid_at              TIMESTAMPTZ,
            voided_at            TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX idx_invoices_client ON invoices(tenant_id, client_id)")
    op.execute("CREATE INDEX idx_invoices_status ON invoices(tenant_id, status)")
    op.execute("ALTER TABLE invoices ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invoices FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_invoices ON invoices
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        CREATE TABLE invoice_line_items (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            invoice_id       UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
            site_id          UUID REFERENCES sites(id) ON DELETE SET NULL,
            site_name        VARCHAR(255) NOT NULL,
            regular_hours    NUMERIC(8,2) NOT NULL DEFAULT 0,
            overtime_hours   NUMERIC(8,2) NOT NULL DEFAULT 0,
            bill_rate        NUMERIC(8,2) NOT NULL,
            regular_amount   NUMERIC(12,2) NOT NULL DEFAULT 0,
            overtime_amount  NUMERIC(12,2) NOT NULL DEFAULT 0,
            line_total       NUMERIC(12,2) NOT NULL DEFAULT 0
        )
    """)
    op.execute("CREATE INDEX idx_invoice_line_items_invoice ON invoice_line_items(tenant_id, invoice_id)")
    op.execute("ALTER TABLE invoice_line_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invoice_line_items FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_invoice_line_items ON invoice_line_items
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('invoicing:read',   'View billing clients and invoices',                 'guard'),
          ('invoicing:manage', 'Manage billing clients, generate/finalize invoices','guard')
        ON CONFLICT (code) DO NOTHING;

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 8) AND p.code IN ('invoicing:read', 'invoicing:manage')
        ON CONFLICT DO NOTHING;

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 3 AND p.code = 'invoicing:read'
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS invoice_line_items")
    op.execute("DROP TABLE IF EXISTS invoices")
    op.execute("""
        ALTER TABLE sites
          DROP COLUMN IF EXISTS client_id,
          DROP COLUMN IF EXISTS bill_rate
    """)
    op.execute("DROP TABLE IF EXISTS billing_clients")
    op.execute("""
        DELETE FROM role_permissions WHERE permission_id IN (
            SELECT id FROM permissions WHERE code IN ('invoicing:read', 'invoicing:manage')
        )
    """)
    op.execute("DELETE FROM permissions WHERE code IN ('invoicing:read', 'invoicing:manage')")
