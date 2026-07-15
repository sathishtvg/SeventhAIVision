"""0059 — Alert response channel tracking

Alerts already track WHO acknowledged/marked-false-positive (user_id) and
WHEN, but not HOW — which client the response came from. On-duty guards now
receive push notifications and can acknowledge from the mobile app (Gap 62
push delivery + existing mobile Acknowledge button), so a supervisor
reviewing the alert log needs to see whether a response came from a guard's
phone in the field or an operator at a desktop, for accountability.

Defaults to 'web' so every existing response, and any future response that
doesn't explicitly say otherwise, is attributed to the web client (matches
current behavior unchanged for the desktop app, which is just the web app
in an Electron shell).
"""
from __future__ import annotations

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS acknowledged_via VARCHAR(10) NOT NULL DEFAULT 'web'")
    op.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS fp_marked_via VARCHAR(10) NOT NULL DEFAULT 'web'")


def downgrade() -> None:
    op.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS fp_marked_via")
    op.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS acknowledged_via")
