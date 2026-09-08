"""Man-down: notice when a guard stops moving, and act on it.

A lone officer on a night shift collapses in a plant room. Nothing in this
system would know. The panic button is the only route to help and it requires
a conscious guard with a free hand — which is exactly the case man-down exists
to cover.

THE PHONE DETECTS; THE SERVER DECIDES. The app watches its accelerometer and
raises an event when the guard stops moving, or is knocked and then lies still.
That event starts a countdown the guard can cancel — most man-down triggers are
a phone left on a desk, and a system that escalates every one of them is a
system people turn off. But escalation must NOT depend on the phone calling
back: a handset that shattered on impact, ran flat, or lost signal is precisely
the case that matters most. So the countdown deadline is stored, and a
scheduler sweep escalates anything past it. The phone escalating itself is the
fast path, not the only path.

CANCELLED IS RECORDED, NOT DELETED. A guard cancelling six times a shift means
the thresholds are wrong for how they work, and that is only visible if the
cancellations are kept. They are also the answer to "why did nobody know" if a
real event was cancelled by accident.

OFF BY DEFAULT. Turning on continuous sensor monitoring across every guard's
phone is a battery and privacy decision that belongs to the company, not to an
upgrade. The setting ships disabled; the app does not monitor until a tenant
turns it on.

Revision ID: 0100
Revises: 0099
"""
from alembic import op

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE man_down_events (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            guard_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            -- Which shift and site, when the guard is on one. A guard whose
            -- phone triggers off duty is still worth recording; hiding it
            -- would lose the evidence that the thresholds are wrong.
            shift_id    UUID REFERENCES shifts(id) ON DELETE SET NULL,
            site_id     UUID REFERENCES sites(id) ON DELETE SET NULL,

            trigger     VARCHAR(20) NOT NULL
                CHECK (trigger IN ('no_motion', 'impact', 'tilt', 'manual')),
            detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- Where the phone was. Nullable: a basement plant room has no fix,
            -- and refusing the event for want of a location would defeat it.
            latitude    DOUBLE PRECISION,
            longitude   DOUBLE PRECISION,
            accuracy_m  DOUBLE PRECISION,
            -- A phone at 2% that stopped moving is a different story from one
            -- at 80%, and the supervisor deciding whether to send somebody
            -- wants to know which.
            battery_level INTEGER CHECK (battery_level IS NULL
                                         OR battery_level BETWEEN 0 AND 100),
            device_info TEXT,

            status      VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'cancelled', 'escalated',
                                  'acknowledged', 'resolved')),

            -- When the countdown runs out. Stored rather than computed from a
            -- setting, because the setting can change while an event is live
            -- and the guard was shown a specific number of seconds.
            escalate_at TIMESTAMPTZ NOT NULL,

            cancelled_at TIMESTAMPTZ,
            escalated_at TIMESTAMPTZ,
            -- Whether the phone escalated itself or the server swept it up.
            -- The difference says whether the handset survived.
            escalated_by_server BOOLEAN NOT NULL DEFAULT FALSE,

            acknowledged_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            acknowledged_at TIMESTAMPTZ,
            resolved_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            resolved_at TIMESTAMPTZ,
            outcome     VARCHAR(20)
                CHECK (outcome IS NULL OR outcome IN
                       ('false_alarm', 'guard_ok', 'injury', 'other')),
            notes       TEXT,

            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # The sweep's query: everything still counting down whose deadline passed.
    op.execute("""
        CREATE INDEX idx_man_down_pending
            ON man_down_events (escalate_at)
         WHERE status = 'pending'
    """)
    op.execute("""
        CREATE INDEX idx_man_down_live
            ON man_down_events (tenant_id, detected_at DESC)
         WHERE status IN ('pending', 'escalated', 'acknowledged')
    """)
    op.execute("""
        CREATE INDEX idx_man_down_guard
            ON man_down_events (tenant_id, guard_user_id, detected_at DESC)
    """)
    op.execute("ALTER TABLE man_down_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE man_down_events FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_man_down_events ON man_down_events
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # A guard cannot have two live man-down events. The second trigger from a
    # phone that is already counting down is the same incident, and two rows
    # would mean two escalations for one guard on one floor.
    op.execute("""
        CREATE UNIQUE INDEX uq_man_down_one_live_per_guard
            ON man_down_events (guard_user_id)
         WHERE status IN ('pending', 'escalated', 'acknowledged')
    """)

    # ── Permissions ──────────────────────────────────────────────────────────
    #
    # A guard reports and cancels their own event — that is the phone acting on
    # their behalf, so it reaches every operational role. Acknowledging and
    # closing one out is the control room's job.
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('mandown:report',  'Raise and cancel a man-down event', 'guard-ops'),
          ('mandown:read',    'View man-down events', 'guard-ops'),
          ('mandown:manage',  'Acknowledge, resolve and configure man-down', 'guard-ops')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 4, 5, 8) AND p.code IN ('mandown:report', 'mandown:read')
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 4, 8) AND p.code = 'mandown:manage'
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 6 AND p.code = 'mandown:read'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    codes = ("mandown:report", "mandown:read", "mandown:manage")
    joined = ", ".join(f"'{c}'" for c in codes)
    op.execute(f"""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ({joined}))
    """)
    op.execute(f"DELETE FROM permissions WHERE code IN ({joined})")
    op.execute("DROP TABLE IF EXISTS man_down_events")
