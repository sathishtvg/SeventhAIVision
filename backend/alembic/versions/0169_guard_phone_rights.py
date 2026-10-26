"""What a guard may do from the phone: incident notes and status, visitor pre-registration.

Owner's decision, 26 October 2026, after an audit of every call the phone makes against the guard role (role 5):
the phone showed guards four buttons the server refused. The owner chose to ALLOW three and hide the fourth:

* the end-of-shift handover (POST /shifts/{id}/handover): NOT by granting handover:create - the occurrence book reads
  that permission as "may read and draft every guard's handover" (routers/occurrence_book.py). The guard on a shift
  writes its handover instead (routers/shifts.py); anyone else's still needs handover:create. No change here.
* incident:update (notes on an incident, its status, and the bulk status change) - granted to guards.
* visitor:register - NEW, narrower than visitor:manage: pre-register a visitor and send them their QR code. Granted to
  guards and to every role that already holds visitor:manage, so nobody loses anything. Deleting a visitor and the
  visitor form's fields stay with visitor:manage (supervisor and up); the owner chose this over giving guards
  visitor:manage.
* broadcast:send (sending an emergency broadcast) is NOT granted; the phone hides the form from guards instead.

Revision ID: 0169
Revises: 0168
"""
from alembic import op

revision = "0169"
down_revision = "0168"
branch_labels = None
depends_on = None

GUARD = 5
GUARD_GRANTS = ("incident:update", "visitor:register")


def upgrade() -> None:
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES ('visitor:register', 'Pre-register a visitor and send them their QR code', 'visitor')
        ON CONFLICT (code) DO NOTHING
    """)
    # Everyone who could pre-register before (visitor:manage) still can.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT rp.role_id, reg.id
          FROM role_permissions rp
          JOIN permissions manage ON manage.id = rp.permission_id AND manage.code = 'visitor:manage'
          CROSS JOIN permissions reg
         WHERE reg.code = 'visitor:register'
        ON CONFLICT DO NOTHING
    """)
    codes = ", ".join(f"'{c}'" for c in GUARD_GRANTS)
    op.execute(f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT {GUARD}, p.id FROM permissions p WHERE p.code IN ({codes})
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute(f"""
        DELETE FROM role_permissions
         WHERE role_id = {GUARD}
           AND permission_id IN (SELECT id FROM permissions WHERE code = 'incident:update')
    """)
    op.execute("DELETE FROM role_permissions WHERE permission_id IN "
               "(SELECT id FROM permissions WHERE code = 'visitor:register')")
    op.execute("DELETE FROM permissions WHERE code = 'visitor:register'")
