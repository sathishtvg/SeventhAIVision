"""Advanced AI modules: camera tampering, abandoned objects, slip/fall detection.

All three follow the same partitioned-FK pattern as the Phase 3 tables.

Revision ID: 0011
Revises:     0010
Create Date: 2026-06-22
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def _rls(table: str) -> str:
    return f"""
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation_{table} ON {table}
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """


NEW_RLS_TABLES = [
    "tampering_events",
    "abandoned_object_events",
    "fall_events",
]


def upgrade() -> None:
    # ── Camera tampering events ──────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE tampering_events (
            detection_id    UUID        NOT NULL,
            detected_at     TIMESTAMPTZ NOT NULL,
            tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id       UUID        NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            tampering_type  VARCHAR(30) NOT NULL
                CHECK (tampering_type IN ('blocked', 'moved', 'covered', 'defocused')),
            score           NUMERIC(5,4) NOT NULL,
            reason          TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (detection_id, detected_at),
            FOREIGN KEY (detection_id, detected_at)
                REFERENCES detections(id, detected_at) ON DELETE CASCADE
        ) PARTITION BY RANGE (detected_at);
        CREATE INDEX idx_tampering_events_tenant_camera
            ON tampering_events(tenant_id, camera_id);
        """
    )
    for t in ["tampering_events"]:
        op.execute(_rls(t))

    # ── Abandoned object events ──────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE abandoned_object_events (
            detection_id    UUID        NOT NULL,
            detected_at     TIMESTAMPTZ NOT NULL,
            tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id       UUID        NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            object_class    VARCHAR(50),
            dwell_seconds   NUMERIC(8,2) NOT NULL DEFAULT 0,
            bbox            JSONB        NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (detection_id, detected_at),
            FOREIGN KEY (detection_id, detected_at)
                REFERENCES detections(id, detected_at) ON DELETE CASCADE
        ) PARTITION BY RANGE (detected_at);
        CREATE INDEX idx_abandoned_object_events_tenant_camera
            ON abandoned_object_events(tenant_id, camera_id);
        """
    )
    for t in ["abandoned_object_events"]:
        op.execute(_rls(t))

    # ── Slip/fall events ─────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE fall_events (
            detection_id    UUID        NOT NULL,
            detected_at     TIMESTAMPTZ NOT NULL,
            tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id       UUID        NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            fall_confidence NUMERIC(5,4) NOT NULL,
            pose_keypoints  JSONB,
            person_bbox     JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (detection_id, detected_at),
            FOREIGN KEY (detection_id, detected_at)
                REFERENCES detections(id, detected_at) ON DELETE CASCADE
        ) PARTITION BY RANGE (detected_at);
        CREATE INDEX idx_fall_events_tenant_camera
            ON fall_events(tenant_id, camera_id);
        """
    )
    for t in ["fall_events"]:
        op.execute(_rls(t))

    # ── Grant permissions seed ───────────────────────────────────────────
    op.execute(
        """
        INSERT INTO permissions (code, description, category) VALUES
            ('tampering:read',  'View camera tampering events',     'tampering'),
            ('abandoned:read',  'View abandoned object events',     'abandoned'),
            ('fall:read',       'View slip/fall detection events',  'fall')
        ON CONFLICT (code) DO NOTHING;
        """
    )

    # Grant to roles 1-5 (viewer=6 is read-only on detections but not these specialized events)
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r, permissions p
        WHERE p.code IN ('tampering:read', 'abandoned:read', 'fall:read')
          AND r.id IN (1,2,3,4,5)
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('tampering:read','abandoned:read','fall:read'))")
    op.execute("DELETE FROM permissions WHERE code IN ('tampering:read','abandoned:read','fall:read')")
    op.execute("DROP TABLE IF EXISTS fall_events CASCADE")
    op.execute("DROP TABLE IF EXISTS abandoned_object_events CASCADE")
    op.execute("DROP TABLE IF EXISTS tampering_events CASCADE")
