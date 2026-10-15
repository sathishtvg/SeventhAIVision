"""Fleet geofences: circles as well as polygons, rules with severity and recipients, and each vehicle's side.

Phase 5 of the GPS fleet upgrade (docs/plans/2026-10-14-gps-fleet-tracking-design.md,
section 5). Everything here is added; nothing existing is removed or renamed.

geofences (0024) gains
    shape            'polygon' (as before) or 'circle' with its centre and radius (25 m - 50 km). A circle's
                     `polygon` is filled with its outline, so the older position endpoint, which reads only
                     `polygon`, still sees the same place.
    archived_*       a geofence is archived, never removed: its events stay readable.
    created_by_user_id

fleet_geofence_groups    a geofence may apply to chosen vehicle groups instead of to every vehicle of its site
                         (or of the organisation, when it has no site).
fleet_geofence_rules     entry, exit, inside longer than N minutes, outside longer than N minutes, overspeed
                         inside; one of each kind per geofence, each with its severity and the people and roles
                         told. The flags the older endpoint sets (alert_on_entry, alert_on_exit, speed_limit)
                         are copied into rules once, here.
fleet_geofence_state     which side of each geofence each vehicle is on, and since when: the protection against
                         a fix wobbling on the boundary. Worked out again from positions if ever lost.
geofence_events (0024)   gains the rule, the alert raised, and "between X and Y" when nothing was heard while
                         the vehicle crossed.

    fleet:geofence:manage   Admin, Supervisor, Manager

A geofence never opens a barrier, admits anybody or confirms who is driving: it records where a vehicle's
tracker said it was, and raises an alert for a person to look at.

Revision ID: 0162
Revises: 0161
"""
from alembic import op

revision = "0162"
down_revision = "0161"
branch_labels = None
depends_on = None

NEW_TABLES = ("fleet_geofence_groups", "fleet_geofence_rules", "fleet_geofence_state")
KINDS = ("entry", "exit", "dwell_inside", "dwell_outside", "overspeed")
SEVERITIES = ("low", "medium", "high", "critical")
PERMISSIONS = [("fleet:geofence:manage", "Draw, change and archive fleet geofences and their rules", "gps")]
ROLES = [(2, "fleet:geofence:manage"), (3, "fleet:geofence:manage"), (8, "fleet:geofence:manage")]


def _quoted(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute("""
        ALTER TABLE geofences
            ADD COLUMN shape               VARCHAR(10) NOT NULL DEFAULT 'polygon',
            ADD COLUMN center_lat          DOUBLE PRECISION,
            ADD COLUMN center_lon          DOUBLE PRECISION,
            ADD COLUMN radius_m            DOUBLE PRECISION,
            ADD COLUMN created_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN archived_at         TIMESTAMPTZ,
            ADD COLUMN archived_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN archive_reason      TEXT,
            ADD CONSTRAINT ck_geofences_shape CHECK (shape IN ('polygon', 'circle')),
            ADD CONSTRAINT ck_geofences_circle CHECK (
                shape <> 'circle' OR (center_lat BETWEEN -90 AND 90 AND center_lon BETWEEN -180 AND 180
                                      AND radius_m BETWEEN 25 AND 50000)),
            ADD CONSTRAINT ck_geofences_archived_inactive CHECK (archived_at IS NULL OR NOT is_active)
    """)

    op.execute("""
        CREATE TABLE fleet_geofence_groups (
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            geofence_id UUID NOT NULL REFERENCES geofences(id) ON DELETE CASCADE,
            group_id    UUID NOT NULL REFERENCES fleet_vehicle_groups(id) ON DELETE CASCADE,
            PRIMARY KEY (geofence_id, group_id)
        )
    """)
    op.execute("CREATE INDEX idx_fleet_geofence_groups_group ON fleet_geofence_groups(group_id)")
    op.execute(f"""
        CREATE TABLE fleet_geofence_rules (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            geofence_id        UUID NOT NULL REFERENCES geofences(id) ON DELETE CASCADE,
            kind               VARCHAR(20) NOT NULL CHECK (kind IN ({_quoted(KINDS)})),
            minutes            INTEGER CHECK (minutes BETWEEN 1 AND 1440),
            speed_kmh          NUMERIC(6, 1) CHECK (speed_kmh BETWEEN 5 AND 300),
            severity           VARCHAR(10) NOT NULL DEFAULT 'medium' CHECK (severity IN ({_quoted(SEVERITIES)})),
            recipient_user_ids UUID[] NOT NULL DEFAULT '{{}}',
            recipient_role_ids INTEGER[] NOT NULL DEFAULT '{{}}',
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (geofence_id, kind),
            CHECK ((kind IN ('dwell_inside', 'dwell_outside')) = (minutes IS NOT NULL)),
            CHECK ((kind = 'overspeed') = (speed_kmh IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE TABLE fleet_geofence_state (
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            vehicle_id       UUID NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            geofence_id      UUID NOT NULL REFERENCES geofences(id) ON DELETE CASCADE,
            inside           BOOLEAN NOT NULL,
            since            TIMESTAMPTZ NOT NULL,
            last_fix_at      TIMESTAMPTZ NOT NULL,
            last_side_fix_at TIMESTAMPTZ NOT NULL,
            pending_inside   BOOLEAN,
            pending_since    TIMESTAMPTZ,
            pending_fixes    INTEGER NOT NULL DEFAULT 0,
            dwell_alerted    BOOLEAN NOT NULL DEFAULT FALSE,
            over_fixes       INTEGER NOT NULL DEFAULT 0,
            over_alerted     BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (vehicle_id, geofence_id)
        )
    """)
    op.execute("CREATE INDEX idx_fleet_geofence_state_geofence ON fleet_geofence_state(geofence_id)")
    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)

    op.execute("""
        ALTER TABLE geofence_events
            ADD COLUMN rule_id       UUID REFERENCES fleet_geofence_rules(id) ON DELETE SET NULL,
            ADD COLUMN alert_id      UUID REFERENCES alerts(id) ON DELETE SET NULL,
            ADD COLUMN between_start TIMESTAMPTZ,
            ADD COLUMN between_end   TIMESTAMPTZ,
            ADD COLUMN accuracy      DOUBLE PRECISION,
            ADD COLUMN detail        JSONB
    """)
    op.execute("CREATE INDEX idx_geoevents_geofence ON geofence_events(geofence_id, occurred_at DESC)")

    # What the older endpoint's flags asked for, as rules: the same severities it raised.
    op.execute("""
        INSERT INTO fleet_geofence_rules (tenant_id, geofence_id, kind, severity)
        SELECT tenant_id, id, 'entry', 'medium' FROM geofences WHERE alert_on_entry
        UNION ALL
        SELECT tenant_id, id, 'exit', 'medium' FROM geofences WHERE alert_on_exit
    """)
    op.execute("""
        INSERT INTO fleet_geofence_rules (tenant_id, geofence_id, kind, severity, speed_kmh)
        SELECT tenant_id, id, 'overspeed', 'high', LEAST(GREATEST(speed_limit, 5), 300)
          FROM geofences WHERE speed_limit IS NOT NULL AND speed_limit > 0
    """)

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
    op.execute("DROP INDEX IF EXISTS idx_geoevents_geofence")
    op.execute("""
        ALTER TABLE geofence_events
            DROP COLUMN IF EXISTS detail, DROP COLUMN IF EXISTS accuracy, DROP COLUMN IF EXISTS between_end,
            DROP COLUMN IF EXISTS between_start, DROP COLUMN IF EXISTS alert_id, DROP COLUMN IF EXISTS rule_id
    """)
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("""
        ALTER TABLE geofences
            DROP CONSTRAINT IF EXISTS ck_geofences_archived_inactive, DROP CONSTRAINT IF EXISTS ck_geofences_circle,
            DROP CONSTRAINT IF EXISTS ck_geofences_shape,
            DROP COLUMN IF EXISTS archive_reason, DROP COLUMN IF EXISTS archived_by_user_id,
            DROP COLUMN IF EXISTS archived_at, DROP COLUMN IF EXISTS created_by_user_id,
            DROP COLUMN IF EXISTS radius_m, DROP COLUMN IF EXISTS center_lon, DROP COLUMN IF EXISTS center_lat,
            DROP COLUMN IF EXISTS shape
    """)
