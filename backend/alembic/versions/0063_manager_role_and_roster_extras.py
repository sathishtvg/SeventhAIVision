"""0063 — Manager role (id=8, seeded between Admin and Supervisor) +
roster/attendance follow-ups (ShiftSecure Phase 2C).

Manager's permission set is a clone of Admin's (role 2) existing grants —
practically "between Admin and Supervisor" since Admin already excludes the
two super-admin-only permissions (tenant:manage, license:manage).

No new tables — pure seed data. The roster auto-scheduler / attendance /
leave-block behavior changes that accompany this round are pure Python
(roster_autoschedule.py, attendance.py, roster.py) and need no schema
changes.
"""
from __future__ import annotations

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO roles (id, code, name, description) VALUES
          (8, 'manager', 'Manager', 'Operational manager — between Admin and Supervisor in seniority')
        ON CONFLICT (id) DO NOTHING
    """)

    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 8, permission_id FROM role_permissions WHERE role_id = 2
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE role_id = 8")
    op.execute("DELETE FROM roles WHERE id = 8")
