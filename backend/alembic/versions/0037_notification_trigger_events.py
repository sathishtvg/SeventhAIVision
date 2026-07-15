"""notification_trigger_events — extend notification_rules with trigger_events,
generalize notification_logs to support non-alert events.

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add trigger_events to notification_rules.
    # [] = legacy "alert_created only" behaviour (fully backwards-compatible).
    # Non-empty list = explicit allowlist of event_type strings to trigger this rule.
    # Supports '*' wildcard to match every event type.
    op.execute("""
        ALTER TABLE notification_rules
            ADD COLUMN trigger_events JSONB NOT NULL DEFAULT '[]'
    """)

    # Make notification_logs.alert_id nullable so non-alert events can be logged.
    op.execute("""
        ALTER TABLE notification_logs
            ALTER COLUMN alert_id DROP NOT NULL
    """)

    # Add event_type column to notification_logs for clarity.
    op.execute("""
        ALTER TABLE notification_logs
            ADD COLUMN event_type VARCHAR(50) NOT NULL DEFAULT 'alert_created'
    """)

    # Backfill event_type on existing rows (they are all alert events).
    op.execute("""
        UPDATE notification_logs SET event_type = 'alert_created' WHERE event_type = 'alert_created'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE notification_logs DROP COLUMN IF EXISTS event_type")
    op.execute("ALTER TABLE notification_logs ALTER COLUMN alert_id SET NOT NULL")
    op.execute("ALTER TABLE notification_rules DROP COLUMN IF EXISTS trigger_events")
