"""Drone patrol, phase 8: "verify with drone" — an officer's request for a flying
drone to hold where it is and look again.

Still additive and drone-only: one new table. Incidents, their notes, status
history and guard dispatch are the platform's own and are used as they are — no
parallel incident system, no second dispatch module.

A REQUEST, NOT A FLIGHT COMMAND. The officer asks; the system checks the flight
(airborne and active, still near the spot, the provider can hold and resume,
enough battery to hold and still come home) and only then queues an ordinary
PAUSE through the flight command queue — the same queue, the same rules, the
same gateway relay as an operator's pause. When the hold has lasted its time the
runner queues the RESUME. Nothing here can steer the aircraft anywhere, and
nothing bypasses a flight-safety rule.

ONE AT A TIME PER FLIGHT, by a partial unique index on the active states.

Revision ID: 0128
Revises: 0127
"""
from alembic import op

revision = "0128"
down_revision = "0127"
branch_labels = None
depends_on = None

STATUSES = "'REQUESTED','HOLDING','COMPLETED','FAILED','CANCELLED'"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE drone_verification_requests (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            event_id             UUID NOT NULL REFERENCES drone_events(id) ON DELETE CASCADE,
            session_id           UUID NOT NULL REFERENCES drone_patrol_sessions(id) ON DELETE CASCADE,
            requested_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            reason               TEXT,
            hold_seconds         INTEGER NOT NULL,
            status               VARCHAR(10) NOT NULL DEFAULT 'REQUESTED',
            pause_command_id     UUID REFERENCES drone_session_commands(id) ON DELETE SET NULL,
            resume_command_id    UUID REFERENCES drone_session_commands(id) ON DELETE SET NULL,
            detections_before    INTEGER,
            risk_before          VARCHAR(10),
            started_at           TIMESTAMPTZ,
            ends_at              TIMESTAMPTZ,
            completed_at         TIMESTAMPTZ,
            result               JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_dvr_status CHECK (status IN ({STATUSES})),
            CONSTRAINT ck_dvr_hold   CHECK (hold_seconds BETWEEN 5 AND 300)
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_dvr_one_active ON drone_verification_requests (session_id) "
               "WHERE status IN ('REQUESTED','HOLDING')")
    op.execute("CREATE INDEX idx_dvr_active ON drone_verification_requests (created_at) "
               "WHERE status IN ('REQUESTED','HOLDING')")
    op.execute("CREATE INDEX idx_dvr_event ON drone_verification_requests (event_id, created_at)")
    op.execute("ALTER TABLE drone_verification_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE drone_verification_requests FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_drone_verification_requests ON drone_verification_requests
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("GRANT ALL ON drone_verification_requests TO svc_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS drone_verification_requests CASCADE")
