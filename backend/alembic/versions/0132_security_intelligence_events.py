"""AI security intelligence, phase 2: one shape for every security event.

Additive: two new tables, one function, seven permissions. No existing table,
policy or permission is altered (AI_SECURITY_INTELLIGENCE_GAP_ANALYSIS.md §7, §12).

WHY A TABLE AND NOT A VIEW. The platform raises security events from 21 places
in as many shapes, and `alerts` is the only place they meet. A view over the
sources would give one shape to read, but the next phases have to say things
about an event that no source table has anywhere to hold — which situation it
belongs to, and why — and they must keep saying it after the source row has been
acknowledged, dismissed or aged out. So each source record gets one row here,
found again by `(tenant_id, source_table, source_id)`: reading a source twice
inserts nothing.

WHAT A ROW IS NOT. It is not a copy of the source. It carries the small core
every later stage needs — where, when, what kind, who or what if the source
knows, the source's own confidence, unaltered — and references for the rest.
Snapshots and clips stay in `evidence` and `recordings`; the alert's status
stays on the alert. `detection_id` is a reference without a foreign key because
`detections` is partitioned and has no key on `id` alone, as on `drone_events`.

`subject_ref` IS AN IDENTIFIER, NEVER A NAME: a number plate, or the id of a
watchlist entry. A face that matched nobody has none.

`security_ingest_cursors` records, per tenant and source, where reading starts.
It is also what stops a tenant's whole history arriving at once on the day the
feature is switched on (services/intel_events.py).

`security_intel_tenants()` tells the runner which tenants have switched the
feature on. SECURITY DEFINER for the reason `drone_report_tenants()` is: the
runner connects as the application role outside any tenant, where
`tenant_settings` shows it nothing. It returns tenant ids and nothing else.

SUPER ADMIN (1) AND CLIENT (7) GET NO PERMISSION HERE. The platform owner is not
a customer's security operator; an assessment describes a site's weaknesses and
is not for the site's own customer to read.

Revision ID: 0132
Revises: 0131
"""
from alembic import op

revision = "0132"
down_revision = "0131"
branch_labels = None
depends_on = None

SOURCE_TYPES = ("'CCTV_AI','DRONE_PATROL','VIRTUAL_PATROL','LPR','FACE_RECOGNITION',"
                "'ACCESS_CONTROL','ALARM','GUARD','SENSOR','SYSTEM','OTHER'")
SEVERITIES = "'info','low','medium','high','critical'"
SUBJECT_KINDS = "'PERSON','VEHICLE','NONE'"
SUBJECT_VERDICTS = "'ALLOW','BLOCK','UNKNOWN'"
# NEW: read from its source. LINKED: belongs to a situation (phase 4).
EVENT_STATUSES = "'NEW','LINKED'"

TABLES = ("security_events", "security_ingest_cursors")

PERMISSIONS = [
    ("intel:read", "View AI security events, situations and assessments", "security_intelligence"),
    ("intel:recommendation:read", "View AI recommendations and the reasons for them", "security_intelligence"),
    ("intel:decide", "Record a security decision on an AI-assessed situation", "security_intelligence"),
    ("intel:override", "Decide against an AI recommendation, with a reason", "security_intelligence"),
    ("intel:approve", "Approve a decision that the decision policy sends to the command centre",
     "security_intelligence"),
    ("intel:manage", "Set the decision policy, site security profiles and risk weights", "security_intelligence"),
    ("intel:feedback:export", "Export the recommendation, decision and outcome dataset", "security_intelligence"),
]


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE security_events (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id          UUID REFERENCES sites(id)     ON DELETE SET NULL,
            source_type      VARCHAR(24) NOT NULL,
            source_table     VARCHAR(64) NOT NULL,
            source_id        UUID NOT NULL,
            event_type       VARCHAR(80) NOT NULL,
            occurred_at      TIMESTAMPTZ NOT NULL,
            camera_id        UUID REFERENCES cameras(id)   ON DELETE SET NULL,
            drone_id         UUID REFERENCES drones(id)    ON DELETE SET NULL,
            alert_id         UUID REFERENCES alerts(id)    ON DELETE SET NULL,
            incident_id      UUID REFERENCES incidents(id) ON DELETE SET NULL,
            detection_id     UUID,
            subject_kind     VARCHAR(12) NOT NULL DEFAULT 'NONE',
            subject_ref      VARCHAR(120),
            subject_verdict  VARCHAR(12),
            confidence       NUMERIC(5,4),
            severity         VARCHAR(10) NOT NULL,
            title            VARCHAR(255) NOT NULL,
            latitude         DOUBLE PRECISION,
            longitude        DOUBLE PRECISION,
            location_label   VARCHAR(255),
            attributes       JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            status           VARCHAR(12) NOT NULL DEFAULT 'NEW',
            ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_secevent_source      UNIQUE (tenant_id, source_table, source_id),
            CONSTRAINT ck_secevent_source_type CHECK (source_type IN ({SOURCE_TYPES})),
            CONSTRAINT ck_secevent_severity    CHECK (severity IN ({SEVERITIES})),
            CONSTRAINT ck_secevent_subject     CHECK (subject_kind IN ({SUBJECT_KINDS})),
            CONSTRAINT ck_secevent_verdict     CHECK (subject_verdict IS NULL
                                                      OR subject_verdict IN ({SUBJECT_VERDICTS})),
            CONSTRAINT ck_secevent_confidence  CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
            CONSTRAINT ck_secevent_status      CHECK (status IN ({EVENT_STATUSES}))
        )
    """)
    # The command centre lists a tenant's events newest first, usually for one
    # site; correlation (phase 4) asks what else happened at a camera, or to a
    # plate, in a window.
    op.execute("CREATE INDEX idx_secevent_tenant_time ON security_events (tenant_id, occurred_at DESC)")
    op.execute("CREATE INDEX idx_secevent_site_time ON security_events (tenant_id, site_id, occurred_at DESC)")
    op.execute("CREATE INDEX idx_secevent_camera_time ON security_events (camera_id, occurred_at DESC) "
               "WHERE camera_id IS NOT NULL")
    op.execute("CREATE INDEX idx_secevent_subject ON security_events (tenant_id, subject_kind, subject_ref, "
               "occurred_at DESC) WHERE subject_ref IS NOT NULL")
    op.execute("CREATE INDEX idx_secevent_alert ON security_events (alert_id) WHERE alert_id IS NOT NULL")

    op.execute("""
        CREATE TABLE security_ingest_cursors (
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            source      VARCHAR(40) NOT NULL,
            read_from   TIMESTAMPTZ NOT NULL,
            last_run_at TIMESTAMPTZ,
            last_count  INTEGER NOT NULL DEFAULT 0,
            total_count BIGINT NOT NULL DEFAULT 0,
            last_error  TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, source)
        )
    """)

    # The same policy text as every other tenant table, so there is one form to audit.
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)
        op.execute(f"GRANT ALL ON {table} TO svc_app")

    # Which tenants the runner works for. A tenant that has never set the switch
    # has no row and is not listed: off is the default.
    op.execute("""
        CREATE OR REPLACE FUNCTION security_intel_tenants()
        RETURNS TABLE (tenant_id UUID)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT s.tenant_id
              FROM tenant_settings s
              JOIN tenants t ON t.id = s.tenant_id AND t.is_active
             WHERE s.setting_key = 'intel.enabled'
               AND s.setting_value = 'true'::jsonb
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION security_intel_tenants() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION security_intel_tenants() TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)

    # Admin (2) and Manager (8) run it. Supervisor (3) decides, overrides and
    # approves but does not set policy. Operator (4) — the person at the screen —
    # decides and may override. Guard (5) may see and decide, and the decision
    # policy (phase 7) says at which sites and up to which risk; until an
    # administrator sets one, the policy lets no guard decide. Viewer (6) reads.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'intel:read'), (2, 'intel:recommendation:read'), (2, 'intel:decide'),
                  (2, 'intel:override'), (2, 'intel:approve'), (2, 'intel:manage'),
                  (2, 'intel:feedback:export'),

                  (8, 'intel:read'), (8, 'intel:recommendation:read'), (8, 'intel:decide'),
                  (8, 'intel:override'), (8, 'intel:approve'), (8, 'intel:manage'),
                  (8, 'intel:feedback:export'),

                  (3, 'intel:read'), (3, 'intel:recommendation:read'), (3, 'intel:decide'),
                  (3, 'intel:override'), (3, 'intel:approve'),

                  (4, 'intel:read'), (4, 'intel:recommendation:read'), (4, 'intel:decide'),
                  (4, 'intel:override'),

                  (5, 'intel:read'), (5, 'intel:recommendation:read'), (5, 'intel:decide'),

                  (6, 'intel:read')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code LIKE 'intel:%')
    """)
    op.execute("DELETE FROM permissions WHERE code LIKE 'intel:%'")
    op.execute("DROP FUNCTION IF EXISTS security_intel_tenants()")
    op.execute("DROP TABLE IF EXISTS security_ingest_cursors CASCADE")
    op.execute("DROP TABLE IF EXISTS security_events CASCADE")
