"""Tier 2 Feature 8: scheduled report delivery

Revision ID: 0020
Revises: 0019
"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE report_schedules (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name                VARCHAR(255) NOT NULL,
            report_type         VARCHAR(30) NOT NULL
                                    CHECK (report_type IN ('site_summary', 'dob', 'incident_summary')),
            frequency           VARCHAR(20) NOT NULL
                                    CHECK (frequency IN ('daily', 'weekly', 'monthly')),
            day_of_week         SMALLINT CHECK (day_of_week BETWEEN 0 AND 6),
            day_of_month        SMALLINT CHECK (day_of_month BETWEEN 1 AND 28),
            hour_utc            SMALLINT NOT NULL DEFAULT 8
                                    CHECK (hour_utc BETWEEN 0 AND 23),
            site_id             UUID REFERENCES sites(id) ON DELETE SET NULL,
            delivery_method     VARCHAR(20) NOT NULL DEFAULT 'email'
                                    CHECK (delivery_method IN ('email', 'webhook')),
            recipients          JSONB NOT NULL DEFAULT '[]',
            webhook_url         VARCHAR(500),
            is_active           BOOLEAN NOT NULL DEFAULT TRUE,
            last_run_at         TIMESTAMPTZ,
            next_run_at         TIMESTAMPTZ,
            created_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX idx_report_schedules_tenant ON report_schedules(tenant_id)
    """)

    op.execute("""
        CREATE INDEX idx_report_schedules_next_run
            ON report_schedules(next_run_at)
            WHERE is_active = TRUE
    """)

    op.execute("""
        ALTER TABLE report_schedules ENABLE ROW LEVEL SECURITY
    """)
    op.execute("""
        ALTER TABLE report_schedules FORCE ROW LEVEL SECURITY
    """)
    op.execute("""
        CREATE POLICY tenant_isolation_report_schedules ON report_schedules
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        CREATE TABLE report_deliveries (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            schedule_id         UUID NOT NULL REFERENCES report_schedules(id) ON DELETE CASCADE,
            status              VARCHAR(20) NOT NULL DEFAULT 'pending'
                                    CHECK (status IN ('pending', 'success', 'failed')),
            delivered_at        TIMESTAMPTZ,
            error_message       TEXT,
            report_period_start TIMESTAMPTZ,
            report_period_end   TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX idx_report_deliveries_schedule ON report_deliveries(schedule_id, created_at DESC)
    """)

    op.execute("""
        ALTER TABLE report_deliveries ENABLE ROW LEVEL SECURITY
    """)
    op.execute("""
        ALTER TABLE report_deliveries FORCE ROW LEVEL SECURITY
    """)
    op.execute("""
        CREATE POLICY tenant_isolation_report_deliveries ON report_deliveries
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        GRANT ALL ON report_schedules TO svc_app
    """)
    op.execute("""
        GRANT ALL ON report_deliveries TO svc_app
    """)

    # Permission: report:schedule — manage report schedules (admin + super_admin)
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES ('report:schedule', 'Manage scheduled report deliveries', 'report')
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r, permissions p
        WHERE r.code IN ('super_admin', 'admin')
          AND p.code = 'report:schedule'
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_report_deliveries ON report_deliveries")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_report_schedules ON report_schedules")
    op.execute("DROP TABLE IF EXISTS report_deliveries")
    op.execute("DROP TABLE IF EXISTS report_schedules")
    op.execute("""
        DELETE FROM role_permissions
        WHERE permission_id = (SELECT id FROM permissions WHERE code = 'report:schedule')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'report:schedule'")
