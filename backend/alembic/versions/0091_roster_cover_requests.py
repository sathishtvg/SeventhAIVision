"""Approving leave should leave a job behind, not just a warning.

Approving a leave request already did two useful things: it synced a row into
guard_leave_blocks, so roster_autoschedule stops picking that guard for FUTURE
rosters, and it returned the already-published shifts that clash. The live
attendance board already reads those blocks too, so the guard correctly shows
as "Approved Leave" rather than as a no-show.

What nothing did was cover the post. The clashing shifts were handed back to
the UI, which printed "See Roster to reassign these shifts to another guard"
and forgot them. If the supervisor did not act on that sentence immediately,
the site was short a guard on a day nobody was going to be reminded about —
the shift still existed, still named the guard who was on leave, and read as
handled everywhere except reality.

So the approval now writes down the work: one open cover request per clashing
shift, which stays open until a supervisor either assigns somebody or
explicitly says no cover is needed.

WHY NOT VACATE THE SHIFT. The obvious move is to null out shifts.guard_user_id
and call the post open. Two reasons not to:

  * The column is NOT NULL and 247 places read it, several through INNER JOINs
    that would silently drop a vacated shift out of command-centre counts
    rather than show it as needing cover.
  * A pattern-generated shift would come back. generate_roster_shifts inserts
    from shift_patterns with ON CONFLICT (pattern_id, scheduled_start) DO
    NOTHING, so a deleted or re-created row would be regenerated on the next
    run, quietly re-assigning the guard who is on leave.

Assigning cover therefore UPDATES the existing shift to the replacement guard,
which is both the smallest change and the one the rest of the system already
understands.

Revision ID: 0091
Revises: 0090
"""
from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE roster_cover_requests (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- The post that needs somebody. CASCADE because a cover request
            -- for a shift that no longer exists is not a job anyone can do.
            shift_id            UUID NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
            -- Who was rostered and is now on leave. Kept even after the
            -- request is filled: "who did this originally belong to" is the
            -- first question asked when a cover goes wrong.
            absent_user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            -- Where it came from. Nullable because a supervisor can also block
            -- out leave directly on the roster, with no request behind it.
            leave_request_id    UUID REFERENCES leave_requests(id) ON DELETE SET NULL,
            leave_block_id      UUID REFERENCES guard_leave_blocks(id) ON DELETE SET NULL,
            status              VARCHAR(20) NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'filled', 'dismissed')),
            -- Who actually stood the post. Null while open, and null on a
            -- dismissed request, which is how "covered" and "decided we did
            -- not need to" stay tellable apart in a report.
            filled_with_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            resolved_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            resolved_at         TIMESTAMPTZ,
            note                TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # One OPEN request per shift. Overlapping leave, or a request approved
    # after a manual block already covered the same day, would otherwise put
    # the same post in the queue twice and invite two supervisors to fill it
    # with two different people. Resolved rows are exempt so the same shift can
    # legitimately need cover again later.
    op.execute("""
        CREATE UNIQUE INDEX uq_cover_request_open_per_shift
            ON roster_cover_requests (shift_id)
         WHERE status = 'open'
    """)
    op.execute("""
        CREATE INDEX idx_cover_requests_open
            ON roster_cover_requests (tenant_id, status, created_at)
    """)
    op.execute("""
        CREATE INDEX idx_cover_requests_absent
            ON roster_cover_requests (tenant_id, absent_user_id)
    """)

    op.execute("ALTER TABLE roster_cover_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE roster_cover_requests FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_roster_cover_requests ON roster_cover_requests
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # Filling a post is rostering someone, so it reuses shift:manage rather
    # than minting a permission that would have to be granted separately to
    # every role that can already edit a shift.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS roster_cover_requests")
