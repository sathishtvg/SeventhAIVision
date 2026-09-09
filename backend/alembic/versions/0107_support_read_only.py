"""A support session should be able to look without being able to touch.

Support sessions (migration 0102) hand the platform operator a token with the
customer's own Admin permissions for the duration. That is the right access for
the rare ticket that needs something changed, and far more than is needed for
the common one, which is somebody asking why a screen is empty.

The spec asks for read-only BY DEFAULT with elevated access as an option, and
it is right: most support is looking. An operator who only ever needs to read
should not be one mis-click away from editing a customer's roster, and a
customer reading their audit log afterwards should be able to see the
difference between "they looked" and "they could have changed anything".

  read_only   GET, HEAD and OPTIONS. Everything else is refused.
  elevated    what a session used to be: the customer's Admin.

DEFAULT read_only, and the column is NOT NULL, so a session opened by any code
path that has not thought about this gets the safe one rather than the
convenient one.

ENFORCED ON EVERY REQUEST, in get_db_with_tenant, next to the liveness check
that is already there — not in the token. A token carrying its own access level
would be a claim the server trusts about itself; the session row is the
authority, so downgrading a live session takes effect on its next request
rather than whenever the token happens to expire.

EXISTING ROWS BECOME 'elevated'. They were opened under the old rule and did
have that access; rewriting history to say otherwise would make the audit trail
a lie about what was possible at the time.

Revision ID: 0107
Revises: 0106
"""
from alembic import op

revision = "0107"
down_revision = "0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE tenant_support_sessions
            ADD COLUMN IF NOT EXISTS access_level VARCHAR(20)
                NOT NULL DEFAULT 'read_only'
    """)
    # Sessions that already happened had elevated access, whatever the new
    # default says. The audit trail records what was possible, not what would
    # be possible if they were opened today.
    op.execute("""
        UPDATE tenant_support_sessions SET access_level = 'elevated'
    """)
    op.execute("""
        ALTER TABLE tenant_support_sessions
            ADD CONSTRAINT ck_support_access_level
                CHECK (access_level IN ('read_only', 'elevated'))
    """)
    op.execute("""
        COMMENT ON COLUMN tenant_support_sessions.access_level IS
            'read_only allows GET/HEAD/OPTIONS and nothing else. Enforced per '
            'request against this row, not against a claim in the token, so a '
            'downgrade takes effect immediately.'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE tenant_support_sessions "
               "DROP CONSTRAINT IF EXISTS ck_support_access_level")
    op.execute("ALTER TABLE tenant_support_sessions DROP COLUMN IF EXISTS access_level")
