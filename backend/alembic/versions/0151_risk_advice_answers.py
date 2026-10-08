"""What a person said of a piece of advice: accepted, or not accepted and why.

Additive: one new table and two permissions. No existing table, policy or
permission is altered (LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 9).

  what was recorded ──► counted, on request ──► where it gathers: hours, days, places
                                                      │
                                                      ▼
                             ADVICE: a statement, what it rests on, how much history
                                                      │
                                                      ▼
                        a person's ANSWER, kept here:  ACCEPTED │ NOT_ACCEPTED, with why

NOTHING OF THE COUNTING IS STORED. A pattern and the advice made of it are
counted when somebody asks, from rows the platform already keeps. The only
thing this migration keeps is what a person answered — with the statement as it
was shown to them, so that the answer can always be read against what it was an
answer to.

AN ANSWER IS ADDED AND NEVER REWRITTEN. Somebody who changes their mind answers
again; both stay. The application's role may read and add, and nothing else.

AN ANSWER CHANGES NOTHING ELSE. Accepting advice raises no work, moves no guard
and alters no roster: it is a person's record that they have read it and what
they made of it.

THE TABLE IS `risk_advice_answers`, NOT `security_advice`. Tables named
`security_...` are the AI security intelligence layer's own, and its tests say
what the application may do to every one of them.

SUPER ADMIN (1), GUARD (5) AND CLIENT (7) GET NO PERMISSION HERE.

Revision ID: 0151
Revises: 0150
"""
from alembic import op

revision = "0151"
down_revision = "0150"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("advice:read", "Read where recorded events gather, and the advice made of that", "risk"),
    ("advice:answer", "Answer a piece of advice: accepted, or not accepted and why", "risk"),
]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE risk_advice_answers (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id             UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            -- What the advice is about, so that the same advice shown again finds its answer.
            advice_key          VARCHAR(200) NOT NULL,
            code                VARCHAR(40) NOT NULL,
            source              VARCHAR(20) NOT NULL,
            -- The advice as it was shown to whoever answered it.
            statement           TEXT NOT NULL,
            rests_on            JSONB NOT NULL DEFAULT '{}'::jsonb,
            confidence          VARCHAR(6) NOT NULL,
            period_weeks        SMALLINT NOT NULL,
            period_end          TIMESTAMPTZ NOT NULL,
            answer              VARCHAR(12) NOT NULL,
            reason              TEXT,
            answered_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            answered_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_advans_answer     CHECK (answer IN ('ACCEPTED', 'NOT_ACCEPTED')),
            CONSTRAINT ck_advans_confidence CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH')),
            CONSTRAINT ck_advans_weeks      CHECK (period_weeks BETWEEN 1 AND 12),
            CONSTRAINT ck_advans_statement  CHECK (btrim(statement) <> ''),
            -- Not accepting advice says why.
            CONSTRAINT ck_advans_reason     CHECK (answer <> 'NOT_ACCEPTED' OR (
                reason IS NOT NULL AND btrim(reason) <> ''))
        )
    """)
    op.execute("CREATE INDEX idx_advans_key ON risk_advice_answers (tenant_id, advice_key, answered_at DESC)")
    op.execute("CREATE INDEX idx_advans_site ON risk_advice_answers (tenant_id, site_id, answered_at DESC)")

    op.execute("ALTER TABLE risk_advice_answers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE risk_advice_answers FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_risk_advice_answers ON risk_advice_answers
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    # The REVOKE is what does it: default privileges hand the application
    # UPDATE and DELETE on every new table.
    op.execute("REVOKE ALL ON risk_advice_answers FROM svc_app")
    op.execute("GRANT SELECT, INSERT ON risk_advice_answers TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    # Read by whoever watches a site from a desk (2, 3, 4, 6, 8). Answered by
    # Admin, Manager and Supervisor: the people who would act on it.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'advice:read'), (2, 'advice:answer'),
                  (8, 'advice:read'), (8, 'advice:answer'),
                  (3, 'advice:read'), (3, 'advice:answer'),
                  (4, 'advice:read'),
                  (6, 'advice:read')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('advice:read', 'advice:answer'))
    """)
    op.execute("DELETE FROM permissions WHERE code IN ('advice:read', 'advice:answer')")
    op.execute("DROP TABLE IF EXISTS risk_advice_answers CASCADE")
