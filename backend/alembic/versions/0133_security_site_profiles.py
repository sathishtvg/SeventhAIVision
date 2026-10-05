"""AI security intelligence, phase 3: what a site expects.

Additive: two new tables. No existing table is altered.

WHY A SITE NEEDS A PROFILE. To say that something is out of the ordinary the
platform has to know what ordinary is, and it does not. `sites.operating_hours`
is free text and is empty; there is no measure of how sensitive a site is; a
camera has a name and a location but nothing says "this one watches the server
room". These two tables are where an administrator says so.

NOTHING IS ASSUMED WHEN A PROFILE IS MISSING. `business_hours` is NULL until
someone sets it, and NULL means *not defined* — the context engine then says the
hours are not known and does not claim that an event was after hours. The same
for criticality: NULL is "not set", not "medium". A site with no profile row at
all behaves the same as one with every field empty.

`business_hours` is an object keyed by weekday (`mon` … `sun`), each a list of
`["HH:MM", "HH:MM"]` periods in the site's time zone. A day that is missing has
no opening period: the site is closed that day. The shape is checked by the API
(services/intel_context.py); the column only insists it is an object.

A CAMERA'S PROFILE IS ITS OWN ROW, not a map inside the site's, so that deleting
a camera removes what was said about it and a camera cannot be described twice.

Revision ID: 0133
Revises: 0132
"""
from alembic import op

revision = "0133"
down_revision = "0132"
branch_labels = None
depends_on = None

CRITICALITIES = "'low','medium','high','critical'"
TABLES = ("security_site_profiles", "security_camera_profiles")


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE security_site_profiles (
            id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id                 UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id                   UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            timezone                  VARCHAR(64),
            business_hours            JSONB,
            closed_on_public_holidays BOOLEAN NOT NULL DEFAULT TRUE,
            criticality               VARCHAR(10),
            notes                     TEXT,
            updated_by_user_id        UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_secsite_site        UNIQUE (site_id),
            CONSTRAINT ck_secsite_criticality CHECK (criticality IS NULL OR criticality IN ({CRITICALITIES})),
            CONSTRAINT ck_secsite_hours       CHECK (business_hours IS NULL
                                                     OR jsonb_typeof(business_hours) = 'object')
        )
    """)
    op.execute(f"""
        CREATE TABLE security_camera_profiles (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id          UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            area_label         VARCHAR(120),
            criticality        VARCHAR(10),
            is_restricted_area BOOLEAN NOT NULL DEFAULT FALSE,
            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_seccam_camera      UNIQUE (camera_id),
            CONSTRAINT ck_seccam_criticality CHECK (criticality IS NULL OR criticality IN ({CRITICALITIES}))
        )
    """)

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)
        op.execute(f"GRANT ALL ON {table} TO svc_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS security_camera_profiles CASCADE")
    op.execute("DROP TABLE IF EXISTS security_site_profiles CASCADE")
