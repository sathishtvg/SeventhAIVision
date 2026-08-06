"""Plate-crop evidence for every LPR read.

WHY THIS EXISTS
    An LPR read already stores an evidence snapshot, but it stores the FULL
    FRAME. That answers "which vehicle, which lane, what time" — it does not
    answer "is SGB1234X what the camera actually saw", because at frame
    resolution the plate is a smudge.

    That matters most at the visitor desk. When a vehicle enters, the plate
    read auto-creates the visitor row and starts the parking clock; an operator
    then fills in who the vehicle is. Today they are asked to confirm a
    vehicle's identity against a text string with no image to check it against.
    A misread character means the wrong vehicle is billed, or a blocklisted
    plate is waved through as a typo.

    The plate crop already exists in the worker — it is cut out of the frame to
    run OCR on (lpr_task.py), and then discarded. This stores it.

WHY A DISCRIMINATOR COLUMN
    Both images are worth keeping and they answer different questions, so a
    detection now has two evidence rows. Without a marker the API cannot tell
    them apart, and "show me the plate" would be a guess based on file size or
    insert order. `capture_kind` makes it explicit.

    Existing rows are backfilled to 'frame', which is what they are — every
    evidence row written before this migration is a full frame.

Revision ID: 0082
Revises: 0081
"""
from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # evidence is RANGE-partitioned on captured_at; adding to the parent
    # cascades to every partition.
    op.execute("""
        ALTER TABLE evidence
          ADD COLUMN IF NOT EXISTS capture_kind VARCHAR(20) NOT NULL DEFAULT 'frame'
    """)
    op.execute("""
        ALTER TABLE evidence
          ADD CONSTRAINT ck_evidence_capture_kind
          CHECK (capture_kind IN ('frame','plate_crop','face_crop','clip'))
    """)
    # The lookup this exists to serve: "the plate proof for this detection".
    # Partial, because plate crops are a small slice of all evidence.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_evidence_detection_capture_kind
            ON evidence(detection_id, capture_kind)
            WHERE capture_kind <> 'frame'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_evidence_detection_capture_kind")
    op.execute("ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_evidence_capture_kind")
    # Plate crops become indistinguishable from frames once the column is gone,
    # so remove them rather than leave a detection with two rows that claim to
    # be the same thing. The frame — the pre-existing behaviour — is kept.
    op.execute("DELETE FROM evidence WHERE capture_kind = 'plate_crop'")
    op.execute("ALTER TABLE evidence DROP COLUMN IF EXISTS capture_kind")
