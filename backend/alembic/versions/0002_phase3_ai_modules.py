"""Phase 3: PPE / Crowd / Fire-Smoke / Weapon / Behavior AI module tables.

All five follow the identical partitioned-FK pattern from 0001 — one row per
event, detected_at partitioned by month, composite (detection_id, detected_at)
PK + FK into detections, same RLS triplet.

Also adds crowd_zones (supporting table for crowd density, parallel to
restricted_zones used by intrusion).

Revision ID: 0002
Revises:     0001
Create Date: 2026-06-18
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
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
    "crowd_zones",
    "ppe_events",
    "crowd_events",
    "fire_smoke_events",
    "weapon_events",
    "behavior_events",
]


def upgrade() -> None:
    # --- crowd_zones: capacity-bounded zone for crowd density monitoring ---
    op.execute(
        """
        CREATE TABLE crowd_zones (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            polygon JSONB NOT NULL,
            max_capacity INTEGER NOT NULL DEFAULT 10,
            severity VARCHAR(10) NOT NULL DEFAULT 'medium'
                CHECK (severity IN ('low','medium','high','critical')),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_crowd_zones_camera_active ON crowd_zones(camera_id) WHERE is_active = TRUE;
        """
    )

    # --- PPE Detection ---
    op.execute(
        """
        CREATE TABLE ppe_events (
            detection_id UUID NOT NULL,
            detected_at  TIMESTAMPTZ NOT NULL,
            tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id    UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            person_bbox  JSONB NOT NULL,
            items_detected JSONB NOT NULL DEFAULT '[]',
            items_missing  JSONB NOT NULL DEFAULT '[]',
            severity     VARCHAR(10) NOT NULL DEFAULT 'high'
                CHECK (severity IN ('low','medium','high','critical')),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (detection_id, detected_at),
            FOREIGN KEY (detection_id, detected_at)
                REFERENCES detections(id, detected_at) ON DELETE CASCADE
        ) PARTITION BY RANGE (detected_at);
        CREATE INDEX idx_ppe_events_tenant_camera ON ppe_events(tenant_id, camera_id);
        """
    )

    # --- Crowd Density ---
    op.execute(
        """
        CREATE TABLE crowd_events (
            detection_id  UUID NOT NULL,
            detected_at   TIMESTAMPTZ NOT NULL,
            tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id     UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            zone_id       UUID NOT NULL REFERENCES crowd_zones(id),
            person_count  INTEGER NOT NULL,
            max_capacity  INTEGER NOT NULL,
            density_ratio NUMERIC(5,4) NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (detection_id, detected_at),
            FOREIGN KEY (detection_id, detected_at)
                REFERENCES detections(id, detected_at) ON DELETE CASCADE
        ) PARTITION BY RANGE (detected_at);
        CREATE INDEX idx_crowd_events_tenant_zone ON crowd_events(tenant_id, zone_id);
        """
    )

    # --- Fire/Smoke Detection ---
    op.execute(
        """
        CREATE TABLE fire_smoke_events (
            detection_id    UUID NOT NULL,
            detected_at     TIMESTAMPTZ NOT NULL,
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id       UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            detection_type  VARCHAR(10) NOT NULL CHECK (detection_type IN ('fire','smoke')),
            confidence      NUMERIC(5,4) NOT NULL,
            bbox            JSONB NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (detection_id, detected_at),
            FOREIGN KEY (detection_id, detected_at)
                REFERENCES detections(id, detected_at) ON DELETE CASCADE
        ) PARTITION BY RANGE (detected_at);
        CREATE INDEX idx_fire_smoke_events_tenant_camera
            ON fire_smoke_events(tenant_id, camera_id);
        """
    )

    # --- Weapon Detection ---
    op.execute(
        """
        CREATE TABLE weapon_events (
            detection_id UUID NOT NULL,
            detected_at  TIMESTAMPTZ NOT NULL,
            tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id    UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            weapon_type  VARCHAR(30) NOT NULL,
            confidence   NUMERIC(5,4) NOT NULL,
            bbox         JSONB NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (detection_id, detected_at),
            FOREIGN KEY (detection_id, detected_at)
                REFERENCES detections(id, detected_at) ON DELETE CASCADE
        ) PARTITION BY RANGE (detected_at);
        CREATE INDEX idx_weapon_events_tenant_camera ON weapon_events(tenant_id, camera_id);
        """
    )

    # --- Behavior Analysis ---
    op.execute(
        """
        CREATE TABLE behavior_events (
            detection_id     UUID NOT NULL,
            detected_at      TIMESTAMPTZ NOT NULL,
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id        UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            zone_id          UUID REFERENCES restricted_zones(id),
            behavior_type    VARCHAR(30) NOT NULL,
            confidence       NUMERIC(5,4),
            duration_seconds NUMERIC(8,2),
            person_bbox      JSONB,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (detection_id, detected_at),
            FOREIGN KEY (detection_id, detected_at)
                REFERENCES detections(id, detected_at) ON DELETE CASCADE
        ) PARTITION BY RANGE (detected_at);
        CREATE INDEX idx_behavior_events_tenant_camera ON behavior_events(tenant_id, camera_id);
        CREATE INDEX idx_behavior_events_type ON behavior_events(tenant_id, behavior_type);
        """
    )

    for table in NEW_RLS_TABLES:
        op.execute(_rls(table))

    op.execute(
        """
        SELECT public.create_parent(p_parent_table => 'public.ppe_events',
            p_control => 'detected_at', p_interval => '1 month', p_premake => 3);
        SELECT public.create_parent(p_parent_table => 'public.crowd_events',
            p_control => 'detected_at', p_interval => '1 month', p_premake => 3);
        SELECT public.create_parent(p_parent_table => 'public.fire_smoke_events',
            p_control => 'detected_at', p_interval => '1 month', p_premake => 3);
        SELECT public.create_parent(p_parent_table => 'public.weapon_events',
            p_control => 'detected_at', p_interval => '1 month', p_premake => 3);
        SELECT public.create_parent(p_parent_table => 'public.behavior_events',
            p_control => 'detected_at', p_interval => '1 month', p_premake => 3);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM part_config_sub WHERE sub_parent IN (
            'public.ppe_events', 'public.crowd_events', 'public.fire_smoke_events',
            'public.weapon_events', 'public.behavior_events'
        );
        DELETE FROM part_config WHERE parent_table IN (
            'public.ppe_events', 'public.crowd_events', 'public.fire_smoke_events',
            'public.weapon_events', 'public.behavior_events'
        );
        DROP TABLE IF EXISTS
            template_public_ppe_events, template_public_crowd_events,
            template_public_fire_smoke_events, template_public_weapon_events,
            template_public_behavior_events
        CASCADE;
        """
    )
    op.execute(
        """
        DROP TABLE IF EXISTS behavior_events CASCADE;
        DROP TABLE IF EXISTS weapon_events CASCADE;
        DROP TABLE IF EXISTS fire_smoke_events CASCADE;
        DROP TABLE IF EXISTS crowd_events CASCADE;
        DROP TABLE IF EXISTS ppe_events CASCADE;
        DROP TABLE IF EXISTS crowd_zones CASCADE;
        """
    )
