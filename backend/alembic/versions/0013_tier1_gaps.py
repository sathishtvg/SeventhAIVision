"""Tier 1 gap fixes: per-account lockout, session tracking, alert correlation/escalation,
patrol checkpoint scans, shift handovers, incident status history, visitor overstay.

Revision ID: 0013
Revises:     0012
Create Date: 2026-06-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Per-account login lockout columns on users ──────────────────────────
    op.add_column("users", sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until", sa.TIMESTAMP(timezone=True), nullable=True))

    # ── 2. Session tracking columns on refresh_tokens ─────────────────────────
    op.add_column("refresh_tokens", sa.Column("device_name", sa.String(255), nullable=True))
    op.add_column("refresh_tokens", sa.Column("last_ip", sa.String(45), nullable=True))
    op.add_column("refresh_tokens", sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=True))

    # ── 3. Alert correlation + escalation columns ──────────────────────────────
    op.add_column("alerts", sa.Column("correlation_id", sa.UUID(), nullable=True))
    op.add_column("alerts", sa.Column("escalated_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("alerts", sa.Column("original_severity", sa.String(10), nullable=True))
    op.create_index("idx_alerts_correlation_id", "alerts", ["correlation_id"])

    # ── 4. Enhance existing checkpoint_scans table (from migration 0008) ──────
    # Add verified flag + guard_user_id for QR/NFC scan verification
    op.add_column("checkpoint_scans", sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("checkpoint_scans", sa.Column(
        "guard_user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    ))

    # ── 5. Shift handovers table ───────────────────────────────────────────────
    op.create_table(
        "shift_handovers",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shift_id", sa.UUID(), nullable=False),
        sa.Column("outgoing_guard_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("incoming_guard_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("open_incidents_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("open_alerts_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("patrol_routes_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("patrol_routes_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checkpoints_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checkpoints_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outgoing_notes", sa.Text(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_shift_handovers_tenant", "shift_handovers", ["tenant_id"])
    op.create_index("idx_shift_handovers_shift_id", "shift_handovers", ["shift_id"])
    op.execute("""
        ALTER TABLE shift_handovers ENABLE ROW LEVEL SECURITY;
        ALTER TABLE shift_handovers FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation_shift_handovers ON shift_handovers
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """)

    # ── 6. Incident status history table ──────────────────────────────────────
    op.create_table(
        "incident_status_history",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("incident_id", sa.UUID(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("changed_by_user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("from_status", sa.String(30), nullable=True),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_incident_status_history_incident", "incident_status_history", ["incident_id"])
    op.execute("""
        ALTER TABLE incident_status_history ENABLE ROW LEVEL SECURITY;
        ALTER TABLE incident_status_history FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation_incident_status_history ON incident_status_history
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """)

    # ── 7. Extended incident statuses (dispatched/en_route/on_scene/contained) ─
    # incidents.status is VARCHAR(20) — existing values open/investigating/resolved/closed
    # all fit within 20 chars. New values: dispatched(10), en_route(8), on_scene(8), contained(9)
    # No column-level change needed; the CHECK is enforced at the application layer.

    # ── 8. New permissions ─────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
            ('session:manage',  'View and revoke own or others active sessions', 'auth'),
            ('handover:create', 'Generate shift handover report',                'guard-ops'),
            ('handover:read',   'View shift handover reports',                   'guard-ops'),
            ('scan:create',     'Record patrol checkpoint scan',                  'guard-ops')
        ON CONFLICT (code) DO NOTHING;
    """)

    # Grant to roles
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE (
            (r.code = 'super_admin'   AND p.code IN ('session:manage','handover:create','handover:read','scan:create'))
         OR (r.code = 'admin'         AND p.code IN ('session:manage','handover:create','handover:read','scan:create'))
         OR (r.code = 'supervisor'    AND p.code IN ('handover:create','handover:read','scan:create'))
         OR (r.code = 'operator'      AND p.code IN ('handover:read','scan:create'))
         OR (r.code = 'security_guard' AND p.code IN ('scan:create'))
        )
        ON CONFLICT DO NOTHING;
    """)

    # ── 9. Alert escalation tenant settings defaults ───────────────────────────
    # No schema change needed; escalation window is driven by tenant_settings keys:
    # alert.escalation_minutes_critical = 5
    # alert.escalation_minutes_high     = 15
    # alert.escalation_minutes_medium   = 60
    # Workers/scheduler read these via existing tenant_settings_cache pattern.


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_incident_status_history ON incident_status_history")
    op.drop_table("incident_status_history")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_shift_handovers ON shift_handovers")
    op.drop_table("shift_handovers")
    op.drop_column("checkpoint_scans", "guard_user_id")
    op.drop_column("checkpoint_scans", "verified")
    op.drop_column("alerts", "original_severity")
    op.drop_column("alerts", "escalated_at")
    op.drop_column("alerts", "correlation_id")
    op.drop_column("refresh_tokens", "last_seen_at")
    op.drop_column("refresh_tokens", "last_ip")
    op.drop_column("refresh_tokens", "device_name")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
