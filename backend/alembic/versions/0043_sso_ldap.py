"""SSO/SAML/LDAP — per-tenant identity provider configuration.

Adds:
  - tenant_sso_configs table (SAML + LDAP settings, RLS-protected)
  - sso_provider, sso_subject_id, sso_provisioned columns on users
  - hashed_password made nullable (SSO users have no local password)
  - Unique index for SSO subject lookup

Revision ID: 0043
Revises:     0042
"""

from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── SSO config table ──────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE tenant_sso_configs (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            protocol            VARCHAR(10) NOT NULL DEFAULT 'saml'
                                    CHECK (protocol IN ('saml', 'ldap', 'oidc')),
            is_enabled          BOOLEAN NOT NULL DEFAULT FALSE,

            -- SAML 2.0 fields
            idp_entity_id       TEXT,
            idp_sso_url         TEXT,
            idp_slo_url         TEXT,
            idp_certificate     TEXT,
            sp_entity_id        TEXT,
            sp_acs_url          TEXT,

            -- LDAP / Active Directory fields
            ldap_host           VARCHAR(255),
            ldap_port           INTEGER DEFAULT 636,
            ldap_use_ssl        BOOLEAN NOT NULL DEFAULT TRUE,
            ldap_bind_dn        TEXT,
            ldap_bind_password  TEXT,
            ldap_base_dn        TEXT,
            ldap_user_filter    VARCHAR(255) DEFAULT '(objectClass=person)',
            ldap_attr_email     VARCHAR(100) DEFAULT 'mail',
            ldap_attr_name      VARCHAR(100) DEFAULT 'cn',
            ldap_attr_group     VARCHAR(100) DEFAULT 'memberOf',

            -- JIT provisioning + role mapping
            auto_provision      BOOLEAN NOT NULL DEFAULT TRUE,
            default_role_id     SMALLINT REFERENCES roles(id),
            role_mapping        JSONB NOT NULL DEFAULT '{}',

            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

            UNIQUE (tenant_id, protocol)
        );
        CREATE INDEX idx_sso_configs_tenant ON tenant_sso_configs(tenant_id);

        ALTER TABLE tenant_sso_configs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tenant_sso_configs FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation_tenant_sso_configs ON tenant_sso_configs
            USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """)

    # ── SSO columns on users ─────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS sso_provider    VARCHAR(20),
            ADD COLUMN IF NOT EXISTS sso_subject_id  TEXT,
            ADD COLUMN IF NOT EXISTS sso_provisioned BOOLEAN NOT NULL DEFAULT FALSE;

        -- SSO-provisioned users have no local password — make nullable
        ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_sso_subject
            ON users(tenant_id, sso_provider, sso_subject_id)
            WHERE sso_provider IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS idx_users_sso_subject;
        ALTER TABLE users DROP COLUMN IF EXISTS sso_provisioned;
        ALTER TABLE users DROP COLUMN IF EXISTS sso_subject_id;
        ALTER TABLE users DROP COLUMN IF EXISTS sso_provider;
        ALTER TABLE users ALTER COLUMN hashed_password SET NOT NULL;
        DROP TABLE IF EXISTS tenant_sso_configs;
    """)
