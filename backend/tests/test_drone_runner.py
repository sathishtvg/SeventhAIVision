"""Drone patrol, phase 4: flights, end to end, against the real database.

The API queues or runs; the runner — called here directly, with a chosen clock —
launches, flies the simulator, carries out commands, creates scheduled sessions
and sweeps for lost links. Everything goes through svc_app with RLS enforced; the
first test proves the runner's connection cannot bypass it.

  A — Flying: run, complete, block
  B — Commands: abort, pause, cancel, duplicates, capabilities, licence
  C — Failures and alerts
  D — Schedules: one session per run, missed runs, runs not owed
  E — Health: lost links, recovery, a new drone coming online
  F — Two tenants
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.services import drone_runner as runner
from app.services.drone_providers import Capability, SimulatorProvider

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)
ADMIN, OPERATOR = 2, 4
TERMINAL = ("COMPLETED", "FAILED", "ABORTED", "CANCELLED", "BLOCKED", "MISSED")


async def _run(statements: list[tuple[str, dict]]) -> list:
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    out = []
    try:
        async with factory() as s:
            for stmt, params in statements:
                r = await s.execute(text(stmt), params)
                out.append(r.mappings().all() if r.returns_rows else [])
            await s.commit()
        return out
    finally:
        await engine.dispose()


async def _sql(stmt: str, params: dict | None = None) -> list:
    return (await _run([(stmt, params or {})]))[0]


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app), base_url="http://test")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _world(*, battery: int = 100, speed: float = 50.0, failure_rate: float = 0.0,
                 provider: bool = True, drone_status: str = "READY") -> dict:
    """A licensed tenant with a fenced site, a simulator provider flying at `speed`x,
    a healthy drone that has just reported, a 3-waypoint route, a profile and a
    mission that uses them all."""
    w = {k: uuid.uuid4() for k in ("tenant", "site", "provider", "drone", "route", "profile",
                                   "mission", "admin", "operator")}
    stmts = [
        ("INSERT INTO tenants (id, name, slug) VALUES (:t,'Runner Co',:s)",
         {"t": w["tenant"], "s": f"drun-{w['tenant'].hex[:10]}"}),
        ("INSERT INTO sites (id, tenant_id, name, latitude, longitude, geofence_radius_meters) "
         "VALUES (:i,:t,'Factory A',1.3,103.8,800)", {"i": w["site"], "t": w["tenant"]}),
        ("INSERT INTO drone_module_licenses (tenant_id, is_enabled, licensed_at) VALUES (:t,TRUE,now())",
         {"t": w["tenant"]}),
    ]
    for key, role in (("admin", ADMIN), ("operator", OPERATOR)):
        stmts.append(("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                      "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
                      {"i": w[key], "t": w["tenant"], "r": role, "e": f"{key}-{w[key].hex[:8]}@run.test",
                       "n": f"{key.title()} User"}))
    if provider:
        stmts.append(("INSERT INTO drone_provider_configs (id, tenant_id, name, provider_key, config) "
                      "VALUES (:i,:t,'Sim','simulator',CAST(:c AS jsonb))",
                      {"i": w["provider"], "t": w["tenant"],
                       "c": f'{{"speed_factor": {speed}, "failure_rate": {failure_rate}}}'}))
    stmts += [
        ("INSERT INTO drones (id, tenant_id, site_id, provider_config_id, name, code, status, battery_level, "
         "   gps_status, communication_status, camera_status, storage_status, last_heartbeat_at, "
         "   current_latitude, current_longitude) "
         "VALUES (:i,:t,:s,:p,'Drone One','D-01',:st,:b,'OK','OK','OK','OK',now(),1.3,103.8)",
         {"i": w["drone"], "t": w["tenant"], "s": w["site"], "p": w["provider"] if provider else None,
          "st": drone_status, "b": battery}),
        ("INSERT INTO drone_routes (id, tenant_id, site_id, name, base_latitude, base_longitude, "
         "   default_speed_mps) VALUES (:i,:t,:s,'Perimeter',1.3,103.8,8)",
         {"i": w["route"], "t": w["tenant"], "s": w["site"]}),
        ("INSERT INTO drone_security_profiles (id, tenant_id, name) VALUES (:i,:t,'Night')",
         {"i": w["profile"], "t": w["tenant"]}),
        ("INSERT INTO drone_missions (id, tenant_id, site_id, drone_id, route_id, security_profile_id, name) "
         "VALUES (:i,:t,:s,:d,:r,:p,'Night Perimeter')",
         {"i": w["mission"], "t": w["tenant"], "s": w["site"], "d": w["drone"], "r": w["route"],
          "p": w["profile"]}),
    ]
    for seq, lat, lng, hover in ((1, 1.3010, 103.8000, 5), (2, 1.3010, 103.8010, 0), (3, 1.3000, 103.8010, 0)):
        stmts.append(("INSERT INTO drone_waypoints (tenant_id, route_id, sequence, latitude, longitude, "
                      "   hover_seconds) VALUES (:t,:r,:q,:la,:lo,:h)",
                      {"t": w["tenant"], "r": w["route"], "q": seq, "la": lat, "lo": lng, "h": hover}))
    await _run(stmts)
    w["h_admin"] = {"Authorization": f"Bearer {create_access_token(str(w['admin']), str(w['tenant']), ADMIN)}"}
    w["h_op"] = {"Authorization": f"Bearer {create_access_token(str(w['operator']), str(w['tenant']), OPERATOR)}"}
    return w


async def _start(c: AsyncClient, w: dict) -> dict:
    r = await c.post(f"/api/v1/drone-missions/{w['mission']}/run", headers=w["h_admin"])
    assert r.status_code == 201, r.text
    return r.json()


async def _session(sid) -> dict:
    return dict((await _sql("SELECT * FROM drone_patrol_sessions WHERE id = :i", {"i": uuid.UUID(str(sid))}))[0])


async def _fly_until(sid, start: datetime, *, pub=None, until=TERMINAL, max_ticks: int = 40,
                     step_s: float = 2.0) -> tuple[dict, datetime]:
    now = start
    for _ in range(max_ticks):
        now += timedelta(seconds=step_s)
        await runner.run_tick(AsyncSessionLocal, pub, now=now)
        s = await _session(sid)
        if s["status"] in until:
            return s, now
    return await _session(sid), now


# ─── A. Flying ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_runner_connects_as_the_app_and_cannot_bypass_rls():
    async with AsyncSessionLocal() as db:
        who = (await db.execute(text(
            "SELECT current_user, (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"))).first()
    assert who[1] is False, f"the runner connects as {who[0]!r}, which bypasses RLS"


@pytest.mark.asyncio
async def test_a_manual_run_flies_the_whole_route_and_completes():
    w = await _world()
    pub = runner.ListPublisher()
    async with _client() as c:
        started = await _start(c, w)
    assert started["session"]["status"] == "READY", started["preflight"]
    sid = started["session"]["id"]
    s, _ = await _fly_until(sid, _now(), pub=pub)

    assert s["status"] == "COMPLETED", s
    assert s["launched_at"] and s["landed_at"] and s["ended_at"]
    assert s["provider_mission_ref"].startswith("sim-")
    wps = await _sql("SELECT sequence, status FROM drone_session_waypoints WHERE session_id = :s "
                     " ORDER BY sequence", {"s": uuid.UUID(sid)})
    assert [(r["sequence"], r["status"]) for r in wps] == [(1, "OBSERVED"), (2, "OBSERVED"), (3, "OBSERVED")]
    n = (await _sql("SELECT count(*) AS n FROM drone_telemetry WHERE session_id = :s", {"s": uuid.UUID(sid)}))[0]["n"]
    assert n > 10, "the flight left no telemetry"
    d = (await _sql("SELECT status, total_flight_seconds, last_flight_at, battery_level FROM drones "
                    " WHERE id = :d", {"d": w["drone"]}))[0]
    assert d["status"] in ("READY", "CHARGING") and d["total_flight_seconds"] > 0 and d["last_flight_at"]
    types = [e[1] for e in pub.events]
    assert "drone_session_updated" in types and "drone_telemetry" in types


@pytest.mark.asyncio
async def test_a_run_that_fails_preflight_is_recorded_blocked_and_never_flies():
    w = await _world(battery=12)
    async with _client() as c:
        started = await _start(c, w)
    s = started["session"]
    assert s["status"] == "BLOCKED" and "BATTERY" in started["preflight"]["blocking"]
    assert "Battery 12%" in s["blocked_reason"]
    await runner.run_tick(AsyncSessionLocal, None, now=_now() + timedelta(seconds=2))
    assert (await _session(s["id"]))["status"] == "BLOCKED"
    n = (await _sql("SELECT count(*) AS n FROM drone_telemetry WHERE session_id = :s",
                    {"s": uuid.UUID(s["id"])}))[0]["n"]
    assert n == 0


@pytest.mark.asyncio
async def test_a_drone_cannot_be_sent_on_two_flights():
    w = await _world(speed=1)
    async with _client() as c:
        first = await _start(c, w)
        second = await _start(c, w)
    assert first["session"]["status"] == "READY"
    assert second["session"]["status"] == "BLOCKED"
    assert "already on a mission" in second["session"]["blocked_reason"]


@pytest.mark.asyncio
async def test_the_preflight_preview_creates_nothing():
    w = await _world()
    async with _client() as c:
        r = await c.get(f"/api/v1/drone-missions/{w['mission']}/preflight", headers=w["h_op"])
    assert r.status_code == 200 and r.json()["passed"] is True
    assert r.json()["estimate"]["duration_s"] > 0
    n = (await _sql("SELECT count(*) AS n FROM drone_patrol_sessions WHERE mission_id = :m",
                    {"m": w["mission"]}))[0]["n"]
    assert n == 0


# ─── B. Commands ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_abort_brings_the_drone_home_and_the_command_says_what_happened():
    w = await _world(speed=2)
    async with _client() as c:
        sid = (await _start(c, w))["session"]["id"]
        t = _now()
        s, t = await _fly_until(sid, t, until=("ACTIVE",))
        r = await c.post(f"/api/v1/drone-patrols/{sid}/abort", headers=w["h_op"],
                         json={"reason": "Person reported at the gate; recalling"})
        assert r.status_code == 202 and r.json()["queued"] is True, r.text
        s, t = await _fly_until(sid, t, max_ticks=80)
        cmds = (await c.get(f"/api/v1/drone-patrols/{sid}/commands", headers=w["h_op"])).json()
    assert s["status"] == "ABORTED", s
    assert cmds[0]["command"] == "ABORT" and cmds[0]["status"] == "DONE"
    assert cmds[0]["requested_by_name"] == "Operator User"
    audit = await _sql("SELECT count(*) AS n FROM audit_logs WHERE tenant_id = :t AND action = 'drone.command.abort'",
                       {"t": w["tenant"]})
    assert audit[0]["n"] == 1


@pytest.mark.asyncio
async def test_pause_holds_and_resume_carries_on():
    w = await _world(speed=2)
    async with _client() as c:
        sid = (await _start(c, w))["session"]["id"]
        s, t = await _fly_until(sid, _now(), until=("ACTIVE",))
        assert (await c.post(f"/api/v1/drone-patrols/{sid}/pause", headers=w["h_op"])).status_code == 202
        s, t = await _fly_until(sid, t, until=("PAUSED",), max_ticks=3)
        assert s["status"] == "PAUSED"
        refused = await c.post(f"/api/v1/drone-patrols/{sid}/pause", headers=w["h_op"])
        assert refused.status_code == 409 and "Only an active mission" in refused.json()["detail"]
        assert (await c.post(f"/api/v1/drone-patrols/{sid}/resume", headers=w["h_op"])).status_code == 202
        s, _ = await _fly_until(sid, t, max_ticks=80)
    assert s["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_a_mission_cancelled_before_launch_never_flies():
    w = await _world()
    async with _client() as c:
        sid = (await _start(c, w))["session"]["id"]
        r = await c.post(f"/api/v1/drone-patrols/{sid}/cancel", headers=w["h_op"])
        assert r.status_code == 202
        await runner.run_tick(AsyncSessionLocal, None, now=_now() + timedelta(seconds=2))
        again = await c.post(f"/api/v1/drone-patrols/{sid}/cancel", headers=w["h_op"])
    s = await _session(sid)
    assert s["status"] == "CANCELLED" and s["launched_at"] is None
    assert again.status_code == 409 and "already ended" in again.json()["detail"]


@pytest.mark.asyncio
async def test_two_aborts_at_once_are_one_command():
    w = await _world(speed=1)
    async with _client() as c:
        sid = (await _start(c, w))["session"]["id"]
        await _fly_until(sid, _now(), until=("ACTIVE", "LAUNCHING"), max_ticks=3)
        a = await c.post(f"/api/v1/drone-patrols/{sid}/abort", headers=w["h_op"])
        b = await c.post(f"/api/v1/drone-patrols/{sid}/abort", headers=w["h_admin"])
    assert a.json()["queued"] is True and b.json()["queued"] is False
    assert a.json()["command"]["id"] == b.json()["command"]["id"]


@pytest.mark.asyncio
async def test_a_command_the_provider_cannot_do_is_refused_at_once(monkeypatch):
    """Never queue what the aircraft cannot do: the operator is told now, not
    after the drone ignores it."""
    w = await _world(speed=1)
    monkeypatch.setattr(SimulatorProvider, "capabilities",
                        SimulatorProvider.capabilities - {Capability.PAUSE})
    async with _client() as c:
        sid = (await _start(c, w))["session"]["id"]
        await _fly_until(sid, _now(), until=("ACTIVE",))
        r = await c.post(f"/api/v1/drone-patrols/{sid}/pause", headers=w["h_op"])
    assert r.status_code == 409 and "cannot pause" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_lapsed_licence_stops_new_flights_but_never_stops_bringing_one_home():
    w = await _world(speed=1)
    async with _client() as c:
        sid = (await _start(c, w))["session"]["id"]
        await _fly_until(sid, _now(), until=("ACTIVE", "LAUNCHING"), max_ticks=3)
        await _sql("UPDATE drone_module_licenses SET expires_at = now() - interval '1 minute' WHERE tenant_id = :t",
                   {"t": w["tenant"]})
        home = await c.post(f"/api/v1/drone-patrols/{sid}/return-to-home", headers=w["h_op"])
        run = await c.post(f"/api/v1/drone-missions/{w['mission']}/run", headers=w["h_admin"])
    assert home.status_code == 202, home.text
    assert run.status_code == 403 and "expired" in run.json()["detail"]
    # The lapsed tenant's airborne flight is still carried to the ground.
    tenants = [str(t) for t in (await _sql("SELECT tenant_id FROM drone_runner_tenants()"))]
    assert any(str(w["tenant"]) in t for t in tenants)


# ─── C. Failures and alerts ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_failing_flight_raises_one_alert_announced_after_commit():
    w = await _world(failure_rate=1.0)
    pub = runner.ListPublisher()
    async with _client() as c:
        sid = (await _start(c, w))["session"]["id"]
    s, t = await _fly_until(sid, _now(), pub=pub, max_ticks=60)
    await runner.run_tick(AsyncSessionLocal, pub, now=t + timedelta(seconds=2))   # a tick after landing
    assert s["status"] in ("FAILED", "COMPLETED") and s["failure_code"], s
    code = "drone.mission_failed" if s["status"] == "FAILED" else "drone.flight_fault"
    alerts = await _sql("SELECT id, module_type, site_id FROM alerts WHERE tenant_id = :t AND alert_code = :c",
                        {"t": w["tenant"], "c": code})
    assert len(alerts) == 1, "one failure must raise exactly one alert"
    assert alerts[0]["module_type"] == "drone_patrol" and alerts[0]["site_id"] == w["site"]
    announced = [p for (_, e, p) in pub.events if e == "alert_created" and p["alert_code"] == code]
    assert [a["alert_id"] for a in announced] == [str(alerts[0]["id"])]


# ─── D. Schedules ────────────────────────────────────────────────────────────

async def _schedule(w: dict, run_at: datetime, *, armed_hours_ago: float = 1.0, grace: int = 15) -> uuid.UUID:
    sid = uuid.uuid4()
    await _run([
        ("INSERT INTO drone_schedules (id, tenant_id, mission_id, schedule_type, timezone, start_date, "
         "   launch_time, grace_minutes) VALUES (:i,:t,:m,'DAILY','UTC',:d,:lt,:g)",
         {"i": sid, "t": w["tenant"], "m": w["mission"], "d": run_at.date(),
          "lt": time(run_at.hour, run_at.minute), "g": grace}),
        # When the schedule and its mission were last changed: runs before this
        # are never owed.
        ("UPDATE drone_schedules SET created_at = now() - make_interval(secs => :s), "
         "       updated_at = now() - make_interval(secs => :s) WHERE id = :i",
         {"s": armed_hours_ago * 3600, "i": sid}),
        ("UPDATE drone_missions SET updated_at = now() - make_interval(secs => :s) WHERE id = :m",
         {"s": armed_hours_ago * 3600, "m": w["mission"]}),
    ])
    return sid


@pytest.mark.asyncio
async def test_a_schedule_makes_one_session_per_run_however_often_the_scheduler_ticks():
    w = await _world()
    now = _now()
    run_at = (now - timedelta(minutes=2)).replace(second=0, microsecond=0)
    sch = await _schedule(w, run_at)
    for _ in range(3):
        await runner.run_schedule_tick(AsyncSessionLocal, None, now=now)
    rows = await _sql("SELECT id, status, scheduled_for, triggered_by FROM drone_patrol_sessions "
                      " WHERE schedule_id = :s", {"s": sch})
    assert len(rows) == 1, rows
    assert rows[0]["status"] == "READY" and rows[0]["triggered_by"] == "SCHEDULE"
    assert rows[0]["scheduled_for"] == run_at
    s, _ = await _fly_until(rows[0]["id"], now)
    assert s["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_a_run_missed_while_the_runner_was_down_is_recorded_once():
    w = await _world()
    now = _now()
    run_at = (now - timedelta(minutes=40)).replace(second=0, microsecond=0)   # grace is 15 min
    sch = await _schedule(w, run_at, armed_hours_ago=2)
    await runner.run_schedule_tick(AsyncSessionLocal, None, now=now)
    await runner.run_schedule_tick(AsyncSessionLocal, None, now=now + timedelta(seconds=30))
    rows = await _sql("SELECT status FROM drone_patrol_sessions WHERE schedule_id = :s", {"s": sch})
    assert [r["status"] for r in rows] == ["MISSED"]
    alerts = await _sql("SELECT count(*) AS n FROM alerts WHERE tenant_id = :t AND alert_code = 'drone.mission_missed'",
                        {"t": w["tenant"]})
    assert alerts[0]["n"] == 1


@pytest.mark.asyncio
async def test_a_schedule_created_after_its_time_does_not_owe_todays_run():
    """Creating a 'daily at 09:00' schedule at 14:00 is not a missed patrol."""
    w = await _world()
    now = _now()
    run_at = (now - timedelta(minutes=30)).replace(second=0, microsecond=0)
    sch = await _schedule(w, run_at, armed_hours_ago=0)
    await runner.run_schedule_tick(AsyncSessionLocal, None, now=now)
    rows = await _sql("SELECT count(*) AS n FROM drone_patrol_sessions WHERE schedule_id = :s", {"s": sch})
    assert rows[0]["n"] == 0


# ─── E. Health ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_silent_drone_is_marked_lost_once_and_recovers_when_heard():
    w = await _world(provider=False)
    await _sql("UPDATE drones SET last_heartbeat_at = now() - interval '5 minutes' WHERE id = :d", {"d": w["drone"]})
    await runner.run_health_tick(AsyncSessionLocal, None, now=_now())
    await runner.run_health_tick(AsyncSessionLocal, None, now=_now() + timedelta(seconds=15))
    d = (await _sql("SELECT status, comms_alerted_at FROM drones WHERE id = :d", {"d": w["drone"]}))[0]
    assert d["status"] == "COMMUNICATION_LOST" and d["comms_alerted_at"] is not None
    n = (await _sql("SELECT count(*) AS n FROM alerts WHERE tenant_id = :t AND alert_code = 'drone.comms_lost'",
                    {"t": w["tenant"]}))[0]["n"]
    assert n == 1, "one outage must raise one alert"

    # It is heard from again.
    await _sql("INSERT INTO drone_provider_configs (id, tenant_id, name, provider_key) "
               "VALUES (:i,:t,'Sim','simulator')", {"i": w["provider"], "t": w["tenant"]})
    await _sql("UPDATE drones SET provider_config_id = :p WHERE id = :d", {"p": w["provider"], "d": w["drone"]})
    await runner.run_health_tick(AsyncSessionLocal, None, now=_now() + timedelta(seconds=30))
    d = (await _sql("SELECT status, comms_alerted_at FROM drones WHERE id = :d", {"d": w["drone"]}))[0]
    assert d["status"] in ("READY", "CHARGING") and d["comms_alerted_at"] is None


@pytest.mark.asyncio
async def test_a_new_simulated_drone_comes_online_by_itself():
    w = await _world(drone_status="OFFLINE")
    await _sql("UPDATE drones SET last_heartbeat_at = NULL WHERE id = :d", {"d": w["drone"]})
    await runner.run_health_tick(AsyncSessionLocal, None, now=_now())
    d = (await _sql("SELECT status, last_heartbeat_at, communication_status FROM drones WHERE id = :d",
                    {"d": w["drone"]}))[0]
    assert d["status"] == "READY" and d["last_heartbeat_at"] is not None and d["communication_status"] == "OK"


# ─── F. Two tenants ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_tenants_fly_at_once_and_each_keeps_its_own_records():
    a, b = await _world(), await _world()
    async with _client() as c:
        sa = (await _start(c, a))["session"]["id"]
        sb = (await _start(c, b))["session"]["id"]
    t = _now()
    for _ in range(10):
        t += timedelta(seconds=2)
        await runner.run_tick(AsyncSessionLocal, None, now=t)
    for w, sid in ((a, sa), (b, sb)):
        s = await _session(sid)
        assert s["status"] == "COMPLETED", s
        owners = await _sql("SELECT DISTINCT tenant_id FROM drone_telemetry WHERE session_id = :s",
                            {"s": uuid.UUID(sid)})
        assert [r["tenant_id"] for r in owners] == [w["tenant"]]
