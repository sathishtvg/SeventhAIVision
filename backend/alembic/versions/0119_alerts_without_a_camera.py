"""An alert that has nothing to do with a camera should not need one.

THE BUG THIS FIXES IS ALREADY WRITTEN DOWN IN THE CODEBASE, IN SIX PLACES.
alerts.camera_id is NOT NULL, and alerts carries no site of its own -- site
scoping is derived entirely through camera -> site. So every alert that is not
about a camera (a contractor permit expiring, a visitor overstaying, a guard
pressing panic) has to produce a camera from somewhere, and eighteen call sites
across six files do this:

    (SELECT id FROM cameras WHERE tenant_id = ... LIMIT 1)

Two different failures come out of that, and the second is the worse one:

  * A tenant with NO cameras gets nothing. The subselect returns no rows, the
    guarded INSERT writes nothing, and the alert silently does not exist. A
    guarding-only agency -- a large part of who this is sold to -- receives no
    contractor expiry warnings, no visitor overstay alerts, and (until sos.py
    worked around it) no record of a guard pressing panic.

  * A tenant WITH cameras gets the alert attached to an ARBITRARY one. LIMIT 1
    with no ORDER BY is whichever row Postgres hands back. So a contractor
    permit alert is filed against an unrelated camera at an unrelated site --
    and because scoping runs through that camera, it is shown to whoever can
    see that site and hidden from the people responsible for the contractor.
    Wrong data reads as right; missing data at least looks missing.

The cause is that alerts cannot say where they happened except by pointing at a
camera. So: give them a site, and let the camera be optional.

WHY BOTH CHANGES IN ONE MIGRATION. Making camera_id nullable on its own would
be worse than the status quo: seven queries INNER JOIN alerts to cameras (the
Action Centre and the Command Centre among them), so a null-camera alert would
vanish from exactly the boards an operator watches. The site column is what lets
those become LEFT JOINs without losing scoping.

Revision ID: 0119
Revises: 0118
"""
from alembic import op

revision = "0119"
down_revision = "0118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE alerts
            ADD COLUMN IF NOT EXISTS site_id UUID REFERENCES sites(id) ON DELETE SET NULL;
    """)

    # Backfill from the camera, which is where the site has effectively been
    # stored all along. Every existing row resolves, because every existing row
    # was forced to have a camera.
    op.execute("""
        UPDATE alerts a
           SET site_id = c.site_id
          FROM cameras c
         WHERE c.id = a.camera_id AND a.site_id IS NULL;
    """)

    # Now the camera may be absent. Nullable rather than dropped: an alert that
    # IS about a camera should still say which, and the detection pipeline
    # depends on it.
    op.execute("ALTER TABLE alerts ALTER COLUMN camera_id DROP NOT NULL;")

    # Scoping reads site_id directly now, so it needs to be indexed the way the
    # camera join used to be.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_alerts_site_status
            ON alerts (tenant_id, site_id, status, created_at DESC);
    """)


def downgrade() -> None:
    # camera_id cannot simply be made NOT NULL again -- by then there may be
    # rows that legitimately have no camera, which is the entire point of this
    # migration. Those are given one the old way so the constraint can be
    # restored; it is lossy, and it is why this downgrade exists for
    # completeness rather than for use.
    op.execute("""
        UPDATE alerts a
           SET camera_id = (SELECT c.id FROM cameras c
                             WHERE c.tenant_id = a.tenant_id LIMIT 1)
         WHERE a.camera_id IS NULL;
        DELETE FROM alerts WHERE camera_id IS NULL;
        ALTER TABLE alerts ALTER COLUMN camera_id SET NOT NULL;
        DROP INDEX IF EXISTS idx_alerts_site_status;
        ALTER TABLE alerts DROP COLUMN IF EXISTS site_id;
    """)
