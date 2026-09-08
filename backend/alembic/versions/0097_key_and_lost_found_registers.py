"""The two registers every guardhouse keeps on paper.

Both are near-universal at Singapore guardhouses and neither existed here, so
both are kept in a hardcover book beside the terminal — which is exactly where
a client audit asks to look, and exactly what cannot be reported on.

KEYS. A key cabinet, and a log of who took which key, when, and whether it came
back. The question that matters is never "what keys exist" but "what is still
out", so the schema is built around an open transaction rather than a status
column somebody has to remember to change.

LOST AND FOUND. What was found, where it is stored, and who took it away. The
claimant's name and identification are personal data collected for a single
purpose, which is why the disposal fields exist alongside them: an item held
forever is a PDPA problem quietly accruing, and a register with no disposal
step is how that happens.

WHY TWO TABLES FOR KEYS AND ONE FOR ITEMS. A key is a durable thing issued
many times; its history is the point, so the key and the issue are separate
rows. A found item has exactly one life — found, held, then claimed or
disposed — and splitting that across two tables would invent a join for a
relationship that is always one to one.

Revision ID: 0097
Revises: 0096
"""
from alembic import op

revision = "0097"
down_revision = "0096"
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
    # ── Keys ─────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE site_keys (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id     UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            key_code    VARCHAR(40) NOT NULL,
            label       VARCHAR(160) NOT NULL,
            -- Where it hangs when it is in. A cabinet position is how a guard
            -- finds it at 3am, and how the next shift knows it is missing.
            cabinet_position VARCHAR(40),
            notes       TEXT,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- Codes are stencilled on the fob and reused across sites, so they
            -- are unique per site rather than per tenant.
            CONSTRAINT uq_site_key_code UNIQUE (site_id, key_code)
        )
    """)
    op.execute("CREATE INDEX idx_site_keys_site ON site_keys (tenant_id, site_id, is_active)")
    op.execute(_rls("site_keys"))

    op.execute("""
        CREATE TABLE key_transactions (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            key_id        UUID NOT NULL REFERENCES site_keys(id) ON DELETE CASCADE,

            -- Who took it. A staff id where the holder is staff, and a written
            -- name where they are not: contractors and tenants take keys far
            -- more often than employees do, and forcing a user row for a
            -- lift engineer would mean either refusing to log it or creating
            -- an account for somebody who will never sign in.
            issued_to_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            issued_to_name    VARCHAR(160),
            issued_to_company VARCHAR(160),
            issued_to_contact VARCHAR(60),

            issued_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            issued_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            expected_return_at TIMESTAMPTZ,
            purpose           TEXT,

            returned_at       TIMESTAMPTZ,
            received_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            return_notes      TEXT,

            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- Somebody has to be identifiable, or the row records that a key
            -- left the cabinet and nothing else.
            CONSTRAINT ck_key_issued_to_someone
                CHECK (issued_to_user_id IS NOT NULL OR issued_to_name IS NOT NULL)
        )
    """)
    # One key cannot be out twice. The partial unique index makes that a
    # database fact rather than a check the API has to remember, which matters
    # because two guards at a handover will both reach for the same fob.
    op.execute("""
        CREATE UNIQUE INDEX uq_key_single_open_issue
            ON key_transactions (key_id) WHERE returned_at IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_key_transactions_open
            ON key_transactions (tenant_id, expected_return_at)
         WHERE returned_at IS NULL
    """)
    op.execute("CREATE INDEX idx_key_transactions_key ON key_transactions (tenant_id, key_id, issued_at DESC)")
    op.execute(_rls("key_transactions"))

    # ── Lost and found ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE lost_found_items (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id       UUID REFERENCES sites(id) ON DELETE SET NULL,

            description   TEXT NOT NULL,
            category      VARCHAR(40) NOT NULL DEFAULT 'other'
                CHECK (category IN ('wallet', 'phone', 'keys', 'bag', 'clothing',
                                    'jewellery', 'documents', 'electronics', 'other')),
            found_location TEXT,
            found_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            found_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            storage_location VARCHAR(160),
            photo_path    TEXT,

            status        VARCHAR(20) NOT NULL DEFAULT 'held'
                CHECK (status IN ('held', 'claimed', 'disposed', 'handed_to_police')),

            -- Personal data, collected for one purpose: proving the right
            -- person took the item. Nullable because most items are never
            -- claimed, and null is the honest state until somebody does.
            claimed_by_name    VARCHAR(160),
            claimed_by_contact VARCHAR(60),
            claimed_id_type    VARCHAR(40),
            claimed_id_last4   VARCHAR(8),
            released_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            released_at        TIMESTAMPTZ,

            disposed_at    TIMESTAMPTZ,
            disposal_method VARCHAR(80),
            notes         TEXT,

            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Only the last four digits of an identity document are stored. Proving a
    # claimant is who they say they are needs a check against the document in
    # their hand, not a copy of it kept forever in a table nobody prunes.
    op.execute("""
        CREATE INDEX idx_lost_found_open
            ON lost_found_items (tenant_id, site_id, found_at DESC)
         WHERE status = 'held'
    """)
    op.execute("CREATE INDEX idx_lost_found_status ON lost_found_items (tenant_id, status)")
    op.execute(_rls("lost_found_items"))

    # ── Permissions ──────────────────────────────────────────────────────────
    #
    # A guard on the gate issues keys and books in found property — that is the
    # job, not an administrative extra — so read and write reach role 5. Only
    # a supervisor maintains the cabinet itself or disposes of property.
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('keyreg:read',    'View the key register and what is currently out', 'guard-ops'),
          ('keyreg:issue',   'Issue and receive keys', 'guard-ops'),
          ('keyreg:manage',  'Maintain the key cabinet', 'guard-ops'),
          ('lostfound:read', 'View the lost and found register', 'guard-ops'),
          ('lostfound:log',  'Record found property and release it to a claimant', 'guard-ops'),
          ('lostfound:manage', 'Dispose of unclaimed property', 'guard-ops')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 4, 5, 8)
          AND p.code IN ('keyreg:read', 'keyreg:issue', 'lostfound:read', 'lostfound:log')
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 8)
          AND p.code IN ('keyreg:manage', 'lostfound:manage')
        ON CONFLICT DO NOTHING
    """)
    # Viewers see both registers and change neither.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 6 AND p.code IN ('keyreg:read', 'lostfound:read')
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    codes = ("keyreg:read", "keyreg:issue", "keyreg:manage",
             "lostfound:read", "lostfound:log", "lostfound:manage")
    joined = ", ".join(f"'{c}'" for c in codes)
    op.execute(f"""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ({joined}))
    """)
    op.execute(f"DELETE FROM permissions WHERE code IN ({joined})")
    op.execute("DROP TABLE IF EXISTS lost_found_items")
    op.execute("DROP TABLE IF EXISTS key_transactions")
    op.execute("DROP TABLE IF EXISTS site_keys")
