"""Make a handover something two people sign, not one person types.

shift_handovers already exists and already gathers useful numbers — open
incidents, open alerts, patrol completion — but it is a one-sided note. The
outgoing guard writes it and leaves. Nothing records that the incoming guard
read it, agreed with it, or disagreed with it, which means the one moment where
responsibility actually transfers leaves no trace of the transfer.

TWO SIGNATURES. A handover is submitted, then accepted or disputed. Disputed is
the state that matters: the incoming guard counted eleven keys against a
handover claiming twelve, and somebody has to look at that before the next
shift buries it. Acceptance is not a formality — it is the moment the incoming
guard becomes accountable for what the outgoing guard is walking away from.

THE CHECKLIST IS PER SITE, AND SNAPSHOTTED. What gets checked genuinely varies:
a condominium counts the visitor book and the barrier remote, a data centre
counts mantrap logs and access cards. A fixed list would be wrong somewhere, so
a template belongs to a site — or to the tenant, as the fallback for sites that
have not set one up. The items are then COPIED onto the handover, because
editing the template next month must not silently rewrite what somebody signed
last month.

COUNTS, NOT JUST TICKS. "Keys checked" as a tick is worth very little; "keys
checked: 11" against a register saying 12 is worth a great deal. An item can
ask for a number, and the mismatch is the whole point.

ACCEPTANCE IS A GUARD'S PERMISSION. handover:create already exists and reaches
only supervisors and up. Accepting is the incoming guard's own act, so it gets
its own permission rather than either widening handover:create or leaving the
feature unusable by the people it is for.

Revision ID: 0099
Revises: 0098
"""
from alembic import op

revision = "0099"
down_revision = "0098"
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
    # ── Acceptance, and the register counts worth carrying ───────────────────
    #
    # Every existing row was written under the old one-sided model. They default
    # to 'accepted' rather than 'submitted': marking historic handovers as
    # awaiting acceptance would put months of rows into a queue nobody can
    # action, since the guards involved have long gone home.
    op.execute("""
        ALTER TABLE shift_handovers
            ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'accepted',
            ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS accepted_by_user_id UUID
                REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS incoming_notes TEXT,
            ADD COLUMN IF NOT EXISTS dispute_reason TEXT,
            ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS resolved_by_user_id UUID
                REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS keys_outstanding INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS keys_overdue INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS lost_found_held INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS open_defects_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS equipment_out_count INTEGER NOT NULL DEFAULT 0
    """)
    # New handovers start as submitted; only the backfill above is 'accepted'.
    op.execute("ALTER TABLE shift_handovers ALTER COLUMN status SET DEFAULT 'submitted'")
    op.execute("""
        ALTER TABLE shift_handovers
            ADD CONSTRAINT ck_handover_status
            CHECK (status IN ('submitted', 'accepted', 'disputed', 'resolved'))
    """)
    op.execute("""
        CREATE INDEX idx_shift_handovers_pending
            ON shift_handovers (tenant_id, created_at DESC)
         WHERE status IN ('submitted', 'disputed')
    """)

    # ── Checklist templates ──────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE handover_checklist_templates (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- NULL means the tenant-wide fallback, used by any site without
            -- its own. One company-standard list plus per-site exceptions is
            -- how these are actually maintained.
            site_id     UUID REFERENCES sites(id) ON DELETE CASCADE,
            name        VARCHAR(160) NOT NULL,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # At most one active template per site, and one tenant-wide fallback.
    # Two would mean the handover picks whichever it read first.
    op.execute("""
        CREATE UNIQUE INDEX uq_handover_template_per_site
            ON handover_checklist_templates (tenant_id, site_id)
         WHERE is_active AND site_id IS NOT NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_handover_template_tenant_default
            ON handover_checklist_templates (tenant_id)
         WHERE is_active AND site_id IS NULL
    """)
    op.execute(_rls("handover_checklist_templates"))

    op.execute("""
        CREATE TABLE handover_checklist_items (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            template_id UUID NOT NULL REFERENCES handover_checklist_templates(id)
                ON DELETE CASCADE,
            label       VARCHAR(200) NOT NULL,
            -- "Keys checked" as a tick is worth little; "keys checked: 11"
            -- against a register saying 12 is worth a great deal.
            requires_count BOOLEAN NOT NULL DEFAULT FALSE,
            -- What the count should be measured against, when the system
            -- already knows: 'keys_out', 'lost_found_held', 'equipment_out'.
            -- NULL means the count is informational.
            expected_source VARCHAR(30)
                CHECK (expected_source IS NULL OR expected_source IN
                       ('keys_out', 'lost_found_held', 'equipment_out')),
            is_required BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_handover_items_template
            ON handover_checklist_items (tenant_id, template_id, sort_order)
    """)
    op.execute(_rls("handover_checklist_items"))

    # ── The checks as performed ──────────────────────────────────────────────
    op.execute("""
        CREATE TABLE handover_checks (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            handover_id UUID NOT NULL REFERENCES shift_handovers(id) ON DELETE CASCADE,
            -- Which template item this came from, kept for reporting. Nullable
            -- and ON DELETE SET NULL because deleting a template item must not
            -- delete the evidence that it was once checked.
            item_id     UUID REFERENCES handover_checklist_items(id) ON DELETE SET NULL,

            -- The label as it read at the time. A template edited next month
            -- must not rewrite what somebody signed last month.
            label       VARCHAR(200) NOT NULL,
            requires_count BOOLEAN NOT NULL DEFAULT FALSE,
            is_required BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order  INTEGER NOT NULL DEFAULT 0,

            checked     BOOLEAN NOT NULL DEFAULT FALSE,
            counted_value  INTEGER,
            -- What the system believed at the moment the handover was raised.
            expected_value INTEGER,
            notes       TEXT,

            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_handover_checks_handover
            ON handover_checks (tenant_id, handover_id, sort_order)
    """)
    # The rows worth a supervisor's attention: a count that did not match.
    op.execute("""
        CREATE INDEX idx_handover_checks_mismatched
            ON handover_checks (tenant_id, handover_id)
         WHERE counted_value IS NOT NULL AND expected_value IS NOT NULL
               AND counted_value <> expected_value
    """)
    op.execute(_rls("handover_checks"))

    # ── Permissions ──────────────────────────────────────────────────────────
    #
    # handover:create and handover:read already exist. Accepting is the incoming
    # guard's own act, so it rides on handover:create rather than needing a
    # grant nobody would remember to make. Maintaining templates and closing
    # out disputes are supervision.
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('handover:accept', 'Tick a handover checklist, and accept or dispute it', 'guard-ops'),
          ('handover:manage', 'Maintain handover checklists and resolve disputes', 'guard-ops')
        ON CONFLICT (code) DO NOTHING
    """)
    # Accepting is the INCOMING guard's act, and handover:create — which
    # already exists — reaches only roles 1, 2, 3 and 8. Gating acceptance on
    # it would make the feature unusable at the guardhouse it exists for, and
    # widening handover:create instead would quietly change who can raise a
    # handover, which is a different question with its own existing answer.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 4, 5, 8) AND p.code = 'handover:accept'
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 3, 8) AND p.code = 'handover:manage'
        ON CONFLICT DO NOTHING
    """)
    # And a guard has to be able to READ what they are being asked to accept.
    # handover:read reaches roles 1-4 and 8 today, which left the one person
    # the acceptance screen is built for unable to open it.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id = 5 AND p.code = 'handover:read'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions
                                  WHERE code IN ('handover:accept', 'handover:manage'))
    """)
    op.execute("DELETE FROM permissions WHERE code IN ('handover:accept', 'handover:manage')")
    # The handover:read grant this migration added for guards.
    op.execute("""
        DELETE FROM role_permissions
         WHERE role_id = 5
           AND permission_id = (SELECT id FROM permissions WHERE code = 'handover:read')
    """)
    op.execute("DROP TABLE IF EXISTS handover_checks")
    op.execute("DROP TABLE IF EXISTS handover_checklist_items")
    op.execute("DROP TABLE IF EXISTS handover_checklist_templates")
    op.execute("ALTER TABLE shift_handovers DROP CONSTRAINT IF EXISTS ck_handover_status")
    op.execute("""
        ALTER TABLE shift_handovers
            DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS accepted_at,
            DROP COLUMN IF EXISTS accepted_by_user_id,
            DROP COLUMN IF EXISTS incoming_notes,
            DROP COLUMN IF EXISTS dispute_reason,
            DROP COLUMN IF EXISTS resolved_at,
            DROP COLUMN IF EXISTS resolved_by_user_id,
            DROP COLUMN IF EXISTS keys_outstanding,
            DROP COLUMN IF EXISTS keys_overdue,
            DROP COLUMN IF EXISTS lost_found_held,
            DROP COLUMN IF EXISTS open_defects_count,
            DROP COLUMN IF EXISTS equipment_out_count
    """)
