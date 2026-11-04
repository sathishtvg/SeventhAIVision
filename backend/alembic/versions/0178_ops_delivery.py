"""The daily briefing emailed when published, and operations reports emailed on a schedule.

Phase 4 of closing the feature reference's known limits (docs/plans/2026-11-04-ops-reports-and-briefing-email-
design.md). Only the organisation's own people receive anything, each what they could read on screen.

`briefing_recipient_lists` - who a published briefing is emailed to: people and roles, for one site's briefings or
(no site) for every briefing. One list for each.

`ops_report_schedules` - one of the nine operations reports, for a site or every site, daily, weekly or monthly at an
hour where the organisation is, to people and roles. Paused, changed, or taken away (kept, with who and when).

`ops_emails` - each email to each person: a briefing or a report for a period, queued, sent, failed with why, or left
out with why (no longer in use, not shown the site, not holding what the report is read under, no email address).
The document is made when it is sent and never stored here. Unique per briefing and person, and per schedule, period
and person, so nothing is queued twice.
"""
from alembic import op

revision = "0178"
down_revision = "0177"
branch_labels = None
depends_on = None

REPORTS = ("board-sites", "response", "device-health", "maintenance", "visitors", "access", "risk", "evidence",
           "investigations")
FREQUENCIES = ("DAILY", "WEEKLY", "MONTHLY")
STATUSES = ("PENDING", "PROCESSING", "SENT", "FAILED", "LEFT_OUT")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


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
        CREATE TABLE briefing_recipient_lists (
            id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- No site: every briefing of the organisation, whatever its site.
            site_id            UUID        REFERENCES sites(id) ON DELETE CASCADE,
            user_ids           UUID[]      NOT NULL DEFAULT '{}',
            role_ids           SMALLINT[]  NOT NULL DEFAULT '{}',
            updated_by_user_id UUID        REFERENCES users(id) ON DELETE SET NULL,
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_brieflist_site ON briefing_recipient_lists "
               "(tenant_id, COALESCE(site_id, '00000000-0000-0000-0000-000000000000'::uuid))")
    _secured("briefing_recipient_lists", "SELECT, INSERT, UPDATE")

    op.execute(f"""
        CREATE TABLE ops_report_schedules (
            id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            report_key         VARCHAR(40) NOT NULL CHECK (report_key IN ({_in(REPORTS)})),
            -- No site: every site the person receiving it may see.
            site_id            UUID        REFERENCES sites(id) ON DELETE CASCADE,
            frequency          VARCHAR(8)  NOT NULL CHECK (frequency IN ({_in(FREQUENCIES)})),
            hour               SMALLINT    NOT NULL CHECK (hour BETWEEN 0 AND 23),
            weekday            SMALLINT    CHECK (weekday BETWEEN 0 AND 6),
            month_day          SMALLINT    CHECK (month_day BETWEEN 1 AND 28),
            user_ids           UUID[]      NOT NULL DEFAULT '{{}}',
            role_ids           SMALLINT[]  NOT NULL DEFAULT '{{}}',
            is_active          BOOLEAN     NOT NULL DEFAULT TRUE,
            next_run_at        TIMESTAMPTZ NOT NULL,
            last_run_at        TIMESTAMPTZ,
            created_by_user_id UUID        REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by_user_id UUID        REFERENCES users(id) ON DELETE SET NULL,
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            removed_at         TIMESTAMPTZ,
            removed_by_user_id UUID        REFERENCES users(id) ON DELETE SET NULL,
            CONSTRAINT ck_opsched_when CHECK (
                (frequency = 'DAILY' AND weekday IS NULL AND month_day IS NULL)
             OR (frequency = 'WEEKLY' AND weekday IS NOT NULL AND month_day IS NULL)
             OR (frequency = 'MONTHLY' AND month_day IS NOT NULL AND weekday IS NULL))
        )
    """)
    op.execute("CREATE INDEX idx_opsched_due ON ops_report_schedules (next_run_at) "
               "WHERE is_active AND removed_at IS NULL")
    _secured("ops_report_schedules", "SELECT, INSERT, UPDATE")

    op.execute(f"""
        CREATE TABLE ops_emails (
            id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            kind          VARCHAR(8)  NOT NULL CHECK (kind IN ('BRIEFING', 'REPORT')),
            briefing_id   UUID        REFERENCES daily_briefings(id) ON DELETE CASCADE,
            schedule_id   UUID        REFERENCES ops_report_schedules(id) ON DELETE CASCADE,
            user_id       UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            email         VARCHAR(320),
            period_start  TIMESTAMPTZ,
            period_end    TIMESTAMPTZ,
            revised       BOOLEAN     NOT NULL DEFAULT FALSE,
            subject       TEXT        NOT NULL,
            status        VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ({_in(STATUSES)})),
            attempts      SMALLINT    NOT NULL DEFAULT 0,
            claimed_at    TIMESTAMPTZ,
            scheduled_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            sent_at       TIMESTAMPTZ,
            last_error    TEXT,
            rows_sent     INTEGER,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_opsmail_what CHECK ((kind = 'BRIEFING') = (briefing_id IS NOT NULL)
                                              AND (kind = 'REPORT') = (schedule_id IS NOT NULL)),
            CONSTRAINT ck_opsmail_why CHECK (status <> 'LEFT_OUT' OR last_error IS NOT NULL)
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_opsmail_briefing ON ops_emails (briefing_id, user_id) "
               "WHERE kind = 'BRIEFING'")
    op.execute("CREATE UNIQUE INDEX uq_opsmail_report ON ops_emails (schedule_id, period_start, user_id) "
               "WHERE kind = 'REPORT'")
    op.execute("CREATE INDEX idx_opsmail_due ON ops_emails (scheduled_at) "
               "WHERE status IN ('PENDING', 'FAILED', 'PROCESSING')")
    _secured("ops_emails", "SELECT, INSERT, UPDATE")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops_emails")
    op.execute("DROP TABLE IF EXISTS ops_report_schedules")
    op.execute("DROP TABLE IF EXISTS briefing_recipient_lists")
