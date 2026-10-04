"""Partition maintenance that makes partitions.

WHAT WAS WRONG
    Fourteen tables are partitioned by month through pg_partman, each created
    with the three months ahead already made. Making the months after that is
    the scheduler's nightly job — and it has never made one. It ran as the app
    role, which does not own the tables and so cannot add a partition to them;
    and pg_partman reads each partition by name, which on a pooled connection
    raised ''::uuid inside the partition's own row-security policy. The scheduler
    caught the error and logged it as a warning. So every partitioned table ran
    out of partitions three months after it was created, and every row after
    that goes to the table's default partition.

    The job is fixed in scheduler_main: it now runs on the superuser session the
    audit-archive job already uses, one table at a time. Two things belong here
    rather than there.

KEEP MAKING PARTITIONS FOR A QUIET TABLE
    pg_partman's default is to make new partitions only while data keeps
    arriving: it finds the newest partition that holds a row and makes the
    months after *that*. A table that has been quiet for a season — weapon
    events on a site where nothing has happened — is left with no partition for
    today, and the first row to arrive goes to the default. For tables that
    must always be ready for the next event that is the wrong default.
    `infinite_time_partitions = true` makes the months ahead of *now*, whatever
    the table holds.

CLOSE THE GAP NOW, NOT TONIGHT
    The scheduler's daily cycle runs once the process has been up for a day, and
    a machine that is switched off at night never gets there. So the months
    ahead are made here too, as the owner of the tables. A table whose default
    partition already holds rows for a month that is missing cannot have that
    month made by this — Postgres refuses, by design — and is left, with a
    warning, to the scheduler, which moves those rows first. That must not be
    done here: moving them the plain way deletes everything linked to them (see
    scheduler_main._move_stranded_rows), and a migration is no place for it.

    New partitions get their row security in the same transaction.

Revision ID: 0131
Revises: 0130
"""
from alembic import op

revision = "0131"
down_revision = "0130"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE public.part_config SET infinite_time_partitions = true WHERE NOT infinite_time_partitions")
    op.execute("""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT parent_table FROM public.part_config
                      WHERE automatic_maintenance = 'on' ORDER BY parent_table
            LOOP
                BEGIN
                    PERFORM public.run_maintenance(r.parent_table, p_analyze => false);
                EXCEPTION WHEN OTHERS THEN
                    RAISE WARNING 'partitions for % are left to the scheduler: %', r.parent_table, SQLERRM;
                END;
            END LOOP;
            PERFORM public.apply_partition_rls();
        END $$;
    """)


def downgrade() -> None:
    # The setting goes back to what it was for every set. The partitions made
    # stay: they may hold rows by now, and an empty partition harms nothing.
    op.execute("UPDATE public.part_config SET infinite_time_partitions = false")
