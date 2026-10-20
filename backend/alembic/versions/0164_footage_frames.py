"""Footage Review: one frame a second of each video, as a picture and a vector, to search by description.

Phase 2 of Footage Review (docs/plans/2026-10-20-footage-review-design.md,
sections 1 and 3). The footage worker (ai-worker/worker/footage) samples one
frame a second, keeps it as a thumbnail and stores the SigLIP 2 vector of it
here; a typed description is turned into a vector of the same kind and the
nearest frames are found through the HNSW index.

A FRAME IS ITS VIDEO'S: it goes when the video goes (ON DELETE CASCADE), so the
30-day removal of a video takes its frames with it. Each frame says which model
made its vector: a search compares only vectors of one model.

WHICH VIDEOS ARE WAITING, across every organisation, is the one question the
worker asks without an organisation: footage_waiting() answers it as the
database's owner, with ids and states and nothing else. Everything after that
the worker does under the video's own organisation.

What the worker found out about a video is kept on it: the codec, how many
frames were indexed and by which model, and whether a browser can play the
original, a copy the worker made, or neither.

Revision ID: 0164
Revises: 0163
"""
from alembic import op

revision = "0164"
down_revision = "0163"
branch_labels = None
depends_on = None

PLAYABLE = ("ORIGINAL", "PROXY", "NONE")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE footage_frames (
            tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            video_id      UUID NOT NULL REFERENCES footage_videos(id) ON DELETE CASCADE,
            second        INTEGER NOT NULL,
            thumb_path    VARCHAR(500) NOT NULL,
            embedding     vector(768) NOT NULL,
            model_version VARCHAR(80) NOT NULL,
            PRIMARY KEY (video_id, second),
            CONSTRAINT ck_footage_frame_second CHECK (second >= 0)
        )
    """)
    op.execute("CREATE INDEX idx_footage_frames_tenant ON footage_frames (tenant_id, video_id)")
    op.execute("CREATE INDEX idx_footage_frames_embedding ON footage_frames USING hnsw (embedding vector_cosine_ops)")
    op.execute("ALTER TABLE footage_frames ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE footage_frames FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_footage_frames ON footage_frames
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("REVOKE ALL ON footage_frames FROM svc_app")
    # The worker writes them and clears them before a video is analysed again; nothing changes one.
    op.execute("GRANT SELECT, INSERT, DELETE ON footage_frames TO svc_app")

    playable = ", ".join(f"'{p}'" for p in PLAYABLE)
    op.execute(f"""
        ALTER TABLE footage_videos
            ADD COLUMN codec          VARCHAR(40),
            ADD COLUMN frames_indexed INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN index_model    VARCHAR(80),
            ADD COLUMN playable       VARCHAR(10),
            ADD COLUMN proxy_path     VARCHAR(500),
            ADD CONSTRAINT ck_footage_playable CHECK (playable IS NULL OR playable IN ({playable}))
    """)

    op.execute("""
        CREATE FUNCTION footage_waiting(p_stale interval)
        RETURNS TABLE (tenant_id uuid, video_id uuid, status varchar, attempts smallint)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT v.tenant_id, v.id, v.status, v.attempts FROM footage_videos v
             WHERE v.status = 'QUEUED' OR (v.status = 'PROCESSING' AND v.updated_at < now() - p_stale)
             ORDER BY v.updated_at, v.id
             LIMIT 100
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION footage_waiting(interval) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION footage_waiting(interval) TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION footage_waiting(interval)")
    op.execute("""
        ALTER TABLE footage_videos
            DROP CONSTRAINT ck_footage_playable,
            DROP COLUMN proxy_path, DROP COLUMN playable, DROP COLUMN index_model, DROP COLUMN frames_indexed,
            DROP COLUMN codec
    """)
    op.execute("DROP TABLE footage_frames")
