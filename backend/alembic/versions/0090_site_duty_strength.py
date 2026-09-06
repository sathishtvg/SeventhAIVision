"""Day duty and night duty are two different jobs — staff them separately.

A site had one number, `min_guards_per_shift`, covering every shift it runs.
Real sites are not staffed that way: a mall wants four officers on a Saturday
night and two on a Tuesday morning, and an office block reverses it. One
number cannot say that.

Worse, the number was never actually used to *schedule* anyone. The
auto-scheduler assigned exactly one guard per shift pattern and then compared
the day's total against min_guards_per_shift only to decide whether to attach
a `below_min_coverage` warning. A site needing three guards got one guard and
a warning, every day, and somebody rostered the other two by hand. The field
also had no UI anywhere, so the number it warned against was whatever the row
was seeded with.

So: two columns, one per shift type, read by the scheduler as the number of
officers to actually place on that shift at that site — and backfilled from
the patterns each site already runs, so nobody's coverage changes the day this
lands.

`min_guards_per_shift` is left in place rather than dropped. Nothing reads it
after this migration, but it is the only record of what a site was configured
for before the split, and dropping a column to save eight bytes is a poor
trade against being able to reconstruct that.

The second half is who those guards are. Sites and guards were connected only
through `user_sites`, which exists for permission scoping — which sites a user
may *see*. That is a different question from who is on the day team at Marina
Bay, and using one for the other means either over-granting visibility or
under-describing the deployment. `site_duty_assignments` answers the second
question on its own terms: this guard, this site, this shift type.

Revision ID: 0090
Revises: 0089
"""
from alembic import op

revision = "0090"
down_revision = "0089"
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
    # ── Per-shift-type strength ───────────────────────────────────────────
    op.execute("ALTER TABLE sites ADD COLUMN IF NOT EXISTS day_guards_required INTEGER")
    op.execute("ALTER TABLE sites ADD COLUMN IF NOT EXISTS night_guards_required INTEGER")

    # Backfilled from the patterns the site already runs, NOT from a flat 1.
    #
    # A shift_pattern names its own guard, so a site with three day patterns
    # already produces three day shifts. Seeding the requirement at 1 would
    # have cut that site to a single officer the first time the scheduler ran
    # against the new column — a silent reduction in coverage, which is the
    # worst possible outcome of a schema change in this product.
    #
    # Same day/night rule as services/roster_autoschedule.infer_shift_type:
    # a start hour of 5 through 16 is day, anything else is night.
    op.execute("""
        WITH counts AS (
            SELECT p.site_id,
                   COUNT(*) FILTER (
                       WHERE EXTRACT(HOUR FROM p.start_time) >= 5
                         AND EXTRACT(HOUR FROM p.start_time) < 17) AS day_n,
                   COUNT(*) FILTER (
                       WHERE EXTRACT(HOUR FROM p.start_time) < 5
                          OR EXTRACT(HOUR FROM p.start_time) >= 17) AS night_n
              FROM shift_patterns p
             WHERE p.is_active = TRUE
          GROUP BY p.site_id
        )
        UPDATE sites s
           SET day_guards_required   = COALESCE(s.min_guards_per_shift, c.day_n, 0),
               night_guards_required = COALESCE(s.min_guards_per_shift, c.night_n, 0)
          FROM counts c
         WHERE c.site_id = s.id
    """)

    # Sites with no patterns at all produce no roster today, so 0 is the
    # truthful figure — the Duty Teams page reads it as "not staffed" rather
    # than claiming a shortfall against a number nobody set.
    op.execute("""
        UPDATE sites
           SET day_guards_required   = COALESCE(day_guards_required,   min_guards_per_shift, 0),
               night_guards_required = COALESCE(night_guards_required, min_guards_per_shift, 0)
         WHERE day_guards_required IS NULL OR night_guards_required IS NULL
    """)

    op.execute("ALTER TABLE sites ALTER COLUMN day_guards_required SET DEFAULT 1")
    op.execute("ALTER TABLE sites ALTER COLUMN night_guards_required SET DEFAULT 1")
    op.execute("ALTER TABLE sites ALTER COLUMN day_guards_required SET NOT NULL")
    op.execute("ALTER TABLE sites ALTER COLUMN night_guards_required SET NOT NULL")

    # A site can legitimately run no night shift at all, so zero is allowed;
    # negative strength is not a configuration, it is a typo.
    op.execute("""
        ALTER TABLE sites ADD CONSTRAINT ck_sites_duty_strength_non_negative
            CHECK (day_guards_required >= 0 AND night_guards_required >= 0)
    """)

    # ── Who is on each team ───────────────────────────────────────────────
    op.execute("""
        CREATE TABLE site_duty_assignments (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id       UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            guard_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            -- Matches shift_patterns' inferred type and guard_shift_preferences'
            -- vocabulary, so the scheduler compares like with like.
            shift_type    VARCHAR(10) NOT NULL CHECK (shift_type IN ('day', 'night')),
            notes         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- A guard can hold both the day and the night team at one site
            -- (small sites do this), but not the same one twice.
            CONSTRAINT uq_site_duty_assignment UNIQUE (site_id, guard_user_id, shift_type)
        )
    """)
    op.execute("""
        CREATE INDEX idx_site_duty_site
            ON site_duty_assignments (tenant_id, site_id, shift_type)
    """)
    op.execute("""
        CREATE INDEX idx_site_duty_guard
            ON site_duty_assignments (tenant_id, guard_user_id)
    """)
    op.execute(_rls("site_duty_assignments"))

    # Reuses site:manage rather than minting a permission: deciding who stands
    # at a site is the same authority as configuring the site itself, and a
    # separate code would let the two drift apart in a role definition.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS site_duty_assignments")
    op.execute("ALTER TABLE sites DROP CONSTRAINT IF EXISTS ck_sites_duty_strength_non_negative")
    op.execute("ALTER TABLE sites DROP COLUMN IF EXISTS night_guards_required")
    op.execute("ALTER TABLE sites DROP COLUMN IF EXISTS day_guards_required")
