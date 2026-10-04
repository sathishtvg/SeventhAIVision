"""Drone patrol, phase 13: what the security review found in the schema.

Still additive and drone-only: a policy on the drone module's own partitions,
two indexes on drone tables and one function. Nothing existing is altered.

THE TELEMETRY PARTITIONS WERE READABLE BY NAME. `drone_telemetry` has had row
level security since it was created, but Postgres does not pass that to a
table's partitions and a partition is an ordinary table: a session scoped to one
tenant that selected from `drone_telemetry_p20261001` saw every tenant's flight
track. Nothing in the application names a partition, so nothing leaked through
the API — what was missing was the guarantee. The platform already has the cure
(`apply_partition_rls()`, migration 0083), run daily by the scheduler after it
creates the next month's partitions; the drone migrations simply never called it
for the partitions they created themselves, which left them open until the
scheduler's next daily run. On a machine where that run does not happen — a
development box that is switched off at night — they stayed open. Calling it
here closes that window at the moment the schema is built.

TWO INDEXES, EACH FROM A MEASUREMENT. The phone opens a drone event from the
alert that announced it, by `alert_id` (Phase 10); that lookup had no index and
read the tenant's events to find one. And the flight list, the period reports
and the analytics all take a tenant's flights by when they were created, newest
first, with nothing to walk in that order: against a year of a twenty-drone
fleet (87,600 flights) the first page of the flight list took 116 ms without the
index and 16 ms with it.

`platform_drone_health()` answers the platform owner's question — is the drone
service keeping up for everybody? — in four counts. SECURITY DEFINER for the
reason `platform_recording_health()` is (0108): the console runs outside any
tenant, so counting the tables directly returns zero and reports a healthy idle
service on an installation with flights in the air. It returns counts only.

Revision ID: 0130
Revises: 0129
"""
from alembic import op

revision = "0130"
down_revision = "0129"
branch_labels = None
depends_on = None

LIVE = ("'SCHEDULED','PRECHECK','READY','LAUNCHING','ACTIVE','PAUSED',"
        "'EVENT_DETECTED','RETURNING'")

#: Matches vpatrol_email.MAX_ATTEMPTS: an email that has used its attempts is a
#: failed delivery the organisation can see and retry, not work the runner owes.
MAX_ATTEMPTS = 5


def upgrade() -> None:
    op.execute("SELECT public.apply_partition_rls()")

    op.execute("CREATE INDEX IF NOT EXISTS idx_devent_alert ON drone_events (alert_id) "
               "WHERE alert_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_dps_tenant_created ON drone_patrol_sessions "
               "(tenant_id, created_at DESC)")

    # A command the central runner owes is one for a flight no site gateway has
    # claimed; a claimed flight's commands wait for that gateway, and a gateway
    # being offline is the site's outage, already alerted to its own operators.
    op.execute(f"""
        CREATE OR REPLACE FUNCTION platform_drone_health()
        RETURNS TABLE (licensed BIGINT, live BIGINT, commands_overdue BIGINT, emails_overdue BIGINT)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT
                (SELECT count(*) FROM drone_module_licenses l
                   JOIN tenants t ON t.id = l.tenant_id AND t.is_active
                  WHERE l.is_enabled AND (l.expires_at IS NULL OR l.expires_at > now())),
                (SELECT count(*) FROM drone_patrol_sessions s WHERE s.status IN ({LIVE})),
                (SELECT count(*) FROM drone_session_commands c
                   JOIN drone_patrol_sessions s ON s.id = c.session_id
                  WHERE c.status = 'PENDING'
                    AND c.requested_at < now() - interval '60 seconds'
                    AND NOT (s.edge_claimed_at IS NOT NULL AND s.edge_gateway_id IS NOT NULL)),
                (SELECT count(*) FROM drone_report_email_queue q
                  WHERE q.status IN ('PENDING','FAILED') AND q.attempts < {MAX_ATTEMPTS}
                    AND q.scheduled_at < now() - interval '15 minutes')
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION platform_drone_health() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION platform_drone_health() TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS platform_drone_health()")
    op.execute("DROP INDEX IF EXISTS idx_dps_tenant_created")
    op.execute("DROP INDEX IF EXISTS idx_devent_alert")
    # The partitions keep their policy. Taking tenant isolation off is not a
    # rollback step, and the scheduler would put it back within a day.
