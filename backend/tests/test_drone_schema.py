"""Drone patrol, phase 2: the schema holds the rules it claims to hold.

Migration 0123 makes promises in constraints rather than in code — one flight per
drone, one session per scheduled run, no duplicate telemetry, zones that are
actually shapes, deletion that never blocks, and tenant isolation. Each promise
here is tested by trying to break it.

  A — The migration's shape (RLS on every drone table, who holds what)
  B — Constraints that stop bad data
  C — Deletion never blocks, and history survives
  D — Tenant isolation, as svc_app

Isolation is tested as svc_app, the role the application runs as. postgres has
BYPASSRLS, so an isolation test that connected as postgres would pass whether or
not a single policy existed; the svc_app connection asserts it cannot bypass RLS
before drawing any conclusion.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Imported at module level on purpose: the first test to import app.main pays for
# the ML stack, and inside a test that is a 120-second timeout.
from app.main import app  # noqa: F401

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


def _svc_app_test_url() -> str:
    """The app's own credentials against the test database — derived from
    DATABASE_URL, never by rewriting the admin URL, which silently yields a
    BYPASSRLS connection when the credentials differ."""
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        return ADMIN_DATABASE_URL
    base, _, db = raw.rpartition("/")
    db = db.split("?")[0]
    if not db.endswith("_test"):
        db = f"{db}_test"
    return f"{base}/{db}"


APP_DATABASE_URL = _svc_app_test_url()


async def _sql(stmt: str, params: dict | None = None):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            r = await s.execute(text(stmt), params or {})
            rows = r.mappings().all() if r.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


async def _rejects(stmt: str, params: dict, constraint: str) -> None:
    """The statement fails, and fails on the constraint named — not on some
    unrelated NOT NULL that would make the test pass for the wrong reason."""
    with pytest.raises(Exception) as exc:
        await _sql(stmt, params)
    assert constraint in str(exc.value), (
        f"expected {constraint!r} to reject this, got: {str(exc.value)[:400]}")


async def _world() -> dict:
    """A tenant with a site, a camera, a drone, a route with two waypoints, a
    zone, a profile, a mission and a daily schedule."""
    i = {k: uuid.uuid4() for k in (
        "tenant", "site", "camera", "drone", "route", "wp1", "wp2", "zone",
        "profile", "mission", "schedule", "user")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'Drone Co',:s)",
               {"t": i["tenant"], "s": f"drone-{i['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name, latitude, longitude) "
               "VALUES (:i,:t,'Factory A',1.3000,103.8000)",
               {"i": i["site"], "t": i["tenant"]})
    await _sql("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
               "VALUES (:i,:t,2,:e,'x','An Admin')",
               {"i": i["user"], "t": i["tenant"], "e": f"a-{i['user'].hex[:8]}@drone.test"})
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name, latitude, longitude) "
               "VALUES (:i,:t,:s,'CAM-27',1.3002,103.8001)",
               {"i": i["camera"], "t": i["tenant"], "s": i["site"]})
    await _sql("INSERT INTO drones (id, tenant_id, site_id, name, code, status, battery_level) "
               "VALUES (:i,:t,:s,'Drone One','D-01','READY',90)",
               {"i": i["drone"], "t": i["tenant"], "s": i["site"]})
    await _sql("INSERT INTO drone_routes (id, tenant_id, site_id, name, base_latitude, base_longitude) "
               "VALUES (:i,:t,:s,'Night Perimeter',1.3000,103.8000)",
               {"i": i["route"], "t": i["tenant"], "s": i["site"]})
    for key, seq, lat in (("wp1", 1, 1.3010), ("wp2", 2, 1.3020)):
        await _sql("INSERT INTO drone_waypoints (id, tenant_id, route_id, sequence, name, latitude, longitude) "
                   "VALUES (:i,:t,:r,:q,:n,:la,103.8000)",
                   {"i": i[key], "t": i["tenant"], "r": i["route"], "q": seq,
                    "n": f"WP0{seq}", "la": lat})
    await _sql(
        "INSERT INTO drone_security_zones (id, tenant_id, site_id, name, zone_type, polygon) "
        "VALUES (:i,:t,:s,'Rear Fence','RESTRICTED',CAST(:p AS jsonb))",
        {"i": i["zone"], "t": i["tenant"], "s": i["site"],
         "p": '[{"lat":1.301,"lng":103.799},{"lat":1.301,"lng":103.801},{"lat":1.302,"lng":103.800}]'})
    await _sql("INSERT INTO drone_security_profiles (id, tenant_id, name) "
               "VALUES (:i,:t,'Night High Security')", {"i": i["profile"], "t": i["tenant"]})
    await _sql("INSERT INTO drone_missions (id, tenant_id, site_id, drone_id, route_id, "
               "                            security_profile_id, name) "
               "VALUES (:i,:t,:s,:d,:r,:p,'Night Perimeter Security')",
               {"i": i["mission"], "t": i["tenant"], "s": i["site"], "d": i["drone"],
                "r": i["route"], "p": i["profile"]})
    await _sql("INSERT INTO drone_schedules (id, tenant_id, mission_id, schedule_type, "
               "                             start_date, launch_time) "
               "VALUES (:i,:t,:m,'DAILY',:d,'23:00')",
               {"i": i["schedule"], "t": i["tenant"], "m": i["mission"], "d": date.today()})
    return i


async def _session(w: dict, *, status: str, scheduled_for: datetime | None = None,
                   number: str | None = None) -> uuid.UUID:
    sid = uuid.uuid4()
    await _sql(
        "INSERT INTO drone_patrol_sessions (id, tenant_id, session_number, mission_id, "
        "    schedule_id, site_id, drone_id, mission_name, drone_name, scheduled_for, status) "
        "VALUES (:i,:t,:n,:m,:sc,:s,:d,'Night Perimeter Security','Drone One',:f,:st)",
        {"i": sid, "t": w["tenant"], "n": number or f"DP-{sid.hex[:10]}",
         "m": w["mission"], "sc": w["schedule"] if scheduled_for else None,
         "s": w["site"], "d": w["drone"], "f": scheduled_for, "st": status})
    return sid


# ─── A. The migration's shape ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_every_drone_table_is_under_forced_rls_with_one_policy():
    """Checked by discovery, not by a list: a drone table added later without
    RLS fails here even though nobody remembered to add it to a test."""
    rows = await _sql(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies "
        "  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        " WHERE n.nspname = 'public' AND c.relname LIKE 'drone%' "
        "   AND c.relkind IN ('r','p') AND NOT c.relispartition")
    assert len(rows) >= 18, [r["relname"] for r in rows]
    unsafe = [r["relname"] for r in rows
              if not (r["relrowsecurity"] and r["relforcerowsecurity"] and r["policies"] == 1)]
    assert unsafe == [], f"drone tables without forced RLS and exactly one policy: {unsafe}"


@pytest.mark.asyncio
async def test_telemetry_is_partitioned_and_maintained():
    part = await _sql("SELECT partition_interval::text AS iv FROM public.part_config "
                      " WHERE parent_table = 'public.drone_telemetry'")
    assert part, "drone_telemetry is not registered with pg_partman"
    # relkind is Postgres's one-byte "char"; asyncpg hands it back as bytes.
    kind = await _sql("SELECT relkind::text AS relkind FROM pg_class WHERE relname = 'drone_telemetry'")
    assert kind[0]["relkind"] == "p", "drone_telemetry is not a partitioned table"


@pytest.mark.asyncio
async def test_super_admin_and_client_hold_no_drone_permission():
    """The platform owner licenses the module; it does not operate tenants'
    drones. A cross-tenant operational grant is the boundary CI caught being
    crossed once before."""
    rows = await _sql(
        "SELECT rp.role_id, p.code FROM role_permissions rp "
        "  JOIN permissions p ON p.id = rp.permission_id "
        " WHERE p.code LIKE 'drone:%' AND rp.role_id IN (1, 7)")
    assert rows == [], [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_the_person_at_the_screen_can_always_abort():
    """Operator can start a flight, so Operator must be able to stop one."""
    rows = await _sql(
        "SELECT rp.role_id FROM role_permissions rp "
        "  JOIN permissions p ON p.id = rp.permission_id "
        " WHERE p.code = 'drone:mission:abort'")
    holders = {r["role_id"] for r in rows}
    execute = await _sql(
        "SELECT rp.role_id FROM role_permissions rp "
        "  JOIN permissions p ON p.id = rp.permission_id "
        " WHERE p.code = 'drone:mission:execute'")
    can_start = {r["role_id"] for r in execute}
    assert can_start <= holders, f"roles that can start but not abort: {can_start - holders}"


@pytest.mark.asyncio
async def test_the_module_is_listed_but_nobody_is_licensed_by_the_migration():
    """Listing drone_patrol for sale must not switch it on for anyone, and must
    not touch tenant_module_licenses, whose no-rows fallback grants every AI
    module."""
    listed = await _sql("SELECT billing_type, unit_price FROM billing_modules "
                        " WHERE code = 'drone_patrol'")
    assert listed and listed[0]["billing_type"] == "per_site"
    leaked = await _sql("SELECT count(*) AS n FROM tenant_module_licenses "
                        " WHERE module_type LIKE 'drone%'")
    assert leaked[0]["n"] == 0


# ─── B. Constraints that stop bad data ───────────────────────────────────────

@pytest.mark.asyncio
async def test_a_drone_cannot_fly_two_missions_at_once():
    w = await _world()
    await _session(w, status="ACTIVE")
    await _rejects(
        "INSERT INTO drone_patrol_sessions (tenant_id, session_number, drone_id, status) "
        "VALUES (:t,:n,:d,'LAUNCHING')",
        {"t": w["tenant"], "n": "DP-SECOND", "d": w["drone"]},
        "uq_dps_one_flight_per_drone")


@pytest.mark.asyncio
async def test_finished_flights_do_not_block_the_next_one():
    w = await _world()
    for status in ("COMPLETED", "FAILED", "ABORTED", "BLOCKED"):
        await _session(w, status=status)
    await _session(w, status="ACTIVE")  # must not raise


@pytest.mark.asyncio
async def test_one_scheduled_run_makes_one_session():
    """A scheduler restart or a second worker retries the same run. The
    constraint, not an if statement, is what stops a second flight."""
    w = await _world()
    run = datetime.combine(date.today(), time(23, 0), tzinfo=timezone.utc)
    await _session(w, status="SCHEDULED", scheduled_for=run)
    await _rejects(
        "INSERT INTO drone_patrol_sessions (tenant_id, session_number, schedule_id, "
        "                                   scheduled_for, status) "
        "VALUES (:t,'DP-RETRY',:sc,:f,'SCHEDULED')",
        {"t": w["tenant"], "sc": w["schedule"], "f": run},
        "uq_dps_execution")


@pytest.mark.asyncio
async def test_manual_runs_are_not_mistaken_for_duplicates():
    """schedule_id is NULL for a manual flight, and NULL <> NULL, so any number
    of manual sessions may exist — each on its own drone."""
    w = await _world()
    await _session(w, status="COMPLETED")
    await _session(w, status="COMPLETED")


@pytest.mark.asyncio
async def test_a_resent_telemetry_sample_is_rejected_and_lands_in_its_month():
    w = await _world()
    # This month, not a fixed date: pg_partman makes partitions relative to when
    # the migration ran, so a pinned month would fall into the default partition
    # on a database created later and fail for the wrong reason.
    at = datetime.now(timezone.utc).replace(day=15, hour=23, minute=17, second=43, microsecond=0)
    await _sql("INSERT INTO drone_telemetry (tenant_id, drone_id, recorded_at, latitude, longitude) "
               "VALUES (:t,:d,:a,1.301,103.800)", {"t": w["tenant"], "d": w["drone"], "a": at})
    where = await _sql("SELECT tableoid::regclass::text AS part FROM drone_telemetry "
                       " WHERE drone_id = :d", {"d": w["drone"]})
    assert where[0]["part"] == f"drone_telemetry_p{at:%Y%m}01", where
    # On a partitioned table the violation is reported against the partition's
    # own index (drone_telemetry_p<month>_drone_id_recorded_at_key), not the
    # parent's uq_dtel_sample — so match the part every partition shares.
    await _rejects(
        "INSERT INTO drone_telemetry (tenant_id, drone_id, recorded_at) VALUES (:t,:d,:a)",
        {"t": w["tenant"], "d": w["drone"], "a": at},
        "_drone_id_recorded_at_key")


@pytest.mark.asyncio
async def test_a_zone_must_be_a_shape():
    w = await _world()
    await _rejects(
        "INSERT INTO drone_security_zones (tenant_id, site_id, name, shape) "
        "VALUES (:t,:s,'No radius','CIRCLE')",
        {"t": w["tenant"], "s": w["site"]}, "ck_dsz_geometry")
    await _rejects(
        "INSERT INTO drone_security_zones (tenant_id, site_id, name, polygon) "
        "VALUES (:t,:s,'A line',CAST(:p AS jsonb))",
        {"t": w["tenant"], "s": w["site"],
         "p": '[{"lat":1.30,"lng":103.80},{"lat":1.31,"lng":103.81}]'},
        "ck_dsz_geometry")
    await _sql(
        "INSERT INTO drone_security_zones (tenant_id, site_id, name, shape, "
        "    center_latitude, center_longitude, radius_m) "
        "VALUES (:t,:s,'Loading Bay','CIRCLE',1.3005,103.8005,25)",
        {"t": w["tenant"], "s": w["site"]})


@pytest.mark.asyncio
async def test_a_route_can_be_reordered_in_one_transaction():
    """The sequence constraint is DEFERRABLE, so swapping two waypoints does not
    collide half-way through."""
    w = await _world()
    engine = create_async_engine(ADMIN_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE drone_waypoints SET sequence = 2 WHERE id = :i"),
                               {"i": w["wp1"]})
            await conn.execute(text("UPDATE drone_waypoints SET sequence = 1 WHERE id = :i"),
                               {"i": w["wp2"]})
    finally:
        await engine.dispose()
    order = await _sql("SELECT id FROM drone_waypoints WHERE route_id = :r ORDER BY sequence",
                       {"r": w["route"]})
    assert [r["id"] for r in order] == [w["wp2"], w["wp1"]]


@pytest.mark.asyncio
async def test_selected_days_needs_days():
    w = await _world()
    await _rejects(
        "INSERT INTO drone_schedules (tenant_id, mission_id, schedule_type, start_date, launch_time) "
        "VALUES (:t,:m,'SELECTED_DAYS',:d,'22:00')",
        {"t": w["tenant"], "m": w["mission"], "d": date.today()}, "ck_dsched_selected")


@pytest.mark.asyncio
async def test_a_projected_location_must_say_where():
    """location_method = PROJECTED claims an estimate exists. Without the
    coordinates it would be a label on nothing."""
    w = await _world()
    await _rejects(
        "INSERT INTO drone_events (tenant_id, drone_id, module_type, detected_at, location_method) "
        "VALUES (:t,:d,'intrusion',now(),'PROJECTED')",
        {"t": w["tenant"], "d": w["drone"]}, "ck_devent_estimate")


@pytest.mark.asyncio
async def test_confidence_and_risk_stay_in_their_ranges():
    w = await _world()
    # 1.5, not 94: a percentage typed where a fraction belongs overflows
    # NUMERIC(5,4) before the CHECK is reached, which is a rejection too — but
    # this test is about the CHECK, so it needs a value the type accepts.
    await _rejects(
        "INSERT INTO drone_events (tenant_id, module_type, detected_at, ai_confidence) "
        "VALUES (:t,'intrusion',now(),1.5)",
        {"t": w["tenant"]}, "ck_devent_confidence")
    await _rejects(
        "INSERT INTO drone_events (tenant_id, module_type, detected_at, ai_confidence) "
        "VALUES (:t,'intrusion',now(),94)",
        {"t": w["tenant"]}, "numeric field overflow")
    await _rejects(
        "INSERT INTO drone_events (tenant_id, module_type, detected_at, risk_score) "
        "VALUES (:t,'intrusion',now(),101)",
        {"t": w["tenant"]}, "ck_devent_risk_score")


@pytest.mark.asyncio
async def test_media_belongs_to_something():
    w = await _world()
    await _rejects(
        "INSERT INTO drone_event_media (tenant_id, media_kind, storage_path, captured_at) "
        "VALUES (:t,'SNAPSHOT','drone/x.jpg',now())",
        {"t": w["tenant"]}, "ck_dmedia_owner")


@pytest.mark.asyncio
async def test_an_edge_resend_is_recognised_by_its_client_ref():
    w = await _world()
    ref = uuid.uuid4()
    stmt = ("INSERT INTO drone_events (tenant_id, module_type, detected_at, client_ref) "
            "VALUES (:t,'intrusion',now(),:r)")
    await _sql(stmt, {"t": w["tenant"], "r": ref})
    await _rejects(stmt, {"t": w["tenant"], "r": ref}, "drone_events_client_ref_key")


# ─── C. Deletion never blocks, and history survives ──────────────────────────

@pytest.mark.asyncio
async def test_a_tenant_with_a_full_drone_history_can_be_deleted():
    """The test suite's cleanup deletes tenants, and was once silently defeated
    by a single RESTRICT foreign key. Every drone table must cascade."""
    w = await _world()
    sid = await _session(w, status="COMPLETED")
    alert, incident, event = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _sql("INSERT INTO alerts (id, tenant_id, module_type, title, site_id) "
               "VALUES (:i,:t,'drone_patrol','Possible intrusion',:s)",
               {"i": alert, "t": w["tenant"], "s": w["site"]})
    await _sql("INSERT INTO incidents (id, tenant_id, alert_id, title) "
               "VALUES (:i,:t,:a,'Possible intrusion — Rear Fence')",
               {"i": incident, "t": w["tenant"], "a": alert})
    await _sql(
        "INSERT INTO drone_events (id, tenant_id, site_id, drone_id, session_id, mission_id, "
        "    security_zone_id, alert_id, incident_id, module_type, detected_at, risk_level) "
        "VALUES (:i,:t,:s,:d,:ss,:m,:z,:a,:inc,'intrusion',now(),'HIGH')",
        {"i": event, "t": w["tenant"], "s": w["site"], "d": w["drone"], "ss": sid,
         "m": w["mission"], "z": w["zone"], "a": alert, "inc": incident})
    await _sql("INSERT INTO drone_event_media (tenant_id, event_id, session_id, media_kind, "
               "    storage_path, captured_at) VALUES (:t,:e,:s,'SNAPSHOT','drone/a.jpg',now())",
               {"t": w["tenant"], "e": event, "s": sid})
    await _sql("INSERT INTO drone_event_cameras (tenant_id, event_id, camera_id, camera_name, distance_m) "
               "VALUES (:t,:e,:c,'CAM-27',42.5)",
               {"t": w["tenant"], "e": event, "c": w["camera"]})
    await _sql("INSERT INTO drone_telemetry (tenant_id, drone_id, session_id, recorded_at) "
               "VALUES (:t,:d,:s,now())", {"t": w["tenant"], "d": w["drone"], "s": sid})
    await _sql("INSERT INTO drone_session_waypoints (tenant_id, session_id, sequence, latitude, longitude) "
               "VALUES (:t,:s,1,1.301,103.800)", {"t": w["tenant"], "s": sid})
    await _sql("INSERT INTO drone_maintenance_logs (tenant_id, drone_id, maintenance_type) "
               "VALUES (:t,:d,'INSPECTION')", {"t": w["tenant"], "d": w["drone"]})
    await _sql("INSERT INTO drone_module_licenses (tenant_id, is_enabled) VALUES (:t,TRUE)",
               {"t": w["tenant"]})

    await _sql("DELETE FROM tenants WHERE id = :t", {"t": w["tenant"]})

    left = await _sql(
        "SELECT (SELECT count(*) FROM drones WHERE tenant_id = :t) "
        "     + (SELECT count(*) FROM drone_patrol_sessions WHERE tenant_id = :t) "
        "     + (SELECT count(*) FROM drone_events WHERE tenant_id = :t) "
        "     + (SELECT count(*) FROM drone_telemetry WHERE tenant_id = :t) AS n",
        {"t": w["tenant"]})
    assert left[0]["n"] == 0


@pytest.mark.asyncio
async def test_deleting_a_mission_keeps_the_flight_record():
    """A session is history. Removing the mission it came from must not remove
    it, and it must still say what the mission was called."""
    w = await _world()
    sid = await _session(w, status="COMPLETED")
    await _sql("DELETE FROM drone_missions WHERE id = :m", {"m": w["mission"]})
    row = await _sql("SELECT mission_id, mission_name FROM drone_patrol_sessions WHERE id = :s",
                     {"s": sid})
    assert row and row[0]["mission_id"] is None
    assert row[0]["mission_name"] == "Night Perimeter Security"


@pytest.mark.asyncio
async def test_deleting_a_camera_keeps_the_correlation_readable():
    w = await _world()
    event = uuid.uuid4()
    await _sql("INSERT INTO drone_events (id, tenant_id, module_type, detected_at) "
               "VALUES (:i,:t,'intrusion',now())", {"i": event, "t": w["tenant"]})
    await _sql("INSERT INTO drone_event_cameras (tenant_id, event_id, camera_id, camera_name) "
               "VALUES (:t,:e,:c,'CAM-27')", {"t": w["tenant"], "e": event, "c": w["camera"]})
    await _sql("DELETE FROM cameras WHERE id = :c", {"c": w["camera"]})
    row = await _sql("SELECT camera_id, camera_name FROM drone_event_cameras WHERE event_id = :e",
                     {"e": event})
    assert row and row[0]["camera_id"] is None and row[0]["camera_name"] == "CAM-27"


# ─── D. Tenant isolation, as the role the app actually uses ──────────────────

async def _as_app(tenant: uuid.UUID, stmt: str, params: dict | None = None):
    engine = create_async_engine(APP_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            who = (await s.execute(text(
                "SELECT current_user, (SELECT rolbypassrls FROM pg_roles "
                " WHERE rolname = current_user)"))).first()
            assert who[1] is False, f"connected as {who[0]!r}, which bypasses RLS"
            await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                            {"t": str(tenant)})
            r = await s.execute(text(stmt), params or {})
            rows = r.mappings().all() if r.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_tenant_sees_none_of_another_tenants_drone_data():
    a, b = await _world(), await _world()
    for w in (a, b):
        sid = await _session(w, status="COMPLETED")
        await _sql("INSERT INTO drone_events (tenant_id, drone_id, session_id, module_type, detected_at) "
                   "VALUES (:t,:d,:s,'intrusion',now())",
                   {"t": w["tenant"], "d": w["drone"], "s": sid})
        await _sql("INSERT INTO drone_telemetry (tenant_id, drone_id, recorded_at) VALUES (:t,:d,now())",
                   {"t": w["tenant"], "d": w["drone"]})
        await _sql("INSERT INTO drone_module_licenses (tenant_id, is_enabled) VALUES (:t,TRUE)",
                   {"t": w["tenant"]})

    counts = await _as_app(a["tenant"], """
        SELECT (SELECT count(*) FROM drones                WHERE id = :bd)        AS drones,
               (SELECT count(*) FROM drone_missions        WHERE id = :bm)        AS missions,
               (SELECT count(*) FROM drone_routes          WHERE id = :br)        AS routes,
               (SELECT count(*) FROM drone_security_zones  WHERE id = :bz)        AS zones,
               (SELECT count(*) FROM drone_patrol_sessions WHERE drone_id = :bd)  AS sessions,
               (SELECT count(*) FROM drone_events          WHERE drone_id = :bd)  AS events,
               (SELECT count(*) FROM drone_telemetry       WHERE drone_id = :bd)  AS telemetry,
               (SELECT count(*) FROM drone_module_licenses WHERE tenant_id = :bt) AS licence,
               (SELECT count(*) FROM drones                WHERE id = :ad)        AS own_drone
    """, {"bd": b["drone"], "bm": b["mission"], "br": b["route"], "bz": b["zone"],
          "bt": b["tenant"], "ad": a["drone"]})
    c = dict(counts[0])
    own = c.pop("own_drone")
    assert own == 1, "tenant A cannot see its own drone — the test would prove nothing"
    assert all(v == 0 for v in c.values()), f"tenant A read tenant B's rows: {c}"


@pytest.mark.asyncio
async def test_a_tenant_cannot_write_a_drone_into_another_tenant():
    a, b = await _world(), await _world()
    with pytest.raises(Exception) as exc:
        await _as_app(a["tenant"],
                      "INSERT INTO drones (tenant_id, name, code) VALUES (:t,'Smuggled','D-99')",
                      {"t": b["tenant"]})
    assert "row-level security" in str(exc.value).lower(), str(exc.value)[:300]
