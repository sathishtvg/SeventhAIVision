"""Fleet geofences: a fix that wobbles on the boundary changes nothing; entries, exits, stays and overspeeds are
recorded and alerted as their rules say; a vehicle that stops reporting is alerted once; and a geofence never
opens a barrier or confirms who is driving.

Design: docs/plans/2026-10-14-gps-fleet-tracking-design.md, section 5. Positions arrive as Traccar forwards
them, through the real app over ASGI as svc_app; the minute pass is run as svc_app with two organisations.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from app.main import app  # noqa: F401
from app.db.session import AsyncSessionLocal
from app.services import fleet_alerts as fa
from app.services import fleet_geofences as fg
from app.services import fleet_tracking as ft
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _sql, _world
from tests.test_fleet_tracking import BASE, _device, _vehicle, forwarding, said  # noqa: F401 - the fixture is used
from tests.test_investigation_search import _audit

APP = Path(__file__).resolve().parents[1] / "app"
CENTRE = (1.3000, 103.8000)          # Factory A's pin in the test world
IN = CENTRE                          # 0 m from the centre of a 200 m circle
OUT = (1.3030, 103.8000)             # about 334 m: past the line by 134 m
ON_LINE = (1.30185, 103.8000)        # about 206 m: 6 m past the line, nearer than 15 m


def _circle(radius: float = 200, centre=CENTRE) -> dict:
    return {"kind": "circle", "center": {"lat": centre[0], "lon": centre[1]}, "radius_m": radius}


async def _fence(c, w, *, who: int = ADMIN, site: str | None = "site_a", shape: dict | None = None, rules=(), **extra):
    body = {"name": f"Gate {uuid.uuid4().hex[:4]}", "site_id": str(w[site]) if site else None,
            "shape": shape or _circle(), "rules": list(rules), **extra}
    return await c.post(f"{BASE}/geofences", headers=w["h"][who], json=body)


async def _tracked(c, w, plate: str, site: str = "site_a") -> tuple[dict, str]:
    v = await _vehicle(c, w, plate, site=site)
    device = f"IMEI-{uuid.uuid4().hex[:10]}"
    r = await _device(c, w, v["id"], device)
    assert r.status_code == 200, r.text
    return v, device


async def _fix(c, headers, device: str, where, ago_s: float, *, knots: float = 0.0, accuracy: float = 5.0) -> dict:
    r = await c.post(f"{BASE}/ingest/traccar", headers=headers, json=said(
        device, ago=timedelta(seconds=ago_s), latitude=where[0], longitude=where[1], speed=knots, accuracy=accuracy))
    assert r.status_code == 200 and r.json()["accepted"], r.text
    return r.json()


async def _events(vehicle_id: str) -> list[dict]:
    return await _sql("""
        SELECT event_type, occurred_at, alert_id, between_start, between_end FROM geofence_events
         WHERE vehicle_id = :v ORDER BY occurred_at, event_type
    """, {"v": vehicle_id})


async def _alerts(tenant, code: str | None = None) -> list[dict]:
    rows = await _sql("""
        SELECT id, alert_code, severity, title, site_id, status, message_params FROM alerts
         WHERE tenant_id = :t AND module_type = 'gps' ORDER BY created_at, alert_code
    """, {"t": tenant})
    rows = [{**r, "message_params": json.loads(r["message_params"]) if isinstance(r["message_params"], str)
             else r["message_params"]} for r in rows]
    return [r for r in rows if code is None or r["alert_code"] == code]


def _ago(seconds: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


# ─── A. Shapes and the line ──────────────────────────────────────────────────

def test_a_shape_is_a_circle_of_25_m_to_50_km_or_a_polygon_whose_edges_do_not_cross():
    assert fg.check_circle({"lat": 1.3, "lon": 103.8}, 25)[1] == 25
    for radius in (24, 50_001, float("nan")):
        with pytest.raises(ValueError):
            fg.check_circle({"lat": 1.3, "lon": 103.8}, radius)
    square = [{"lat": 1.30, "lon": 103.80}, {"lat": 1.30, "lon": 103.81}, {"lat": 1.31, "lon": 103.81},
              {"lat": 1.31, "lon": 103.80}]
    assert len(fg.check_polygon(square)) == 4
    assert len(fg.check_polygon([*square, square[0]])) == 4, "a closed ring is the same polygon"
    bow_tie = [square[0], square[2], square[1], square[3]]
    with pytest.raises(ValueError, match="cross"):
        fg.check_polygon(bow_tie)
    with pytest.raises(ValueError):
        fg.check_polygon(square[:2])
    with pytest.raises(ValueError):
        fg.check_polygon([square[0], square[1], square[1], square[2]])
    with pytest.raises(ValueError):
        fg.check_polygon([{"lat": 91, "lon": 0}, *square[1:]])
    outline = fg.circle_outline((1.3, 103.8), 200)
    assert len(outline) == fg.CIRCLE_POINTS
    assert all(abs(fg.haversine_meters(1.3, 103.8, p["lat"], p["lon"]) - 200) < 1 for p in outline)


def test_a_fix_nearer_the_line_than_15_m_or_its_own_accuracy_counts_for_neither_side():
    circle = fg.Shape("circle", center=CENTRE, radius_m=200)
    assert fg.side(*IN, 5, circle) is True
    assert fg.side(*OUT, 5, circle) is False
    assert fg.side(*ON_LINE, 5, circle) is None
    assert fg.side(*OUT, 150, circle) is None, "a fix 134 m past the line but only good to 150 m says nothing"
    square = fg.Shape("polygon", points=((1.30, 103.80), (1.30, 103.81), (1.31, 103.81), (1.31, 103.80)))
    assert fg.side(1.305, 103.805, None, square) is True
    assert fg.side(1.3001, 103.805, None, square) is None, "11 m inside the bottom edge"
    assert fg.side(1.2990, 103.805, None, square) is False


# ─── B. Entering and leaving ─────────────────────────────────────────────────

async def test_a_change_needs_two_fixes_past_the_line_is_dated_at_the_first_and_is_not_alerted_twice_in_2_min(forwarding):
    w = await _world(licensed=False)
    async with _client() as c:
        v, device = await _tracked(c, w, "GEO 1")
        r = await _fence(c, w, rules=[{"kind": "entry", "severity": "medium"}, {"kind": "exit", "severity": "high"}])
        assert r.status_code == 201, r.text
        fence = r.json()
        await _fix(c, forwarding, device, OUT, 600)          # says which side; raises nothing
        await _fix(c, forwarding, device, IN, 540)
        assert await _events(v["id"]) == [], "one fix is not enough"
        await _fix(c, forwarding, device, IN, 530)           # entered, at 540
        await _fix(c, forwarding, device, ON_LINE, 525)      # on the line: nothing
        await _fix(c, forwarding, device, OUT, 520)
        await _fix(c, forwarding, device, IN, 515)           # a wobble: nothing
        await _fix(c, forwarding, device, OUT, 500)
        await _fix(c, forwarding, device, OUT, 495)          # left, at 500
        await _fix(c, forwarding, device, IN, 480)
        await _fix(c, forwarding, device, IN, 475)           # entered again, within 2 minutes of the last entry alert
    events = await _events(v["id"])
    assert [e["event_type"] for e in events] == ["entry", "exit", "entry"]
    assert abs((events[0]["occurred_at"] - _ago(540)).total_seconds()) < 5, "dated at the first fix inside"
    assert [e["alert_id"] is not None for e in events] == [True, True, False]
    alerts = await _alerts(w["tenant"])
    assert [(a["alert_code"], a["severity"]) for a in alerts] == [("gps.entry", "medium"), ("gps.exit", "high")]
    assert alerts[0]["title"] == f"Car GEO 1 (GEO 1) entered {fence['name']}"
    assert str(alerts[0]["site_id"]) == str(w["site_a"]) and alerts[0]["status"] == "open"
    assert alerts[0]["message_params"]["vehicle_id"] == v["id"]
    assert alerts[0]["message_params"]["geofence_id"] == fence["id"]


async def test_the_first_fix_says_which_side_and_a_change_after_silence_is_between_two_times(forwarding):
    w = await _world(licensed=False)
    async with _client() as c:
        v, device = await _tracked(c, w, "GEO 2")
        await _fence(c, w, rules=[{"kind": "entry"}, {"kind": "exit"}])
        await _fix(c, forwarding, device, IN, 4000)          # parked inside when it starts reporting: no entry
        assert await _events(v["id"]) == []
        await _fix(c, forwarding, device, OUT, 600)
        await _fix(c, forwarding, device, OUT, 590)
    [exit_] = await _events(v["id"])
    assert exit_["event_type"] == "exit"
    assert abs((exit_["between_start"] - _ago(4000)).total_seconds()) < 5
    assert abs((exit_["between_end"] - _ago(600)).total_seconds()) < 5
    [alert] = await _alerts(w["tenant"])
    assert " between " in alert["title"] and alert["title"].endswith(" UTC")


async def test_a_late_position_never_changes_the_side(forwarding):
    w = await _world(licensed=False)
    async with _client() as c:
        v, device = await _tracked(c, w, "GEO 3")
        await _fence(c, w, rules=[{"kind": "exit"}])
        await _fix(c, forwarding, device, IN, 300)
        await _fix(c, forwarding, device, OUT, 900)          # sent late
        await _fix(c, forwarding, device, OUT, 800)          # and another
    assert await _events(v["id"]) == []
    [state] = await _sql("SELECT inside FROM fleet_geofence_state WHERE vehicle_id = :v", {"v": v["id"]})
    assert state["inside"] is True


async def test_overspeed_needs_the_reported_speed_over_the_limit_twice_and_is_raised_once_an_episode(forwarding):
    w = await _world(licensed=False)
    async with _client() as c:
        v, device = await _tracked(c, w, "GEO 4")
        await _fence(c, w, rules=[{"kind": "overspeed", "speed_kmh": 50, "severity": "high"}])
        await _fix(c, forwarding, device, IN, 600)
        # 30 kn = 55.6 km/h. The second episode is more than 2 minutes after the first alert.
        for ago, knots in ((590, 30), (580, 30), (570, 30), (560, 10), (400, 30), (390, 30)):
            await _fix(c, forwarding, device, IN, ago, knots=knots)
        await _fix(c, forwarding, device, IN, 380, knots=10)
        await _fix(c, forwarding, device, IN, 370, knots=30)
        await _fix(c, forwarding, device, IN, 360, knots=30)   # a third episode within 2 minutes: recorded, no alert
    events = await _events(v["id"])
    assert [e["event_type"] for e in events] == ["speed_violation"] * 3
    assert [round((e["occurred_at"] - _ago(0)).total_seconds() / 10) * 10 for e in events] == [-580, -390, -360]
    assert [e["alert_id"] is not None for e in events] == [True, True, False]
    alerts = await _alerts(w["tenant"], "gps.speed_violation")
    assert len(alerts) == 2 and alerts[0]["title"].startswith("Car GEO 4 (GEO 4) at 56 km/h in ")


# ─── C. The minute pass, as svc_app, two organisations ───────────────────────

async def test_the_minute_pass_changes_a_side_held_30_s_and_raises_a_long_stay_once_for_each_organisation(forwarding):
    w, other = await _world(licensed=False), await _world(licensed=False)
    async with _client() as c:
        stays = {}
        for world in (w, other):
            v, device = await _tracked(c, world, f"STAY {world['tenant'].hex[:4]}")
            await _fence(c, world, rules=[{"kind": "dwell_inside", "minutes": 5, "severity": "low"}])
            await _fix(c, forwarding, device, IN, 1200)
            await _fix(c, forwarding, device, IN, 60)
            stays[str(world["tenant"])] = v
        leaving, device = await _tracked(c, w, "LEAVE 1", site="site_b")
        await _fence(c, w, site="site_b", shape=_circle(200, (1.35, 103.85)), rules=[{"kind": "exit"}])
        await _fix(c, forwarding, device, (1.35, 103.85), 300)
        await _fix(c, forwarding, device, (1.3530, 103.85), 100)    # one fix outside, then nothing more
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
    await ft.sweep(AsyncSessionLocal, None)
    await ft.sweep(AsyncSessionLocal, None)
    for world in (w, other):
        stay = await _alerts(world["tenant"], "gps.dwell_inside")
        assert len(stay) == 1, "once a stay, in each organisation"
        assert stay[0]["title"].endswith("for more than 5 min") and stay[0]["severity"] == "low"
    assert [e["event_type"] for e in await _events(leaving["id"])] == ["exit"], "held 30 s with nothing saying otherwise"
    assert len(await _alerts(w["tenant"], "gps.exit")) == 1
    assert await _alerts(other["tenant"], "gps.exit") == []


async def test_a_vehicle_that_stops_reporting_is_alerted_once_an_episode_and_noted_when_it_reports_again(forwarding):
    w, other = await _world(licensed=False), await _world(licensed=False)
    async with _client() as c:
        mine, my_device = await _tracked(c, w, "OFF 1")
        theirs, their_device = await _tracked(c, other, "OFF 2")
        r = await c.put(f"{BASE}/alert-settings", headers=other["h"][ADMIN],
                        json={"offline": {"on": False, "severity": "medium"}, "stale": {"on": True, "severity": "high"}})
        assert r.status_code == 200, r.text
        assert (await c.put(f"{BASE}/alert-settings", headers=other["h"][SUPERVISOR], json=r.json())).status_code == 403
        assert (await c.get(f"{BASE}/alert-settings", headers=w["h"][VIEWER])).json() == {
            "offline": {"on": True, "severity": "medium"}, "stale": {"on": False, "severity": "low"}}
        for device in (my_device, their_device):
            await _fix(c, forwarding, device, IN, 10)
        now = datetime.now(timezone.utc)
        for minutes in (10, 40, 41):
            await ft.sweep(AsyncSessionLocal, None, now=now + timedelta(minutes=minutes))
        assert [(a["alert_code"], a["severity"]) for a in await _alerts(w["tenant"])] == [("gps.offline", "medium")]
        assert [(a["alert_code"], a["severity"]) for a in await _alerts(other["tenant"])] == [("gps.stale", "high")]
        await _fix(c, forwarding, my_device, IN, 5)
    [alert] = await _alerts(w["tenant"])
    notes = await _sql("SELECT note, author_user_id FROM alert_notes WHERE alert_id = :a", {"a": alert["id"]})
    assert len(notes) == 1 and notes[0]["note"].startswith("The vehicle is reporting again")
    assert notes[0]["author_user_id"] is None
    assert alert["status"] == "open", "noted, not closed for anybody"
    assert await _audit(other, "fleet.alert_settings.changed")


async def test_a_vehicle_out_of_service_or_not_tracked_raises_no_offline_alert(forwarding):
    w = await _world(licensed=False)
    async with _client() as c:
        v, device = await _tracked(c, w, "OFF 3")
        r = await c.patch(f"{BASE}/vehicles/{v['id']}", headers=w["h"][ADMIN], json={"service_status": "maintenance"})
        assert r.status_code == 200, r.text
        await _fix(c, forwarding, device, IN, 10)
    await ft.sweep(AsyncSessionLocal, None, now=datetime.now(timezone.utc) + timedelta(minutes=40))
    assert await _alerts(w["tenant"]) == []


# ─── D. Who may, and site limits ─────────────────────────────────────────────

async def test_who_may_draw_geofences_and_a_site_limit_holds():
    w = await _world(licensed=False)
    async with _client() as c:
        for who in (VIEWER, OPERATOR, GUARD):
            assert (await _fence(c, w, who=who)).status_code == 403
        assert (await _fence(c, w, who=SUPERVISOR)).status_code == 201
        assert (await _fence(c, w, who=SUPERVISOR, site="site_b")).status_code == 403
        assert (await _fence(c, w, who=SUPERVISOR, site=None)).status_code == 403, "the organisation's own is not theirs"
        theirs = (await _fence(c, w, who=ADMIN, site="site_b", shape=_circle(200, (1.35, 103.85)))).json()
        everyone = (await _fence(c, w, who=MANAGER, site=None)).json()
        seen = await c.get(f"{BASE}/geofences", headers=w["h"][SUPERVISOR])
        names = {g["name"]: g for g in seen.json()["items"]}
        assert theirs["name"] not in names and everyone["name"] in names
        assert names[everyone["name"]]["may_change"] is False
        assert (await c.get(f"{BASE}/geofences/{theirs['id']}", headers=w["h"][SUPERVISOR])).status_code == 404
        r = await c.patch(f"{BASE}/geofences/{everyone['id']}", headers=w["h"][SUPERVISOR], json={"name": "Mine now"})
        assert r.status_code == 403
        options = (await c.get(f"{BASE}/geofences/options", headers=w["h"][SUPERVISOR])).json()
        assert [s["id"] for s in options["sites"]] == [str(w["site_a"])]
        b_car = await _vehicle(c, w, "SITE B", site="site_b")
    await _sql("INSERT INTO geofence_events (tenant_id, vehicle_id, geofence_id, event_type, occurred_at) "
               "VALUES (:t, :v, :g, 'entry', now())", {"t": w["tenant"], "v": b_car["id"], "g": theirs["id"]})
    async with _client() as c:
        assert (await c.get(f"{BASE}/geofence-events", headers=w["h"][SUPERVISOR])).json()["items"] == []
        assert len((await c.get(f"{BASE}/geofence-events", headers=w["h"][ADMIN])).json()["items"]) == 1


async def test_a_rule_tells_only_people_who_may_see_the_vehicle():
    w = await _world(licensed=False)
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        at_b = {"id": uuid.uuid4(), "site_id": w["site_b"]}
        told = await fa.recipients(db, at_b, [w["users"][SUPERVISOR], w["users"][VIEWER]], [OPERATOR])
        assert told == sorted([str(w["users"][VIEWER]), str(w["users"][OPERATOR])]), "the supervisor's site is A"
        at_a = {"id": uuid.uuid4(), "site_id": w["site_a"]}
        assert str(w["users"][SUPERVISOR]) in await fa.recipients(db, at_a, [w["users"][SUPERVISOR]], [])
        assert await fa.recipients(db, at_a, [], []) == []


# ─── E. Rules, the older API, archiving ──────────────────────────────────────

async def test_a_site_boundary_can_be_the_start_and_the_older_api_reads_the_same_rules():
    w = await _world(licensed=False)
    bare = uuid.uuid4()
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i, :t, 'No boundary')", {"i": bare, "t": w["tenant"]})
    async with _client() as c:
        r = await c.post(f"{BASE}/geofences", headers=w["h"][ADMIN], json={
            "name": "Factory A boundary", "site_id": str(w["site_a"]), "from_site": True,
            "rules": [{"kind": "entry"}, {"kind": "overspeed", "speed_kmh": 60, "severity": "high"}]})
        assert r.status_code == 201, r.text
        g = r.json()
        assert (g["shape"], g["radius_m"], g["center"]) == ("circle", 300, {"lat": 1.3, "lon": 103.8})
        assert len(g["polygon"]) == fg.CIRCLE_POINTS
        older = {x["id"]: x for x in (await c.get("/api/v1/gps/geofences", headers=w["h"][ADMIN])).json()}
        assert (older[g["id"]]["alert_on_entry"], older[g["id"]]["alert_on_exit"], float(older[g["id"]]["speed_limit"])) == (
            True, False, 60.0)
        r = await c.patch(f"{BASE}/geofences/{g['id']}", headers=w["h"][ADMIN], json={"rules": [{"kind": "exit"}]})
        assert r.status_code == 200, r.text
        assert {x["kind"]: x["is_active"] for x in r.json()["rules"]} == {"entry": False, "exit": True, "overspeed": False}
        older = {x["id"]: x for x in (await c.get("/api/v1/gps/geofences", headers=w["h"][ADMIN])).json()}
        assert (older[g["id"]]["alert_on_entry"], older[g["id"]]["alert_on_exit"], older[g["id"]]["speed_limit"]) == (
            False, True, None)
        r = await c.post(f"{BASE}/geofences", headers=w["h"][ADMIN],
                         json={"name": "Nothing", "site_id": str(bare), "from_site": True})
        assert r.status_code == 422 and "no boundary" in r.json()["detail"]
        bow_tie = {"kind": "polygon", "points": [{"lat": 1.30, "lon": 103.80}, {"lat": 1.31, "lon": 103.81},
                                                 {"lat": 1.30, "lon": 103.81}, {"lat": 1.31, "lon": 103.80}]}
        r = await _fence(c, w, shape=bow_tie)
        assert r.status_code == 422 and "cross" in r.json()["detail"]
        assert (await _fence(c, w, shape=_circle(10))).status_code == 422
        r = await _fence(c, w, rules=[{"kind": "dwell_inside"}])
        assert r.status_code == 422 and "minutes" in r.json()["detail"]
        r = await _fence(c, w, rules=[{"kind": "entry", "role_ids": [1]}])
        assert r.status_code == 422


async def test_an_archived_geofence_keeps_its_events_forgets_every_side_and_is_not_changed(forwarding):
    w = await _world(licensed=False)
    async with _client() as c:
        v, device = await _tracked(c, w, "ARC 1")
        g = (await _fence(c, w, rules=[{"kind": "entry"}])).json()
        await _fix(c, forwarding, device, OUT, 300)
        await _fix(c, forwarding, device, IN, 200)
        await _fix(c, forwarding, device, IN, 190)
        assert (await c.post(f"{BASE}/geofences/{g['id']}/archive", headers=w["h"][ADMIN], json={"reason": "x"})).status_code == 422
        r = await c.post(f"{BASE}/geofences/{g['id']}/archive", headers=w["h"][ADMIN], json={"reason": "Gate moved"})
        assert r.status_code == 200 and r.json()["archived"] and r.json()["is_active"] is False
        assert (await c.patch(f"{BASE}/geofences/{g['id']}", headers=w["h"][ADMIN], json={"name": "Again"})).status_code == 409
        listed = (await c.get(f"{BASE}/geofences", headers=w["h"][ADMIN])).json()["items"]
        assert g["id"] not in [x["id"] for x in listed]
        archived = (await c.get(f"{BASE}/geofences?archived=true", headers=w["h"][ADMIN])).json()["items"]
        assert [x["archive_reason"] for x in archived if x["id"] == g["id"]] == ["Gate moved"]
        events = (await c.get(f"{BASE}/geofence-events?geofence_id={g['id']}", headers=w["h"][VIEWER])).json()["items"]
        assert [e["event_type"] for e in events] == ["entry"] and events[0]["alert_status"] == "open"
        await _fix(c, forwarding, device, OUT, 100)
        await _fix(c, forwarding, device, OUT, 90)
    assert [e["event_type"] for e in await _events(v["id"])] == ["entry"], "an archived geofence is not looked at"
    assert await _sql("SELECT 1 FROM fleet_geofence_state WHERE geofence_id = :g", {"g": g["id"]}) == []
    assert [a["detail"]["reason"] for a in await _audit(w, "fleet.geofence.archived")] == ["Gate moved"]


async def test_a_geofence_for_chosen_groups_looks_at_those_vehicles_only(forwarding):
    w = await _world(licensed=False)
    async with _client() as c:
        member, member_device = await _tracked(c, w, "GRP 1")
        outsider, outsider_device = await _tracked(c, w, "GRP 2")
        group = await c.post(f"{BASE}/groups", headers=w["h"][ADMIN], json={"name": "Night patrol", "site_id": str(w["site_a"])})
        assert group.status_code in (200, 201), group.text
        r = await c.put(f"{BASE}/groups/{group.json()['id']}/vehicles", headers=w["h"][ADMIN], json={"vehicle_ids": [member["id"]]})
        assert r.status_code == 200, r.text
        r = await _fence(c, w, rules=[{"kind": "entry"}], group_ids=[group.json()["id"]])
        assert r.status_code == 201 and [x["name"] for x in r.json()["groups"]] == ["Night patrol"]
        for device in (member_device, outsider_device):
            for where, ago in ((OUT, 300), (IN, 200), (IN, 190)):
                await _fix(c, forwarding, device, where, ago)
    assert [e["event_type"] for e in await _events(member["id"])] == ["entry"]
    assert await _events(outsider["id"]) == []


# ─── F. Never a barrier, never an identity ───────────────────────────────────

def test_a_geofence_never_opens_a_barrier_admits_anybody_or_confirms_who_is_driving():
    sources = {name: (APP / "services" / name).read_text(encoding="utf-8")
               for name in ("fleet_geofences.py", "fleet_alerts.py")}
    sources["routers/fleet_geofences.py"] = (APP / "routers" / "fleet_geofences.py").read_text(encoding="utf-8")
    for name, source in sources.items():
        imports = "\n".join(line for line in source.splitlines() if line.startswith(("import ", "from ")))
        for word in ("barrier", "access", "door", "lpr", "anpr", "watchlist", "visitor", "identity", "face"):
            assert word not in imports.lower(), (name, word)
        written = set(re.findall(r"(?:INSERT INTO|(?<!DO )UPDATE|DELETE FROM)\s+(\w+)", source))
        assert written <= {"alerts", "alert_notes", "geofence_events", "fleet_geofence_state", "geofences",
                           "fleet_geofence_rules", "fleet_geofence_groups", "tenant_settings"}, (name, written)
        assert "confirmed" not in source.lower().replace("never confirms", ""), name
    for name in ("fleet_geofences.py", "fleet_alerts.py"):
        assert "never opens a barrier, admits anybody or confirms who is driving" in " ".join(sources[name].lower().split())
