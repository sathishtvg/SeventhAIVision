"""The permissions to read how long records are kept, and what is held about a person.

Additive: two permissions. No table is created and none is altered
(LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 13).

THE RETENTION STATEMENT IS A READING. It says, for each kind of record that
something removes, how long it is kept, where that period is set, what removes
it and whether a hold stops it; and, for what nothing removes, that nothing
does. It stores nothing and changes no period: a period is changed where it has
always been changed, in the organisation's settings and a site's recording
policy.

A SUBJECT REPORT IS A LOOK ACROSS EVERYBODY'S WORK FOR ONE PERSON, so it has a
permission of its own. It says where a person appears and how often, not what
each record says: each record is read on its own screen, under its own
permission, by somebody who can judge what of it is that person's. Nothing is
stored of a report but the line in the audit log that says who asked about
whom.

SUPER ADMIN (1), OPERATOR (4), GUARD (5) AND CLIENT (7) GET NEITHER. Admin and
Manager hold both - the people who already answer data-subject requests
(`pdpa:admin`). Supervisor and Viewer read the statement only: it names no
person.

Revision ID: 0156
Revises: 0155
"""
from alembic import op

revision = "0156"
down_revision = "0155"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("retention:read", "Read how long each kind of record is kept, and the holds in force", "governance"),
    ("subject:report", "Read where a person appears in the records, and how often", "governance"),
]
ROLES = [(2, "retention:read"), (8, "retention:read"), (3, "retention:read"), (6, "retention:read"),
         (2, "subject:report"), (8, "subject:report")]


def upgrade() -> None:
    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    values = ", ".join(f"({role}, '{code}')" for role, code in ROLES)
    op.execute(f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES {values}) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    codes = ", ".join(f"'{code}'" for code, _, _ in PERMISSIONS)
    op.execute(f"""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ({codes}))
    """)
    op.execute(f"DELETE FROM permissions WHERE code IN ({codes})")
