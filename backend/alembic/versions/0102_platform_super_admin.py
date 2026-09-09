"""Super Admin runs the platform, not the customer's guardhouse.

WHAT WAS WRONG. Super Admin held 142 permissions and Admin held 140. The only
two it did not share were tenant:manage and license:manage, so the platform
operator was a tenant administrator with two extra switches rather than a role
with a job of its own. Of the 57 entries in the sidebar, exactly one was gated
on a platform-only permission; the other 56 were the customer's day-to-day
business — rosters, payroll, leave, key handovers, lost property, invoicing.

WHY THAT MATTERS. A Super Admin account provisioned inside a tenant could read
that tenant's employment records and pay rates while doing platform work that
never needs them. For a product sold into Singapore that is a PDPA
data-minimisation problem, and "the vendor's role can see everything" is the
first finding an enterprise security review raises. RLS did contain a Super
Admin to their own tenant, so this was never ambient access to every customer
— but tenant:manage can create a user in any tenant, so the path existed.

WHAT SUPER ADMIN KEEPS. The platform operator's actual job:

    tenant:manage    create, configure and suspend tenants
    license:manage   which AI modules each tenant has paid for
    audit:read       platform-side incident response
    support:manage   open a support session into a customer tenant (new)

Everything else is the customer's, and Admin (2) already has all of it.

HOW SUPPORT STILL WORKS, because removing the operational permissions removes
the way customers were supported. tenant_support_sessions records a deliberate,
time-boxed, justified entry into one tenant: who opened it, which tenant, why,
and when it expires. The API mints a token scoped to that tenant for the life
of the session and writes an audit entry into BOTH the platform tenant and the
customer's own audit log, so the customer can see that the vendor came in and
what reason was given. Access becomes an event with a record, not a standing
condition.

THE ROLE STAYS GLOBAL. roles.tenant_id is NULL for the built-ins, so this
applies to every Super Admin in every tenant, which is the point.

Revision ID: 0102
Revises: 0101
"""
from alembic import op

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None

SUPER_ADMIN_ROLE_ID = 1

# The platform operator's whole job. Deliberately short: anything a customer's
# own administrator should be doing is not on this list.
PLATFORM_PERMISSIONS = (
    "tenant:manage",
    "license:manage",
    "audit:read",
    "support:manage",
)


def upgrade() -> None:
    # ── The new permission ───────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES ('support:manage',
                'Open a time-boxed, audited support session into a customer tenant',
                'platform')
        ON CONFLICT (code) DO NOTHING
    """)

    # ── Narrow Super Admin to the platform ───────────────────────────────────
    #
    # Delete-then-grant rather than a diff: the set is small and stating it
    # outright means the role cannot drift back by accident when a later
    # migration adds a permission and grants it to "the admin roles".
    codes = ", ".join(f"'{c}'" for c in PLATFORM_PERMISSIONS)
    op.execute(f"""
        DELETE FROM role_permissions
         WHERE role_id = {SUPER_ADMIN_ROLE_ID}
           AND permission_id NOT IN (SELECT id FROM permissions WHERE code IN ({codes}))
    """)
    op.execute(f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT {SUPER_ADMIN_ROLE_ID}, id FROM permissions WHERE code IN ({codes})
        ON CONFLICT DO NOTHING
    """)

    # ── The support session ──────────────────────────────────────────────────
    #
    # No RLS. The table spans tenants by definition — a platform user in tenant
    # A opening a session into tenant B — so a tenant-scoped policy could not
    # express it. Access is gated by support:manage, which only Super Admin has.
    op.execute("""
        CREATE TABLE IF NOT EXISTS tenant_support_sessions (
            id                UUID PRIMARY KEY,
            platform_user_id  UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            platform_tenant_id UUID       NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            tenant_id         UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            reason            TEXT        NOT NULL,
            started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at        TIMESTAMPTZ NOT NULL,
            ended_at          TIMESTAMPTZ,
            CONSTRAINT reason_is_not_blank CHECK (btrim(reason) <> '')
        )
    """)
    op.execute("""
        COMMENT ON TABLE tenant_support_sessions IS
            'A deliberate, time-boxed entry by a platform operator into one '
            'customer tenant. The reason is required because an access record '
            'nobody has to justify is a log, not a control.'
    """)

    # One live session per platform user. Two at once means a token for tenant A
    # and a token for tenant B in the same hands, which is the thing being
    # avoided — and it makes "end my session" unambiguous.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_one_live_support_session
            ON tenant_support_sessions (platform_user_id)
         WHERE ended_at IS NULL
    """)
    # The liveness check runs on every request made with a support token.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_support_session_live
            ON tenant_support_sessions (id) WHERE ended_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenant_support_sessions")
    # Put Super Admin back to everything, which is what it had before.
    op.execute(f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT {SUPER_ADMIN_ROLE_ID}, id FROM permissions
        ON CONFLICT DO NOTHING
    """)
    op.execute("DELETE FROM permissions WHERE code = 'support:manage'")
