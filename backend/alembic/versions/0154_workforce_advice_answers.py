"""What a manager said of a workforce recommendation: accepted, or not accepted and why.

Additive: one new table and three permissions. No existing table, policy or
permission is altered (LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 11).

  what is recorded of a guard's work ──► counted, on request ──► A READING: counts, each
  shifts · patrols · responses ·                                  beside how much there was to do
  violations · training · handovers                                       │
                                                                          ▼
                              a RECOMMENDATION for a manager: training for a guard,
                              or cover for a site — a statement and what it rests on
                                                                          │
                                                                          ▼
                             the manager's ANSWER, kept here:  ACCEPTED │ NOT_ACCEPTED, with why

NOTHING OF A READING IS STORED. It is counted when somebody asks, from rows the
platform already keeps. There is no score, grade or rank of a person anywhere
in it, and nothing here is kept about a guard but a manager's answer to a
recommendation.

A RECOMMENDATION IS NEVER AN EMPLOYMENT DECISION AND NEVER A CHANGE TO A
ROSTER. Accepting one assigns no course, moves no shift, and records nothing
against anybody: it is the manager's note that they have read it and what they
made of it. The course is assigned in Training and the roster is changed in the
roster, by a person, as before.

AN ANSWER IS ADDED AND NEVER REWRITTEN. The application's role may read and
add, and nothing else.

SUPER ADMIN (1), OPERATOR (4), VIEWER (6) AND CLIENT (7) DO NOT READ ANOTHER
PERSON'S READING. A guard, an operator and a supervisor may read THEIR OWN.

Revision ID: 0154
Revises: 0153
"""
from alembic import op

revision = "0154"
down_revision = "0153"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("workforce:read", "Read what is recorded of each guard''s work, and the recommendations made of it", "workforce"),
    ("workforce:answer", "Answer a workforce recommendation: accepted, or not accepted and why", "workforce"),
    ("workforce:own", "Read what is recorded of one''s own work", "workforce"),
]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE workforce_advice_answers (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- TRAINING is about a guard; COVERAGE is about a site.
            kind                VARCHAR(10) NOT NULL,
            subject_user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
            site_id             UUID REFERENCES sites(id) ON DELETE CASCADE,
            -- What the recommendation is known by, so that the same one shown again finds its answer.
            advice_key          VARCHAR(200) NOT NULL,
            code                VARCHAR(40) NOT NULL,
            -- The recommendation as it was shown to whoever answered it.
            statement           TEXT NOT NULL,
            rests_on            JSONB NOT NULL DEFAULT '{}'::jsonb,
            answer              VARCHAR(12) NOT NULL,
            reason              TEXT,
            answered_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            answered_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_wfans_kind      CHECK (kind IN ('TRAINING', 'COVERAGE')),
            CONSTRAINT ck_wfans_answer    CHECK (answer IN ('ACCEPTED', 'NOT_ACCEPTED')),
            CONSTRAINT ck_wfans_statement CHECK (btrim(statement) <> ''),
            -- A training recommendation is about a person; a coverage one is about a site.
            CONSTRAINT ck_wfans_subject   CHECK ((kind = 'TRAINING' AND subject_user_id IS NOT NULL)
                                              OR (kind = 'COVERAGE' AND site_id IS NOT NULL AND subject_user_id IS NULL)),
            -- Not accepting a recommendation says why.
            CONSTRAINT ck_wfans_reason    CHECK (answer <> 'NOT_ACCEPTED' OR (
                reason IS NOT NULL AND btrim(reason) <> ''))
        )
    """)
    op.execute("CREATE INDEX idx_wfans_key ON workforce_advice_answers (tenant_id, advice_key, answered_at DESC)")
    op.execute("CREATE INDEX idx_wfans_subject ON workforce_advice_answers (tenant_id, subject_user_id, answered_at DESC)")

    op.execute("ALTER TABLE workforce_advice_answers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workforce_advice_answers FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_workforce_advice_answers ON workforce_advice_answers
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    # The REVOKE is what does it: default privileges hand the application
    # UPDATE and DELETE on every new table.
    op.execute("REVOKE ALL ON workforce_advice_answers FROM svc_app")
    op.execute("GRANT SELECT, INSERT ON workforce_advice_answers TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    # Another person's reading: Admin, Manager and Supervisor — the people a
    # guard answers to. One's own: whoever works shifts.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'workforce:read'), (2, 'workforce:answer'),
                  (8, 'workforce:read'), (8, 'workforce:answer'),
                  (3, 'workforce:read'), (3, 'workforce:answer'), (3, 'workforce:own'),
                  (4, 'workforce:own'),
                  (5, 'workforce:own')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions
                                  WHERE code IN ('workforce:read', 'workforce:answer', 'workforce:own'))
    """)
    op.execute("DELETE FROM permissions WHERE code IN ('workforce:read', 'workforce:answer', 'workforce:own')")
    op.execute("DROP TABLE IF EXISTS workforce_advice_answers CASCADE")
