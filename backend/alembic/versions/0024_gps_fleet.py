"""GPS Vehicle / Fleet Tracking — vehicles, positions, geofences, journeys

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── vehicles ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE vehicles (
            id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id                 UUID        REFERENCES sites(id) ON DELETE SET NULL,
            name                    VARCHAR(255) NOT NULL,
            plate_number            VARCHAR(30),
            vehicle_type            VARCHAR(30)  NOT NULL DEFAULT 'patrol_car',
            make                    VARCHAR(100),
            model                   VARCHAR(100),
            color                   VARCHAR(50),
            assigned_driver_user_id UUID        REFERENCES users(id) ON DELETE SET NULL,
            is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
            last_position_at        TIMESTAMPTZ,
            last_lat                DOUBLE PRECISION,
            last_lon                DOUBLE PRECISION,
            last_speed              NUMERIC(6,2),
            current_status          VARCHAR(20) NOT NULL DEFAULT 'offline',
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_vehicles_tenant    ON vehicles(tenant_id)")
    op.execute("CREATE INDEX idx_vehicles_site      ON vehicles(site_id)")
    op.execute("CREATE INDEX idx_vehicles_status    ON vehicles(tenant_id, current_status)")

    op.execute("ALTER TABLE vehicles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE vehicles FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_vehicles ON vehicles
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── vehicle_positions (partitioned monthly) ───────────────────────────────
    op.execute("""
        CREATE TABLE vehicle_positions (
            id          UUID            NOT NULL DEFAULT gen_random_uuid(),
            tenant_id   UUID            NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            vehicle_id  UUID            NOT NULL,
            lat         DOUBLE PRECISION NOT NULL,
            lon         DOUBLE PRECISION NOT NULL,
            speed       NUMERIC(6,2),
            heading     SMALLINT,
            accuracy    NUMERIC(6,2),
            recorded_at TIMESTAMPTZ     NOT NULL DEFAULT now(),
            PRIMARY KEY (id, recorded_at)
        ) PARTITION BY RANGE (recorded_at)
    """)
    op.execute("CREATE INDEX idx_vpos_vehicle_time ON vehicle_positions(vehicle_id, recorded_at DESC)")
    op.execute("CREATE INDEX idx_vpos_tenant_time  ON vehicle_positions(tenant_id, recorded_at DESC)")

    op.execute("ALTER TABLE vehicle_positions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE vehicle_positions FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_vehicle_positions ON vehicle_positions
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        SELECT public.create_parent(
            p_parent_table => 'public.vehicle_positions',
            p_control      => 'recorded_at',
            p_interval     => '1 month',
            p_premake      => 3
        )
    """)

    # ── geofences ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE geofences (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id         UUID        REFERENCES sites(id) ON DELETE SET NULL,
            name            VARCHAR(255) NOT NULL,
            description     TEXT,
            polygon         JSONB       NOT NULL,
            alert_on_entry  BOOLEAN     NOT NULL DEFAULT TRUE,
            alert_on_exit   BOOLEAN     NOT NULL DEFAULT FALSE,
            speed_limit     NUMERIC(6,2),
            is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_geofences_tenant ON geofences(tenant_id)")

    op.execute("ALTER TABLE geofences ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE geofences FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_geofences ON geofences
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── geofence_events ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE geofence_events (
            id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            vehicle_id   UUID        NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            geofence_id  UUID        NOT NULL REFERENCES geofences(id) ON DELETE CASCADE,
            event_type   VARCHAR(20) NOT NULL,
            lat          DOUBLE PRECISION,
            lon          DOUBLE PRECISION,
            speed        NUMERIC(6,2),
            occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_geoevents_tenant   ON geofence_events(tenant_id, occurred_at DESC)")
    op.execute("CREATE INDEX idx_geoevents_vehicle  ON geofence_events(vehicle_id, occurred_at DESC)")

    op.execute("ALTER TABLE geofence_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE geofence_events FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_geofence_events ON geofence_events
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── vehicle_journeys ──────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE vehicle_journeys (
            id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            vehicle_id   UUID        NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            start_lat    DOUBLE PRECISION,
            start_lon    DOUBLE PRECISION,
            end_lat      DOUBLE PRECISION,
            end_lon      DOUBLE PRECISION,
            start_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            end_at       TIMESTAMPTZ,
            distance_km  NUMERIC(10,3) NOT NULL DEFAULT 0,
            max_speed    NUMERIC(6,2),
            avg_speed    NUMERIC(6,2),
            status       VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_journeys_tenant  ON vehicle_journeys(tenant_id, start_at DESC)")
    op.execute("CREATE INDEX idx_journeys_vehicle ON vehicle_journeys(vehicle_id, start_at DESC)")

    op.execute("ALTER TABLE vehicle_journeys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE vehicle_journeys FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_vehicle_journeys ON vehicle_journeys
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
            ('gps:read',   'View vehicle positions, journeys, geofences', 'gps'),
            ('gps:write',  'Create / edit vehicles and geofences',        'gps'),
            ('gps:ingest', 'Push GPS positions from tracker devices',     'gps')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE p.code = 'gps:read' AND r.id IN (1,2,3,4,5,6)
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE p.code = 'gps:write' AND r.id IN (1,2,3)
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE p.code = 'gps:ingest' AND r.id IN (1,2,3,4)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('gps:read','gps:write','gps:ingest'))")
    op.execute("DELETE FROM permissions WHERE code IN ('gps:read','gps:write','gps:ingest')")
    op.execute("DROP TABLE IF EXISTS vehicle_journeys")
    op.execute("DROP TABLE IF EXISTS geofence_events")
    op.execute("DROP TABLE IF EXISTS geofences")
    op.execute("DROP TABLE IF EXISTS vehicle_positions")
    op.execute("DROP TABLE IF EXISTS vehicles")
