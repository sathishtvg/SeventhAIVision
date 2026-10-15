"""Fleet journey history: who may read a vehicle's history, and who may export it.

Phase 4 of the GPS fleet upgrade (docs/plans/2026-10-14-gps-fleet-tracking-design.md,
sections 4 and 6). Journeys and stops are worked out from the stored positions
when they are asked for (app/services/fleet_journeys.py): nothing new is
stored, so no table is created or altered.

    fleet:history:read    Admin, Supervisor, Operator, Viewer, Manager
    fleet:history:export  Admin, Manager - and every export is in the audit log

Revision ID: 0161
Revises: 0160
"""
from alembic import op

revision = "0161"
down_revision = "0160"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("fleet:history:read", "Read the journeys, stops and route of a fleet vehicle", "gps"),
    ("fleet:history:export", "Export the journeys, stops and positions of a fleet vehicle", "gps"),
]
ROLES = [(2, "fleet:history:read"), (3, "fleet:history:read"), (4, "fleet:history:read"), (6, "fleet:history:read"),
         (8, "fleet:history:read"), (2, "fleet:history:export"), (8, "fleet:history:export")]


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
    codes = ", ".join(f"'{c}'" for c, _, _ in PERMISSIONS)
    op.execute(f"DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ({codes}))")
    op.execute(f"DELETE FROM permissions WHERE code IN ({codes})")
