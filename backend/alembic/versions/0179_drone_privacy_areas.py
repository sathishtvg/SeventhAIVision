"""A drone's picture blanked over privacy areas, and a record of what was withheld.

Phase 5 of closing the feature reference's known limits (docs/plans/2026-11-04-drone-privacy-areas-design.md).

`drone_privacy_areas` - places on the map a drone's camera is not to show: a polygon, a rectangle or a circle, in the
geometry the drone zones use, with a margin around it. Drawn and taken away by privacy:manage, never edited; one
taken away is kept with who and when.

`drone_media_withheld` - a picture from the site's gateway that was taken at an area: a snapshot that is stored
blank, or a clip that is not kept at all. Which, when, why and over which areas.
"""
from alembic import op

revision = "0179"
down_revision = "0178"
branch_labels = None
depends_on = None


def _secured(table: str, grants: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute(f"REVOKE ALL ON {table} FROM svc_app")
    op.execute(f"GRANT {grants} ON {table} TO svc_app")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE drone_privacy_areas (
            id                 UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID          NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id            UUID          NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            name               VARCHAR(120)  NOT NULL CHECK (btrim(name) <> ''),
            shape              VARCHAR(10)   NOT NULL CHECK (shape IN ('POLYGON', 'RECTANGLE', 'CIRCLE')),
            polygon            JSONB,
            center_latitude    DOUBLE PRECISION,
            center_longitude   DOUBLE PRECISION,
            radius_m           NUMERIC(9,2),
            margin_m           NUMERIC(6,1)  NOT NULL DEFAULT 25 CHECK (margin_m BETWEEN 0 AND 200),
            created_by_user_id UUID          REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
            removed_at         TIMESTAMPTZ,
            removed_by_user_id UUID          REFERENCES users(id) ON DELETE SET NULL,
            CONSTRAINT ck_dprivacy_shape CHECK (
                (shape = 'CIRCLE' AND polygon IS NULL AND center_latitude IS NOT NULL AND center_longitude IS NOT NULL
                 AND radius_m > 0)
             OR (shape <> 'CIRCLE' AND polygon IS NOT NULL AND center_latitude IS NULL AND radius_m IS NULL))
        )
    """)
    op.execute("CREATE INDEX idx_dprivacy_active ON drone_privacy_areas (tenant_id) WHERE removed_at IS NULL")
    _secured("drone_privacy_areas", "SELECT, INSERT, UPDATE")

    op.execute("""
        CREATE TABLE drone_media_withheld (
            id               UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID          NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            client_ref       UUID          NOT NULL,
            media_id         UUID          REFERENCES drone_event_media(id) ON DELETE SET NULL,
            event_id         UUID          REFERENCES drone_events(id) ON DELETE CASCADE,
            session_id       UUID,
            drone_id         UUID          REFERENCES drones(id) ON DELETE SET NULL,
            edge_gateway_id  UUID,
            media_kind       VARCHAR(12)   NOT NULL,
            -- BLANKED: a snapshot stored as a blank picture. NOT_KEPT: a clip the platform did not take.
            action           VARCHAR(10)   NOT NULL CHECK (action IN ('BLANKED', 'NOT_KEPT')),
            captured_at      TIMESTAMPTZ,
            duration_seconds NUMERIC(8,2),
            reason           TEXT          NOT NULL CHECK (btrim(reason) <> ''),
            area_names       TEXT[]        NOT NULL DEFAULT '{}',
            created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
            CONSTRAINT uq_dwithheld_ref UNIQUE (tenant_id, client_ref)
        )
    """)
    op.execute("CREATE INDEX idx_dwithheld_event ON drone_media_withheld (event_id) WHERE event_id IS NOT NULL")
    _secured("drone_media_withheld", "SELECT, INSERT, UPDATE")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS drone_media_withheld")
    op.execute("DROP TABLE IF EXISTS drone_privacy_areas")
