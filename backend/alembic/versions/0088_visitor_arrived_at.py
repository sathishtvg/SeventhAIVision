"""When the PERSON arrived, separate from when their VEHICLE entered.

vehicle_entry_at is a parking clock. It is deliberately left NULL for a
visitor with no plate, because the overstay scheduler keys off it and a
walk-in has no parking to meter — starting that clock for someone on foot
would raise overstay alerts about a car that does not exist.

That left the on-site board with nothing to show for a walk-in: no arrival
time, no time-on-site, and no way to order the list. Reusing vehicle_entry_at
would have meant either lying to the overstay job or teaching it to special-
case its own key column.

So the two facts get two columns:

    vehicle_entry_at   the vehicle crossed the barrier  -> parking clock
    arrived_at         the visitor arrived              -> on-site board

Backfilled from the arrival event already recorded in visitor_logs, falling
back to vehicle_entry_at, then to created_at — every existing visit has at
least one of the three.

Revision ID: 0088
Revises: 0087
"""
from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE visitors ADD COLUMN IF NOT EXISTS arrived_at TIMESTAMPTZ")

    # visitor_logs is the authoritative record of when someone was checked in,
    # for LPR and manual arrivals alike, so it is the best source here. MIN()
    # because a visit can carry more than one arrival row if a guard corrected
    # a mistaken check-in; the first is the one that happened.
    op.execute("""
        UPDATE visitors v
           SET arrived_at = COALESCE(
                 (SELECT MIN(l.occurred_at) FROM visitor_logs l
                   WHERE l.visitor_id = v.id AND l.event_type = 'arrival'),
                 v.vehicle_entry_at,
                 v.created_at)
         WHERE v.arrived_at IS NULL
           AND (v.vehicle_entry_at IS NOT NULL OR v.status = 'arrived')
    """)

    # Deliberately nullable: a pre-registered visitor who has not turned up yet
    # has no arrival time, and NULL is the honest way to say so. "Is this
    # visitor on site?" is exactly `arrived_at IS NOT NULL AND departed`, which
    # a sentinel date would quietly break.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_visitors_arrived_open
            ON visitors (tenant_id, site_id, arrived_at)
         WHERE is_active = TRUE AND arrived_at IS NOT NULL AND vehicle_exit_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_visitors_arrived_open")
    op.execute("ALTER TABLE visitors DROP COLUMN IF EXISTS arrived_at")
