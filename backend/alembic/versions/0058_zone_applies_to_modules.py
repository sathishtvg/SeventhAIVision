"""0058 — Zone applies_to_modules

Restricted zones were implicitly intrusion-only, but intrusion_task.py and
behavior_task.py both already query the exact same
`SELECT id, polygon, severity FROM restricted_zones WHERE tenant_id=%s AND
camera_id=%s AND is_active=TRUE` — a zone drawn once already gates both
pipelines with no way to scope it to just one. This column lets an operator
pick which module(s) a drawn zone applies to (Intrusion / Behavior — the
only two workers that currently read restricted_zones).

Defaults to '["intrusion"]' so every existing zone keeps its current
behavior unchanged after this migration runs.
"""
from __future__ import annotations

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE restricted_zones ADD COLUMN IF NOT EXISTS "
        "applies_to_modules JSONB NOT NULL DEFAULT '[\"intrusion\"]'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE restricted_zones DROP COLUMN IF EXISTS applies_to_modules")
