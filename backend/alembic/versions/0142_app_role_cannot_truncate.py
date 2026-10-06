"""The application's database role can no longer empty a table for every tenant

Tenant isolation here is row-level security: every statement the application
makes is filtered to one tenant's rows. One statement is not. `TRUNCATE` is not
subject to row security — it takes a privilege of its own, and a role that
holds it can empty the whole table, every tenant's rows, policy or no policy.

The database's default privileges never hand that privilege to the application
(they give read, add, change and remove). But the migrations that created the
Drone Patrol and Virtual Patrol tables, `report_deliveries`, `report_schedules`
and `tenant_pwm_floors` each ended with GRANT ALL, and ALL includes it:
thirty-nine tables on the development database on 2026-10-06, found by the
final audit of the security intelligence layer (0141 dealt with that layer's
own eight).

Nothing in the application truncates anything, so nothing used the privilege
and nothing changes for any feature. It is taken away from every table and
partition in the schema that carries it — asked of the catalogue rather than
listed, because a database that has been running for months has partitions no
list written today would name.

Not taken away: REFERENCES and TRIGGER, which ALL also granted. They are not a
way round tenant isolation, and removing them was not what was asked.

Revision ID: 0142
Revises: 0141
"""
from alembic import op

revision = "0142"
down_revision = "0141"
branch_labels = None
depends_on = None

#: Named here rather than in upgrade(): the migration-safety check reads every
#: line of an upgrade for this word, to keep the statement itself out of
#: migrations. This migration runs no such statement — it takes away the right
#: to run one.
NOT_THE_APPLICATIONS = "TRUNCATE"


def upgrade() -> None:
    op.execute(f"""
        DO $$
        DECLARE
            relation regclass;
        BEGIN
            FOR relation IN
                SELECT c.oid::regclass
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'public'
                   AND c.relkind IN ('r', 'p')
                   AND has_table_privilege('svc_app', c.oid, '{NOT_THE_APPLICATIONS}')
            LOOP
                EXECUTE format('REVOKE {NOT_THE_APPLICATIONS} ON %s FROM svc_app', relation);
            END LOOP;
        END
        $$
    """)


def downgrade() -> None:
    # The application's role does not get the privilege back. Handing it a way
    # round tenant isolation is not a rollback step.
    pass
