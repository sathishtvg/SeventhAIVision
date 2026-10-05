"""AI security intelligence, phase 5: how unusual, and how much it matters.

Additive: one new table, and four columns on `security_situations` — this
layer's own table, created in 0134. No table that existed before the layer is
altered.

AN ASSESSMENT IS WRITTEN ONCE AND NEVER CHANGED. Each time a situation is
assessed and the answer differs, a new row is added with the next `sequence`.
What the software believed at 02:18, before the drone arrived, stays on record
beside what it believed at 02:21: that history is evidence, and an officer who
decided at 02:19 decided on the first one. Nothing in the application updates or
deletes a row here.

IT CARRIES ITS OWN REASONS. `risk_factors` and `normality_factors` are the
points and the sentence behind each; `context` is what the engine knew about the
place and the moment when it scored — the unknowns included. The explanation an
officer reads later is read from this row, not recomputed from a database that
has since moved on.

THREE CONFIDENCES, SEPARATELY. `detection_confidence` is the model's, copied.
`correlation_confidence` is how firmly the events belong together.
`risk_confidence` is how complete the context was. They are three columns and
are never one number (the fourth, for a recommendation, comes with phase 6).

RISK IS NOT THE EVENT'S SEVERITY. `security_situations.severity` is the most
severe event's own severity, fixed by the rule that raised it. `risk_score` and
`risk_level` are this layer's judgement in context, and can be lower or higher.
The situation keeps the latest of them only so that a list can be sorted and
filtered; the assessment rows are the record.

Revision ID: 0135
Revises: 0134
"""
from alembic import op

revision = "0135"
down_revision = "0134"
branch_labels = None
depends_on = None

RISK_LEVELS = "'INFO','LOW','MEDIUM','HIGH','CRITICAL'"
# What a situation appears to be, as a code; `label` is the same in words.
KINDS = ("'GUARD_EMERGENCY','WEAPON','FIRE_SMOKE','DOOR_FORCED','ACCESS_REFUSED','ALARM','FALL',"
         "'BLOCK_LISTED','RESTRICTED_ZONE','PATROL_FINDING','CAMERA_OFFLINE','ACTIVITY'")


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE security_assessments (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id              UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            situation_id           UUID NOT NULL REFERENCES security_situations(id) ON DELETE CASCADE,
            sequence               INTEGER NOT NULL,
            assessed_at            TIMESTAMPTZ NOT NULL,
            kind                   VARCHAR(24) NOT NULL,
            label                  VARCHAR(120) NOT NULL,
            summary                TEXT NOT NULL,
            risk_score             SMALLINT NOT NULL,
            risk_level             VARCHAR(10) NOT NULL,
            risk_factors           JSONB NOT NULL,
            normality_score        SMALLINT,
            anomaly_score          SMALLINT,
            normality_factors      JSONB NOT NULL DEFAULT '[]'::jsonb,
            detection_confidence   NUMERIC(5,4),
            correlation_confidence NUMERIC(5,4),
            risk_confidence        NUMERIC(5,4) NOT NULL,
            context                JSONB NOT NULL,
            event_count            INTEGER NOT NULL,
            engine_version         VARCHAR(16) NOT NULL,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_secassess_sequence   UNIQUE (situation_id, sequence),
            CONSTRAINT ck_secassess_sequence   CHECK (sequence >= 1),
            CONSTRAINT ck_secassess_kind       CHECK (kind IN ({KINDS})),
            CONSTRAINT ck_secassess_score      CHECK (risk_score BETWEEN 0 AND 100),
            CONSTRAINT ck_secassess_level      CHECK (risk_level IN ({RISK_LEVELS})),
            -- Spelled out with IS NOT NULL: a CHECK passes when it is unknown, so
            -- "one of the two is null" has to be made false, not left null.
            CONSTRAINT ck_secassess_normality  CHECK (
                (normality_score IS NULL AND anomaly_score IS NULL)
                OR (normality_score IS NOT NULL AND anomaly_score IS NOT NULL
                    AND normality_score BETWEEN 0 AND 100 AND anomaly_score = 100 - normality_score)),
            CONSTRAINT ck_secassess_confidence CHECK (
                risk_confidence BETWEEN 0 AND 1
                AND (detection_confidence IS NULL OR detection_confidence BETWEEN 0 AND 1)
                AND (correlation_confidence IS NULL OR correlation_confidence BETWEEN 0 AND 1)),
            CONSTRAINT ck_secassess_factors    CHECK (jsonb_typeof(risk_factors) = 'array'
                                                      AND jsonb_array_length(risk_factors) >= 1)
        )
    """)
    op.execute("CREATE INDEX idx_secassess_situation ON security_assessments (situation_id, sequence DESC)")
    op.execute("ALTER TABLE security_assessments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security_assessments FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_security_assessments ON security_assessments
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    # Append-only for the application: it may add and read, never change or
    # remove. The REVOKE is what does it — the database's default privileges
    # hand the application UPDATE and DELETE on every new table.
    op.execute("REVOKE ALL ON security_assessments FROM svc_app")
    op.execute("GRANT SELECT, INSERT ON security_assessments TO svc_app")

    # The latest assessment, kept on the situation for sorting and filtering.
    op.execute(f"""
        ALTER TABLE security_situations
            ADD COLUMN risk_score    SMALLINT,
            ADD COLUMN risk_level    VARCHAR(10),
            ADD COLUMN assessed_at   TIMESTAMPTZ,
            ADD COLUMN assessment_id UUID REFERENCES security_assessments(id) ON DELETE SET NULL,
            ADD CONSTRAINT ck_secsit_risk_score CHECK (risk_score IS NULL OR risk_score BETWEEN 0 AND 100),
            ADD CONSTRAINT ck_secsit_risk_level CHECK (risk_level IS NULL OR risk_level IN ({RISK_LEVELS}))
    """)
    op.execute("CREATE INDEX idx_secsit_risk ON security_situations (tenant_id, status, risk_score DESC NULLS LAST)")
    # What the runner asks for on every pass: situations changed since they were assessed.
    op.execute("CREATE INDEX idx_secsit_unassessed ON security_situations (tenant_id, updated_at) "
               "WHERE assessed_at IS NULL OR assessed_at < updated_at")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_secsit_unassessed")
    op.execute("DROP INDEX IF EXISTS idx_secsit_risk")
    # DESTRUCTIVE: these four columns are this migration's own, on a table this
    # layer created in 0134; removing them loses only the cached latest
    # assessment, and the assessments themselves go with the table below.
    op.execute("""
        ALTER TABLE security_situations
            DROP CONSTRAINT IF EXISTS ck_secsit_risk_level,
            DROP CONSTRAINT IF EXISTS ck_secsit_risk_score,
            DROP COLUMN IF EXISTS assessment_id,
            DROP COLUMN IF EXISTS assessed_at,
            DROP COLUMN IF EXISTS risk_level,
            DROP COLUMN IF EXISTS risk_score
    """)
    op.execute("DROP TABLE IF EXISTS security_assessments CASCADE")
