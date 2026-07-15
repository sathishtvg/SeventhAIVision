"""0064 — Check-in/out selfie photos + liveness score + mock-location flag
on shifts (ShiftSecure Phase 2D).

Photo is stored on disk under ATTENDANCE_PHOTOS_ROOT; only the relative
path lives in the DB (same convention as employee_documents/evidence).
Liveness score and mock-location flag are kept for audit even though both
are hard-enforced at request time (start_shift/end_shift reject before
ever writing a row when either check fails) — the columns record the
passing values for the historical record, not a bypassable soft flag.
"""
from __future__ import annotations

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE shifts
          ADD COLUMN check_in_photo_path text,
          ADD COLUMN check_out_photo_path text,
          ADD COLUMN check_in_liveness_score numeric(5,4),
          ADD COLUMN check_out_liveness_score numeric(5,4),
          ADD COLUMN check_in_is_mock_location boolean,
          ADD COLUMN check_out_is_mock_location boolean
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE shifts
          DROP COLUMN IF EXISTS check_in_photo_path,
          DROP COLUMN IF EXISTS check_out_photo_path,
          DROP COLUMN IF EXISTS check_in_liveness_score,
          DROP COLUMN IF EXISTS check_out_liveness_score,
          DROP COLUMN IF EXISTS check_in_is_mock_location,
          DROP COLUMN IF EXISTS check_out_is_mock_location
    """)
