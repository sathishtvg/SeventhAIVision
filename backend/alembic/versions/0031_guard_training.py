"""Guard Training & Certification Tracking:
- training_courses: catalog of available training modules
- training_records: per-guard completion records (score, pass/fail, expiry)
- guard_certifications: professional licenses and external certifications

New permissions: training:read (all), training:write (1-3), training:manage (1-2)

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
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
    # ── training_courses ──────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE training_courses (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name            VARCHAR(255) NOT NULL,
            description     TEXT,
            category        VARCHAR(50) NOT NULL DEFAULT 'general',
            -- general | fire_safety | first_aid | security | cctv | legal | physical
            duration_hours  NUMERIC(6,2),
            passing_score   INTEGER NOT NULL DEFAULT 70,
            validity_months INTEGER,
            -- NULL = no expiry; otherwise record expires this many months after completion
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_training_courses_tenant ON training_courses(tenant_id);
        """
    )
    op.execute(_rls("training_courses"))

    # ── training_records ──────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE training_records (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            course_id           UUID NOT NULL REFERENCES training_courses(id) ON DELETE CASCADE,
            completed_at        DATE NOT NULL DEFAULT CURRENT_DATE,
            score               INTEGER,
            passed              BOOLEAN NOT NULL DEFAULT TRUE,
            expires_at          DATE,
            notes               TEXT,
            recorded_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_training_records_tenant_user ON training_records(tenant_id, user_id);
        CREATE INDEX idx_training_records_expires ON training_records(expires_at)
            WHERE expires_at IS NOT NULL;
        """
    )
    op.execute(_rls("training_records"))

    # ── guard_certifications ──────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE guard_certifications (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            certification_type  VARCHAR(100) NOT NULL,
            -- security_officer_license | first_aid | fire_warden | cpr | etc.
            issuing_body        VARCHAR(255),
            certificate_number  VARCHAR(100),
            issued_at           DATE,
            expires_at          DATE,
            is_valid            BOOLEAN NOT NULL DEFAULT TRUE,
            notes               TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_guard_certs_tenant_user ON guard_certifications(tenant_id, user_id);
        CREATE INDEX idx_guard_certs_expires ON guard_certifications(expires_at)
            WHERE expires_at IS NOT NULL AND is_valid = TRUE;
        """
    )
    op.execute(_rls("guard_certifications"))

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute(
        """
        INSERT INTO permissions (code, description, category) VALUES
            ('training:read',   'View training courses, records and certifications', 'training'),
            ('training:write',  'Record training completions and manage certifications', 'training'),
            ('training:manage', 'Create/edit training course catalog',               'training')
        ON CONFLICT (code) DO NOTHING;

        -- All roles: training:read
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3,4,5,6) AND p.code = 'training:read'
        ON CONFLICT DO NOTHING;

        -- Roles 1-3 (supervisor+): training:write
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3) AND p.code = 'training:write'
        ON CONFLICT DO NOTHING;

        -- Roles 1-2 (admin+): training:manage
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2) AND p.code = 'training:manage'
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS guard_certifications;
        DROP TABLE IF EXISTS training_records;
        DROP TABLE IF EXISTS training_courses;
        """
    )
