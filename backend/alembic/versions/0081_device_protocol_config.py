"""Per-protocol hardware configuration (admin-configurable devices).

WHY THIS EXISTS
    Barrier config was seven fixed columns — host, port, username, password,
    relay_channel, pulse_ms (+ vendor). Every site runs different hardware, and
    anything that did not fit those seven fields could not be configured at all
    without a schema change.

    Concretely, today: the `relay` driver needs an open/close URL path and an
    auth scheme, because no-name LAN relay boards all differ. Those three
    values have nowhere to live, so the driver hardcodes them and a board with
    a different path simply cannot be used.

    Two changes fix that:

      1. `config JSONB` holds whatever a protocol needs beyond the common
         fields. Common fields stay as real columns — they are near-universal,
         worth having queryable, and `password_encrypted` already has crypto
         wiring behind it. Only the varying tail moves to JSONB.

      2. The `vendor` CHECK constraint is dropped. It enumerated six values, so
         adding a protocol required a migration before the protocol could even
         be named. Validation moves to shared/shared/device_protocols.py, which
         the API enforces on write and the admin UI renders forms from.

ON DROPPING A CHECK CONSTRAINT
    This trades a database-level guarantee for an application-level one, which
    is normally the wrong direction. It is right here because the constraint
    was not protecting data integrity — it was hardcoding a product catalogue
    into the schema, and that is precisely what has to become configurable.
    `vendor` stays NOT NULL, and the API rejects any value not in the registry,
    so the reachable states are unchanged for every caller that goes through
    the API.

Revision ID: 0081
Revises: 0080
"""
from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None

# Enumerated by migration 0076/0078. Recreated verbatim on downgrade so a
# rollback restores exactly the constraint that existed before.
_LEGACY_VENDORS = (
    "hikvision", "dahua", "relay", "simulator",
    "hikvision_camera_io", "dahua_camera_io",
)


def upgrade() -> None:
    # Protocol-specific parameters. Defaulted to '{}' rather than NULL so every
    # read path can treat it as a dict without a None check.
    op.execute("""
        ALTER TABLE barriers
          ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'::jsonb
    """)
    op.execute("""
        ALTER TABLE alarm_panels
          ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'::jsonb
    """)

    # The product catalogue no longer lives in the schema — see module docstring.
    op.execute("ALTER TABLE barriers DROP CONSTRAINT IF EXISTS barriers_vendor_check")

    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('device_config:read',   'View hardware device protocol configuration', 'device'),
          ('device_config:manage', 'Configure hardware device protocols and connection settings', 'device')
        ON CONFLICT (code) DO NOTHING
    """)
    # Same split used by recording_policy (0079) and alert_rules (0080):
    # supervisors need to see how a site's hardware is wired when diagnosing a
    # gate that won't open; changing the wiring is an admin action.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3,8) AND p.code = 'device_config:read'
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,8) AND p.code = 'device_config:manage'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions WHERE permission_id IN
          (SELECT id FROM permissions WHERE code IN ('device_config:read','device_config:manage'))
    """)
    op.execute("""
        DELETE FROM permissions WHERE code IN ('device_config:read','device_config:manage')
    """)

    # Any row using a protocol added after this migration would violate the
    # restored constraint. Park those on 'simulator' — inert and obviously
    # wrong to an operator — rather than letting the rollback fail outright or
    # silently deleting a site's gate configuration.
    vendor_list = ", ".join(f"'{v}'" for v in _LEGACY_VENDORS)
    op.execute(f"""
        UPDATE barriers SET vendor = 'simulator', is_active = FALSE
        WHERE vendor NOT IN ({vendor_list})
    """)
    op.execute(f"""
        ALTER TABLE barriers ADD CONSTRAINT barriers_vendor_check
          CHECK (vendor::text = ANY (ARRAY[{vendor_list}]::text[]))
    """)

    op.execute("ALTER TABLE alarm_panels DROP COLUMN IF EXISTS config")
    op.execute("ALTER TABLE barriers DROP COLUMN IF EXISTS config")
