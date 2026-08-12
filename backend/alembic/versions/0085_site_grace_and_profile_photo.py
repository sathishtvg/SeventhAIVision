"""Per-site late grace period, and a guard profile photo.

sites.late_grace_minutes
    Grace was a single tenant-wide number, but sites do not behave alike: a
    remote gate with one bus an hour cannot hold the same five-minute standard
    as a downtown lobby on a train line. Nullable — NULL keeps the tenant
    default, so nothing changes for sites nobody configures. Mirrors
    geofence_radius_meters, which already works exactly this way.

users.profile_photo_path
    Until now the only face the system held for a guard was their check-in
    selfie, so the command office had nobody to look at until the guard turned
    up — precisely backwards, since the guard you most need to identify is the
    one who has not arrived.

Revision ID: 0085
Revises: 0084
"""
from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sites ADD COLUMN IF NOT EXISTS late_grace_minutes INTEGER")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_photo_path TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS profile_photo_path")
    op.execute("ALTER TABLE sites DROP COLUMN IF EXISTS late_grace_minutes")
