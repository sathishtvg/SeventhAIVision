"""The application's database role, and the one statement tenant isolation does not cover.

Tenant isolation here is row-level security, and `TRUNCATE` is not subject to
it: a role that holds that privilege on a table can empty it for every tenant
at once. Migration 0142 took the privilege away from the thirty-nine tables
whose migrations had granted ALL (0141 had done the same for the security
intelligence layer's own).

  A — No table or partition can be emptied by the application
  B — The statement itself is refused
  C — Everything the application did with those tables before, it still may

A asks the database's catalogue rather than naming tables, so a table created
later with GRANT ALL fails here the day it is added.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.db.session import AsyncSessionLocal
from tests.test_drone_api import _sql

#: The tables that carried the privilege when it was found, 2026-10-06.
ONCE_ALLOWED = (
    "drone_camera_coverage", "drone_edge_gateways", "drone_event_cameras", "drone_event_media", "drone_events",
    "drone_maintenance_logs", "drone_missions", "drone_module_licenses", "drone_observations",
    "drone_patrol_sessions", "drone_profile_rules", "drone_provider_configs", "drone_report_email_queue",
    "drone_report_recipients", "drone_reports", "drone_routes", "drone_schedules", "drone_security_profiles",
    "drone_security_zones", "drone_session_commands", "drone_session_waypoints", "drone_sync_receipts",
    "drone_telemetry", "drone_verification_requests", "drone_waypoints", "drones", "report_deliveries",
    "report_schedules", "tenant_pwm_floors", "virtual_patrol_email_queue", "virtual_patrol_email_recipients",
    "virtual_patrol_questions", "virtual_patrol_reports", "virtual_patrol_schedule_cameras",
    "virtual_patrol_schedules", "virtual_patrol_session_answers", "virtual_patrol_session_cameras",
    "virtual_patrol_session_questions", "virtual_patrol_sessions",
)


# ─── A. The catalogue ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_table_or_partition_can_be_emptied_by_the_application_role():
    rows = await _sql("""
        SELECT c.relname, has_table_privilege('svc_app', c.oid, 'TRUNCATE') AS can_empty
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')""")
    assert len(rows) > 300, "the catalogue was really asked"
    can = sorted(r["relname"] for r in rows if r["can_empty"])
    assert can == [], (f"the application's role can empty {len(can)} table(s) for every tenant at once: {can[:5]} — "
                       "grant read, add, change and remove, not ALL")


# ─── B. The statement ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emptying_a_table_is_refused_to_the_application_whatever_the_table():
    """Asked for, not looked up. Had one been let through, no tenant policy
    would have stopped it — and it would have been rolled back here."""
    async with AsyncSessionLocal() as db:
        who = (await db.execute(text(
            "SELECT (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user)"))).scalar()
        assert who is False, "this proves nothing as a role the privilege check does not apply to"
        for table in ("drones", "drone_telemetry", "virtual_patrol_sessions", "report_schedules", "tenant_pwm_floors",
                      "alerts", "incidents"):
            with pytest.raises(Exception, match="permission denied"):
                await db.execute(text(f"TRUNCATE {table} CASCADE"))
            await db.rollback()


# ─── C. What did not change ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_application_still_reads_adds_changes_and_removes_in_those_tables():
    rows = {r["relname"]: dict(r) for r in await _sql("""
        SELECT c.relname,
               has_table_privilege('svc_app', c.oid, 'SELECT') AS reads,
               has_table_privilege('svc_app', c.oid, 'INSERT') AS adds,
               has_table_privilege('svc_app', c.oid, 'UPDATE') AS changes,
               has_table_privilege('svc_app', c.oid, 'DELETE') AS removes,
               c.relrowsecurity AND c.relforcerowsecurity AS isolated
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relname = ANY(:names)""", {"names": list(ONCE_ALLOWED)})}
    assert set(rows) == set(ONCE_ALLOWED), sorted(set(ONCE_ALLOWED) - set(rows))
    for name, r in rows.items():
        assert r["reads"] and r["adds"] and r["changes"] and r["removes"], f"{name}: a feature lost what it works with"
        assert r["isolated"], f"{name}: and each of those statements is still under the tenant policy"
