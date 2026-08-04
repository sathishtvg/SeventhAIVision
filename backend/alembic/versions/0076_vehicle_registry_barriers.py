"""0076 — Vehicle registry depth + barrier/gate actuation

Three linked gaps from the Enterprise VMS scope, built as one chain because
they only make sense together: ANPR reads a plate -> the registry says who
that vehicle is -> the decision engine applies policy -> the barrier opens.

1. VEHICLE REGISTRY (extends watchlist_entries, does NOT fork it)
   The existing table carried only plate_number + list_type('allow'|'block').
   The scope needs ten categories plus owner/company/vehicle/validity data.
   Extending is deliberate: ai-worker/worker/tasks/lpr_task.py::_lookup_watchlist
   already reads list_type from this table, so a parallel registry table would
   create two sources of truth for "is this plate blocked" — unacceptable for
   something that physically opens a gate.

   list_type stays as the COARSE enforcement signal the LPR worker already
   understands (blacklist -> 'block', everything else -> 'allow'), maintained
   by a trigger so it can never drift from category no matter who writes.
   The FINE-grained behaviour (watchlist -> alert an operator, visitor ->
   check pre-registration, expired validity -> hold for operator) lives in
   the new decision engine, which reads `category` directly. This split means
   the AI worker needs no change at all and loses no existing behaviour.

2. barriers — one row per physical gate/boom, carrying its device binding
   (vendor + host/port/credentials or relay channel). Credentials are stored
   via app.core.crypto.encrypt_secret (Fernet), the same helper SSO already
   uses — never plaintext.

3. barrier_commands — an append-only audit of every actuation attempt, its
   source (operator vs. decision engine vs. schedule), the deciding verdict,
   and whether the device actually accepted it. A device that lets vehicles
   onto a site must be fully accountable; this is the Phase 16 audit
   requirement applied to the actuator itself.
"""
from __future__ import annotations

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


VEHICLE_CATEGORIES = (
    "whitelist", "blacklist", "watchlist", "vip", "staff",
    "visitor", "contractor", "emergency", "government", "unknown",
)
# Only an explicit blacklisting maps to the worker's hard 'block' signal.
# Everything else is 'allow' at the coarse layer; nuance is the decision
# engine's job (see module docstring).
_BLOCKING_CATEGORIES = ("blacklist",)


def upgrade() -> None:
    # ── 1. Vehicle registry fields ────────────────────────────────────────
    cats = ", ".join(f"'{c}'" for c in VEHICLE_CATEGORIES)
    op.execute(f"""
        ALTER TABLE watchlist_entries
          ADD COLUMN IF NOT EXISTS category      VARCHAR(20) NOT NULL DEFAULT 'watchlist',
          ADD COLUMN IF NOT EXISTS owner_name    VARCHAR(255),
          ADD COLUMN IF NOT EXISTS company       VARCHAR(255),
          ADD COLUMN IF NOT EXISTS vehicle_type  VARCHAR(30),
          ADD COLUMN IF NOT EXISTS vehicle_color VARCHAR(30),
          ADD COLUMN IF NOT EXISTS valid_from    DATE,
          ADD COLUMN IF NOT EXISTS valid_to      DATE,
          ADD COLUMN IF NOT EXISTS remarks       TEXT
    """)

    # Backfill from the pre-existing binary list_type before adding the CHECK,
    # so historical rows land in a sensible category rather than the default.
    op.execute("UPDATE watchlist_entries SET category = 'whitelist' WHERE list_type = 'allow'")
    op.execute("UPDATE watchlist_entries SET category = 'blacklist' WHERE list_type = 'block'")

    op.execute("ALTER TABLE watchlist_entries DROP CONSTRAINT IF EXISTS watchlist_entries_category_check")
    op.execute(f"""
        ALTER TABLE watchlist_entries
          ADD CONSTRAINT watchlist_entries_category_check
          CHECK (category IN ({cats}))
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_watchlist_entries_category
            ON watchlist_entries(tenant_id, category) WHERE is_active = TRUE
    """)

    # Trigger keeps list_type derived from category. Enforced in the database
    # rather than the application because the LPR worker reads list_type
    # directly on a hot path and must never see a value that contradicts the
    # category an admin actually chose.
    blocking = ", ".join(f"'{c}'" for c in _BLOCKING_CATEGORIES)
    op.execute(f"""
        CREATE OR REPLACE FUNCTION watchlist_sync_list_type() RETURNS trigger AS $$
        BEGIN
            NEW.list_type := CASE WHEN NEW.category IN ({blocking}) THEN 'block' ELSE 'allow' END;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_watchlist_sync_list_type ON watchlist_entries")
    op.execute("""
        CREATE TRIGGER trg_watchlist_sync_list_type
            BEFORE INSERT OR UPDATE OF category ON watchlist_entries
            FOR EACH ROW EXECUTE FUNCTION watchlist_sync_list_type()
    """)

    # ── 2. barriers ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS barriers (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id            UUID REFERENCES sites(id)        ON DELETE SET NULL,
            camera_id          UUID REFERENCES cameras(id)      ON DELETE SET NULL,
            door_id            UUID REFERENCES access_doors(id) ON DELETE SET NULL,
            name               VARCHAR(255) NOT NULL,
            lane_direction     VARCHAR(15) NOT NULL DEFAULT 'entry'
                                   CHECK (lane_direction IN ('entry', 'exit', 'bidirectional')),
            vendor             VARCHAR(20) NOT NULL
                                   CHECK (vendor IN ('hikvision', 'dahua', 'relay', 'simulator')),
            host               VARCHAR(255),
            port               INTEGER,
            username           VARCHAR(100),
            password_encrypted TEXT,
            relay_channel      INTEGER,
            pulse_ms           INTEGER NOT NULL DEFAULT 1000,
            auto_open_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            last_status        VARCHAR(20),
            last_status_at     TIMESTAMPTZ,
            last_error         TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, name)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_barriers_tenant_site ON barriers(tenant_id, site_id)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_barriers_camera
            ON barriers(camera_id) WHERE camera_id IS NOT NULL
    """)

    # ── 3. barrier_commands ───────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS barrier_commands (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            barrier_id        UUID NOT NULL REFERENCES barriers(id) ON DELETE CASCADE,
            command           VARCHAR(20) NOT NULL
                                  CHECK (command IN ('open', 'close', 'hold_open',
                                                     'release_hold', 'emergency_override', 'status')),
            source            VARCHAR(20) NOT NULL
                                  CHECK (source IN ('operator', 'decision_engine', 'schedule', 'api')),
            decision          VARCHAR(20),
            plate_number      VARCHAR(20),
            reason            TEXT,
            issued_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            succeeded         BOOLEAN,
            error             TEXT,
            latency_ms        INTEGER,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_barrier_commands_barrier
            ON barrier_commands(tenant_id, barrier_id, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_barrier_commands_plate
            ON barrier_commands(tenant_id, plate_number) WHERE plate_number IS NOT NULL
    """)

    for table in ("barriers", "barrier_commands"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)

    # ── 4. Permissions ────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('barrier:read',    'View barriers, live status and command history', 'access'),
          ('barrier:operate', 'Manually open/close/hold a barrier',             'access'),
          ('barrier:manage',  'Add/edit/delete barriers and device settings',   'access')
        ON CONFLICT (code) DO NOTHING
    """)
    # read: everyone operational (a guard should see whether the gate is open).
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 4, 5, 6, 8) AND p.code = 'barrier:read'
        ON CONFLICT DO NOTHING
    """)
    # operate: the command-centre operator opening a gate IS the core use case.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 4, 8) AND p.code = 'barrier:operate'
        ON CONFLICT DO NOTHING
    """)
    # manage: device credentials + auto-open policy — admin tier only.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 8) AND p.code = 'barrier:manage'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions WHERE permission_id IN (
            SELECT id FROM permissions
            WHERE code IN ('barrier:read', 'barrier:operate', 'barrier:manage')
        )
    """)
    op.execute("""
        DELETE FROM permissions
        WHERE code IN ('barrier:read', 'barrier:operate', 'barrier:manage')
    """)

    op.execute("DROP TABLE IF EXISTS barrier_commands")
    op.execute("DROP TABLE IF EXISTS barriers")

    op.execute("DROP TRIGGER IF EXISTS trg_watchlist_sync_list_type ON watchlist_entries")
    op.execute("DROP FUNCTION IF EXISTS watchlist_sync_list_type()")
    op.execute("DROP INDEX IF EXISTS idx_watchlist_entries_category")
    op.execute("ALTER TABLE watchlist_entries DROP CONSTRAINT IF EXISTS watchlist_entries_category_check")
    op.execute("""
        ALTER TABLE watchlist_entries
          DROP COLUMN IF EXISTS category,
          DROP COLUMN IF EXISTS owner_name,
          DROP COLUMN IF EXISTS company,
          DROP COLUMN IF EXISTS vehicle_type,
          DROP COLUMN IF EXISTS vehicle_color,
          DROP COLUMN IF EXISTS valid_from,
          DROP COLUMN IF EXISTS valid_to,
          DROP COLUMN IF EXISTS remarks
    """)
