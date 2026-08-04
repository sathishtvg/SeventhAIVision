"""0077 — Per-site VMS: LPR-driven visitor entry/exit, configurable forms, free parking

Turns visitor management into a per-site capability wired to the site's own
ANPR cameras.

WHY sites GAINS COLUMNS RATHER THAN A site_vms_config TABLE
    These are four scalars on a row that already carries geofence_radius_meters,
    min_guards_per_shift, client_id and bill_rate by exactly this precedent. A
    side table would mean an outer join on every site read for no gain.

WHY NOT REUSE parking_lpr_cameras
    That table already binds a camera + trigger_type(entry|exit|both), but to a
    CAR PARK, and it drives parking_sessions (occupancy and fees). This binding
    is to a SITE and drives visitor records (who came, who hosted them, when
    they left). A site can have VMS on with no car park modelled at all, and a
    car park can meter vehicles with no visitor workflow. Same idea, different
    subject — collapsing them would force every VMS site to invent a car park.

visitor_form_fields is the Phase 8 "dynamic entry forms" requirement: admins
define what the visitor form asks, including dropdowns. Field definitions are
tenant-wide when site_id IS NULL and site-specific otherwise, so a tenant sets
a common form once and a particular site adds its own questions on top.
Submitted values land in visitors.custom_fields as JSONB — same convention as
restricted_zones.polygon and alerts.message_params for small structured
per-record data, rather than a value table nothing would ever query alone.

FREE PARKING: a site-level default with an optional per-visit override, so the
common case is one number and a specific visitor can still be granted longer
without an operator having to dismiss a recurring alert.
"""
from __future__ import annotations

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None

# Field types the visitor form builder supports. 'select'/'multiselect' are the
# dropdown cases the requirement called out explicitly.
FIELD_TYPES = ("text", "textarea", "number", "date", "select", "multiselect", "checkbox", "phone", "email")


def upgrade() -> None:
    # ── Per-site VMS configuration ────────────────────────────────────────
    op.execute("""
        ALTER TABLE sites
          ADD COLUMN IF NOT EXISTS vms_enabled          BOOLEAN NOT NULL DEFAULT FALSE,
          ADD COLUMN IF NOT EXISTS entry_lpr_camera_id  UUID REFERENCES cameras(id) ON DELETE SET NULL,
          ADD COLUMN IF NOT EXISTS exit_lpr_camera_id   UUID REFERENCES cameras(id) ON DELETE SET NULL,
          ADD COLUMN IF NOT EXISTS free_parking_minutes INTEGER
    """)
    # A camera can only be one site's entry (or exit) lane; a partial unique
    # index enforces that without blocking the many sites that leave it NULL.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_sites_entry_lpr_camera
            ON sites(entry_lpr_camera_id) WHERE entry_lpr_camera_id IS NOT NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_sites_exit_lpr_camera
            ON sites(exit_lpr_camera_id) WHERE exit_lpr_camera_id IS NOT NULL
    """)

    # ── Dynamic visitor form fields ───────────────────────────────────────
    types = ", ".join(f"'{t}'" for t in FIELD_TYPES)
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS visitor_form_fields (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id      UUID REFERENCES sites(id) ON DELETE CASCADE,
            field_key    VARCHAR(50) NOT NULL,
            label        VARCHAR(255) NOT NULL,
            field_type   VARCHAR(20) NOT NULL CHECK (field_type IN ({types})),
            options      JSONB NOT NULL DEFAULT '[]',
            is_required  BOOLEAN NOT NULL DEFAULT FALSE,
            placeholder  VARCHAR(255),
            help_text    TEXT,
            sort_order   INTEGER NOT NULL DEFAULT 0,
            is_active    BOOLEAN NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # field_key is the JSON key inside visitors.custom_fields, so it must be
    # unique within its scope or one field would silently overwrite another.
    # Two partial indexes because NULL site_id never equals NULL in a plain
    # unique constraint, which would let duplicate tenant-wide keys through.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_visitor_form_fields_tenant_key
            ON visitor_form_fields(tenant_id, field_key) WHERE site_id IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_visitor_form_fields_site_key
            ON visitor_form_fields(site_id, field_key) WHERE site_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_visitor_form_fields_lookup
            ON visitor_form_fields(tenant_id, site_id, sort_order) WHERE is_active = TRUE
    """)

    op.execute("ALTER TABLE visitor_form_fields ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE visitor_form_fields FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_visitor_form_fields ON visitor_form_fields
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── Visitor record: custom values, vehicle timing, parking override ───
    op.execute("""
        ALTER TABLE visitors
          ADD COLUMN IF NOT EXISTS custom_fields        JSONB NOT NULL DEFAULT '{}',
          ADD COLUMN IF NOT EXISTS free_parking_minutes INTEGER,
          ADD COLUMN IF NOT EXISTS vehicle_entry_at     TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS vehicle_exit_at      TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS entry_lpr_detection_id UUID,
          ADD COLUMN IF NOT EXISTS exit_lpr_detection_id  UUID
    """)
    # Drives the overstay sweep: find visits whose vehicle is still on site.
    # entry_lpr_detection_id is deliberately not a foreign key — detections is
    # partitioned on (id, detected_at), so a real FK would force a denormalised
    # timestamp onto this row for no practical gain. Same trade-off already
    # documented for alerts.detection_id.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_visitors_vehicle_onsite
            ON visitors(tenant_id, vehicle_entry_at)
            WHERE vehicle_entry_at IS NOT NULL AND vehicle_exit_at IS NULL
    """)

    # No new permission codes: site VMS config is site configuration
    # (site:manage) and form-field authoring is visitor administration
    # (visitor:manage). Both already exist with the right grantees.


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_visitors_vehicle_onsite")
    op.execute("""
        ALTER TABLE visitors
          DROP COLUMN IF EXISTS custom_fields,
          DROP COLUMN IF EXISTS free_parking_minutes,
          DROP COLUMN IF EXISTS vehicle_entry_at,
          DROP COLUMN IF EXISTS vehicle_exit_at,
          DROP COLUMN IF EXISTS entry_lpr_detection_id,
          DROP COLUMN IF EXISTS exit_lpr_detection_id
    """)

    op.execute("DROP TABLE IF EXISTS visitor_form_fields")

    op.execute("DROP INDEX IF EXISTS uq_sites_entry_lpr_camera")
    op.execute("DROP INDEX IF EXISTS uq_sites_exit_lpr_camera")
    op.execute("""
        ALTER TABLE sites
          DROP COLUMN IF EXISTS vms_enabled,
          DROP COLUMN IF EXISTS entry_lpr_camera_id,
          DROP COLUMN IF EXISTS exit_lpr_camera_id,
          DROP COLUMN IF EXISTS free_parking_minutes
    """)
