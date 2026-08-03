"""0078 — Barrier control via the ANPR camera's own relay output

Most gate installations do not have a separate access controller: the ANPR
camera itself has an alarm/relay output wired directly to the barrier's open
terminal, and the camera is told to close that contact over its HTTP API. That
is one less device, one less IP, and one less thing to fail.

Adds two vendors alongside the existing dedicated-controller ones:

    hikvision_camera_io — ISAPI /System/IO/outputs/{port}/trigger
    dahua_camera_io     — CGI configManager AlarmOut[N].Mode

The existing 'relay' vendor already covers the other case the requirement
names — a standalone network relay board driven by a software call. Between
camera IO and a relay board, essentially every barrier on the market is
reachable without a vendor access controller.
"""
from __future__ import annotations

from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None

_VENDORS = (
    "hikvision", "dahua", "relay", "simulator",
    "hikvision_camera_io", "dahua_camera_io",
)


def upgrade() -> None:
    vendors = ", ".join(f"'{v}'" for v in _VENDORS)
    op.execute("ALTER TABLE barriers DROP CONSTRAINT IF EXISTS barriers_vendor_check")
    op.execute(f"ALTER TABLE barriers ADD CONSTRAINT barriers_vendor_check CHECK (vendor IN ({vendors}))")


def downgrade() -> None:
    # Any barrier already using a camera-IO vendor would violate the narrowed
    # constraint, so park those on 'relay' — the closest equivalent (a contact
    # closure driven by a software call) — rather than failing the downgrade.
    op.execute(
        "UPDATE barriers SET vendor = 'relay' "
        "WHERE vendor IN ('hikvision_camera_io', 'dahua_camera_io')"
    )
    op.execute("ALTER TABLE barriers DROP CONSTRAINT IF EXISTS barriers_vendor_check")
    op.execute(
        "ALTER TABLE barriers ADD CONSTRAINT barriers_vendor_check "
        "CHECK (vendor IN ('hikvision', 'dahua', 'relay', 'simulator'))"
    )
