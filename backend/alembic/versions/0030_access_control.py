"""Access Control / Door Management:
- access_doors: physical doors / card readers / biometric gates
- access_credentials: card, PIN, biometric credentials per person
- access_rules: who can access which door and when (time-based schedule)
- access_events: full access event log (granted / denied / forced / held_open)

Denied and forced events also create rows in the main alerts table and
publish alert_created to Redis so existing notification dispatch fires.

New permissions: access:read (all roles), access:write (1-3), access:ingest (1-4)

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rls(table: str) -> str:
    return f"""
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation_{table} ON {table}
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """


def upgrade() -> None:
    # ── access_doors ─────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE access_doors (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id     UUID REFERENCES sites(id) ON DELETE SET NULL,
            camera_id   UUID REFERENCES cameras(id) ON DELETE SET NULL,
            name        VARCHAR(255) NOT NULL,
            location    VARCHAR(255),
            door_type   VARCHAR(30) NOT NULL DEFAULT 'card_reader',
            -- card_reader | biometric | pin | combined | manual
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_access_doors_tenant ON access_doors(tenant_id);
        """
    )
    op.execute(_rls("access_doors"))

    # ── access_credentials ────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE access_credentials (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
            holder_name     VARCHAR(255),
            credential_type VARCHAR(20) NOT NULL DEFAULT 'card',
            -- card | pin | fingerprint | face | qr
            credential_ref  VARCHAR(255) NOT NULL,
            -- card UID, PIN hash, face_watchlist_entry ID, etc.
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            expires_at      TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_access_credentials_tenant ON access_credentials(tenant_id);
        CREATE INDEX idx_access_credentials_user ON access_credentials(user_id);
        """
    )
    op.execute(_rls("access_credentials"))

    # ── access_rules ──────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE access_rules (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            credential_id UUID NOT NULL REFERENCES access_credentials(id) ON DELETE CASCADE,
            door_id       UUID NOT NULL REFERENCES access_doors(id) ON DELETE CASCADE,
            schedule_days VARCHAR(7) NOT NULL DEFAULT '1234567',
            -- each char = day of week 1=Mon … 7=Sun; '1234567' = every day
            time_from     TIME,
            time_to       TIME,
            -- NULL time_from/to means any time of day
            is_active     BOOLEAN NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_access_rules_credential ON access_rules(credential_id);
        CREATE INDEX idx_access_rules_door ON access_rules(door_id);
        """
    )
    op.execute(_rls("access_rules"))

    # ── access_events ─────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE access_events (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            door_id       UUID NOT NULL REFERENCES access_doors(id) ON DELETE CASCADE,
            credential_id UUID REFERENCES access_credentials(id) ON DELETE SET NULL,
            event_type    VARCHAR(20) NOT NULL,
            -- granted | denied | forced | held_open | door_opened | door_closed | tamper
            denial_reason VARCHAR(50),
            -- not_authorized | expired | outside_schedule | invalid_credential | no_rule
            occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_access_events_tenant_time ON access_events(tenant_id, occurred_at DESC);
        CREATE INDEX idx_access_events_door ON access_events(door_id);
        """
    )
    op.execute(_rls("access_events"))

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute(
        """
        INSERT INTO permissions (code, description, category) VALUES
            ('access:read',   'View access doors, credentials and event log', 'access'),
            ('access:write',  'Create/edit doors, credentials and rules',     'access'),
            ('access:ingest', 'Post access events from physical readers',     'access')
        ON CONFLICT (code) DO NOTHING;

        -- All roles: access:read
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3,4,5,6) AND p.code = 'access:read'
        ON CONFLICT DO NOTHING;

        -- Roles 1-4 (operator+): access:ingest
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3,4) AND p.code = 'access:ingest'
        ON CONFLICT DO NOTHING;

        -- Roles 1-3 (admin+): access:write
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3) AND p.code = 'access:write'
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS access_events;
        DROP TABLE IF EXISTS access_rules;
        DROP TABLE IF EXISTS access_credentials;
        DROP TABLE IF EXISTS access_doors;
        """
    )
