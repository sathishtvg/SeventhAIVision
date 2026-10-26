"""The AI module, phase 2: what each organisation's models learned, and the suggestions people decide on.

Design: docs/plans/2026-10-26-ai-module-design.md, section 6.

ai_models: one row per training run of one feature for one organisation. The
weights are plain JSON (services/ai_learning.Fitted), never a pickle. The bar a
warning must reach, and why it is there, are kept with the model.

ai_suggestions: one row per subject (one shift, one guard's month) per feature.
A suggestion is OPEN until a person ACCEPTS or DISMISSES it, or it EXPIRES
(the shift started, or the risk fell). What actually happened is recorded later
as its outcome; outcomes and decisions are what the next training run learns from.
A suggestion acts on nothing: no column here is read by a roster, a shift or an alert.

Revision ID: 0171
Revises: 0170
"""
from alembic import op

revision = "0171"
down_revision = "0170"
branch_labels = None
depends_on = None

TABLES = ("ai_models", "ai_suggestions")


def _secure(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute(f"REVOKE ALL ON {table} FROM svc_app")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO svc_app")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ai_models (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            feature_key      VARCHAR(40) NOT NULL,
            version          INTEGER NOT NULL,
            status           VARCHAR(10) NOT NULL,
            trained_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            rows_used        INTEGER NOT NULL DEFAULT 0,
            positives        INTEGER NOT NULL DEFAULT 0,
            fitted           JSONB,
            metrics          JSONB NOT NULL DEFAULT '{}'::jsonb,
            threshold        NUMERIC(4,2),
            threshold_reason TEXT,
            note             TEXT,
            CONSTRAINT uq_aim_version UNIQUE (tenant_id, feature_key, version),
            CONSTRAINT ck_aim_status CHECK (status IN ('LEARNING', 'READY')),
            CONSTRAINT ck_aim_ready_has_fit CHECK (status <> 'READY' OR fitted IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX idx_aim_latest ON ai_models (tenant_id, feature_key, version DESC)")
    op.execute("""
        CREATE TABLE ai_suggestions (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            feature_key        VARCHAR(40) NOT NULL,
            subject_type       VARCHAR(20) NOT NULL,
            subject_id         UUID NOT NULL,
            site_id            UUID REFERENCES sites(id) ON DELETE SET NULL,
            person_user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
            title              TEXT NOT NULL,
            detail             TEXT,
            score              NUMERIC(5,4) NOT NULL,
            threshold          NUMERIC(4,2),
            reasons            JSONB NOT NULL DEFAULT '[]'::jsonb,
            evidence           JSONB NOT NULL DEFAULT '{}'::jsonb,
            model_id           UUID REFERENCES ai_models(id) ON DELETE SET NULL,
            status             VARCHAR(10) NOT NULL DEFAULT 'OPEN',
            decided_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            decided_at         TIMESTAMPTZ,
            decision_note      TEXT,
            outcome            VARCHAR(15),
            outcome_at         TIMESTAMPTZ,
            valid_until        TIMESTAMPTZ,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_ais_subject UNIQUE (tenant_id, feature_key, subject_type, subject_id),
            CONSTRAINT ck_ais_status CHECK (status IN ('OPEN', 'ACCEPTED', 'DISMISSED', 'EXPIRED')),
            CONSTRAINT ck_ais_outcome CHECK (outcome IS NULL OR outcome IN ('HAPPENED', 'DID_NOT_HAPPEN')),
            CONSTRAINT ck_ais_decided CHECK (
                (status IN ('ACCEPTED', 'DISMISSED')) = (decided_at IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX idx_ais_open ON ai_suggestions (tenant_id, feature_key, status, created_at DESC)")
    for table in TABLES:
        _secure(table)


def downgrade() -> None:
    for table in ("ai_suggestions", "ai_models"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
