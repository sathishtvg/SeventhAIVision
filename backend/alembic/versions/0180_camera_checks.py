"""A camera's frame rate, delay and recording gaps, measured.

Phase 6 of closing the feature reference's known limits (docs/plans/2026-11-04-device-health-measured-design.md).
Nothing here changes a camera, a stream or a recording.

`camera_check_settings` - one row per organisation, made the first time its limits are changed, else the defaults
apply. Changed by whoever may change the organisation's settings.

`camera_checks` - each 10-second check of a stream: what was received and how the camera answered, or why the check
could not run, and which limits it broke when it was made. Removed after the organisation's device health history
period, or 90 days; a stream's latest check never.

`recording_switches` - each time a stream's "record all the time" was switched on or off, noted by a trigger on
`streams` (approved by the owner as a change to that table's behaviour; what the switch does is unchanged). The
streams that record all the time when this runs are noted as switched on now: nothing before it is counted.

`recording_watch` - one row per stream the gap pass watches: when it began watching, and since when the camera has
been switched on and its site's recording policy has asked for recording, as the pass saw it.

`recording_gaps` - each stretch in which a stream that should have been recording has no usable recording: no piece,
a piece that holds nothing, or a piece whose file is missing. An open gap has no end yet. Kept once per stream and
start.
"""
from alembic import op

revision = "0180"
down_revision = "0179"
branch_labels = None
depends_on = None

RESULTS = ("OK", "NO_CONNECTION", "SIGN_IN_REFUSED", "SECOND_CONNECTION_REFUSED", "NO_VIDEO", "TIMED_OUT", "FAILED")
GAP_KINDS = ("NOT_RECORDED", "EMPTY", "FILE_MISSING")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _secured(table: str, grants: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute(f"REVOKE ALL ON {table} FROM svc_app")
    op.execute(f"GRANT {grants} ON {table} TO svc_app")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE camera_check_settings (
            tenant_id             UUID         PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            least_rate_pct        SMALLINT     NOT NULL DEFAULT 70   CHECK (least_rate_pct BETWEEN 10 AND 100),
            most_stall_ms         INTEGER      NOT NULL DEFAULT 2000 CHECK (most_stall_ms BETWEEN 200 AND 60000),
            most_missed_pct       NUMERIC(4,1) NOT NULL DEFAULT 2.0  CHECK (most_missed_pct BETWEEN 0 AND 50),
            most_answer_ms        INTEGER      NOT NULL DEFAULT 500  CHECK (most_answer_ms BETWEEN 10 AND 10000),
            most_first_picture_ms INTEGER      NOT NULL DEFAULT 5000 CHECK (most_first_picture_ms BETWEEN 500 AND 60000),
            most_gap_s            INTEGER      NOT NULL DEFAULT 60   CHECK (most_gap_s BETWEEN 10 AND 3600),
            updated_by_user_id    UUID         REFERENCES users(id) ON DELETE SET NULL,
            updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
    _secured("camera_check_settings", "SELECT, INSERT, UPDATE")

    op.execute(f"""
        CREATE TABLE camera_checks (
            id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id        UUID         NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            stream_id        UUID         NOT NULL REFERENCES streams(id) ON DELETE CASCADE,
            checked_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
            result           VARCHAR(28)  NOT NULL CHECK (result IN ({_in(RESULTS)})),
            listened_ms      INTEGER      CHECK (listened_ms >= 0),
            frames           INTEGER      CHECK (frames >= 0),
            -- The rate the camera's own frame times give, and the rate that arrived.
            stated_rate      NUMERIC(6,2) CHECK (stated_rate >= 0),
            received_rate    NUMERIC(6,2) CHECK (received_rate >= 0),
            longest_stall_ms INTEGER      CHECK (longest_stall_ms >= 0),
            missed_pct       NUMERIC(5,2) CHECK (missed_pct BETWEEN 0 AND 100),
            answer_ms        INTEGER      CHECK (answer_ms >= 0),
            first_picture_ms INTEGER      CHECK (first_picture_ms >= 0),
            -- The limits it broke when it was made: [{{"limit": key, "text": "..."}}].
            breaks           JSONB        NOT NULL DEFAULT '[]'::jsonb,
            CONSTRAINT ck_camchk_figures CHECK (result <> 'OK' OR frames IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX idx_camchk_stream ON camera_checks (stream_id, checked_at DESC)")
    op.execute("CREATE INDEX idx_camchk_camera ON camera_checks (camera_id, checked_at DESC)")
    op.execute("CREATE INDEX idx_camchk_tenant ON camera_checks (tenant_id, checked_at)")
    _secured("camera_checks", "SELECT, INSERT, DELETE")

    op.execute("""
        CREATE TABLE recording_switches (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            stream_id   UUID        NOT NULL REFERENCES streams(id) ON DELETE CASCADE,
            camera_id   UUID        NOT NULL,
            switched_on BOOLEAN     NOT NULL,
            at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE INDEX idx_recswitch_stream ON recording_switches (stream_id, at DESC)")
    _secured("recording_switches", "SELECT")
    # The note is made by the database whoever changes the switch, so it cannot be left out; the function runs as its
    # owner to write it. It changes nothing in `streams`.
    op.execute("""
        CREATE FUNCTION note_recording_switch() RETURNS trigger
            LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.continuous_recording THEN
                    INSERT INTO recording_switches (tenant_id, stream_id, camera_id, switched_on)
                    VALUES (NEW.tenant_id, NEW.id, NEW.camera_id, TRUE);
                END IF;
            ELSIF NEW.continuous_recording IS DISTINCT FROM OLD.continuous_recording THEN
                INSERT INTO recording_switches (tenant_id, stream_id, camera_id, switched_on)
                VALUES (NEW.tenant_id, NEW.id, NEW.camera_id, COALESCE(NEW.continuous_recording, FALSE));
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION note_recording_switch() FROM PUBLIC")
    op.execute("""
        CREATE TRIGGER trg_note_recording_switch
            AFTER INSERT OR UPDATE OF continuous_recording ON streams
            FOR EACH ROW EXECUTE FUNCTION note_recording_switch()
    """)
    op.execute("""
        INSERT INTO recording_switches (tenant_id, stream_id, camera_id, switched_on)
        SELECT tenant_id, id, camera_id, TRUE FROM streams WHERE continuous_recording
    """)

    op.execute("""
        CREATE TABLE recording_watch (
            stream_id        UUID        PRIMARY KEY REFERENCES streams(id) ON DELETE CASCADE,
            tenant_id        UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id        UUID        NOT NULL,
            first_watched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- Since when the camera has been switched on and its site's policy has asked for recording, as seen.
            others_ok_since  TIMESTAMPTZ,
            seen_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    _secured("recording_watch", "SELECT, INSERT, UPDATE")

    op.execute(f"""
        CREATE TABLE recording_gaps (
            id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_id          UUID        NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            stream_id          UUID        NOT NULL REFERENCES streams(id) ON DELETE CASCADE,
            kind               VARCHAR(16) NOT NULL CHECK (kind IN ({_in(GAP_KINDS)})),
            started_at         TIMESTAMPTZ NOT NULL,
            ended_at           TIMESTAMPTZ,
            seconds            INTEGER     NOT NULL CHECK (seconds >= 0),
            camera_was_offline BOOLEAN     NOT NULL DEFAULT FALSE,
            limit_s            INTEGER     NOT NULL,
            over_limit         BOOLEAN     NOT NULL,
            found_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_recgap_order CHECK (ended_at IS NULL OR ended_at >= started_at)
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_recgap_once ON recording_gaps (stream_id, started_at)")
    op.execute("CREATE INDEX idx_recgap_camera ON recording_gaps (camera_id, started_at DESC)")
    op.execute("CREATE INDEX idx_recgap_tenant ON recording_gaps (tenant_id, started_at)")
    _secured("recording_gaps", "SELECT, INSERT, UPDATE, DELETE")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS recording_gaps")
    op.execute("DROP TABLE IF EXISTS recording_watch")
    op.execute("DROP TRIGGER IF EXISTS trg_note_recording_switch ON streams")
    op.execute("DROP FUNCTION IF EXISTS note_recording_switch()")
    op.execute("DROP TABLE IF EXISTS recording_switches")
    op.execute("DROP TABLE IF EXISTS camera_checks")
    op.execute("DROP TABLE IF EXISTS camera_check_settings")
