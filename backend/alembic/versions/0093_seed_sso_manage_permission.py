"""Seed sso:manage, which no migration ever created.

Every endpoint in routers/sso.py is gated on `sso:manage`, and nothing in the
migration history inserts that permission code. On the databases this was
developed against the row exists — put there by hand, or by a migration later
rewritten — so the SSO tests passed and the feature worked, and nobody had
cause to look.

On a database built purely from migrations it does not exist. require_permission
resolves the code against the permissions table, finds nothing, and returns 403
to everyone including Super Admin. So a customer deployed from a clean install
has SSO configuration that no account on the platform can reach, and the only
symptom is a 403 that looks like a role misconfiguration.

Found through CI. Its backend job had never passed; two earlier fixes (the
unmigrated test database in cf3d412, the unwritable JUnit path in 1067fad)
each uncovered the next problem, and this was underneath both. Reproduced by
running the suite against a database created and migrated from scratch: 31
failures, all in test_sso.py and test_sso_features.py, all 403.

Granted to the three roles that hold the comparable platform-configuration
permissions — Super Admin, Admin and Manager — matching what the existing
databases already had, so this changes nothing for anyone already running.
Supervisor is excluded for the same reason it is excluded from tenant SSO
elsewhere: federation is an identity-provider decision, not a site one.

Revision ID: 0093
Revises: 0092
"""
from alembic import op

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('sso:manage', 'Configure single sign-on and directory synchronisation', 'auth')
        ON CONFLICT (code) DO NOTHING
    """)
    # ON CONFLICT DO NOTHING throughout: the databases that already carry this
    # row and these grants must come through the migration unchanged.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1, 2, 8) AND p.code = 'sso:manage'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    # The permission is removed, not just the grants: leaving an ungranted code
    # behind would put the database back into exactly the broken state this
    # migration exists to fix, which is a worse resting place than not having
    # the feature.
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'sso:manage')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'sso:manage'")
