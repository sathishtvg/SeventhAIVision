"""The AI module, phase 1: the licence the Super Admin grants, and the company admin's controls.

Design agreed with the owner on 26 October 2026 (docs/plans/2026-10-26-ai-module-design.md).

ITS OWN LICENCE TABLE, as Drone Patrol has. tenant_module_licenses treats an
organisation with no rows as licensed for every camera AI module, so a row there
for this module would quietly switch those off (DRONE_PATROL_GAP_ANALYSIS.md
§19.1). No row here, or is_enabled false, or past expires_at: the module is off.

FEATURE KEYS ARE CHECKED IN CODE, not by a CHECK constraint (services/ai_module.FEATURES),
so a feature added in a later phase needs no migration.

A HOSTED MODEL NEEDS A MONTHLY LIMIT. The owner pays for every reply of the hosted
model, so the database refuses a hosted licence without one. The free built-in model
may have none.

Permissions (the platform owner, 1, and the client role, 7, hold none):
  ai:configure        the company admin's controls             Admin 2, Manager 8
  ai:insights:read    see the warnings and suggestions          2, 8, Supervisor 3, Operator 4
  ai:insights:decide  accept or dismiss them                    2, 8, 3
  ai:assistant:use    talk to the assistant (if the admin lets their role)   2, 8, 3, 4, Viewer 6, Guard 5

Revision ID: 0170
Revises: 0169
"""
from alembic import op

revision = "0170"
down_revision = "0169"
branch_labels = None
depends_on = None

#: Descriptions carry no apostrophe: they are written into SQL as literals.
PERMISSIONS = (
    ("ai:configure", "Turn the features of the AI module on or off and choose who uses them", "ai"),
    ("ai:insights:read", "See the warnings and suggestions of the AI module", "ai"),
    ("ai:insights:decide", "Accept or dismiss the warnings and suggestions of the AI module", "ai"),
    ("ai:assistant:use", "Talk to the AI assistant", "ai"),
)
ROLES = (
    tuple((r, "ai:configure") for r in (2, 8))
    + tuple((r, "ai:insights:read") for r in (2, 8, 3, 4))
    + tuple((r, "ai:insights:decide") for r in (2, 8, 3))
    + tuple((r, "ai:assistant:use") for r in (2, 8, 3, 4, 6, 5))
)
TABLES = ("ai_module_licenses", "ai_feature_settings")


def _secure(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute(f"REVOKE ALL ON {table} FROM svc_app")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO svc_app")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ai_module_licenses (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
            is_enabled          BOOLEAN NOT NULL DEFAULT FALSE,
            licensed_at         TIMESTAMPTZ,
            expires_at          TIMESTAMPTZ,
            features            TEXT[] NOT NULL DEFAULT '{}',
            assistant_provider  VARCHAR(10) NOT NULL DEFAULT 'LOCAL',
            monthly_reply_limit INTEGER,
            licensed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            notes               TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_aiml_provider CHECK (assistant_provider IN ('LOCAL', 'HOSTED')),
            CONSTRAINT ck_aiml_limit CHECK (monthly_reply_limit IS NULL OR monthly_reply_limit >= 0),
            CONSTRAINT ck_aiml_hosted_has_limit
                CHECK (assistant_provider <> 'HOSTED' OR monthly_reply_limit IS NOT NULL)
        )
    """)
    op.execute("""
        CREATE TABLE ai_feature_settings (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            feature_key        VARCHAR(40) NOT NULL,
            enabled            BOOLEAN NOT NULL DEFAULT FALSE,
            role_ids           SMALLINT[] NOT NULL DEFAULT '{}',
            -- NULL is every site; a list is those sites only.
            site_ids           UUID[],
            sensitivity        VARCHAR(6) NOT NULL DEFAULT 'MEDIUM',
            learning_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_aifs_feature UNIQUE (tenant_id, feature_key),
            CONSTRAINT ck_aifs_sensitivity CHECK (sensitivity IN ('LOW', 'MEDIUM', 'HIGH'))
        )
    """)
    for table in TABLES:
        _secure(table)

    values = ", ".join(f"('{c}', '{d}', '{cat}')" for c, d, cat in PERMISSIONS)
    op.execute(f"INSERT INTO permissions (code, description, category) VALUES {values} "
               "ON CONFLICT (code) DO NOTHING")
    pairs = ", ".join(f"({r}, '{c}')" for r, c in ROLES)
    op.execute(f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT g.role_id, p.id
          FROM (VALUES {pairs}) AS g(role_id, code)
          JOIN permissions p ON p.code = g.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c, _, _ in PERMISSIONS)
    op.execute(f"DELETE FROM role_permissions WHERE permission_id IN "
               f"(SELECT id FROM permissions WHERE code IN ({codes}))")
    op.execute(f"DELETE FROM permissions WHERE code IN ({codes})")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
