"""parking_lpr_cameras — maps cameras to carparks for LPR-triggered auto-entry/exit

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE parking_lpr_cameras (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id       UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            car_park_id     UUID NOT NULL REFERENCES car_parks(id) ON DELETE CASCADE,
            trigger_type    VARCHAR(10) NOT NULL DEFAULT 'both'
                            CHECK (trigger_type IN ('entry', 'exit', 'both')),
            default_zone_id UUID REFERENCES parking_zones(id) ON DELETE SET NULL,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            notes           TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, camera_id)
        )
    """)
    op.execute("CREATE INDEX idx_parking_lpr_cameras_tenant ON parking_lpr_cameras(tenant_id)")
    op.execute("CREATE INDEX idx_parking_lpr_cameras_camera ON parking_lpr_cameras(camera_id)")

    op.execute("ALTER TABLE parking_lpr_cameras ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE parking_lpr_cameras FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_parking_lpr_cameras ON parking_lpr_cameras
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # Track whether a parking session was auto-created by LPR
    op.execute("""
        ALTER TABLE parking_sessions
        ADD COLUMN IF NOT EXISTS lpr_triggered BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS lpr_detection_id UUID
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE parking_sessions DROP COLUMN IF EXISTS lpr_detection_id")
    op.execute("ALTER TABLE parking_sessions DROP COLUMN IF EXISTS lpr_triggered")
    op.execute("DROP TABLE IF EXISTS parking_lpr_cameras")
