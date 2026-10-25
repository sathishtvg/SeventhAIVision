"""Shift auto-close, overtime requests, and which overtime rule a shift follows.

Design: docs/plans/2026-10-25-roster-autoclose-overtime-design.md, agreed with
the owner on 25 October 2026. Nothing existing is changed or removed; every new
column has a default that leaves existing shifts exactly as they are.

* shifts.auto_closed_at - when the system closed a shift nobody checked out of
  (attendance.auto_close_hours after check-in, plus any overtime asked for).
* shifts.auto_close_reminded_for - the closing time the guard was last warned
  about, so a warning is sent once per closing time (an overtime request moves it).
* shifts.checked_out_away_from_site - a check-out outside the site's geofence:
  allowed, and flagged, as an off-site check-in is.
* shifts.overtime_needs_approval - set at check-in once this is released: the
  shift's overtime is paid only as approved. FALSE for every existing shift,
  which keeps the rule it was worked under (overtime counted automatically).
* overtime_requests - a guard's request for extra hours on a shift, and the
  supervisor's or admin's decision.

Revision ID: 0168
Revises: 0167
"""
from alembic import op

revision = "0168"
down_revision = "0167"
branch_labels = None
depends_on = None

STATUSES = ("pending", "approved", "rejected")


def upgrade() -> None:
    op.execute("ALTER TABLE shifts ADD COLUMN auto_closed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE shifts ADD COLUMN auto_close_reminded_for TIMESTAMPTZ")
    op.execute("ALTER TABLE shifts ADD COLUMN checked_out_away_from_site BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE shifts ADD COLUMN overtime_needs_approval BOOLEAN NOT NULL DEFAULT FALSE")
    # The sweep reads open shifts every five minutes.
    op.execute("CREATE INDEX IF NOT EXISTS idx_shifts_open_since ON shifts (tenant_id, actual_start) WHERE status = 'active'")

    statuses = ", ".join(f"'{s}'" for s in STATUSES)
    op.execute(f"""
        CREATE TABLE overtime_requests (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            shift_id            UUID NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
            guard_user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            hours_requested     NUMERIC(4,2) NOT NULL,
            reason              TEXT NOT NULL,
            status              VARCHAR(10) NOT NULL DEFAULT 'pending',
            decided_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            decided_at          TIMESTAMPTZ,
            decision_note       TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_overtime_requests_hours CHECK (hours_requested > 0 AND hours_requested <= 12),
            CONSTRAINT ck_overtime_requests_status CHECK (status IN ({statuses})),
            CONSTRAINT ck_overtime_requests_reason CHECK (length(btrim(reason)) > 0)
        )
    """)
    op.execute("CREATE INDEX idx_overtime_requests_shift ON overtime_requests (shift_id)")
    op.execute("CREATE INDEX idx_overtime_requests_queue ON overtime_requests (tenant_id, status, created_at DESC)")
    op.execute("ALTER TABLE overtime_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE overtime_requests FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_overtime_requests ON overtime_requests
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("REVOKE ALL ON overtime_requests FROM svc_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON overtime_requests TO svc_app")


def downgrade() -> None:
    op.execute("DROP TABLE overtime_requests")
    op.execute("DROP INDEX IF EXISTS idx_shifts_open_since")
    op.execute("ALTER TABLE shifts DROP COLUMN overtime_needs_approval")
    op.execute("ALTER TABLE shifts DROP COLUMN checked_out_away_from_site")
    op.execute("ALTER TABLE shifts DROP COLUMN auto_close_reminded_for")
    op.execute("ALTER TABLE shifts DROP COLUMN auto_closed_at")
