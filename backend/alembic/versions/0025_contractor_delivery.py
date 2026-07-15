"""Contractor & Delivery Management — contractors, accreditations, work_permits, deliveries

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-25
"""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── contractors ───────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE contractors (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            company_name        VARCHAR(255) NOT NULL,
            registration_number VARCHAR(100),
            contact_name        VARCHAR(255),
            contact_phone       VARCHAR(50),
            contact_email       VARCHAR(255),
            address             TEXT,
            specialization      VARCHAR(255),
            vetting_status      VARCHAR(20) NOT NULL DEFAULT 'pending',
            vetting_notes       TEXT,
            vetted_by_user_id   UUID        REFERENCES users(id) ON DELETE SET NULL,
            vetted_at           TIMESTAMPTZ,
            is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_contractors_tenant        ON contractors(tenant_id)")
    op.execute("CREATE INDEX idx_contractors_vetting       ON contractors(tenant_id, vetting_status)")

    op.execute("ALTER TABLE contractors ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE contractors FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_contractors ON contractors
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── contractor_accreditations ─────────────────────────────────────────────
    op.execute("""
        CREATE TABLE contractor_accreditations (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            contractor_id   UUID        NOT NULL REFERENCES contractors(id) ON DELETE CASCADE,
            document_type   VARCHAR(100) NOT NULL,
            document_number VARCHAR(100),
            issued_by       VARCHAR(255),
            issued_at       DATE,
            expires_at      DATE,
            notes           TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_accreditations_contractor ON contractor_accreditations(contractor_id)")

    op.execute("ALTER TABLE contractor_accreditations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE contractor_accreditations FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_contractor_accreditations ON contractor_accreditations
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── work_permits ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE work_permits (
            id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            contractor_id           UUID        NOT NULL REFERENCES contractors(id) ON DELETE CASCADE,
            site_id                 UUID        REFERENCES sites(id) ON DELETE SET NULL,
            permit_number           VARCHAR(100),
            work_description        TEXT        NOT NULL,
            work_type               VARCHAR(100),
            requested_by_name       VARCHAR(255),
            requested_by_email      VARCHAR(255),
            workers_count           INTEGER     NOT NULL DEFAULT 1,
            vehicles_count          INTEGER     NOT NULL DEFAULT 0,
            start_at                TIMESTAMPTZ NOT NULL,
            end_at                  TIMESTAMPTZ NOT NULL,
            status                  VARCHAR(20) NOT NULL DEFAULT 'pending',
            approved_by_user_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
            approved_at             TIMESTAMPTZ,
            rejection_reason        TEXT,
            safety_briefing_done    BOOLEAN     NOT NULL DEFAULT FALSE,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_work_permits_tenant      ON work_permits(tenant_id, status, start_at DESC)")
    op.execute("CREATE INDEX idx_work_permits_contractor  ON work_permits(contractor_id)")
    op.execute("CREATE INDEX idx_work_permits_site        ON work_permits(site_id)")

    op.execute("ALTER TABLE work_permits ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE work_permits FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_work_permits ON work_permits
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── deliveries ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE deliveries (
            id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id               UUID        REFERENCES sites(id) ON DELETE SET NULL,
            tracking_number       VARCHAR(100),
            carrier               VARCHAR(100),
            sender_name           VARCHAR(255),
            sender_company        VARCHAR(255),
            recipient_name        VARCHAR(255) NOT NULL,
            recipient_department  VARCHAR(255),
            description           TEXT,
            expected_at           TIMESTAMPTZ,
            received_at           TIMESTAMPTZ,
            received_by_user_id   UUID        REFERENCES users(id) ON DELETE SET NULL,
            collected_at          TIMESTAMPTZ,
            collected_by_name     VARCHAR(255),
            status                VARCHAR(20) NOT NULL DEFAULT 'pending',
            rejection_reason      TEXT,
            notes                 TEXT,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_deliveries_tenant  ON deliveries(tenant_id, status, created_at DESC)")
    op.execute("CREATE INDEX idx_deliveries_site    ON deliveries(site_id)")

    op.execute("ALTER TABLE deliveries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE deliveries FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_deliveries ON deliveries
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
            ('contractor:read',    'View contractors, work permits, deliveries', 'contractor'),
            ('contractor:write',   'Create / edit contractors and work permits', 'contractor'),
            ('contractor:approve', 'Approve or reject work permits',             'contractor')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE p.code = 'contractor:read' AND r.id IN (1,2,3,4,5,6)
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE p.code = 'contractor:write' AND r.id IN (1,2,3,4)
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE p.code = 'contractor:approve' AND r.id IN (1,2,3)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('contractor:read','contractor:write','contractor:approve'))")
    op.execute("DELETE FROM permissions WHERE code IN ('contractor:read','contractor:write','contractor:approve')")
    op.execute("DROP TABLE IF EXISTS deliveries")
    op.execute("DROP TABLE IF EXISTS work_permits")
    op.execute("DROP TABLE IF EXISTS contractor_accreditations")
    op.execute("DROP TABLE IF EXISTS contractors")
