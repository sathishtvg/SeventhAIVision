"""Guard Tour Compliance Reports:
- tour_schedules: recurring patrol schedule definitions (what route, when, which guard)
- tour_occurrences: individual scheduled instances with linked session + compliance score

New permissions: compliance:read (all roles), compliance:manage (roles 1-3)

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-25
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
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
    # ── tour_schedules ────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE tour_schedules (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            route_id                UUID NOT NULL REFERENCES patrol_routes(id) ON DELETE CASCADE,
            name                    VARCHAR(255) NOT NULL,
            description             TEXT,
            recurrence              VARCHAR(20) NOT NULL DEFAULT 'daily',
            -- daily | weekdays | weekends | custom
            days_of_week            SMALLINT[],
            -- NULL = all days; [1,2,3,4,5] = Mon-Fri; [0,6] = Sat-Sun (0=Mon..6=Sun)
            scheduled_time          TIME NOT NULL,
            -- UTC time of day when the tour should start
            window_minutes          INTEGER NOT NULL DEFAULT 30,
            -- grace window: session must start within scheduled_time + window_minutes
            assigned_guard_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            is_active               BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_tour_schedules_tenant ON tour_schedules(tenant_id, route_id);
        CREATE INDEX idx_tour_schedules_active  ON tour_schedules(tenant_id) WHERE is_active = TRUE;
        """
    )
    op.execute(_rls("tour_schedules"))

    # ── tour_occurrences ──────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE tour_occurrences (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            schedule_id         UUID NOT NULL REFERENCES tour_schedules(id) ON DELETE CASCADE,
            session_id          UUID REFERENCES patrol_sessions(id) ON DELETE SET NULL,
            scheduled_at        TIMESTAMPTZ NOT NULL,
            window_end          TIMESTAMPTZ NOT NULL,
            status              VARCHAR(20) NOT NULL DEFAULT 'pending',
            -- pending | completed | incomplete | missed | late
            compliance_score    NUMERIC(5,2),
            -- 0-100: percentage of checkpoints scanned; NULL if pending/missed with no session
            missed_checkpoints  SMALLINT,
            notes               TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (schedule_id, scheduled_at)
        );
        CREATE INDEX idx_tour_occurrences_tenant_time  ON tour_occurrences(tenant_id, scheduled_at DESC);
        CREATE INDEX idx_tour_occurrences_schedule     ON tour_occurrences(schedule_id, scheduled_at DESC);
        CREATE INDEX idx_tour_occurrences_session      ON tour_occurrences(session_id);
        CREATE INDEX idx_tour_occurrences_status       ON tour_occurrences(tenant_id, status, scheduled_at DESC);
        """
    )
    op.execute(_rls("tour_occurrences"))

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute(
        """
        INSERT INTO permissions (code, description, category) VALUES
            ('compliance:read',   'View guard tour compliance reports', 'compliance'),
            ('compliance:manage', 'Create/edit tour schedules and occurrences', 'compliance')
        ON CONFLICT (code) DO NOTHING;

        -- All roles: compliance:read
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3,4,5,6) AND p.code = 'compliance:read'
        ON CONFLICT DO NOTHING;

        -- Roles 1-3: compliance:manage
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3) AND p.code = 'compliance:manage'
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS tour_occurrences;
        DROP TABLE IF EXISTS tour_schedules;
        """
    )
