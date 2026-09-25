"""Drone patrol, phase 5: the site edge gateway end to end — the real agent, its
real local store, talking to the real API over a link these tests can cut.

  A — A flight flown from the site
  B — Through an outage: keep flying, keep everything, catch up once
  C — Restarts and lost answers
  D — Commands, events and files
  E — A malformed item cannot block the rest
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from app.main import app
from app.core.config import settings
from app.drone_edge.agent import EdgeAgent
from app.drone_edge.central import CentralClient
from app.drone_edge.store import EdgeStore
from tests.test_drone_edge_sync import JPEG, _client, _now, _run_mission, _session, _sql, _world

TERMINAL = ("COMPLETED", "FAILED", "ABORTED", "CANCELLED", "BLOCKED", "MISSED")


class Link(httpx.AsyncBaseTransport):
    """The site's link to the centre: up, down, or losing the next answer."""

    def __init__(self):
        self.inner = ASGITransport(app)
        self.up = True
        self.lose_next_answer = False
        self.sent: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("the site link is down", request=request)
        resp = await self.inner.handle_async_request(request)
        self.sent.append(request.url.path)
        if self.lose_next_answer and request.url.path.endswith("/sync"):
            self.lose_next_answer = False
            await resp.aread()
            raise httpx.ReadError("the answer was lost on the way back", request=request)
        return resp


def _agent(w: dict, where: Path, link: Link, **kw) -> EdgeAgent:
    return EdgeAgent(EdgeStore(where / "edge.sqlite3"), CentralClient("http://test", w["key"], transport=link),
                     media_dir=where / "media", tenant_id=str(w["tenant"]),
                     health_every_s=kw.pop("health_every_s", 3600), **kw)


async def _until(agent: EdgeAgent, t, done, *, step_s: float = 2.0, max_cycles: int = 60):
    """Cycle the agent, advancing its clock, until `done()` is true."""
    for _ in range(max_cycles):
        t += timedelta(seconds=step_s)
        await agent.cycle(t)
        if await done():
            return t
    raise AssertionError("the agent never got there")


def _status_is(sid, *statuses):
    async def check():
        return (await _session(sid))["status"] in statuses
    return check


def _drained(agent: EdgeAgent, sid, *statuses):
    async def check():
        return agent.store.depth()[0] == 0 and (await _session(sid))["status"] in statuses
    return check


# ─── A. A flight flown from the site ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_gateway_claims_flies_and_reports_a_whole_patrol(tmp_path):
    w = await _world(speed=50)
    link = Link()
    agent = _agent(w, tmp_path, link)
    async with _client() as c:
        sid = await _run_mission(c, w)
    await _until(agent, _now(), _drained(agent, sid, *TERMINAL))

    s = await _session(sid)
    local = agent.store.session(sid)
    assert s["status"] == "COMPLETED", s
    assert s["edge_seq"] == local.seq and local.outcome == "COMPLETED"
    assert s["launched_at"] and s["landed_at"] and s["edge_claimed_at"]
    wps = await _sql("SELECT status FROM drone_session_waypoints WHERE session_id = :s", {"s": uuid.UUID(sid)})
    assert {x["status"] for x in wps} == {"OBSERVED"}
    assert (await _sql("SELECT count(*) AS n FROM drone_telemetry WHERE session_id = :s",
                       {"s": uuid.UUID(sid)}))[0]["n"] > 0
    assert "/api/v1/drone-edge/sessions/%s/claim" % sid in link.sent
    assert agent.store.rejected() == []


@pytest.mark.asyncio
async def test_the_gateways_drone_health_reaches_the_centre(tmp_path):
    w = await _world()
    await _sql("UPDATE drones SET battery_level = 60, status = 'CHARGING' WHERE id = :d", {"d": w["drone"]})
    agent = _agent(w, tmp_path, Link(), health_every_s=1)
    t = _now()
    for _ in range(3):
        t += timedelta(seconds=2)
        await agent.cycle(t)
    d = (await _sql("SELECT battery_level, last_heartbeat_at, communication_status FROM drones WHERE id = :d",
                    {"d": w["drone"]}))[0]
    assert d["communication_status"] == "OK" and d["last_heartbeat_at"] >= t - timedelta(seconds=4)
    assert d["battery_level"] >= 60, "a docked simulated drone charges, it does not drain"


# ─── B. Through an outage ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_flight_continues_through_an_outage_and_is_reported_in_full_afterwards(tmp_path):
    w = await _world(speed=5)
    link = Link()
    agent = _agent(w, tmp_path, link)
    async with _client() as c:
        sid = await _run_mission(c, w)
    t = await _until(agent, _now(), _status_is(sid, "ACTIVE"))

    link.up = False
    async def landed_here():
        return agent.store.session(sid).outcome is not None
    t = await _until(agent, t, landed_here, max_cycles=80)
    during = await _session(sid)
    buffered, _ = agent.store.depth()
    assert agent.online is False
    assert during["status"] not in TERMINAL, "the centre cannot know the flight ended — the link was down"
    assert buffered > 0

    link.up = True
    await _until(agent, t, _drained(agent, sid, *TERMINAL))
    s = await _session(sid)
    assert s["status"] == "COMPLETED" and s["edge_seq"] == agent.store.session(sid).seq
    wps = await _sql("SELECT status FROM drone_session_waypoints WHERE session_id = :s", {"s": uuid.UUID(sid)})
    assert {x["status"] for x in wps} == {"OBSERVED"}
    totals = (await _sql("SELECT sum(accepted) AS a, sum(duplicates) AS d, sum(rejected) AS r "
                         "FROM drone_sync_receipts WHERE gateway_id = :g", {"g": w["gw"]}))[0]
    assert totals["r"] == 0 and totals["d"] == 0, totals


@pytest.mark.asyncio
async def test_no_new_flight_starts_without_the_centre(tmp_path):
    w = await _world()
    link = Link()
    link.up = False
    agent = _agent(w, tmp_path, link)
    async with _client() as c:
        sid = await _run_mission(c, w)
    t = _now()
    for _ in range(4):
        t += timedelta(seconds=2)
        await agent.cycle(t)
    assert (await _session(sid))["status"] == "READY" and agent.store.known_session_ids() == []
    link.up = True
    await _until(agent, t, _status_is(sid, "LAUNCHING", "ACTIVE", "RETURNING", "COMPLETED"))


# ─── C. Restarts and lost answers ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_gateway_restarted_mid_flight_picks_the_flight_up_where_it_was(tmp_path):
    w = await _world(speed=5)
    link = Link()
    first = _agent(w, tmp_path, link)
    async with _client() as c:
        sid = await _run_mission(c, w)
    t = await _until(first, _now(), _status_is(sid, "ACTIVE"))
    t = await _until(first, t, _status_is(sid, "ACTIVE"), max_cycles=2)
    seq_before = first.store.session(sid).seq
    first.store.close()

    second = _agent(w, tmp_path, link)        # same disk, new process
    assert second.store.session(sid).seq == seq_before
    await _until(second, t, _drained(second, sid, *TERMINAL))
    s = await _session(sid)
    assert s["status"] == "COMPLETED" and s["edge_seq"] == second.store.session(sid).seq
    assert second.store.rejected() == []


@pytest.mark.asyncio
async def test_a_batch_whose_answer_was_lost_is_resent_as_the_same_batch(tmp_path):
    w = await _world(speed=5)
    link = Link()
    agent = _agent(w, tmp_path, link)
    async with _client() as c:
        sid = await _run_mission(c, w)
    t = await _until(agent, _now(), _status_is(sid, "ACTIVE"))
    t += timedelta(seconds=2)
    await agent.cycle(t)                      # queues more telemetry
    open_before = agent.store.get_meta("open_batch")
    link.lose_next_answer = True
    t += timedelta(seconds=2)
    await agent.cycle(t)                      # the centre applies it; the answer never arrives
    lost = agent.store.get_meta("open_batch")
    assert agent.online is False and lost and lost != open_before
    t += timedelta(seconds=2)
    await agent.cycle(t)                      # sent again, same id, same items
    receipts = await _sql("SELECT count(*) AS n FROM drone_sync_receipts WHERE batch_id = :b",
                          {"b": uuid.UUID(lost["batch_id"])})
    assert receipts[0]["n"] == 1
    assert agent.store.get_meta("open_batch") is None or agent.store.get_meta("open_batch")["batch_id"] != lost["batch_id"]
    await _until(agent, t, _drained(agent, sid, *TERMINAL))
    totals = (await _sql("SELECT sum(rejected) AS r FROM drone_sync_receipts WHERE gateway_id = :g", {"g": w["gw"]}))[0]
    assert totals["r"] == 0


# ─── D. Commands, events and files ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_operators_abort_reaches_the_drone_through_its_gateway(tmp_path):
    w = await _world(speed=2)
    agent = _agent(w, tmp_path, Link())
    async with _client() as c:
        sid = await _run_mission(c, w)
        t = await _until(agent, _now(), _status_is(sid, "ACTIVE"))
        r = await c.post(f"/api/v1/drone-patrols/{sid}/abort", headers=w["h_op"], json={"reason": "weather"})
        cid = uuid.UUID(r.json()["command"]["id"])
    await _until(agent, t, _drained(agent, sid, *TERMINAL))
    s = await _session(sid)
    cmd = (await _sql("SELECT status, result, delivered_at FROM drone_session_commands WHERE id = :i", {"i": cid}))[0]
    assert s["status"] == "ABORTED"
    assert cmd["status"] == "DONE" and cmd["delivered_at"] and "returning home" in cmd["result"]


@pytest.mark.asyncio
async def test_an_event_and_its_snapshot_recorded_offline_arrive_once_and_the_file_is_uploaded(tmp_path):
    w = await _world(speed=2)
    link = Link()
    agent = _agent(w, tmp_path, link)
    async with _client() as c:
        sid = await _run_mission(c, w)
    t = await _until(agent, _now(), _status_is(sid, "ACTIVE"))

    link.up = False
    ev = agent.report_event(drone_id=str(w["drone"]), session_id=sid, module_type="intrusion", detected_at=t,
                            ai_confidence=0.88, drone_latitude=1.3005, drone_longitude=103.8005, now=t)
    ref = agent.record_media(JPEG, media_kind="SNAPSHOT", captured_at=t, event_client_ref=ev, session_id=sid, now=t)
    for _ in range(3):
        t += timedelta(seconds=2)
        await agent.cycle(t)
    assert await _sql("SELECT 1 FROM drone_events WHERE client_ref = :r", {"r": uuid.UUID(ev)}) == []

    link.up = True
    stored = None
    try:
        async def uploaded():
            rows = await _sql("SELECT sync_state, storage_path FROM drone_event_media WHERE client_ref = :r",
                              {"r": uuid.UUID(ref)})
            return bool(rows) and rows[0]["sync_state"] == "synced"
        await _until(agent, t, uploaded, max_cycles=10)
        row = (await _sql("SELECT storage_path FROM drone_event_media WHERE client_ref = :r", {"r": uuid.UUID(ref)}))[0]
        stored = Path(settings.EVIDENCE_ROOT) / row["storage_path"]
        assert stored.read_bytes() == JPEG
        events = await _sql("SELECT count(*) AS n FROM drone_events WHERE session_id = :s", {"s": uuid.UUID(sid)})
        assert events[0]["n"] == 1
        assert agent.store.media(ref)["uploaded_at"] is not None
    finally:
        if stored is not None and stored.exists():
            stored.unlink()


# ─── E. A malformed item cannot block the rest ───────────────────────────────

@pytest.mark.asyncio
async def test_a_malformed_item_is_set_aside_and_everything_else_is_delivered(tmp_path):
    w = await _world()
    agent = _agent(w, tmp_path, Link())
    t = _now()
    good = [agent.report_event(drone_id=str(w["drone"]), module_type="fire_smoke", detected_at=t, now=t)
            for _ in range(2)]
    # An item the centre can never accept: a module this system does not have.
    bad = str(uuid.uuid4())
    agent.store.put_event({"client_ref": bad, "drone_id": str(w["drone"]), "session_id": None,
                           "module_type": "teleport", "detected_at": t.isoformat()}, t)
    for _ in range(8):
        t += timedelta(seconds=2)
        await agent.cycle(t)
        if agent.store.depth()[0] == 0:
            break
    assert agent.store.depth()[0] == 0
    arrived = await _sql("SELECT client_ref::text AS r FROM drone_events WHERE drone_id = :d", {"d": w["drone"]})
    assert {r["r"] for r in arrived} == set(good)
    rejected = agent.store.rejected()
    assert [r["ref"] for r in rejected] == [bad] and "Invalid" in rejected[0]["reason"]
