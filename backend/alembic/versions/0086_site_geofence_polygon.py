"""Site geofence polygon — an alternative to the radius circle.

A circle is the wrong shape for most real sites: a logistics yard, an L-shaped
mall or a compound that hugs a road ends up either excluding ground the guard
must stand on, or including a neighbouring building's car park. Admins can now
draw the actual boundary instead.

The radius column stays and stays used: a site with no polygon keeps working
exactly as before, so this is additive and needs no backfill. Where both are
set, the polygon wins — see services/geofence.py.

Revision ID: 0086
Revises: 0085
"""
from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # JSONB array of {"lat": float, "lng": float}, in drawing order.
    # NULL means "no polygon — use the radius", which is what every existing
    # row gets for free.
    op.execute("ALTER TABLE sites ADD COLUMN IF NOT EXISTS geofence_polygon JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE sites DROP COLUMN IF EXISTS geofence_polygon")
