"""Footage Review: a saved camera recording, copied in to be analysed.

Phase 5 of Footage Review (docs/plans/2026-10-20-footage-review-design.md,
section 2). A person who may upload footage and read recordings picks a
finished recording; it is copied into Footage Review, where the 30-day rule
applies to the copy. The recording itself keeps its own retention.

A video's `source` may now be RECORDING, and says which recording it was copied
from. The copy keeps nothing alive: when the recording is removed by its own
retention, `recording_id` is set to NULL and the copy stays until its own 30
days are up. Recordings are masked when they are recorded (PRIVACY_MASKING.md),
so the copy is masked too.

Revision ID: 0166
Revises: 0165
"""
from alembic import op

revision = "0166"
down_revision = "0165"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE footage_videos DROP CONSTRAINT ck_footage_source")
    op.execute("ALTER TABLE footage_videos ADD CONSTRAINT ck_footage_source CHECK (source IN ('UPLOAD', 'RECORDING'))")
    op.execute("ALTER TABLE footage_videos ADD COLUMN recording_id UUID REFERENCES recordings(id) ON DELETE SET NULL")
    op.execute("CREATE INDEX idx_footage_videos_recording ON footage_videos (recording_id) WHERE recording_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DELETE FROM footage_videos WHERE source = 'RECORDING'")
    op.execute("ALTER TABLE footage_videos DROP COLUMN recording_id")
    op.execute("ALTER TABLE footage_videos DROP CONSTRAINT ck_footage_source")
    op.execute("ALTER TABLE footage_videos ADD CONSTRAINT ck_footage_source CHECK (source IN ('UPLOAD'))")
