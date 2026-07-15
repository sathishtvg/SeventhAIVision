"""Zone schedule — allow restricted zones to be active only during certain
hours and days of the week.

Revision ID: 0040
Revises: 0039
"""
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE restricted_zones
            ADD COLUMN schedule_enabled   BOOLEAN   NOT NULL DEFAULT FALSE,
            ADD COLUMN schedule_timezone  VARCHAR(50) NOT NULL DEFAULT 'UTC',
            ADD COLUMN active_days        SMALLINT[] NOT NULL DEFAULT '{0,1,2,3,4,5,6}',
            ADD COLUMN active_start_time  TIME      NOT NULL DEFAULT '00:00:00',
            ADD COLUMN active_end_time    TIME      NOT NULL DEFAULT '23:59:59'
    """)
    # active_days uses ISO-weekday offset: 0=Monday … 6=Sunday


def downgrade() -> None:
    op.execute("""
        ALTER TABLE restricted_zones
            DROP COLUMN IF EXISTS schedule_enabled,
            DROP COLUMN IF EXISTS schedule_timezone,
            DROP COLUMN IF EXISTS active_days,
            DROP COLUMN IF EXISTS active_start_time,
            DROP COLUMN IF EXISTS active_end_time
    """)
