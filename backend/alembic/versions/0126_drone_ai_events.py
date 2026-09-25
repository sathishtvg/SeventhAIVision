"""Drone patrol, phase 6: from detection to drone security event — what the
context and risk engine needs to record.

Still additive and still drone-only: one new table and new columns on two drone
tables. The AI workers, detections, alerts and every other existing table are
read, never changed.

AN EVENT IS MANY OBSERVATIONS. A person in view for twenty seconds is dozens of
worker detections; it is one security event. drone_observations keeps every
detection (from the central AI workers) and every edge-reported sighting that
fed an event — the evidence of what the risk rests on, and the reason a single
frame is treated differently from a sustained sighting.

IDEMPOTENT BY CONSTRAINT. A detection is observed once (unique detection_id); an
edge sighting once (unique client_ref). Re-reading detections after a runner
restart, or a gateway resending its batch, adds nothing.

drone_events gains what grouping and verification need: when the sighting was
last seen and how many times, what was seen (label — a plate, a weapon class),
its attributes (the worker's watchlist verdict, a face match), when it was
verified, and whether it came from the centre or a site gateway.
drone_patrol_sessions.ai_watermark_at is how far the pipeline has read that
flight's detections.

Events recorded before this migration (phase 5 edge events) get an observation
each, so a file still finds its event by the gateway's client_ref.

Revision ID: 0126
Revises: 0125
"""
from alembic import op

revision = "0126"
down_revision = "0125"
branch_labels = None
depends_on = None

AI_MODULES = ("'lpr','face','intrusion','ppe','crowd','fire_smoke','weapon',"
              "'behavior','tampering','abandoned','fall'")


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE drone_observations (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            event_id         UUID NOT NULL REFERENCES drone_events(id) ON DELETE CASCADE,
            session_id       UUID REFERENCES drone_patrol_sessions(id) ON DELETE SET NULL,
            drone_id         UUID REFERENCES drones(id) ON DELETE SET NULL,
            source           VARCHAR(8)  NOT NULL,
            detection_id     UUID,
            client_ref       UUID,
            module_type      VARCHAR(20) NOT NULL,
            label            VARCHAR(80),
            ai_confidence    NUMERIC(5,4),
            detected_at      TIMESTAMPTZ NOT NULL,
            drone_latitude   DOUBLE PRECISION,
            drone_longitude  DOUBLE PRECISION,
            drone_altitude_m NUMERIC(7,2),
            attributes       JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dobs_detection  UNIQUE (detection_id),
            CONSTRAINT uq_dobs_client_ref UNIQUE (client_ref),
            CONSTRAINT ck_dobs_source     CHECK (source IN ('CENTRAL','EDGE')),
            CONSTRAINT ck_dobs_module     CHECK (module_type IN ({AI_MODULES})),
            CONSTRAINT ck_dobs_confidence CHECK (ai_confidence IS NULL OR ai_confidence BETWEEN 0 AND 1),
            CONSTRAINT ck_dobs_origin     CHECK (detection_id IS NOT NULL OR client_ref IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX idx_dobs_event ON drone_observations (event_id, detected_at)")
    op.execute("ALTER TABLE drone_observations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE drone_observations FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_drone_observations ON drone_observations
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("GRANT ALL ON drone_observations TO svc_app")

    op.execute("""
        ALTER TABLE drone_events
            ADD COLUMN label             VARCHAR(80),
            ADD COLUMN last_detected_at  TIMESTAMPTZ,
            ADD COLUMN detection_count   INTEGER NOT NULL DEFAULT 1,
            ADD COLUMN attributes        JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN verified_at       TIMESTAMPTZ,
            ADD COLUMN risk_evaluated_at TIMESTAMPTZ,
            ADD COLUMN source            VARCHAR(8) NOT NULL DEFAULT 'CENTRAL',
            ADD CONSTRAINT ck_devent_source CHECK (source IN ('CENTRAL','EDGE')),
            ADD CONSTRAINT ck_devent_count  CHECK (detection_count >= 1)
    """)
    op.execute("UPDATE drone_events SET last_detected_at = detected_at WHERE last_detected_at IS NULL")
    op.execute("UPDATE drone_events SET source = 'EDGE' WHERE client_ref IS NOT NULL")
    op.execute("""
        CREATE INDEX idx_devent_grouping ON drone_events (session_id, module_type, last_detected_at DESC)
         WHERE verification_state IN ('UNVERIFIED','OBSERVING','VERIFIED')
    """)
    op.execute("""
        INSERT INTO drone_observations
            (tenant_id, event_id, session_id, drone_id, source, detection_id, client_ref, module_type,
             ai_confidence, detected_at, drone_latitude, drone_longitude, drone_altitude_m)
        SELECT tenant_id, id, session_id, drone_id, source, detection_id, client_ref, module_type,
               ai_confidence, detected_at, drone_latitude, drone_longitude, drone_altitude_m
          FROM drone_events
         WHERE client_ref IS NOT NULL OR detection_id IS NOT NULL
    """)

    op.execute("ALTER TABLE drone_patrol_sessions ADD COLUMN ai_watermark_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE drone_patrol_sessions DROP COLUMN IF EXISTS ai_watermark_at")
    op.execute("DROP INDEX IF EXISTS idx_devent_grouping")
    op.execute("""
        ALTER TABLE drone_events
            DROP CONSTRAINT IF EXISTS ck_devent_count,
            DROP CONSTRAINT IF EXISTS ck_devent_source,
            DROP COLUMN IF EXISTS source,
            DROP COLUMN IF EXISTS risk_evaluated_at,
            DROP COLUMN IF EXISTS verified_at,
            DROP COLUMN IF EXISTS attributes,
            DROP COLUMN IF EXISTS detection_count,
            DROP COLUMN IF EXISTS last_detected_at,
            DROP COLUMN IF EXISTS label
    """)
    op.execute("DROP TABLE IF EXISTS drone_observations CASCADE")
