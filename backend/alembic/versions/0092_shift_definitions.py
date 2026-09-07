"""Name the shifts a company actually runs, instead of retyping times.

A roster today is built from shift_patterns, and a pattern carries a raw
start_time and a duration. That is enough to generate shifts and nothing more:
every pattern restates "07:00 for 720 minutes" from memory, two patterns for
the same real shift can disagree by an hour with nothing to notice it, and the
grid has no name to print in a cell or a legend.

Agencies do not think that way. They run a Day Shift and a Night Shift, each
with a grace period, a paid or unpaid break, and a rule about whether it earns
overtime — and then put people on them. So: shift_definitions holds the shift
itself, once, and everything else points at it.

    Day Shift    07:00, 720 min, grace 10, break 30, OT not eligible
    Night Shift  19:00, 720 min, grace 10, break 30, OT eligible

STORED AS START + DURATION, NOT START + END. The UI works in start and end
because that is how people describe a shift, but an end time cannot express a
24-hour shift (start 07:00, end 07:00 is either zero hours or twenty-four and
the row cannot say which), and it makes "does this cross midnight" a
comparison rather than a fact. Duration says it exactly, matches
shift_patterns, and lets the API hand the UI a derived end time and a
crosses-midnight flag without either side guessing.

THE FOREIGN KEYS ARE NULLABLE ON PURPOSE. Every existing pattern and every
already-rostered shift predates this table, and rewriting them into invented
definitions would be a migration asserting something nobody said. They stay
null and keep working exactly as before; new work carries the reference, and
the two can coexist indefinitely.

Creation is gated on shift:manage, which Admin, Supervisor, Manager and Super
Admin already hold — no new permission, because deciding what shifts a company
runs is the same authority as rostering them.

Revision ID: 0092
Revises: 0091
"""
from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE shift_definitions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name            VARCHAR(100) NOT NULL,
            -- Matches the vocabulary the scheduler, guard preferences and duty
            -- teams already share, so a definition can be compared against a
            -- guard's preference without translating between two spellings.
            -- 'general' exists for a shift that is neither, such as a relief or
            -- an event shift that should not count toward day/night balance.
            shift_type      VARCHAR(20) NOT NULL DEFAULT 'day'
                CHECK (shift_type IN ('day', 'night', 'general', 'split')),
            start_time      TIME NOT NULL,
            duration_minutes INTEGER NOT NULL
                CHECK (duration_minutes >= 1 AND duration_minutes <= 1440),
            -- Minutes past the start before a guard on this shift counts late.
            -- Null falls back to the site's own grace, then the tenant default,
            -- which is the order attendance already resolves it in.
            grace_minutes   INTEGER CHECK (grace_minutes >= 0 AND grace_minutes <= 240),
            break_minutes   INTEGER NOT NULL DEFAULT 0
                CHECK (break_minutes >= 0 AND break_minutes <= 480),
            -- Whether hours beyond the shift earn overtime. Recorded here
            -- rather than assumed, because a 12-hour shift that already prices
            -- its length is a different deal from one that does not.
            ot_eligible     BOOLEAN NOT NULL DEFAULT FALSE,
            -- Drives the roster grid and its legend. Held per definition so a
            -- company running four shifts can tell them apart at a glance.
            colour          VARCHAR(9),
            notes           TEXT,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Two shifts called "Night Shift" is a data-entry slip every time, not a
    # configuration. Scoped to active rows so a retired definition does not
    # block reusing its name.
    op.execute("""
        CREATE UNIQUE INDEX uq_shift_definition_name
            ON shift_definitions (tenant_id, lower(name))
         WHERE is_active = TRUE
    """)
    op.execute("""
        CREATE INDEX idx_shift_definitions_tenant
            ON shift_definitions (tenant_id, is_active)
    """)

    op.execute("ALTER TABLE shift_definitions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shift_definitions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_shift_definitions ON shift_definitions
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── Adoption points ──────────────────────────────────────────────────────
    #
    # SET NULL rather than CASCADE: retiring a definition must never delete a
    # roster. The shift keeps its own start and end; it just stops being able
    # to say which named shift it came from.
    op.execute("""
        ALTER TABLE shift_patterns
            ADD COLUMN IF NOT EXISTS shift_definition_id UUID
            REFERENCES shift_definitions(id) ON DELETE SET NULL
    """)
    op.execute("""
        ALTER TABLE shifts
            ADD COLUMN IF NOT EXISTS shift_definition_id UUID
            REFERENCES shift_definitions(id) ON DELETE SET NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shifts_definition
            ON shifts (tenant_id, shift_definition_id)
         WHERE shift_definition_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_shifts_definition")
    op.execute("ALTER TABLE shifts DROP COLUMN IF EXISTS shift_definition_id")
    op.execute("ALTER TABLE shift_patterns DROP COLUMN IF EXISTS shift_definition_id")
    op.execute("DROP TABLE IF EXISTS shift_definitions")
