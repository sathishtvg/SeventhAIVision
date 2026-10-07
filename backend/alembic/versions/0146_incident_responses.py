"""Guard response: a sending as its own record, its steps, and who is told when it is late.

Additive: four new tables and two permissions. No existing table, policy or
permission is altered (LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 4).

  an incident is DISPATCHED          a RESPONSE: one guard, one sending
  (the existing endpoint,     ───►   SENT ─► ACCEPTED ─► EN_ROUTE ─► ARRIVED
   by a person, as before)                └─► DECLINED        └─► STOOD_DOWN

THE DISPATCH IS STILL THE EXISTING ONE. A person sends a guard through the
endpoint that has always done it, and the incident's own columns —
`dispatched_guard_id`, `dispatched_at`, `guard_arrived_at`, `status` — stay the
record other screens read. A response row is made for that sending the first
time anything looks at it, and what the guard then does is written through to
those same columns.

A STEP IS ADDED TO AND NEVER REWRITTEN. `incident_response_steps` takes SELECT
and INSERT from the application and nothing else: who accepted, declined, set
off, arrived or reported, when, and from where.

AN ESCALATION POLICY SAYS WHO IS TOLD, AND WHEN. Nothing here acts on an
incident. A clock that runs out, and a policy step that falls due, are each
recorded once in `incident_escalations` — which clock or which policy, when it
fell due, who it was addressed to and how many people that was — and that
record is added to and never rewritten. Each also writes a row to the existing
`escalation_events` table, which until this phase nothing wrote to, and it is
there that whether the telling went out is marked: that table's columns cannot
say which policy, or which role.

SUPER ADMIN (1) AND CLIENT (7) GET NO PERMISSION HERE.

Revision ID: 0146
Revises: 0145
"""
from alembic import op

revision = "0146"
down_revision = "0145"
branch_labels = None
depends_on = None

STATES = "'SENT','ACCEPTED','DECLINED','EN_ROUTE','ARRIVED','STOOD_DOWN'"
STEPS = "'SENT','ACCEPTED','DECLINED','EN_ROUTE','ARRIVED','REPORTED','STOOD_DOWN'"
TRIGGERS = "'NOT_ACKNOWLEDGED','NOT_ARRIVED','NOT_RESOLVED'"
CLOCKS = "'ACKNOWLEDGE','ARRIVAL','RESOLVE'"

PERMISSIONS = [
    ("response:read", "View guard responses, their steps and the response clocks", "response"),
    ("response:act", "Accept, decline and report on a dispatch one has been sent on", "response"),
]

#: What a guard's steps and a stand-down change. Nothing else of a response can
#: be changed by the application: not which incident, which guard or when.
RESPONSE_CHANGES = ("state, accepted_at, declined_at, decline_reason, en_route_at, arrived_at, stood_down_at, "
                    "stood_down_by_user_id, stand_down_reason, updated_at")


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE incident_responses (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            incident_id           UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            site_id               UUID REFERENCES sites(id) ON DELETE SET NULL,
            guard_user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
            -- The incident's own dispatched_at: one response per sending.
            dispatched_at         TIMESTAMPTZ NOT NULL,
            state                 VARCHAR(12) NOT NULL DEFAULT 'SENT',
            accepted_at           TIMESTAMPTZ,
            declined_at           TIMESTAMPTZ,
            decline_reason        TEXT,
            en_route_at           TIMESTAMPTZ,
            arrived_at            TIMESTAMPTZ,
            stood_down_at         TIMESTAMPTZ,
            stood_down_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            stand_down_reason     TEXT,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_response_sending UNIQUE (incident_id, dispatched_at),
            CONSTRAINT ck_response_state CHECK (state IN ({STATES})),
            -- Not coming, and being called off, each say why.
            CONSTRAINT ck_response_declined CHECK (
                state <> 'DECLINED' OR (declined_at IS NOT NULL AND decline_reason IS NOT NULL
                                        AND btrim(decline_reason) <> '')),
            CONSTRAINT ck_response_stood_down CHECK (
                state <> 'STOOD_DOWN' OR (stood_down_at IS NOT NULL AND stand_down_reason IS NOT NULL
                                          AND btrim(stand_down_reason) <> ''))
        )
    """)
    op.execute("CREATE INDEX idx_response_incident ON incident_responses (incident_id, dispatched_at DESC)")
    op.execute("CREATE INDEX idx_response_guard_open ON incident_responses (guard_user_id) "
               "WHERE state NOT IN ('DECLINED','STOOD_DOWN')")

    op.execute(f"""
        CREATE TABLE incident_response_steps (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            response_id   UUID NOT NULL REFERENCES incident_responses(id) ON DELETE CASCADE,
            incident_id   UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            step          VARCHAR(12) NOT NULL,
            -- NULL when the platform recorded the step itself: a response that
            -- was superseded by a second sending.
            actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            actor_role    SMALLINT,
            note          TEXT,
            latitude      DOUBLE PRECISION,
            longitude     DOUBLE PRECISION,
            occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_respstep_step  CHECK (step IN ({STEPS})),
            CONSTRAINT ck_respstep_point CHECK (
                (latitude IS NULL) = (longitude IS NULL)
                AND (latitude IS NULL OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180))),
            -- A report says something.
            CONSTRAINT ck_respstep_report CHECK (step <> 'REPORTED' OR (note IS NOT NULL AND btrim(note) <> ''))
        )
    """)
    op.execute("CREATE INDEX idx_respstep_response ON incident_response_steps (response_id, occurred_at)")
    op.execute("CREATE INDEX idx_respstep_incident ON incident_response_steps (incident_id, occurred_at)")

    op.execute(f"""
        CREATE TABLE escalation_policies (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name               VARCHAR(120) NOT NULL,
            -- NULL: every site. NULL: every severity.
            site_id            UUID REFERENCES sites(id) ON DELETE CASCADE,
            severity           VARCHAR(10),
            trigger            VARCHAR(16) NOT NULL,
            after_seconds      INTEGER NOT NULL,
            -- Who is told: everybody in a role, or one person. One of them.
            notify_role_id     SMALLINT,
            notify_user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_escpol_name     CHECK (btrim(name) <> ''),
            CONSTRAINT ck_escpol_trigger  CHECK (trigger IN ({TRIGGERS})),
            CONSTRAINT ck_escpol_severity CHECK (severity IS NULL
                                                 OR severity IN ('info','low','medium','high','critical')),
            CONSTRAINT ck_escpol_after    CHECK (after_seconds BETWEEN 30 AND 604800),
            -- Never the platform owner, and never a customer's client.
            CONSTRAINT ck_escpol_role     CHECK (notify_role_id IS NULL OR notify_role_id IN (2, 3, 4, 5, 6, 8)),
            CONSTRAINT ck_escpol_whom     CHECK (
                is_active = FALSE OR notify_role_id IS NOT NULL OR notify_user_id IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX idx_escpol_active ON escalation_policies (tenant_id) WHERE is_active")

    op.execute(f"""
        CREATE TABLE incident_escalations (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            incident_id         UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            site_id             UUID REFERENCES sites(id) ON DELETE SET NULL,
            -- A clock that ran out, or a step of a policy that fell due.
            kind                VARCHAR(12) NOT NULL,
            clock               VARCHAR(12) NOT NULL,
            policy_id           UUID REFERENCES escalation_policies(id) ON DELETE SET NULL,
            -- The policy as it was named then: a policy can be renamed later.
            policy_name         VARCHAR(120),
            -- The sending it is about, for the arrival clock: an incident sent
            -- twice can be late twice. NULL for the clocks that run from opening.
            sending_at          TIMESTAMPTZ,
            due_at              TIMESTAMPTZ NOT NULL,
            notify_role_id      SMALLINT,
            notify_user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
            -- How many people it was addressed to, when it was recorded.
            recipients          INTEGER NOT NULL DEFAULT 0,
            escalation_event_id UUID REFERENCES escalation_events(id) ON DELETE SET NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_incesc_kind       CHECK (kind IN ('SLA_BREACH','POLICY_STEP')),
            CONSTRAINT ck_incesc_clock      CHECK (clock IN ({CLOCKS})),
            CONSTRAINT ck_incesc_policy     CHECK (kind <> 'POLICY_STEP' OR policy_name IS NOT NULL),
            CONSTRAINT ck_incesc_recipients CHECK (recipients >= 0)
        )
    """)
    op.execute("CREATE INDEX idx_incesc_incident ON incident_escalations (incident_id, created_at)")
    op.execute("CREATE INDEX idx_incesc_recent ON incident_escalations (tenant_id, created_at DESC)")
    # Each is recorded once. NULLs are distinct in these, so a policy removed
    # with its tenant leaves nothing that could collide.
    op.execute("CREATE UNIQUE INDEX uq_incesc_breach ON incident_escalations (incident_id, clock) "
               "WHERE kind = 'SLA_BREACH' AND sending_at IS NULL")
    op.execute("CREATE UNIQUE INDEX uq_incesc_breach_sending ON incident_escalations "
               "(incident_id, clock, sending_at) WHERE kind = 'SLA_BREACH' AND sending_at IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX uq_incesc_step ON incident_escalations (incident_id, policy_id) "
               "WHERE kind = 'POLICY_STEP' AND sending_at IS NULL")
    op.execute("CREATE UNIQUE INDEX uq_incesc_step_sending ON incident_escalations "
               "(incident_id, policy_id, sending_at) WHERE kind = 'POLICY_STEP' AND sending_at IS NOT NULL")

    for table in ("incident_responses", "incident_response_steps", "escalation_policies",
                  "incident_escalations"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)
        # The REVOKE is what does it: default privileges hand the application
        # UPDATE and DELETE on every new table.
        op.execute(f"REVOKE ALL ON {table} FROM svc_app")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO svc_app")
    op.execute(f"GRANT UPDATE ({RESPONSE_CHANGES}) ON incident_responses TO svc_app")
    # A policy is changed and retired, never removed.
    op.execute("GRANT UPDATE ON escalation_policies TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    # Whoever can be sent can answer: Guard (5), and the Supervisor (3),
    # Operator (4), Admin (2) and Manager (8) who are sometimes sent themselves.
    # Viewer (6) reads.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'response:read'), (2, 'response:act'),
                  (8, 'response:read'), (8, 'response:act'),
                  (3, 'response:read'), (3, 'response:act'),
                  (4, 'response:read'), (4, 'response:act'),
                  (5, 'response:act'),
                  (6, 'response:read')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code LIKE 'response:%')
    """)
    op.execute("DELETE FROM permissions WHERE code LIKE 'response:%'")
    for table in ("incident_escalations", "escalation_policies", "incident_response_steps",
                  "incident_responses"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
