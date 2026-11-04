"""Cases that escalate: an organisation's timings, and a record of each escalation sent.

Phase 3 of closing the feature reference's known limits (docs/plans/2026-11-04-case-notify-and-escalate-design.md).
Nothing here changes a case. Being told when put on a case needs no table: it is told once, when it happens.

`case_escalation_settings` - one row per organisation, made on the scheduler's first pass with the defaults, and
changed by whoever may change the organisation's settings. `since` is when its escalation began: nothing is due from
before it, so the cases an organisation already has do not all escalate on the first day.

`case_escalations` - each escalation, once: the case, the task when it is one, the trigger, the step, the moment it
counts from, when it was due and sent, and how many people it was addressed to. Unique per case, task, trigger, step
and moment, so a pass that runs again sends nothing again, and a quiet case that is added to and goes quiet again
counts afresh. It goes with its case.
"""
from alembic import op

revision = "0177"
down_revision = "0176"
branch_labels = None
depends_on = None

KINDS = ("TASK_OVERDUE", "QUIET", "CLOSE_WAITING")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _secured(table: str, grants: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute(f"REVOKE ALL ON {table} FROM svc_app")
    op.execute(f"GRANT {grants} ON {table} TO svc_app")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE case_escalation_settings (
            tenant_id          UUID        PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            quiet_high_days    SMALLINT    NOT NULL DEFAULT 3  CHECK (quiet_high_days BETWEEN 1 AND 90),
            quiet_normal_days  SMALLINT    NOT NULL DEFAULT 7  CHECK (quiet_normal_days BETWEEN 1 AND 90),
            quiet_low_days     SMALLINT    NOT NULL DEFAULT 14 CHECK (quiet_low_days BETWEEN 1 AND 90),
            overdue_hours      SMALLINT    NOT NULL DEFAULT 24 CHECK (overdue_hours BETWEEN 1 AND 168),
            close_wait_days    SMALLINT    NOT NULL DEFAULT 2  CHECK (close_wait_days BETWEEN 1 AND 30),
            since              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by_user_id UUID        REFERENCES users(id) ON DELETE SET NULL,
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    _secured("case_escalation_settings", "SELECT, INSERT, UPDATE")

    op.execute(f"""
        CREATE TABLE case_escalations (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            case_id     UUID        NOT NULL REFERENCES case_files(id) ON DELETE CASCADE,
            task_id     UUID        REFERENCES case_tasks(id) ON DELETE CASCADE,
            kind        VARCHAR(16) NOT NULL CHECK (kind IN ({_in(KINDS)})),
            step        SMALLINT    NOT NULL CHECK (step IN (1, 2)),
            -- The moment it counts from: the task's due time, the case's last change, the close request.
            counted_from TIMESTAMPTZ NOT NULL,
            due_at      TIMESTAMPTZ NOT NULL,
            sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            addressed   INTEGER     NOT NULL DEFAULT 0 CHECK (addressed >= 0),
            message     TEXT        NOT NULL,
            CONSTRAINT ck_caseesc_task CHECK ((kind = 'TASK_OVERDUE') = (task_id IS NOT NULL))
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_caseesc_once ON case_escalations "
               "(case_id, kind, COALESCE(task_id, '00000000-0000-0000-0000-000000000000'::uuid), step, counted_from)")
    op.execute("CREATE INDEX idx_caseesc_case ON case_escalations (case_id, sent_at DESC)")
    _secured("case_escalations", "SELECT, INSERT")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS case_escalations")
    op.execute("DROP TABLE IF EXISTS case_escalation_settings")
