"""The platform is a tenant of its own, and billing belongs to the vendor.

THREE THINGS, all following from the same fact: Seventh AI sells this to
security companies, so the vendor and the customer are different businesses
and should not share a login, a permission set, or a bill.

1. A PLATFORM TENANT. The super admin sat inside 'demo', which is a customer.
   That made the vendor look like one of its own subscribers and meant the
   platform owner's account lived in a tenant that could be deleted. The
   'seventh-ai-demo' tenant becomes 'seventhaivision' — the vendor's own — and
   the super admin moves into it. 'demo' goes back to being nothing but a
   customer.

   Both statements are keyed on values that exist only in the seeded
   development database, so this section is a no-op anywhere else. It is
   written to be safe to re-run.

2. BILLING WAS POINTED THE WRONG WAY. billing:read and billing:manage were
   granted to Admin (2), Supervisor (3) and Manager (8) — the CUSTOMER's roles
   — and not to Super Admin. So a security company could read and change its
   own subscription to Seventh AI, and Seventh AI could see none of it. The
   Stripe tables (billing_plans, billing_subscriptions, billing_customers) are
   all keyed by tenant_id, which is to say they describe what each customer
   owes the vendor. That is the vendor's business. The permissions move.

3. ERRORS HAVE NOWHERE TO GO. Nothing persists an application failure. A 500 in
   a customer's tenant is a line in a container log that nobody reads and that
   disappears on the next deploy, so the first the vendor hears of a broken
   feature is a phone call. platform_errors gives them somewhere to land, with
   the tenant on each row so "which customer is this hurting" is answerable.

   No RLS: it spans tenants by definition and is read only through
   platform:read, which Super Admin alone holds. The tenant column is nullable
   because a failure before authentication has no tenant to record.

Revision ID: 0103
Revises: 0102
"""
from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None

SUPER_ADMIN = 1
#: The customer-side roles that were holding the vendor's billing permissions.
TENANT_ADMIN_ROLES = (2, 3, 8)

PLATFORM_SLUG = "seventhaivision"
PLATFORM_NAME = "Seventh AI Vision"
SEEDED_PLATFORM_SLUG = "seventh-ai-demo"
PLATFORM_OWNER_EMAIL = "superadmin@seventhaivision.com"


def upgrade() -> None:
    # ── 1. The platform's own tenant ─────────────────────────────────────────
    op.execute(f"""
        UPDATE tenants
           SET slug = '{PLATFORM_SLUG}', name = '{PLATFORM_NAME}'
         WHERE slug = '{SEEDED_PLATFORM_SLUG}'
           AND NOT EXISTS (SELECT 1 FROM tenants WHERE slug = '{PLATFORM_SLUG}')
    """)
    # Moving a user between tenants keeps their id, so all 121 foreign keys
    # pointing at users stay valid. What changes is which tenant's RLS scope
    # they fall under, which is the whole point.
    #
    # employee_code is cleared, not carried. It is a TENANT's staffing number
    # -- (tenant_id, employee_code) is unique -- so bringing 'demo' EMP0006
    # into a tenant that already numbers its own people collides, and would be
    # wrong even if it did not: the platform owner is not the demo company's
    # sixth employee.
    op.execute(f"""
        UPDATE users
           SET tenant_id = (SELECT id FROM tenants WHERE slug = '{PLATFORM_SLUG}'),
               employee_code = NULL
         WHERE email = '{PLATFORM_OWNER_EMAIL}'
           AND EXISTS (SELECT 1 FROM tenants WHERE slug = '{PLATFORM_SLUG}')
           AND tenant_id <> (SELECT id FROM tenants WHERE slug = '{PLATFORM_SLUG}')
           -- (tenant_id, email) is unique, so refuse rather than collide.
           AND NOT EXISTS (
               SELECT 1 FROM users u2
                WHERE u2.email = '{PLATFORM_OWNER_EMAIL}'
                  AND u2.tenant_id = (SELECT id FROM tenants WHERE slug = '{PLATFORM_SLUG}')
           )
    """)

    # ── 2. Billing moves to the vendor ───────────────────────────────────────
    tenant_roles = ", ".join(str(r) for r in TENANT_ADMIN_ROLES)
    op.execute(f"""
        DELETE FROM role_permissions
         WHERE role_id IN ({tenant_roles})
           AND permission_id IN (
               SELECT id FROM permissions WHERE code IN ('billing:read', 'billing:manage')
           )
    """)
    op.execute(f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT {SUPER_ADMIN}, id FROM permissions
         WHERE code IN ('billing:read', 'billing:manage')
        ON CONFLICT DO NOTHING
    """)

    # ── 3. The platform console ──────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES ('platform:read',
                'Read cross-tenant platform figures: usage, adoption and errors',
                'platform')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute(f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT {SUPER_ADMIN}, id FROM permissions WHERE code = 'platform:read'
        ON CONFLICT DO NOTHING
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS platform_errors (
            id           UUID PRIMARY KEY,
            occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            tenant_id    UUID REFERENCES tenants(id) ON DELETE SET NULL,
            user_id      UUID,
            method       VARCHAR(10),
            path         TEXT,
            status_code  INTEGER,
            error_type   VARCHAR(200),
            message      TEXT,
            stack        TEXT
        )
    """)
    op.execute("""
        COMMENT ON TABLE platform_errors IS
            'Unhandled application failures, kept so the vendor hears about a '
            'broken feature before the customer telephones about it. No RLS: '
            'it spans tenants and is read through platform:read only.'
    """)
    # The console reads newest-first, and almost always filtered by tenant.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_errors_recent
            ON platform_errors (occurred_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_errors_tenant
            ON platform_errors (tenant_id, occurred_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform_errors")
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'platform:read')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'platform:read'")

    # Billing goes back to the tenant-side roles it was on.
    tenant_roles = ", ".join(str(r) for r in TENANT_ADMIN_ROLES)
    op.execute(f"""
        DELETE FROM role_permissions
         WHERE role_id = {SUPER_ADMIN}
           AND permission_id IN (
               SELECT id FROM permissions WHERE code IN ('billing:read', 'billing:manage')
           )
    """)
    op.execute(f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
         WHERE r.id IN ({tenant_roles})
           AND p.code IN ('billing:read', 'billing:manage')
        ON CONFLICT DO NOTHING
    """)

    # The tenant rename is deliberately NOT undone: the super admin would be
    # moved back into a customer's tenant, which is the state this migration
    # exists to correct, and slugs may have been handed out since.
