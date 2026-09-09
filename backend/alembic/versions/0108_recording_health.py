"""The recording health check was counting nothing and calling it fine.

platform_health.check_recording asked "how many streams should be recording,
and how many are" straight from streams and cameras. Both have FORCE ROW LEVEL
SECURITY, and the platform console runs on get_raw_db, which sets a zero-UUID
sentinel tenant — so the count came back zero and the check reported

    recording: ok — no streams are set to record continuously

on an installation with three cameras recording. The most dangerous possible
answer: a green light produced by a query that could not see anything.

Same fix as migration 0106, and the same reasoning. The function returns two
integers and nothing else.

It also counts COVERAGE rather than the instant: a rotating stream has no live
row for up to one supervisor interval, and counting that as a fault reports a
healthy installation as degraded several times an hour.

Revision ID: 0108
Revises: 0107
"""
from alembic import op

revision = "0108"
down_revision = "0107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # COVERED, NOT "RECORDING RIGHT NOW". A stream that is rotating has no row
    # in 'recording' between the old segment closing and the supervisor opening
    # the next one — up to a full supervisor interval. Counting the instant
    # reports a healthy installation as degraded several times an hour, and a
    # health check that cries wolf during normal operation is one nobody reads.
    #
    # So a stream counts as covered if it is recording OR finished a segment
    # within the grace window below. What that measures is continuity of
    # footage, which is the thing anyone actually cares about.
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_recording_health()
        RETURNS TABLE (expected BIGINT, live BIGINT)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT count(*) FILTER (WHERE s.continuous_recording AND c.is_active),
                   count(*) FILTER (WHERE s.continuous_recording
                                      AND c.is_active
                                      AND EXISTS (
                                            SELECT 1 FROM recordings r
                                             WHERE r.stream_id = s.id
                                               AND (r.status = 'recording'
                                                    OR r.ended_at > now()
                                                       - interval '5 minutes')))
              FROM streams s JOIN cameras c ON c.id = s.camera_id
        $$;
    """)
    op.execute("REVOKE ALL ON FUNCTION platform_recording_health() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION platform_recording_health() TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS platform_recording_health()")
