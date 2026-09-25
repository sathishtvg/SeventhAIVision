"""Drone patrol, phase 7: CCTV correlation — which fixed cameras could have seen
what a drone saw, and what they recorded.

Still additive and still drone-only: one new table (decision D2) and new
columns on two drone tables. `cameras`, `streams`, `recordings`, `detections`
and `alerts` are read, never changed.

COVERAGE IS OPTIONAL AND SEPARATE. Cameras store a point, not what they can see.
drone_camera_coverage adds, for the cameras someone has surveyed, either a
viewing sector (heading, field of view, range) or an explicit coverage polygon.
Until a camera has one, it is correlated by distance and said to be NEARBY;
with one, a camera that can actually see the spot is said to COVER it and is
listed first. A camera table the whole platform uses gains nothing.

A CORRELATION IS A ROW PER (EVENT, CAMERA) — drone_event_cameras, created in
phase 2 — now carrying what an operator needs to look: the bearing and whether
the camera covers the spot, what that camera detected in the event's window and
whether it corroborates the drone (a person for a person, the same plate for a
plate), its most serious alert, the recording covering the moment and the offset
into it, and whether the camera is online. Refreshed as the event grows, and
settled (cctv_final) once late detections can no longer arrive.

Revision ID: 0127
Revises: 0126
"""
from alembic import op

revision = "0127"
down_revision = "0126"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE drone_camera_coverage (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id          UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            heading_deg        NUMERIC(5,2),
            fov_deg            NUMERIC(5,2),
            range_m            NUMERIC(7,1),
            coverage_polygon   JSONB,
            notes              TEXT,
            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dcc_camera   UNIQUE (camera_id),
            CONSTRAINT ck_dcc_heading  CHECK (heading_deg IS NULL OR (heading_deg >= 0 AND heading_deg < 360)),
            CONSTRAINT ck_dcc_fov      CHECK (fov_deg IS NULL OR (fov_deg > 0 AND fov_deg <= 360)),
            CONSTRAINT ck_dcc_range    CHECK (range_m IS NULL OR (range_m > 0 AND range_m <= 5000)),
            CONSTRAINT ck_dcc_defined  CHECK (coverage_polygon IS NOT NULL
                                              OR (heading_deg IS NOT NULL AND fov_deg IS NOT NULL AND range_m IS NOT NULL))
        )
    """)
    op.execute("ALTER TABLE drone_camera_coverage ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE drone_camera_coverage FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_drone_camera_coverage ON drone_camera_coverage
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("GRANT ALL ON drone_camera_coverage TO svc_app")

    op.execute("""
        ALTER TABLE drone_event_cameras
            ADD COLUMN bearing_deg             NUMERIC(5,2),
            ADD COLUMN in_coverage             BOOLEAN,
            ADD COLUMN corroborates            BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN related_detection_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN related_module_type     VARCHAR(20),
            ADD COLUMN related_detected_at     TIMESTAMPTZ,
            ADD COLUMN recording_id            UUID REFERENCES recordings(id) ON DELETE SET NULL,
            ADD COLUMN recording_offset_s      NUMERIC(9,2),
            ADD COLUMN camera_online           BOOLEAN,
            ADD COLUMN rank                    SMALLINT,
            ADD COLUMN updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            ADD CONSTRAINT ck_decam_related CHECK (related_detection_count >= 0)
    """)
    op.execute("""
        ALTER TABLE drone_events
            ADD COLUMN cctv_correlated_at TIMESTAMPTZ,
            ADD COLUMN cctv_final         BOOLEAN NOT NULL DEFAULT FALSE
    """)
    op.execute("CREATE INDEX idx_devent_cctv_due ON drone_events (detected_at) WHERE NOT cctv_final")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_devent_cctv_due")
    op.execute("""
        ALTER TABLE drone_events
            DROP COLUMN IF EXISTS cctv_final,
            DROP COLUMN IF EXISTS cctv_correlated_at
    """)
    op.execute("""
        ALTER TABLE drone_event_cameras
            DROP CONSTRAINT IF EXISTS ck_decam_related,
            DROP COLUMN IF EXISTS updated_at,
            DROP COLUMN IF EXISTS rank,
            DROP COLUMN IF EXISTS camera_online,
            DROP COLUMN IF EXISTS recording_offset_s,
            DROP COLUMN IF EXISTS recording_id,
            DROP COLUMN IF EXISTS related_detected_at,
            DROP COLUMN IF EXISTS related_module_type,
            DROP COLUMN IF EXISTS related_detection_count,
            DROP COLUMN IF EXISTS corroborates,
            DROP COLUMN IF EXISTS in_coverage,
            DROP COLUMN IF EXISTS bearing_deg
    """)
    op.execute("DROP TABLE IF EXISTS drone_camera_coverage CASCADE")
