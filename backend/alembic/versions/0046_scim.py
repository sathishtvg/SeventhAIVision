"""Gap 9 — SCIM 2.0 User Provisioning.

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-02
"""
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── scim_external_id on users ─────────────────────────────────────────────
    op.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS scim_external_id VARCHAR(255)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_scim_ext_id
        ON users(tenant_id, scim_external_id)
        WHERE scim_external_id IS NOT NULL
    """)

    # ── scim_tokens (global — no RLS; auth via token hash, not GUC) ──────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS scim_tokens (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name                VARCHAR(255) NOT NULL,
            token_hash          VARCHAR(64) NOT NULL UNIQUE,
            created_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            expires_at          TIMESTAMPTZ,
            last_used_at        TIMESTAMPTZ,
            is_active           BOOLEAN NOT NULL DEFAULT TRUE,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_scim_tokens_tenant ON scim_tokens(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_scim_tokens_hash ON scim_tokens(token_hash)")

    # ── scim_sync_log (global — idempotency + audit trail) ───────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS scim_sync_log (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            scim_token_id   UUID REFERENCES scim_tokens(id) ON DELETE SET NULL,
            operation       VARCHAR(20) NOT NULL,
            resource_type   VARCHAR(20) NOT NULL,
            resource_id     VARCHAR(255),
            external_id     VARCHAR(255),
            status          VARCHAR(20) NOT NULL DEFAULT 'success',
            detail          JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_scim_sync_log_tenant ON scim_sync_log(tenant_id, created_at DESC)")

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
            ('scim:manage', 'Manage SCIM provisioning tokens', 'scim')
        ON CONFLICT (code) DO NOTHING
    """)
    # scim:manage → super_admin (1), admin (2)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM (VALUES (1), (2)) AS r(id)
        CROSS JOIN (SELECT id FROM permissions WHERE code = 'scim:manage') p
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'scim:manage')")
    op.execute("DELETE FROM permissions WHERE code = 'scim:manage'")
    op.execute("DROP TABLE IF EXISTS scim_sync_log")
    op.execute("DROP TABLE IF EXISTS scim_tokens")
    op.execute("DROP INDEX IF EXISTS idx_users_scim_ext_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS scim_external_id")
