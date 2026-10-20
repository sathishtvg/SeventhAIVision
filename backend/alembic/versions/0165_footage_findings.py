"""Footage Review: what the worker found in a video - people and vehicles over time, and a person's verdict.

Phase 3 of Footage Review (docs/plans/2026-10-20-footage-review-design.md,
section 3). The footage worker counts the people and the vehicles in each
second it samples (on `footage_frames`) and joins the seconds in which some
were seen into events (`footage_findings`, kinds PEOPLE and VEHICLES). Phase 4
adds possible altercations to the same table.

A FINDING IS ITS VIDEO'S and goes with it. The worker writes and replaces them;
the application changes only a person's verdict on one - confirmed or not, with
a note, by whom and when - and nothing else.

A COUNT IS HOW MANY WERE SEEN IN ONE FRAME, never who.

Revision ID: 0165
Revises: 0164
"""
from alembic import op

revision = "0165"
down_revision = "0164"
branch_labels = None
depends_on = None

KINDS = ("PEOPLE", "VEHICLES", "ALTERCATION")
VERDICTS = ("CONFIRMED", "REJECTED")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute("""
        ALTER TABLE footage_frames
            ADD COLUMN people   SMALLINT,
            ADD COLUMN vehicles SMALLINT
    """)
    op.execute(f"""
        CREATE TABLE footage_findings (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            video_id           UUID NOT NULL REFERENCES footage_videos(id) ON DELETE CASCADE,
            kind               VARCHAR(12) NOT NULL,
            start_second       INTEGER NOT NULL,
            end_second         INTEGER NOT NULL,
            best_second        INTEGER NOT NULL,
            score              NUMERIC(6, 4) NOT NULL,
            most               SMALLINT,
            reasons            JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            model_version      VARCHAR(80) NOT NULL,
            verdict            VARCHAR(12),
            verdict_note       TEXT,
            verdict_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            verdict_at         TIMESTAMPTZ,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_footage_finding_kind    CHECK (kind IN ({_in(KINDS)})),
            CONSTRAINT ck_footage_finding_span    CHECK (start_second >= 0 AND end_second >= start_second
                                                         AND best_second BETWEEN start_second AND end_second),
            CONSTRAINT ck_footage_finding_verdict CHECK (verdict IS NULL OR verdict IN ({_in(VERDICTS)})),
            CONSTRAINT ck_footage_finding_judged  CHECK ((verdict IS NULL) = (verdict_at IS NULL))
        )
    """)
    op.execute("CREATE INDEX idx_footage_findings_video ON footage_findings (video_id, kind, start_second)")
    op.execute("CREATE INDEX idx_footage_findings_tenant ON footage_findings (tenant_id, kind, created_at DESC)")
    op.execute("ALTER TABLE footage_findings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE footage_findings FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_footage_findings ON footage_findings
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("REVOKE ALL ON footage_findings FROM svc_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON footage_findings TO svc_app")
    # A person's verdict, and nothing else, is changed after the worker wrote a finding.
    op.execute("GRANT UPDATE (verdict, verdict_note, verdict_by_user_id, verdict_at) ON footage_findings TO svc_app")


def downgrade() -> None:
    op.execute("DROP TABLE footage_findings")
    op.execute("ALTER TABLE footage_frames DROP COLUMN vehicles, DROP COLUMN people")
