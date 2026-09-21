"""Take certification:enforce back off Super Admin.

0118 as first written granted it to roles 1, 2 and 8. Role 1 is Super Admin --
the VENDOR's operator, not a tenant user -- and 0102 deliberately stripped that
role to nine platform permissions (tenant, license, audit, support, billing,
platform, 2fa) precisely so a platform operator cannot reach into a customer's
feature surface. test_support_sessions asserts the set exactly, and CI caught
the regression.

0118 HAS BEEN CORRECTED, SO WHY THIS EXISTS. Alembic will not re-run a revision
it has already applied. Any database that migrated against the first version of
0118 -- a developer's, a staging environment's -- carries the grant and would
keep it forever, because the file it would consult no longer describes what it
did. A fresh database never receives the grant and this revision does nothing,
which is the correct shape for a corrective migration: idempotent, and a no-op
wherever the problem never existed.

Deliberately narrow. It removes exactly one grant from exactly one role, rather
than "resetting" role 1 to the expected set -- a migration that rewrites a
permission set wholesale would silently undo any legitimate later change.

Revision ID: 0120
Revises: 0119
"""
from alembic import op

revision = "0120"
down_revision = "0119"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions rp
         USING permissions p
         WHERE p.id = rp.permission_id
           AND rp.role_id = 1
           AND p.code = 'certification:enforce';
    """)


def downgrade() -> None:
    # Nothing. Re-granting a tenant permission to the platform operator is the
    # bug this exists to remove, and a downgrade that recreates it would hand
    # the privilege back to whoever rolled the schema back for an unrelated
    # reason.
    pass
