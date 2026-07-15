"""Guard Operations:
- shifts: guard duty scheduling (who works which site, what hours)
- patrol_routes: named patrol routes per site
- patrol_checkpoints: ordered waypoints per route
- patrol_sessions: a guard's actual run through a route
- checkpoint_scans: individual checkpoint tap/scan events
- occurrence_book_entries: Singapore DOB digital log (append-only)

New permissions: shift:manage, shift:read, patrol:manage, patrol:read,
                 patrol:scan, dob:write, dob:read, guard:sos

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-22
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
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
    # ── shifts ────────────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE shifts (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id         UUID REFERENCES sites(id) ON DELETE SET NULL,
            guard_user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            scheduled_start TIMESTAMPTZ NOT NULL,
            scheduled_end   TIMESTAMPTZ NOT NULL,
            actual_start    TIMESTAMPTZ,
            actual_end      TIMESTAMPTZ,
            status          VARCHAR(20) NOT NULL DEFAULT 'scheduled',
            -- scheduled | active | completed | missed | partial
            check_in_lat    DOUBLE PRECISION,
            check_in_lon    DOUBLE PRECISION,
            check_out_lat   DOUBLE PRECISION,
            check_out_lon   DOUBLE PRECISION,
            handover_notes  TEXT,
            created_by_user_id UUID REFERENCES users(id),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_shifts_tenant_guard ON shifts(tenant_id, guard_user_id, scheduled_start DESC);
        CREATE INDEX idx_shifts_tenant_site  ON shifts(tenant_id, site_id, scheduled_start DESC);
        """
    )
    op.execute(_rls("shifts"))

    # ── patrol_routes ─────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE patrol_routes (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id     UUID REFERENCES sites(id) ON DELETE SET NULL,
            name        VARCHAR(255) NOT NULL,
            description TEXT,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_patrol_routes_tenant ON patrol_routes(tenant_id, site_id);
        """
    )
    op.execute(_rls("patrol_routes"))

    # ── patrol_checkpoints ────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE patrol_checkpoints (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            route_id    UUID NOT NULL REFERENCES patrol_routes(id) ON DELETE CASCADE,
            sequence    SMALLINT NOT NULL,
            name        VARCHAR(255) NOT NULL,
            description TEXT,
            latitude    DOUBLE PRECISION,
            longitude   DOUBLE PRECISION,
            nfc_tag_id  VARCHAR(255),
            qr_code     VARCHAR(255),
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_patrol_checkpoints_route ON patrol_checkpoints(route_id, sequence);
        """
    )
    op.execute(_rls("patrol_checkpoints"))

    # ── patrol_sessions ───────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE patrol_sessions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            route_id        UUID NOT NULL REFERENCES patrol_routes(id) ON DELETE CASCADE,
            guard_user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            shift_id        UUID REFERENCES shifts(id) ON DELETE SET NULL,
            status          VARCHAR(20) NOT NULL DEFAULT 'in_progress',
            -- in_progress | completed | missed | incomplete
            started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at    TIMESTAMPTZ,
            total_checkpoints SMALLINT NOT NULL DEFAULT 0,
            scanned_checkpoints SMALLINT NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_patrol_sessions_tenant ON patrol_sessions(tenant_id, guard_user_id, started_at DESC);
        """
    )
    op.execute(_rls("patrol_sessions"))

    # ── checkpoint_scans ──────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE checkpoint_scans (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            session_id      UUID NOT NULL REFERENCES patrol_sessions(id) ON DELETE CASCADE,
            checkpoint_id   UUID NOT NULL REFERENCES patrol_checkpoints(id) ON DELETE CASCADE,
            scanned_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            scan_method     VARCHAR(20) NOT NULL DEFAULT 'manual',
            -- manual | nfc | qr
            latitude        DOUBLE PRECISION,
            longitude       DOUBLE PRECISION,
            notes           TEXT
        );
        CREATE INDEX idx_checkpoint_scans_session ON checkpoint_scans(session_id, scanned_at);
        """
    )
    op.execute(_rls("checkpoint_scans"))

    # ── occurrence_book_entries (DOB — append-only, immutable after insert) ───
    op.execute(
        """
        CREATE TABLE occurrence_book_entries (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id         UUID REFERENCES sites(id) ON DELETE SET NULL,
            shift_id        UUID REFERENCES shifts(id) ON DELETE SET NULL,
            author_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            entry_type      VARCHAR(30) NOT NULL DEFAULT 'general',
            -- general | visitor | incident | patrol | handover | maintenance
            body            TEXT NOT NULL,
            severity        VARCHAR(10),
            related_alert_id    UUID,
            related_incident_id UUID,
            latitude        DOUBLE PRECISION,
            longitude       DOUBLE PRECISION,
            occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            -- NOTE: no updated_at — entries are immutable by design (DOB compliance)
        );
        CREATE INDEX idx_dob_tenant_site_time ON occurrence_book_entries(tenant_id, site_id, occurred_at DESC);
        CREATE INDEX idx_dob_shift ON occurrence_book_entries(shift_id, occurred_at DESC);
        """
    )
    op.execute(_rls("occurrence_book_entries"))

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute(
        """
        INSERT INTO permissions (code, description, category) VALUES
            ('shift:manage', 'Create / edit guard shift schedules', 'guard'),
            ('shift:read',   'View guard shift schedules',           'guard'),
            ('patrol:manage','Create / edit patrol routes',          'guard'),
            ('patrol:read',  'View patrol routes and sessions',       'guard'),
            ('patrol:scan',  'Scan patrol checkpoints',               'guard'),
            ('dob:write',    'Add occurrence book entries',           'guard'),
            ('dob:read',     'Read occurrence book',                  'guard'),
            ('guard:sos',    'Send SOS panic alert',                  'guard')
        ON CONFLICT (code) DO NOTHING;

        -- super_admin (1), admin (2), supervisor (3) manage everything
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3) AND p.code IN (
            'shift:manage','shift:read','patrol:manage','patrol:read',
            'patrol:scan','dob:write','dob:read','guard:sos'
        ) ON CONFLICT DO NOTHING;

        -- operator (4), security_guard (5) can scan, read, write DOB, SOS
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (4, 5) AND p.code IN (
            'shift:read','patrol:read','patrol:scan','dob:write','dob:read','guard:sos'
        ) ON CONFLICT DO NOTHING;

        -- viewer (6) read-only
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 6 AND p.code IN ('shift:read','patrol:read','dob:read')
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS checkpoint_scans;
        DROP TABLE IF EXISTS patrol_sessions;
        DROP TABLE IF EXISTS patrol_checkpoints;
        DROP TABLE IF EXISTS patrol_routes;
        DROP TABLE IF EXISTS occurrence_book_entries;
        DROP TABLE IF EXISTS shifts;
        """
    )
