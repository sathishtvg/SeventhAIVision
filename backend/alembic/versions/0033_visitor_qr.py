"""Visitor pre-registration QR enhancements:
- visitors: add qr_token (unique), visitor_email, status
- visitor_logs: add checkin_method, qr_token_used

Revision ID: 0033
Revises: 0032
Create Date: 2026-06-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE visitors
            ADD COLUMN IF NOT EXISTS qr_token       VARCHAR(64) UNIQUE,
            ADD COLUMN IF NOT EXISTS visitor_email  VARCHAR(255),
            ADD COLUMN IF NOT EXISTS qr_email_sent_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS status         VARCHAR(20) NOT NULL DEFAULT 'pending';
        -- status: pending | arrived | departed | cancelled | expired

        -- Back-fill tokens for existing rows
        UPDATE visitors SET qr_token = gen_random_uuid()::text WHERE qr_token IS NULL;

        ALTER TABLE visitors ALTER COLUMN qr_token SET NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_visitors_qr_token ON visitors(qr_token);
        """
    )

    op.execute(
        """
        ALTER TABLE visitor_logs
            ADD COLUMN IF NOT EXISTS checkin_method  VARCHAR(20) NOT NULL DEFAULT 'manual',
            -- manual | qr_scan
            ADD COLUMN IF NOT EXISTS qr_token_used   VARCHAR(64);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE visitor_logs
            DROP COLUMN IF EXISTS checkin_method,
            DROP COLUMN IF EXISTS qr_token_used;
        ALTER TABLE visitors
            DROP COLUMN IF EXISTS qr_token,
            DROP COLUMN IF EXISTS visitor_email,
            DROP COLUMN IF EXISTS qr_email_sent_at,
            DROP COLUMN IF EXISTS status;
        """
    )
