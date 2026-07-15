"""IoT Smart Facilities Monitoring — sensors, readings, alerts

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── iot_sensors ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE iot_sensors (
            id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id                   UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id                     UUID        REFERENCES sites(id) ON DELETE SET NULL,
            name                        VARCHAR(255) NOT NULL,
            sensor_type                 VARCHAR(50)  NOT NULL,
            unit                        VARCHAR(30),
            location                    VARCHAR(255),
            description                 TEXT,
            is_active                   BOOLEAN     NOT NULL DEFAULT TRUE,
            threshold_warning_low       NUMERIC,
            threshold_warning_high      NUMERIC,
            threshold_critical_low      NUMERIC,
            threshold_critical_high     NUMERIC,
            expected_interval_seconds   INTEGER     NOT NULL DEFAULT 300,
            last_reading_at             TIMESTAMPTZ,
            last_reading_value          NUMERIC,
            current_status              VARCHAR(20) NOT NULL DEFAULT 'unknown',
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_iot_sensors_tenant ON iot_sensors(tenant_id)")
    op.execute("CREATE INDEX idx_iot_sensors_site   ON iot_sensors(site_id)")
    op.execute("CREATE INDEX idx_iot_sensors_type   ON iot_sensors(tenant_id, sensor_type)")

    op.execute("ALTER TABLE iot_sensors ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE iot_sensors FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_iot_sensors ON iot_sensors
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── iot_readings (partitioned by month) ──────────────────────────────────
    op.execute("""
        CREATE TABLE iot_readings (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            tenant_id   UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            sensor_id   UUID        NOT NULL,
            value       NUMERIC     NOT NULL,
            raw_data    JSONB,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, recorded_at)
        ) PARTITION BY RANGE (recorded_at)
    """)
    op.execute("CREATE INDEX idx_iot_readings_sensor_time ON iot_readings(sensor_id, recorded_at DESC)")
    op.execute("CREATE INDEX idx_iot_readings_tenant_time ON iot_readings(tenant_id, recorded_at DESC)")

    op.execute("ALTER TABLE iot_readings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE iot_readings FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_iot_readings ON iot_readings
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # Seed initial partitions via pg_partman (functions live in public schema)
    op.execute("""
        SELECT public.create_parent(
            p_parent_table => 'public.iot_readings',
            p_control      => 'recorded_at',
            p_interval     => '1 month',
            p_premake      => 3
        )
    """)

    # ── iot_alerts ───────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE iot_alerts (
            id                       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id                UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            sensor_id                UUID        NOT NULL REFERENCES iot_sensors(id) ON DELETE CASCADE,
            alert_type               VARCHAR(30) NOT NULL,
            severity                 VARCHAR(10) NOT NULL DEFAULT 'medium',
            value                    NUMERIC,
            message                  TEXT,
            status                   VARCHAR(20) NOT NULL DEFAULT 'open',
            acknowledged_by_user_id  UUID        REFERENCES users(id),
            acknowledged_at          TIMESTAMPTZ,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_iot_alerts_tenant_status ON iot_alerts(tenant_id, status, created_at DESC)")
    op.execute("CREATE INDEX idx_iot_alerts_sensor        ON iot_alerts(sensor_id, created_at DESC)")

    op.execute("ALTER TABLE iot_alerts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE iot_alerts FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_iot_alerts ON iot_alerts
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── New permissions ───────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
            ('iot:read',   'View IoT sensor readings and alerts',      'iot'),
            ('iot:write',  'Create / edit IoT sensors and thresholds', 'iot'),
            ('iot:ingest', 'Push readings from IoT devices',           'iot')
        ON CONFLICT (code) DO NOTHING
    """)
    # Grant to roles 1-4 (super_admin, admin, supervisor, operator) for read;
    # write to 1-3; ingest to 1-4
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM   roles r, permissions p
        WHERE  p.code = 'iot:read'  AND r.id IN (1,2,3,4,5,6)
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM   roles r, permissions p
        WHERE  p.code = 'iot:write' AND r.id IN (1,2,3)
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM   roles r, permissions p
        WHERE  p.code = 'iot:ingest' AND r.id IN (1,2,3,4)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('iot:read','iot:write','iot:ingest'))")
    op.execute("DELETE FROM permissions WHERE code IN ('iot:read','iot:write','iot:ingest')")
    op.execute("DROP TABLE IF EXISTS iot_alerts")
    op.execute("DROP TABLE IF EXISTS iot_readings")
    op.execute("DROP TABLE IF EXISTS iot_sensors")
