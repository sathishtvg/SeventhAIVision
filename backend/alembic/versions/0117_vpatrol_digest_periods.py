"""Periods on the patrol email queue, so a digest is sent once and only once.

WHY THIS IS A MIGRATION AND NOT APPLICATION LOGIC. A digest covers a window --
yesterday, last week, last month -- and the scheduler that queues it runs every
two minutes. Deciding "have I already sent Monday's digest?" in Python loses the
race the moment there are two workers, a restart mid-run, or a retry after a
timeout, and the failure is not subtle: the recipient gets the same weekly
summary every two minutes until somebody notices.

So the window is stored on the row and the database enforces one digest per
(schedule, frequency, period). The queueing code does not check whether a digest
exists -- it inserts and treats the unique violation as "already handled", the
same shape uq_vpsess_execution gives session creation.

The index is PARTIAL, on session_id IS NULL. Immediate reports are one per
session and many per schedule, so constraining them the same way would refuse
the second patrol of the day.

Revision ID: 0117
Revises: 0116
"""
from alembic import op

revision = "0117"
down_revision = "0116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable: an IMMEDIATE row covers one patrol, not a period, and has none.
    op.execute("""
        ALTER TABLE virtual_patrol_email_queue
            ADD COLUMN IF NOT EXISTS period_start date,
            ADD COLUMN IF NOT EXISTS period_end   date
    """)

    # A digest row must name its window. An immediate row must not -- a period
    # on a single-session row would be meaningless and would collide in the
    # index below.
    op.execute("""
        ALTER TABLE virtual_patrol_email_queue
            ADD CONSTRAINT ck_vpeq_period CHECK (
                (frequency = 'IMMEDIATE' AND period_start IS NULL)
                OR (frequency <> 'IMMEDIATE' AND period_start IS NOT NULL
                    AND period_end IS NOT NULL AND period_end >= period_start)
            )
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_vpeq_digest_period
            ON virtual_patrol_email_queue (schedule_id, frequency, period_start)
         WHERE session_id IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_vpeq_digest_period")
    op.execute("ALTER TABLE virtual_patrol_email_queue "
               "DROP CONSTRAINT IF EXISTS ck_vpeq_period")
    op.execute("ALTER TABLE virtual_patrol_email_queue "
               "DROP COLUMN IF EXISTS period_end, DROP COLUMN IF EXISTS period_start")
