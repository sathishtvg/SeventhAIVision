"""The security map: the places of a site, as somebody who knows it drew them.

Additive: one new table and two permissions. No existing table, policy or
permission is altered (LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 3;
owner decision E4).

The platform already knows where a site is, where its cameras and patrol
checkpoints are, and where a drone may fly. It does not know a site's
buildings, floors, gates, doors, muster points or the areas people on foot
mean when they say "the yard". This is where an administrator says so.

OPTIONAL. A site with no places works exactly as it did; the map then shows
what the platform already knew about it and nothing more.

A PLACE IS A POINT, AN OUTLINE, OR A PART OF ANOTHER PLACE. A gate is a point;
a building or a zone is an outline; a floor is a level of a building and has
no shape of its own. An access point may name the door the platform already
knows (`access_doors`), which is what gives a door event somewhere to appear.

RETIRED, NOT REMOVED. The application's role cannot delete a place: a place
that no longer exists is marked inactive, so that whatever once referred to it
still does.

SUPER ADMIN (1) AND CLIENT (7) GET NO PERMISSION HERE, and nor does a guard
(5): the map shows where colleagues last recorded their position.

Revision ID: 0145
Revises: 0144
"""
from alembic import op

revision = "0145"
down_revision = "0144"
branch_labels = None
depends_on = None

KINDS = ("'BUILDING','FLOOR','GATE','ACCESS_POINT','EMERGENCY_POINT','ASSEMBLY_POINT','ZONE','PARKING',"
         "'OTHER'")

PERMISSIONS = [
    ("sitemap:read", "View the security map: places, and what each layer's own permission allows", "sitemap"),
    ("sitemap:manage", "Draw and change the places of a site on the security map", "sitemap"),
]


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE site_places (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id            UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            kind               VARCHAR(16) NOT NULL,
            name               VARCHAR(120) NOT NULL,
            -- The building a floor is a level of, or a gate belongs to.
            parent_id          UUID REFERENCES site_places(id) ON DELETE SET NULL,
            -- 0 is the ground floor; basements are negative.
            level              SMALLINT,
            latitude           DOUBLE PRECISION,
            longitude          DOUBLE PRECISION,
            -- An outline: [[lat, lng], ...], three points or more.
            polygon            JSONB,
            -- For an access point: the door the platform already knows.
            door_id            UUID REFERENCES access_doors(id) ON DELETE SET NULL,
            description        TEXT,
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_place_kind  CHECK (kind IN ({KINDS})),
            CONSTRAINT ck_place_name  CHECK (btrim(name) <> ''),
            CONSTRAINT ck_place_point CHECK (
                (latitude IS NULL) = (longitude IS NULL)
                AND (latitude IS NULL OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180))),
            -- CASE, not AND: the length of something that is not a list is an
            -- error, and AND does not promise which side it looks at first.
            CONSTRAINT ck_place_outline CHECK (
                polygon IS NULL OR CASE WHEN jsonb_typeof(polygon) = 'array'
                                        THEN jsonb_array_length(polygon) >= 3 ELSE FALSE END),
            -- Somewhere, or a part of somewhere: a floor may have neither a
            -- point nor an outline, and then has to say which building it is in.
            CONSTRAINT ck_place_somewhere CHECK (
                latitude IS NOT NULL OR polygon IS NOT NULL OR (kind = 'FLOOR' AND parent_id IS NOT NULL)),
            CONSTRAINT ck_place_level CHECK (level IS NULL OR level BETWEEN -20 AND 200),
            CONSTRAINT ck_place_not_its_own_parent CHECK (parent_id IS NULL OR parent_id <> id),
            CONSTRAINT ck_place_door CHECK (door_id IS NULL OR kind IN ('ACCESS_POINT', 'GATE'))
        )
    """)
    op.execute("CREATE INDEX idx_site_places_site ON site_places (site_id) WHERE is_active")
    op.execute("CREATE INDEX idx_site_places_parent ON site_places (parent_id) WHERE parent_id IS NOT NULL")
    # A door is at one place.
    op.execute("CREATE UNIQUE INDEX uq_site_places_door ON site_places (door_id) "
               "WHERE door_id IS NOT NULL AND is_active")
    # Two active places of one kind at a site are not called the same thing.
    op.execute("CREATE UNIQUE INDEX uq_site_places_name ON site_places (site_id, kind, lower(name)) WHERE is_active")

    op.execute("ALTER TABLE site_places ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE site_places FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_site_places ON site_places
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    # The REVOKE is what does it: default privileges hand the application
    # DELETE on every new table.
    op.execute("REVOKE ALL ON site_places FROM svc_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON site_places TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description.replace("'", "''")}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    # Admin (2) and Manager (8) draw the map. Supervisor (3), Operator (4) and
    # Viewer (6) read it.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'sitemap:read'), (2, 'sitemap:manage'),
                  (8, 'sitemap:read'), (8, 'sitemap:manage'),
                  (3, 'sitemap:read'), (4, 'sitemap:read'), (6, 'sitemap:read')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code LIKE 'sitemap:%')
    """)
    op.execute("DELETE FROM permissions WHERE code LIKE 'sitemap:%'")
    op.execute("DROP TABLE IF EXISTS site_places CASCADE")
