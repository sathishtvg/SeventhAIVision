"""0073 — RLS on roles table (gap-scan finding)

roles started as a pure global catalogue (0001, correctly RLS-exempt at the
time) but 0057 (custom roles) added a tenant_id column to support
per-tenant custom roles without ever adding an RLS policy — a systematic
gap-scan of every tenant_id column in the schema caught it. Isolation for
custom roles depended entirely on every current and future code path
remembering to manually filter tenant_id IS NULL OR tenant_id = <tenant>;
this migration makes that a real, enforced database-level invariant
instead. Mirrors camera_model_library's identical split-visibility pattern
(0049) exactly: built-in rows (tenant_id IS NULL) visible to everyone,
custom rows visible/writable only by their own tenant.

Confirmed safe against every existing caller before writing this:
- roles.py itself only ever queries roles through get_db_with_tenant
  (tenant-scoped) sessions — this migration adds a backstop, not a
  behavior change, there.
- users.py's _assert_assignable_role — same, tenant-scoped session.
- emergency.py's roles JOIN for broadcast recipients — same, tenant-scoped.
- scim.py's GET /v2/Groups lists roles via get_raw_db (no RLS scoping),
  but _get_scim_context already re-scopes app.current_tenant to the SCIM
  token's own tenant before this runs — so this migration also closes a
  latent leak there (that endpoint previously returned every tenant's
  custom role names/codes to any SCIM client, not just its own).

Revision ID: 0073
Revises: 0072
"""
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE roles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE roles FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_roles ON roles
            USING (tenant_id IS NULL
                OR (current_setting('app.current_tenant', true) <> ''
                    AND tenant_id = current_setting('app.current_tenant', true)::uuid))
            WITH CHECK (current_setting('app.current_tenant', true) <> ''
                AND tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_roles ON roles")
    op.execute("ALTER TABLE roles DISABLE ROW LEVEL SECURITY")
