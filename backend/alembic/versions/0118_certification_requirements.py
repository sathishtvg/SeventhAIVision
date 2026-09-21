"""What a guard must hold to work a site, so a lapsed licence is visible.

THE DATA WAS ALREADY HERE AND NOTHING LOOKED AT IT. guard_certifications has
recorded licence types and expiry dates since 0031, and the scheduler has warned
about a CONTRACTOR's accreditation expiring since the contractor round -- but a
tenant's own officers had no equivalent. A guard whose PLRD licence lapsed last
month could be rostered onto a client site, and the shift, the attendance record
and the invoice would all be produced without a murmur.

This migration supplies the missing half: a statement of what is required.

TWO LEVELS, BECAUSE THEY COME FROM DIFFERENT PLACES. A tenant baseline
(site_id IS NULL) is the regulatory floor -- every officer this agency deploys
holds a valid licence, full stop. A per-site row is a client's contract term --
this mall also wants a fire warden on shift. Folding them into one list would
mean repeating the regulatory rule on every site and silently omitting it from
the next site somebody creates.

REQUIREMENTS ARE NOT ENFORCED AS A BLOCK. Chosen deliberately: an ops manager
covering a 2am no-show must not be stopped by the roster tool, or they will stop
using the roster tool. Compliance is reported, not prevented -- see
services/certification_compliance.py and the daily sweep in scheduler_main.

Revision ID: 0118
Revises: 0117
"""
from alembic import op

revision = "0118"
down_revision = "0117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE certification_requirements (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- NULL means "everywhere in this tenant": the regulatory baseline.
            -- A site id narrows it to one client's contract terms.
            site_id            UUID REFERENCES sites(id) ON DELETE CASCADE,
            certification_type VARCHAR(100) NOT NULL,
            -- Free text matching guard_certifications.certification_type, e.g.
            -- security_officer_license | first_aid | fire_warden | cpr.
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            -- How long before expiry a shift starts counting as at risk. A
            -- licence renewal takes weeks, so a warning on the day it lapses is
            -- a warning that arrives too late to act on.
            warn_days_before   INTEGER NOT NULL DEFAULT 30,
            notes              TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_certreq_warn_days CHECK (warn_days_before >= 0),
            CONSTRAINT ck_certreq_type_not_blank
                CHECK (length(btrim(certification_type)) > 0)
        );
    """)

    # TWO partial unique indexes, not one constraint. In SQL, NULL <> NULL, so a
    # plain UNIQUE (tenant_id, site_id, certification_type) would happily accept
    # the same tenant-wide requirement a hundred times over -- the rows differ
    # as far as the index is concerned because site_id is NULL in each.
    op.execute("""
        CREATE UNIQUE INDEX uq_certreq_tenant_baseline
            ON certification_requirements (tenant_id, certification_type)
         WHERE site_id IS NULL;
        CREATE UNIQUE INDEX uq_certreq_site
            ON certification_requirements (tenant_id, site_id, certification_type)
         WHERE site_id IS NOT NULL;
        CREATE INDEX idx_certreq_lookup
            ON certification_requirements (tenant_id, site_id)
         WHERE is_active;
    """)

    op.execute("""
        ALTER TABLE certification_requirements ENABLE ROW LEVEL SECURITY;
        ALTER TABLE certification_requirements FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation_certification_requirements
            ON certification_requirements
            USING (tenant_id = (current_setting('app.current_tenant', true))::uuid)
            WITH CHECK (tenant_id = (current_setting('app.current_tenant', true))::uuid);
        GRANT SELECT, INSERT, UPDATE, DELETE ON certification_requirements TO svc_app;
    """)

    # ── Findings ─────────────────────────────────────────────────────────────
    #
    # NOT alerts, deliberately. alerts.camera_id is NOT NULL, which is why the
    # contractor expiry job guards every insert with "WHERE a camera exists" --
    # and means a tenant with no cameras silently receives none of those
    # warnings at all. An agency using this product for rostering and payroll
    # with few or no cameras is exactly the customer who needs a licence warning
    # most, and since these warnings are the entire feature (nothing is
    # blocked), routing them through a camera-shaped table would make the
    # feature useless precisely where it matters.
    op.execute("""
        CREATE TABLE shift_certification_findings (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            shift_id           UUID NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
            guard_user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            site_id            UUID REFERENCES sites(id) ON DELETE SET NULL,
            certification_type VARCHAR(100) NOT NULL,
            status             VARCHAR(20) NOT NULL,
            -- The date the guard actually stands at the post. Stored because
            -- the whole point is that it is not today: a licence valid this
            -- afternoon may have lapsed by a shift three weeks out.
            shift_date         DATE NOT NULL,
            detected_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- Set when a later sweep finds the problem gone (renewed licence,
            -- reassigned shift). Kept rather than deleted: that the agency was
            -- warned on the 1st and it was fixed by the 3rd is the record worth
            -- having in front of an auditor.
            resolved_at        TIMESTAMPTZ,
            CONSTRAINT ck_scf_status CHECK (
                status IN ('MISSING', 'EXPIRED', 'REVOKED', 'EXPIRING')),
            CONSTRAINT uq_scf_shift_type UNIQUE (shift_id, certification_type)
        );
        CREATE INDEX idx_scf_open ON shift_certification_findings
            (tenant_id, shift_date) WHERE resolved_at IS NULL;
        CREATE INDEX idx_scf_guard ON shift_certification_findings
            (tenant_id, guard_user_id) WHERE resolved_at IS NULL;
    """)

    op.execute("""
        ALTER TABLE shift_certification_findings ENABLE ROW LEVEL SECURITY;
        ALTER TABLE shift_certification_findings FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation_shift_certification_findings
            ON shift_certification_findings
            USING (tenant_id = (current_setting('app.current_tenant', true))::uuid)
            WITH CHECK (tenant_id = (current_setting('app.current_tenant', true))::uuid);
        GRANT SELECT, INSERT, UPDATE, DELETE ON shift_certification_findings TO svc_app;
    """)

    # Reuses the training:* permissions rather than minting new ones. A
    # certification requirement is a statement about certifications, the roles
    # that already curate those are the ones that should set them, and a
    # seventh permission nobody grants is a feature nobody can reach.
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
            ('certification:enforce',
             'Define which certifications a site or tenant requires',
             'training')
        ON CONFLICT (code) DO NOTHING;

        -- Roles 2 (Admin) and 8 (Manager) ONLY. NOT role 1.
        --
        -- Super Admin is the VENDOR's operator, not a tenant user, and 0102
        -- deliberately stripped it to nine platform permissions: tenant,
        -- license, audit, support, billing, platform, 2fa. Granting it a
        -- tenant feature permission walks privilege back across the boundary
        -- that migration exists to hold, and test_support_sessions asserts the
        -- set exactly. An earlier draft of this migration listed role 1, and CI
        -- caught it.
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
         WHERE r.id IN (2, 8) AND p.code = 'certification:enforce'
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions
                                  WHERE code = 'certification:enforce');
        DELETE FROM permissions WHERE code = 'certification:enforce';
        DROP TABLE IF EXISTS shift_certification_findings;
        DROP TABLE IF EXISTS certification_requirements;
    """)
