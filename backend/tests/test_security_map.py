"""The security map: what is where, in layers, for the people who may see it.

  A — The rules, with nothing running: outlines, the last recorded position, who is nearest
  B — The layers, through the API
  C — A guard is shown where they last recorded being, with how long ago
  D — What is near an incident
  E — Who may see which layer, at which sites, in which organisation
  F — The places of a site: drawn, changed, retired, and what the database refuses

Every request goes through the real app over ASGI, as svc_app with RLS
enforced.

The claims, each with tests: a layer is shown only to someone who may already
read what is on it; a thing with no position is counted and not hidden; a
guard's position is the last they recorded and always says its age; `around`
lists and sends nobody anywhere; and a place is retired, never removed.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import site_map as api
from app.services import guard_positions as positions
from app.services import security_map as maps
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _auth, _client, _run, _sql, _world
from tests.test_investigation_search import _audit

BASE = "/api/v1/site-map"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0145_site_places.py").read_text(encoding="utf-8")
EVERY_LAYER = [layer.key for layer in maps.LAYERS]
#: Site A of the test world is at 1.3000, 103.8000. A thousandth of a degree of latitude is about 111 m.
LAT, LNG = 1.3000, 103.8000


async def _ground(w: dict, *, site: str = "site_a", tag: str = "A", lat: float = LAT, lng: float = LNG) -> dict:
    """A site with things on it: two cameras with positions and one without, a
    guard on shift who has scanned a checkpoint, another who has only clocked
    in, an incident, an alert, a situation, a drone, a patrol route."""
    t, s = w["tenant"], w[site]
    now = datetime.now(timezone.utc)
    ids = {k: uuid.uuid4() for k in ("gate", "yard", "nowhere", "stream", "incident", "lost", "alert", "situation",
                                     "drone", "route", "cp1", "cp2", "session", "scan", "shift1", "shift2", "door",
                                     "second")}
    scanner, second = w["users"][GUARD], ids["second"]
    stmts = [
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
         "VALUES (:i,:t,5,:e,'x',:n)", {"i": second, "t": t, "e": f"g2-{second.hex[:8]}@map.test",
                                         "n": f"Second Guard {tag}"}),
        ("INSERT INTO cameras (id, tenant_id, site_id, name, location, latitude, longitude) VALUES (:i,:t,:s,:n,'Gate',:a,:o)",
         {"i": ids["gate"], "t": t, "s": s, "n": f"Gate {tag}", "a": lat, "o": lng}),
        ("INSERT INTO cameras (id, tenant_id, site_id, name, latitude, longitude) VALUES (:i,:t,:s,:n,:a,:o)",
         {"i": ids["yard"], "t": t, "s": s, "n": f"Yard {tag}", "a": lat + 0.0018, "o": lng}),
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,:n)",
         {"i": ids["nowhere"], "t": t, "s": s, "n": f"Unsurveyed {tag}"}),
        ("INSERT INTO streams (id, tenant_id, camera_id, url, status, last_frame_at) "
         "VALUES (:i,:t,:c,'rtsp://203.0.113.10/s','offline',:at)",
         {"i": ids["stream"], "t": t, "c": ids["yard"], "at": now - timedelta(minutes=20)}),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, created_at) "
         "VALUES (:i,:t,:c,:n,'high','open',:at)",
         {"i": ids["incident"], "t": t, "c": ids["gate"], "n": f"Forced gate {tag}", "at": now - timedelta(minutes=30)}),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, created_at) "
         "VALUES (:i,:t,:c,:n,'low','open',:at)",
         {"i": ids["lost"], "t": t, "c": ids["nowhere"], "n": f"Somewhere {tag}", "at": now - timedelta(minutes=10)}),
        ("INSERT INTO alerts (id, tenant_id, camera_id, site_id, module_type, severity, title, status, created_at) "
         "VALUES (:i,:t,:c,:s,'intrusion','critical',:n,'open',:at)",
         {"i": ids["alert"], "t": t, "c": ids["yard"], "s": s, "n": f"Zone breach {tag}",
          "at": now - timedelta(minutes=5)}),
        ("INSERT INTO security_situations (id, tenant_id, site_id, situation_number, title, severity, started_at, "
         "    last_event_at, risk_level, risk_score, primary_camera_id) VALUES (:i,:t,:s,:n,:ti,'high',:at,:at,'HIGH',72,:c)",
         {"i": ids["situation"], "t": t, "s": s, "n": f"SIT-M-{uuid.uuid4().hex[:8]}", "ti": f"Activity {tag}",
          "at": now - timedelta(minutes=15), "c": ids["gate"]}),
        ("INSERT INTO drones (id, tenant_id, site_id, name, code, status, battery_level, current_latitude, "
         "    current_longitude, last_heartbeat_at) VALUES (:i,:t,:s,:n,:c,'READY',87,:a,:o,:at)",
         {"i": ids["drone"], "t": t, "s": s, "n": f"Drone {tag}", "c": f"D-{uuid.uuid4().hex[:6]}",
          "a": lat + 0.0005, "o": lng, "at": now - timedelta(seconds=20)}),
        ("INSERT INTO patrol_routes (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Perimeter')",
         {"i": ids["route"], "t": t, "s": s}),
        ("INSERT INTO patrol_checkpoints (id, tenant_id, route_id, sequence, name, latitude, longitude) "
         "VALUES (:i,:t,:r,1,:n,:a,:o)", {"i": ids["cp1"], "t": t, "r": ids["route"], "n": f"Back fence {tag}",
                                         "a": lat + 0.0009, "o": lng}),
        ("INSERT INTO patrol_checkpoints (id, tenant_id, route_id, sequence, name) VALUES (:i,:t,:r,2,:n)",
         {"i": ids["cp2"], "t": t, "r": ids["route"], "n": f"Unsurveyed checkpoint {tag}"}),
        # The first guard clocked in at the gate two hours ago and scanned the back fence ten minutes ago.
        ("INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, "
         "    check_in_lat, check_in_lon) VALUES (:i,:t,:s,:g,:a,:b,:a,:la,:lo)",
         {"i": ids["shift1"], "t": t, "s": s, "g": scanner, "a": now - timedelta(hours=2),
          "b": now + timedelta(hours=6), "la": lat, "lo": lng}),
        ("INSERT INTO patrol_sessions (id, tenant_id, route_id, guard_user_id) VALUES (:i,:t,:r,:g)",
         {"i": ids["session"], "t": t, "r": ids["route"], "g": scanner}),
        ("INSERT INTO checkpoint_scans (id, tenant_id, session_id, checkpoint_id, scanned_at, scan_method, latitude, "
         "    longitude, guard_user_id) VALUES (:i,:t,:ss,:c,:at,'qr',:la,:lo,:g)",
         {"i": ids["scan"], "t": t, "ss": ids["session"], "c": ids["cp1"], "at": now - timedelta(minutes=10),
          "la": lat + 0.0009, "lo": lng, "g": scanner}),
        # The second clocked in three hours ago, two kilometres off, and has recorded nothing since.
        ("INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, "
         "    check_in_lat, check_in_lon) VALUES (:i,:t,:s,:g,:a,:b,:a,:la,:lo)",
         {"i": ids["shift2"], "t": t, "s": s, "g": second, "a": now - timedelta(hours=3),
          "b": now + timedelta(hours=5), "la": lat + 0.018, "lo": lng}),
        ("INSERT INTO access_doors (id, tenant_id, site_id, name) VALUES (:i,:t,:s,:n)",
         {"i": ids["door"], "t": t, "s": s, "n": f"Server room {tag}"}),
    ]
    await _run(stmts)
    return {"ids": ids, "now": now, "second": second}


async def _features(c, w: dict, who: int = ADMIN, **params) -> dict:
    r = await c.get(f"{BASE}/features", headers=w["h"][who], params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _by_id(found: dict, layer: str) -> dict:
    return {f["id"]: f for f in found["layers"].get(layer, [])}


async def _draw(c, w: dict, who: int = ADMIN, expect: int = 201, **body) -> dict:
    body = {"site_id": str(w["site_a"]), "kind": "GATE", "name": "North Gate", "latitude": LAT, "longitude": LNG,
            **body}
    r = await c.post(f"{BASE}/places", headers=w["h"][who], json=body)
    assert r.status_code == expect, r.text
    return r.json()


# ─── A. The rules ────────────────────────────────────────────────────────────

def test_an_outline_is_read_whichever_way_it_was_stored_and_refused_when_it_is_not_one():
    square = [[1.30, 103.80], [1.30, 103.81], [1.31, 103.81], [1.31, 103.80]]
    assert maps.outline(square) == square
    assert maps.outline([{"lat": 1.30, "lng": 103.80}, {"lat": 1.30, "lng": 103.81}, {"lat": 1.31, "lng": 103.81}]) == \
        square[:3]
    assert maps.outline([{"latitude": 1.3, "longitude": 103.8}, {"latitude": 1.3, "lon": 103.81},
                         {"lat": 1.31, "longitude": 103.81}]) == square[:3]
    assert maps.outline("[[1.3, 103.8], [1.3, 103.81], [1.31, 103.81]]") == square[:3]
    for bad in (None, [], square[:2], "not json", {"lat": 1}, [[1.3]], [[91, 0], [0, 0], [1, 1]],
                [[0, 181], [0, 0], [1, 1]], [["a", "b"], [0, 0], [1, 1]], [[1.3, 103.8], None, [1.31, 103.81]]):
        assert maps.outline(bad) is None, bad
    assert maps.centre(square) == (pytest.approx(1.305), pytest.approx(103.805))
    assert maps.centre(None) == (None, None) and maps.centre([]) == (None, None)


def test_the_last_recorded_position_is_the_most_recent_one_that_is_whole():
    at = datetime(2026, 10, 7, 1, 0, tzinfo=timezone.utc)
    known = [(at, 1.30, 103.80, "shift check-in"), (at + timedelta(minutes=40), 1.31, 103.81, "checkpoint scan"),
             (at + timedelta(minutes=50), None, 103.82, "incident status update"),
             (None, 1.32, 103.83, "occurrence book entry")]
    assert positions.latest_position(known) == (at + timedelta(minutes=40), 1.31, 103.81, "checkpoint scan")
    assert positions.latest_position([(at, None, None, "shift check-in")]) == (None, None, None, None)
    assert positions.latest_position([]) == (None, None, None, None)
    assert positions.distance_m(1.30, 103.80, 1.301, 103.80) == pytest.approx(111, abs=2)
    assert positions.distance_m(None, 103.8, 1.3, 103.8) is None and positions.distance_m(1.3, 103.8, 1.3, None) is None
    assert positions.STALE_AFTER_S == 3600 and "not where they are now" in positions.NOTE


def test_nearest_is_by_last_recorded_position_free_before_busy_and_unknown_last():
    def guard(name, lat, available=True):
        return {"full_name": name, "latitude": lat, "longitude": 103.8 if lat is not None else None,
                "available": available}

    ranked = positions.nearest([guard("Far", 1.31), guard("Near but busy", 1.3001, False), guard("Near", 1.3005),
                                guard("Nowhere", None), guard("Also nowhere, busy", None, False)], 1.30, 103.8)
    assert [g["full_name"] for g in ranked] == ["Near", "Far", "Nowhere", "Near but busy", "Also nowhere, busy"]
    assert ranked[0]["distance_m"] == pytest.approx(56, abs=2) and ranked[2]["distance_m"] is None
    assert positions.nearest([], 1.3, 103.8) == []
    unknown = positions.nearest([guard("A", 1.3), guard("B", 1.31)], None, None)
    assert [g["distance_m"] for g in unknown] == [None, None], "with no position to measure from, no distance is given"


def test_each_layer_asks_for_the_permission_its_own_screen_asks_for():
    assert {layer.key: layer.permission for layer in maps.LAYERS} == {
        "SITE": "site:read", "CAMERA": "camera:read", "GUARD": "shift:read", "INCIDENT": "incident:read",
        "ALERT": "alert:read", "SITUATION": "intel:read", "DRONE": "drone:read", "CHECKPOINT": "patrol:read",
        "PLACE": "sitemap:read", "DRONE_ZONE": "drone:read"}
    assert [layer.key for layer in maps.LAYERS if layer.live] == ["INCIDENT", "ALERT", "SITUATION"]
    assert set(maps.READERS) == set(EVERY_LAYER)
    listed = set(re.findall(r"'([A-Z_]+)'", re.search(r"^KINDS = \((.*?)\)$", MIGRATION, re.M | re.S).group(1)))
    assert listed == set(maps.PLACE_KINDS), "the database and the code disagree on the kinds of place"
    code = Path(maps.__file__).read_text(encoding="utf-8") + Path(positions.__file__).read_text(encoding="utf-8")
    for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert verb not in code, f"the map reads; it does not {verb.strip().lower()}"


# ─── B. The layers ───────────────────────────────────────────────────────────

async def test_the_map_shows_what_is_where_in_layers_and_counts_what_has_no_position():
    w = await _world()
    g = await _ground(w)
    ids = g["ids"]
    async with _client() as c:
        found = await _features(c, w, site_id=str(w["site_a"]))
        listed = (await c.get(f"{BASE}/layers", headers=w["h"][ADMIN])).json()
    assert list(found["layers"]) == EVERY_LAYER and found["not_shown"] == [] and found["hours"] == 24
    assert found["without_position"] == {"CAMERA": 1, "INCIDENT": 1, "CHECKPOINT": 1}, \
        "an unsurveyed camera, the incident at it, and an unsurveyed checkpoint: counted, not hidden"
    assert found["note"] == maps.NOTE and "not where they are now" in found["note"]

    (site,) = found["layers"]["SITE"]
    assert (site["label"], site["state"], site["latitude"], site["longitude"]) == ("Factory A", "attention", LAT, LNG)
    assert site["detail"]["cameras"] == 3 and site["detail"]["cameras_offline"] == 1

    cameras = _by_id(found, "CAMERA")
    assert set(cameras) == {str(ids["gate"]), str(ids["yard"])}
    assert (cameras[str(ids["yard"])]["state"], cameras[str(ids["gate"])]["state"]) == ("offline", "unknown")
    assert cameras[str(ids["gate"])]["detail"]["location"] == "Gate"

    incidents = _by_id(found, "INCIDENT")
    assert set(incidents) == {str(ids["incident"])}
    incident = incidents[str(ids["incident"])]
    assert (incident["label"], incident["state"], incident["latitude"]) == ("Forced gate A", "high", LAT)
    assert incident["detail"]["position_from"] == "its camera" and incident["detail"]["status"] == "open"

    (alert,) = found["layers"]["ALERT"]
    assert (alert["label"], alert["state"], alert["detail"]["kind"]) == ("Zone breach A", "critical", "intrusion")
    assert alert["latitude"] == pytest.approx(LAT + 0.0018)
    (situation,) = found["layers"]["SITUATION"]
    assert (situation["state"], situation["detail"]["risk_score"], situation["latitude"]) == ("high", 72, LAT)
    assert situation["detail"]["stands"] == "AWAITING"
    (drone,) = found["layers"]["DRONE"]
    assert (drone["state"], drone["detail"]["battery_level"]) == ("ready", 87) and drone["at"]
    (checkpoint,) = found["layers"]["CHECKPOINT"]
    assert (checkpoint["label"], checkpoint["state"], checkpoint["detail"]["route_name"]) == (
        "Back fence A", "scanned", "Perimeter")
    assert found["layers"]["PLACE"] == [] and found["layers"]["DRONE_ZONE"] == []

    assert [x["key"] for x in listed["layers"]] == EVERY_LAYER and all(x["may_see"] for x in listed["layers"])
    assert (listed["default_hours"], listed["max_hours"], listed["default_radius_m"], listed["stale_after_s"]) == (
        24, 168, 300, 3600)
    assert listed["can_manage"] is True and listed["place_kinds"] == list(maps.PLACE_KINDS)


async def test_the_live_layers_look_back_as_far_as_asked_and_only_at_what_is_still_open():
    w = await _world()
    g = await _ground(w)
    ids, now = g["ids"], g["now"]
    old, closed = uuid.uuid4(), uuid.uuid4()
    await _run([
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, created_at) "
         "VALUES (:i,:t,:c,'Three days old','low','open',:at)",
         {"i": old, "t": w["tenant"], "c": ids["gate"], "at": now - timedelta(days=3)}),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, created_at) "
         "VALUES (:i,:t,:c,'Dealt with','low','resolved',:at)",
         {"i": closed, "t": w["tenant"], "c": ids["gate"], "at": now - timedelta(minutes=5)}),
        ("UPDATE alerts SET status = 'dismissed' WHERE id = :a", {"a": ids["alert"]}),
        ("UPDATE security_situations SET decision_status = 'RESOLVED', closed_at = now() WHERE id = :s",
         {"s": ids["situation"]}),
    ])
    async with _client() as c:
        day = await _features(c, w, layers=["INCIDENT", "ALERT", "SITUATION"])
        week = await _features(c, w, layers=["INCIDENT"], hours=168)
        hour = await _features(c, w, layers=["INCIDENT"], hours=1)
        for bad in ({"hours": 0}, {"hours": 169}, {"layers": ["EVERYTHING"]}, {"site_id": "not-a-site"}):
            assert (await c.get(f"{BASE}/features", headers=w["h"][ADMIN], params=bad)).status_code == 422, bad
        assert (await c.get(f"{BASE}/features", headers=w["h"][ADMIN],
                            params={"site_id": str(uuid.uuid4())})).status_code == 200
    assert list(day["layers"]) == ["INCIDENT", "ALERT", "SITUATION"], "only the layers asked for"
    assert set(_by_id(day, "INCIDENT")) == {str(ids["incident"])}, "not the resolved one, nor the one from three days ago"
    assert day["layers"]["ALERT"] == [] and day["layers"]["SITUATION"] == []
    assert set(_by_id(week, "INCIDENT")) == {str(ids["incident"]), str(old)} and week["hours"] == 168
    assert set(_by_id(hour, "INCIDENT")) == {str(ids["incident"])}


async def test_an_incident_reported_from_the_ground_is_drawn_where_it_was_reported():
    w = await _world()
    g = await _ground(w)
    await _sql("INSERT INTO incident_status_history (tenant_id, incident_id, changed_by_user_id, from_status, "
               "    to_status, latitude, longitude) VALUES (:t,:i,:u,'open','investigating',:la,:lo)",
               {"t": w["tenant"], "i": g["ids"]["lost"], "u": w["users"][GUARD], "la": LAT + 0.0004, "lo": LNG})
    async with _client() as c:
        found = await _features(c, w, layers=["INCIDENT"])
    lost = _by_id(found, "INCIDENT")[str(g["ids"]["lost"])]
    assert lost["latitude"] == pytest.approx(LAT + 0.0004) and lost["detail"]["position_from"] == \
        "reported from the ground"
    assert found["without_position"] == {}, "its camera has no position; the guard who went there gave it one"


# ─── C. A guard's position ───────────────────────────────────────────────────

async def test_a_guard_is_shown_where_they_last_recorded_being_with_how_long_ago():
    w = await _world()
    g = await _ground(w)
    off_shift = uuid.uuid4()
    await _run([
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
         "VALUES (:i,:t,5,:e,'x','Gone Home')", {"i": off_shift, "t": w["tenant"], "e": f"o-{off_shift.hex[:8]}@map.test"}),
        ("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, "
         "    actual_end, check_in_lat, check_in_lon) VALUES (:t,:s,:g,:a,:b,:a,:b,:la,:lo)",
         {"t": w["tenant"], "s": w["site_a"], "g": off_shift, "a": g["now"] - timedelta(hours=10),
          "b": g["now"] - timedelta(hours=2), "la": LAT, "lo": LNG}),
    ])
    async with _client() as c:
        found = await _features(c, w, layers=["GUARD"])
    guards = _by_id(found, "GUARD")
    assert set(guards) == {str(w["users"][GUARD]), str(g["second"])}, "the one who clocked out is not on the map"
    scanner, second = guards[str(w["users"][GUARD])], guards[str(g["second"])]
    assert (scanner["label"], scanner["state"], scanner["site_name"]) == (f"Role {GUARD} User", "available", "Factory A")
    assert scanner["latitude"] == pytest.approx(LAT + 0.0009), "the scan ten minutes ago, not the check-in two hours ago"
    assert scanner["detail"]["position_source"] == "checkpoint scan" and scanner["detail"]["stale"] is False
    assert 590 <= scanner["detail"]["position_age_s"] <= 640
    assert (second["detail"]["position_source"], second["detail"]["stale"]) == ("shift check-in", True)
    assert 3 * 3600 - 10 <= second["detail"]["position_age_s"] <= 3 * 3600 + 60
    assert second["latitude"] == pytest.approx(LAT + 0.018)
    for guard in (scanner, second):
        assert guard["at"] and guard["detail"]["position_age_s"] is not None, "no position is given without its age"


async def test_a_guard_who_recorded_no_position_is_counted_and_one_sent_somewhere_or_in_trouble_says_so():
    w = await _world()
    g = await _ground(w)
    await _run([
        ("UPDATE shifts SET check_in_lat = NULL, check_in_lon = NULL WHERE id = :s", {"s": g["ids"]["shift2"]}),
        ("UPDATE incidents SET dispatched_guard_id = :g, dispatched_at = now() WHERE id = :i",
         {"g": w["users"][GUARD], "i": g["ids"]["incident"]}),
    ])
    async with _client() as c:
        found = await _features(c, w, layers=["GUARD"])
        assert found["without_position"] == {"GUARD": 1}
        (busy,) = found["layers"]["GUARD"]
        assert (busy["state"], busy["detail"]["busy_incident_id"]) == ("busy", str(g["ids"]["incident"]))
        await _sql("INSERT INTO man_down_events (tenant_id, guard_user_id, site_id, trigger, detected_at, escalate_at, "
                   "    latitude, longitude) VALUES (:t,:g,:s,'manual',now(),now(),:la,:lo)",
                   {"t": w["tenant"], "g": w["users"][GUARD], "s": w["site_a"], "la": LAT + 0.0002, "lo": LNG})
        found = await _features(c, w, layers=["GUARD"])
    (down,) = found["layers"]["GUARD"]
    assert (down["state"], down["detail"]["position_source"]) == ("emergency", "guard emergency")
    assert down["latitude"] == pytest.approx(LAT + 0.0002), "where the emergency was raised is the latest position"


# ─── D. What is near an incident ─────────────────────────────────────────────

async def test_what_is_near_an_incident_nearest_first_and_the_guards_however_far():
    w = await _world()
    g = await _ground(w)
    ids = g["ids"]
    async with _client() as c:
        await _draw(c, w, kind="EMERGENCY_POINT", name="Assembly by the gate", latitude=LAT + 0.0003, longitude=LNG)
        r = await c.get(f"{BASE}/around", headers=w["h"][OPERATOR], params={"kind": "INCIDENT", "id": str(ids["incident"])})
        assert r.status_code == 200, r.text
        near = r.json()
        wide = (await c.get(f"{BASE}/around", headers=w["h"][OPERATOR],
                            params={"kind": "INCIDENT", "id": str(ids["incident"]), "radius_m": 5000})).json()
        for bad in ({"kind": "CAMERA", "id": str(ids["gate"])}, {"kind": "INCIDENT", "id": "x"},
                    {"kind": "INCIDENT", "id": str(ids["incident"]), "radius_m": 5}, {"kind": "INCIDENT"}):
            assert (await c.get(f"{BASE}/around", headers=w["h"][ADMIN], params=bad)).status_code == 422, bad
        missing = await c.get(f"{BASE}/around", headers=w["h"][ADMIN], params={"kind": "INCIDENT", "id": str(uuid.uuid4())})
        assert missing.status_code == 404 and missing.json()["detail"] == "Incident not found"
        for kind, key in (("ALERT", "alert"), ("SITUATION", "situation")):
            other = await c.get(f"{BASE}/around", headers=w["h"][ADMIN], params={"kind": kind, "id": str(ids[key])})
            assert other.status_code == 200 and other.json()["subject"]["layer"] == kind
    assert (near["subject"]["id"], near["located"], near["radius_m"]) == (str(ids["incident"]), True, 300)
    assert [(f["label"], f["distance_m"]) for f in near["nearby"]["CAMERA"]] == [("Gate A", 0), ("Yard A", pytest.approx(200, abs=3))]
    assert [f["label"] for f in near["nearby"]["DRONE"]] == ["Drone A"] and near["nearby"]["DRONE"][0]["distance_m"] == \
        pytest.approx(56, abs=2)
    assert [f["label"] for f in near["nearby"]["CHECKPOINT"]] == ["Back fence A"]
    assert [(f["label"], f["distance_m"]) for f in near["nearby"]["PLACE"]] == [("Assembly by the gate", pytest.approx(33, abs=2))]
    assert [x["full_name"] for x in near["nearby"]["GUARD"]] == [f"Role {GUARD} User"], "the other is two kilometres off"
    first, second = near["nearest_guards"]
    assert (first["full_name"], first["distance_m"], first["position_source"], first["available"]) == (
        f"Role {GUARD} User", pytest.approx(100, abs=3), "checkpoint scan", True)
    assert (second["full_name"], second["stale"]) == ("Second Guard A", True) and second["distance_m"] > 1900
    assert near["note"] == positions.NOTE and near["not_shown"] == []
    assert len(wide["nearby"]["GUARD"]) == 2 and wide["radius_m"] == 5000


async def test_an_incident_with_no_position_says_so_and_still_lists_the_guards_at_its_site():
    w = await _world()
    g = await _ground(w)
    async with _client() as c:
        near = (await c.get(f"{BASE}/around", headers=w["h"][ADMIN],
                            params={"kind": "INCIDENT", "id": str(g["ids"]["lost"])})).json()
    assert near["located"] is False and near["subject"]["latitude"] is None
    assert all(near["nearby"][k] == [] for k in ("CAMERA", "DRONE", "CHECKPOINT", "PLACE", "GUARD"))
    assert len(near["nearest_guards"]) == 2 and all(x["distance_m"] is None for x in near["nearest_guards"]), \
        "who is on shift there is known; how far each is from somewhere with no position is not"


async def test_reading_what_is_near_changes_nothing_and_sends_nobody():
    w = await _world()
    g = await _ground(w)
    before = await _sql("SELECT dispatched_guard_id, status, updated_at FROM incidents WHERE id = :i",
                        {"i": g["ids"]["incident"]})
    async with _client() as c:
        for _ in range(3):
            await c.get(f"{BASE}/around", headers=w["h"][ADMIN],
                        params={"kind": "INCIDENT", "id": str(g["ids"]["incident"])})
            await _features(c, w)
    assert await _sql("SELECT dispatched_guard_id, status, updated_at FROM incidents WHERE id = :i",
                      {"i": g["ids"]["incident"]}) == before
    assert await _sql("SELECT 1 FROM audit_logs WHERE tenant_id = :t", {"t": w["tenant"]}) == [], \
        "looking at the map writes nothing"
    code = Path(api.__file__).read_text(encoding="utf-8")
    assert "dispatch" not in code.lower().replace("nobody is dispatched by it", ""), "the map dispatches nobody"


# ─── E. Who may see which layer ──────────────────────────────────────────────

async def test_who_may_open_the_map_and_who_may_draw_on_it():
    w = await _world()
    await _ground(w)
    async with _client() as c:
        for role in (ADMIN, MANAGER, SUPERVISOR, OPERATOR, VIEWER):
            assert (await c.get(f"{BASE}/features", headers=w["h"][role])).status_code == 200, role
        r = await c.get(f"{BASE}/features", headers=w["h"][GUARD])
        assert r.status_code == 403 and r.json()["detail"] == "Missing permission: sitemap:read"
        for role in (1, 7):
            headers = _auth(uuid.uuid4(), w["tenant"], role)
            for path in ("/features", "/layers", "/places", f"/around?kind=INCIDENT&id={uuid.uuid4()}"):
                assert (await c.get(f"{BASE}{path}", headers=headers)).status_code == 403, (role, path)
        assert (await c.get(f"{BASE}/features")).status_code == 401
        for role in (SUPERVISOR, OPERATOR, VIEWER):
            refused = await _draw(c, w, role, expect=403)
            assert refused["detail"] == "Missing permission: sitemap:manage"
        assert (await c.get(f"{BASE}/layers", headers=w["h"][OPERATOR])).json()["can_manage"] is False
    rows = await _sql("SELECT rp.role_id, p.code FROM role_permissions rp JOIN permissions p ON p.id = "
                      "rp.permission_id WHERE p.code LIKE 'sitemap:%'")
    held: dict[str, set[int]] = {}
    for row in rows:
        held.setdefault(row["code"], set()).add(row["role_id"])
    assert held == {"sitemap:read": {2, 3, 4, 6, 8}, "sitemap:manage": {2, 8}}


async def test_a_layer_is_left_out_for_someone_who_may_not_read_what_is_on_it_and_the_answer_says_so():
    w = await _world()
    await _ground(w)
    everything = frozenset(maps.PERMISSIONS)
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        without = await maps.features(db, everything - {"shift:read", "drone:read"}, None)
        nothing = await maps.features(db, frozenset(), None)
    assert {"GUARD", "DRONE", "DRONE_ZONE"}.isdisjoint(without["layers"])
    assert {n["layer"]: n["reason"] for n in without["not_shown"]} == {
        "GUARD": "You do not hold the permission shift:read.",
        "DRONE": "You do not hold the permission drone:read.",
        "DRONE_ZONE": "You do not hold the permission drone:read."}
    assert nothing["layers"] == {} and len(nothing["not_shown"]) == len(maps.LAYERS)


async def test_someone_held_to_a_site_sees_that_site_on_every_layer_and_another_organisation_sees_none_of_it():
    w, other = await _world(), await _world()
    a = await _ground(w, site="site_a", tag="A")
    b = await _ground(w, site="site_b", tag="B", lat=1.3500, lng=103.8500)
    await _ground(other, tag="X")
    async with _client() as c:
        everything = await _features(c, w)
        mine = await _features(c, w, SUPERVISOR)
        assert (await c.get(f"{BASE}/features", headers=w["h"][SUPERVISOR],
                            params={"site_id": str(w["site_b"])})).status_code == 404
        hidden = await c.get(f"{BASE}/around", headers=w["h"][SUPERVISOR],
                             params={"kind": "INCIDENT", "id": str(b["ids"]["incident"])})
        assert hidden.status_code == 404
        theirs = await _features(c, other)
        assert (await c.get(f"{BASE}/around", headers=other["h"][ADMIN],
                            params={"kind": "INCIDENT", "id": str(a["ids"]["incident"])})).status_code == 404
    for layer in ("SITE", "CAMERA", "GUARD", "INCIDENT", "ALERT", "SITUATION", "DRONE", "CHECKPOINT"):
        assert {f["site_name"] for f in everything["layers"][layer]} == {"Factory A", "Factory B"}, layer
        assert {f["site_name"] for f in mine["layers"][layer]} == {"Factory A"}, layer
        assert {f["label"][-1] for f in theirs["layers"][layer] if layer not in ("SITE", "GUARD")} <= {"X"}, layer
    assert len(theirs["layers"]["CAMERA"]) == 2 and len(everything["layers"]["CAMERA"]) == 4


# ─── F. The places of a site ─────────────────────────────────────────────────

async def test_a_place_is_a_point_an_outline_or_a_part_of_a_building():
    w = await _world()
    g = await _ground(w)
    square = [[LAT, LNG], [LAT, LNG + 0.001], [LAT + 0.001, LNG + 0.001], [LAT + 0.001, LNG]]
    async with _client() as c:
        gate = await _draw(c, w)
        assert (gate["layer"], gate["label"], gate["state"], gate["latitude"], gate["outline"]) == (
            "PLACE", "North Gate", "gate", LAT, None)
        building = await _draw(c, w, MANAGER, kind="BUILDING", name=" Warehouse 1 ", latitude=None, longitude=None,
                               polygon=square,
                               description="  Cold store and dispatch.  ")
        assert building["label"] == "Warehouse 1" and building["outline"] == square
        assert building["latitude"] == pytest.approx(LAT + 0.0005), "drawn from the middle of its outline"
        assert building["detail"]["description"] == "Cold store and dispatch." and building["detail"]["has_point"] is False
        floor = await _draw(c, w, kind="FLOOR", name="Level 2", latitude=None, longitude=None, level=2,
                            parent_id=building["id"])
        assert (floor["detail"]["parent_name"], floor["detail"]["level"], floor["latitude"]) == ("Warehouse 1", 2, None)
        door = await _draw(c, w, kind="ACCESS_POINT", name="Server room door", door_id=str(g["ids"]["door"]),
                           latitude=LAT + 0.0002)
        assert door["detail"]["door_name"] == "Server room A" and door["detail"]["last_door_event"] is None

        for bad, why in (
            ({"kind": "CASTLE"}, "Unknown kind of place"),
            ({"name": "No position", "latitude": None, "longitude": None}, "needs a position or an outline"),
            ({"name": "Half a position", "longitude": None}, "both a latitude and a longitude"),
            ({"name": "Two points", "polygon": square[:2]}, "three or more"),
            ({"name": "Off the earth", "polygon": [[95, 0], [0, 0], [1, 1]]}, "three or more"),
            ({"kind": "FLOOR", "name": "A floor of nothing", "latitude": None, "longitude": None}, "needs a position"),
            ({"name": "Part of a gate", "parent_id": gate["id"]}, "a part of a building"),
            ({"name": "Part of nowhere", "parent_id": str(uuid.uuid4())}, "an active place of the same site"),
            ({"kind": "ZONE", "name": "A zone with a door", "door_id": str(g["ids"]["door"])}, "Only an access point"),
            ({"kind": "GATE", "name": "Somebody else's door", "door_id": str(uuid.uuid4())}, "not a door of this site"),
        ):
            refused = await _draw(c, w, expect=422, **bad)
            assert why in refused["detail"], (bad, refused)
        for bad in ({"name": ""}, {"latitude": 91}, {"level": 999}, {"colour": "red"}, {"polygon": [[0, 0]] * 201}):
            assert (await c.post(f"{BASE}/places", headers=w["h"][ADMIN], json={
                "site_id": str(w["site_a"]), "kind": "GATE", "name": "X", "latitude": LAT, "longitude": LNG,
                **bad})).status_code == 422, bad
        assert (await _draw(c, w, expect=409, name="north gate"))["detail"] == \
            "This site already has an active place of that kind with that name."
        assert (await _draw(c, w, expect=409, kind="GATE", name="Another way in", door_id=str(g["ids"]["door"])))[
            "detail"] == "That door is already at another place of this site."
        assert (await _draw(c, w, expect=404, site_id=str(uuid.uuid4())))["detail"] == "Site not found"
        assert (await _draw(c, w, name="North Gate", site_id=str(w["site_b"]), latitude=1.35, longitude=103.85))[
            "site_name"] == "Factory B", "the same name at another site is another place"

        listed = (await c.get(f"{BASE}/places", headers=w["h"][VIEWER], params={"site_id": str(w["site_a"])})).json()
        on_map = await _features(c, w, layers=["PLACE"], site_id=str(w["site_a"]))
    assert [(p["detail"]["kind"], p["label"]) for p in listed["items"]] == [
        ("ACCESS_POINT", "Server room door"), ("BUILDING", "Warehouse 1"), ("FLOOR", "Level 2"), ("GATE", "North Gate")]
    assert listed["can_manage"] is False and listed["kinds"] == list(maps.PLACE_KINDS)
    assert [p["label"] for p in on_map["layers"]["PLACE"]] == ["Server room door", "Warehouse 1", "North Gate"]
    assert on_map["without_position"] == {"PLACE": 1}, "a floor is a level of a building and is not drawn by itself"


async def test_a_place_is_changed_by_what_is_given_and_nothing_else():
    w = await _world()
    async with _client() as c:
        building = await _draw(c, w, kind="BUILDING", name="Warehouse 1")
        gate = await _draw(c, w, description="The main way in.")
        url = f"{BASE}/places/{gate['id']}"
        r = await c.patch(url, headers=w["h"][MANAGER], json={"name": " South Gate ", "parent_id": building["id"]})
        assert r.status_code == 200, r.text
        changed = r.json()
        assert (changed["label"], changed["detail"]["parent_name"], changed["latitude"]) == ("South Gate", "Warehouse 1", LAT)
        assert changed["detail"]["description"] == "The main way in.", "what was not given is as it was"
        moved = (await c.patch(url, headers=w["h"][ADMIN], json={"latitude": LAT + 0.001, "longitude": LNG,
                                                                 "parent_id": None, "description": None})).json()
        assert (moved["latitude"], moved["detail"]["parent_id"], moved["detail"]["description"]) == (
            pytest.approx(LAT + 0.001), None, None)
        for bad, code, why in (
            ({}, 422, "Nothing was given"), ({"name": "  "}, 422, "has a name"),
            ({"latitude": None, "longitude": None}, 422, "needs a position or an outline"),
            ({"parent_id": gate["id"]}, 422, "a part of itself"), ({"name": "Warehouse 1"}, 200, None),
            ({"kind": "ZONE"}, 422, None), ({"site_id": str(w["site_b"])}, 422, None),
        ):
            r = await c.patch(url, headers=w["h"][ADMIN], json=bad)
            assert r.status_code == code, (bad, r.text)
            if why:
                assert why in r.json()["detail"], bad
        taken = await c.patch(f"{BASE}/places/{building['id']}", headers=w["h"][ADMIN], json={"name": "warehouse 1"})
        assert taken.status_code == 200, "its own name, written another way, is still its own"
        assert (await c.patch(f"{BASE}/places/{uuid.uuid4()}", headers=w["h"][ADMIN], json={"name": "X"})).status_code == 404
        assert (await c.patch(url, headers=w["h"][OPERATOR], json={"name": "Mine"})).status_code == 403
    rows = await _audit(w, "sitemap.place.update")
    assert rows[0]["detail"]["changed"] == ["name", "parent_id"] and rows[0]["user_id"] == w["users"][MANAGER]


async def test_a_place_is_retired_and_restored_and_never_removed():
    w = await _world()
    async with _client() as c:
        gate = await _draw(c, w)
        url = f"{BASE}/places/{gate['id']}"
        retired = await c.post(f"{url}/retire", headers=w["h"][MANAGER])
        assert retired.status_code == 200 and retired.json() == {"is_active": False}
        assert (await c.post(f"{url}/retire", headers=w["h"][ADMIN])).status_code == 409
        assert (await c.patch(url, headers=w["h"][ADMIN], json={"name": "X"})).status_code == 409
        assert (await _features(c, w, layers=["PLACE"]))["layers"]["PLACE"] == []
        assert (await c.get(f"{BASE}/places", headers=w["h"][ADMIN])).json()["items"] == []
        kept = (await c.get(f"{BASE}/places", headers=w["h"][ADMIN], params={"include_retired": "true"})).json()["items"]
        assert [(p["label"], p["detail"]["is_active"]) for p in kept] == [("North Gate", False)]
        assert (await c.get(f"{BASE}/places", headers=w["h"][VIEWER],
                            params={"include_retired": "true"})).json()["items"] == [], "retired places are the drawer's to see"
        again = await _draw(c, w)
        assert again["id"] != gate["id"], "the name is free once its place is retired"
        clash = await c.post(f"{url}/restore", headers=w["h"][ADMIN])
        assert clash.status_code == 409 and "already has an active place" in clash.json()["detail"]
        await c.post(f"{BASE}/places/{again['id']}/retire", headers=w["h"][ADMIN])
        restored = await c.post(f"{url}/restore", headers=w["h"][ADMIN])
        assert restored.json() == {"is_active": True}
        assert (await c.post(f"{url}/restore", headers=w["h"][ADMIN])).status_code == 409
        for role in (SUPERVISOR, OPERATOR, VIEWER):
            assert (await c.post(f"{url}/retire", headers=w["h"][role])).status_code == 403
        assert (await c.delete(url, headers=w["h"][ADMIN])).status_code == 405, "there is no removing a place"
    for action, n in (("sitemap.place.create", 2), ("sitemap.place.retire", 2), ("sitemap.place.restore", 1)):
        rows = await _audit(w, action)
        assert len(rows) == n and all(r["detail"]["name"] == "North Gate" and r["row_hash"] for r in rows), action
    assert (await _sql("SELECT count(*) AS n FROM site_places WHERE tenant_id = :t", {"t": w["tenant"]}))[0]["n"] == 2


async def test_a_door_at_a_place_shows_its_last_event_to_someone_who_may_read_door_events():
    w = await _world()
    g = await _ground(w)
    async with _client() as c:
        door = await _draw(c, w, kind="ACCESS_POINT", name="Server room door", door_id=str(g["ids"]["door"]))
        await _sql("INSERT INTO access_events (tenant_id, door_id, event_type, occurred_at) "
                   "VALUES (:t,:d,'granted', now() - interval '2 hours'), (:t,:d,'forced', now() - interval '3 minutes')",
                   {"t": w["tenant"], "d": g["ids"]["door"]})
        (place,) = (await _features(c, w, layers=["PLACE"]))["layers"]["PLACE"]
        assert (place["state"], place["detail"]["last_door_event"]) == ("alert", "forced") and place["at"]
        await _sql("UPDATE access_events SET occurred_at = now() - interval '40 minutes' WHERE event_type = 'forced' "
                   " AND door_id = :d", {"d": g["ids"]["door"]})
        (place,) = (await _features(c, w, layers=["PLACE"]))["layers"]["PLACE"]
        assert (place["state"], place["detail"]["last_door_event"]) == ("access_point", "forced"), \
            "forty minutes on it is the last thing that happened there, and no longer an alarm"
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        blind = await maps.features(db, frozenset(maps.PERMISSIONS) - {"access:read"}, None, layers=["PLACE"])
    assert blind["layers"]["PLACE"][0]["detail"]["last_door_event"] is None, \
        "the place is on the map; what happened at its door is for someone who may read door events"
    assert door["id"] == blind["layers"]["PLACE"][0]["id"]


async def test_an_api_key_and_a_support_session_draw_nothing():
    w = await _world()
    from fastapi import HTTPException
    for not_a_person, why in (
        (TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True),
         "not by an API key"),
        (TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN,
                      support_session_id=str(uuid.uuid4())), "not from a support session"),
    ):
        with pytest.raises(HTTPException) as refused:
            api._a_person(not_a_person)
        assert refused.value.status_code == 403 and why in refused.value.detail
    key = TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key
    try:
        async with _client() as c:
            r = await c.post(f"{BASE}/places", json={"site_id": str(w["site_a"]), "kind": "GATE", "name": "North Gate",
                                                    "latitude": LAT, "longitude": LNG})
            assert r.status_code == 403 and "not by an API key" in r.json()["detail"]
            assert (await c.get(f"{BASE}/features")).status_code == 200, "an integration may read the map it may read"
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    assert await _sql("SELECT 1 FROM site_places WHERE tenant_id = :t", {"t": w["tenant"]}) == []


async def test_what_the_application_role_and_the_database_refuse_of_a_place():
    w, other = await _world(), await _world()
    async with _client() as c:
        gate = await _draw(c, w)
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        for statement in ("DELETE FROM site_places WHERE id = :p", "TRUNCATE site_places"):
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(statement), {"p": gate["id"]} if ":p" in statement else {})
            await db.rollback()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(other["tenant"])})
        assert (await db.execute(text("SELECT count(*) FROM site_places"))).scalar() == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(text("INSERT INTO site_places (tenant_id, site_id, kind, name, latitude, longitude) "
                                  "VALUES (:t, :s, 'GATE', 'Planted', 1.3, 103.8)"),
                             {"t": w["tenant"], "s": w["site_a"]})
        await db.rollback()
    add = ("INSERT INTO site_places (tenant_id, site_id, kind, name{more}) VALUES (:t, :s, :k, :n{values})")
    for kind, name, more, values, constraint in (
        ("GATE", "Nowhere", "", "", "ck_place_somewhere"),
        ("CASTLE", "A castle", ", latitude, longitude", ", 1.3, 103.8", "ck_place_kind"),
        ("GATE", " ", ", latitude, longitude", ", 1.3, 103.8", "ck_place_name"),
        ("GATE", "Half", ", latitude", ", 1.3", "ck_place_point"),
        ("GATE", "Off the earth", ", latitude, longitude", ", 95, 103.8", "ck_place_point"),
        ("ZONE", "Two points", ", polygon", ", '[[1,2],[3,4]]'::jsonb", "ck_place_outline"),
        ("ZONE", "Not a list", ", polygon", ", '{\"a\": 1}'::jsonb", "ck_place_outline"),
        ("FLOOR", "Too high", ", latitude, longitude, level", ", 1.3, 103.8, 500", "ck_place_level"),
        ("ZONE", "A zone with a door", ", latitude, longitude, door_id", ", 1.3, 103.8, gen_random_uuid()",
         "ck_place_door|foreign key"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(add.format(more=more, values=values), {"t": w["tenant"], "s": w["site_a"], "k": kind, "n": name})
    row = (await _sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'site_places'"))[0]
    assert row["relrowsecurity"] and row["relforcerowsecurity"]
    assert "REVOKE ALL ON site_places FROM svc_app" in MIGRATION and "GRANT ALL" not in MIGRATION
    assert "GRANT SELECT, INSERT, UPDATE ON site_places TO svc_app" in MIGRATION


def _needs(route) -> set[str]:
    found: set[str] = set()

    def walk(dep):
        if "require_permission" in getattr(dep.call, "__qualname__", ""):
            found.update(c.cell_contents for c in (dep.call.__closure__ or ()) if isinstance(c.cell_contents, str))
        for sub in dep.dependencies:
            walk(sub)

    for d in route.dependant.dependencies:
        walk(d)
    return found


def test_every_route_needs_the_permission_and_every_change_needs_more():
    served = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if path.startswith(BASE) and getattr(route, "endpoint", None):
                for method in route.methods - {"HEAD"}:
                    served[(method, path.replace(":uuid", ""))] = _needs(route)
    assert set(served) == {
        ("GET", f"{BASE}/layers"), ("GET", f"{BASE}/features"), ("GET", f"{BASE}/around"), ("GET", f"{BASE}/places"),
        ("POST", f"{BASE}/places"), ("PATCH", f"{BASE}/places/{{place_id}}"),
        ("POST", f"{BASE}/places/{{place_id}}/retire"), ("POST", f"{BASE}/places/{{place_id}}/restore")}
    for (method, path), needs in served.items():
        assert "sitemap:read" in needs, path
        assert ("sitemap:manage" in needs) == (method != "GET"), f"{method} {path}"
        assert "drone" not in path
    assert not [m for m, _ in served if m in ("DELETE", "PUT")]
