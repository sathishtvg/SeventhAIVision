"""0054 — Continuous recording flag (Gap 85)

streams.continuous_recording marks a stream for 24/7 segment recording.
The API-process supervisor (app/services/continuous_recording.py) keeps an
active recording running for every flagged stream, rotating segments every
RECORDING_SEGMENT_MINUTES (default 15) and purging segments older than the
tenant's recording.retention_days setting (default 7 days).
"""
from __future__ import annotations

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE streams "
        "ADD COLUMN IF NOT EXISTS continuous_recording BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_streams_continuous "
        "ON streams(tenant_id) WHERE continuous_recording = TRUE"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_streams_continuous")
    op.execute("ALTER TABLE streams DROP COLUMN IF EXISTS continuous_recording")
