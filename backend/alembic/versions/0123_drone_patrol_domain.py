"""Drone patrol, phase 2: the domain — fleet, routes, zones, missions, sessions, events.

Everything the Autonomous Drone Security Patrol module stores, and nothing it
does yet. The gap analysis this follows is DRONE_PATROL_GAP_ANALYSIS.md.

PURELY ADDITIVE. Every table here is new and prefixed drone_. No existing table
gains a column, loses a constraint or changes a default. Where a drone row
needs to point at something that already exists — a site, a camera, an alert,
an incident — the new table holds the reference; the existing table does not
learn about drones. `patrol_sessions`, `patrol_routes` and friends belong to the
physical guard patrol, which is why nothing here is called simply `patrol_*`.

THE ENTITLEMENT DOES NOT LIVE IN tenant_module_licenses. It looks like the
obvious home, and it is not safe. /licenses/me/enabled-modules and
cameras._check_module_licenses both treat a tenant with *no* licence rows as
licensed for every AI module. Writing a drone_patrol row for such a tenant would
give it one row, switch that fallback off, and silently take away all eleven AI
modules. So the drone entitlement and its limits live in drone_module_licenses,
which nothing outside this module reads.

CONFIGURATION IS COPIED INTO THE SESSION (config_snapshot, and one
drone_session_waypoints row per waypoint), the same rule Virtual Patrolling
follows. Editing a route while a drone is flying it must not change what that
flight is doing, and must not change what last month's report says it did.

ONE DRONE, ONE FLIGHT. A partial unique index allows at most one session per
drone in any in-flight state. Two schedulers, a double-click, or a retry cannot
put a drone on two missions — the second insert fails.

IDEMPOTENCY IS A CONSTRAINT, NOT AN IF STATEMENT.
  - UNIQUE (schedule_id, scheduled_for) on sessions: a scheduler restart or two
    workers cannot create a second session for one scheduled run.
  - client_ref UNIQUE on sessions, events and media: records created at a site
    edge carry their own stable id, so re-sending them after a reconnect is a
    no-op, not a duplicate.
  - UNIQUE (drone_id, recorded_at) on telemetry: a resent sample is ignored.

DRONE MEDIA IS NOT IN `evidence`. purge_expired_evidence() deletes every
evidence row past the tenant's retention window — file first, then row — with
no exemption for an open incident. drone_event_media sits outside that purge.
Its storage_location and sync_state use the same vocabulary as recordings and
evidence, so the edge-to-central sync these columns were designed for reads the
same everywhere.

TELEMETRY IS PARTITIONED MONTHLY by pg_partman, like iot_readings and
vehicle_positions, and like iot_readings it carries no foreign key on drone_id or
session_id: it is written several times a second per drone, and the writer
already knows the ids are valid. Retention is dropping old partitions, not
deleting rows.

NO FOREIGN KEY TO detections. It is partitioned, and its primary key includes
detected_at, so an FK on the id alone is impossible. drone_events.detection_id
is a plain reference.

DELETION NEVER BLOCKS. Every tenant FK cascades and every other FK sets NULL, so
the test suite's tenant cleanup — which has been bitten by a RESTRICT before —
cannot be stopped by a drone row. History survives a deleted mission, route or
drone because sessions and events keep their own copies of names.

Revision ID: 0123
Revises: 0122
"""
from alembic import op

revision = "0123"
down_revision = "0122"
branch_labels = None
depends_on = None

DRONE_STATUSES = ("'OFFLINE','STANDBY','READY','PREPARING','MISSION_ACTIVE','RETURNING',"
                  "'CHARGING','WARNING','COMMUNICATION_LOST','CRITICAL','MAINTENANCE','DISABLED'")
COMPONENT_STATES = "'OK','WARNING','FAULT','UNKNOWN'"
GATEWAY_STATUSES = "'ONLINE','OFFLINE','DEGRADED','UNKNOWN'"
MAINTENANCE_TYPES = ("'SERVICE','INSPECTION','BATTERY_REPLACEMENT','FIRMWARE_UPDATE',"
                     "'CAMERA_SERVICE','REPAIR','FAULT'")
ZONE_TYPES = ("'NORMAL','RESTRICTED','CRITICAL','VEHICLE_RESTRICTED','PERSON_RESTRICTED',"
              "'NO_ENTRY','SPECIAL_INSPECTION'")
ZONE_SHAPES = "'POLYGON','RECTANGLE','CIRCLE'"
RISK_LEVELS = "'INFO','LOW','MEDIUM','HIGH','CRITICAL'"
ALERT_POLICIES = "'NONE','ALERT','INCIDENT'"
# The AI worker module codes, exactly as detections.module_type spells them, so a
# rule joins straight onto what a worker produced. Only modules that exist.
AI_MODULES = ("'lpr','face','intrusion','ppe','crowd','fire_smoke','weapon',"
              "'behavior','tampering','abandoned','fall'")
SCHEDULE_TYPES = "'ONCE','DAILY','WEEKLY','SELECTED_DAYS','SPECIFIC_DATE'"
# recording_policies.sync_mode's vocabulary. NULL on a mission means "inherit the
# site's recording policy".
SYNC_MODES = "'central','local_only','incident_only','scheduled','manual'"
SESSION_TRIGGERS = "'SCHEDULE','MANUAL','VERIFY_REQUEST'"
SESSION_STATUSES = ("'SCHEDULED','PRECHECK','READY','LAUNCHING','ACTIVE','PAUSED',"
                    "'EVENT_DETECTED','RETURNING','COMPLETED','FAILED','ABORTED',"
                    "'CANCELLED','BLOCKED','MISSED'")
# The states in which a drone is committed to a flight. At most one session per
# drone may be in any of them.
IN_FLIGHT = ("'PRECHECK','READY','LAUNCHING','ACTIVE','PAUSED',"
             "'EVENT_DETECTED','RETURNING'")
WAYPOINT_RUN_STATUSES = "'PENDING','REACHED','OBSERVED','SKIPPED','FAILED'"
LOCATION_METHODS = "'DRONE_POSITION','PROJECTED'"
VERIFICATION_STATES = "'UNVERIFIED','OBSERVING','VERIFIED','DISMISSED'"
EVENT_STATUSES = ("'NEW','ACKNOWLEDGED','INVESTIGATING','ESCALATED','RESOLVED',"
                  "'FALSE_POSITIVE'")
MEDIA_KINDS = "'SNAPSHOT','PRE_CLIP','EVENT_CLIP','POST_CLIP','CLIP'"
# Same vocabulary and meaning as recordings / evidence.
STORAGE_LOCATIONS = "'central','local','both'"
SYNC_STATES = "'not_required','pending','uploading','synced','failed'"
CORRELATION_METHODS = "'DISTANCE','COVERAGE'"

# Order matters: a table is dropped before anything it references.
TENANT_TABLES = (
    "drone_module_licenses",
    "drone_provider_configs",
    "drone_edge_gateways",
    "drones",
    "drone_maintenance_logs",
    "drone_security_zones",
    "drone_security_profiles",
    "drone_profile_rules",
    "drone_routes",
    "drone_waypoints",
    "drone_missions",
    "drone_schedules",
    "drone_patrol_sessions",
    "drone_session_waypoints",
    "drone_telemetry",
    "drone_events",
    "drone_event_media",
    "drone_event_cameras",
)

PERMISSIONS = [
    ("drone:read", "View drones, their health and the fleet", "drone_patrol"),
    ("drone:create", "Register drones, providers and edge gateways", "drone_patrol"),
    ("drone:update", "Edit drones, providers and edge gateways", "drone_patrol"),
    ("drone:delete", "Remove drones, providers and edge gateways", "drone_patrol"),
    ("drone:operate", "Watch live drone missions and use operator controls", "drone_patrol"),
    ("drone:mission:create", "Create missions, routes, security zones and profiles", "drone_patrol"),
    ("drone:mission:update", "Change missions, routes, security zones, profiles and schedules", "drone_patrol"),
    ("drone:mission:execute", "Start a drone mission", "drone_patrol"),
    ("drone:mission:abort", "Abort a drone mission in flight", "drone_patrol"),
    ("drone:event:read", "View drone security events and their media", "drone_patrol"),
    ("drone:event:acknowledge", "Acknowledge drone security events", "drone_patrol"),
    ("drone:event:investigate", "Investigate, escalate and close drone security events", "drone_patrol"),
    ("drone:maintenance:read", "View drone maintenance history", "drone_patrol"),
    ("drone:maintenance:manage", "Record drone maintenance and faults", "drone_patrol"),
    ("drone:report:read", "View drone mission reports", "drone_patrol"),
    ("drone:report:export", "Export drone mission reports and data", "drone_patrol"),
]


def upgrade() -> None:
    # ── Entitlement ──────────────────────────────────────────────────────────
    #
    # One row per tenant that has been offered the module. No row, or
    # is_enabled = FALSE, or past expires_at: the module is off. A NULL limit is
    # "no limit", not zero.
    op.execute("""
        CREATE TABLE drone_module_licenses (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
            is_enabled          BOOLEAN NOT NULL DEFAULT FALSE,
            licensed_at         TIMESTAMPTZ,
            expires_at          TIMESTAMPTZ,
            max_drones          INTEGER,
            max_missions        INTEGER,
            max_sites           INTEGER,
            licensed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            notes               TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_dml_max_drones   CHECK (max_drones   IS NULL OR max_drones   >= 0),
            CONSTRAINT ck_dml_max_missions CHECK (max_missions IS NULL OR max_missions >= 0),
            CONSTRAINT ck_dml_max_sites    CHECK (max_sites    IS NULL OR max_sites    >= 0)
        )
    """)

    # ── Fleet ────────────────────────────────────────────────────────────────
    #
    # provider_key is validated against the provider registry in code rather
    # than a CHECK, so adding a provider adapter needs no migration. The secret
    # is a Fernet token from core.crypto, kept out of `config` so it can never
    # be returned with it — the same split device_protocols makes.
    op.execute("""
        CREATE TABLE drone_provider_configs (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name               VARCHAR(120) NOT NULL,
            provider_key       VARCHAR(60)  NOT NULL,
            config             JSONB NOT NULL DEFAULT '{}'::jsonb,
            secret_encrypted   TEXT,
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dpc_name UNIQUE (tenant_id, name)
        )
    """)

    # A site's edge gateway authenticates to central with a token; only its
    # SHA-256 is stored, as refresh tokens are. credential_prefix identifies a
    # token in logs without revealing it.
    op.execute(f"""
        CREATE TABLE drone_edge_gateways (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id               UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            name                  VARCHAR(120) NOT NULL,
            code                  VARCHAR(40)  NOT NULL,
            status                VARCHAR(20)  NOT NULL DEFAULT 'UNKNOWN',
            last_seen_at          TIMESTAMPTZ,
            software_version      VARCHAR(40),
            credential_hash       VARCHAR(64),
            credential_prefix     VARCHAR(12),
            credential_rotated_at TIMESTAMPTZ,
            is_active             BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_deg_code   UNIQUE (tenant_id, code),
            CONSTRAINT ck_deg_status CHECK (status IN ({GATEWAY_STATUSES}))
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_deg_credential_hash ON drone_edge_gateways (credential_hash) "
               "WHERE credential_hash IS NOT NULL")

    # camera_id is the cameras row that stands for this drone's video (decision
    # D1): it is how the unchanged ingestion and AI workers see the feed.
    # DISABLED is the soft delete — a drone with flight history is disabled, not
    # removed. heartbeat_timeout_seconds mirrors iot_sensors'
    # expected_interval_seconds: silence longer than this is COMMUNICATION_LOST.
    op.execute(f"""
        CREATE TABLE drones (
            id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id                 UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id                   UUID REFERENCES sites(id) ON DELETE SET NULL,
            provider_config_id        UUID REFERENCES drone_provider_configs(id) ON DELETE SET NULL,
            edge_gateway_id           UUID REFERENCES drone_edge_gateways(id) ON DELETE SET NULL,
            camera_id                 UUID REFERENCES cameras(id) ON DELETE SET NULL,
            name                      VARCHAR(120) NOT NULL,
            code                      VARCHAR(40)  NOT NULL,
            manufacturer              VARCHAR(120),
            model                     VARCHAR(120),
            serial_number             VARCHAR(120),
            firmware_version          VARCHAR(60),
            drone_type                VARCHAR(40),
            camera_type               VARCHAR(40),
            communication_type        VARCHAR(40),
            provider_drone_ref        VARCHAR(120),
            status                    VARCHAR(24) NOT NULL DEFAULT 'OFFLINE',
            battery_level             SMALLINT,
            battery_health            SMALLINT,
            gps_status                VARCHAR(10) NOT NULL DEFAULT 'UNKNOWN',
            communication_status      VARCHAR(10) NOT NULL DEFAULT 'UNKNOWN',
            camera_status             VARCHAR(10) NOT NULL DEFAULT 'UNKNOWN',
            storage_status            VARCHAR(10) NOT NULL DEFAULT 'UNKNOWN',
            temperature_c             NUMERIC(5,1),
            current_latitude          DOUBLE PRECISION,
            current_longitude         DOUBLE PRECISION,
            current_altitude_m        NUMERIC(7,2),
            current_heading_deg       NUMERIC(5,2),
            current_speed_mps         NUMERIC(6,2),
            last_heartbeat_at         TIMESTAMPTZ,
            heartbeat_timeout_seconds INTEGER NOT NULL DEFAULT 30,
            total_flight_seconds      BIGINT  NOT NULL DEFAULT 0,
            last_flight_at            TIMESTAMPTZ,
            last_maintenance_at       TIMESTAMPTZ,
            next_maintenance_at       TIMESTAMPTZ,
            maintenance_interval_hours INTEGER,
            created_by_user_id        UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_by_user_id        UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_drones_code            UNIQUE (tenant_id, code),
            CONSTRAINT ck_drones_status          CHECK (status IN ({DRONE_STATUSES})),
            CONSTRAINT ck_drones_gps             CHECK (gps_status           IN ({COMPONENT_STATES})),
            CONSTRAINT ck_drones_comms           CHECK (communication_status IN ({COMPONENT_STATES})),
            CONSTRAINT ck_drones_camera          CHECK (camera_status        IN ({COMPONENT_STATES})),
            CONSTRAINT ck_drones_storage         CHECK (storage_status       IN ({COMPONENT_STATES})),
            CONSTRAINT ck_drones_battery         CHECK (battery_level  IS NULL OR battery_level  BETWEEN 0 AND 100),
            CONSTRAINT ck_drones_battery_health  CHECK (battery_health IS NULL OR battery_health BETWEEN 0 AND 100),
            CONSTRAINT ck_drones_lat             CHECK (current_latitude  IS NULL OR current_latitude  BETWEEN -90  AND 90),
            CONSTRAINT ck_drones_lng             CHECK (current_longitude IS NULL OR current_longitude BETWEEN -180 AND 180),
            CONSTRAINT ck_drones_heading         CHECK (current_heading_deg IS NULL OR current_heading_deg >= 0 AND current_heading_deg < 360),
            CONSTRAINT ck_drones_heartbeat       CHECK (heartbeat_timeout_seconds > 0),
            CONSTRAINT ck_drones_flight_seconds  CHECK (total_flight_seconds >= 0),
            CONSTRAINT ck_drones_maint_interval  CHECK (maintenance_interval_hours IS NULL OR maintenance_interval_hours > 0)
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_drones_serial ON drones (tenant_id, serial_number) "
               "WHERE serial_number IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX uq_drones_camera ON drones (camera_id) WHERE camera_id IS NOT NULL")
    op.execute("CREATE INDEX idx_drones_site   ON drones (tenant_id, site_id)")
    op.execute("CREATE INDEX idx_drones_status ON drones (tenant_id, status)")
    # The heartbeat sweep's query: live drones, oldest heartbeat first.
    op.execute("CREATE INDEX idx_drones_heartbeat ON drones (last_heartbeat_at) "
               "WHERE status NOT IN ('DISABLED','MAINTENANCE')")

    op.execute(f"""
        CREATE TABLE drone_maintenance_logs (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id              UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            drone_id               UUID NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
            maintenance_type       VARCHAR(24) NOT NULL,
            description            TEXT,
            performed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            performed_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            performed_by_name      VARCHAR(160),
            flight_seconds_at      BIGINT,
            firmware_version_after VARCHAR(60),
            next_due_at            TIMESTAMPTZ,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_dmlog_type CHECK (maintenance_type IN ({MAINTENANCE_TYPES}))
        )
    """)
    op.execute("CREATE INDEX idx_dmlog_drone ON drone_maintenance_logs (drone_id, performed_at DESC)")

    # ── Security zones and profiles ──────────────────────────────────────────
    #
    # MAP space, not image space. crowd_zones is drawn over one fixed camera's
    # picture; a drone's picture moves every second, so its zones are
    # latitude/longitude shapes, evaluated with geofence.point_in_polygon
    # against where the drone was when the frame was taken.
    #
    # polygon is [{"lat": .., "lng": ..}, ...] — the shape sites.geofence_polygon
    # already uses and geofence.normalize_polygon already reads. A rectangle is
    # stored as its four corners. A circle is a centre and a radius.
    #
    # active_from/active_to bound the zone to a time of day (NULL = always);
    # active_weekdays (0 = Monday) to days. allowed_* list who or what may be in
    # the zone without it being a finding.
    op.execute(f"""
        CREATE TABLE drone_security_zones (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id              UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id                UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            name                   VARCHAR(120) NOT NULL,
            description            TEXT,
            zone_type              VARCHAR(24) NOT NULL DEFAULT 'NORMAL',
            shape                  VARCHAR(12) NOT NULL DEFAULT 'POLYGON',
            polygon                JSONB,
            center_latitude        DOUBLE PRECISION,
            center_longitude       DOUBLE PRECISION,
            radius_m               NUMERIC(8,2),
            severity               VARCHAR(10) NOT NULL DEFAULT 'MEDIUM',
            active_from            TIME,
            active_to              TIME,
            active_weekdays        SMALLINT[],
            allowed_user_ids       UUID[] NOT NULL DEFAULT '{{}}',
            allowed_role_ids       SMALLINT[] NOT NULL DEFAULT '{{}}',
            allowed_vehicle_plates TEXT[] NOT NULL DEFAULT '{{}}',
            detection_threshold    NUMERIC(4,3),
            alert_policy           VARCHAR(10) NOT NULL DEFAULT 'ALERT',
            is_active              BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_by_user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dsz_name      UNIQUE (tenant_id, site_id, name),
            CONSTRAINT ck_dsz_type      CHECK (zone_type    IN ({ZONE_TYPES})),
            CONSTRAINT ck_dsz_shape     CHECK (shape        IN ({ZONE_SHAPES})),
            CONSTRAINT ck_dsz_severity  CHECK (severity     IN ({RISK_LEVELS})),
            CONSTRAINT ck_dsz_policy    CHECK (alert_policy IN ({ALERT_POLICIES})),
            CONSTRAINT ck_dsz_threshold CHECK (detection_threshold IS NULL OR detection_threshold BETWEEN 0 AND 1),
            CONSTRAINT ck_dsz_geometry  CHECK (
                (shape = 'CIRCLE'  AND center_latitude IS NOT NULL AND center_longitude IS NOT NULL
                                   AND radius_m IS NOT NULL AND radius_m > 0)
             OR (shape <> 'CIRCLE' AND polygon IS NOT NULL AND jsonb_typeof(polygon) = 'array'
                                   AND jsonb_array_length(polygon) >= 3)
            ),
            CONSTRAINT ck_dsz_center_lat CHECK (center_latitude  IS NULL OR center_latitude  BETWEEN -90  AND 90),
            CONSTRAINT ck_dsz_center_lng CHECK (center_longitude IS NULL OR center_longitude BETWEEN -180 AND 180),
            CONSTRAINT ck_dsz_weekdays   CHECK (active_weekdays IS NULL OR active_weekdays <@ ARRAY[0,1,2,3,4,5,6]::smallint[])
        )
    """)
    op.execute("CREATE INDEX idx_dsz_site ON drone_security_zones (tenant_id, site_id) WHERE is_active")

    # A profile is a reusable bundle of AI rules ("Night High Security") that a
    # mission picks, so rules are not configured mission by mission.
    # verify_min_seconds is how long a detection must persist before it can be
    # escalated — one frame of a person is not an intrusion.
    op.execute("""
        CREATE TABLE drone_security_profiles (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name               VARCHAR(120) NOT NULL,
            description        TEXT,
            min_confidence     NUMERIC(4,3) NOT NULL DEFAULT 0.500,
            verify_min_seconds INTEGER NOT NULL DEFAULT 3,
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dsp_name       UNIQUE (tenant_id, name),
            CONSTRAINT ck_dsp_confidence CHECK (min_confidence BETWEEN 0 AND 1),
            CONSTRAINT ck_dsp_verify     CHECK (verify_min_seconds >= 0)
        )
    """)

    op.execute(f"""
        CREATE TABLE drone_profile_rules (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            profile_id          UUID NOT NULL REFERENCES drone_security_profiles(id) ON DELETE CASCADE,
            module_type         VARCHAR(20) NOT NULL,
            is_enabled          BOOLEAN NOT NULL DEFAULT TRUE,
            min_confidence      NUMERIC(4,3),
            base_severity       VARCHAR(10) NOT NULL DEFAULT 'MEDIUM',
            incident_risk_level VARCHAR(10) NOT NULL DEFAULT 'HIGH',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dpr_module     UNIQUE (profile_id, module_type),
            CONSTRAINT ck_dpr_module     CHECK (module_type IN ({AI_MODULES})),
            CONSTRAINT ck_dpr_confidence CHECK (min_confidence IS NULL OR min_confidence BETWEEN 0 AND 1),
            CONSTRAINT ck_dpr_severity   CHECK (base_severity       IN ({RISK_LEVELS})),
            CONSTRAINT ck_dpr_incident   CHECK (incident_risk_level IN ({RISK_LEVELS}))
        )
    """)

    # ── Routes ───────────────────────────────────────────────────────────────
    #
    # The base is the home point the drone launches from and returns to.
    # Waypoint sequence is unique per route but DEFERRABLE, so a whole route can
    # be reordered in one transaction without a temporary collision.
    op.execute("""
        CREATE TABLE drone_routes (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id            UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            name               VARCHAR(120) NOT NULL,
            description        TEXT,
            base_latitude      DOUBLE PRECISION NOT NULL,
            base_longitude     DOUBLE PRECISION NOT NULL,
            base_altitude_m    NUMERIC(7,2) NOT NULL DEFAULT 0,
            default_altitude_m NUMERIC(7,2) NOT NULL DEFAULT 40,
            default_speed_mps  NUMERIC(6,2) NOT NULL DEFAULT 5,
            return_to_base     BOOLEAN NOT NULL DEFAULT TRUE,
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_droute_name   UNIQUE (tenant_id, site_id, name),
            CONSTRAINT ck_droute_lat    CHECK (base_latitude  BETWEEN -90  AND 90),
            CONSTRAINT ck_droute_lng    CHECK (base_longitude BETWEEN -180 AND 180),
            CONSTRAINT ck_droute_alt    CHECK (default_altitude_m > 0),
            CONSTRAINT ck_droute_speed  CHECK (default_speed_mps  > 0)
        )
    """)
    op.execute("CREATE INDEX idx_droute_site ON drone_routes (tenant_id, site_id)")

    op.execute("""
        CREATE TABLE drone_waypoints (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            route_id            UUID NOT NULL REFERENCES drone_routes(id) ON DELETE CASCADE,
            sequence            INTEGER NOT NULL,
            name                VARCHAR(120),
            latitude            DOUBLE PRECISION NOT NULL,
            longitude           DOUBLE PRECISION NOT NULL,
            altitude_m          NUMERIC(7,2),
            speed_mps           NUMERIC(6,2),
            heading_deg         NUMERIC(5,2),
            hover_seconds       INTEGER NOT NULL DEFAULT 0,
            gimbal_pitch_deg    NUMERIC(5,2),
            gimbal_yaw_deg      NUMERIC(6,2),
            zoom                NUMERIC(5,2),
            observe_seconds     INTEGER NOT NULL DEFAULT 0,
            security_zone_id    UUID REFERENCES drone_security_zones(id) ON DELETE SET NULL,
            security_profile_id UUID REFERENCES drone_security_profiles(id) ON DELETE SET NULL,
            snapshot_required   BOOLEAN NOT NULL DEFAULT FALSE,
            notes               TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dwp_sequence UNIQUE (route_id, sequence) DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT ck_dwp_sequence CHECK (sequence >= 1),
            CONSTRAINT ck_dwp_lat      CHECK (latitude  BETWEEN -90  AND 90),
            CONSTRAINT ck_dwp_lng      CHECK (longitude BETWEEN -180 AND 180),
            CONSTRAINT ck_dwp_alt      CHECK (altitude_m  IS NULL OR altitude_m  > 0),
            CONSTRAINT ck_dwp_speed    CHECK (speed_mps   IS NULL OR speed_mps   > 0),
            CONSTRAINT ck_dwp_heading  CHECK (heading_deg IS NULL OR heading_deg >= 0 AND heading_deg < 360),
            CONSTRAINT ck_dwp_pitch    CHECK (gimbal_pitch_deg IS NULL OR gimbal_pitch_deg BETWEEN -90 AND 30),
            CONSTRAINT ck_dwp_zoom     CHECK (zoom IS NULL OR zoom >= 1),
            CONSTRAINT ck_dwp_hover    CHECK (hover_seconds   >= 0),
            CONSTRAINT ck_dwp_observe  CHECK (observe_seconds >= 0)
        )
    """)
    op.execute("CREATE INDEX idx_dwp_route ON drone_waypoints (route_id, sequence)")

    # ── Missions and schedules ───────────────────────────────────────────────
    op.execute(f"""
        CREATE TABLE drone_missions (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id              UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            drone_id             UUID REFERENCES drones(id) ON DELETE SET NULL,
            route_id             UUID REFERENCES drone_routes(id) ON DELETE SET NULL,
            security_profile_id  UUID REFERENCES drone_security_profiles(id) ON DELETE SET NULL,
            name                 VARCHAR(160) NOT NULL,
            description          TEXT,
            recording_sync_mode  VARCHAR(16),
            priority             SMALLINT NOT NULL DEFAULT 3,
            min_battery_pct      SMALLINT NOT NULL DEFAULT 30,
            max_duration_minutes INTEGER,
            enabled              BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dmission_name     UNIQUE (tenant_id, name),
            CONSTRAINT ck_dmission_sync     CHECK (recording_sync_mode IS NULL OR recording_sync_mode IN ({SYNC_MODES})),
            CONSTRAINT ck_dmission_priority CHECK (priority BETWEEN 1 AND 5),
            CONSTRAINT ck_dmission_battery  CHECK (min_battery_pct BETWEEN 0 AND 100),
            CONSTRAINT ck_dmission_duration CHECK (max_duration_minutes IS NULL OR max_duration_minutes > 0)
        )
    """)
    op.execute("CREATE INDEX idx_dmission_site  ON drone_missions (tenant_id, site_id)")
    op.execute("CREATE INDEX idx_dmission_drone ON drone_missions (drone_id)")

    # Mirrors virtual_patrol_schedules, plus the two types the drone brief adds.
    # Occurrences are computed in `timezone`, never the server clock.
    #   ONCE           start_date at launch_time
    #   DAILY          every day from start_date to end_date
    #   WEEKLY         the weekday of start_date, weekly
    #   SELECTED_DAYS  the listed weekdays (0 = Monday)
    #   SPECIFIC_DATE  the listed dates
    op.execute(f"""
        CREATE TABLE drone_schedules (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            mission_id         UUID NOT NULL REFERENCES drone_missions(id) ON DELETE CASCADE,
            schedule_type      VARCHAR(16) NOT NULL,
            timezone           VARCHAR(60) NOT NULL DEFAULT 'Asia/Singapore',
            start_date         DATE NOT NULL,
            end_date           DATE,
            launch_time        TIME NOT NULL,
            weekdays           SMALLINT[],
            specific_dates     DATE[],
            grace_minutes      INTEGER NOT NULL DEFAULT 15,
            enabled            BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_dsched_type     CHECK (schedule_type IN ({SCHEDULE_TYPES})),
            CONSTRAINT ck_dsched_range    CHECK (end_date IS NULL OR end_date >= start_date),
            CONSTRAINT ck_dsched_grace    CHECK (grace_minutes >= 0),
            CONSTRAINT ck_dsched_weekdays CHECK (weekdays IS NULL OR weekdays <@ ARRAY[0,1,2,3,4,5,6]::smallint[]),
            CONSTRAINT ck_dsched_selected CHECK (schedule_type <> 'SELECTED_DAYS'
                                                 OR (weekdays IS NOT NULL AND cardinality(weekdays) > 0)),
            CONSTRAINT ck_dsched_specific CHECK (schedule_type <> 'SPECIFIC_DATE'
                                                 OR (specific_dates IS NOT NULL AND cardinality(specific_dates) > 0))
        )
    """)
    op.execute("CREATE INDEX idx_dsched_due ON drone_schedules (enabled, start_date) WHERE enabled")

    # ── Patrol sessions ──────────────────────────────────────────────────────
    #
    # One row per execution. The names are copied so a deleted mission, route or
    # drone leaves readable history; config_snapshot holds the route, zones and
    # profile rules exactly as they were when the flight began.
    op.execute(f"""
        CREATE TABLE drone_patrol_sessions (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            session_number       VARCHAR(40) NOT NULL,
            mission_id           UUID REFERENCES drone_missions(id)          ON DELETE SET NULL,
            schedule_id          UUID REFERENCES drone_schedules(id)         ON DELETE SET NULL,
            site_id              UUID REFERENCES sites(id)                   ON DELETE SET NULL,
            drone_id             UUID REFERENCES drones(id)                  ON DELETE SET NULL,
            route_id             UUID REFERENCES drone_routes(id)            ON DELETE SET NULL,
            security_profile_id  UUID REFERENCES drone_security_profiles(id) ON DELETE SET NULL,
            edge_gateway_id      UUID REFERENCES drone_edge_gateways(id)     ON DELETE SET NULL,
            mission_name         VARCHAR(160),
            drone_name           VARCHAR(120),
            route_name           VARCHAR(120),
            profile_name         VARCHAR(120),
            config_snapshot      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            triggered_by         VARCHAR(16) NOT NULL DEFAULT 'SCHEDULE',
            triggered_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            scheduled_for        TIMESTAMPTZ,
            status               VARCHAR(16) NOT NULL DEFAULT 'SCHEDULED',
            preflight_result     JSONB,
            blocked_reason       TEXT,
            failure_reason       TEXT,
            abort_reason         TEXT,
            aborted_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            provider_mission_ref VARCHAR(120),
            last_waypoint_sequence INTEGER,
            distance_m           NUMERIC(10,2),
            event_count          INTEGER NOT NULL DEFAULT 0,
            incident_count       INTEGER NOT NULL DEFAULT 0,
            started_at           TIMESTAMPTZ,
            launched_at          TIMESTAMPTZ,
            ended_at             TIMESTAMPTZ,
            client_ref           UUID UNIQUE,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dps_number    UNIQUE (tenant_id, session_number),
            CONSTRAINT uq_dps_execution UNIQUE (schedule_id, scheduled_for),
            CONSTRAINT ck_dps_trigger   CHECK (triggered_by IN ({SESSION_TRIGGERS})),
            CONSTRAINT ck_dps_status    CHECK (status IN ({SESSION_STATUSES})),
            CONSTRAINT ck_dps_counts    CHECK (event_count >= 0 AND incident_count >= 0),
            CONSTRAINT ck_dps_ended     CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at)
        )
    """)
    op.execute(f"CREATE UNIQUE INDEX uq_dps_one_flight_per_drone ON drone_patrol_sessions (drone_id) "
               f"WHERE status IN ({IN_FLIGHT})")
    op.execute("CREATE INDEX idx_dps_tenant_status ON drone_patrol_sessions (tenant_id, status)")
    op.execute("CREATE INDEX idx_dps_drone ON drone_patrol_sessions (drone_id, started_at DESC)")
    op.execute("CREATE INDEX idx_dps_site ON drone_patrol_sessions (site_id, scheduled_for DESC)")
    op.execute("CREATE INDEX idx_dps_mission ON drone_patrol_sessions (mission_id, created_at DESC)")

    op.execute(f"""
        CREATE TABLE drone_session_waypoints (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            session_id        UUID NOT NULL REFERENCES drone_patrol_sessions(id) ON DELETE CASCADE,
            sequence          INTEGER NOT NULL,
            name              VARCHAR(120),
            latitude          DOUBLE PRECISION NOT NULL,
            longitude         DOUBLE PRECISION NOT NULL,
            altitude_m        NUMERIC(7,2),
            hover_seconds     INTEGER NOT NULL DEFAULT 0,
            observe_seconds   INTEGER NOT NULL DEFAULT 0,
            snapshot_required BOOLEAN NOT NULL DEFAULT FALSE,
            security_zone_id  UUID,
            status            VARCHAR(10) NOT NULL DEFAULT 'PENDING',
            reached_at        TIMESTAMPTZ,
            departed_at       TIMESTAMPTZ,
            notes             TEXT,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dsw_sequence UNIQUE (session_id, sequence),
            CONSTRAINT ck_dsw_status   CHECK (status IN ({WAYPOINT_RUN_STATUSES}))
        )
    """)

    # ── Telemetry ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE drone_telemetry (
            id                UUID NOT NULL DEFAULT gen_random_uuid(),
            tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            drone_id          UUID NOT NULL,
            session_id        UUID,
            recorded_at       TIMESTAMPTZ NOT NULL,
            latitude          DOUBLE PRECISION,
            longitude         DOUBLE PRECISION,
            altitude_m        NUMERIC(7,2),
            heading_deg       NUMERIC(5,2),
            speed_mps         NUMERIC(6,2),
            battery_pct       SMALLINT,
            gps_fix           VARCHAR(10),
            signal_quality    SMALLINT,
            mission_state     VARCHAR(24),
            waypoint_sequence INTEGER,
            gimbal_pitch_deg  NUMERIC(5,2),
            gimbal_yaw_deg    NUMERIC(6,2),
            payload           JSONB,
            received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, recorded_at),
            CONSTRAINT uq_dtel_sample UNIQUE (drone_id, recorded_at)
        ) PARTITION BY RANGE (recorded_at)
    """)
    op.execute("CREATE INDEX idx_dtel_session ON drone_telemetry (session_id, recorded_at)")
    op.execute("CREATE INDEX idx_dtel_tenant_time ON drone_telemetry (tenant_id, recorded_at DESC)")

    # ── Events ───────────────────────────────────────────────────────────────
    #
    # A drone event is a detection AFTER context: where the drone was, which
    # zone that is, what time, who is authorised, what the nearby cameras saw.
    #
    # ai_confidence and risk_score are different quantities and are kept apart
    # on purpose. The first is what the model reported about the pixels. The
    # second is this platform's assessment of how much the situation matters.
    # A 94%-confident person in a car park at noon is low risk.
    #
    # The drone's position is recorded as fact. estimated_* is where the object
    # probably was, projected from altitude and gimbal angle, and is labelled an
    # estimate by location_method.
    op.execute(f"""
        CREATE TABLE drone_events (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id               UUID REFERENCES sites(id)                 ON DELETE SET NULL,
            drone_id              UUID REFERENCES drones(id)                ON DELETE SET NULL,
            session_id            UUID REFERENCES drone_patrol_sessions(id) ON DELETE SET NULL,
            mission_id            UUID REFERENCES drone_missions(id)        ON DELETE SET NULL,
            security_zone_id      UUID REFERENCES drone_security_zones(id)  ON DELETE SET NULL,
            alert_id              UUID REFERENCES alerts(id)                ON DELETE SET NULL,
            incident_id           UUID REFERENCES incidents(id)             ON DELETE SET NULL,
            detection_id          UUID,
            module_type           VARCHAR(20) NOT NULL,
            waypoint_sequence     INTEGER,
            detected_at           TIMESTAMPTZ NOT NULL,
            drone_latitude        DOUBLE PRECISION,
            drone_longitude       DOUBLE PRECISION,
            drone_altitude_m      NUMERIC(7,2),
            estimated_latitude    DOUBLE PRECISION,
            estimated_longitude   DOUBLE PRECISION,
            location_method       VARCHAR(16) NOT NULL DEFAULT 'DRONE_POSITION',
            zone_type             VARCHAR(24),
            zone_name             VARCHAR(120),
            ai_confidence         NUMERIC(5,4),
            risk_score            SMALLINT,
            risk_level            VARCHAR(10) NOT NULL DEFAULT 'INFO',
            risk_factors          JSONB NOT NULL DEFAULT '[]'::jsonb,
            verification_state    VARCHAR(12) NOT NULL DEFAULT 'UNVERIFIED',
            observed_seconds      NUMERIC(7,2),
            status                VARCHAR(16) NOT NULL DEFAULT 'NEW',
            acknowledged_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            acknowledged_at       TIMESTAMPTZ,
            resolved_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            resolved_at           TIMESTAMPTZ,
            false_positive_reason TEXT,
            client_ref            UUID UNIQUE,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_devent_module     CHECK (module_type IN ({AI_MODULES})),
            CONSTRAINT ck_devent_location   CHECK (location_method IN ({LOCATION_METHODS})),
            CONSTRAINT ck_devent_zone_type  CHECK (zone_type IS NULL OR zone_type IN ({ZONE_TYPES})),
            CONSTRAINT ck_devent_confidence CHECK (ai_confidence IS NULL OR ai_confidence BETWEEN 0 AND 1),
            CONSTRAINT ck_devent_risk_score CHECK (risk_score IS NULL OR risk_score BETWEEN 0 AND 100),
            CONSTRAINT ck_devent_risk_level CHECK (risk_level IN ({RISK_LEVELS})),
            CONSTRAINT ck_devent_verify     CHECK (verification_state IN ({VERIFICATION_STATES})),
            CONSTRAINT ck_devent_status     CHECK (status IN ({EVENT_STATUSES})),
            CONSTRAINT ck_devent_estimate   CHECK (location_method <> 'PROJECTED'
                                                   OR (estimated_latitude IS NOT NULL AND estimated_longitude IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX idx_devent_tenant_time ON drone_events (tenant_id, detected_at DESC)")
    op.execute("CREATE INDEX idx_devent_site_time   ON drone_events (site_id, detected_at DESC)")
    op.execute("CREATE INDEX idx_devent_session     ON drone_events (session_id, detected_at)")
    op.execute("CREATE INDEX idx_devent_incident    ON drone_events (incident_id) WHERE incident_id IS NOT NULL")
    op.execute("CREATE INDEX idx_devent_open        ON drone_events (tenant_id, risk_level) "
               "WHERE status IN ('NEW','ACKNOWLEDGED','INVESTIGATING','ESCALATED')")

    # Media that belongs to a session (a waypoint snapshot) or to an event (its
    # snapshot and pre/event/post clips). Outside `evidence`, so outside the
    # retention purge — see the module docstring.
    op.execute(f"""
        CREATE TABLE drone_event_media (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            event_id           UUID REFERENCES drone_events(id)          ON DELETE CASCADE,
            session_id         UUID REFERENCES drone_patrol_sessions(id) ON DELETE SET NULL,
            waypoint_sequence  INTEGER,
            media_kind         VARCHAR(12) NOT NULL,
            storage_path       VARCHAR(512) NOT NULL,
            storage_location   VARCHAR(10) NOT NULL DEFAULT 'central',
            sync_state         VARCHAR(16) NOT NULL DEFAULT 'not_required',
            synced_at          TIMESTAMPTZ,
            checksum_sha256    VARCHAR(64),
            size_bytes         BIGINT,
            duration_seconds   NUMERIC(7,2),
            captured_at        TIMESTAMPTZ NOT NULL,
            telemetry_snapshot JSONB,
            client_ref         UUID UNIQUE,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_dmedia_kind     CHECK (media_kind       IN ({MEDIA_KINDS})),
            CONSTRAINT ck_dmedia_location CHECK (storage_location IN ({STORAGE_LOCATIONS})),
            CONSTRAINT ck_dmedia_sync     CHECK (sync_state       IN ({SYNC_STATES})),
            CONSTRAINT ck_dmedia_owner    CHECK (event_id IS NOT NULL OR session_id IS NOT NULL),
            CONSTRAINT ck_dmedia_size     CHECK (size_bytes IS NULL OR size_bytes >= 0)
        )
    """)
    op.execute("CREATE INDEX idx_dmedia_event   ON drone_event_media (event_id)")
    op.execute("CREATE INDEX idx_dmedia_session ON drone_event_media (session_id, captured_at)")
    op.execute("CREATE INDEX idx_dmedia_pending ON drone_event_media (sync_state) "
               "WHERE sync_state IN ('pending','failed')")

    # CCTV correlation: the fixed cameras relevant to a drone event. DISTANCE is
    # "within range of the point"; COVERAGE, once coverage geometry exists, is
    # "able to see it". The camera's name is copied so a deleted camera still
    # reads in an old investigation.
    op.execute(f"""
        CREATE TABLE drone_event_cameras (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            event_id             UUID NOT NULL REFERENCES drone_events(id) ON DELETE CASCADE,
            camera_id            UUID REFERENCES cameras(id) ON DELETE SET NULL,
            camera_name          VARCHAR(255),
            distance_m           NUMERIC(9,2),
            correlation_method   VARCHAR(10) NOT NULL DEFAULT 'DISTANCE',
            related_detection_id UUID,
            related_alert_id     UUID REFERENCES alerts(id) ON DELETE SET NULL,
            window_start         TIMESTAMPTZ,
            window_end           TIMESTAMPTZ,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_decam_pair   UNIQUE (event_id, camera_id),
            CONSTRAINT ck_decam_method CHECK (correlation_method IN ({CORRELATION_METHODS})),
            CONSTRAINT ck_decam_window CHECK (window_end IS NULL OR window_start IS NULL OR window_end >= window_start)
        )
    """)
    op.execute("CREATE INDEX idx_decam_camera ON drone_event_cameras (camera_id)")

    # ── Tenant isolation ─────────────────────────────────────────────────────
    #
    # The same policy text as all 269 existing policies, so there is one form to
    # audit. On the partitioned telemetry table the policy on the parent governs
    # every partition.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)
        op.execute(f"GRANT ALL ON {table} TO svc_app")

    # Monthly partitions, three made ahead; the scheduler's existing partition
    # maintenance keeps making them.
    op.execute("""
        SELECT public.create_parent(
            p_parent_table => 'public.drone_telemetry',
            p_control      => 'recorded_at',
            p_interval     => '1 month',
            p_premake      => 3
        )
    """)

    # ── Permissions ──────────────────────────────────────────────────────────
    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)

    # Admin (2) and Manager (8) run the module. Supervisor (3) plans and flies
    # missions for their sites but does not register or remove aircraft.
    # Operator (4) watches, and can start and ABORT a flight — the person at the
    # screen must always be able to stop one. Guard (5) sees drone events only
    # because an incident they are dispatched to may carry drone media. Viewer
    # (6) reads. Client (7) nothing, for now.
    #
    # Super Admin (1) gets nothing here, deliberately. The platform owner
    # licenses the module and watches its health; it is not a tenant security
    # operator, and a cross-tenant operational grant is the boundary CI caught
    # being crossed once before (certification:enforce).
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'drone:read'), (2, 'drone:create'), (2, 'drone:update'), (2, 'drone:delete'),
                  (2, 'drone:operate'), (2, 'drone:mission:create'), (2, 'drone:mission:update'),
                  (2, 'drone:mission:execute'), (2, 'drone:mission:abort'), (2, 'drone:event:read'),
                  (2, 'drone:event:acknowledge'), (2, 'drone:event:investigate'),
                  (2, 'drone:maintenance:read'), (2, 'drone:maintenance:manage'),
                  (2, 'drone:report:read'), (2, 'drone:report:export'),

                  (8, 'drone:read'), (8, 'drone:create'), (8, 'drone:update'), (8, 'drone:delete'),
                  (8, 'drone:operate'), (8, 'drone:mission:create'), (8, 'drone:mission:update'),
                  (8, 'drone:mission:execute'), (8, 'drone:mission:abort'), (8, 'drone:event:read'),
                  (8, 'drone:event:acknowledge'), (8, 'drone:event:investigate'),
                  (8, 'drone:maintenance:read'), (8, 'drone:maintenance:manage'),
                  (8, 'drone:report:read'), (8, 'drone:report:export'),

                  (3, 'drone:read'), (3, 'drone:operate'), (3, 'drone:mission:create'),
                  (3, 'drone:mission:update'), (3, 'drone:mission:execute'), (3, 'drone:mission:abort'),
                  (3, 'drone:event:read'), (3, 'drone:event:acknowledge'), (3, 'drone:event:investigate'),
                  (3, 'drone:maintenance:read'), (3, 'drone:report:read'), (3, 'drone:report:export'),

                  (4, 'drone:read'), (4, 'drone:operate'), (4, 'drone:mission:execute'),
                  (4, 'drone:mission:abort'), (4, 'drone:event:read'), (4, 'drone:event:acknowledge'),
                  (4, 'drone:event:investigate'), (4, 'drone:report:read'),

                  (5, 'drone:event:read'),

                  (6, 'drone:read'), (6, 'drone:event:read'), (6, 'drone:report:read')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)

    # ── Sellable module ──────────────────────────────────────────────────────
    #
    # Priced at zero for the same reason virtual_patrol was: the price is a
    # commercial decision, and zero reads as "listed, not yet priced" in the
    # platform console rather than as an amount somebody agreed. per_site, as
    # decided (D4). Listing it here does not license anyone — that is
    # drone_module_licenses.
    op.execute("""
        INSERT INTO billing_modules
               (code, name, description, billing_type, unit_price, sort_order)
        SELECT 'drone_patrol', 'Drone Patrol',
               'Scheduled autonomous drone security missions with AI detection, '
               'CCTV correlation, incidents and mission reports',
               'per_site', 0.00, 210
         WHERE NOT EXISTS (SELECT 1 FROM billing_modules WHERE code = 'drone_patrol')
    """)


def downgrade() -> None:
    op.execute("DELETE FROM billing_modules WHERE code = 'drone_patrol'")
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code LIKE 'drone:%')
    """)
    op.execute("DELETE FROM permissions WHERE code LIKE 'drone:%'")
    # Unregister from pg_partman before the table goes, or its maintenance run
    # keeps trying to make partitions for a table that no longer exists.
    op.execute("DELETE FROM public.part_config WHERE parent_table = 'public.drone_telemetry'")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
