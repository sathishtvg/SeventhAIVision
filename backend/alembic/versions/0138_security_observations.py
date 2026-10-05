"""AI security intelligence, phase 9: what the person on the ground reports.

Additive: one new table. Nothing that existed before the layer is altered.

AN OBSERVATION IS A STATEMENT OF FACT BY A PERSON, NOT A DECISION. "I have this",
"I am there", "the door is closed and nobody is here": said by the guard or
officer who is dealing with a situation, kept with who said it, when, and from
where if the phone gave a position. It decides nothing and carries nothing out.
What to do about the situation is still a decision (0137), made under the
decision policy.

IT IS THE LAYER'S OWN RECORD. A guard's "arrived" here does not set the
incident's own arrival time: that column belongs to the platform's dispatch
function, which needs a permission a guard does not hold, and nothing here goes
round it.

WRITTEN ONCE. The application's role may add and read observations and can
neither change nor remove one — what was reported from the ground at 02:24
stays as it was reported.

Revision ID: 0138
Revises: 0137
"""
from alembic import op

revision = "0138"
down_revision = "0137"
branch_labels = None
depends_on = None

KINDS = "'ACCEPTED','ARRIVED','OBSERVATION'"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE security_observations (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            situation_id UUID NOT NULL REFERENCES security_situations(id) ON DELETE CASCADE,
            kind         VARCHAR(16) NOT NULL,
            note         TEXT,
            user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
            actor_role   SMALLINT NOT NULL,
            latitude     DOUBLE PRECISION,
            longitude    DOUBLE PRECISION,
            via          VARCHAR(10) NOT NULL DEFAULT 'mobile',
            request_id   VARCHAR(64),
            client_ref   UUID,
            observed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_secobs_kind     CHECK (kind IN ({KINDS})),
            CONSTRAINT ck_secobs_via      CHECK (via IN ('web','mobile')),
            -- What was seen has to be said.
            CONSTRAINT ck_secobs_note     CHECK (kind <> 'OBSERVATION' OR (note IS NOT NULL AND btrim(note) <> '')),
            -- A position is both numbers or neither, and on the globe.
            CONSTRAINT ck_secobs_position CHECK (
                (latitude IS NULL AND longitude IS NULL)
                OR (latitude IS NOT NULL AND longitude IS NOT NULL
                    AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180))
        )
    """)
    op.execute("CREATE INDEX idx_secobs_situation ON security_observations (situation_id, observed_at)")
    # A retried report is one observation, not two.
    op.execute("CREATE UNIQUE INDEX uq_secobs_client_ref ON security_observations (tenant_id, client_ref) "
               "WHERE client_ref IS NOT NULL")
    op.execute("ALTER TABLE security_observations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security_observations FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_security_observations ON security_observations
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    # Added to and read. The REVOKE is what does it: default privileges hand
    # the application UPDATE and DELETE on every new table.
    op.execute("REVOKE ALL ON security_observations FROM svc_app")
    op.execute("GRANT SELECT, INSERT ON security_observations TO svc_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS security_observations CASCADE")
