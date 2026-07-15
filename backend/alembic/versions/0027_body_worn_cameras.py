"""Body Worn Camera (BWC) Management

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-25
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── body_cameras ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE body_cameras (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            serial_number       VARCHAR(100) NOT NULL,
            model               VARCHAR(100),
            firmware_version    VARCHAR(50),
            status              VARCHAR(20) NOT NULL DEFAULT 'available',
            battery_pct         SMALLINT,
            storage_used_gb     NUMERIC(6,2),
            storage_total_gb    NUMERIC(6,2) NOT NULL DEFAULT 64,
            assigned_user_id    UUID        REFERENCES users(id) ON DELETE SET NULL,
            assigned_at         TIMESTAMPTZ,
            last_docked_at      TIMESTAMPTZ,
            last_sync_at        TIMESTAMPTZ,
            is_recording        BOOLEAN     NOT NULL DEFAULT FALSE,
            notes               TEXT,
            is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, serial_number)
        )
    """)
    op.execute("CREATE INDEX idx_body_cameras_tenant  ON body_cameras(tenant_id)")
    op.execute("CREATE INDEX idx_body_cameras_user    ON body_cameras(assigned_user_id)")

    op.execute("ALTER TABLE body_cameras ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE body_cameras FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_body_cameras ON body_cameras
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── bwc_assignments ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE bwc_assignments (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id       UUID        NOT NULL REFERENCES body_cameras(id) ON DELETE CASCADE,
            user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            assigned_by     UUID        REFERENCES users(id) ON DELETE SET NULL,
            assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            returned_at     TIMESTAMPTZ,
            shift_id        UUID,
            notes           TEXT
        )
    """)
    op.execute("CREATE INDEX idx_bwc_assignments_camera ON bwc_assignments(camera_id)")
    op.execute("CREATE INDEX idx_bwc_assignments_user   ON bwc_assignments(user_id)")

    op.execute("ALTER TABLE bwc_assignments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE bwc_assignments FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_bwc_assignments ON bwc_assignments
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── bwc_recordings ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE bwc_recordings (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id           UUID        NOT NULL REFERENCES body_cameras(id) ON DELETE CASCADE,
            user_id             UUID        REFERENCES users(id) ON DELETE SET NULL,
            incident_id         UUID,
            title               VARCHAR(255),
            started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at            TIMESTAMPTZ,
            duration_seconds    INTEGER,
            file_size_mb        NUMERIC(8,2),
            file_path           VARCHAR(500),
            trigger_type        VARCHAR(30) NOT NULL DEFAULT 'manual',
            latitude            DOUBLE PRECISION,
            longitude           DOUBLE PRECISION,
            status              VARCHAR(20) NOT NULL DEFAULT 'recording',
            notes               TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_bwc_recordings_tenant  ON bwc_recordings(tenant_id, started_at DESC)")
    op.execute("CREATE INDEX idx_bwc_recordings_camera  ON bwc_recordings(camera_id)")
    op.execute("CREATE INDEX idx_bwc_recordings_user    ON bwc_recordings(user_id)")

    op.execute("ALTER TABLE bwc_recordings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE bwc_recordings FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_bwc_recordings ON bwc_recordings
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── bwc_events ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE bwc_events (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id       UUID        NOT NULL REFERENCES body_cameras(id) ON DELETE CASCADE,
            user_id         UUID        REFERENCES users(id) ON DELETE SET NULL,
            event_type      VARCHAR(50) NOT NULL,
            detail          TEXT,
            battery_pct     SMALLINT,
            occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_bwc_events_camera ON bwc_events(camera_id, occurred_at DESC)")
    op.execute("CREATE INDEX idx_bwc_events_tenant ON bwc_events(tenant_id, occurred_at DESC)")

    op.execute("ALTER TABLE bwc_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE bwc_events FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_bwc_events ON bwc_events
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
            ('bwc:read',   'View body cameras, recordings, assignments', 'bwc'),
            ('bwc:write',  'Assign cameras, start/stop recordings',      'bwc'),
            ('bwc:manage', 'Register, configure, retire cameras',        'bwc')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE p.code = 'bwc:read' AND r.id IN (1,2,3,4,5,6)
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE p.code = 'bwc:write' AND r.id IN (1,2,3,4)
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE p.code = 'bwc:manage' AND r.id IN (1,2,3)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('bwc:read','bwc:write','bwc:manage'))")
    op.execute("DELETE FROM permissions WHERE code IN ('bwc:read','bwc:write','bwc:manage')")
    op.execute("DROP TABLE IF EXISTS bwc_events")
    op.execute("DROP TABLE IF EXISTS bwc_recordings")
    op.execute("DROP TABLE IF EXISTS bwc_assignments")
    op.execute("DROP TABLE IF EXISTS body_cameras")
