"""A checksum on the patrol report, so the document can be checked later.

THE SNAPSHOT ALREADY HAS ONE AND THE REPORT DOES NOT. 0116 gave
virtual_patrol_session_cameras a snapshot_checksum, and vpatrol_snapshot writes
it on every capture -- 15 of 15 on this database. virtual_patrol_reports, added
in the same round, records storage_path and file_bytes and nothing about the
content. So the frame is verifiable and the document built from it is not, which
is the wrong way round: the PDF is the artefact an agency actually hands to a
client.

WHAT THIS DOES AND DOES NOT PROVE, stated here because overstating it would be
worse than leaving it out. A checksum in the same database as the row proves the
FILE has not changed since it was written -- a truncated write, a full volume, a
corrupted disk, a file replaced on storage. It does NOT prove that nobody with
database access altered the row and the file together. Tamper-evidence against
someone holding the database needs the chained approach audit_logs uses, or an
anchor outside the system. This is file integrity, and the code says so.

Revision ID: 0121
Revises: 0120
"""
from alembic import op

revision = "0121"
down_revision = "0120"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, and deliberately not backfilled. A checksum computed now for a
    # report generated last month would attest to whatever the file happens to
    # contain today, which is precisely the thing a checksum is supposed to
    # detect. A report with no checksum is honestly unverifiable; one with a
    # checksum invented after the fact is falsely verifiable.
    op.execute("""
        ALTER TABLE virtual_patrol_reports
            ADD COLUMN IF NOT EXISTS checksum_sha256 VARCHAR(64);
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE virtual_patrol_reports "
               "DROP COLUMN IF EXISTS checksum_sha256;")
