"""Phase 10: sites hierarchy, tenant module licensing, stream credential storage,
and video recordings table.

- sites: intermediate grouping layer between tenant and camera
- cameras.site_id FK (nullable, backward-compatible)
- streams.auth_config JSONB (username/password for RTSP auth)
- tenant_module_licenses: super-admin controls which AI modules each tenant purchased
- recordings: tracks started/stopped video recording sessions per stream

New permissions: site:manage, recording:create, recording:read, license:manage

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-21
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rls(table: str) -> str:
    return f"""
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation_{table} ON {table}
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """


def upgrade() -> None:
    # ── sites table ──────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE sites (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name        VARCHAR(255) NOT NULL,
            address     TEXT,
            description TEXT,
            latitude    DOUBLE PRECISION,
            longitude   DOUBLE PRECISION,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_sites_tenant_id ON sites(tenant_id);
        """
    )
    op.execute(_rls("sites"))

    # ── cameras.site_id FK (nullable — existing cameras keep working) ────────
    op.execute(
        """
        ALTER TABLE cameras
            ADD COLUMN site_id UUID REFERENCES sites(id) ON DELETE SET NULL;
        CREATE INDEX idx_cameras_site_id ON cameras(site_id);
        """
    )

    # ── streams.auth_config (credential storage for RTSP auth) ──────────────
    op.execute(
        "ALTER TABLE streams ADD COLUMN auth_config JSONB NOT NULL DEFAULT '{}'"
    )

    # ── tenant_module_licenses ───────────────────────────────────────────────
    # No RLS USING clause — super admin writes cross-tenant via get_raw_db().
    # We still enable RLS so tenant-scoped sessions can't read other tenants'
    # licenses, but the super admin session bypasses via set_config GUC.
    op.execute(
        """
        CREATE TABLE tenant_module_licenses (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            module_type         VARCHAR(30) NOT NULL,
            is_enabled          BOOLEAN NOT NULL DEFAULT TRUE,
            max_cameras         INTEGER,
            licensed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at          TIMESTAMPTZ,
            licensed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            notes               TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, module_type)
        );
        CREATE INDEX idx_tenant_module_licenses_tenant ON tenant_module_licenses(tenant_id);
        """
    )
    op.execute(_rls("tenant_module_licenses"))

    # ── recordings ───────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE recordings (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id        UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            stream_id        UUID NOT NULL REFERENCES streams(id) ON DELETE CASCADE,
            site_id          UUID REFERENCES sites(id) ON DELETE SET NULL,
            status           VARCHAR(20) NOT NULL DEFAULT 'recording',
            started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at         TIMESTAMPTZ,
            file_path        VARCHAR(500),
            file_size_bytes  BIGINT,
            duration_seconds INTEGER,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_recordings_tenant_camera
            ON recordings(tenant_id, camera_id, started_at DESC);
        """
    )
    op.execute(_rls("recordings"))

    # ── new permissions ───────────────────────────────────────────────────────
    op.execute(
        """
        INSERT INTO permissions (code, category, description) VALUES
            ('site:manage',       'site',      'Create / edit / deactivate sites'),
            ('recording:create',  'recording', 'Start and stop stream recordings'),
            ('recording:read',    'recording', 'View and download recordings'),
            ('license:manage',    'license',   'Manage per-tenant AI module licenses');

        -- super_admin: all new permissions
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 1, id FROM permissions
        WHERE code IN ('site:manage','recording:create','recording:read','license:manage');

        -- admin: all except license:manage
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 2, id FROM permissions
        WHERE code IN ('site:manage','recording:create','recording:read');

        -- supervisor: site:manage + recording:create/read
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 3, id FROM permissions
        WHERE code IN ('site:manage','recording:create','recording:read');

        -- operator: recording:create/read (no site management)
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 4, id FROM permissions
        WHERE code IN ('recording:create','recording:read');

        -- security_guard + viewer: recording:read only
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 5, id FROM permissions WHERE code = 'recording:read';

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 6, id FROM permissions WHERE code = 'recording:read';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions
            WHERE code IN ('site:manage','recording:create','recording:read','license:manage')
        );
        DELETE FROM permissions
        WHERE code IN ('site:manage','recording:create','recording:read','license:manage');
        """
    )
    op.execute("DROP TABLE IF EXISTS recordings")
    op.execute("DROP TABLE IF EXISTS tenant_module_licenses")
    op.execute("ALTER TABLE streams DROP COLUMN IF EXISTS auth_config")
    op.execute("ALTER TABLE cameras DROP COLUMN IF EXISTS site_id")
    op.execute("DROP TABLE IF EXISTS sites")
