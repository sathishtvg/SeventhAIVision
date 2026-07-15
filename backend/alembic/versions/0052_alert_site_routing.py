"""0052 — Site-aware alert routing (Gap 82)

Adds site_ids JSONB filter to notification_rules so email/SMS/webhook rules
can be scoped to specific sites (empty list = all sites, matching the
existing module_types/alert_codes filter convention).

Push-notification routing (guards on active shift at the alert's site) needs
no schema — it reads shifts + user_sites at dispatch time and keys Expo push
tokens per-user in Redis (push_tokens:{tenant_id}:{user_id}).
"""
from __future__ import annotations

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE notification_rules "
        "ADD COLUMN IF NOT EXISTS site_ids JSONB NOT NULL DEFAULT '[]'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notification_rules DROP COLUMN IF EXISTS site_ids")
