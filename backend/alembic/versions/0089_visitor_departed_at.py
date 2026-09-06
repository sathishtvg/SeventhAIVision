"""When the PERSON left, separate from when their VEHICLE did.

The mirror of arrived_at (0088), and it fixes a real break.

0088 made the on-site board show everyone by keying presence off arrived_at,
but kept filtering departures on vehicle_exit_at. Both check-out paths —
POST /visitors/{id}/checkout and the departure branch of /visitors/qr-scan —
only ever set status='departed'; neither touches vehicle_exit_at, because
neither is about a vehicle. So a guard could check a walk-in out and watch them
stay on the board indefinitely, which is worse than the original bug: the
board would claim people are on site who left hours ago.

Filtering on status alone is not the answer either. 'departed' is also written
by flows that are not a real exit, and the day-close needs to stay tellable
apart from a genuine departure (see closed_by_day_rollover, 0087).

So the same split as arrivals:

    vehicle_exit_at   the vehicle left the barrier   -> parking / LPR evidence
    departed_at       the visitor left               -> on-site board

Backfilled from vehicle_exit_at where present, then from the departure event
already in visitor_logs, so a visit checked out by QR or by hand before this
migration still leaves the board.

Revision ID: 0089
Revises: 0088
"""
from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE visitors ADD COLUMN IF NOT EXISTS departed_at TIMESTAMPTZ")

    # vehicle_exit_at first: where a vehicle exit was recorded it is the most
    # precise moment we have, and it came from an LPR read or the day-close.
    op.execute("""
        UPDATE visitors v
           SET departed_at = COALESCE(
                 v.vehicle_exit_at,
                 (SELECT MAX(l.occurred_at) FROM visitor_logs l
                   WHERE l.visitor_id = v.id AND l.event_type = 'departure'))
         WHERE v.departed_at IS NULL
           AND (v.vehicle_exit_at IS NOT NULL OR v.status = 'departed')
    """)

    # Anyone marked departed with no timestamp anywhere still has to leave the
    # board. updated_at is the closest honest approximation of when that
    # happened, and leaving them on it would be a live wrong answer.
    op.execute("""
        UPDATE visitors
           SET departed_at = COALESCE(updated_at, created_at)
         WHERE departed_at IS NULL AND status = 'departed'
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_visitors_on_site
            ON visitors (tenant_id, site_id, arrived_at)
         WHERE is_active = TRUE AND arrived_at IS NOT NULL AND departed_at IS NULL
    """)
    # Superseded: the board no longer filters on vehicle_exit_at.
    op.execute("DROP INDEX IF EXISTS ix_visitors_arrived_open")


def downgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_visitors_arrived_open
            ON visitors (tenant_id, site_id, arrived_at)
         WHERE is_active = TRUE AND arrived_at IS NOT NULL AND vehicle_exit_at IS NULL
    """)
    op.execute("DROP INDEX IF EXISTS ix_visitors_on_site")
    op.execute("ALTER TABLE visitors DROP COLUMN IF EXISTS departed_at")
