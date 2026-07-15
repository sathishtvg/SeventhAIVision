"""0051 — User↔Site assignment for site-scoped access control (Gap 81)

Adds the user_sites join table so a user can be restricted to one or more
sites within their tenant. Enforcement semantics (implemented in
app/dependencies/sites.py):

  - Roles 1-2 (super_admin, admin) and API keys: always unrestricted.
  - Roles 3-6 (supervisor, operator, security_guard, viewer): restricted to
    assigned sites when any assignment rows exist; unrestricted when none
    (fail-open for backwards compatibility with existing users).
  - Role 7 (client): restricted to assigned sites; with NO assignments the
    user sees NOTHING (fail-closed — external users must be explicitly
    granted their site).

users.primary_site_id (added in 0012, never enforced) remains for display
purposes; user_sites is the authoritative assignment source.
"""
from __future__ import annotations

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_sites (
            user_id    UUID NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
            site_id    UUID NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,
            tenant_id  UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, site_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_sites_tenant ON user_sites(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_sites_site ON user_sites(site_id)")

    # Standard RLS triplet — same pattern as every tenant-scoped table.
    op.execute("ALTER TABLE user_sites ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_sites FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_user_sites ON user_sites
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_sites")
