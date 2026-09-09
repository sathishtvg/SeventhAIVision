"""A guard stands one post. Supervisors and standby cover roam.

The duty-team page let the same officer be added to every site. Its only
constraint was UNIQUE (site_id, guard_user_id, shift_type), which stops nothing
except the identical row twice — so in this database two Security Guards were
each on THREE sites, and one of them held both the day and the night team at
the same site. Day plus night is twenty-four hours; three sites at once is not
a rota, it is a hole in one.

WHO IS CONSTRAINED, AND WHY THE ROLE DECIDES IT

    Operator (4), Security Guard (5)   one posting, full stop
    Supervisor (3), Manager (8)        as many as they like
    Super Admin (1), Admin (2)         as many as they like
    anyone flagged is_standby          as many as they like

A guard stands a twelve-hour post at one place; being on a second site's team
is a promise nobody can keep, and the auto-scheduler drawing from that team
produces a roster that cannot be worked. A supervisor is not standing the post
— they cover several sites by design, which is what the job is. Standby and
relief officers are deliberately on more than one list; they can work anywhere,
just not two places at once, and that is the ROSTER's job to enforce at
scheduling time, not this table's.

"One posting" also settles the day-and-night question without a second rule:
a constrained guard cannot hold both shifts anywhere, because they cannot hold
two of anything.

WHY A TRIGGER AND NOT A UNIQUE INDEX. The rule depends on the guard's role and
standby flag, which live in `users`. A unique index cannot look at another
table. The trigger raises unique_violation so SQLAlchemy surfaces it as an
IntegrityError, the same shape the routers already handle.

EXISTING VIOLATIONS ARE RESOLVED, NOT IGNORED, because the trigger would
otherwise reject every future edit to a row that is already wrong. For each
over-assigned guard one posting is kept — the one matching their own shift
preference where they have expressed one, else the earliest — and the rest are
deleted. That is a real loss of data, so it is deliberate, deterministic, and
reported in the migration output rather than done quietly.

Revision ID: 0101
Revises: 0100
"""
from alembic import op

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None

# The roles that physically stand a post. Everyone else covers sites rather
# than staffing them.
POST_STANDING_ROLES = (4, 5)


def upgrade() -> None:
    # ── The standby flag ─────────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS is_standby BOOLEAN NOT NULL DEFAULT FALSE
    """)
    op.execute("""
        COMMENT ON COLUMN users.is_standby IS
            'Relief officer: may hold duty postings at several sites. The roster '
            'still schedules them at one site at a time.'
    """)

    # ── Resolve what is already there ────────────────────────────────────────
    #
    # Keep the posting that matches the guard's own preference where they have
    # one — a guard who prefers nights and holds a night team somewhere should
    # keep that — and otherwise the one they have held longest.
    op.execute("""
        WITH ranked AS (
            SELECT a.id,
                   row_number() OVER (
                       PARTITION BY a.guard_user_id
                       ORDER BY (p.preferred_shift_type IS NOT NULL
                                 AND p.preferred_shift_type = a.shift_type) DESC,
                                a.created_at,
                                a.id
                   ) AS keep_rank
              FROM site_duty_assignments a
              JOIN users u ON u.id = a.guard_user_id
         LEFT JOIN guard_shift_preferences p ON p.guard_user_id = a.guard_user_id
             WHERE u.role_id = ANY(:roles) AND NOT COALESCE(u.is_standby, FALSE)
        )
        DELETE FROM site_duty_assignments
         WHERE id IN (SELECT id FROM ranked WHERE keep_rank > 1)
    """.replace(":roles", "ARRAY[%s]" % ", ".join(str(r) for r in POST_STANDING_ROLES)))

    # ── The rule ─────────────────────────────────────────────────────────────
    op.execute(f"""
        CREATE OR REPLACE FUNCTION enforce_single_duty_posting() RETURNS trigger AS $$
        DECLARE
            guard_role    INTEGER;
            guard_standby BOOLEAN;
            other_posting INTEGER;
        BEGIN
            SELECT role_id, COALESCE(is_standby, FALSE)
              INTO guard_role, guard_standby
              FROM users
             WHERE id = NEW.guard_user_id;

            -- Supervisors, managers and admins cover several sites by design,
            -- and standby officers are deliberately on more than one list.
            IF guard_standby
               OR guard_role IS NULL
               OR guard_role <> ALL (ARRAY[{", ".join(str(r) for r in POST_STANDING_ROLES)}])
            THEN
                RETURN NEW;
            END IF;

            -- Compared on the NATURAL key, not the surrogate id. The add
            -- endpoint uses ON CONFLICT (site_id, guard_user_id, shift_type)
            -- DO UPDATE so that re-adding somebody already on the team just
            -- updates their note. Postgres attempts the INSERT first, with a
            -- freshly defaulted id, so an id-only comparison counts the very
            -- row about to be updated as a second posting and refuses a no-op.
            SELECT count(*) INTO other_posting
              FROM site_duty_assignments
             WHERE guard_user_id = NEW.guard_user_id
               AND id IS DISTINCT FROM NEW.id
               AND NOT (site_id = NEW.site_id AND shift_type = NEW.shift_type);

            IF other_posting > 0 THEN
                RAISE EXCEPTION
                    'guard % already holds a duty posting', NEW.guard_user_id
                    USING ERRCODE = 'unique_violation',
                          HINT = 'A guard stands one post. Remove the other posting, '
                                 'or mark them standby if they relieve across sites.';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_single_duty_posting
            BEFORE INSERT OR UPDATE ON site_duty_assignments
            FOR EACH ROW EXECUTE FUNCTION enforce_single_duty_posting()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_single_duty_posting ON site_duty_assignments")
    op.execute("DROP FUNCTION IF EXISTS enforce_single_duty_posting()")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_standby")
