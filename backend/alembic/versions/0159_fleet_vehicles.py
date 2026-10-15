"""Fleet vehicles: a full record, archive in place of delete, custom fields, groups.

Phase 1 of the GPS fleet upgrade (docs/plans/2026-10-14-gps-fleet-tracking-design.md,
section 3). The fleet (`vehicles`, 0024) is extended; the plate register used by
the cameras and barriers (`watchlist_entries`) is a different list and is not
touched.

A VEHICLE IS ARCHIVED, NOT DELETED. `archived_at`, who archived it and why are
kept, and `is_active` - which every existing query reads - is held equal to "not
archived" by a check, so nothing that reads it changes meaning. Rows already
deactivated before this have no recorded reason or moment: they are given the
moment they were last changed, which is when they were deactivated, and say
that no reason was recorded. Nothing is made up about them.

NOTHING THE APPLICATION DOES REMOVES A VEHICLE OR ITS HISTORY. The application's
role loses DELETE on the vehicle and on the three kinds of record that hang off
it; only the retention job, on its own session, will remove anything, after a
period an organisation sets. The links from journeys and geofence events to a
vehicle no longer cascade: a vehicle removed by hand while it still has history
is refused, where before its journeys and events went with it without a word
(approved by the owner, 14 October 2026). They become NO ACTION, not RESTRICT,
so that removing a whole organisation - which removes the vehicle and its
history in the same statement - still works.

ONE PLATE, ONE VEHICLE, PER ORGANISATION. The plate is compared without spaces,
dashes or case (`plate_key`, computed by the database so no writer can get it
wrong). Archived vehicles do not hold their plate. If two vehicles that are not
archived already share a plate, this migration stops and names them: which one
is right is not something to guess.

CUSTOM FIELDS are defined by the organisation, like a site's visitor form
fields (0077), and their values are kept on the vehicle as JSON. A field is
retired, never deleted, so the values already given under it are kept.

GROUPS of vehicles are what a geofence will be able to apply to (phase 5). A
group may belong to a site.

Revision ID: 0159
Revises: 0158
"""
from alembic import op

revision = "0159"
down_revision = "0158"
branch_labels = None
depends_on = None

SERVICE_STATUSES = ("in_service", "maintenance", "out_of_service")
FIELD_TYPES = ("text", "number", "date", "select", "boolean")
NEW_TABLES = ("fleet_vehicle_fields", "fleet_vehicle_groups", "fleet_vehicle_group_members")
NO_DELETE = ("vehicles", "vehicle_journeys", "geofence_events", "vehicle_positions")

PERMISSIONS = [
    ("fleet:vehicle:archive", "Archive a fleet vehicle, and restore one", "gps"),
    ("fleet:fields:manage", "Define the custom fields of fleet vehicles", "gps"),
]
# Admin and Manager: the people who answer for the organisation's records.
ROLES = [(2, "fleet:vehicle:archive"), (8, "fleet:vehicle:archive"),
         (2, "fleet:fields:manage"), (8, "fleet:fields:manage")]


def upgrade() -> None:
    statuses = ", ".join(f"'{s}'" for s in SERVICE_STATUSES)
    op.execute(f"""
        ALTER TABLE vehicles
          ADD COLUMN department          VARCHAR(120),
          ADD COLUMN driver_name         VARCHAR(255),
          ADD COLUMN driver_phone        VARCHAR(40),
          ADD COLUMN service_status      VARCHAR(20) NOT NULL DEFAULT 'in_service'
                                             CHECK (service_status IN ({statuses})),
          ADD COLUMN notes               TEXT,
          ADD COLUMN custom_fields       JSONB NOT NULL DEFAULT '{{}}',
          ADD COLUMN archived_at         TIMESTAMPTZ,
          ADD COLUMN archived_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
          ADD COLUMN archive_reason      TEXT,
          ADD COLUMN plate_key           VARCHAR(30) GENERATED ALWAYS AS (
              NULLIF(upper(regexp_replace(coalesce(plate_number, ''), '[^A-Za-z0-9]', '', 'g')), '')
          ) STORED
    """)
    op.execute("""
        UPDATE vehicles
           SET archived_at = updated_at,
               archive_reason = 'Deactivated before vehicles could be archived; no reason was recorded.'
         WHERE is_active = FALSE
    """)
    op.execute("""
        ALTER TABLE vehicles ADD CONSTRAINT vehicles_archive_matches_active
            CHECK ((archived_at IS NULL) = is_active)
    """)
    op.execute("""
        ALTER TABLE vehicles ADD CONSTRAINT vehicles_custom_fields_object
            CHECK (jsonb_typeof(custom_fields) = 'object')
    """)

    # Which plates two vehicles share, named, before the rule that forbids it.
    op.execute("""
        DO $$
        DECLARE shared text;
        BEGIN
            SELECT string_agg(format('organisation %s: plate %s on %s vehicles', tenant_id, plate_key, n), '; ')
              INTO shared
              FROM (SELECT tenant_id, plate_key, count(*) AS n FROM vehicles
                     WHERE archived_at IS NULL AND plate_key IS NOT NULL
                     GROUP BY tenant_id, plate_key HAVING count(*) > 1) d;
            IF shared IS NOT NULL THEN
                RAISE EXCEPTION 'Vehicles that are not archived share a plate (%). Archive or correct one of each before upgrading.', shared;
            END IF;
        END
        $$
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_vehicles_tenant_plate
            ON vehicles(tenant_id, plate_key) WHERE archived_at IS NULL AND plate_key IS NOT NULL
    """)
    op.execute("CREATE INDEX idx_vehicles_tenant_archived ON vehicles(tenant_id, archived_at)")

    # History is refused, not erased, when somebody removes a vehicle by hand.
    for table in ("vehicle_journeys", "geofence_events"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_vehicle_id_fkey")
        op.execute(f"""
            ALTER TABLE {table} ADD CONSTRAINT {table}_vehicle_id_fkey
                FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE NO ACTION
        """)

    types = ", ".join(f"'{t}'" for t in FIELD_TYPES)
    op.execute(f"""
        CREATE TABLE fleet_vehicle_fields (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            field_key   VARCHAR(50) NOT NULL CHECK (field_key ~ '^[a-z][a-z0-9_]{{0,49}}$'),
            label       VARCHAR(120) NOT NULL,
            field_type  VARCHAR(20) NOT NULL CHECK (field_type IN ({types})),
            options     JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(options) = 'array'),
            is_required BOOLEAN NOT NULL DEFAULT FALSE,
            help_text   VARCHAR(255),
            sort_order  INTEGER NOT NULL DEFAULT 0,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, field_key)
        )
    """)
    op.execute("""
        CREATE TABLE fleet_vehicle_groups (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id            UUID REFERENCES sites(id) ON DELETE SET NULL,
            name               VARCHAR(120) NOT NULL,
            description        VARCHAR(500),
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_fleet_vehicle_groups_name
            ON fleet_vehicle_groups(tenant_id, lower(name)) WHERE is_active
    """)
    op.execute("""
        CREATE TABLE fleet_vehicle_group_members (
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            group_id         UUID NOT NULL REFERENCES fleet_vehicle_groups(id) ON DELETE CASCADE,
            vehicle_id       UUID NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            added_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            added_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (group_id, vehicle_id)
        )
    """)
    op.execute("CREATE INDEX idx_fleet_group_members_vehicle ON fleet_vehicle_group_members(vehicle_id)")

    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)
    # A field and a group are retired, never removed; a membership is configuration and may be.
    for table in ("fleet_vehicle_fields", "fleet_vehicle_groups"):
        op.execute(f"REVOKE DELETE ON {table} FROM svc_app")
    for table in NO_DELETE:
        op.execute(f"REVOKE DELETE ON {table} FROM svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    values = ", ".join(f"({role}, '{code}')" for role, code in ROLES)
    op.execute(f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES {values}) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c, _, _ in PERMISSIONS)
    op.execute(f"DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ({codes}))")
    op.execute(f"DELETE FROM permissions WHERE code IN ({codes})")
    for table in NO_DELETE:
        op.execute(f"GRANT DELETE ON {table} TO svc_app")
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    for table in ("vehicle_journeys", "geofence_events"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_vehicle_id_fkey")
        op.execute(f"""
            ALTER TABLE {table} ADD CONSTRAINT {table}_vehicle_id_fkey
                FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE CASCADE
        """)
    op.execute("DROP INDEX IF EXISTS idx_vehicles_tenant_archived")
    op.execute("DROP INDEX IF EXISTS uq_vehicles_tenant_plate")
    op.execute("ALTER TABLE vehicles DROP CONSTRAINT IF EXISTS vehicles_custom_fields_object")
    op.execute("ALTER TABLE vehicles DROP CONSTRAINT IF EXISTS vehicles_archive_matches_active")
    op.execute("""
        ALTER TABLE vehicles
          DROP COLUMN plate_key, DROP COLUMN archive_reason, DROP COLUMN archived_by_user_id,
          DROP COLUMN archived_at, DROP COLUMN custom_fields, DROP COLUMN notes,
          DROP COLUMN service_status, DROP COLUMN driver_phone, DROP COLUMN driver_name,
          DROP COLUMN department
    """)
