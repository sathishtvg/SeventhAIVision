"""The permission to take an operations report out as a file.

Additive: one permission. No table is created and none is altered
(LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 10, the reports).

A REPORT IS RECORDS LEAVING THE PLATFORM AS A FILE. Reading a record on a screen
and taking a thousand of them away are not the same act, so the second has a
permission of its own. It is held ON TOP OF the permission each report's own
records are read under: this one alone opens nothing.

NOTHING IS STORED OF A REPORT. It is made when it is asked for, from rows the
platform already keeps, and what is kept of it is the line in the audit log
that says who took which report, for where and how much.

SUPER ADMIN (1), OPERATOR (4), GUARD (5), VIEWER (6) AND CLIENT (7) DO NOT GET
IT. Admin, Manager and Supervisor do: the people who send a report on.

Revision ID: 0153
Revises: 0152
"""
from alembic import op

revision = "0153"
down_revision = "0152"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("opsreport:export", "Take an operations report out as a file", "operations"),
]


def upgrade() -> None:
    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES (2, 'opsreport:export'), (8, 'opsreport:export'), (3, 'opsreport:export')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'opsreport:export')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'opsreport:export'")
