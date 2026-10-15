"""Fleet tracking: a vehicle's GPS device, the providers that send its positions, and its tracking status.

Phase 2 of the GPS fleet upgrade (docs/plans/2026-10-14-gps-fleet-tracking-design.md,
sections 2 and 3). Positions come from Traccar, which forwards each one with
the device's own ID; this is what decides whose vehicle a position is.

A DEVICE IS ONE VEHICLE'S, ON THE WHOLE PLATFORM. The register of which device
belongs to which vehicle (`fleet_device_registry`) has no tenant policy: one
Traccar serves every organisation, and the position it forwards says only the
device's ID. The application's role may not read or write that register at all.
It goes through four functions that each answer one question, as the database's
owner: claim a device for one of the calling organisation's vehicles, release a
vehicle's device, say whose a device is, and say whose a provider connection is.
Everything after that runs under the owning organisation's own policy.

A POSITION KEEPS THE DEVICE'S TIME. `recorded_at` is the device's fix time, not
the moment the server heard of it (`received_at`); one vehicle has one position
per fix time, so a position forwarded twice is kept once. If this database
already holds two positions of one vehicle at one moment, this migration stops
and says how many: which to keep is not something to guess.

A POSITION THAT IS REFUSED IS COUNTED, NOT KEPT: per vehicle and reason, with
the last one's detail (`fleet_position_rejects`). A device nobody has registered
is counted on its own (`fleet_unknown_devices`), which no organisation can read:
its ID is not theirs to see.

THE TRACKING STATUS (`tracking_status`) is worked out from the last fix time
whenever it is read; the column is what the minute job last saw, so that a
change can be recorded once (`fleet_status_changes`, append-only).

A PROVIDER CONNECTION is an organisation's own Traccar. Its password or token is
kept encrypted (app.core.crypto) and never leaves the server; the secret its
Traccar sends with each position is kept as a hash, shown once when it is made.

Revision ID: 0160
Revises: 0159
"""
from alembic import op

revision = "0160"
down_revision = "0159"
branch_labels = None
depends_on = None

STATUSES = ("online", "stale", "offline", "not_configured")
SOURCES = ("traccar",)
TENANT_TABLES = ("fleet_provider_connections", "fleet_position_rejects", "fleet_status_changes")

PERMISSIONS = [
    ("fleet:device:manage", "Configure fleet GPS devices and provider connections", "gps"),
    ("fleet:alerts:manage", "Set when a fleet vehicle counts as stale or offline, and fleet alert settings", "gps"),
]
ROLES = [(2, "fleet:device:manage"), (8, "fleet:device:manage"), (2, "fleet:alerts:manage"), (8, "fleet:alerts:manage")]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE fleet_provider_connections (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            provider              VARCHAR(20) NOT NULL DEFAULT 'traccar' CHECK (provider IN ('traccar')),
            name                  VARCHAR(120) NOT NULL,
            base_url              VARCHAR(500) NOT NULL,
            auth_kind             VARCHAR(10) NOT NULL CHECK (auth_kind IN ('token', 'basic')),
            username              VARCHAR(255),
            secret_encrypted      TEXT NOT NULL,
            forward_secret_hash   VARCHAR(64) NOT NULL,
            is_active             BOOLEAN NOT NULL DEFAULT TRUE,
            last_test_at          TIMESTAMPTZ,
            last_test_ok          BOOLEAN,
            last_test_message     VARCHAR(500),
            created_by_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_fleet_provider_name ON fleet_provider_connections(tenant_id, lower(name)) WHERE is_active")

    statuses = ", ".join(f"'{s}'" for s in STATUSES)
    sources = ", ".join(f"'{s}'" for s in SOURCES)
    op.execute(f"""
        ALTER TABLE vehicles
          ADD COLUMN tracking_enabled       BOOLEAN NOT NULL DEFAULT FALSE,
          ADD COLUMN tracking_source        VARCHAR(20) CHECK (tracking_source IN ({sources})),
          ADD COLUMN device_identifier      VARCHAR(100),
          ADD COLUMN provider_connection_id UUID REFERENCES fleet_provider_connections(id) ON DELETE SET NULL,
          ADD COLUMN device_assigned_at     TIMESTAMPTZ,
          ADD COLUMN last_fix_at            TIMESTAMPTZ,
          ADD COLUMN last_received_at       TIMESTAMPTZ,
          ADD COLUMN last_heading           SMALLINT,
          ADD COLUMN last_accuracy          NUMERIC(8,2),
          ADD COLUMN last_altitude          NUMERIC(8,2),
          ADD COLUMN last_ignition          BOOLEAN,
          ADD COLUMN last_satellites        SMALLINT,
          ADD COLUMN tracking_status        VARCHAR(20) NOT NULL DEFAULT 'not_configured' CHECK (tracking_status IN ({statuses})),
          ADD COLUMN tracking_status_at     TIMESTAMPTZ
    """)
    op.execute("""
        ALTER TABLE vehicles ADD CONSTRAINT vehicles_device_needs_source
            CHECK (device_identifier IS NULL OR tracking_source IS NOT NULL)
    """)
    op.execute("CREATE INDEX idx_vehicles_tracking ON vehicles(tenant_id, tracking_status) WHERE tracking_enabled")

    # ── positions: the device's time, and once each ──────────────────────────
    op.execute("""
        ALTER TABLE vehicle_positions
          ADD COLUMN received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          ADD COLUMN source      VARCHAR(20),
          ADD COLUMN ignition    BOOLEAN,
          ADD COLUMN satellites  SMALLINT,
          ADD COLUMN altitude    NUMERIC(8,2),
          ADD COLUMN valid       BOOLEAN
    """)
    op.execute("""
        DO $$
        DECLARE twice bigint;
        BEGIN
            SELECT count(*) INTO twice FROM (
                SELECT 1 FROM vehicle_positions GROUP BY vehicle_id, recorded_at HAVING count(*) > 1) d;
            IF twice > 0 THEN
                RAISE EXCEPTION '% moments hold two positions of one vehicle. Decide which to keep before upgrading.', twice;
            END IF;
        END
        $$
    """)
    op.execute("CREATE UNIQUE INDEX uq_vpos_vehicle_fix ON vehicle_positions(vehicle_id, recorded_at)")

    # ── the device register, read and written only through its functions ────
    op.execute("""
        CREATE TABLE fleet_device_registry (
            source_key        VARCHAR(60)  NOT NULL,
            device_identifier VARCHAR(100) NOT NULL,
            tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            vehicle_id        UUID NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            claimed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_key, device_identifier),
            UNIQUE (vehicle_id)
        )
    """)
    op.execute("""
        CREATE TABLE fleet_unknown_devices (
            source_key        VARCHAR(60)  NOT NULL,
            device_identifier VARCHAR(100) NOT NULL,
            reason            VARCHAR(40)  NOT NULL,
            first_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen         TIMESTAMPTZ NOT NULL DEFAULT now(),
            times             BIGINT NOT NULL DEFAULT 1,
            PRIMARY KEY (source_key, device_identifier, reason)
        )
    """)
    for table in ("fleet_device_registry", "fleet_unknown_devices"):
        op.execute(f"REVOKE ALL ON {table} FROM svc_app")

    op.execute("""
        CREATE FUNCTION fleet_device_claim(p_source text, p_identifier text, p_vehicle uuid) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            v_tenant uuid := nullif(current_setting('app.current_tenant', true), '')::uuid;
            v_holder uuid;
        BEGIN
            IF v_tenant IS NULL THEN
                RAISE EXCEPTION 'no organisation is set';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM vehicles WHERE id = p_vehicle AND tenant_id = v_tenant AND archived_at IS NULL) THEN
                RAISE EXCEPTION 'not a vehicle of this organisation';
            END IF;
            SELECT vehicle_id INTO v_holder FROM fleet_device_registry
             WHERE source_key = p_source AND device_identifier = p_identifier;
            IF v_holder IS NOT NULL AND v_holder <> p_vehicle THEN
                RAISE EXCEPTION 'fleet device in use' USING ERRCODE = 'unique_violation';
            END IF;
            DELETE FROM fleet_device_registry WHERE vehicle_id = p_vehicle;
            INSERT INTO fleet_device_registry (source_key, device_identifier, tenant_id, vehicle_id)
            VALUES (p_source, p_identifier, v_tenant, p_vehicle);
        END
        $$
    """)
    op.execute("""
        CREATE FUNCTION fleet_device_release(p_vehicle uuid) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        BEGIN
            DELETE FROM fleet_device_registry
             WHERE vehicle_id = p_vehicle
               AND tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid;
        END
        $$
    """)
    op.execute("""
        CREATE FUNCTION fleet_device_owner(p_source text, p_identifier text)
        RETURNS TABLE (tenant_id uuid, vehicle_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT r.tenant_id, r.vehicle_id FROM fleet_device_registry r
             WHERE r.source_key = p_source AND r.device_identifier = p_identifier
        $$
    """)
    op.execute("""
        CREATE FUNCTION fleet_connection_owner(p_connection uuid)
        RETURNS TABLE (tenant_id uuid, forward_secret_hash text, is_active boolean)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT c.tenant_id, c.forward_secret_hash::text, c.is_active
              FROM fleet_provider_connections c WHERE c.id = p_connection
        $$
    """)
    op.execute("""
        CREATE FUNCTION fleet_note_unknown_device(p_source text, p_identifier text, p_reason text) RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
            INSERT INTO fleet_unknown_devices (source_key, device_identifier, reason)
            VALUES (left(p_source, 60), left(p_identifier, 100), left(p_reason, 40))
            ON CONFLICT (source_key, device_identifier, reason)
            DO UPDATE SET last_seen = now(), times = fleet_unknown_devices.times + 1
        $$
    """)
    for fn in ("fleet_device_claim(text, text, uuid)", "fleet_device_release(uuid)",
               "fleet_device_owner(text, text)", "fleet_connection_owner(uuid)",
               "fleet_note_unknown_device(text, text, text)"):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO svc_app")

    # ── what was refused, and what the status went through ───────────────────
    op.execute("""
        CREATE TABLE fleet_position_rejects (
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            vehicle_id  UUID NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            reason      VARCHAR(40) NOT NULL,
            times       BIGINT NOT NULL DEFAULT 1,
            first_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_detail JSONB,
            PRIMARY KEY (vehicle_id, reason)
        )
    """)
    op.execute(f"""
        CREATE TABLE fleet_status_changes (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            vehicle_id  UUID NOT NULL REFERENCES vehicles(id) ON DELETE NO ACTION,
            from_status VARCHAR(20) NOT NULL CHECK (from_status IN ({statuses})),
            to_status   VARCHAR(20) NOT NULL CHECK (to_status IN ({statuses})),
            last_fix_at TIMESTAMPTZ,
            changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_fleet_status_changes_vehicle ON fleet_status_changes(vehicle_id, changed_at DESC)")
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)
    # A connection is retired, never removed; the status history is only ever added to.
    op.execute("REVOKE DELETE ON fleet_provider_connections FROM svc_app")
    op.execute("REVOKE UPDATE, DELETE ON fleet_status_changes FROM svc_app")
    op.execute("REVOKE DELETE ON fleet_position_rejects FROM svc_app")

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
    for fn in ("fleet_note_unknown_device(text, text, text)", "fleet_connection_owner(uuid)",
               "fleet_device_owner(text, text)", "fleet_device_release(uuid)", "fleet_device_claim(text, text, uuid)"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    for table in ("fleet_status_changes", "fleet_position_rejects", "fleet_unknown_devices", "fleet_device_registry"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP INDEX IF EXISTS uq_vpos_vehicle_fix")
    op.execute("""
        ALTER TABLE vehicle_positions
          DROP COLUMN valid, DROP COLUMN altitude, DROP COLUMN satellites, DROP COLUMN ignition,
          DROP COLUMN source, DROP COLUMN received_at
    """)
    op.execute("DROP INDEX IF EXISTS idx_vehicles_tracking")
    op.execute("ALTER TABLE vehicles DROP CONSTRAINT IF EXISTS vehicles_device_needs_source")
    op.execute("""
        ALTER TABLE vehicles
          DROP COLUMN tracking_status_at, DROP COLUMN tracking_status, DROP COLUMN last_satellites,
          DROP COLUMN last_ignition, DROP COLUMN last_altitude, DROP COLUMN last_accuracy, DROP COLUMN last_heading,
          DROP COLUMN last_received_at, DROP COLUMN last_fix_at, DROP COLUMN device_assigned_at,
          DROP COLUMN provider_connection_id, DROP COLUMN device_identifier, DROP COLUMN tracking_source,
          DROP COLUMN tracking_enabled
    """)
    op.execute("DROP TABLE IF EXISTS fleet_provider_connections")
