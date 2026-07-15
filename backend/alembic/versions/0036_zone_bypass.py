"""zone_bypass — add bypass_until to restricted_zones for timed zone suppression

Revision ID: 0036
Revises: 0035
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "restricted_zones",
        sa.Column("bypass_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_restricted_zones_bypass_until",
        "restricted_zones",
        ["bypass_until"],
        postgresql_where=sa.text("bypass_until IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_restricted_zones_bypass_until", table_name="restricted_zones")
    op.drop_column("restricted_zones", "bypass_until")
