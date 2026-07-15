"""Dispatch, SLA escalation, evidence chain of custody:
- incidents: dispatch fields (guard assignment + response time tracking)
- sla_configs: per-tenant configurable SLA thresholds per severity
- escalation_events: log of auto-escalation notifications fired
- evidence_access_log: every view/download of evidence (chain of custody)

New permissions: incident:dispatch, sla:manage, evidence:custody:read

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-22
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rls(table: str) -> str:
    return f"""
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation_{table} ON {table}
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """


def upgrade() -> None:
    # ── incidents: dispatch tracking ─────────────────────────────────────────
    op.execute(
        """
        ALTER TABLE incidents
            ADD COLUMN dispatched_guard_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN dispatched_at         TIMESTAMPTZ,
            ADD COLUMN guard_arrived_at      TIMESTAMPTZ,
            ADD COLUMN dispatch_notes        TEXT,
            ADD COLUMN sla_deadline_at       TIMESTAMPTZ,
            ADD COLUMN sla_breached          BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN escalated_at          TIMESTAMPTZ,
            ADD COLUMN escalated_to_user_id  UUID REFERENCES users(id) ON DELETE SET NULL;
        """
    )

    # ── sla_configs ───────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE sla_configs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            severity        VARCHAR(10) NOT NULL,
            ack_within_seconds    INTEGER NOT NULL DEFAULT 300,
            dispatch_within_seconds INTEGER NOT NULL DEFAULT 600,
            resolve_within_seconds  INTEGER NOT NULL DEFAULT 3600,
            escalation_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(tenant_id, severity)
        );
        CREATE INDEX idx_sla_configs_tenant ON sla_configs(tenant_id);
        """
    )
    op.execute(_rls("sla_configs"))

    # ── escalation_events ─────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE escalation_events (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            alert_id        UUID,
            incident_id     UUID,
            escalation_type VARCHAR(30) NOT NULL DEFAULT 'sla_breach',
            -- sla_breach | unacknowledged | unresolved
            escalated_to_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            notification_sent BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_escalation_events_tenant ON escalation_events(tenant_id, created_at DESC);
        """
    )
    op.execute(_rls("escalation_events"))

    # ── evidence_access_log (chain of custody — no RLS update needed, append-only) ──
    op.execute(
        """
        CREATE TABLE evidence_access_log (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            evidence_id     UUID NOT NULL,
            user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
            action          VARCHAR(20) NOT NULL DEFAULT 'view',
            -- view | download | export | custody_transfer
            ip_address      VARCHAR(45),
            user_agent      TEXT,
            checksum_verified BOOLEAN,
            accessed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_evidence_access_log_evidence ON evidence_access_log(evidence_id, accessed_at DESC);
        CREATE INDEX idx_evidence_access_log_tenant   ON evidence_access_log(tenant_id, accessed_at DESC);
        """
    )
    op.execute(_rls("evidence_access_log"))

    # ── seed default SLA configs for demo tenant ─────────────────────────────
    op.execute(
        """
        INSERT INTO sla_configs (tenant_id, severity, ack_within_seconds, dispatch_within_seconds, resolve_within_seconds)
        SELECT t.id, s.severity, s.ack, s.dispatch, s.resolve
        FROM tenants t
        CROSS JOIN (VALUES
            ('critical', 120,  300,  1800),
            ('high',     300,  600,  3600),
            ('medium',   600, 1200,  7200),
            ('low',     1800, 3600, 86400),
            ('info',    3600, 7200, 604800)
        ) AS s(severity, ack, dispatch, resolve)
        ON CONFLICT DO NOTHING;
        """
    )

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute(
        """
        INSERT INTO permissions (code, description, category) VALUES
            ('incident:dispatch',      'Dispatch guard to incident',        'incident'),
            ('sla:manage',             'Configure SLA thresholds',          'operations'),
            ('evidence:custody:read',  'View evidence chain of custody log','evidence')
        ON CONFLICT (code) DO NOTHING;

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3) AND p.code IN (
            'incident:dispatch','sla:manage','evidence:custody:read'
        ) ON CONFLICT DO NOTHING;

        -- operators can dispatch
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 4 AND p.code = 'incident:dispatch'
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS evidence_access_log;
        DROP TABLE IF EXISTS escalation_events;
        DROP TABLE IF EXISTS sla_configs;

        ALTER TABLE incidents
            DROP COLUMN IF EXISTS dispatched_guard_id,
            DROP COLUMN IF EXISTS dispatched_at,
            DROP COLUMN IF EXISTS guard_arrived_at,
            DROP COLUMN IF EXISTS dispatch_notes,
            DROP COLUMN IF EXISTS sla_deadline_at,
            DROP COLUMN IF EXISTS sla_breached,
            DROP COLUMN IF EXISTS escalated_at,
            DROP COLUMN IF EXISTS escalated_to_user_id;
        """
    )
