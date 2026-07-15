"""0068 — SOP Training quiz engine (ShiftSecure Phase 6)

training_courses/training_records (migration 0031) are a manual log/
cert-tracker — an admin asserts "guard X completed course Y" by hand, with
no actual quiz content anywhere. This migration adds a real question bank
and guard-taken attempts on top; passing an attempt auto-writes into the
existing training_records table (via services/training.py::compute_expiry,
shared with the pre-existing manual path) rather than replacing it, so
non-quiz training (e.g. an in-person fire drill) keeps working exactly as
it does today.

No new permission codes — reuses training:read (guards see/take quizzes)
and training:manage (question-bank authoring, same boundary as course
CRUD).
"""
from __future__ import annotations

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE training_questions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            course_id       UUID NOT NULL REFERENCES training_courses(id) ON DELETE CASCADE,
            question_text   TEXT NOT NULL,
            options         JSONB NOT NULL,
            correct_index   INTEGER NOT NULL,
            points          INTEGER NOT NULL DEFAULT 1,
            sort_order      INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_training_questions_course ON training_questions(tenant_id, course_id)")
    op.execute("ALTER TABLE training_questions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE training_questions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_training_questions ON training_questions
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        CREATE TABLE training_attempts (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            course_id           UUID NOT NULL REFERENCES training_courses(id) ON DELETE CASCADE,
            guard_user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status              VARCHAR(20) NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress','submitted')),
            answers             JSONB NOT NULL DEFAULT '{}',
            score               INTEGER,
            passed              BOOLEAN,
            training_record_id  UUID REFERENCES training_records(id) ON DELETE SET NULL,
            started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            submitted_at        TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX idx_training_attempts_guard ON training_attempts(tenant_id, guard_user_id)")
    op.execute("CREATE INDEX idx_training_attempts_course ON training_attempts(tenant_id, course_id)")
    op.execute("ALTER TABLE training_attempts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE training_attempts FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_training_attempts ON training_attempts
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS training_attempts")
    op.execute("DROP TABLE IF EXISTS training_questions")
