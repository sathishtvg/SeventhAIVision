"""Row-level security on partitions, not just their parents.

WHY THIS EXISTS
    Tenant isolation in this system rests on one claim: RLS is applied by
    Postgres itself, so a query cannot accidentally skip tenant scoping even
    if the application forgets. For thirteen partitioned tables that claim
    was not true.

    ENABLE/FORCE ROW LEVEL SECURITY and the tenant_isolation policy were set
    on each PARENT table only. Postgres does NOT cascade either to the
    partitions, and a partition is an ordinary table you can query by name.
    Measured on this database before the fix, in a single session scoped to
    one tenant:

        SELECT ... FROM detections            -> 1 tenant   (policy applied)
        SELECT ... FROM detections_p20260801  -> 2 tenants  (policy skipped)

    No application code queries a partition by name today, so nothing was
    leaking through the API. What was broken is the guarantee: the moment
    anyone writes a maintenance, analytics, archival or performance-tuned
    query against a partition — exactly the sort of query partitions invite —
    tenant isolation silently disappears with no error to notice.

WHY A FUNCTION AND NOT A ONE-OFF BACKFILL
    pg_partman creates new monthly partitions on a schedule (premake = 3).
    Those arrive with no RLS, so a plain backfill would fix today and rot by
    next month. The obvious lever, pg_partman's template table, does NOT work
    here — verified empirically: a template with RLS enabled produced six new
    partitions all with relrowsecurity = false and zero policies. Native
    partitioning simply doesn't inherit RLS, and partman's template only
    covers the properties it explicitly copies.

    So the mechanism is a function that finds any partition missing the
    protection its parent has, and applies it. It is idempotent, so it can be
    run whenever: once here to backfill, and daily from the scheduler right
    after run_maintenance_proc() creates the next month's partitions. Premake
    is 3 months, so a partition exists long before it holds a row — a daily
    sweep closes the window with months to spare.

    SECURITY DEFINER because the partitions are owned by the bootstrap
    superuser while the scheduler connects as the restricted app role.
    search_path is pinned to defeat the usual SECURITY DEFINER hijack.
"""
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


# Applies to any partition whose parent has RLS on and which has a tenant_id
# column. Deliberately re-states the project's canonical policy rather than
# copying the parent's definition — parsing pg_policies text to rebuild a
# policy is fragile, and every partitioned table here uses this exact rule.
_CREATE_FN = """
CREATE OR REPLACE FUNCTION public.apply_partition_rls()
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    r        record;
    n_fixed  integer := 0;
BEGIN
    FOR r IN
        SELECT child.oid::regclass AS child_rel, child.relname AS child_name
        FROM pg_class child
        JOIN pg_inherits inh   ON inh.inhrelid = child.oid
        JOIN pg_class parent   ON parent.oid = inh.inhparent
        JOIN pg_namespace ns   ON ns.oid = child.relnamespace
        WHERE ns.nspname = 'public'
          AND parent.relrowsecurity                 -- parent is protected
          AND NOT child.relrowsecurity              -- child is not
          AND EXISTS (
              SELECT 1 FROM pg_attribute a
              WHERE a.attrelid = child.oid
                AND a.attname = 'tenant_id'
                AND NOT a.attisdropped
          )
    LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', r.child_rel);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', r.child_rel);

        -- Guard the policy separately: a partition could in principle already
        -- carry a policy while having RLS switched off.
        IF NOT EXISTS (
            SELECT 1 FROM pg_policy p
            WHERE p.polrelid = r.child_rel
              AND p.polname = 'tenant_isolation_' || r.child_name
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON %s '
                'USING (tenant_id = current_setting(''app.current_tenant'', true)::uuid) '
                'WITH CHECK (tenant_id = current_setting(''app.current_tenant'', true)::uuid)',
                'tenant_isolation_' || r.child_name, r.child_rel
            );
        END IF;

        n_fixed := n_fixed + 1;
    END LOOP;

    RETURN n_fixed;
END;
$$;
"""


def upgrade() -> None:
    op.execute(_CREATE_FN)
    # The scheduler runs as the app role and must be able to call this daily.
    op.execute("GRANT EXECUTE ON FUNCTION public.apply_partition_rls() TO PUBLIC")
    # Backfill every partition that exists right now.
    op.execute("SELECT public.apply_partition_rls()")


def downgrade() -> None:
    # Strip the policy + RLS from partitions again so the schema matches the
    # pre-0083 state. Parents are untouched — they were always protected.
    op.execute("""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT child.oid::regclass AS child_rel, child.relname AS child_name
                FROM pg_class child
                JOIN pg_inherits inh ON inh.inhrelid = child.oid
                JOIN pg_class parent ON parent.oid = inh.inhparent
                JOIN pg_namespace ns ON ns.oid = child.relnamespace
                WHERE ns.nspname = 'public' AND child.relrowsecurity
            LOOP
                EXECUTE format('DROP POLICY IF EXISTS %I ON %s',
                               'tenant_isolation_' || r.child_name, r.child_rel);
                EXECUTE format('ALTER TABLE %s NO FORCE ROW LEVEL SECURITY', r.child_rel);
                EXECUTE format('ALTER TABLE %s DISABLE ROW LEVEL SECURITY', r.child_rel);
            END LOOP;
        END $$;
    """)
    op.execute("DROP FUNCTION IF EXISTS public.apply_partition_rls()")
