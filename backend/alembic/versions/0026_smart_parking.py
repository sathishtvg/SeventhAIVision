"""Smart Parking / Carpark Management

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-25
"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── car_parks ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE car_parks (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id         UUID        REFERENCES sites(id) ON DELETE SET NULL,
            name            VARCHAR(255) NOT NULL,
            description     TEXT,
            total_capacity  INTEGER     NOT NULL DEFAULT 0,
            levels          INTEGER     NOT NULL DEFAULT 1,
            address         TEXT,
            is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_car_parks_tenant ON car_parks(tenant_id)")
    op.execute("CREATE INDEX idx_car_parks_site   ON car_parks(site_id)")

    op.execute("ALTER TABLE car_parks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE car_parks FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_car_parks ON car_parks
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── parking_zones ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE parking_zones (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            car_park_id     UUID        NOT NULL REFERENCES car_parks(id) ON DELETE CASCADE,
            name            VARCHAR(100) NOT NULL,
            zone_type       VARCHAR(30) NOT NULL DEFAULT 'regular',
            level           INTEGER     NOT NULL DEFAULT 1,
            capacity        INTEGER     NOT NULL DEFAULT 0,
            is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_parking_zones_carpark ON parking_zones(car_park_id)")

    op.execute("ALTER TABLE parking_zones ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE parking_zones FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_parking_zones ON parking_zones
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── parking_bays ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE parking_bays (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            zone_id         UUID        NOT NULL REFERENCES parking_zones(id) ON DELETE CASCADE,
            car_park_id     UUID        NOT NULL REFERENCES car_parks(id) ON DELETE CASCADE,
            bay_number      VARCHAR(20) NOT NULL,
            status          VARCHAR(20) NOT NULL DEFAULT 'available',
            vehicle_plate   VARCHAR(20),
            occupied_since  TIMESTAMPTZ,
            notes           TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_parking_bays_zone    ON parking_bays(zone_id)")
    op.execute("CREATE INDEX idx_parking_bays_carpark ON parking_bays(car_park_id, status)")

    op.execute("ALTER TABLE parking_bays ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE parking_bays FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_parking_bays ON parking_bays
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── parking_rates ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE parking_rates (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            car_park_id     UUID        NOT NULL REFERENCES car_parks(id) ON DELETE CASCADE,
            zone_type       VARCHAR(30) NOT NULL DEFAULT 'regular',
            rate_name       VARCHAR(100) NOT NULL,
            first_hour_rate NUMERIC(8,2) NOT NULL DEFAULT 0,
            subsequent_rate NUMERIC(8,2) NOT NULL DEFAULT 0,
            daily_max_rate  NUMERIC(8,2),
            currency        VARCHAR(3)  NOT NULL DEFAULT 'SGD',
            is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_parking_rates_carpark ON parking_rates(car_park_id)")

    op.execute("ALTER TABLE parking_rates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE parking_rates FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_parking_rates ON parking_rates
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── parking_sessions ──────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE parking_sessions (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            car_park_id         UUID        NOT NULL REFERENCES car_parks(id) ON DELETE CASCADE,
            zone_id             UUID        REFERENCES parking_zones(id) ON DELETE SET NULL,
            bay_id              UUID        REFERENCES parking_bays(id) ON DELETE SET NULL,
            vehicle_plate       VARCHAR(20),
            vehicle_type        VARCHAR(30) NOT NULL DEFAULT 'car',
            entry_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            exit_at             TIMESTAMPTZ,
            duration_minutes    INTEGER,
            fee_amount          NUMERIC(8,2),
            currency            VARCHAR(3)  NOT NULL DEFAULT 'SGD',
            payment_status      VARCHAR(20) NOT NULL DEFAULT 'unpaid',
            payment_method      VARCHAR(30),
            entry_camera_id     UUID        REFERENCES cameras(id) ON DELETE SET NULL,
            exit_camera_id      UUID        REFERENCES cameras(id) ON DELETE SET NULL,
            operator_notes      TEXT,
            status              VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_parking_sessions_tenant  ON parking_sessions(tenant_id, status, entry_at DESC)")
    op.execute("CREATE INDEX idx_parking_sessions_carpark ON parking_sessions(car_park_id, status)")
    op.execute("CREATE INDEX idx_parking_sessions_plate   ON parking_sessions(tenant_id, vehicle_plate)")

    op.execute("ALTER TABLE parking_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE parking_sessions FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_parking_sessions ON parking_sessions
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
            ('parking:read',   'View parking status, sessions, occupancy', 'parking'),
            ('parking:write',  'Log vehicle entry/exit, update bay status', 'parking'),
            ('parking:manage', 'Configure carparks, zones, rates',          'parking')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE p.code = 'parking:read' AND r.id IN (1,2,3,4,5,6)
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE p.code = 'parking:write' AND r.id IN (1,2,3,4)
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE p.code = 'parking:manage' AND r.id IN (1,2,3)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('parking:read','parking:write','parking:manage'))")
    op.execute("DELETE FROM permissions WHERE code IN ('parking:read','parking:write','parking:manage')")
    op.execute("DROP TABLE IF EXISTS parking_sessions")
    op.execute("DROP TABLE IF EXISTS parking_rates")
    op.execute("DROP TABLE IF EXISTS parking_bays")
    op.execute("DROP TABLE IF EXISTS parking_zones")
    op.execute("DROP TABLE IF EXISTS car_parks")
