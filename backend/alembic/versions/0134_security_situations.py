"""AI security intelligence, phase 4: events that belong together.

Additive: three new tables. `security_events.status` — a column of this
layer's own table, added in 0132 for exactly this — starts being set to LINKED.
No table that existed before the layer is altered.

A SITUATION IS ONE THING HAPPENING. A denied door, the camera above it and the
drone overhead a minute later are, to the officer, one matter to deal with; today
they are three alerts in a list of eight thousand. A situation is the record
that says so, and `security_situation_events` is the record of *why*: every
event's place in a situation carries the method that put it there, the reason in
words, and how sure the link is. A link nobody can explain is not made.

AN EVENT BELONGS TO AT MOST ONE SITUATION — `uq_secsitev_event` — so nothing is
counted twice and an operator never finds the same alert under two headings.
The first event of a situation is linked too, as FIRST_EVENT, so that "the
events of a situation" is always one query on one table.

`is_duplicate` marks an event that says nothing the situation did not already
have: the same camera raising the same alert again, or a drone event's own
alert. Those are what an operator no longer has to look at one by one. They are
still here, and their alerts are untouched.

A SITUATION SETTLES; IT IS NOT CLOSED HERE. ACTIVE means it still takes new
events. SETTLED means it has been quiet long enough that a new event is a new
matter. What a person has decided about it is a different question with a
different column, in phase 7.

`security_camera_links` is where an administrator says two cameras are next to
each other and how long the walk is. Cameras with coordinates are related by
distance without being told; this is for the cases distance gets wrong — two
cameras either side of a wall — and for sites whose cameras have no coordinates.
`camera_a < camera_b` so that a pair has one row whichever way round it is said.

Revision ID: 0134
Revises: 0133
"""
from alembic import op

revision = "0134"
down_revision = "0133"
branch_labels = None
depends_on = None

SEVERITIES = "'info','low','medium','high','critical'"
STATUSES = "'ACTIVE','SETTLED'"
METHODS = ("'FIRST_EVENT','SAME_ALERT','DRONE_CCTV','SAME_IDENTITY','SAME_SOURCE_REPEAT',"
           "'ACCESS_AT_CAMERA','ALARM_AT_CAMERA','ADJACENT_CAMERA','NEAR_POSITION','PATROL_FINDING',"
           "'GUARD_SOS_AT_SITE'")
TABLES = ("security_situations", "security_situation_events", "security_camera_links")


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE security_situations (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id              UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id                UUID REFERENCES sites(id)   ON DELETE SET NULL,
            situation_number       VARCHAR(24) NOT NULL,
            title                  VARCHAR(255) NOT NULL,
            status                 VARCHAR(12) NOT NULL DEFAULT 'ACTIVE',
            severity               VARCHAR(10) NOT NULL,
            started_at             TIMESTAMPTZ NOT NULL,
            last_event_at          TIMESTAMPTZ NOT NULL,
            event_count            INTEGER NOT NULL DEFAULT 0,
            duplicate_count        INTEGER NOT NULL DEFAULT 0,
            source_types           TEXT[] NOT NULL DEFAULT '{{}}',
            primary_camera_id      UUID REFERENCES cameras(id) ON DELETE SET NULL,
            location_label         VARCHAR(255),
            latitude               DOUBLE PRECISION,
            longitude              DOUBLE PRECISION,
            correlation_confidence NUMERIC(5,4),
            settled_at             TIMESTAMPTZ,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_secsit_number     UNIQUE (tenant_id, situation_number),
            CONSTRAINT ck_secsit_status     CHECK (status IN ({STATUSES})),
            CONSTRAINT ck_secsit_severity   CHECK (severity IN ({SEVERITIES})),
            CONSTRAINT ck_secsit_counts     CHECK (event_count >= 0 AND duplicate_count >= 0
                                                   AND duplicate_count <= event_count),
            CONSTRAINT ck_secsit_times      CHECK (last_event_at >= started_at),
            CONSTRAINT ck_secsit_confidence CHECK (correlation_confidence IS NULL
                                                   OR correlation_confidence BETWEEN 0 AND 1)
        )
    """)
    op.execute("CREATE INDEX idx_secsit_tenant_status ON security_situations (tenant_id, status, last_event_at DESC)")
    op.execute("CREATE INDEX idx_secsit_site ON security_situations (tenant_id, site_id, last_event_at DESC)")

    op.execute(f"""
        CREATE TABLE security_situation_events (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            situation_id     UUID NOT NULL REFERENCES security_situations(id) ON DELETE CASCADE,
            event_id         UUID NOT NULL REFERENCES security_events(id) ON DELETE CASCADE,
            method           VARCHAR(24) NOT NULL,
            reason           TEXT NOT NULL,
            confidence       NUMERIC(5,4) NOT NULL,
            matched_event_id UUID REFERENCES security_events(id) ON DELETE SET NULL,
            is_duplicate     BOOLEAN NOT NULL DEFAULT FALSE,
            linked_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_secsitev_event      UNIQUE (event_id),
            CONSTRAINT ck_secsitev_method     CHECK (method IN ({METHODS})),
            CONSTRAINT ck_secsitev_reason     CHECK (length(btrim(reason)) > 0),
            CONSTRAINT ck_secsitev_confidence CHECK (confidence BETWEEN 0 AND 1)
        )
    """)
    op.execute("CREATE INDEX idx_secsitev_situation ON security_situation_events (situation_id, linked_at)")

    op.execute("""
        CREATE TABLE security_camera_links (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            camera_a           UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            camera_b           UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            walk_seconds       INTEGER NOT NULL,
            note               VARCHAR(255),
            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_seccamlink_pair  UNIQUE (camera_a, camera_b),
            CONSTRAINT ck_seccamlink_order CHECK (camera_a < camera_b),
            CONSTRAINT ck_seccamlink_walk  CHECK (walk_seconds BETWEEN 1 AND 3600)
        )
    """)
    op.execute("CREATE INDEX idx_seccamlink_b ON security_camera_links (camera_b)")

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)
        op.execute(f"GRANT ALL ON {table} TO svc_app")

    # The runner asks for a tenant's unlinked events on every pass.
    op.execute("CREATE INDEX idx_secevent_new ON security_events (tenant_id, occurred_at) WHERE status = 'NEW'")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_secevent_new")
    op.execute("DROP TABLE IF EXISTS security_camera_links CASCADE")
    op.execute("DROP TABLE IF EXISTS security_situation_events CASCADE")
    op.execute("DROP TABLE IF EXISTS security_situations CASCADE")
    op.execute("UPDATE security_events SET status = 'NEW' WHERE status = 'LINKED'")
