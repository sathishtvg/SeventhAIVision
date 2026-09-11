"""Virtual Patrolling: schedules a person follows, and sessions nobody can edit.

A supervisor configures which cameras to inspect, in what order, with what
questions. At the scheduled time a session is created, a duty officer is walked
through each camera, captures a real snapshot, answers the questions, and the
whole thing becomes an immutable record.

EVERY TABLE IS PREFIXED virtual_patrol_. The specification asked for
`patrol_sessions` — which already exists, along with patrol_routes,
patrol_checkpoints and checkpoint_scans, belonging to the PHYSICAL guard patrol
feature where a guard walks a route scanning QR checkpoints. Building the names
as specified would have collided with a live feature on day one.

CONFIGURATION IS COPIED INTO THE SESSION, NOT REFERENCED FROM IT. This is the
rule the whole design turns on. A supervisor who edits a schedule to cameras
1/4/5 while a patrol of 1/2/3 is running must not change what that officer is
being asked to inspect, and must not change what last month's report says was
inspected. So virtual_patrol_session_cameras and _session_questions carry their
own copies of the name, code, question text, type and options. Nothing at read
time joins back to the configuration tables.

SNAPSHOTS ARE DELIBERATELY NOT ROWS IN `evidence`. scheduler_main's
purge_expired_evidence() deletes from that table once a tenant's retention_days
elapses — file first, then row. A completed patrol's evidence has to survive
indefinitely, and a PDF whose images have been purged still renders: the missing
frame reads as a camera fault rather than a retention policy, and somebody
investigates a failure that never happened. The file lives under EVIDENCE_ROOT;
the reference lives here, where the purge does not walk.

IDEMPOTENCY IS A DATABASE CONSTRAINT, NOT AN IF STATEMENT. Two scheduler workers,
a restart mid-run, or a retry after a timeout must not produce two sessions for
one execution. UNIQUE (schedule_id, scheduled_for) makes the second attempt fail
loudly instead of quietly duplicating somebody's morning patrol.

Revision ID: 0116
Revises: 0115
"""
from alembic import op

revision = "0116"
down_revision = "0115"
branch_labels = None
depends_on = None

SCHEDULE_TYPES = "'ONCE','DAILY','WEEKLY'"
QUESTION_TYPES = "'YES_NO','PASS_FAIL','TEXT','NUMBER','SINGLE_CHOICE','MULTI_CHOICE'"
FAILURE_ACTIONS = "'NONE','CREATE_INCIDENT','RAISE_ALERT','NOTIFY_SUPERVISOR'"
SESSION_STATUSES = ("'SCHEDULED','STARTED','IN_PROGRESS','COMPLETED',"
                    "'PARTIALLY_COMPLETED','MISSED','CANCELLED','FAILED'")
CAMERA_STATUSES = ("'PENDING','IN_PROGRESS','COMPLETED','SKIPPED',"
                   "'SNAPSHOT_FAILED','CAMERA_UNAVAILABLE'")
EMAIL_FREQUENCIES = "'IMMEDIATE','DAILY','WEEKLY','MONTHLY'"
QUEUE_STATUSES = "'PENDING','PROCESSING','SENT','FAILED'"

TENANT_TABLES = (
    "virtual_patrol_schedules",
    "virtual_patrol_schedule_cameras",
    "virtual_patrol_questions",
    "virtual_patrol_email_recipients",
    "virtual_patrol_sessions",
    "virtual_patrol_session_cameras",
    "virtual_patrol_session_questions",
    "virtual_patrol_session_answers",
    "virtual_patrol_reports",
    "virtual_patrol_email_queue",
)

PERMISSIONS = [
    ("vpatrol:read", "View virtual patrol schedules, sessions and history", "virtual_patrol"),
    ("vpatrol:manage", "Create and modify virtual patrol schedules, cameras and questions", "virtual_patrol"),
    ("vpatrol:execute", "Carry out an assigned virtual patrol", "virtual_patrol"),
    ("vpatrol:report", "View and download virtual patrol reports", "virtual_patrol"),
    ("vpatrol:export", "Export virtual patrol data", "virtual_patrol"),
    ("vpatrol:email", "Configure virtual patrol email recipients and frequency", "virtual_patrol"),
]


def upgrade() -> None:
    # ── Configuration ────────────────────────────────────────────────────────
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS virtual_patrol_schedules (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id        UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            name           VARCHAR(150) NOT NULL,
            description    TEXT,
            -- Sites have no timezone column, so the schedule carries its own.
            -- Reading the server clock instead would shift every patrol the
            -- moment this stack runs outside SGT, and silently at DST.
            timezone       VARCHAR(64) NOT NULL DEFAULT 'Asia/Singapore',
            schedule_type  VARCHAR(10) NOT NULL,
            start_date     DATE NOT NULL,
            end_date       DATE,
            patrol_time    TIME NOT NULL,
            -- ISO weekdays 1=Mon..7=Sun, WEEKLY only.
            weekdays       SMALLINT[] NOT NULL DEFAULT '{{}}',
            grace_minutes  INTEGER NOT NULL DEFAULT 15 CHECK (grace_minutes >= 0),
            enabled        BOOLEAN NOT NULL DEFAULT TRUE,
            assigned_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            assigned_role_id SMALLINT REFERENCES roles(id) ON DELETE SET NULL,
            email_frequency  VARCHAR(10) NOT NULL DEFAULT 'IMMEDIATE',
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_vps_type      CHECK (schedule_type IN ({SCHEDULE_TYPES})),
            CONSTRAINT ck_vps_frequency CHECK (email_frequency IN ({EMAIL_FREQUENCIES})),
            CONSTRAINT ck_vps_dates     CHECK (end_date IS NULL OR end_date >= start_date),
            -- A weekly patrol with no weekday would never run, and would look
            -- enabled while doing nothing at all.
            --
            -- cardinality(), NOT array_length(). array_length('{}', 1) returns
            -- NULL rather than 0, a CHECK passes on NULL, and the constraint
            -- would have accepted exactly the row it exists to reject — while
            -- looking correct in the schema. cardinality() returns 0.
            CONSTRAINT ck_vps_weekdays  CHECK (
                schedule_type <> 'WEEKLY' OR cardinality(weekdays) >= 1)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_vps_tenant_site "
               "ON virtual_patrol_schedules (tenant_id, site_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_vps_due "
               "ON virtual_patrol_schedules (enabled, start_date) WHERE enabled")

    op.execute("""
        CREATE TABLE IF NOT EXISTS virtual_patrol_schedule_cameras (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            schedule_id  UUID NOT NULL REFERENCES virtual_patrol_schedules(id) ON DELETE CASCADE,
            camera_id    UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            sequence_no  INTEGER NOT NULL CHECK (sequence_no > 0),
            enabled      BOOLEAN NOT NULL DEFAULT TRUE,
            timeout_seconds INTEGER CHECK (timeout_seconds IS NULL OR timeout_seconds > 0),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_vpsc_camera UNIQUE (schedule_id, camera_id)
        )
    """)
    # Deferrable: a drag-and-drop reorder rewrites several rows in one
    # transaction and would trip a non-deferrable constraint halfway through,
    # even though the final state is valid.
    op.execute("""
        ALTER TABLE virtual_patrol_schedule_cameras
            ADD CONSTRAINT uq_vpsc_sequence UNIQUE (schedule_id, sequence_no)
            DEFERRABLE INITIALLY DEFERRED
    """)

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS virtual_patrol_questions (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            schedule_camera_id  UUID NOT NULL
                REFERENCES virtual_patrol_schedule_cameras(id) ON DELETE CASCADE,
            question_text       TEXT NOT NULL,
            question_type       VARCHAR(20) NOT NULL,
            is_required         BOOLEAN NOT NULL DEFAULT TRUE,
            sequence_no         INTEGER NOT NULL CHECK (sequence_no > 0),
            enabled             BOOLEAN NOT NULL DEFAULT TRUE,
            options             JSONB,
            failure_action      VARCHAR(20) NOT NULL DEFAULT 'NONE',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_vpq_type   CHECK (question_type IN ({QUESTION_TYPES})),
            CONSTRAINT ck_vpq_action CHECK (failure_action IN ({FAILURE_ACTIONS})),
            -- A choice question with no options cannot be answered, and the
            -- officer only discovers that standing in front of the camera.
            CONSTRAINT ck_vpq_options CHECK (
                question_type NOT IN ('SINGLE_CHOICE','MULTI_CHOICE')
                OR (options IS NOT NULL AND jsonb_array_length(options) >= 1))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_vpq_camera "
               "ON virtual_patrol_questions (schedule_camera_id, sequence_no)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS virtual_patrol_email_recipients (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            schedule_id UUID NOT NULL REFERENCES virtual_patrol_schedules(id) ON DELETE CASCADE,
            email       VARCHAR(255) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_vper UNIQUE (schedule_id, email)
        )
    """)

    # ── Execution: immutable once written ────────────────────────────────────
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS virtual_patrol_sessions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id         UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            -- The schedule may be deleted; the patrol that ran under it is a
            -- historical fact and must survive (section 41).
            schedule_id     UUID REFERENCES virtual_patrol_schedules(id) ON DELETE SET NULL,
            patrol_number   VARCHAR(40) NOT NULL,
            schedule_name   VARCHAR(150) NOT NULL,
            scheduled_for   TIMESTAMPTZ NOT NULL,
            officer_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            status          VARCHAR(24) NOT NULL DEFAULT 'SCHEDULED',
            started_at      TIMESTAMPTZ,
            completed_at    TIMESTAMPTZ,
            camera_count            INTEGER NOT NULL DEFAULT 0,
            completed_camera_count  INTEGER NOT NULL DEFAULT 0,
            question_count          INTEGER NOT NULL DEFAULT 0,
            answered_question_count INTEGER NOT NULL DEFAULT 0,
            notes           TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_vpsess_status CHECK (status IN ({SESSION_STATUSES})),
            -- Idempotency as a constraint rather than an if statement. Two
            -- workers, a restart mid-run or a retry after a timeout must not
            -- produce two sessions for one execution.
            CONSTRAINT uq_vpsess_execution UNIQUE (schedule_id, scheduled_for)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_vpsess_officer "
               "ON virtual_patrol_sessions (officer_user_id, status, scheduled_for DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_vpsess_tenant_time "
               "ON virtual_patrol_sessions (tenant_id, scheduled_for DESC)")
    op.execute("""
        COMMENT ON COLUMN virtual_patrol_sessions.schedule_name IS
            'Copied at session creation. The schedule may be renamed or deleted '
            'later; a report must still say what the patrol was called when it ran.'
    """)

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS virtual_patrol_session_cameras (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            session_id   UUID NOT NULL REFERENCES virtual_patrol_sessions(id) ON DELETE CASCADE,
            -- SET NULL, not CASCADE: deleting a camera must not delete the
            -- record of having inspected it (section 41).
            camera_id    UUID REFERENCES cameras(id) ON DELETE SET NULL,
            sequence_no  INTEGER NOT NULL,
            camera_name  VARCHAR(150) NOT NULL,
            camera_code  VARCHAR(80),
            status       VARCHAR(24) NOT NULL DEFAULT 'PENDING',
            started_at   TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            -- Path under EVIDENCE_ROOT. Deliberately NOT a row in `evidence`:
            -- purge_expired_evidence would delete it once retention elapsed,
            -- and the report would silently lose its image.
            snapshot_path      TEXT,
            snapshot_filename  VARCHAR(255),
            snapshot_taken_at  TIMESTAMPTZ,
            snapshot_checksum  VARCHAR(64),
            snapshot_bytes     BIGINT,
            snapshot_error     TEXT,
            camera_metadata    JSONB,
            officer_notes      TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_vpsc_status CHECK (status IN ({CAMERA_STATUSES})),
            CONSTRAINT uq_vpsessc_sequence UNIQUE (session_id, sequence_no)
        )
    """)

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS virtual_patrol_session_questions (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            session_camera_id  UUID NOT NULL
                REFERENCES virtual_patrol_session_cameras(id) ON DELETE CASCADE,
            source_question_id UUID REFERENCES virtual_patrol_questions(id) ON DELETE SET NULL,
            question_text      TEXT NOT NULL,
            question_type      VARCHAR(20) NOT NULL,
            is_required        BOOLEAN NOT NULL,
            sequence_no        INTEGER NOT NULL,
            options            JSONB,
            failure_action     VARCHAR(20) NOT NULL DEFAULT 'NONE',
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_vpsq_type   CHECK (question_type IN ({QUESTION_TYPES})),
            CONSTRAINT ck_vpsq_action CHECK (failure_action IN ({FAILURE_ACTIONS})),
            CONSTRAINT uq_vpsq_sequence UNIQUE (session_camera_id, sequence_no)
        )
    """)
    op.execute("""
        COMMENT ON TABLE virtual_patrol_session_questions IS
            'The questions as they were asked, copied at session creation. '
            'Editing the configured question afterwards must not change what a '
            'historical report says the officer was asked.'
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS virtual_patrol_session_answers (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            session_question_id UUID NOT NULL
                REFERENCES virtual_patrol_session_questions(id) ON DELETE CASCADE,
            answered_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            answer_text         TEXT,
            answer_json         JSONB,
            answered_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            is_exception        BOOLEAN NOT NULL DEFAULT FALSE,
            exception_reason    TEXT,
            incident_id         UUID REFERENCES incidents(id) ON DELETE SET NULL,
            CONSTRAINT uq_vpsa_question UNIQUE (session_question_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_vpsa_exception "
               "ON virtual_patrol_session_answers (tenant_id, is_exception) "
               "WHERE is_exception")

    op.execute("""
        CREATE TABLE IF NOT EXISTS virtual_patrol_reports (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            session_id   UUID NOT NULL REFERENCES virtual_patrol_sessions(id) ON DELETE CASCADE,
            report_format VARCHAR(10) NOT NULL CHECK (report_format IN ('PDF','XLSX')),
            storage_path TEXT NOT NULL,
            file_bytes   BIGINT,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_vpr_format UNIQUE (session_id, report_format)
        )
    """)

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS virtual_patrol_email_queue (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            schedule_id  UUID REFERENCES virtual_patrol_schedules(id) ON DELETE SET NULL,
            session_id   UUID REFERENCES virtual_patrol_sessions(id) ON DELETE CASCADE,
            frequency    VARCHAR(10) NOT NULL,
            recipients   TEXT NOT NULL,
            subject      TEXT NOT NULL,
            status       VARCHAR(12) NOT NULL DEFAULT 'PENDING',
            attempts     INTEGER NOT NULL DEFAULT 0,
            last_error   TEXT,
            scheduled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            sent_at      TIMESTAMPTZ,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_vpeq_status    CHECK (status IN ({QUEUE_STATUSES})),
            CONSTRAINT ck_vpeq_frequency CHECK (frequency IN ({EMAIL_FREQUENCIES}))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_vpeq_due "
               "ON virtual_patrol_email_queue (status, scheduled_at) "
               "WHERE status IN ('PENDING','FAILED')")

    # ── Tenant isolation ─────────────────────────────────────────────────────
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)
        op.execute(f"GRANT ALL ON {table} TO svc_app")

    # ── Permissions ──────────────────────────────────────────────────────────
    #
    # vpatrol:*, not patrol:* — the latter belongs to the physical guard patrol
    # feature, and reusing it would silently widen access to a different one.
    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)

    # Admin (2) and Manager (8) administer; Supervisor (3) runs and reviews;
    # Operator (4) and Guard (5) carry out an assigned patrol and nothing more —
    # a duty officer must not acquire schedule administration by being handed a
    # patrol (section 28).
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'vpatrol:read'), (2, 'vpatrol:manage'), (2, 'vpatrol:execute'),
                  (2, 'vpatrol:report'), (2, 'vpatrol:export'), (2, 'vpatrol:email'),
                  (8, 'vpatrol:read'), (8, 'vpatrol:manage'), (8, 'vpatrol:execute'),
                  (8, 'vpatrol:report'), (8, 'vpatrol:export'), (8, 'vpatrol:email'),
                  (3, 'vpatrol:read'), (3, 'vpatrol:execute'), (3, 'vpatrol:report'),
                  (4, 'vpatrol:read'), (4, 'vpatrol:execute'),
                  (5, 'vpatrol:read'), (5, 'vpatrol:execute')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)

    # ── Sellable module ──────────────────────────────────────────────────────
    #
    # billing_modules already carries a `patrol` module — that is the PHYSICAL
    # one. Without its own entry this feature could never be charged for.
    #
    # PRICED AT ZERO ON PURPOSE. unit_price is NOT NULL so a number has to go in,
    # and what that number should be is a commercial decision, not an
    # engineering one. Zero means "listed but not yet priced" — visible in the
    # platform console for the owner to set — whereas a plausible-looking figure
    # invented here would start billing customers an amount nobody agreed.
    # per_site because a patrol schedule belongs to a site.
    op.execute("""
        INSERT INTO billing_modules
               (code, name, description, billing_type, unit_price, sort_order)
        SELECT 'virtual_patrol', 'Virtual Patrolling',
               'Scheduled camera-by-camera inspections with snapshots, '
               'questionnaires and evidence reports',
               'per_site', 0.00, 200
         WHERE NOT EXISTS (SELECT 1 FROM billing_modules WHERE code = 'virtual_patrol')
    """)


def downgrade() -> None:
    op.execute("DELETE FROM billing_modules WHERE code = 'virtual_patrol'")
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code LIKE 'vpatrol:%')
    """)
    op.execute("DELETE FROM permissions WHERE code LIKE 'vpatrol:%'")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
