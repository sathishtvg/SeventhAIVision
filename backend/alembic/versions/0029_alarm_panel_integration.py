"""Alarm Panel Integration:
- alarm_panels: registered alarm control panels per site
- alarm_zones: individual detection zones on each panel
- alarm_events: event log (zone triggered, armed, disarmed, tamper, power fail, etc.)

Auto-creates alerts on zone_alarm / tamper events.

New permissions: alarm:read (all roles), alarm:manage (1-3), alarm:arm (1-4)

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-25
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
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
    # ── alarm_panels ──────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE alarm_panels (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id         UUID REFERENCES sites(id) ON DELETE SET NULL,
            name            VARCHAR(255) NOT NULL,
            model           VARCHAR(100),
            serial_number   VARCHAR(100),
            protocol        VARCHAR(30) NOT NULL DEFAULT 'webhook',
            -- webhook | contact_id_tcp | mqtt | sdk
            host            VARCHAR(255),
            port            INTEGER,
            api_key         VARCHAR(255),
            -- panels POST events to /api/v1/alarms/events/ingest with this key
            status          VARCHAR(20) NOT NULL DEFAULT 'unknown',
            -- unknown | online | offline | fault
            arm_state       VARCHAR(20) NOT NULL DEFAULT 'disarmed',
            -- disarmed | armed_away | armed_stay | armed_night | alarm
            last_contact_at TIMESTAMPTZ,
            notes           TEXT,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_alarm_panels_tenant ON alarm_panels(tenant_id, site_id);
        CREATE INDEX idx_alarm_panels_api_key ON alarm_panels(api_key) WHERE api_key IS NOT NULL;
        """
    )
    op.execute(_rls("alarm_panels"))

    # ── alarm_zones ───────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE alarm_zones (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            panel_id            UUID NOT NULL REFERENCES alarm_panels(id) ON DELETE CASCADE,
            zone_number         SMALLINT NOT NULL,
            name                VARCHAR(255) NOT NULL,
            zone_type           VARCHAR(30) NOT NULL DEFAULT 'motion',
            -- motion | door | window | glass_break | smoke | heat | panic | tamper | 24hr | vibration
            current_state       VARCHAR(20) NOT NULL DEFAULT 'normal',
            -- normal | alarm | tamper | fault | bypass | open | restored
            linked_camera_id    UUID REFERENCES cameras(id) ON DELETE SET NULL,
            is_active           BOOLEAN NOT NULL DEFAULT TRUE,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (panel_id, zone_number)
        );
        CREATE INDEX idx_alarm_zones_panel ON alarm_zones(panel_id, zone_number);
        CREATE INDEX idx_alarm_zones_tenant ON alarm_zones(tenant_id);
        """
    )
    op.execute(_rls("alarm_zones"))

    # ── alarm_events ──────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE alarm_events (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            panel_id        UUID NOT NULL REFERENCES alarm_panels(id) ON DELETE CASCADE,
            zone_id         UUID REFERENCES alarm_zones(id) ON DELETE SET NULL,
            zone_number     SMALLINT,
            event_type      VARCHAR(30) NOT NULL,
            -- zone_alarm | zone_restore | zone_tamper | zone_bypass | zone_unbypass
            -- panel_armed_away | panel_armed_stay | panel_armed_night | panel_disarmed
            -- panel_ac_fail | panel_battery_low | panel_tamper | panel_online | panel_offline
            -- test_signal | unknown
            severity        VARCHAR(10) NOT NULL DEFAULT 'info',
            -- info | low | medium | high | critical
            description     TEXT,
            raw_payload     JSONB,
            alert_id        UUID,
            occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_alarm_events_tenant_time ON alarm_events(tenant_id, occurred_at DESC);
        CREATE INDEX idx_alarm_events_panel       ON alarm_events(panel_id, occurred_at DESC);
        CREATE INDEX idx_alarm_events_zone        ON alarm_events(zone_id, occurred_at DESC);
        CREATE INDEX idx_alarm_events_type        ON alarm_events(tenant_id, event_type, occurred_at DESC);
        """
    )
    op.execute(_rls("alarm_events"))

    # ── permissions ───────────────────────────────────────────────────────────
    op.execute(
        """
        INSERT INTO permissions (code, description, category) VALUES
            ('alarm:read',   'View alarm panels and event log',          'alarm'),
            ('alarm:manage', 'Create/edit alarm panels and zones',       'alarm'),
            ('alarm:arm',    'Arm/disarm alarm panels remotely',         'alarm')
        ON CONFLICT (code) DO NOTHING;

        -- All roles: alarm:read
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3,4,5,6) AND p.code = 'alarm:read'
        ON CONFLICT DO NOTHING;

        -- Roles 1-4: alarm:arm
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3,4) AND p.code = 'alarm:arm'
        ON CONFLICT DO NOTHING;

        -- Roles 1-3: alarm:manage
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3) AND p.code = 'alarm:manage'
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS alarm_events;
        DROP TABLE IF EXISTS alarm_zones;
        DROP TABLE IF EXISTS alarm_panels;
        """
    )
