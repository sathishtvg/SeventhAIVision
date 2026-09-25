"""Drone patrol, phase 8: event → risk → alert → incident → officer → guard →
resolution, through the platform's own incident system and dispatch.

  A — Incidents: when the rules call for one, in `incidents`, never twice
  B — The officer: opening one by hand, the command-centre card, the alert
  C — The guard: nearest free first, dispatched through the existing dispatch
  D — Resolution flowing back to the drone event
  E — Verify with drone: a real simulated flight holds, looks again, resumes
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import AsyncSessionLocal
from app.services import drone_runner as runner
from tests.test_drone_ai_pipeline import (
    OVER_ZONE, _ai, _detect, _events, _flying, _last, _world,
)
from tests.test_drone_cctv_pipeline import _cam_detect, _cameras
from tests.test_drone_edge_sync import _client, _run, _session, _sql

M = 1 / 111_320


async def _incident(iid) -> dict:
    return dict((await _sql("SELECT * FROM incidents WHERE id = :i", {"i": iid}))[0])


async def _tenant_incidents(w) -> list[dict]:
    return [dict(r) for r in await _sql("SELECT * FROM incidents WHERE tenant_id = :t ORDER BY created_at",
                                        {"t": w["tenant"]})]


async def _sustained(w, at, module="intrusion", n=3, **kw):
    for s in range(n):
        await _detect(w, at + timedelta(seconds=2 * s), module, **kw)


# ─── A. Incidents ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_verified_event_at_the_profiles_incident_level_opens_one_incident_in_the_platforms_system():
    w = await _world(rules=[("intrusion", "low", None, "HIGH")])
    at = _last(2, 17)
    sid = await _flying(w, at)
    await _sustained(w, at)
    await _ai(at + timedelta(seconds=10))
    await _ai(at + timedelta(seconds=12))
    [e] = await _events(sid)
    incidents = await _tenant_incidents(w)
    assert e["risk_level"] == "HIGH" and len(incidents) == 1, "no incident, or two"
    inc = incidents[0]
    assert e["incident_id"] == inc["id"] and e["status"] == "ESCALATED"
    assert inc["severity"] == "high" and inc["status"] == "open" and inc["is_auto_created"] is True
    assert inc["camera_id"] == w["camera"] and inc["alert_id"] == e["alert_id"]
    assert inc["alert_code"] == "drone.intrusion" and inc["title"].startswith("Drone: Possible unauthorised person")
    p = inc["message_params"]
    assert p["drone_event_id"] == str(e["id"]) and p["risk_level"] == "HIGH" and p["incident_ref"].startswith("INC-")
    assert "the drone's position" in inc["description"] and "Loading Bay" in inc["description"]
    assert (await _session(sid))["incident_count"] == 1
    async with _client() as c:
        listed = (await c.get("/api/v1/incidents", headers=w["h_admin"])).json()
        timeline = (await c.get(f"/api/v1/incidents/{inc['id']}/timeline", headers=w["h_admin"])).json()
    mine = next(i for i in listed["items"] if i["id"] == str(inc["id"]))
    assert mine["site_name"] == "Depot", "the drone's camera gives the incident its site in the existing list"
    assert timeline[0]["type"] == "status_change" and timeline[0]["to_status"] == "open"


@pytest.mark.asyncio
async def test_with_no_profile_the_default_incident_level_is_high_as_for_every_rule():
    high = await _world()                                  # intrusion, restricted zone, night: HIGH
    medium = await _world(zone_type="NORMAL")              # missing PPE by day in a normal zone: MEDIUM
    at, day = _last(2, 17), _last(14)
    high["sid"] = await _flying(high, at)
    await _sustained(high, at)
    medium["sid"] = await _flying(medium, day)
    await _sustained(medium, day, module="ppe")
    await _ai(at + timedelta(seconds=10))
    await _ai(day + timedelta(seconds=10))
    [h], [m] = await _events(high["sid"]), await _events(medium["sid"])
    assert h["risk_level"] == "HIGH" and h["incident_id"] is not None
    assert (await _incident(h["incident_id"]))["severity"] == "high"
    assert m["risk_level"] == "MEDIUM" and m["alert_id"] is not None and m["incident_id"] is None


@pytest.mark.asyncio
async def test_a_zone_whose_policy_is_incident_makes_every_alertable_event_an_incident():
    w = await _world(zone_extra={"alert_policy": "INCIDENT"})
    at = _last(14)
    sid = await _flying(w, at)
    await _sustained(w, at)
    await _ai(at + timedelta(seconds=10))
    [e] = await _events(sid)
    assert e["risk_level"] in ("MEDIUM", "HIGH") and e["incident_id"] is not None


@pytest.mark.asyncio
async def test_a_workers_own_incident_is_linked_not_duplicated():
    w = await _world()
    at = _last(14)
    sid = await _flying(w, at)
    det = await _detect(w, at, "weapon", 0.93, weapon="handgun", worker_alert="critical")
    worker_alert = (await _sql("SELECT id FROM alerts WHERE detection_id = :d", {"d": det}))[0]["id"]
    worker_incident = uuid.uuid4()
    await _run([("INSERT INTO incidents (id, tenant_id, alert_id, camera_id, title, severity, is_auto_created) "
                 "VALUES (:i,:t,:a,:c,'Weapon incident (handgun)','critical',TRUE)",
                 {"i": worker_incident, "t": w["tenant"], "a": worker_alert, "c": w["camera"]})])
    await _ai(at + timedelta(seconds=5))
    [e] = await _events(sid)
    assert e["risk_level"] == "CRITICAL" and e["incident_id"] == worker_incident
    assert [i["id"] for i in await _tenant_incidents(w)] == [worker_incident]
    notes = await _sql("SELECT note FROM incident_notes WHERE incident_id = :i", {"i": worker_incident})
    assert notes and "the drone saw it too" in notes[0]["note"]


@pytest.mark.asyncio
async def test_a_rising_risk_raises_the_drone_incidents_severity_and_says_why():
    w = await _world(rules=[("intrusion", "low", None, "HIGH")])
    cams = await _cameras(w)
    at = _last(2, 17)
    sid = await _flying(w, at)
    await _sustained(w, at)
    await _ai(at + timedelta(seconds=10))
    [e] = await _events(sid)
    assert (await _incident(e["incident_id"]))["severity"] == "high"
    # A fixed camera saw the person too: +10, verified, CRITICAL.
    await _cam_detect(w, cams["CAM-27"], at + timedelta(seconds=3), "intrusion")
    await _sql("UPDATE drone_events SET cctv_correlated_at = NULL WHERE id = :e", {"e": e["id"]})
    await _ai(at + timedelta(seconds=14))
    [e] = await _events(sid)
    inc = await _incident(e["incident_id"])
    notes = [n["note"] for n in await _sql("SELECT note FROM incident_notes WHERE incident_id = :i",
                                           {"i": inc["id"]})]
    assert e["risk_level"] == "CRITICAL" and inc["severity"] == "critical"
    assert any("rose to CRITICAL" in n and "CAM-27" in n for n in notes), notes


# ─── B. The officer ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_officer_can_open_an_incident_for_any_event_and_asking_twice_gives_the_same_one():
    w = await _world()
    at = _last(14)
    sid = await _flying(w, at)
    await _detect(w, at, "intrusion")
    await _ai(at + timedelta(seconds=5))
    [e] = await _events(sid)
    assert e["incident_id"] is None
    async with _client() as c:
        first = await c.post(f"/api/v1/drone-events/{e['id']}/incident", headers=w["h_op"],
                             json={"reason": "looks deliberate"})
        again = await c.post(f"/api/v1/drone-events/{e['id']}/incident", headers=w["h_op"])
    assert first.status_code == 200 and first.json()["created"] is True
    assert again.json()["created"] is False and again.json()["incident"]["id"] == first.json()["incident"]["id"]
    inc = await _incident(first.json()["incident"]["id"])
    assert inc["is_auto_created"] is False and "looks deliberate" in inc["description"]


@pytest.mark.asyncio
async def test_the_card_carries_what_the_command_centre_shows_and_the_alert_carries_the_card():
    w = await _world()
    at = _last(2, 17)
    sid = await _flying(w, at)
    await _sustained(w, at, conf=0.94)
    pub = runner.ListPublisher()
    await runner.run_ai_tick(AsyncSessionLocal, pub, now=at + timedelta(seconds=10))
    [e] = await _events(sid)
    async with _client() as c:
        card = (await c.get(f"/api/v1/drone-events/{e['id']}/card", headers=w["h_op"])).json()
    assert card["headline"] == "Possible unauthorised person" and card["site_name"] == "Depot"
    assert card["area"] == "Loading Bay" and card["drone_code"] == "D-01" and card["mission_name"] == "Night Watch"
    assert card["detected_at_site_time"].startswith(at.astimezone().strftime("%Y")) and "+08:00" in card["detected_at_site_time"]
    assert card["ai_confidence"] == pytest.approx(0.94) and card["risk_level"] == "HIGH"
    actions = {a["action"] for a in card["actions"]}
    assert {"acknowledge", "open_incident", "view_drone", "view_cctv", "view_map", "dispatch_guard",
            "escalate", "mark_false_positive", "resolve", "verify_with_drone"} <= actions
    alert = next(p for t, kind, p in pub.events if kind == "alert_created" and p.get("drone_event_id") == str(e["id"]))
    assert alert["drone"]["site_name"] == "Depot" and alert["drone"]["risk_level"] == "HIGH"
    assert alert["drone"]["ai_confidence"] == pytest.approx(0.94)


# ─── C. The guard ────────────────────────────────────────────────────────────

async def _guards(w, at_now: datetime) -> dict:
    """Guards A (checked in 50 m away, then reported 10 m away), B (400 m),
    C (20 m, busy on another incident), D (shift over)."""
    g = {k: uuid.uuid4() for k in "ABCD"}
    near = lambda m: (OVER_ZONE[0] + m * M, OVER_ZONE[1])  # noqa: E731
    stmts = []
    for k in "ABCD":
        stmts.append(("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                      "VALUES (:i,:t,5,:e,'x',:n)", {"i": g[k], "t": w["tenant"], "e": f"g{k}-{g[k].hex[:6]}@ai.test",
                                                     "n": f"Guard {k}"}))
    for k, dist, ended in (("A", 50, False), ("B", 400, False), ("C", 20, False), ("D", 5, True)):
        lat, lng = near(dist)
        stmts.append(("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, "
                      "   actual_start, actual_end, status, check_in_lat, check_in_lon) "
                      "VALUES (:t,:s,:g,:a,:b,:a,:end,:st,:la,:lo)",
                      {"t": w["tenant"], "s": w["site"], "g": g[k], "a": at_now - timedelta(hours=2),
                       "b": at_now + timedelta(hours=6), "end": at_now - timedelta(minutes=5) if ended else None,
                       "st": "completed" if ended else "active", "la": lat, "lo": lng}))
    busy = uuid.uuid4()
    stmts.append(("INSERT INTO incidents (id, tenant_id, title, severity, status, dispatched_guard_id, dispatched_at) "
                  "VALUES (:i,:t,'Elsewhere','low','in_progress',:g,now())", {"i": busy, "t": w["tenant"], "g": g["C"]}))
    lat, lng = near(10)
    stmts.append(("INSERT INTO incident_status_history (tenant_id, incident_id, changed_by_user_id, to_status, "
                  "   latitude, longitude, changed_at) VALUES (:t,:i,:g,'on_scene',:la,:lo,:at)",
                  {"t": w["tenant"], "i": busy, "g": g["A"], "la": lat, "lo": lng,
                   "at": at_now - timedelta(minutes=10)}))
    await _run(stmts)
    return g


@pytest.mark.asyncio
async def test_guards_are_ranked_free_and_nearest_first_with_where_that_position_came_from():
    w = await _world()
    at = _last(14)
    sid = await _flying(w, at)
    await _sustained(w, at)
    await _ai(at + timedelta(seconds=10))
    [e] = await _events(sid)
    g = await _guards(w, datetime.now(timezone.utc))
    async with _client() as c:
        guards = (await c.get(f"/api/v1/drone-events/{e['id']}/guards", headers=w["h_op"])).json()
    assert [x["full_name"] for x in guards] == ["Guard A", "Guard B", "Guard C"], guards
    a, b, cc = guards
    assert a["available"] and a["position_source"] == "incident status update"
    assert a["distance_m"] == pytest.approx(10, abs=1) and a["position_age_s"] >= 590
    assert b["position_source"] == "shift check-in" and b["distance_m"] == pytest.approx(400, abs=2)
    assert cc["available"] is False and cc["busy_incident_id"]


@pytest.mark.asyncio
async def test_dispatch_goes_through_the_platforms_dispatch_and_opens_the_incident_first():
    w = await _world(zone_type="NORMAL")     # a low-risk event: no incident yet
    at = _last(14)
    sid = await _flying(w, at)
    await _sustained(w, at)
    await _ai(at + timedelta(seconds=10))
    [e] = await _events(sid)
    g = await _guards(w, datetime.now(timezone.utc))
    async with _client() as c:
        auto = await c.post(f"/api/v1/drone-events/{e['id']}/dispatch", headers=w["h_op"], json={"notes": "go"})
        named = await c.post(f"/api/v1/drone-events/{e['id']}/dispatch", headers=w["h_op"],
                             json={"guard_user_id": str(g["B"])})
    assert auto.status_code == 200, auto.text
    body = auto.json()
    assert body["incident_created"] is True and body["guard"]["full_name"] == "Guard A"
    inc = await _incident(uuid.UUID(body["incident_id"]))
    assert named.json()["incident_created"] is False and named.json()["incident_id"] == body["incident_id"]
    assert inc["dispatched_guard_id"] == g["B"] and inc["dispatched_at"] and inc["dispatch_notes"] is None
    notes = [n["note"] for n in await _sql("SELECT note FROM incident_notes WHERE incident_id = :i ORDER BY created_at",
                                           {"i": inc["id"]})]
    assert any("Guard A dispatched" in n and "10 m" in n for n in notes)


@pytest.mark.asyncio
async def test_with_no_guard_free_the_officer_is_told_to_choose_one():
    w = await _world(zone_type="NORMAL")     # a low-risk event: no incident yet
    at = _last(14)
    sid = await _flying(w, at)
    await _sustained(w, at)
    await _ai(at + timedelta(seconds=10))
    [e] = await _events(sid)
    async with _client() as c:
        r = await c.post(f"/api/v1/drone-events/{e['id']}/dispatch", headers=w["h_op"])
    assert r.status_code == 409 and "Name one" in r.json()["detail"]
    assert await _tenant_incidents(w) == [], "a refused dispatch must not leave an incident behind"


# ─── D. Resolution ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolving_the_incident_resolves_the_drone_event():
    w = await _world(zone_type="CRITICAL")
    at = _last(2, 17)
    sid = await _flying(w, at)
    await _sustained(w, at)
    await _ai(at + timedelta(seconds=10))
    [e] = await _events(sid)
    async with _client() as c:
        r = await c.post(f"/api/v1/incidents/{e['incident_id']}/resolve", headers=w["h_admin"])
    assert r.status_code == 200
    got = await _ai(at + timedelta(seconds=20))
    [e] = await _events(sid)
    assert got["resolved"] == 1 and e["status"] == "RESOLVED" and e["resolved_at"] is not None


# ─── E. Verify with drone ────────────────────────────────────────────────────

async def _live_flight(w) -> tuple[str, datetime]:
    """A real simulated flight, slow enough to hold, with the drone over the zone."""
    await _sql("UPDATE drone_provider_configs SET config = '{\"speed_factor\": 1}' WHERE id = :p", {"p": w["provider"]})
    async with _client() as c:
        r = await c.post(f"/api/v1/drone-missions/{w['mission']}/run", headers=w["h_admin"])
    sid = r.json()["session"]["id"]
    t = datetime.now(timezone.utc)
    for _ in range(25):
        t += timedelta(seconds=2)
        await runner.run_tick(AsyncSessionLocal, runner.ListPublisher(), now=t)
        s = await _session(sid)
        d = (await _sql("SELECT current_latitude FROM drones WHERE id = :d", {"d": w["drone"]}))[0]
        if s["status"] == "ACTIVE" and d["current_latitude"] and d["current_latitude"] > 1.3005:
            return sid, t
    raise AssertionError("the drone never reached the zone")


@pytest.mark.asyncio
async def test_verify_with_drone_holds_the_flight_looks_again_and_resumes():
    w = await _world()
    sid, t = await _live_flight(w)
    await _detect(w, t, "intrusion")
    await _ai(t + timedelta(seconds=1))
    [e] = await _events(sid)
    async with _client() as c:
        r = await c.post(f"/api/v1/drone-events/{e['id']}/verify-with-drone", headers=w["h_op"],
                         json={"hold_seconds": 10, "reason": "confirm the intruder"})
        dup = await c.post(f"/api/v1/drone-events/{e['id']}/verify-with-drone", headers=w["h_op"])
    assert r.status_code == 202, r.text
    assert dup.status_code == 409 and "already holding" in dup.json()["detail"]

    await runner.run_tick(AsyncSessionLocal, runner.ListPublisher(), now=t + timedelta(seconds=2))
    assert (await _session(sid))["status"] == "PAUSED"
    await runner.run_tick(AsyncSessionLocal, runner.ListPublisher(), now=t + timedelta(seconds=3))
    v = (await _sql("SELECT * FROM drone_verification_requests WHERE event_id = :e", {"e": e["id"]}))[0]
    assert v["status"] == "HOLDING"

    for s in (4, 6, 8):                                    # the drone looks again
        await _detect(w, t + timedelta(seconds=s), "intrusion", 0.9)
    await _ai(t + timedelta(seconds=9))
    await runner.run_tick(AsyncSessionLocal, runner.ListPublisher(), now=t + timedelta(seconds=14))
    await runner.run_tick(AsyncSessionLocal, runner.ListPublisher(), now=t + timedelta(seconds=15))
    v = dict((await _sql("SELECT * FROM drone_verification_requests WHERE event_id = :e", {"e": e["id"]}))[0])
    [e2] = await _events(sid)
    assert v["status"] == "COMPLETED" and v["result"]["detections_added"] == 3
    assert v["result"]["risk_before"] == e["risk_level"] and e2["verification_state"] == "VERIFIED"
    assert (await _session(sid))["status"] in ("ACTIVE", "RETURNING", "COMPLETED"), "the flight never resumed"
    cmds = [x["command"] for x in await _sql(
        "SELECT command FROM drone_session_commands WHERE session_id = :s ORDER BY requested_at", {"s": uuid.UUID(sid)})]
    assert cmds == ["PAUSE", "RESUME"]


@pytest.mark.asyncio
async def test_verify_with_drone_refuses_what_it_cannot_do_safely():
    w = await _world()
    sid, t = await _live_flight(w)
    await _detect(w, t, "intrusion")
    await _ai(t + timedelta(seconds=1))
    [e] = await _events(sid)
    await _sql("UPDATE drone_events SET drone_latitude = drone_latitude + 0.01 WHERE id = :e", {"e": e["id"]})
    async with _client() as c:
        far = await c.post(f"/api/v1/drone-events/{e['id']}/verify-with-drone", headers=w["h_op"])
        await _sql("UPDATE drone_events SET drone_latitude = drone_latitude - 0.01 WHERE id = :e", {"e": e["id"]})
        await _sql("UPDATE drones SET battery_level = 25 WHERE id = :d", {"d": w["drone"]})
        flat = await c.post(f"/api/v1/drone-events/{e['id']}/verify-with-drone", headers=w["h_op"])
        await _sql("UPDATE drones SET battery_level = 90 WHERE id = :d", {"d": w["drone"]})
        await _sql("UPDATE drone_patrol_sessions SET status = 'RETURNING' WHERE id = :s", {"s": uuid.UUID(sid)})
        returning = await c.post(f"/api/v1/drone-events/{e['id']}/verify-with-drone", headers=w["h_op"])
    assert far.status_code == 409 and "m from where it saw this" in far.json()["detail"]
    assert flat.status_code == 409 and "battery" in flat.json()["detail"]
    assert returning.status_code == 409 and "returning" in returning.json()["detail"]
    assert await _sql("SELECT 1 FROM drone_session_commands WHERE session_id = :s", {"s": uuid.UUID(sid)}) == []
