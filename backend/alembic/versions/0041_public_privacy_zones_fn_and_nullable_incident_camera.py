"""Two schema fixes:
1. SECURITY DEFINER function get_camera_privacy_zones_public — lets AI workers
   (and the public /privacy/zones/camera/{id} endpoint) query privacy zones by
   camera_id without holding a tenant JWT.  The function runs as the owner
   (superuser) and bypasses RLS, which is safe because the camera_id acts as
   a direct scoping key — callers can only see zones for cameras they already
   know the UUID of.

2. Make incidents.camera_id nullable — some incident types (manually filed
   security reports, DSAR-related incidents) are not linked to a specific
   camera.  Existing rows are unaffected; camera-linked incidents keep the FK
   and the ON DELETE CASCADE behavior.

Revision ID: 0041
Revises:     0040
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. SECURITY DEFINER function for public camera privacy-zones lookup
    op.execute("""
        CREATE OR REPLACE FUNCTION get_camera_privacy_zones_public(p_camera_id UUID)
        RETURNS TABLE (id UUID, polygon JSONB, fill_color VARCHAR)
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        AS $$
            SELECT pz.id, pz.polygon, pz.fill_color
            FROM privacy_zones pz
            WHERE pz.camera_id = p_camera_id
              AND pz.is_active = TRUE;
        $$;
    """)
    op.execute("GRANT EXECUTE ON FUNCTION get_camera_privacy_zones_public(UUID) TO svc_app")

    # 2. Make incidents.camera_id nullable
    op.execute("ALTER TABLE incidents ALTER COLUMN camera_id DROP NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE incidents ALTER COLUMN camera_id SET NOT NULL")
    op.execute("DROP FUNCTION IF EXISTS get_camera_privacy_zones_public(UUID)")
