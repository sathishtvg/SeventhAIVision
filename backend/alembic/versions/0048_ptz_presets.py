"""Gap 31 — PTZ presets and ONVIF discovery cache.

revision: 0048
down_revision: 0047
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

_RLS = """
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_{safe} ON {t}
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
"""


def upgrade():
    # ── ptz_presets ────────────────────────────────────────────────────────────
    op.create_table(
        "ptz_presets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("camera_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("pan", sa.Numeric(5, 4), nullable=False, server_default="0"),
        sa.Column("tilt", sa.Numeric(5, 4), nullable=False, server_default="0"),
        sa.Column("zoom", sa.Numeric(5, 4), nullable=False, server_default="0"),
        sa.Column("onvif_token", sa.String(100), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "camera_id", "name",
                            name="uq_ptz_presets_camera_name"),
    )
    op.create_index("idx_ptz_presets_camera", "ptz_presets", ["camera_id"])
    op.execute(_RLS.format(t="ptz_presets", safe="ptz_presets"))

    # ── onvif_discovery_cache ──────────────────────────────────────────────────
    op.create_table(
        "onvif_discovery_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("xaddr", sa.Text, nullable=False),
        sa.Column("hardware", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("location", sa.Text, nullable=True),
        sa.Column("types", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("scopes", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "xaddr",
                            name="uq_onvif_cache_tenant_xaddr"),
    )
    op.create_index("idx_onvif_discovery_tenant", "onvif_discovery_cache", ["tenant_id"])
    op.execute(_RLS.format(t="onvif_discovery_cache", safe="onvif_discovery_cache"))


def downgrade():
    op.drop_table("onvif_discovery_cache")
    op.drop_table("ptz_presets")
