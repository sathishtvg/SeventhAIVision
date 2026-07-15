"""Add SECURITY DEFINER function for parking LPR camera config lookup.

The parking_lpr service (handle_lpr_plate_detected) is called from the Redis
listener with a tenant_id that comes from the LPR event.  In tests it is called
directly with a db_session that has no app.current_tenant set and therefore
receives a random UUID fallback.  A SECURITY DEFINER function lets the service
look up the real tenant_id + LPR config for a camera by camera_id without
needing the RLS context to be set first.

Revision ID: 0042
Revises:     0041
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION get_lpr_config_for_camera(p_camera_id UUID)
        RETURNS TABLE (
            tenant_id      UUID,
            car_park_id    UUID,
            trigger_type   TEXT,
            default_zone_id UUID,
            car_park_name  TEXT
        )
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        AS $$
            SELECT plc.tenant_id,
                   plc.car_park_id,
                   plc.trigger_type::TEXT,
                   plc.default_zone_id,
                   cp.name::TEXT
            FROM parking_lpr_cameras plc
            JOIN car_parks cp ON cp.id = plc.car_park_id
            WHERE plc.camera_id = p_camera_id
              AND plc.is_active = TRUE
            LIMIT 1;
        $$;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS get_lpr_config_for_camera(UUID);")
