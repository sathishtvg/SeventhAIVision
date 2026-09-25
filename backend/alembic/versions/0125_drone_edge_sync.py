"""Drone patrol, phase 5: the site edge gateway — what it needs from the database
to fly a site's drones, keep going through an outage, and catch up afterwards.

Still additive and still drone-only: one new table, new columns on four drone
tables, and a replacement for the phase 4 runner function. Nothing that existed
before the drone module is touched.

WHO FLIES A SESSION IS DECIDED WHEN IT IS CREATED. A session whose drone sits
behind an edge gateway carries that gateway's id (drone_patrol_sessions.
edge_gateway_id, since 0123). The central drone runner leaves it alone; the
gateway claims it, flies it with the provider adapter installed at the site, and
reports what happened. Moving a drone to another gateway mid-flight does not
move the flight.

CATCHING UP IS ORDERED AND IDEMPOTENT.
  - edge_seq: every flight update a gateway sends is numbered per session. The
    central server applies an update only if its number is higher than the last
    one applied, so a resent update is a no-op and updates cannot be applied out
    of order.
  - drone_sync_receipts: a whole batch resent after a lost response returns the
    stored result instead of being processed again. Receipts are kept only for
    batches that carried something, and only for a week — every item in them is
    idempotent on its own (client_ref, edge_seq, (drone_id, recorded_at)), so the
    receipt is for replaying the answer, not for correctness.

MEDIA RECORDS WHICH GATEWAY HOLDS THE FILE. drone_event_media.edge_gateway_id
lets the server ask the right gateway for an upload, and tells an investigator
where footage that was never uploaded physically is.

GATEWAY HEALTH is what the gateway last reported (backlog, free storage, clock
offset) plus offline_alerted_at, so one outage raises one alert.

drone_runner_tenants() now also returns tenants with a gateway that is not
already OFFLINE, so a gateway that goes silent is noticed even if no drone is
flying and the licence has lapsed.

Revision ID: 0125
Revises: 0124
"""
from alembic import op

revision = "0125"
down_revision = "0124"
branch_labels = None
depends_on = None

LIVE = ("'SCHEDULED','PRECHECK','READY','LAUNCHING','ACTIVE','PAUSED',"
        "'EVENT_DETECTED','RETURNING'")


def _runner_tenants(with_gateways: bool) -> str:
    gateways = """
            UNION
            SELECT g.tenant_id FROM drone_edge_gateways g
             WHERE g.is_active AND g.status <> 'OFFLINE' AND g.last_seen_at IS NOT NULL
    """ if with_gateways else ""
    return f"""
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
            {gateways}
        $$
    """


def upgrade() -> None:
    op.execute("""
        CREATE TABLE drone_sync_receipts (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            gateway_id     UUID NOT NULL REFERENCES drone_edge_gateways(id) ON DELETE CASCADE,
            batch_id       UUID NOT NULL,
            received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            edge_sent_at   TIMESTAMPTZ,
            clock_offset_s NUMERIC(10,3),
            item_count     INTEGER NOT NULL DEFAULT 0,
            accepted       INTEGER NOT NULL DEFAULT 0,
            duplicates     INTEGER NOT NULL DEFAULT 0,
            rejected       INTEGER NOT NULL DEFAULT 0,
            result         JSONB NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT uq_dsr_batch UNIQUE (gateway_id, batch_id)
        )
    """)
    op.execute("CREATE INDEX idx_dsr_gateway_time ON drone_sync_receipts (gateway_id, received_at DESC)")
    op.execute("CREATE INDEX idx_dsr_received ON drone_sync_receipts (received_at)")
    op.execute("ALTER TABLE drone_sync_receipts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE drone_sync_receipts FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_drone_sync_receipts ON drone_sync_receipts
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("GRANT ALL ON drone_sync_receipts TO svc_app")

    op.execute("""
        ALTER TABLE drone_edge_gateways
            ADD COLUMN last_sync_at              TIMESTAMPTZ,
            ADD COLUMN buffer_depth              INTEGER,
            ADD COLUMN oldest_buffered_at        TIMESTAMPTZ,
            ADD COLUMN storage_free_pct          NUMERIC(5,2),
            ADD COLUMN clock_offset_s            NUMERIC(10,3),
            ADD COLUMN health                    JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN heartbeat_timeout_seconds INTEGER NOT NULL DEFAULT 90,
            ADD COLUMN offline_alerted_at        TIMESTAMPTZ,
            ADD CONSTRAINT ck_deg_heartbeat CHECK (heartbeat_timeout_seconds BETWEEN 10 AND 3600),
            ADD CONSTRAINT ck_deg_buffer    CHECK (buffer_depth IS NULL OR buffer_depth >= 0),
            ADD CONSTRAINT ck_deg_storage   CHECK (storage_free_pct IS NULL OR storage_free_pct BETWEEN 0 AND 100)
    """)
    op.execute("""
        ALTER TABLE drone_patrol_sessions
            ADD COLUMN edge_seq        INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN edge_claimed_at TIMESTAMPTZ
    """)
    op.execute(f"CREATE INDEX idx_dps_edge_live ON drone_patrol_sessions (edge_gateway_id, status) "
               f"WHERE edge_gateway_id IS NOT NULL AND status IN ({LIVE})")
    op.execute("ALTER TABLE drone_session_commands ADD COLUMN delivered_at TIMESTAMPTZ")
    op.execute("""
        ALTER TABLE drone_event_media
            ADD COLUMN edge_gateway_id UUID REFERENCES drone_edge_gateways(id) ON DELETE SET NULL
    """)
    op.execute("CREATE INDEX idx_dmedia_edge_pending ON drone_event_media (edge_gateway_id, captured_at) "
               "WHERE sync_state IN ('pending','failed')")

    op.execute(_runner_tenants(with_gateways=True))
    op.execute("REVOKE ALL ON FUNCTION drone_runner_tenants() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION drone_runner_tenants() TO svc_app")


def downgrade() -> None:
    op.execute(_runner_tenants(with_gateways=False))
    op.execute("REVOKE ALL ON FUNCTION drone_runner_tenants() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION drone_runner_tenants() TO svc_app")
    op.execute("DROP INDEX IF EXISTS idx_dmedia_edge_pending")
    op.execute("ALTER TABLE drone_event_media DROP COLUMN IF EXISTS edge_gateway_id")
    op.execute("ALTER TABLE drone_session_commands DROP COLUMN IF EXISTS delivered_at")
    op.execute("DROP INDEX IF EXISTS idx_dps_edge_live")
    op.execute("""
        ALTER TABLE drone_patrol_sessions
            DROP COLUMN IF EXISTS edge_claimed_at,
            DROP COLUMN IF EXISTS edge_seq
    """)
    op.execute("""
        ALTER TABLE drone_edge_gateways
            DROP CONSTRAINT IF EXISTS ck_deg_storage,
            DROP CONSTRAINT IF EXISTS ck_deg_buffer,
            DROP CONSTRAINT IF EXISTS ck_deg_heartbeat,
            DROP COLUMN IF EXISTS offline_alerted_at,
            DROP COLUMN IF EXISTS heartbeat_timeout_seconds,
            DROP COLUMN IF EXISTS health,
            DROP COLUMN IF EXISTS clock_offset_s,
            DROP COLUMN IF EXISTS storage_free_pct,
            DROP COLUMN IF EXISTS oldest_buffered_at,
            DROP COLUMN IF EXISTS buffer_depth,
            DROP COLUMN IF EXISTS last_sync_at
    """)
    op.execute("DROP TABLE IF EXISTS drone_sync_receipts CASCADE")
