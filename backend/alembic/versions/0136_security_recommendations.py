"""AI security intelligence, phase 6: what the layer suggests doing.

Additive: one new table. Nothing that existed before the layer is altered.

A RECOMMENDATION IS A SUGGESTION AND NOTHING ELSE. A row here causes nothing to
happen: no guard is sent, no incident is opened, nobody is told. It is what the
layer proposes an officer do next, with the reason, and it stays a proposal
until a person decides (phase 7) — and that decision is its own record in its
own table, so the two can never be mistaken for each other.

ONE SET PER ASSESSMENT, WRITTEN ONCE. Each assessment gets its recommendations
once; a later assessment gets a new set. Nothing here is updated or deleted by
the application, so what was suggested at 02:18 stays on record beside what was
suggested at 02:21. Which set is current is not a column: it is the set that
belongs to the situation's latest assessment.

WHAT CANNOT BE DONE IS SAID, NOT HIDDEN. A step the rules would suggest but
that is not possible right now — nobody on shift to send, no drone ready, an
incident already open — is kept with `available = false` and the reason in
words, so an officer is never offered a button that cannot work, nor left to
wonder why the obvious step is missing.

THE FOURTH CONFIDENCE. `confidence` is how sure the layer is of the suggestion,
and `confidence_limited_by` says what held it down: the rule itself, or the
detection, correlation or risk confidence it rests on. It is stored beside the
other three and never merged with them.

Revision ID: 0136
Revises: 0135
"""
from alembic import op

revision = "0136"
down_revision = "0135"
branch_labels = None
depends_on = None

ACTIONS = ("'MONITOR','VERIFY','VIEW_CAMERA','VERIFY_WITH_DRONE','DISPATCH_GUARD','ESCALATE','INVESTIGATE',"
           "'CONTACT_SITE','CREATE_INCIDENT'")
PRIORITIES = "'LOW','MEDIUM','HIGH','URGENT'"
LIMITS = "'RULE','DETECTION','CORRELATION','RISK'"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE security_recommendations (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            situation_id          UUID NOT NULL REFERENCES security_situations(id) ON DELETE CASCADE,
            assessment_id         UUID NOT NULL REFERENCES security_assessments(id) ON DELETE CASCADE,
            rank                  SMALLINT NOT NULL,
            action                VARCHAR(24) NOT NULL,
            priority              VARCHAR(10) NOT NULL,
            reason                TEXT NOT NULL,
            confidence            NUMERIC(5,4) NOT NULL,
            confidence_limited_by VARCHAR(12) NOT NULL,
            available             BOOLEAN NOT NULL DEFAULT true,
            unavailable_reason    TEXT,
            supporting            JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            engine_version        VARCHAR(16) NOT NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_secrec_rank        UNIQUE (assessment_id, rank),
            CONSTRAINT uq_secrec_action      UNIQUE (assessment_id, action),
            CONSTRAINT ck_secrec_rank        CHECK (rank >= 1),
            CONSTRAINT ck_secrec_action      CHECK (action IN ({ACTIONS})),
            CONSTRAINT ck_secrec_priority    CHECK (priority IN ({PRIORITIES})),
            CONSTRAINT ck_secrec_confidence  CHECK (confidence BETWEEN 0 AND 1),
            CONSTRAINT ck_secrec_limited_by  CHECK (confidence_limited_by IN ({LIMITS})),
            CONSTRAINT ck_secrec_reason      CHECK (btrim(reason) <> ''),
            -- Not available always says why; available never carries a stale reason.
            CONSTRAINT ck_secrec_available   CHECK (
                (available AND unavailable_reason IS NULL)
                OR (NOT available AND unavailable_reason IS NOT NULL AND btrim(unavailable_reason) <> '')),
            CONSTRAINT ck_secrec_supporting  CHECK (jsonb_typeof(supporting) = 'object')
        )
    """)
    op.execute("CREATE INDEX idx_secrec_situation ON security_recommendations (situation_id, created_at DESC)")
    op.execute("ALTER TABLE security_recommendations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security_recommendations FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_security_recommendations ON security_recommendations
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    # Added to and read, never changed or removed — as with assessments, the
    # REVOKE is what does it, because default privileges grant the rest.
    op.execute("REVOKE ALL ON security_recommendations FROM svc_app")
    op.execute("GRANT SELECT, INSERT ON security_recommendations TO svc_app")
    # What the runner asks on every pass: latest assessments with no recommendations yet.
    op.execute("CREATE INDEX idx_secsit_assessment ON security_situations (assessment_id) "
               "WHERE assessment_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_secsit_assessment")
    op.execute("DROP TABLE IF EXISTS security_recommendations CASCADE")
