"""Tenant-configurable AI alert/incident rules (Module 14).

WHY THIS EXISTS
    The severity of every AI alert, and whether it auto-opens an incident, is
    currently a Python constant compiled into an AI worker image. A customer
    who wants loitering treated as `low` instead of `medium`, or who does not
    want a blunt-weapon detection paging anyone at 3am, needs a code change and
    a worker redeploy today.

    Those are policy decisions that legitimately differ between a shopping
    mall, a data centre and a construction site. This table lets a tenant
    express them.

WHAT THIS DOES NOT CHANGE
    Nothing, on its own. A tenant with no rows here behaves exactly as before:
    workers fall back to shared/shared/alert_rules.py::DEFAULT_ALERT_RULES,
    which was transcribed from the running constants rather than redesigned.
    An override is opt-in, per (module, trigger), and can be deleted to return
    to the default.

    `alert_code` is deliberately NOT overridable — it is the stable machine key
    that message_params renders against for i18n (plan §16.3). Severity answers
    "how much do we care"; alert_code answers "what happened", and only the
    former is a customer's call.

Revision ID: 0080
Revises: 0079
"""
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS alert_rules (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

            -- Free-form rather than an enum/FK: module_type is already a plain
            -- VARCHAR on detections/alerts, and a new AI module must be able to
            -- ship its rules without a migration to widen a type first.
            module_type        VARCHAR(30) NOT NULL,
            -- What discriminates outcomes inside a module: 'block'/'allow' for
            -- watchlists, 'zone_high' for zone-driven modules, 'firearm' for
            -- weapon classes, 'detected' for single-outcome modules.
            trigger_key        VARCHAR(50) NOT NULL,

            severity           VARCHAR(10) NOT NULL
                               CHECK (severity IN ('info','low','medium','high','critical')),
            create_incident    BOOLEAN NOT NULL DEFAULT FALSE,
            incident_severity  VARCHAR(10)
                               CHECK (incident_severity IS NULL OR
                                      incident_severity IN ('info','low','medium','high','critical')),

            -- Disabling suppresses the alert entirely for this trigger. Kept
            -- separate from deleting the row: a deleted row means "use the
            -- default", which is the opposite of "we never want this alert".
            is_enabled         BOOLEAN NOT NULL DEFAULT TRUE,

            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- One rule per trigger. Two would make severity non-deterministic,
            -- and severity drives who gets paged.
            CONSTRAINT uq_alert_rule_tenant_module_trigger
                UNIQUE (tenant_id, module_type, trigger_key),

            -- Mirrors AlertRule.__post_init__ in shared/shared/alert_rules.py.
            -- Enforced here too because the workers trust what they read: an
            -- incident-creating rule with no incident severity would fail at
            -- INSERT time inside the detection pipeline, which is the worst
            -- place to discover a bad config.
            CONSTRAINT ck_alert_rule_incident_severity
                CHECK ((create_incident AND incident_severity IS NOT NULL)
                    OR (NOT create_incident AND incident_severity IS NULL))
        )
    """)
    # The workers' read path: every rule for one tenant, fetched in one query
    # and cached for 30s.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_alert_rules_tenant
            ON alert_rules(tenant_id)
    """)

    op.execute("ALTER TABLE alert_rules ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE alert_rules FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_alert_rules ON alert_rules
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('alert_rule:read',   'View AI alert severity and incident rules', 'alert'),
          ('alert_rule:manage', 'Change AI alert severity and incident rules', 'alert')
        ON CONFLICT (code) DO NOTHING
    """)
    # Same split as recording_policy (migration 0079): supervisors need to know
    # why an alert arrived at the severity it did, but retuning what pages the
    # on-call rota is an admin decision.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3,8) AND p.code = 'alert_rule:read'
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,8) AND p.code = 'alert_rule:manage'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions WHERE permission_id IN
          (SELECT id FROM permissions WHERE code IN ('alert_rule:read','alert_rule:manage'))
    """)
    op.execute("DELETE FROM permissions WHERE code IN ('alert_rule:read','alert_rule:manage')")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_alert_rules ON alert_rules")
    op.execute("DROP TABLE IF EXISTS alert_rules")
