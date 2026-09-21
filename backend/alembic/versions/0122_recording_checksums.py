"""Give recordings the checksum that verify_checksums has been promising.

recording_policies HAS OFFERED verify_checksums SINCE THE SETTING WAS ADDED. It
defaults to TRUE, it is stored per site, it is returned to the UI and shown to
whoever configures a site -- and it appears in exactly one place in the
application: a SELECT column list. Nothing reads it to decide anything.

Worse than a setting that does nothing: `recordings` has no checksum column at
all, so there has never been anything to verify. An operator looking at a site
policy sees integrity checking switched on, and no integrity has ever been
checked or even recorded. A control that reports itself as active while doing
nothing is more dangerous than an absent one, because it is relied upon.

This adds the column. 0122's companion changes compute it when a recording is
finalised and verify it where the policy asks for it.

NOT BACKFILLED, for the same reason 0121 was not. A hash computed today for a
segment written last week attests to whatever that file contains today, which is
precisely the thing a checksum exists to detect. An unverifiable recording is
honest; a falsely verifiable one is not.

Revision ID: 0122
Revises: 0121
"""
from alembic import op

revision = "0122"
down_revision = "0121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE recordings
            ADD COLUMN IF NOT EXISTS checksum_sha256 VARCHAR(64),
            -- When the hash was last confirmed against the file. NULL means
            -- never, which is the state every existing row is honestly in.
            ADD COLUMN IF NOT EXISTS checksum_verified_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS checksum_status VARCHAR(20);
    """)

    op.execute("""
        ALTER TABLE recordings
            ADD CONSTRAINT ck_recordings_checksum_status CHECK (
                checksum_status IS NULL
                OR checksum_status IN ('PASSED', 'MISMATCH', 'FILE_MISSING'));
    """)

    # The sweep looks for completed recordings that have a hash and have either
    # never been checked or were checked longest ago.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_recordings_needing_verification
            ON recordings (tenant_id, checksum_verified_at NULLS FIRST)
         WHERE status = 'completed' AND checksum_sha256 IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS idx_recordings_needing_verification;
        ALTER TABLE recordings
            DROP CONSTRAINT IF EXISTS ck_recordings_checksum_status;
        ALTER TABLE recordings
            DROP COLUMN IF EXISTS checksum_status,
            DROP COLUMN IF EXISTS checksum_verified_at,
            DROP COLUMN IF EXISTS checksum_sha256;
    """)
