"""AI security intelligence, phase 7: what a person decided, and what was then done.

Additive: five new tables, and six columns on `security_situations` — this
layer's own table, created in 0134. No table that existed before the layer is
altered.

THREE RECORDS, NEVER ONE. What the layer suggested is in
`security_recommendations` (0136). What a person decided is here, in
`security_decisions`. What the platform then did is in `security_actions`. They
are separate tables so that a suggestion can never be read as a decision, nor a
decision as something that was carried out.

A DECISION IS MADE BY A PERSON AND IS NEVER REWRITTEN. `security_decisions`,
`security_decision_approvals`, `security_actions` and `security_reviews` are
added to and read; the application's role can neither change nor remove a row.
What an officer chose at 02:19, on which assessment, with what reason, stays
exactly as it was written.

AN OVERRIDE ALWAYS SAYS WHY. A decision that goes against what was suggested, or
that closes a situation, carries a reason from a fixed list; "other" carries a
note. The database refuses one without.

WHO MAY DECIDE IS CONFIGURATION. `security_decision_policies` holds, per tenant
and optionally per site, how far each role may decide alone and how far with a
second person's approval. It narrows what a permission allows; it grants
nothing to a role that lacks the permission.

Revision ID: 0137
Revises: 0136
"""
from alembic import op

revision = "0137"
down_revision = "0136"
branch_labels = None
depends_on = None

# The nine steps a recommendation can name, and five only a person can choose.
DECISIONS = ("'MONITOR','VERIFY','VIEW_CAMERA','VERIFY_WITH_DRONE','DISPATCH_GUARD','ESCALATE','INVESTIGATE',"
             "'CONTACT_SITE','CREATE_INCIDENT','ACKNOWLEDGE','CONFIRM_INCIDENT','REQUEST_ASSISTANCE',"
             "'FALSE_POSITIVE','RESOLVE'")
BASES = "'FOLLOWED','OVERRIDE','CLOSING','INDEPENDENT'"
REASONS = ("'AUTHORISED_ACTIVITY','ALREADY_HANDLED','FALSE_DETECTION','GUARD_RESPONDING','MAINTENANCE',"
           "'EMERGENCY','CAMERA_ISSUE','OTHER'")
# What the platform did, each through an existing function of its own.
ACTIONS = ("'ALERT_ACKNOWLEDGE','ALERT_FALSE_POSITIVE','ALERT_DISMISS','ALERT_ASSIGN','INCIDENT_CREATE',"
           "'INCIDENT_CONFIRM','INCIDENT_DISPATCH','INCIDENT_ASSIGN','INCIDENT_RESOLVE','NONE'")
RESULTS = "'OK','FAILED','SKIPPED','RECORDED'"
STATUSES = ("'AWAITING','ACKNOWLEDGED','IN_HAND','PENDING_APPROVAL','ASSISTANCE_REQUESTED','RESOLVED',"
            "'FALSE_POSITIVE'")
RISK_LEVELS = "'INFO','LOW','MEDIUM','HIGH','CRITICAL'"

INSERT_ONLY = ("security_reviews", "security_decisions", "security_decision_approvals", "security_actions")


def upgrade() -> None:
    # ── that an officer looked ───────────────────────────────────────────────
    op.execute("""
        CREATE TABLE security_reviews (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            situation_id  UUID NOT NULL REFERENCES security_situations(id) ON DELETE CASCADE,
            assessment_id UUID NOT NULL REFERENCES security_assessments(id) ON DELETE CASCADE,
            user_id       UUID REFERENCES users(id) ON DELETE SET NULL,
            actor_role    SMALLINT NOT NULL,
            via           VARCHAR(10) NOT NULL DEFAULT 'web',
            viewed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_secreview_once UNIQUE (assessment_id, user_id),
            CONSTRAINT ck_secreview_via  CHECK (via IN ('web','mobile'))
        )
    """)
    op.execute("CREATE INDEX idx_secreview_situation ON security_reviews (situation_id, viewed_at)")

    # ── what a person decided ────────────────────────────────────────────────
    op.execute(f"""
        CREATE TABLE security_decisions (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            situation_id       UUID NOT NULL REFERENCES security_situations(id) ON DELETE CASCADE,
            assessment_id      UUID REFERENCES security_assessments(id) ON DELETE SET NULL,
            seen_assessment_id UUID REFERENCES security_assessments(id) ON DELETE SET NULL,
            recommendation_id  UUID REFERENCES security_recommendations(id) ON DELETE SET NULL,
            suggested_action   VARCHAR(24),
            action             VARCHAR(24) NOT NULL,
            basis              VARCHAR(12) NOT NULL,
            reason_code        VARCHAR(24),
            note               TEXT,
            actor_user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
            actor_role         SMALLINT NOT NULL,
            risk_level         VARCHAR(10),
            risk_score         SMALLINT,
            authority          VARCHAR(16) NOT NULL,
            policy             JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            params             JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            via                VARCHAR(10) NOT NULL DEFAULT 'web',
            request_id         VARCHAR(64),
            client_ref         UUID,
            decided_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_secdec_action    CHECK (action IN ({DECISIONS})),
            CONSTRAINT ck_secdec_basis     CHECK (basis IN ({BASES})),
            CONSTRAINT ck_secdec_reason    CHECK (reason_code IS NULL OR reason_code IN ({REASONS})),
            CONSTRAINT ck_secdec_authority CHECK (authority IN ('ALONE','WITH_APPROVAL')),
            CONSTRAINT ck_secdec_via       CHECK (via IN ('web','mobile')),
            CONSTRAINT ck_secdec_risk      CHECK (risk_level IS NULL OR risk_level IN ({RISK_LEVELS})),
            -- Going against a suggestion, or closing the matter, always says why.
            CONSTRAINT ck_secdec_why       CHECK (basis NOT IN ('OVERRIDE','CLOSING') OR reason_code IS NOT NULL),
            CONSTRAINT ck_secdec_other     CHECK (reason_code IS DISTINCT FROM 'OTHER'
                                                  OR (note IS NOT NULL AND btrim(note) <> '')),
            -- "Followed" names what was followed.
            CONSTRAINT ck_secdec_followed  CHECK (basis <> 'FOLLOWED' OR recommendation_id IS NOT NULL),
            CONSTRAINT ck_secdec_closing   CHECK ((basis = 'CLOSING') = (action IN ('FALSE_POSITIVE','RESOLVE'))),
            CONSTRAINT ck_secdec_params    CHECK (jsonb_typeof(params) = 'object' AND jsonb_typeof(policy) = 'object')
        )
    """)
    op.execute("CREATE INDEX idx_secdec_situation ON security_decisions (situation_id, decided_at)")
    op.execute("CREATE INDEX idx_secdec_recent ON security_decisions (tenant_id, decided_at DESC)")
    # A retried request records one decision, not two.
    op.execute("CREATE UNIQUE INDEX uq_secdec_client_ref ON security_decisions (tenant_id, client_ref) "
               "WHERE client_ref IS NOT NULL")

    # ── a second person's verdict on a decision that needed one ──────────────
    op.execute("""
        CREATE TABLE security_decision_approvals (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            decision_id      UUID NOT NULL REFERENCES security_decisions(id) ON DELETE CASCADE,
            verdict          VARCHAR(10) NOT NULL,
            note             TEXT,
            approver_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            approver_role    SMALLINT NOT NULL,
            request_id       VARCHAR(64),
            decided_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_secapproval_once  UNIQUE (decision_id),
            CONSTRAINT ck_secapproval_verdict CHECK (verdict IN ('APPROVED','REJECTED')),
            -- Refusing says why.
            CONSTRAINT ck_secapproval_why   CHECK (verdict = 'APPROVED' OR (note IS NOT NULL AND btrim(note) <> ''))
        )
    """)

    # ── what the platform then did ───────────────────────────────────────────
    op.execute(f"""
        CREATE TABLE security_actions (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            decision_id         UUID NOT NULL REFERENCES security_decisions(id) ON DELETE CASCADE,
            situation_id        UUID NOT NULL REFERENCES security_situations(id) ON DELETE CASCADE,
            sequence            SMALLINT NOT NULL,
            action              VARCHAR(32) NOT NULL,
            through             VARCHAR(120),
            target_type         VARCHAR(16),
            target_id           UUID,
            result              VARCHAR(12) NOT NULL,
            detail              TEXT,
            executed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            executed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_secaction_sequence UNIQUE (decision_id, sequence),
            CONSTRAINT ck_secaction_sequence CHECK (sequence >= 1),
            CONSTRAINT ck_secaction_action   CHECK (action IN ({ACTIONS})),
            CONSTRAINT ck_secaction_result   CHECK (result IN ({RESULTS})),
            CONSTRAINT ck_secaction_target   CHECK (target_type IS NULL OR target_type IN ('alert','incident')),
            -- Something done was done through a named function; something only
            -- recorded was not, and says so.
            CONSTRAINT ck_secaction_through  CHECK ((result = 'RECORDED') = (through IS NULL))
        )
    """)
    op.execute("CREATE INDEX idx_secaction_situation ON security_actions (situation_id, executed_at)")

    for table in INSERT_ONLY:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)
        # Added to and read. The REVOKE is what does it: default privileges
        # hand the application UPDATE and DELETE on every new table.
        op.execute(f"REVOKE ALL ON {table} FROM svc_app")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO svc_app")

    # ── who may decide ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE security_decision_policies (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id            UUID REFERENCES sites(id) ON DELETE CASCADE,
            roles              JSONB NOT NULL,
            note               VARCHAR(255),
            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_secpolicy_roles CHECK (jsonb_typeof(roles) = 'object')
        )
    """)
    # One for the tenant as a whole, and at most one for each site.
    op.execute("CREATE UNIQUE INDEX uq_secpolicy_tenant ON security_decision_policies (tenant_id) "
               "WHERE site_id IS NULL")
    op.execute("CREATE UNIQUE INDEX uq_secpolicy_site ON security_decision_policies (site_id) "
               "WHERE site_id IS NOT NULL")
    op.execute("ALTER TABLE security_decision_policies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security_decision_policies FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_security_decision_policies ON security_decision_policies
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("GRANT ALL ON security_decision_policies TO svc_app")

    # ── where a situation stands with the people responsible for it ──────────
    op.execute(f"""
        ALTER TABLE security_situations
            ADD COLUMN decision_status       VARCHAR(20) NOT NULL DEFAULT 'AWAITING',
            ADD COLUMN last_decision_id      UUID REFERENCES security_decisions(id) ON DELETE SET NULL,
            ADD COLUMN last_decided_at       TIMESTAMPTZ,
            ADD COLUMN closed_at             TIMESTAMPTZ,
            ADD COLUMN incident_id           UUID REFERENCES incidents(id) ON DELETE SET NULL,
            ADD COLUMN incident_confirmed_at TIMESTAMPTZ,
            ADD CONSTRAINT ck_secsit_decision_status CHECK (decision_status IN ({STATUSES})),
            ADD CONSTRAINT ck_secsit_closed CHECK ((closed_at IS NOT NULL)
                                                   = (decision_status IN ('RESOLVED','FALSE_POSITIVE')))
    """)
    # The work queue: what still wants a decision, highest risk first.
    op.execute("CREATE INDEX idx_secsit_queue ON security_situations "
               "(tenant_id, decision_status, risk_score DESC NULLS LAST) WHERE closed_at IS NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_secsit_queue")
    # DESTRUCTIVE: these six columns are this migration's own, on a table this
    # layer created in 0134; removing them loses where each situation stood,
    # and the decisions themselves go with the tables below.
    op.execute("""
        ALTER TABLE security_situations
            DROP CONSTRAINT IF EXISTS ck_secsit_closed,
            DROP CONSTRAINT IF EXISTS ck_secsit_decision_status,
            DROP COLUMN IF EXISTS incident_confirmed_at,
            DROP COLUMN IF EXISTS incident_id,
            DROP COLUMN IF EXISTS closed_at,
            DROP COLUMN IF EXISTS last_decided_at,
            DROP COLUMN IF EXISTS last_decision_id,
            DROP COLUMN IF EXISTS decision_status
    """)
    op.execute("DROP TABLE IF EXISTS security_decision_policies CASCADE")
    op.execute("DROP TABLE IF EXISTS security_actions CASCADE")
    op.execute("DROP TABLE IF EXISTS security_decision_approvals CASCADE")
    op.execute("DROP TABLE IF EXISTS security_decisions CASCADE")
    op.execute("DROP TABLE IF EXISTS security_reviews CASCADE")
