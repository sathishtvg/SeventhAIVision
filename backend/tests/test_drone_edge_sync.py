"""Drone patrol, phase 5: the central side of a site edge gateway, against the
real database.

A gateway authenticates with its own credential, claims the sessions for its
drones, and reports — possibly hours late — what they did. These tests play the
gateway by hand (the simulator produces the flight updates), so each rule can be
pinned on its own; test_drone_edge_agent.py runs the real gateway end to end.

  A — Recognising a gateway
  B — Who flies what: claiming, and the central runner keeping out
  C — Flight updates: order, duplicates, lost answers, late corrections
  D — Commands relayed to the gateway
  E — Health, events and files
  F — The gateway's own health
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.services import drone_flight_plan as fp
from app.services import drone_runner as runner
from app.services.drone_edge_wire import from_flight_update
from app.services.drone_providers import DroneRef, FlightContext, SimulatorProvider

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)
ADMIN, OPERATOR = 2, 4
JPEG = b"\xff\xd8\xff\xe0" + b"drone snapshot " * 40 + b"\xff\xd9"


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


def _key(tenant) -> tuple[str, str]:
    key = f"deg.{tenant}.{secrets.token_urlsafe(24)}"
    return key, hashlib.sha256(key.encode()).hexdigest()


async def _world(*, battery: int = 100, speed: float = 50.0) -> dict:
    """A licensed tenant with a site, a gateway at that site, a simulator drone
    behind the gateway, a 3-waypoint route and a mission — and a second gateway
    at the same site that flies nothing."""
    w = {k: uuid.uuid4() for k in ("tenant", "site", "provider", "drone", "route", "mission", "admin",
                                   "operator", "gw", "gw2")}
    w["key"], digest = _key(w["tenant"])
    w["key2"], digest2 = _key(w["tenant"])
    stmts = [
        ("INSERT INTO tenants (id, name, slug) VALUES (:t,'Edge Co',:s)",
         {"t": w["tenant"], "s": f"dedge-{w['tenant'].hex[:10]}"}),
        ("INSERT INTO sites (id, tenant_id, name, latitude, longitude, geofence_radius_meters) "
         "VALUES (:i,:t,'Depot',1.3,103.8,800)", {"i": w["site"], "t": w["tenant"]}),
        ("INSERT INTO drone_module_licenses (tenant_id, is_enabled, licensed_at) VALUES (:t,TRUE,now())",
         {"t": w["tenant"]}),
        ("INSERT INTO drone_edge_gateways (id, tenant_id, site_id, name, code, credential_hash, credential_prefix) "
         "VALUES (:i,:t,:s,'Depot Edge','EDGE-1',:h,'x')", {"i": w["gw"], "t": w["tenant"], "s": w["site"], "h": digest}),
        ("INSERT INTO drone_edge_gateways (id, tenant_id, site_id, name, code, credential_hash, credential_prefix) "
         "VALUES (:i,:t,:s,'Spare Edge','EDGE-2',:h,'y')", {"i": w["gw2"], "t": w["tenant"], "s": w["site"], "h": digest2}),
        ("INSERT INTO drone_provider_configs (id, tenant_id, name, provider_key, config, secret_encrypted) "
         "VALUES (:i,:t,'Sim','simulator',CAST(:c AS jsonb),'not-for-the-edge')",
         {"i": w["provider"], "t": w["tenant"], "c": json.dumps({"speed_factor": speed, "failure_rate": 0})}),
        ("INSERT INTO drones (id, tenant_id, site_id, provider_config_id, edge_gateway_id, name, code, status, "
         "   battery_level, gps_status, communication_status, camera_status, storage_status, last_heartbeat_at, "
         "   current_latitude, current_longitude) "
         "VALUES (:i,:t,:s,:p,:g,'Drone One','D-01','READY',:b,'OK','OK','OK','OK',now(),1.3,103.8)",
         {"i": w["drone"], "t": w["tenant"], "s": w["site"], "p": w["provider"], "g": w["gw"], "b": battery}),
        ("INSERT INTO drone_routes (id, tenant_id, site_id, name, base_latitude, base_longitude, default_speed_mps) "
         "VALUES (:i,:t,:s,'Perimeter',1.3,103.8,8)", {"i": w["route"], "t": w["tenant"], "s": w["site"]}),
        ("INSERT INTO drone_missions (id, tenant_id, site_id, drone_id, route_id, name) "
         "VALUES (:i,:t,:s,:d,:r,'Night Perimeter')",
         {"i": w["mission"], "t": w["tenant"], "s": w["site"], "d": w["drone"], "r": w["route"]}),
    ]
    for key, role in (("admin", ADMIN), ("operator", OPERATOR)):
        stmts.append(("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                      "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
                      {"i": w[key], "t": w["tenant"], "r": role, "e": f"{key}-{w[key].hex[:8]}@edge.test",
                       "n": f"{key.title()} User"}))
    for seq, lat, lng in ((1, 1.3010, 103.8000), (2, 1.3010, 103.8010), (3, 1.3000, 103.8010)):
        stmts.append(("INSERT INTO drone_waypoints (tenant_id, route_id, sequence, latitude, longitude) "
                      "VALUES (:t,:r,:q,:la,:lo)", {"t": w["tenant"], "r": w["route"], "q": seq, "la": lat, "lo": lng}))
    await _run(stmts)
    w["h_admin"] = {"Authorization": f"Bearer {create_access_token(str(w['admin']), str(w['tenant']), ADMIN)}"}
    w["h_op"] = {"Authorization": f"Bearer {create_access_token(str(w['operator']), str(w['tenant']), OPERATOR)}"}
    return w


def _batch(**parts) -> dict:
    return {"batch_id": str(uuid.uuid4()), "sent_at": _now().isoformat(), **parts}


async def _sync(c: AsyncClient, key: str, **parts):
    return await c.post("/api/v1/drone-edge/sync", json=_batch(**parts), headers={"X-Gateway-Key": key})


async def _run_mission(c: AsyncClient, w: dict) -> str:
    r = await c.post(f"/api/v1/drone-missions/{w['mission']}/run", headers=w["h_admin"])
    assert r.status_code == 201, r.text
    assert r.json()["session"]["status"] == "READY", r.json()["preflight"]
    return r.json()["session"]["id"]


async def _claimed(c: AsyncClient, w: dict) -> dict:
    sid = await _run_mission(c, w)
    r = await c.post(f"/api/v1/drone-edge/sessions/{sid}/claim", headers={"X-Gateway-Key": w["key"]})
    assert r.status_code == 200, r.text
    return r.json()["session"]


async def _session(sid) -> dict:
    return dict((await _sql("SELECT * FROM drone_patrol_sessions WHERE id = :i", {"i": uuid.UUID(str(sid))}))[0])


class SimFlight:
    """The gateway's side of one flight, played with the real simulator."""

    def __init__(self, session: dict, battery: float = 100, speed: float = 50):
        snap = session["config_snapshot"]
        self.sim = SimulatorProvider({"speed_factor": speed})
        self.ctx = FlightContext(session_id=str(session["id"]), tenant_id="t",
                                 drone=DroneRef(id=str(session["drone_id"]), code="D-01", battery_level=battery),
                                 plan=fp.build_plan(snap["route"], snap["waypoints"]))
        self.seq, self.t = 0, _now()

    async def next(self, step_s: float = 2.0, method: str | None = None) -> dict:
        self.t += timedelta(seconds=step_s)
        if self.seq == 0:
            up = await self.sim.start_mission(self.ctx, self.t)
        else:
            up = await getattr(self.sim, method or "get_telemetry")(self.ctx, self.t)
        self.ctx.provider_state = up.provider_state
        self.seq += 1
        return {"session_id": self.ctx.session_id, "seq": self.seq, "at": self.t.isoformat(),
                "launched": self.seq == 1, "update": from_flight_update(up)}

    async def to_the_end(self, max_steps: int = 40) -> list[dict]:
        out = []
        for _ in range(max_steps):
            u = await self.next()
            out.append(u)
            if u["update"]["outcome"]:
                break
        return out


# ─── A. Recognising a gateway ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_anything_but_this_gateways_credential_is_refused_the_same_way():
    w = await _world()
    other = await _world()
    stolen_secret = w["key"].split(".", 2)[2]
    async with _client() as c:
        answers = [await _sync(c, k) for k in (
            "", "nonsense", "deg.not-a-uuid.abc", f"deg.{w['tenant']}.wrong-secret",
            f"deg.{other['tenant']}.{stolen_secret}",          # the right secret under another tenant
        )]
        user = await c.post("/api/v1/drone-edge/sync", json=_batch(), headers=w["h_admin"])
    assert [a.status_code for a in answers] == [401] * 5
    assert len({a.json()["detail"] for a in answers}) == 1, "the refusals must not tell attempts apart"
    assert user.status_code == 401, "a user's token is not a gateway credential"


@pytest.mark.asyncio
async def test_a_disabled_gateway_is_refused_and_a_rotated_credential_stops_working():
    w = await _world()
    async with _client() as c:
        await _sql("UPDATE drone_edge_gateways SET is_active = FALSE WHERE id = :g", {"g": w["gw"]})
        assert (await _sync(c, w["key"])).status_code == 403
        await _sql("UPDATE drone_edge_gateways SET is_active = TRUE WHERE id = :g", {"g": w["gw"]})
        r = await c.post(f"/api/v1/drones/edge-gateways/{w['gw']}/rotate-credential", headers=w["h_admin"])
        assert r.status_code == 200, r.text
        assert (await _sync(c, w["key"])).status_code == 401
        assert (await _sync(c, r.json()["credential"])).status_code == 200


@pytest.mark.asyncio
async def test_an_empty_batch_is_a_heartbeat_and_is_answered_with_the_gateways_work_and_no_secrets():
    w = await _world()
    async with _client() as c:
        r = await _sync(c, w["key"], software_version="edge/9.9", state={"buffer_depth": 0, "storage_free_pct": 80})
        gw = (await c.get("/api/v1/drones/edge-gateways", headers=w["h_admin"])).json()
    assert r.status_code == 200, r.text
    a = r.json()["assignment"]
    assert a["gateway"]["id"] == str(w["gw"]) and a["licensed"] is True
    assert [d["id"] for d in a["drones"]] == [str(w["drone"])]
    assert a["drones"][0]["provider_key"] == "simulator"
    assert "not-for-the-edge" not in r.text and "secret" not in r.text.lower(), "a provider secret was sent"
    assert a["policy"]["sync_mode"] == "incident_only" and a["policy"]["clip_pre_seconds"] == 20
    mine = next(g for g in gw if g["id"] == str(w["gw"]))
    assert mine["status"] == "ONLINE" and mine["last_seen_at"] and mine["software_version"] == "edge/9.9"
    assert await _sql("SELECT 1 FROM drone_sync_receipts WHERE gateway_id = :g", {"g": w["gw"]}) == [], \
        "a heartbeat carries nothing worth a receipt"


# ─── B. Who flies what ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_central_runner_leaves_an_edge_drones_session_to_its_gateway():
    w = await _world()
    async with _client() as c:
        sid = await _run_mission(c, w)
        assert (await _session(sid))["edge_gateway_id"] == w["gw"]
        await runner.run_tick(AsyncSessionLocal, runner.ListPublisher())
        assert (await _session(sid))["status"] == "READY", "the central runner flew an edge drone"

        a = (await _sync(c, w["key"])).json()["assignment"]
        offered = next(s for s in a["sessions"] if s["id"] == sid)
        assert offered["claimed"] is False and offered["config_snapshot"]["waypoints"]

        first = await c.post(f"/api/v1/drone-edge/sessions/{sid}/claim", headers={"X-Gateway-Key": w["key"]})
        again = await c.post(f"/api/v1/drone-edge/sessions/{sid}/claim", headers={"X-Gateway-Key": w["key"]})
    assert first.status_code == 200 and again.status_code == 200
    assert first.json()["session"]["status"] == "LAUNCHING"
    assert again.json()["session"]["claimed_at"] == first.json()["session"]["claimed_at"], "claiming twice is one claim"
    s = await _session(sid)
    assert s["status"] == "LAUNCHING" and s["edge_claimed_at"] and s["preflight_result"]["passed"] is True


@pytest.mark.asyncio
async def test_a_session_can_only_be_claimed_by_its_own_gateway():
    w = await _world()
    other = await _world()
    async with _client() as c:
        sid = await _run_mission(c, w)
        spare = await c.post(f"/api/v1/drone-edge/sessions/{sid}/claim", headers={"X-Gateway-Key": w["key2"]})
        foreign = await c.post(f"/api/v1/drone-edge/sessions/{sid}/claim", headers={"X-Gateway-Key": other["key"]})
    assert spare.status_code == 404 and foreign.status_code == 404
    assert (await _session(sid))["status"] == "READY"


@pytest.mark.asyncio
async def test_a_claim_re_runs_preflight_and_a_refusal_is_recorded_and_alerted():
    w = await _world()
    async with _client() as c:
        sid = await _run_mission(c, w)
        # The gateway's latest report: the battery is nearly flat.
        h = await _sync(c, w["key"], health=[{"drone_id": str(w["drone"]), "observed_at": _now().isoformat(),
                                              "battery_level": 12, "gps_status": "OK", "communication_status": "OK",
                                              "camera_status": "OK", "storage_status": "OK"}])
        assert h.json()["health"]["accepted"] == 1, h.text
        r = await c.post(f"/api/v1/drone-edge/sessions/{sid}/claim", headers={"X-Gateway-Key": w["key"]})
    assert r.status_code == 409 and "battery" in r.json()["detail"].lower()
    assert "BATTERY" in r.json()["preflight"]["blocking"]
    s = await _session(sid)
    assert s["status"] == "BLOCKED" and s["edge_claimed_at"] is None
    assert await _sql("SELECT 1 FROM alerts WHERE alert_code = 'drone.preflight_blocked' "
                      "AND message_params->>'session_id' = :s", {"s": sid})


@pytest.mark.asyncio
async def test_an_operator_cancel_waiting_before_launch_wins_over_a_claim():
    w = await _world()
    async with _client() as c:
        sid = await _run_mission(c, w)
        assert (await c.post(f"/api/v1/drone-patrols/{sid}/cancel", headers=w["h_op"], json={})).status_code == 202
        r = await c.post(f"/api/v1/drone-edge/sessions/{sid}/claim", headers={"X-Gateway-Key": w["key"]})
        assert r.status_code == 409 and "cancel" in r.json()["detail"]
        await runner.run_tick(AsyncSessionLocal, runner.ListPublisher())
    assert (await _session(sid))["status"] == "CANCELLED", "an unclaimed edge session is cancelled centrally"


@pytest.mark.asyncio
async def test_a_session_its_gateway_never_picks_up_is_recorded_as_missed():
    w = await _world()
    async with _client() as c:
        sid = await _run_mission(c, w)
    await runner.run_tick(AsyncSessionLocal, runner.ListPublisher(), now=_now() + timedelta(minutes=11))
    s = await _session(sid)
    assert s["status"] == "MISSED" and s["failure_code"] == "EDGE_NOT_CLAIMED"
    assert "Depot Edge" in s["failure_reason"]
    assert await _sql("SELECT 1 FROM alerts WHERE alert_code = 'drone.mission_missed' "
                      "AND message_params->>'session_id' = :s", {"s": sid})


# ─── C. Flight updates ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_whole_flight_reported_by_the_gateway_is_recorded_like_a_central_one():
    w = await _world()
    async with _client() as c:
        s = await _claimed(c, w)
        updates = await SimFlight(s).to_the_end()
        r = await _sync(c, w["key"], updates=updates)
    body = r.json()
    assert r.status_code == 200 and body["updates"]["accepted"] == len(updates), body
    done = await _session(s["id"])
    assert done["status"] == "COMPLETED" and done["edge_seq"] == len(updates)
    assert done["launched_at"] and done["landed_at"] and done["provider_mission_ref"].startswith("sim-")
    wps = await _sql("SELECT status FROM drone_session_waypoints WHERE session_id = :s ORDER BY sequence",
                     {"s": uuid.UUID(s["id"])})
    assert [x["status"] for x in wps] == ["OBSERVED"] * 3
    samples = sum(len(u["update"]["samples"]) for u in updates)
    n = await _sql("SELECT count(*) AS n FROM drone_telemetry WHERE session_id = :s", {"s": uuid.UUID(s["id"])})
    assert n[0]["n"] == samples
    drone = (await _sql("SELECT status, last_flight_at FROM drones WHERE id = :d", {"d": w["drone"]}))[0]
    assert drone["status"] in ("READY", "CHARGING") and drone["last_flight_at"]


@pytest.mark.asyncio
async def test_resent_updates_are_ignored_and_a_resent_batch_gets_its_first_answer():
    w = await _world()
    async with _client() as c:
        s = await _claimed(c, w)
        f = SimFlight(s, speed=5)        # slow enough to still be flying at update 3
        first = [await f.next(), await f.next()]
        batch = _batch(updates=first)
        h = {"X-Gateway-Key": w["key"]}
        a1 = (await c.post("/api/v1/drone-edge/sync", json=batch, headers=h)).json()
        a2 = (await c.post("/api/v1/drone-edge/sync", json=batch, headers=h)).json()
        # The same updates again, in a new batch, plus one new one.
        third = await f.next()
        a3 = (await _sync(c, w["key"], updates=first + [third])).json()
    assert a1["updates"]["accepted"] == 2 and a1["duplicate_batch"] is False
    assert a2["duplicate_batch"] is True and a2["updates"] == a1["updates"]
    assert a3["updates"]["accepted"] == 1 and a3["updates"]["duplicates"] == 2
    samples = sum(len(u["update"]["samples"]) for u in first + [third])
    n = (await _sql("SELECT count(*) AS n FROM drone_telemetry WHERE session_id = :s",
                    {"s": uuid.UUID(s["id"])}))[0]["n"]
    assert n == samples, "a resent update must not add telemetry"

    receipts = await _sql("SELECT count(*) AS n FROM drone_sync_receipts WHERE gateway_id = :g", {"g": w["gw"]})
    assert receipts[0]["n"] == 2, "a resent batch must not be recorded twice"
    assert (await _session(s["id"]))["edge_seq"] == 3


@pytest.mark.asyncio
async def test_one_refused_item_does_not_stop_the_rest_of_the_batch():
    w = await _world()
    async with _client() as c:
        s = await _claimed(c, w)
        unclaimed = await _run_mission(c, await _world())            # someone else's session entirely
        good = await SimFlight(s).next()
        stray = dict(good, session_id=unclaimed)
        r = await _sync(c, w["key"], updates=[stray, good])
    body = r.json()
    assert body["updates"]["accepted"] == 1
    assert body["updates"]["rejected"][0]["session_id"] == unclaimed
    assert "not flown by this gateway" in body["updates"]["rejected"][0]["reason"]
    assert (await _session(s["id"]))["edge_seq"] == 1


@pytest.mark.asyncio
async def test_a_flight_given_up_on_while_the_gateway_was_silent_is_corrected_when_it_reports():
    w = await _world()
    async with _client() as c:
        s = await _claimed(c, w)
        f = SimFlight(s, speed=5)
        early = [await f.next()]
        assert (await _sync(c, w["key"], updates=early)).json()["updates"]["accepted"] == 1
        # An hour of silence: the runner gives up on the flight.
        await runner.run_tick(AsyncSessionLocal, runner.ListPublisher(), now=_now() + timedelta(minutes=61))
        gave_up = await _session(s["id"])
        assert gave_up["status"] == "FAILED" and gave_up["failure_code"] == "EDGE_UNREACHABLE"
        # The gateway comes back with the rest of the flight.
        rest = await f.to_the_end()
        body = (await _sync(c, w["key"], updates=rest)).json()
    assert body["updates"]["accepted"] == len(rest), body
    assert body["corrected_sessions"] == [s["id"]]
    real = await _session(s["id"])
    assert real["status"] == "COMPLETED" and real["failure_code"] is None and real["failure_reason"] is None
    wps = await _sql("SELECT status FROM drone_session_waypoints WHERE session_id = :s", {"s": uuid.UUID(s["id"])})
    assert {x["status"] for x in wps} == {"OBSERVED"}


# ─── D. Commands relayed to the gateway ──────────────────────────────────────

@pytest.mark.asyncio
async def test_an_abort_for_a_gateway_flight_is_handed_to_the_gateway_and_closed_by_its_report():
    w = await _world()
    async with _client() as c:
        s = await _claimed(c, w)
        r = await c.post(f"/api/v1/drone-patrols/{s['id']}/abort", headers=w["h_op"], json={"reason": "intruder"})
        assert r.status_code == 202, r.text
        cid = r.json()["command"]["id"]
        await runner.run_tick(AsyncSessionLocal, runner.ListPublisher())
        assert (await _sql("SELECT status FROM drone_session_commands WHERE id = :i", {"i": uuid.UUID(cid)}))[0][
            "status"] == "PENDING", "the central runner carried out a command meant for the gateway"
        a = (await _sync(c, w["key"])).json()["assignment"]
        assert [x["id"] for x in a["commands"]] == [cid] and a["commands"][0]["reason"] == "intruder"
        done = {"command_id": cid, "status": "DONE", "result": "Aborted; the drone is returning home.",
                "at": _now().isoformat()}
        first = (await _sync(c, w["key"], commands=[done])).json()
        again = (await _sync(c, w["key"], commands=[done])).json()
    row = (await _sql("SELECT status, result, delivered_at, processed_at FROM drone_session_commands "
                      "WHERE id = :i", {"i": uuid.UUID(cid)}))[0]
    assert first["commands"]["accepted"] == 1 and again["commands"]["duplicates"] == 1
    assert row["status"] == "DONE" and row["delivered_at"] and row["processed_at"]


@pytest.mark.asyncio
async def test_an_abort_the_gateway_could_not_deliver_raises_a_critical_alert():
    w = await _world()
    async with _client() as c:
        s = await _claimed(c, w)
        cid = (await c.post(f"/api/v1/drone-patrols/{s['id']}/abort", headers=w["h_op"], json={})).json()["command"]["id"]
        await _sync(c, w["key"], commands=[{"command_id": cid, "status": "FAILED", "result": "link timeout",
                                            "at": _now().isoformat()}])
    alert = await _sql("SELECT severity, message FROM alerts WHERE alert_code = 'drone.command_failed' "
                       "AND message_params->>'session_id' = :s", {"s": s["id"]})
    assert alert and alert[0]["severity"] == "critical" and "link timeout" in alert[0]["message"]


# ─── E. Health, events and files ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_drone_health_from_the_gateway_is_its_heartbeat_and_older_news_is_ignored():
    w = await _world()
    other = await _world()
    t = _now()
    h = lambda at, bat, drone=w["drone"]: {"drone_id": str(drone), "observed_at": at.isoformat(),  # noqa: E731
                                           "battery_level": bat, "gps_status": "OK", "communication_status": "OK",
                                           "camera_status": "OK", "storage_status": "OK", "status_hint": "READY"}
    async with _client() as c:
        a = (await _sync(c, w["key"], health=[h(t + timedelta(seconds=5), 81)])).json()
        b = (await _sync(c, w["key"], health=[h(t + timedelta(seconds=1), 40)])).json()
        x = (await _sync(c, w["key"], health=[h(t, 50, other["drone"])])).json()
    d = (await _sql("SELECT battery_level, last_heartbeat_at FROM drones WHERE id = :d", {"d": w["drone"]}))[0]
    assert a["health"]["accepted"] == 1 and b["health"]["duplicates"] == 1
    assert d["battery_level"] == 81 and d["last_heartbeat_at"] == t + timedelta(seconds=5)
    assert x["health"]["rejected"][0]["reason"] == "That drone is not served by this gateway."


@pytest.mark.asyncio
async def test_an_event_recorded_at_the_site_arrives_once_however_often_it_is_sent():
    w = await _world()
    async with _client() as c:
        s = await _claimed(c, w)
        ev = {"client_ref": str(uuid.uuid4()), "drone_id": str(w["drone"]), "session_id": s["id"],
              "module_type": "intrusion", "detected_at": _now().isoformat(), "ai_confidence": 0.91,
              "drone_latitude": 1.3005, "drone_longitude": 103.8005, "waypoint_sequence": 2}
        first = (await _sync(c, w["key"], events=[ev])).json()
        again = (await _sync(c, w["key"], events=[ev])).json()
        bad = await _sync(c, w["key"], events=[dict(ev, client_ref=str(uuid.uuid4()), module_type="teleport")])
        listed = (await c.get(f"/api/v1/drone-events?session_id={s['id']}", headers=w["h_op"])).json()
    assert first["events"]["accepted"] == 1 and again["events"]["duplicates"] == 1
    assert bad.status_code == 422, "an unknown AI module is not an event"
    assert listed["total"] == 1 and listed["items"][0]["module_type"] == "intrusion"
    assert (await _session(s["id"]))["event_count"] == 1


async def _event_with_snapshot(c, w, s) -> tuple[str, str]:
    ev, media = str(uuid.uuid4()), str(uuid.uuid4())
    body = (await _sync(
        c, w["key"],
        events=[{"client_ref": ev, "drone_id": str(w["drone"]), "session_id": s["id"], "module_type": "intrusion",
                 "detected_at": _now().isoformat()}],
        media=[{"client_ref": media, "event_client_ref": ev, "media_kind": "SNAPSHOT",
                "captured_at": _now().isoformat(), "checksum_sha256": hashlib.sha256(JPEG).hexdigest(),
                "size_bytes": len(JPEG)}])).json()
    assert body["media"]["accepted"] == 1, body
    return ev, media


@pytest.mark.asyncio
async def test_the_recording_policy_decides_which_files_the_centre_asks_for():
    w = await _world()
    async with _client() as c:
        s = await _claimed(c, w)
        _, event_file = await _event_with_snapshot(c, w, s)
        clip = str(uuid.uuid4())
        await _sync(c, w["key"], media=[{"client_ref": clip, "session_id": s["id"], "media_kind": "CLIP",
                                         "captured_at": _now().isoformat(), "checksum_sha256": "a" * 64,
                                         "size_bytes": 10_000_000, "duration_seconds": 600}])
        default = (await _sync(c, w["key"])).json()["assignment"]["uploads_wanted"]
        await _sql("INSERT INTO recording_policies (tenant_id, site_id, sync_mode) VALUES (:t,:s,'central')",
                   {"t": w["tenant"], "s": w["site"]})
        clip2 = str(uuid.uuid4())
        central = (await _sync(c, w["key"], media=[{
            "client_ref": clip2, "session_id": s["id"], "media_kind": "CLIP", "captured_at": _now().isoformat(),
            "checksum_sha256": "b" * 64, "size_bytes": 5}])).json()
    states = {r["client_ref"]: r["sync_state"] for r in await _sql(
        "SELECT client_ref::text, sync_state FROM drone_event_media WHERE session_id = :s", {"s": uuid.UUID(s["id"])})}
    assert states[event_file] == "pending", "by default, event media goes to the centre"
    assert states[clip] == "not_required", "by default, the full flight recording stays at the site"
    assert default == [event_file]
    assert states[clip2] == "pending" and clip2 in central["media_upload_requested"], "policy 'central' ships all"


@pytest.mark.asyncio
async def test_a_file_is_stored_centrally_only_if_it_is_exactly_the_file_described():
    w = await _world()
    stored = None
    try:
        async with _client() as c:
            s = await _claimed(c, w)
            _, ref = await _event_with_snapshot(c, w, s)
            url = f"/api/v1/drone-edge/media/{ref}"
            hk = {"X-Gateway-Key": w["key"]}
            ok_sum = hashlib.sha256(JPEG).hexdigest()
            wrong_sum = await c.put(url, content=JPEG, headers=hk | {"X-Checksum-Sha256": "0" * 64})
            corrupt = await c.put(url, content=JPEG[:-1] + b"!", headers=hk | {"X-Checksum-Sha256": ok_sum})
            other_gw = await c.put(url, content=JPEG, headers={"X-Gateway-Key": w["key2"], "X-Checksum-Sha256": ok_sum})
            good = await c.put(url, content=JPEG, headers=hk | {"X-Checksum-Sha256": ok_sum})
            again = await c.put(url, content=JPEG, headers=hk | {"X-Checksum-Sha256": ok_sum})
            row = (await _sql("SELECT id, storage_path, storage_location, sync_state FROM drone_event_media "
                              "WHERE client_ref = :r", {"r": uuid.UUID(ref)}))[0]
            stored = Path(settings.EVIDENCE_ROOT) / row["storage_path"]
            got = await c.get(f"/api/v1/drone-media/{row['id']}/file", headers=w["h_op"])
            wanted = (await _sync(c, w["key"])).json()["assignment"]["uploads_wanted"]
        assert wrong_sum.status_code == 409 and corrupt.status_code == 422 and other_gw.status_code == 404
        assert good.status_code == 200 and good.json()["stored"] is True, good.text
        assert again.json()["duplicate"] is True
        assert row["storage_location"] == "both" and row["sync_state"] == "synced"
        assert stored.read_bytes() == JPEG and got.status_code == 200 and got.content == JPEG
        assert ref not in wanted, "an uploaded file is not asked for again"
    finally:
        if stored is not None and stored.exists():
            stored.unlink()


@pytest.mark.asyncio
async def test_a_file_that_is_not_the_kind_it_claims_or_was_not_asked_for_is_refused():
    w = await _world()
    fake = b"MZ\x90\x00 not a picture at all"
    fake_sum = hashlib.sha256(fake).hexdigest()
    async with _client() as c:
        s = await _claimed(c, w)
        ev, ref, clip = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        await _sync(c, w["key"],
                    events=[{"client_ref": ev, "drone_id": str(w["drone"]), "session_id": s["id"],
                             "module_type": "weapon", "detected_at": _now().isoformat()}],
                    media=[{"client_ref": ref, "event_client_ref": ev, "media_kind": "SNAPSHOT",
                            "captured_at": _now().isoformat(), "checksum_sha256": fake_sum, "size_bytes": len(fake)},
                           {"client_ref": clip, "session_id": s["id"], "media_kind": "CLIP",
                            "captured_at": _now().isoformat(), "checksum_sha256": fake_sum, "size_bytes": len(fake)}])
        hk = {"X-Gateway-Key": w["key"], "X-Checksum-Sha256": fake_sum}
        disguised = await c.put(f"/api/v1/drone-edge/media/{ref}", content=fake, headers=hk)
        unasked = await c.put(f"/api/v1/drone-edge/media/{clip}", content=fake, headers=hk)
    assert disguised.status_code == 415
    assert unasked.status_code == 409 and "did not ask" in unasked.json()["detail"]
    row = (await _sql("SELECT sync_state FROM drone_event_media WHERE client_ref = :r", {"r": uuid.UUID(ref)}))[0]
    assert row["sync_state"] == "pending", "a refused upload leaves the file waiting for a good one"


@pytest.mark.asyncio
async def test_a_file_kept_at_the_site_says_where_it_is_instead_of_a_broken_link():
    w = await _world()
    async with _client() as c:
        s = await _claimed(c, w)
        _, ref = await _event_with_snapshot(c, w, s)
        mid = (await _sql("SELECT id FROM drone_event_media WHERE client_ref = :r", {"r": uuid.UUID(ref)}))[0]["id"]
        r = await c.get(f"/api/v1/drone-media/{mid}/file", headers=w["h_op"])
    assert r.status_code == 409 and "Depot Edge" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_scheduled_policy_asks_for_files_only_inside_its_window():
    w = await _world()
    async with _client() as c:
        s = await _claimed(c, w)
        await _event_with_snapshot(c, w, s)
        await _sql("UPDATE tenants SET timezone = 'Asia/Singapore' WHERE id = :t", {"t": w["tenant"]})
        local = datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Asia/Singapore"))
        start = (local + timedelta(hours=2)).time().replace(microsecond=0)
        end = (local + timedelta(hours=3)).time().replace(microsecond=0)
        await _sql("INSERT INTO recording_policies (tenant_id, site_id, sync_mode, sync_window_start, sync_window_end) "
                   "VALUES (:t,:s,'scheduled',:a,:b)", {"t": w["tenant"], "s": w["site"], "a": start, "b": end})
        a = (await _sync(c, w["key"])).json()["assignment"]
    assert a["uploads_wanted"] == [] and a["policy"]["upload_window_open"] is False


# ─── F. The gateway's own health ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_silent_gateway_raises_one_alert_for_the_site_not_one_per_drone():
    w = await _world()
    async with _client() as c:
        await _sync(c, w["key"])
        later = _now() + timedelta(minutes=5)
        first = await runner.run_health_tick(AsyncSessionLocal, runner.ListPublisher(), now=later)
        second = await runner.run_health_tick(AsyncSessionLocal, runner.ListPublisher(),
                                              now=later + timedelta(minutes=1))
        gw_off = (await _sql("SELECT status FROM drone_edge_gateways WHERE id = :g", {"g": w["gw"]}))[0]["status"]
        drone = (await _sql("SELECT status FROM drones WHERE id = :d", {"d": w["drone"]}))[0]["status"]
        await _sync(c, w["key"])
    alerts = await _sql("SELECT alert_code FROM alerts WHERE tenant_id = :t", {"t": w["tenant"]})
    assert first["gateways_offline"] >= 1 and gw_off == "OFFLINE"
    assert drone == "COMMUNICATION_LOST"
    assert [a["alert_code"] for a in alerts] == ["drone.gateway_offline"], \
        "one broken site link is one alert, not one per drone behind it"
    back = dict((await _sql("SELECT status, offline_alerted_at FROM drone_edge_gateways WHERE id = :g",
                            {"g": w["gw"]}))[0])
    assert back == {"status": "ONLINE", "offline_alerted_at": None}
    assert second["gateways_offline"] == 0, "a gateway already offline is not alerted again"


@pytest.mark.asyncio
async def test_a_gateway_with_a_wrong_clock_is_degraded_and_says_why():
    w = await _world()
    async with _client() as c:
        r = await c.post("/api/v1/drone-edge/sync", headers={"X-Gateway-Key": w["key"]},
                         json=_batch(sent_at=(_now() + timedelta(minutes=3)).isoformat()))
        gw = next(g for g in (await c.get("/api/v1/drones/edge-gateways", headers=w["h_admin"])).json()
                  if g["id"] == str(w["gw"]))
    assert r.status_code == 200
    assert gw["status"] == "DEGRADED" and "clock" in gw["health"]["problems"][0]
    assert abs(float(gw["clock_offset_s"]) - 180) < 5


@pytest.mark.asyncio
async def test_sync_history_lists_what_was_refused_and_why():
    w = await _world()
    async with _client() as c:
        await _sync(c, w["key"], health=[{"drone_id": str(uuid.uuid4()), "observed_at": _now().isoformat()}])
        rows = (await c.get(f"/api/v1/drones/edge-gateways/{w['gw']}/sync-receipts", headers=w["h_admin"])).json()
    assert rows[0]["item_count"] == 1 and rows[0]["rejected"] == 1
    assert rows[0]["rejections"][0]["kind"] == "health" and "not served" in rows[0]["rejections"][0]["reason"]


@pytest.mark.asyncio
async def test_a_gateway_flying_a_mission_cannot_be_deleted():
    w = await _world()
    async with _client() as c:
        await _claimed(c, w)
        await _sql("UPDATE drones SET edge_gateway_id = NULL WHERE id = :d", {"d": w["drone"]})
        r = await c.delete(f"/api/v1/drones/edge-gateways/{w['gw']}", headers=w["h_admin"])
    assert r.status_code == 409 and "flying" in r.json()["detail"]
