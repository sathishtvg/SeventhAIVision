"""Visit type on a visitor, and marking a visit closed by the day rollover.

WHY visit_type
    The on-site board could only ever show vehicles, because "who is here" was
    inferred from vehicle_entry_at. A gatehouse has to record far more than
    that: a walk-in with no car, a courier making a delivery, someone dropping
    a passenger off, someone collecting one. Those were all being squeezed into
    the free-text `purpose` field, which cannot be filtered, counted or
    reported on. This makes the category a real column.

    Existing rows are backfilled to 'vehicle' where a plate was recorded and
    'walk_in' where none was, which is the honest reading of the data we have.

WHY closed_by_day_rollover
    A visit left open overnight is indistinguishable from one still on site,
    so an unclosed day quietly poisons every "currently on site" count that
    follows it. The scheduler now closes open visits at the site's local end of
    day. That has to be tellable apart from a real departure: an exit read by
    the LPR camera means the vehicle actually left, whereas this means nobody
    ever checked them out. Reporting on a gatehouse's discipline depends on the
    difference, so it gets its own flag rather than being hidden inside
    vehicle_exit_at.

Revision ID: 0087
Revises: 0086
"""
from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


VISIT_TYPES = ("vehicle", "walk_in", "delivery", "drop_off", "pick_up")


def upgrade() -> None:
    op.execute("ALTER TABLE visitors ADD COLUMN IF NOT EXISTS visit_type TEXT")

    # Backfill before the constraint, or the constraint rejects every existing
    # row. Plate present is the only evidence available for what an old visit
    # was, and it is a fair one.
    op.execute("""
        UPDATE visitors
           SET visit_type = CASE
                 WHEN vehicle_plate IS NOT NULL AND vehicle_plate <> '' THEN 'vehicle'
                 ELSE 'walk_in'
               END
         WHERE visit_type IS NULL
    """)

    op.execute("ALTER TABLE visitors ALTER COLUMN visit_type SET DEFAULT 'walk_in'")
    op.execute("ALTER TABLE visitors ALTER COLUMN visit_type SET NOT NULL")

    # Named so a violation says which rule was broken, and matching the
    # vendor-constraint style used elsewhere in this schema.
    op.execute(
        "ALTER TABLE visitors ADD CONSTRAINT ck_visitors_visit_type "
        "CHECK (visit_type IN ('vehicle','walk_in','delivery','drop_off','pick_up'))"
    )

    op.execute(
        "ALTER TABLE visitors ADD COLUMN IF NOT EXISTS "
        "closed_by_day_rollover BOOLEAN NOT NULL DEFAULT FALSE"
    )

    # The on-site board reads open visits per site constantly (every 30s per
    # gatehouse screen), and after this change it no longer filters on
    # vehicle_entry_at, so the old access path stops helping.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_visitors_open_by_site
            ON visitors (tenant_id, site_id)
         WHERE is_active = TRUE AND vehicle_exit_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_visitors_open_by_site")
    op.execute("ALTER TABLE visitors DROP COLUMN IF EXISTS closed_by_day_rollover")
    op.execute("ALTER TABLE visitors DROP CONSTRAINT IF EXISTS ck_visitors_visit_type")
    op.execute("ALTER TABLE visitors DROP COLUMN IF EXISTS visit_type")
