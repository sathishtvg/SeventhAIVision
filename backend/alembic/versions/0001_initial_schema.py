"""Initial schema: tenants, RBAC catalogue + seed data, cameras/streams, the
generic detections table plus LPR/face/intrusion child tables, alerts/incidents/
evidence/audit_logs, tenant_settings. RLS policies and pg_partman partition
registration are part of this same migration (not a separate step) so schema and
security policy can never drift apart — see plan §2/§16.

pg_partman note: verified against the real installed v5.4.3 (see chat history) —
its functions live in `public`, not a `partman` schema, and create_parent()'s
p_type defaults to 'range' (not 'native'), which is exactly what's wanted here,
so it's omitted rather than guessed at.

Revision ID: 0001
Revises:
Create Date: 2026-06-16
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


# ---------------------------------------------------------------------------
# RLS policy triplet, identical for every tenant-scoped table (plan §2).
# ---------------------------------------------------------------------------
def _rls(table: str) -> str:
    return f"""
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation_{table} ON {table}
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """


RLS_TABLES = [
    "users", "refresh_tokens", "streams", "camera_health_events", "cameras",
    "detections", "alerts", "incidents", "incident_notes", "evidence", "audit_logs",
    "lpr_events", "watchlist_entries", "face_watchlist_entries", "face_events",
    "restricted_zones", "intrusion_events", "tenant_settings",
]


def upgrade() -> None:
    # --- Global catalogues: no tenant_id, no RLS — every tenant reads the same rows ---
    op.execute(
        """
        CREATE TABLE roles (
            id SMALLINT PRIMARY KEY,
            code VARCHAR(50) NOT NULL UNIQUE,
            name VARCHAR(100) NOT NULL,
            description TEXT
        );

        CREATE TABLE permissions (
            id SERIAL PRIMARY KEY,
            code VARCHAR(100) NOT NULL UNIQUE,
            description TEXT,
            category VARCHAR(50)
        );

        CREATE TABLE role_permissions (
            role_id SMALLINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
            PRIMARY KEY (role_id, permission_id)
        );
        """
    )

    # --- Tenants + users + cameras (unpartitioned, low/moderate volume) ---
    op.execute(
        """
        CREATE TABLE tenants (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(255) NOT NULL,
            slug VARCHAR(100) NOT NULL UNIQUE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            role_id SMALLINT NOT NULL REFERENCES roles(id),
            email VARCHAR(255) NOT NULL,
            hashed_password VARCHAR(255) NOT NULL,
            full_name VARCHAR(255),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            last_login_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, email)
        );
        CREATE INDEX idx_users_tenant_id ON users(tenant_id);

        CREATE TABLE refresh_tokens (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash VARCHAR(255) NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_refresh_tokens_tenant_id ON refresh_tokens(tenant_id);
        CREATE INDEX idx_refresh_tokens_user_id ON refresh_tokens(user_id);

        CREATE TABLE cameras (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            location VARCHAR(255),
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            ai_modules_enabled JSONB NOT NULL DEFAULT '[]',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_cameras_tenant_id ON cameras(tenant_id);
        CREATE INDEX idx_cameras_ai_modules_enabled ON cameras USING GIN (ai_modules_enabled);

        CREATE TABLE streams (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            protocol VARCHAR(20) NOT NULL DEFAULT 'rtsp',
            url VARCHAR(500) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'offline',
            last_frame_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_streams_tenant_id ON streams(tenant_id);
        CREATE INDEX idx_streams_camera_id ON streams(camera_id);

        CREATE TABLE camera_health_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            event_type VARCHAR(50) NOT NULL,
            detail TEXT,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_camera_health_camera_id_time ON camera_health_events(camera_id, occurred_at DESC);
        """
    )

    # --- Partitioned firehose: detections (generic parent for every AI module) ---
    op.execute(
        """
        CREATE TABLE detections (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            module_type VARCHAR(30) NOT NULL,
            confidence NUMERIC(5,4),
            bounding_box JSONB,
            raw_metadata JSONB,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, detected_at)
        ) PARTITION BY RANGE (detected_at);
        CREATE INDEX idx_detections_tenant_module_time ON detections(tenant_id, module_type, detected_at DESC);
        CREATE INDEX idx_detections_camera_id_time ON detections(camera_id, detected_at DESC);
        """
    )

    # --- alerts/incidents/incident_notes/evidence: see plan §2 for why
    #     detection_id/incident_id are plain indexed columns, not FKs, here ---
    op.execute(
        """
        CREATE TABLE alerts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            detection_id UUID,
            camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            module_type VARCHAR(30) NOT NULL,
            severity VARCHAR(10) NOT NULL DEFAULT 'medium',
            alert_code VARCHAR(100),
            message_params JSONB,
            title VARCHAR(255) NOT NULL,
            message TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            acknowledged_by_user_id UUID REFERENCES users(id),
            acknowledged_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_alerts_tenant_status_time ON alerts(tenant_id, status, created_at DESC);
        CREATE INDEX idx_alerts_detection_id ON alerts(detection_id);
        CREATE INDEX idx_alerts_camera_id ON alerts(camera_id);

        CREATE TABLE incidents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            alert_id UUID REFERENCES alerts(id) ON DELETE SET NULL,
            camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            alert_code VARCHAR(100),
            message_params JSONB,
            severity VARCHAR(10) NOT NULL DEFAULT 'medium',
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            is_auto_created BOOLEAN NOT NULL DEFAULT FALSE,
            assigned_to_user_id UUID REFERENCES users(id),
            resolved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_incidents_tenant_status_time ON incidents(tenant_id, status, created_at DESC);

        CREATE TABLE incident_notes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            author_user_id UUID REFERENCES users(id),
            note TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_incident_notes_incident_id ON incident_notes(incident_id, created_at);

        CREATE TABLE evidence (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            detection_id UUID,
            incident_id UUID,
            media_type VARCHAR(10) NOT NULL DEFAULT 'image',
            storage_path VARCHAR(500) NOT NULL,
            checksum_sha256 VARCHAR(64),
            captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, captured_at)
        ) PARTITION BY RANGE (captured_at);
        CREATE INDEX idx_evidence_detection_id ON evidence(detection_id);
        CREATE INDEX idx_evidence_incident_id ON evidence(incident_id);

        CREATE TABLE audit_logs (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id UUID REFERENCES users(id),
            action VARCHAR(100) NOT NULL,
            resource_type VARCHAR(50),
            resource_id UUID,
            ip_address VARCHAR(45),
            detail JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);
        CREATE INDEX idx_audit_logs_tenant_time ON audit_logs(tenant_id, created_at DESC);
        CREATE INDEX idx_audit_logs_resource ON audit_logs(resource_type, resource_id);
        """
    )

    # --- LPR module ---
    op.execute(
        """
        CREATE TABLE lpr_events (
            detection_id UUID NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            plate_number VARCHAR(20) NOT NULL,
            plate_confidence NUMERIC(5,4),
            direction VARCHAR(10),
            vehicle_type VARCHAR(30),
            vehicle_color VARCHAR(30),
            watchlist_match VARCHAR(10),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (detection_id, detected_at),
            FOREIGN KEY (detection_id, detected_at) REFERENCES detections(id, detected_at) ON DELETE CASCADE
        ) PARTITION BY RANGE (detected_at);
        CREATE INDEX idx_lpr_events_plate_number ON lpr_events(tenant_id, plate_number);
        CREATE INDEX idx_lpr_events_camera_id_time ON lpr_events(camera_id, created_at DESC);

        CREATE TABLE watchlist_entries (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            plate_number VARCHAR(20) NOT NULL,
            list_type VARCHAR(10) NOT NULL CHECK (list_type IN ('allow','block')),
            reason TEXT,
            added_by_user_id UUID REFERENCES users(id),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, plate_number, list_type)
        );
        CREATE INDEX idx_watchlist_plate_lookup ON watchlist_entries(tenant_id, plate_number) WHERE is_active = TRUE;
        """
    )

    # --- Face Recognition module ---
    op.execute(
        """
        CREATE TABLE face_watchlist_entries (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            person_name TEXT NOT NULL,
            embedding FLOAT4[] NOT NULL,
            list_type VARCHAR(10) NOT NULL CHECK (list_type IN ('allow','block')),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_face_watchlist_tenant_active ON face_watchlist_entries(tenant_id) WHERE is_active = TRUE;

        CREATE TABLE face_events (
            detection_id UUID NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            embedding FLOAT4[] NOT NULL,
            matched_watchlist_id UUID REFERENCES face_watchlist_entries(id),
            match_confidence NUMERIC(5,4),
            watchlist_match VARCHAR(10) CHECK (watchlist_match IN ('allow','block')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (detection_id, detected_at),
            FOREIGN KEY (detection_id, detected_at) REFERENCES detections(id, detected_at) ON DELETE CASCADE
        ) PARTITION BY RANGE (detected_at);
        CREATE INDEX idx_face_events_tenant_camera ON face_events(tenant_id, camera_id);
        """
    )

    # --- Intrusion Detection module ---
    op.execute(
        """
        CREATE TABLE restricted_zones (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            polygon JSONB NOT NULL,
            severity VARCHAR(10) NOT NULL DEFAULT 'medium' CHECK (severity IN ('low','medium','high','critical')),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_restricted_zones_camera_active ON restricted_zones(camera_id) WHERE is_active = TRUE;

        CREATE TABLE intrusion_events (
            detection_id UUID NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            zone_id UUID NOT NULL REFERENCES restricted_zones(id),
            person_bbox JSONB NOT NULL,
            dwell_time_seconds NUMERIC(8,2),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (detection_id, detected_at),
            FOREIGN KEY (detection_id, detected_at) REFERENCES detections(id, detected_at) ON DELETE CASCADE
        ) PARTITION BY RANGE (detected_at);
        CREATE INDEX idx_intrusion_events_tenant_zone ON intrusion_events(tenant_id, zone_id);
        """
    )

    # --- Admin-editable runtime configuration ---
    op.execute(
        """
        CREATE TABLE tenant_settings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            setting_key TEXT NOT NULL,
            setting_value JSONB NOT NULL,
            updated_by_user_id UUID NOT NULL REFERENCES users(id),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, setting_key)
        );
        """
    )

    # --- Row-Level Security: same policy triplet on every tenant-scoped table ---
    for table in RLS_TABLES:
        op.execute(_rls(table))

    # --- pg_partman registration (functions live in `public`, verified against
    #     the real installed v5.4.3 rather than assumed — see module docstring) ---
    op.execute(
        """
        SELECT public.create_parent(p_parent_table => 'public.detections', p_control => 'detected_at', p_interval => '1 month', p_premake => 3);
        SELECT public.create_parent(p_parent_table => 'public.lpr_events', p_control => 'detected_at', p_interval => '1 month', p_premake => 3);
        SELECT public.create_parent(p_parent_table => 'public.face_events', p_control => 'detected_at', p_interval => '1 month', p_premake => 3);
        SELECT public.create_parent(p_parent_table => 'public.intrusion_events', p_control => 'detected_at', p_interval => '1 month', p_premake => 3);
        SELECT public.create_parent(p_parent_table => 'public.evidence', p_control => 'captured_at', p_interval => '1 month', p_premake => 3);
        SELECT public.create_parent(p_parent_table => 'public.audit_logs', p_control => 'created_at', p_interval => '1 month', p_premake => 3);
        """
    )

    # --- Seed data: roles, permissions, role_permissions ---
    op.execute(
        """
        INSERT INTO roles (id, code, name) VALUES
            (1, 'super_admin', 'Super Admin'),
            (2, 'admin', 'Admin'),
            (3, 'supervisor', 'Supervisor'),
            (4, 'operator', 'Operator'),
            (5, 'security_guard', 'Security Guard'),
            (6, 'viewer', 'Viewer');

        INSERT INTO permissions (code, category, description) VALUES
            ('camera:create', 'camera', 'Create cameras'),
            ('camera:read', 'camera', 'Read cameras'),
            ('camera:update', 'camera', 'Update cameras'),
            ('camera:delete', 'camera', 'Delete cameras'),
            ('user:create', 'user', 'Create users'),
            ('user:read', 'user', 'Read users'),
            ('user:update', 'user', 'Update users'),
            ('user:delete', 'user', 'Delete users'),
            ('role:manage', 'rbac', 'Manage roles and permission assignments'),
            ('settings:read', 'settings', 'Read tenant settings'),
            ('settings:write', 'settings', 'Write tenant settings'),
            ('watchlist:manage', 'watchlist', 'Manage plate and face watchlists'),
            ('zone:manage', 'intrusion', 'Manage restricted zones'),
            ('alert:read', 'alert', 'Read alerts'),
            ('alert:acknowledge', 'alert', 'Acknowledge alerts'),
            ('incident:read', 'incident', 'Read incidents'),
            ('incident:create', 'incident', 'Create incidents'),
            ('incident:update', 'incident', 'Update incidents'),
            ('incident:resolve', 'incident', 'Resolve incidents'),
            ('incident:assign', 'incident', 'Assign incidents'),
            ('evidence:read', 'evidence', 'Read evidence'),
            ('audit:read', 'audit', 'Read audit logs'),
            ('detection:read', 'detection', 'Read the generic detections feed');

        -- super_admin: everything
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 1, id FROM permissions;

        -- admin: everything except role:manage
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 2, id FROM permissions WHERE code != 'role:manage';

        -- supervisor: read users (not create/delete), full operational control
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 3, id FROM permissions WHERE code IN (
            'camera:read', 'camera:update', 'user:read', 'settings:read',
            'watchlist:manage', 'zone:manage', 'alert:read', 'alert:acknowledge',
            'incident:read', 'incident:create', 'incident:update', 'incident:resolve',
            'incident:assign', 'evidence:read', 'audit:read', 'detection:read'
        );

        -- operator: day-to-day monitoring, no watchlist/zone/settings management
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 4, id FROM permissions WHERE code IN (
            'camera:read', 'alert:read', 'alert:acknowledge', 'incident:read',
            'incident:create', 'incident:update', 'evidence:read', 'detection:read'
        );

        -- security_guard: field response, can raise incidents but not edit them
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 5, id FROM permissions WHERE code IN (
            'camera:read', 'alert:read', 'alert:acknowledge', 'incident:read',
            'incident:create', 'evidence:read', 'detection:read'
        );

        -- viewer: read-only across the board
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 6, id FROM permissions WHERE code IN (
            'camera:read', 'alert:read', 'incident:read', 'evidence:read',
            'detection:read', 'audit:read'
        );
        """
    )


def downgrade() -> None:
    # pg_partman's own bookkeeping (part_config/part_config_sub rows, and the
    # per-table template_public_* tables it creates) reference our tables by name,
    # not by FK — DROP TABLE CASCADE doesn't clean these up, and a stale
    # part_config row collides with create_parent() on the next upgrade. Must
    # unregister before dropping (verified by actually round-tripping
    # upgrade->downgrade->upgrade against the real test database).
    op.execute(
        """
        DELETE FROM part_config_sub WHERE sub_parent IN (
            'public.detections', 'public.lpr_events', 'public.face_events',
            'public.intrusion_events', 'public.evidence', 'public.audit_logs'
        );
        DELETE FROM part_config WHERE parent_table IN (
            'public.detections', 'public.lpr_events', 'public.face_events',
            'public.intrusion_events', 'public.evidence', 'public.audit_logs'
        );
        DROP TABLE IF EXISTS
            template_public_detections, template_public_lpr_events,
            template_public_face_events, template_public_intrusion_events,
            template_public_evidence, template_public_audit_logs
        CASCADE;
        """
    )

    # Partition children must drop before their parent; CASCADE handles the rest.
    op.execute(
        """
        DROP TABLE IF EXISTS tenant_settings CASCADE;
        DROP TABLE IF EXISTS intrusion_events CASCADE;
        DROP TABLE IF EXISTS restricted_zones CASCADE;
        DROP TABLE IF EXISTS face_events CASCADE;
        DROP TABLE IF EXISTS face_watchlist_entries CASCADE;
        DROP TABLE IF EXISTS watchlist_entries CASCADE;
        DROP TABLE IF EXISTS lpr_events CASCADE;
        DROP TABLE IF EXISTS audit_logs CASCADE;
        DROP TABLE IF EXISTS evidence CASCADE;
        DROP TABLE IF EXISTS incident_notes CASCADE;
        DROP TABLE IF EXISTS incidents CASCADE;
        DROP TABLE IF EXISTS alerts CASCADE;
        DROP TABLE IF EXISTS detections CASCADE;
        DROP TABLE IF EXISTS camera_health_events CASCADE;
        DROP TABLE IF EXISTS streams CASCADE;
        DROP TABLE IF EXISTS cameras CASCADE;
        DROP TABLE IF EXISTS refresh_tokens CASCADE;
        DROP TABLE IF EXISTS users CASCADE;
        DROP TABLE IF EXISTS tenants CASCADE;
        DROP TABLE IF EXISTS role_permissions CASCADE;
        DROP TABLE IF EXISTS permissions CASCADE;
        DROP TABLE IF EXISTS roles CASCADE;
        """
    )
