"""0057 — Custom roles (Gap 91)

Larger security firms want roles beyond the fixed 7 (e.g. "Control Room
Operator without evidence download"). This makes `roles` extensible per
tenant while keeping the existing authorization model unchanged:
require_permission() already resolves permissions purely by role_id, so a
custom role is just a role_id with a bespoke permission set — no new
enforcement path.

  - roles.tenant_id  NULL = built-in (global, ids 1-7); set = tenant-custom
  - roles.is_custom  marks tenant-created roles (built-ins are not editable)
  - roles_custom_id_seq  allocates custom ids from 100 up (built-ins reserve
    1-99), avoiding races on concurrent creates
  - role:manage granted to admin (role 2) as well as super_admin, so a
    tenant's own admin can manage that tenant's custom roles

roles has no RLS (it's a mostly-global catalogue); the roles router scopes
reads/writes to `tenant_id IS NULL OR tenant_id = <caller tenant>` explicitly.
"""
from __future__ import annotations

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE roles ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE roles ADD COLUMN IF NOT EXISTS is_custom BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("CREATE INDEX IF NOT EXISTS idx_roles_tenant ON roles(tenant_id) WHERE tenant_id IS NOT NULL")
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS roles_custom_id_seq AS smallint "
        "START 100 MINVALUE 100 MAXVALUE 32767"
    )
    # Tenant admins manage their own tenant's custom roles.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 2, id FROM permissions WHERE code = 'role:manage'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE role_id = 2 "
               "AND permission_id = (SELECT id FROM permissions WHERE code = 'role:manage')")
    op.execute("DROP SEQUENCE IF EXISTS roles_custom_id_seq")
    op.execute("DROP INDEX IF EXISTS idx_roles_tenant")
    op.execute("ALTER TABLE roles DROP COLUMN IF EXISTS is_custom")
    op.execute("ALTER TABLE roles DROP COLUMN IF EXISTS tenant_id")
