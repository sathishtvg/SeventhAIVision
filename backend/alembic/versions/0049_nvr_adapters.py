"""Gap 32 — NVR Adapters: Hikvision ISAPI + Dahua HTTP + camera model library.

revision: 0049
down_revision: 0048
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

_RLS = """
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_{safe} ON {t}
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
"""

# Built-in models are visible to all tenants (tenant_id IS NULL).
# Custom models visible only to their own tenant (tenant_id = GUC).
# WITH CHECK prevents inserting rows with another tenant's id.
_MODEL_RLS = """
ALTER TABLE camera_model_library ENABLE ROW LEVEL SECURITY;
ALTER TABLE camera_model_library FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_camera_model_library ON camera_model_library
    USING (tenant_id IS NULL
        OR (current_setting('app.current_tenant', true) <> ''
            AND tenant_id = current_setting('app.current_tenant', true)::uuid))
    WITH CHECK (current_setting('app.current_tenant', true) <> ''
        AND tenant_id = current_setting('app.current_tenant', true)::uuid);
"""

# (make, model_name, model_series, device_type, http_port, rtsp_port,
#  rtsp_path_template, protocols_list, ptz_supported, max_resolution_mp)
_BUILTIN_MODELS = [
    # Hikvision
    ("Hikvision", "DS-2CD2T43G2-2I", "AcuSense", "ipc", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/Streaming/Channels/10{channel_id}",
     ["RTSP", "ONVIF", "ISAPI"], True, 4.0),
    ("Hikvision", "DS-2DE4A425IWG-E", "PTZ Network", "ptz_dome", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/Streaming/Channels/10{channel_id}",
     ["RTSP", "ONVIF", "ISAPI"], True, 4.0),
    ("Hikvision", "DS-7608NXI-I2", "AcuSense NVR", "nvr", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/Streaming/Channels/10{channel_id}",
     ["RTSP", "ONVIF", "ISAPI"], False, None),
    # Dahua
    ("Dahua", "IPC-HDW2831T-AS", "Lite IR Fixed-focal", "ipc", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/cam/realmonitor?channel={channel_id}&subtype=0",
     ["RTSP", "ONVIF", "DAHUA_HTTP"], False, 8.0),
    ("Dahua", "SD49425XB-HNR", "WizSense PTZ", "ptz_dome", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/cam/realmonitor?channel={channel_id}&subtype=0",
     ["RTSP", "ONVIF", "DAHUA_HTTP"], True, 4.0),
    ("Dahua", "NVR4216-16P-EI", "WizSense NVR", "nvr", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/cam/realmonitor?channel={channel_id}&subtype=0",
     ["RTSP", "ONVIF", "DAHUA_HTTP"], False, None),
    ("Dahua", "IPC-HFW2849S-S-IL", "Smart Dual Light", "ipc", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/cam/realmonitor?channel={channel_id}&subtype=0",
     ["RTSP", "ONVIF", "DAHUA_HTTP"], False, 8.0),
    # Axis
    ("Axis", "P3245-V", "Fixed Dome", "ipc", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/axis-media/media.amp",
     ["RTSP", "ONVIF"], False, 2.0),
    ("Axis", "Q6135-LE", "PTZ Network Camera", "ptz_dome", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/axis-media/media.amp",
     ["RTSP", "ONVIF"], True, 2.0),
    # Hanwha
    ("Hanwha", "QNV-8080R", "Q Series Dome", "ipc", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/profile2/media.smp",
     ["RTSP", "ONVIF"], False, 5.0),
    # Bosch
    ("Bosch", "FLEXIDOME 5100i IR", "FLEXIDOME", "ipc", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/video?inst=1",
     ["RTSP", "ONVIF"], False, 2.0),
    # Generic/ONVIF
    ("Generic", "ONVIF-IPC", None, "ipc", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/stream1",
     ["RTSP", "ONVIF"], False, None),
    ("Generic", "ONVIF-PTZ", None, "ptz_dome", 80, 554,
     "rtsp://{username}:{password}@{host}:{rtsp_port}/stream1",
     ["RTSP", "ONVIF"], True, None),
]


def upgrade():
    # ── nvr_connections ────────────────────────────────────────────────────────
    op.create_table(
        "nvr_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer, nullable=False, server_default="80"),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("password_enc", sa.Text, nullable=False),
        sa.Column("adapter_type", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_probe_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_probe_status", sa.String(20), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_nvr_connections_tenant_name"),
    )
    op.create_index("idx_nvr_connections_tenant", "nvr_connections", ["tenant_id"])
    op.execute(_RLS.format(t="nvr_connections", safe="nvr_connections"))

    # ── camera_model_library ───────────────────────────────────────────────────
    op.create_table(
        "camera_model_library",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("make", sa.String(100), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("model_series", sa.String(255), nullable=True),
        sa.Column("device_type", sa.String(30), nullable=False),
        sa.Column("default_http_port", sa.Integer, nullable=False, server_default="80"),
        sa.Column("default_rtsp_port", sa.Integer, nullable=False, server_default="554"),
        sa.Column("rtsp_path_template", sa.Text, nullable=True),
        sa.Column("protocols", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("ptz_supported", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("max_resolution_mp", sa.Numeric(5, 1), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_builtin", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("idx_camera_model_library_make", "camera_model_library", ["make"])
    op.execute(_MODEL_RLS)

    # Seed built-in models (postgres superuser bypasses RLS → tenant_id can be NULL)
    for (make, model_name, series, dtype, http_port, rtsp_port,
         rtsp_tmpl, protocols, ptz, mp) in _BUILTIN_MODELS:
        series_sql = f"'{series}'" if series is not None else "NULL"
        mp_sql = str(mp) if mp is not None else "NULL"
        ptz_sql = "true" if ptz else "false"
        protocols_sql = "ARRAY[" + ",".join(f"'{p}'" for p in protocols) + "]::text[]"
        op.execute(
            f"INSERT INTO camera_model_library "
            f"(make, model_name, model_series, device_type, default_http_port, "
            f" default_rtsp_port, rtsp_path_template, protocols, ptz_supported, "
            f" max_resolution_mp, is_builtin) "
            f"VALUES ('{make}', '{model_name}', {series_sql}, '{dtype}', {http_port}, "
            f"        {rtsp_port}, '{rtsp_tmpl}', {protocols_sql}, {ptz_sql}, {mp_sql}, true)"
        )


def downgrade():
    op.drop_table("camera_model_library")
    op.drop_table("nvr_connections")
