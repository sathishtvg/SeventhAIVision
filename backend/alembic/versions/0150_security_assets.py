"""Security assets, the health of the devices behind them, and the work done to keep them working.

Additive: four new tables and four permissions. No existing table, policy or
permission is altered; cameras, recorders, sensors, drones, gateways, alarm
panels, guard kit and facility defects are as they were
(LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 8).

  a DEVICE the platform already knows ──► its HEALTH, read from what it reports
          │                                    │ kept only when it CHANGES (device_health_changes)
          ▼                                    ▼
  an ASSET in the register            down for long, or a SCHEDULE falls due
  (make, serial, vendor, warranty)             │
          │                                    ▼
          └──────────────►  a WORK ORDER:  SUGGESTED ─► a person accepts it ─► OPEN ─► IN PROGRESS ─► DONE
                                                   └─► or dismisses it, with why

THE PLATFORM SUGGESTS; A PERSON RAISES THE WORK. A work order that the platform
suggested is nothing until somebody accepts it, and the database holds that: a
suggested order cannot be open, in progress or done without who accepted it.

A HEALTH CHANGE IS WHAT WAS OBSERVED, WHEN. The log takes no update and no
delete. It records the state a device was read to be in when that state
differed from the one before - not a measurement of anything the platform is
not told (frame rate, latency, packet loss).

THE REGISTER IS `asset_register`, NOT `security_assets`. Tables named
`security_...` are the AI security intelligence layer's own: its tests list every
one of them and say what the application may do to each. An asset register is
not part of that layer and does not take a name inside it.

A WORK ORDER THAT IS OVER IS NOT REWRITTEN. A trigger refuses a change to one
that is done, cancelled or dismissed.

SUPER ADMIN (1), GUARD (5) AND CLIENT (7) GET NO PERMISSION HERE.

Revision ID: 0150
Revises: 0149
"""
from alembic import op

revision = "0150"
down_revision = "0149"
branch_labels = None
depends_on = None

ASSET_KINDS = ("'CAMERA','NVR','SERVER','EDGE_GATEWAY','ACCESS_CONTROLLER','ALARM_PANEL','DRONE','SENSOR','UPS',"
               "'NETWORK','OTHER'")
ASSET_STATUSES = "'IN_SERVICE','UNDER_REPAIR','SPARE','RETIRED'"
DEVICE_KINDS = "'CAMERA','NVR','SENSOR','DRONE','EDGE_GATEWAY','ALARM_PANEL'"
HEALTH_STATES = "'OK','DEGRADED','DOWN','NOT_KNOWN','OFF'"
ORDER_KINDS = "'CORRECTIVE','PREVENTIVE','INSPECTION'"
PRIORITIES = "'LOW','NORMAL','HIGH','URGENT'"
ORDER_STATES = "'SUGGESTED','OPEN','IN_PROGRESS','DONE','CANCELLED','DISMISSED'"
ORIGINS = "'PERSON','DEFECT','HEALTH','SCHEDULE'"

#: The device an asset is, when the platform knows it as one: column, the table it is in, and the kind that goes with it.
DEVICE_LINKS = [
    ("camera_id", "cameras", "CAMERA"), ("nvr_connection_id", "nvr_connections", "NVR"),
    ("iot_sensor_id", "iot_sensors", "SENSOR"), ("drone_id", "drones", "DRONE"),
    ("edge_gateway_id", "drone_edge_gateways", "EDGE_GATEWAY"), ("alarm_panel_id", "alarm_panels", "ALARM_PANEL"),
]

PERMISSIONS = [
    ("asset:read", "Read the asset register and the health of devices", "device"),
    ("asset:manage", "Add, change and retire assets", "device"),
    ("maintenance:read", "Read work orders and maintenance schedules, and work the orders one is assigned", "maintenance"),
    ("maintenance:manage", "Raise, accept, dismiss, assign and cancel work orders, and keep schedules", "maintenance"),
]

#: What changes of an asset. Not its code, its kind, or who entered it and when.
ASSET_CHANGES = ("site_id, name, make, model, serial_number, location, place_id, vendor, installed_on, warranty_until, "
                 "status, notes, camera_id, nvr_connection_id, iot_sensor_id, drone_id, edge_gateway_id, "
                 "alarm_panel_id, updated_by_user_id, updated_at, retired_at, retired_by_user_id, retire_reason")
SCHEDULE_CHANGES = ("title, instructions, every_days, lead_days, next_due_on, last_done_on, is_active, "
                    "updated_by_user_id, updated_at")
#: What changes of a work order. Not its number, where it came from, why it
#: was suggested, what kind of work it is, or who raised it and when.
ORDER_CHANGES = ("state, title, description, priority, asset_id, due_at, accepted_by_user_id, accepted_at, "
                 "assigned_to_user_id, assigned_to_name, assigned_at, started_at, started_by_user_id, completed_at, "
                 "completed_by_user_id, completion_note, parts_used, downtime_minutes, closed_at, closed_by_user_id, "
                 "closed_reason, updated_at")


def upgrade() -> None:
    links = "\n".join(f"            {column:<18} UUID REFERENCES {table}(id) ON DELETE SET NULL," for column, table, _ in DEVICE_LINKS)
    columns = ", ".join(column for column, _, _ in DEVICE_LINKS)
    of_its_kind = " AND ".join(f"({column} IS NULL OR kind = '{kind}')" for column, _, kind in DEVICE_LINKS)
    op.execute(f"""
        CREATE TABLE asset_register (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id            UUID REFERENCES sites(id) ON DELETE SET NULL,
            -- What it is cited by: AST-0001, numbered per organisation.
            asset_code         VARCHAR(20) NOT NULL,
            kind               VARCHAR(20) NOT NULL,
            name               VARCHAR(200) NOT NULL,
            make               VARCHAR(120),
            model              VARCHAR(120),
            serial_number      VARCHAR(120),
            location           TEXT,
            place_id           UUID REFERENCES site_places(id) ON DELETE SET NULL,
            vendor             VARCHAR(200),
            installed_on       DATE,
            warranty_until     DATE,
            status             VARCHAR(14) NOT NULL DEFAULT 'IN_SERVICE',
            notes              TEXT,
            -- The device it is, when the platform knows it as one. At most one.
{links}
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            retired_at         TIMESTAMPTZ,
            retired_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            retire_reason      TEXT,
            CONSTRAINT uq_asset_code        UNIQUE (tenant_id, asset_code),
            CONSTRAINT ck_asset_kind        CHECK (kind IN ({ASSET_KINDS})),
            CONSTRAINT ck_asset_status      CHECK (status IN ({ASSET_STATUSES})),
            CONSTRAINT ck_asset_name        CHECK (btrim(name) <> ''),
            CONSTRAINT ck_asset_one_device  CHECK (num_nonnulls({columns}) <= 1),
            -- A camera in the register is the asset of a camera, not of a drone.
            CONSTRAINT ck_asset_device_kind CHECK ({of_its_kind}),
            CONSTRAINT ck_asset_retired     CHECK ((status = 'RETIRED') = (retired_at IS NOT NULL)),
            CONSTRAINT ck_asset_retired_why CHECK (status <> 'RETIRED' OR (
                retire_reason IS NOT NULL AND btrim(retire_reason) <> ''))
        )
    """)
    for column, _, _ in DEVICE_LINKS:
        # A device is one asset.
        op.execute(f"CREATE UNIQUE INDEX uq_asset_{column} ON asset_register ({column}) WHERE {column} IS NOT NULL")
    op.execute("CREATE INDEX idx_assets_site ON asset_register (tenant_id, site_id, kind)")

    op.execute(f"""
        CREATE TABLE device_health_changes (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            device_kind VARCHAR(20) NOT NULL,
            device_id   UUID NOT NULL,
            site_id     UUID REFERENCES sites(id) ON DELETE SET NULL,
            state       VARCHAR(10) NOT NULL,
            -- Why it was read so, in the words it was shown in.
            reasons     JSONB NOT NULL DEFAULT '[]'::jsonb,
            observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_dhc_kind  CHECK (device_kind IN ({DEVICE_KINDS})),
            CONSTRAINT ck_dhc_state CHECK (state IN ({HEALTH_STATES}))
        )
    """)
    op.execute("CREATE INDEX idx_dhc_device ON device_health_changes (tenant_id, device_kind, device_id, observed_at DESC)")

    op.execute("""
        CREATE TABLE maintenance_schedules (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id            UUID REFERENCES sites(id) ON DELETE SET NULL,
            asset_id           UUID REFERENCES asset_register(id) ON DELETE CASCADE,
            title              VARCHAR(200) NOT NULL,
            instructions       TEXT,
            every_days         INTEGER NOT NULL,
            -- How long before it is due the work is put forward.
            lead_days          SMALLINT NOT NULL DEFAULT 7,
            next_due_on        DATE NOT NULL,
            last_done_on       DATE,
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_msched_title CHECK (btrim(title) <> ''),
            CONSTRAINT ck_msched_every CHECK (every_days BETWEEN 1 AND 3650),
            CONSTRAINT ck_msched_lead  CHECK (lead_days BETWEEN 0 AND 90)
        )
    """)
    op.execute("CREATE INDEX idx_msched_due ON maintenance_schedules (tenant_id, next_due_on) WHERE is_active")

    op.execute(f"""
        CREATE TABLE maintenance_work_orders (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- What it is cited by: WO-0001, numbered per organisation.
            number               VARCHAR(20) NOT NULL,
            site_id              UUID REFERENCES sites(id) ON DELETE SET NULL,
            asset_id             UUID REFERENCES asset_register(id) ON DELETE SET NULL,
            schedule_id          UUID REFERENCES maintenance_schedules(id) ON DELETE SET NULL,
            defect_id            UUID REFERENCES facility_defects(id) ON DELETE SET NULL,
            title                VARCHAR(200) NOT NULL,
            description          TEXT,
            kind                 VARCHAR(12) NOT NULL DEFAULT 'CORRECTIVE',
            priority             VARCHAR(8) NOT NULL DEFAULT 'NORMAL',
            state                VARCHAR(12) NOT NULL DEFAULT 'OPEN',
            origin               VARCHAR(10) NOT NULL DEFAULT 'PERSON',
            -- What a suggestion was made of, so that it is made once.
            origin_key           VARCHAR(160),
            -- The fact it was suggested from, as it was when it was suggested.
            suggestion_reason    TEXT,
            raised_by_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
            raised_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            accepted_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            accepted_at          TIMESTAMPTZ,
            assigned_to_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            -- A vendor or a technician who is not one of the organisation's people.
            assigned_to_name     VARCHAR(200),
            assigned_at          TIMESTAMPTZ,
            due_at               TIMESTAMPTZ,
            started_at           TIMESTAMPTZ,
            started_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            completed_at         TIMESTAMPTZ,
            completed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            completion_note      TEXT,
            parts_used           TEXT,
            -- How long the thing was out of use, as whoever did the work states it.
            downtime_minutes     INTEGER,
            closed_at            TIMESTAMPTZ,
            closed_by_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
            closed_reason        TEXT,
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_wo_number     UNIQUE (tenant_id, number),
            CONSTRAINT ck_wo_title      CHECK (btrim(title) <> ''),
            CONSTRAINT ck_wo_kind       CHECK (kind IN ({ORDER_KINDS})),
            CONSTRAINT ck_wo_priority   CHECK (priority IN ({PRIORITIES})),
            CONSTRAINT ck_wo_state      CHECK (state IN ({ORDER_STATES})),
            CONSTRAINT ck_wo_origin     CHECK (origin IN ({ORIGINS})),
            CONSTRAINT ck_wo_downtime   CHECK (downtime_minutes IS NULL OR downtime_minutes >= 0),
            -- Only the platform suggests, and what it suggests says what from.
            CONSTRAINT ck_wo_suggestion CHECK (
                (origin IN ('HEALTH', 'SCHEDULE')) = (suggestion_reason IS NOT NULL AND origin_key IS NOT NULL)),
            CONSTRAINT ck_wo_suggested  CHECK (state NOT IN ('SUGGESTED', 'DISMISSED') OR origin IN ('HEALTH', 'SCHEDULE')),
            -- What the platform suggested is work only once a person has accepted it.
            CONSTRAINT ck_wo_accepted   CHECK (origin NOT IN ('HEALTH', 'SCHEDULE') OR state IN ('SUGGESTED', 'DISMISSED')
                                               OR accepted_at IS NOT NULL),
            CONSTRAINT ck_wo_started    CHECK (state NOT IN ('IN_PROGRESS', 'DONE') OR started_at IS NOT NULL),
            CONSTRAINT ck_wo_done       CHECK (state <> 'DONE' OR (
                completed_at IS NOT NULL AND completion_note IS NOT NULL AND btrim(completion_note) <> '')),
            CONSTRAINT ck_wo_closed     CHECK (state NOT IN ('CANCELLED', 'DISMISSED') OR (
                closed_at IS NOT NULL AND closed_reason IS NOT NULL AND btrim(closed_reason) <> ''))
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_wo_origin ON maintenance_work_orders (tenant_id, origin_key) "
               "WHERE origin_key IS NOT NULL")
    op.execute("CREATE INDEX idx_wo_state ON maintenance_work_orders (tenant_id, state, due_at)")
    op.execute("CREATE INDEX idx_wo_asset ON maintenance_work_orders (asset_id) WHERE asset_id IS NOT NULL")

    # One that is over stands. The depth test lets a person who leaves be
    # forgotten (the foreign keys set their name to NULL) and nothing else.
    op.execute("""
        CREATE FUNCTION maintenance_work_order_over() RETURNS trigger AS $$
        BEGIN
            IF OLD.state IN ('DONE', 'CANCELLED', 'DISMISSED') AND pg_trigger_depth() = 1 THEN
                RAISE EXCEPTION 'a work order that is over is not changed';
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER maintenance_work_order_over BEFORE UPDATE ON maintenance_work_orders "
               "FOR EACH ROW EXECUTE FUNCTION maintenance_work_order_over()")

    for table in ("asset_register", "device_health_changes", "maintenance_schedules", "maintenance_work_orders"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)
        # The REVOKE is what does it: default privileges hand the application
        # UPDATE and DELETE on every new table.
        op.execute(f"REVOKE ALL ON {table} FROM svc_app")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO svc_app")
    op.execute(f"GRANT UPDATE ({ASSET_CHANGES}) ON asset_register TO svc_app")
    op.execute(f"GRANT UPDATE ({SCHEDULE_CHANGES}) ON maintenance_schedules TO svc_app")
    op.execute(f"GRANT UPDATE ({ORDER_CHANGES}) ON maintenance_work_orders TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    # Read by whoever watches the site from a desk (2, 3, 4, 6, 8). Kept by
    # Admin, Manager and Supervisor. A guard reports a defect where they always
    # have; a client and the platform owner hold nothing here.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'asset:read'), (2, 'asset:manage'), (2, 'maintenance:read'), (2, 'maintenance:manage'),
                  (8, 'asset:read'), (8, 'asset:manage'), (8, 'maintenance:read'), (8, 'maintenance:manage'),
                  (3, 'asset:read'), (3, 'asset:manage'), (3, 'maintenance:read'), (3, 'maintenance:manage'),
                  (4, 'asset:read'), (4, 'maintenance:read'),
                  (6, 'asset:read'), (6, 'maintenance:read')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions
                                  WHERE code IN ('asset:read', 'asset:manage', 'maintenance:read', 'maintenance:manage'))
    """)
    op.execute("DELETE FROM permissions WHERE code IN ('asset:read', 'asset:manage', 'maintenance:read', 'maintenance:manage')")
    for table in ("maintenance_work_orders", "maintenance_schedules", "device_health_changes", "asset_register"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS maintenance_work_order_over()")
