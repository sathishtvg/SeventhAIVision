"""Security & Ops Foundation:
- users: totp_secret, totp_enabled, expo_push_token (mobile push notifications)
- alerts: false_positive status + fp_reason column
- cameras: last_offline_alert_at (rate-limit offline alert spam)
- New permissions: 2fa:manage (self-service), camera:offline:alert

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-22
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users: TOTP 2FA fields ────────────────────────────────────────────────
    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN totp_secret TEXT,
            ADD COLUMN totp_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN totp_verified_at TIMESTAMPTZ,
            ADD COLUMN expo_push_token TEXT;
        """
    )

    # ── alerts: false_positive status + reason ────────────────────────────────
    op.execute(
        """
        ALTER TABLE alerts
            ADD COLUMN fp_reason TEXT,
            ADD COLUMN fp_marked_by_user_id UUID REFERENCES users(id),
            ADD COLUMN fp_marked_at TIMESTAMPTZ;
        """
    )

    # ── cameras: rate-limit offline alerts ────────────────────────────────────
    op.execute(
        """
        ALTER TABLE cameras
            ADD COLUMN last_offline_alert_at TIMESTAMPTZ;
        """
    )

    # ── new permissions ───────────────────────────────────────────────────────
    op.execute(
        """
        INSERT INTO permissions (code, description, category) VALUES
            ('2fa:manage',  'Enable / disable own 2FA',        'auth'),
            ('push:register', 'Register mobile push token',    'auth')
        ON CONFLICT (code) DO NOTHING;

        -- Grant 2fa:manage to every role (self-service feature)
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE p.code IN ('2fa:manage', 'push:register')
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE users
            DROP COLUMN IF EXISTS totp_secret,
            DROP COLUMN IF EXISTS totp_enabled,
            DROP COLUMN IF EXISTS totp_verified_at,
            DROP COLUMN IF EXISTS expo_push_token;

        ALTER TABLE alerts
            DROP COLUMN IF EXISTS fp_reason,
            DROP COLUMN IF EXISTS fp_marked_by_user_id,
            DROP COLUMN IF EXISTS fp_marked_at;

        ALTER TABLE cameras
            DROP COLUMN IF EXISTS last_offline_alert_at;
        """
    )
