"""Drone patrol, phase 11: the patrol report, and who it is sent to.

Still additive and drone-only: three new tables, one column on a drone table and
one function. The PDF and workbook are built with the libraries the platform's
other reports use; nothing existing is altered.

THE REPORT IS A RECORD, NOT A CACHE. `drone_reports` holds one row per flight and
format — where the file is, how big, and its SHA-256 — so that months later the
question "was that flight reported, and is this the document?" has an answer.
The download endpoints render on demand and always will; this is the proof that
a report existed when the flight ended.

RECIPIENTS ARE SCOPED, AND SCOPE IS ONE THING. A recipient receives every
flight in the organisation, every flight at one site, or every flight of one
mission — never a site and a mission at once, which would be two answers to
"which flights?". Each row also says how often: immediately after each flight,
or a daily, weekly or monthly summary. The same address may appear more than
once to get, say, each flight now and a summary on Monday.

NOTHING IS SENT FROM A REQUEST OR FROM THE FLIGHT LOOP. A flight ending, or a
period closing, puts a row in `drone_report_email_queue`; the runner's report
job claims rows, builds the document at send time and retries failures with
backoff. Two unique indexes make queueing idempotent — one immediate email per
flight, one summary per scope and period — so a restart, a retry or a second
runner cannot send anything twice by queueing it twice.

`drone_patrol_sessions.report_queued_at` marks a finished flight whose report has
been stored and whose email, if anyone wants it, has been queued.

`drone_report_tenants()` tells the report job which tenants have work. It cannot
reuse `drone_runner_tenants()`: that lists tenants with a live licence or a
flight in the air, and last week's summary is still owed to a tenant whose
licence lapsed on Friday.

Revision ID: 0129
Revises: 0128
"""
from alembic import op

revision = "0129"
down_revision = "0128"
branch_labels = None
depends_on = None

FREQUENCIES = "'IMMEDIATE','DAILY','WEEKLY','MONTHLY'"
QUEUE_STATUSES = "'PENDING','PROCESSING','SENT','FAILED'"
NOBODY = "'00000000-0000-0000-0000-000000000000'::uuid"

TABLES = ("drone_reports", "drone_report_recipients", "drone_report_email_queue")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE drone_reports (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            session_id      UUID NOT NULL REFERENCES drone_patrol_sessions(id) ON DELETE CASCADE,
            report_format   VARCHAR(10) NOT NULL,
            storage_path    TEXT NOT NULL,
            file_bytes      BIGINT NOT NULL,
            checksum_sha256 VARCHAR(64) NOT NULL,
            generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_drep_format UNIQUE (session_id, report_format),
            CONSTRAINT ck_drep_format CHECK (report_format IN ('PDF','XLSX')),
            CONSTRAINT ck_drep_bytes  CHECK (file_bytes >= 0)
        )
    """)

    op.execute(f"""
        CREATE TABLE drone_report_recipients (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id            UUID REFERENCES sites(id) ON DELETE CASCADE,
            mission_id         UUID REFERENCES drone_missions(id) ON DELETE CASCADE,
            email              VARCHAR(255) NOT NULL,
            frequency          VARCHAR(10) NOT NULL DEFAULT 'IMMEDIATE',
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_drr_frequency CHECK (frequency IN ({FREQUENCIES})),
            CONSTRAINT ck_drr_scope     CHECK (site_id IS NULL OR mission_id IS NULL),
            CONSTRAINT ck_drr_email     CHECK (email = lower(email) AND position('@' IN email) > 1)
        )
    """)
    op.execute(f"""
        CREATE UNIQUE INDEX uq_drr_recipient ON drone_report_recipients
            (tenant_id, COALESCE(site_id, {NOBODY}), COALESCE(mission_id, {NOBODY}), email, frequency)
    """)
    op.execute("CREATE INDEX idx_drr_active ON drone_report_recipients (tenant_id, frequency) WHERE is_active")

    op.execute(f"""
        CREATE TABLE drone_report_email_queue (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            session_id   UUID REFERENCES drone_patrol_sessions(id) ON DELETE CASCADE,
            site_id      UUID REFERENCES sites(id) ON DELETE CASCADE,
            mission_id   UUID REFERENCES drone_missions(id) ON DELETE SET NULL,
            -- What a summary covers: 'tenant', 'site:<id>' or 'mission:<id>'. Kept
            -- as text, and never rewritten, so a mission deleted later (SET NULL
            -- above) cannot turn its summary into the organisation's.
            scope_key    VARCHAR(60),
            scope_label  VARCHAR(200),
            frequency    VARCHAR(10) NOT NULL,
            period_start DATE,
            period_end   DATE,
            timezone     VARCHAR(64),
            recipients   TEXT NOT NULL,
            subject      TEXT NOT NULL,
            status       VARCHAR(12) NOT NULL DEFAULT 'PENDING',
            attempts     INTEGER NOT NULL DEFAULT 0,
            last_error   TEXT,
            scheduled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- When a worker took the row. A row left PROCESSING by a worker that
            -- died mid-send is picked up again after a while instead of being
            -- neither sent nor failed for ever.
            claimed_at   TIMESTAMPTZ,
            sent_at      TIMESTAMPTZ,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_dreq_status    CHECK (status IN ({QUEUE_STATUSES})),
            CONSTRAINT ck_dreq_frequency CHECK (frequency IN ({FREQUENCIES})),
            CONSTRAINT ck_dreq_attempts  CHECK (attempts >= 0),
            -- An immediate row is about one flight; a summary is about a period.
            CONSTRAINT ck_dreq_shape     CHECK (
                (frequency = 'IMMEDIATE' AND session_id IS NOT NULL)
                OR (frequency <> 'IMMEDIATE' AND session_id IS NULL AND scope_key IS NOT NULL
                    AND period_start IS NOT NULL AND period_end IS NOT NULL AND period_end >= period_start))
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_dreq_immediate ON drone_report_email_queue (session_id) "
               "WHERE frequency = 'IMMEDIATE'")
    op.execute("CREATE UNIQUE INDEX uq_dreq_digest ON drone_report_email_queue "
               "(tenant_id, frequency, period_start, scope_key) WHERE frequency <> 'IMMEDIATE'")
    op.execute("CREATE INDEX idx_dreq_due ON drone_report_email_queue (status, scheduled_at) "
               "WHERE status IN ('PENDING','FAILED')")
    op.execute("CREATE INDEX idx_dreq_tenant_time ON drone_report_email_queue (tenant_id, created_at DESC)")

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)
        op.execute(f"GRANT ALL ON {table} TO svc_app")

    op.execute("ALTER TABLE drone_patrol_sessions ADD COLUMN report_queued_at TIMESTAMPTZ")
    op.execute("CREATE INDEX idx_dps_report_due ON drone_patrol_sessions (ended_at) "
               "WHERE ended_at IS NOT NULL AND report_queued_at IS NULL")

    # SECURITY DEFINER for the same reason as drone_runner_tenants(): the report
    # job runs as svc_app, which cannot see across tenants, and has to learn
    # which tenants to visit before it can scope itself to one. It returns ids
    # only. The seven-day bound keeps a first deployment from reporting years of
    # history: older flights still render on demand.
    op.execute("""
        CREATE OR REPLACE FUNCTION drone_report_tenants()
        RETURNS TABLE (tenant_id UUID)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT q.tenant_id FROM drone_report_email_queue q
             WHERE q.status IN ('PENDING','FAILED','PROCESSING')
            UNION
            SELECT s.tenant_id FROM drone_patrol_sessions s
             WHERE s.ended_at IS NOT NULL AND s.report_queued_at IS NULL
               AND s.ended_at > now() - interval '7 days'
            UNION
            SELECT r.tenant_id FROM drone_report_recipients r
              JOIN tenants t ON t.id = r.tenant_id AND t.is_active
             WHERE r.is_active AND r.frequency <> 'IMMEDIATE'
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION drone_report_tenants() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION drone_report_tenants() TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS drone_report_tenants()")
    op.execute("DROP INDEX IF EXISTS idx_dps_report_due")
    op.execute("ALTER TABLE drone_patrol_sessions DROP COLUMN IF EXISTS report_queued_at")
    op.execute("DROP TABLE IF EXISTS drone_report_email_queue CASCADE")
    op.execute("DROP TABLE IF EXISTS drone_report_recipients CASCADE")
    op.execute("DROP TABLE IF EXISTS drone_reports CASCADE")
