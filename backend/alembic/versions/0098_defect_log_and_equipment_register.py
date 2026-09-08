"""The other two things a guardhouse writes down and nobody can report on.

DEFECTS. A guard finds a blown light in a stairwell, a leak in the car park, a
door closer that has stopped working. They write it in the occurrence book and
tell the building's FM desk, and then nothing tracks whether it was ever fixed.
Clients ask for exactly this list at contract review — "what did your officers
find, and what happened about it" — and today the answer is a stack of
photocopied pages.

The status ladder stops at the boundary of what a security company controls.
A guard does not fix a lift; they report it and chase it. So the row carries
who it was referred to and the building's own reference number, because that
is the thing you quote when you chase, and it lives on a sticky note today.

EQUIPMENT. Radios, torches, batons, body cameras — things with a serial number
that are issued to a named officer and expected back. Modelled exactly like the
key register, and for the same reason: the useful question is "who has the
radio that is missing", which an open-transaction shape answers and a status
column does not. A guard leaving with a radio and never returning it is a real
cost, and it is discovered at audit rather than at handover.

UNIFORMS ARE SEPARATE, and deliberately. A radio is one physical thing issued
many times. A uniform is two shirts and a pair of trousers in a size, issued
once and mostly not returned. Forcing them into one table would mean either
inventing an asset row per shirt or making quantity nullable on everything —
so quantity lives where quantity belongs, with a partial-return count for the
agencies that take a deposit and expect the kit back on resignation.

Revision ID: 0098
Revises: 0097
"""
from alembic import op

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


def _rls(table: str) -> str:
    return f"""
        ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """


def upgrade() -> None:
    # ── Facility defects ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE facility_defects (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id     UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            -- Which shift found it. Nullable: a supervisor walking the site
            -- files these too, and they are not on a shift.
            shift_id    UUID REFERENCES shifts(id) ON DELETE SET NULL,

            category    VARCHAR(30) NOT NULL DEFAULT 'other'
                CHECK (category IN ('lighting', 'plumbing', 'electrical', 'lift',
                                    'door_access', 'cctv', 'fire_safety', 'structural',
                                    'cleanliness', 'landscaping', 'other')),
            location    TEXT,
            description TEXT NOT NULL,
            severity    VARCHAR(20) NOT NULL DEFAULT 'medium'
                CHECK (severity IN ('low', 'medium', 'high', 'safety_hazard')),

            -- open      — found, nobody told yet
            -- reported  — passed to whoever owns the building
            -- in_progress — they have accepted it
            -- resolved  — the guard can see it is fixed
            -- closed    — no longer worth tracking (duplicate, not our scope)
            status      VARCHAR(20) NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'reported', 'in_progress', 'resolved', 'closed')),

            reported_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            reported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            photo_path  TEXT,

            -- Building management is the client's contractor, not a user of
            -- this system, so this is a written name rather than a foreign key.
            referred_to  VARCHAR(160),
            referred_at  TIMESTAMPTZ,
            -- Their ticket number. This is what you quote when you chase, and
            -- today it lives on a sticky note.
            reference_no VARCHAR(80),

            resolved_at        TIMESTAMPTZ,
            resolved_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            resolution_notes   TEXT,

            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_facility_defects_open
            ON facility_defects (tenant_id, site_id, severity, reported_at DESC)
         WHERE status IN ('open', 'reported', 'in_progress')
    """)
    op.execute("""
        CREATE INDEX idx_facility_defects_site
            ON facility_defects (tenant_id, site_id, status, reported_at DESC)
    """)
    op.execute(_rls("facility_defects"))

    # ── Equipment ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE equipment_items (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- Kit usually lives at a site, but a pool radio issued from head
            -- office belongs to nobody's site, so this is nullable.
            site_id     UUID REFERENCES sites(id) ON DELETE SET NULL,

            asset_code  VARCHAR(60) NOT NULL,
            name        VARCHAR(160) NOT NULL,
            category    VARCHAR(30) NOT NULL DEFAULT 'other'
                CHECK (category IN ('radio', 'torch', 'baton', 'handcuffs', 'bodycam',
                                    'ppe', 'phone', 'vehicle', 'metal_detector',
                                    'first_aid', 'other')),
            serial_number VARCHAR(120),
            condition   VARCHAR(20) NOT NULL DEFAULT 'good'
                CHECK (condition IN ('new', 'good', 'fair', 'poor', 'damaged', 'lost')),
            notes       TEXT,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- Asset codes are stencilled by the company, not the site, so
            -- unlike key codes these are unique across the whole tenant.
            CONSTRAINT uq_equipment_asset_code UNIQUE (tenant_id, asset_code)
        )
    """)
    op.execute("""
        CREATE INDEX idx_equipment_items_site
            ON equipment_items (tenant_id, site_id, category, is_active)
    """)
    op.execute(_rls("equipment_items"))

    op.execute("""
        CREATE TABLE equipment_assignments (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            item_id     UUID NOT NULL REFERENCES equipment_items(id) ON DELETE CASCADE,
            -- Unlike a key, kit is only ever issued to staff — a contractor
            -- does not take the company's body camera home.
            assigned_to_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            issued_by_user_id   UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            issued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            expected_return_at TIMESTAMPTZ,
            purpose     TEXT,

            returned_at TIMESTAMPTZ,
            received_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            condition_on_return VARCHAR(20)
                CHECK (condition_on_return IS NULL OR condition_on_return IN
                       ('new', 'good', 'fair', 'poor', 'damaged', 'lost')),
            return_notes TEXT,

            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Same shape as the key register, for the same reason: one thing cannot be
    # in two officers' hands, and that is a fact about the world rather than a
    # rule the API has to remember.
    op.execute("""
        CREATE UNIQUE INDEX uq_equipment_single_open_assignment
            ON equipment_assignments (item_id) WHERE returned_at IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_equipment_assignments_open
            ON equipment_assignments (tenant_id, expected_return_at)
         WHERE returned_at IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_equipment_assignments_user
            ON equipment_assignments (tenant_id, assigned_to_user_id, issued_at DESC)
    """)
    op.execute(_rls("equipment_assignments"))

    # ── Uniforms ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE uniform_issues (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

            item_type   VARCHAR(30) NOT NULL
                CHECK (item_type IN ('shirt', 'trousers', 'jacket', 'beret', 'cap',
                                     'belt', 'shoes', 'epaulette', 'name_tag', 'tie',
                                     'raincoat', 'other')),
            size        VARCHAR(20),
            quantity    INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),

            issued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            issued_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            deposit_amount NUMERIC(10,2),

            -- Partial, because a guard who resigns hands back three of the four
            -- shirts and the fourth is genuinely gone. A boolean would force
            -- somebody to pick a lie.
            returned_quantity INTEGER NOT NULL DEFAULT 0
                CHECK (returned_quantity >= 0),
            returned_at TIMESTAMPTZ,
            notes       TEXT,

            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

            CONSTRAINT ck_uniform_return_within_issue
                CHECK (returned_quantity <= quantity)
        )
    """)
    op.execute("""
        CREATE INDEX idx_uniform_issues_user
            ON uniform_issues (tenant_id, user_id, issued_at DESC)
    """)
    op.execute("""
        CREATE INDEX idx_uniform_issues_outstanding
            ON uniform_issues (tenant_id, user_id)
         WHERE returned_quantity < quantity
    """)
    op.execute(_rls("uniform_issues"))

    # ── Permissions ──────────────────────────────────────────────────────────
    #
    # Reporting a defect is the job of whoever walks the site, so guards log
    # them. Chasing building management and closing them out is supervision.
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('defect:read',      'View the facility defect log', 'guard-ops'),
          ('defect:log',       'Report a defect found on site', 'guard-ops'),
          ('defect:manage',    'Refer, resolve and close defects', 'guard-ops'),
          ('equipment:read',   'View the equipment and uniform register', 'guard-ops'),
          ('equipment:issue',  'Issue and receive equipment and uniforms', 'guard-ops'),
          ('equipment:manage', 'Maintain the equipment inventory', 'guard-ops')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 4, 5, 8)
          AND p.code IN ('defect:read', 'defect:log', 'equipment:read')
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 8)
          AND p.code IN ('defect:manage', 'equipment:issue', 'equipment:manage')
        ON CONFLICT DO NOTHING
    """)
    # Operators run the control room and hand out radios at shift start.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 4 AND p.code = 'equipment:issue'
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 6 AND p.code IN ('defect:read', 'equipment:read')
        ON CONFLICT DO NOTHING
    """)
    # A client sees what your officers found at their building. That report is
    # one of the few things a security contract is judged on, and it is
    # read-only by construction — role 7 gets no log or manage anywhere.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 7 AND p.code = 'defect:read'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    codes = ("defect:read", "defect:log", "defect:manage",
             "equipment:read", "equipment:issue", "equipment:manage")
    joined = ", ".join(f"'{c}'" for c in codes)
    op.execute(f"""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ({joined}))
    """)
    op.execute(f"DELETE FROM permissions WHERE code IN ({joined})")
    op.execute("DROP TABLE IF EXISTS uniform_issues")
    op.execute("DROP TABLE IF EXISTS equipment_assignments")
    op.execute("DROP TABLE IF EXISTS equipment_items")
    op.execute("DROP TABLE IF EXISTS facility_defects")
