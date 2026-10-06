"""AI security intelligence, phase 14: what a person says a situation turned out to be.

Additive: one new table. Nothing that existed before the layer is altered.

  AI recommendation ─► human decision ─► actual outcome
                                          └─ a reviewer's own statement of it

A REVIEW IS A PERSON'S STATEMENT ABOUT A CLOSED MATTER. Once a situation has
been closed, someone with the authority to approve decisions can say what it
turned out to be, whether the layer's assessment was about right, and whether
what it suggested was useful. That, beside the suggestion and the decision the
layer already keeps, is the feedback dataset.

NOTHING LEARNS FROM IT BY ITSELF. No model is trained on these rows, no weight
is changed by them and no rule reads them: they are for people to read, count
and export. A test holds that the runner cannot even see the table's name.

WRITTEN ONCE. The application's role may add and read reviews and can neither
change nor remove one. One review per reviewer per situation.

Revision ID: 0140
Revises: 0139
"""
from alembic import op

revision = "0140"
down_revision = "0139"
branch_labels = None
depends_on = None

OUTCOMES = "'REAL_INCIDENT','AUTHORISED_ACTIVITY','FALSE_DETECTION','EQUIPMENT_FAULT','UNDETERMINED'"
ASSESSMENT_VERDICTS = "'ABOUT_RIGHT','TOO_HIGH','TOO_LOW','WRONG_KIND'"
RECOMMENDATION_VERDICTS = "'USEFUL','NOT_USEFUL','MISSED_A_STEP','NOTHING_SUGGESTED'"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE security_feedback (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id              UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            situation_id           UUID NOT NULL REFERENCES security_situations(id) ON DELETE CASCADE,
            -- The assessment that stood when the review was made.
            assessment_id          UUID REFERENCES security_assessments(id) ON DELETE SET NULL,
            outcome                VARCHAR(24) NOT NULL,
            assessment_verdict     VARCHAR(16),
            recommendation_verdict VARCHAR(20),
            note                   TEXT,
            reviewer_user_id       UUID REFERENCES users(id) ON DELETE SET NULL,
            reviewer_role          SMALLINT NOT NULL,
            request_id             VARCHAR(64),
            reviewed_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_secfb_outcome        CHECK (outcome IN ({OUTCOMES})),
            CONSTRAINT ck_secfb_assessment     CHECK (assessment_verdict IS NULL
                                                      OR assessment_verdict IN ({ASSESSMENT_VERDICTS})),
            CONSTRAINT ck_secfb_recommendation CHECK (recommendation_verdict IS NULL
                                                      OR recommendation_verdict IN ({RECOMMENDATION_VERDICTS})),
            -- "Undetermined" has to say what is still not known.
            CONSTRAINT ck_secfb_undetermined   CHECK (outcome <> 'UNDETERMINED'
                                                      OR (note IS NOT NULL AND btrim(note) <> ''))
        )
    """)
    op.execute("CREATE INDEX idx_secfb_situation ON security_feedback (situation_id, reviewed_at)")
    op.execute("CREATE INDEX idx_secfb_tenant_time ON security_feedback (tenant_id, reviewed_at)")
    # One review per reviewer per situation: a second thought is a second person's.
    op.execute("CREATE UNIQUE INDEX uq_secfb_reviewer ON security_feedback (situation_id, reviewer_user_id) "
               "WHERE reviewer_user_id IS NOT NULL")
    op.execute("ALTER TABLE security_feedback ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security_feedback FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_security_feedback ON security_feedback
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    # Added to and read. The REVOKE is what does it: default privileges hand
    # the application UPDATE and DELETE on every new table.
    op.execute("REVOKE ALL ON security_feedback FROM svc_app")
    op.execute("GRANT SELECT, INSERT ON security_feedback TO svc_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS security_feedback CASCADE")
