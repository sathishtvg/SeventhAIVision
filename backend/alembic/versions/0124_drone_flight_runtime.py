"""Drone patrol, phase 4: what flying needs — a command queue, runtime state, and
a way for the runner to find its work.

Still additive and still drone-only: one new table, new columns on two drone
tables created in 0123, and one function. Nothing that existed before the drone
module is touched.

COMMANDS ARE QUEUED, NOT EXECUTED IN THE REQUEST. The API never talks to a drone.
Pause, resume, abort, return-to-home and cancel are written to
drone_session_commands and carried out by the drone runner — a separate worker,
so provider code stays out of the web process (the brief's rule) and out of the
scheduler that runs man-down escalation (a stuck provider call must never delay
that). The queue is also the audit trail of who told a drone to do what, and it
is where a site edge gateway will pick commands up in phase 5.

ONE PENDING COMMAND OF EACH KIND PER SESSION. Two operators pressing Abort at the
same moment produce one abort, not two; the second request finds the first.

RUNTIME STATE LIVES ON THE SESSION. provider_state is whatever the provider
adapter needs to carry between ticks (the simulator keeps its clock, battery and
return path there), so a runner restart resumes a flight instead of losing it.

drone_runner_tenants() lets the runner find which tenants have drone work
without scanning every tenant every two seconds. It returns tenant ids and
nothing else, and — flight safety — it includes a tenant whose licence lapsed
while a drone is still in the air: a flight is always carried to the ground.
Locked down the way every SECURITY DEFINER function here is: search_path pinned,
EXECUTE revoked from PUBLIC and granted to svc_app only.

Revision ID: 0124
Revises: 0123
"""
from alembic import op

revision = "0124"
down_revision = "0123"
branch_labels = None
depends_on = None

COMMANDS = "'PAUSE','RESUME','ABORT','RETURN_TO_HOME','CANCEL'"
COMMAND_STATUSES = "'PENDING','DONE','REJECTED','FAILED'"
# Sessions the runner still has to act on. BLOCKED, MISSED and the terminal
# outcomes are finished; everything else is live.
LIVE = ("'SCHEDULED','PRECHECK','READY','LAUNCHING','ACTIVE','PAUSED',"
        "'EVENT_DETECTED','RETURNING'")


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE drone_session_commands (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            session_id           UUID NOT NULL REFERENCES drone_patrol_sessions(id) ON DELETE CASCADE,
            command              VARCHAR(16) NOT NULL,
            status               VARCHAR(10) NOT NULL DEFAULT 'PENDING',
            reason               TEXT,
            requested_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            requested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            processed_at         TIMESTAMPTZ,
            result               TEXT,
            CONSTRAINT ck_dcmd_command CHECK (command IN ({COMMANDS})),
            CONSTRAINT ck_dcmd_status  CHECK (status  IN ({COMMAND_STATUSES}))
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_dcmd_one_pending ON drone_session_commands (session_id, command) "
               "WHERE status = 'PENDING'")
    op.execute("CREATE INDEX idx_dcmd_pending ON drone_session_commands (requested_at) "
               "WHERE status = 'PENDING'")
    op.execute("CREATE INDEX idx_dcmd_session ON drone_session_commands (session_id, requested_at)")
    op.execute("ALTER TABLE drone_session_commands ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE drone_session_commands FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_drone_session_commands ON drone_session_commands
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("GRANT ALL ON drone_session_commands TO svc_app")

    # Runtime state on the drone tables phase 2 created.
    op.execute("""
        ALTER TABLE drone_patrol_sessions
            ADD COLUMN provider_state JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN last_tick_at   TIMESTAMPTZ,
            ADD COLUMN landed_at      TIMESTAMPTZ,
            ADD COLUMN comms_lost_at  TIMESTAMPTZ,
            ADD COLUMN failure_code   VARCHAR(40)
    """)
    op.execute(f"CREATE INDEX idx_dps_live ON drone_patrol_sessions (tenant_id, status) "
               f"WHERE status IN ({LIVE})")
    # When a communication-lost alert was last raised for this drone, so one
    # outage raises one alert — cleared when the drone is heard from again.
    op.execute("ALTER TABLE drones ADD COLUMN comms_alerted_at TIMESTAMPTZ")

    op.execute(f"""
        CREATE OR REPLACE FUNCTION drone_runner_tenants()
        RETURNS TABLE (tenant_id UUID)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT l.tenant_id
              FROM drone_module_licenses l
              JOIN tenants t ON t.id = l.tenant_id AND t.is_active
             WHERE l.is_enabled AND (l.expires_at IS NULL OR l.expires_at > now())
            UNION
            SELECT s.tenant_id FROM drone_patrol_sessions s WHERE s.status IN ({LIVE})
            UNION
            SELECT c.tenant_id FROM drone_session_commands c WHERE c.status = 'PENDING'
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION drone_runner_tenants() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION drone_runner_tenants() TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS drone_runner_tenants()")
    op.execute("ALTER TABLE drones DROP COLUMN IF EXISTS comms_alerted_at")
    op.execute("DROP INDEX IF EXISTS idx_dps_live")
    op.execute("""
        ALTER TABLE drone_patrol_sessions
            DROP COLUMN IF EXISTS failure_code,
            DROP COLUMN IF EXISTS comms_lost_at,
            DROP COLUMN IF EXISTS landed_at,
            DROP COLUMN IF EXISTS last_tick_at,
            DROP COLUMN IF EXISTS provider_state
    """)
    op.execute("DROP TABLE IF EXISTS drone_session_commands CASCADE")
