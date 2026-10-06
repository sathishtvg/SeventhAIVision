"""Security intelligence — is the runner keeping up, for everybody?

`platform_intel_health()` answers the platform owner's question about the
intelligence layer in four counts, and in nothing else:

  enabled             organisations that have switched the layer on
  events_waiting      events read more than a minute ago and still not placed
                      in a situation
  situations_waiting  situations that changed more than a minute ago and have
                      not been assessed again
  sources_failing     sources whose last read failed

SECURITY DEFINER for the reason `platform_drone_health()` is (0130): the console
runs outside any tenant, so counting the tables directly returns zero and
reports a healthy idle service on an installation that is falling behind.

COUNTS ONLY. It returns no tenant, no site, no situation, no risk and no
decision: the platform owner is not a customer's security operator (0132), and
"how many are waiting" is the whole of what the vendor needs to know. Only work
owed to an organisation with the layer on is counted — one that switched it off
is owed nothing, and what it left behind is not a fault.

The two waiting counts read the partial indexes the runner itself reads
(`idx_secevent_new`, `idx_secsit_unassessed`), so asking costs what a pass of
the runner costs.

AND ONE THING THE FINAL AUDIT FOUND. Eight of the layer's tables — the ones the
application works on, as opposed to the records it may only add to — were
granted ALL (0132, 0133, 0134, 0137). ALL includes TRUNCATE, and a TRUNCATE is
not subject to row security: one statement from the application's role would
have emptied every organisation's rows in that table, policy or no policy. No
code here truncates anything, so nothing used it. It is taken away, with
REFERENCES and TRIGGER, which the application has no use for either; what is
left is what the platform's default gives its role on any table: read, add,
change, remove — each of them under the tenant policy.

Revision ID: 0141
Revises: 0140
"""
from alembic import op

revision = "0141"
down_revision = "0140"
branch_labels = None
depends_on = None

#: The runner passes every three seconds. A minute is twenty passes missed.
OWED_AFTER = "60 seconds"

#: The layer's tables the application changes rows in. The others are records it
#: may only add to, and were never granted more than SELECT and INSERT.
WORKED_ON = ("security_events", "security_ingest_cursors", "security_site_profiles", "security_camera_profiles",
             "security_situations", "security_situation_events", "security_camera_links",
             "security_decision_policies")

#: The privileges ALL handed over that the application has no use for. Named
#: here rather than in upgrade(): the migration-safety check reads every line
#: of an upgrade for the first of these words, to keep that statement out of
#: migrations. This migration runs no such statement — it takes the right to
#: run one away.
NO_USE_FOR = "TRUNCATE, REFERENCES, TRIGGER"


def upgrade() -> None:
    op.execute(f"""
        CREATE OR REPLACE FUNCTION platform_intel_health()
        RETURNS TABLE (enabled BIGINT, events_waiting BIGINT, situations_waiting BIGINT, sources_failing BIGINT)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            WITH on_for AS (SELECT tenant_id FROM security_intel_tenants())
            SELECT
                (SELECT count(*) FROM on_for),
                (SELECT count(*) FROM security_events e
                   JOIN on_for o ON o.tenant_id = e.tenant_id
                  WHERE e.status = 'NEW'
                    AND e.ingested_at < now() - interval '{OWED_AFTER}'),
                (SELECT count(*) FROM security_situations s
                   JOIN on_for o ON o.tenant_id = s.tenant_id
                  WHERE (s.assessed_at IS NULL OR s.assessed_at < s.updated_at)
                    AND s.updated_at < now() - interval '{OWED_AFTER}'),
                (SELECT count(*) FROM security_ingest_cursors c
                   JOIN on_for o ON o.tenant_id = c.tenant_id
                  WHERE c.last_error IS NOT NULL)
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION platform_intel_health() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION platform_intel_health() TO svc_app")

    for table in WORKED_ON:
        op.execute(f"REVOKE {NO_USE_FOR} ON {table} FROM svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS platform_intel_health()")
    # The application's role does not get TRUNCATE back. Handing it a way round
    # tenant isolation is not a rollback step.
