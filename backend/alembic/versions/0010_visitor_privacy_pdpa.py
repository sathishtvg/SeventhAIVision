"""Visitor management, privacy masking zones, PDPA compliance:
- visitors: pre-registered expected visitors per site
- visitor_logs: actual arrival/departure events
- privacy_zones: per-camera polygon masks applied before AI processing
- pdpa_consents: consent records for face recognition data collection
- data_subject_requests: DSAR / erasure requests

New permissions: visitor:manage, visitor:read, privacy:manage,
                 pdpa:admin, pdpa:read

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-22
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
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
    # ── visitors (pre-registration) ───────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE visitors (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id         UUID REFERENCES sites(id) ON DELETE SET NULL,
            full_name       VARCHAR(255) NOT NULL,
            id_number       VARCHAR(50),
            company         VARCHAR(255),
            host_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
            host_name       VARCHAR(255),
            purpose         TEXT,
            vehicle_plate   VARCHAR(20),
            expected_from   TIMESTAMPTZ,
            expected_until  TIMESTAMPTZ,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_visitors_tenant_site ON visitors(tenant_id, site_id, expected_from);
        CREATE INDEX idx_visitors_plate ON visitors(tenant_id, vehicle_plate) WHERE vehicle_plate IS NOT NULL;
        """
    )
    op.execute(_rls("visitors"))

    # ── visitor_logs (actual arrivals/departures) ─────────────────────────────
    op.execute(
        """
        CREATE TABLE visitor_logs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            visitor_id      UUID REFERENCES visitors(id) ON DELETE SET NULL,
            site_id         UUID REFERENCES sites(id) ON DELETE SET NULL,
            guard_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            event_type      VARCHAR(20) NOT NULL DEFAULT 'arrival',
            -- arrival | departure | denied
            badge_number    VARCHAR(50),
            notes           TEXT,
            is_unregistered BOOLEAN NOT NULL DEFAULT FALSE,
            occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_visitor_logs_tenant_site ON visitor_logs(tenant_id, site_id, occurred_at DESC);
        """
    )
    op.execute(_rls("visitor_logs"))

    # ── privacy_zones (per-camera polygon masks) ──────────────────────────────
    op.execute(
        """
        CREATE TABLE privacy_zones (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id   UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            name        VARCHAR(255) NOT NULL DEFAULT 'Privacy Zone',
            polygon     JSONB NOT NULL,
            -- [{"x": 0.1, "y": 0.2}, ...] normalised 0..1 coords same as restricted_zones
            fill_color  VARCHAR(7) NOT NULL DEFAULT '#000000',
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_privacy_zones_camera ON privacy_zones(camera_id) WHERE is_active = TRUE;
        """
    )
    op.execute(_rls("privacy_zones"))

    # ── pdpa_consents ─────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE pdpa_consents (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id         UUID REFERENCES sites(id) ON DELETE SET NULL,
            data_subject_name VARCHAR(255),
            data_subject_id   VARCHAR(100),
            -- NRIC/passport — hashed before storage in prod
            consent_type    VARCHAR(50) NOT NULL DEFAULT 'face_recognition',
            -- face_recognition | lpr | general_cctv
            consented       BOOLEAN NOT NULL DEFAULT TRUE,
            consent_method  VARCHAR(30) NOT NULL DEFAULT 'physical_form',
            -- physical_form | digital | implied | legitimate_interest
            valid_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
            valid_until     TIMESTAMPTZ,
            notes           TEXT,
            collected_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_pdpa_consents_tenant ON pdpa_consents(tenant_id, data_subject_id);
        """
    )
    op.execute(_rls("pdpa_consents"))

    # ── data_subject_requests (DSAR / right to erasure) ───────────────────────
    op.execute(
        """
        CREATE TABLE data_subject_requests (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            request_type    VARCHAR(30) NOT NULL DEFAULT 'access',
            -- access | erasure | correction | portability | objection
            data_subject_name VARCHAR(255) NOT NULL,
            data_subject_id   VARCHAR(100),
            data_subject_email VARCHAR(255),
            description     TEXT,
            status          VARCHAR(20) NOT NULL DEFAULT 'pending',
            -- pending | in_review | fulfilled | rejected | partial
            deadline_at     TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '30 days',
            -- PDPA requires response within 30 days
            fulfilled_at    TIMESTAMPTZ,
            fulfilled_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            fulfillment_notes TEXT,
            records_erased  INTEGER DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_dsar_tenant_status ON data_subject_requests(tenant_id, status, deadline_at);
        """
    )
    op.execute(_rls("data_subject_requests"))

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute(
        """
        INSERT INTO permissions (code, description, category) VALUES
            ('visitor:manage',  'Create/edit visitor pre-registrations', 'visitor'),
            ('visitor:read',    'View visitor logs',                     'visitor'),
            ('visitor:checkin', 'Check in/out visitors',                 'visitor'),
            ('privacy:manage',  'Configure camera privacy zones',        'privacy'),
            ('pdpa:admin',      'Manage PDPA consents and DSARs',        'compliance'),
            ('pdpa:read',       'View PDPA consent register',            'compliance')
        ON CONFLICT (code) DO NOTHING;

        -- super_admin + admin get everything
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2) AND p.code IN (
            'visitor:manage','visitor:read','visitor:checkin',
            'privacy:manage','pdpa:admin','pdpa:read'
        ) ON CONFLICT DO NOTHING;

        -- supervisor: manage visitors, read PDPA, manage privacy zones
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 3 AND p.code IN (
            'visitor:manage','visitor:read','visitor:checkin','pdpa:read','privacy:manage'
        ) ON CONFLICT DO NOTHING;

        -- operator + security_guard: check in/out visitors, read
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (4, 5) AND p.code IN ('visitor:checkin','visitor:read')
        ON CONFLICT DO NOTHING;

        -- viewer: read-only
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 6 AND p.code IN ('visitor:read','pdpa:read')
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS data_subject_requests;
        DROP TABLE IF EXISTS pdpa_consents;
        DROP TABLE IF EXISTS privacy_zones;
        DROP TABLE IF EXISTS visitor_logs;
        DROP TABLE IF EXISTS visitors;
        """
    )
