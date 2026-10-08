"""Device health read from what devices report, and the register of security assets.

  A — One device's state, with nothing running: each kind, from the facts on its row
  B — Every device of an organisation, and who sees which
  C — What is kept when a state changes, and how long a device was down
  D — The register: adding, changing, retiring
  E — Devices the platform knows, put into the register
  F — What the application role and the database refuse

Every request goes through the real app over ASGI, as svc_app with RLS
enforced. The scheduler's pass is run as the application's role, the way the
scheduler runs it.

The claims, each with tests: a reading is made of what the platform is told
and names what it does not measure; what is not known is not called fine; a
sensor reading a dangerous value is a working sensor; an asset is a record and
changes no device; an asset is retired and never removed.
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
from app.routers import security_assets as api
from app.services import device_health as health
from app.services import maintenance as work
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _drone, _run, _sql, _world
from tests.test_incident_responses import _guard
from tests.test_investigation_search import _audit

BASE = "/api/v1/security-assets"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0150_security_assets.py").read_text(encoding="utf-8")
TABLES = ("asset_register", "device_health_changes", "maintenance_schedules", "maintenance_work_orders")
CLIENT = 7
NOW = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)


def _ago(**span) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**span)


async def _camera(w: dict, name: str = "Gate 1", *, site: str | None = "site_a", streams: tuple = ("online",),
                  active: bool = True) -> uuid.UUID:
    cid = uuid.uuid4()
    stmts = [("INSERT INTO cameras (id, tenant_id, name, site_id, is_active, location) VALUES (:i,:t,:n,:s,:a,'North fence')",
              {"i": cid, "t": w["tenant"], "n": name, "s": w[site] if site else None, "a": active})]
    for index, status in enumerate(streams):
        stmts.append(("INSERT INTO streams (tenant_id, camera_id, url, status, last_frame_at) VALUES (:t,:c,:u,:s,:f)",
                      {"t": w["tenant"], "c": cid, "u": f"rtsp://cam/{cid.hex[:6]}/{index}", "s": status,
                       "f": _ago(seconds=20) if status != "offline" else _ago(hours=6)}))
    await _run(stmts)
    return cid


async def _hit(w: dict, camera, kind: str, *, minutes_ago: float) -> None:
    await _sql("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, occurred_at) VALUES (:t,:c,:k,:at)",
               {"t": w["tenant"], "c": camera, "k": kind, "at": _ago(minutes=minutes_ago)})


async def _nvr(w: dict, name: str = "Recorder 1", *, status: str | None = "ok", probed_minutes_ago: float | None = 10) -> uuid.UUID:
    nid = uuid.uuid4()
    await _sql("INSERT INTO nvr_connections (id, tenant_id, name, host, username, password_enc, adapter_type, "
               "last_probe_at, last_probe_status) VALUES (:i,:t,:n,'10.0.0.9','svc','x','generic',:at,:s)",
               {"i": nid, "t": w["tenant"], "n": name, "s": status,
                "at": _ago(minutes=probed_minutes_ago) if probed_minutes_ago is not None else None})
    return nid


async def _sensor(w: dict, name: str = "Server room temperature", *, site: str = "site_a", interval: int = 60,
                  read_seconds_ago: float | None = 30, status: str = "normal") -> uuid.UUID:
    sid = uuid.uuid4()
    await _sql("INSERT INTO iot_sensors (id, tenant_id, site_id, name, sensor_type, expected_interval_seconds, "
               "last_reading_at, current_status, location) VALUES (:i,:t,:s,:n,'temperature',:e,:at,:st,'Rack 2')",
               {"i": sid, "t": w["tenant"], "s": w[site], "n": name, "e": interval, "st": status,
                "at": _ago(seconds=read_seconds_ago) if read_seconds_ago is not None else None})
    return sid


async def _gateway(w: dict, name: str = "Edge 1", *, site: str = "site_a", status: str = "ONLINE",
                   seen_seconds_ago: float | None = 10) -> uuid.UUID:
    gid = uuid.uuid4()
    await _sql("INSERT INTO drone_edge_gateways (id, tenant_id, site_id, name, code, status, last_seen_at, "
               "heartbeat_timeout_seconds) VALUES (:i,:t,:s,:n,:c,:st,:at,120)",
               {"i": gid, "t": w["tenant"], "s": w[site], "n": name, "c": f"GW-{gid.hex[:6]}", "st": status,
                "at": _ago(seconds=seen_seconds_ago) if seen_seconds_ago is not None else None})
    return gid


async def _panel(w: dict, name: str = "Panel A", *, site: str = "site_a", status: str = "online",
                 contact_minutes_ago: float | None = 5) -> uuid.UUID:
    pid = uuid.uuid4()
    await _sql("INSERT INTO alarm_panels (id, tenant_id, site_id, name, model, serial_number, status, last_contact_at) "
               "VALUES (:i,:t,:s,:n,'DSC PowerSeries','SN-7781',:st,:at)",
               {"i": pid, "t": w["tenant"], "s": w[site], "n": name, "st": status,
                "at": _ago(minutes=contact_minutes_ago) if contact_minutes_ago is not None else None})
    return pid


async def _look(now: datetime | None = None) -> dict:
    """The scheduler's pass, as the application's role."""
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
    return await work.run(AsyncSessionLocal, now)


async def _kept(w: dict, device) -> list[str]:
    rows = await _sql("SELECT state FROM device_health_changes WHERE tenant_id = :t AND device_id = :d "
                      "ORDER BY observed_at, id", {"t": w["tenant"], "d": device})
    return [r["state"] for r in rows]


async def _add(c, w: dict, *, who: int = MANAGER, **body):
    return await c.post(BASE, headers=w["h"][who], json={"kind": "UPS", "name": "UPS rack 2", **body})


async def _added(c, w: dict, **body) -> dict:
    r = await _add(c, w, **body)
    assert r.status_code == 201, r.text
    return r.json()


# ─── A. One device's state ───────────────────────────────────────────────────

def _cam(**over) -> dict:
    return {"is_active": True, "n_streams": 1, "n_online": 1, "n_degraded": 0, "last_frame_at": NOW,
            "last_event_type": None, "last_event_at": None, "disconnects_24h": 0, **over}


def test_a_camera_is_read_from_its_streams_and_the_disconnections_recorded_of_it():
    assert health.camera(_cam(), NOW)["state"] == "OK" and health.camera(_cam(), NOW)["reasons"] == []
    off = health.camera(_cam(is_active=False, n_online=0), NOW)
    assert (off["state"], off["reasons"]) == ("OFF", ["Switched off in the platform."])
    # No stream is not "fine": nothing says how it is.
    none = health.camera(_cam(n_streams=0, n_online=0), NOW)
    assert (none["state"], none["reasons"]) == ("NOT_KNOWN", ["No stream is set up for it."])
    went = NOW - timedelta(hours=3)
    down = health.camera(_cam(n_online=0, last_event_type="stream_disconnected", last_event_at=went), NOW)
    assert (down["state"], down["reasons"], down["facts"]["went_offline_at"]) == ("DOWN", ["Every stream is offline."], went)
    # Offline with no record of going: down, and when is not made up.
    unknown_when = health.camera(_cam(n_online=0, last_event_type="stream_reconnected", last_event_at=went), NOW)
    assert unknown_when["state"] == "DOWN" and "went_offline_at" not in unknown_when["facts"]
    assert health.camera(_cam(n_streams=2, n_online=1, n_degraded=1), NOW)["reasons"] == ["A stream is degraded."]
    # One of two streams up is a camera that is giving a picture.
    assert health.camera(_cam(n_streams=2, n_online=1), NOW)["state"] == "OK"
    steady = health.camera(_cam(disconnects_24h=health.UNSTEADY_AT - 1), NOW)
    unsteady = health.camera(_cam(disconnects_24h=health.UNSTEADY_AT), NOW)
    assert steady["state"] == "OK" and unsteady["state"] == "DEGRADED"
    assert unsteady["reasons"] == ["It disconnected 5 times in the last 24 hours."]


def test_a_recorder_a_sensor_a_gateway_and_a_panel_are_read_from_what_they_last_said():
    probe = {"is_active": True, "last_probe_at": NOW - timedelta(minutes=5), "last_probe_status": "ok"}
    assert health.recorder(probe, NOW)["state"] == "OK"
    assert health.recorder({**probe, "last_probe_status": "error"}, NOW)["reasons"] == ["The last probe failed."]
    # A probe from yesterday says how it was. Neither a success nor a failure that old is carried forward.
    for was in ("ok", "error"):
        old = health.recorder({**probe, "last_probe_status": was, "last_probe_at": NOW - timedelta(days=2)}, NOW)
        assert old["state"] == "NOT_KNOWN" and "not been probed for more than a day" in old["reasons"][0]
    assert health.recorder({**probe, "last_probe_at": None}, NOW)["reasons"] == ["It has never been probed."]
    assert health.recorder({**probe, "is_active": False}, NOW)["state"] == "OFF"

    reading = {"is_active": True, "last_reading_at": NOW - timedelta(seconds=90), "expected_interval_seconds": 60}
    assert health.sensor(reading, NOW)["state"] == "OK", "late by less than twice the interval is not silence"
    silent = health.sensor({**reading, "last_reading_at": NOW - timedelta(seconds=121)}, NOW)
    assert silent["state"] == "DOWN" and "more than twice the time one is expected in" in silent["reasons"][0]
    assert health.sensor({**reading, "last_reading_at": None}, NOW)["state"] == "NOT_KNOWN"
    # What a reading says is not the sensor's health, and is not even looked at.
    assert "current_status" not in reading and "status" not in health.sensor(reading, NOW)["facts"]

    seen = {"is_active": True, "status": "ONLINE", "last_seen_at": NOW - timedelta(seconds=30),
            "heartbeat_timeout_seconds": 120, "storage_free_pct": 40, "buffer_depth": 0}
    assert health.gateway(seen, NOW)["state"] == "OK"
    assert health.gateway({**seen, "last_seen_at": NOW - timedelta(seconds=121)}, NOW)["state"] == "DOWN"
    assert health.gateway({**seen, "status": "DEGRADED"}, NOW)["reasons"] == ["It reports degraded."]
    assert health.gateway({**seen, "status": "OFFLINE"}, NOW)["state"] == "DOWN"
    assert health.gateway({**seen, "status": "UNKNOWN"}, NOW)["state"] == "NOT_KNOWN"
    assert health.gateway({**seen, "last_seen_at": None}, NOW)["reasons"] == ["It has never reported."]
    # With no time a heartbeat is expected in, lateness cannot be judged, and is not.
    assert health.gateway({**seen, "heartbeat_timeout_seconds": None, "last_seen_at": NOW - timedelta(days=3)}, NOW)["state"] == "OK"

    said = {"is_active": True, "status": "online", "last_contact_at": NOW - timedelta(days=9)}
    quiet = health.panel(said, NOW)
    assert quiet["state"] == "OK" and quiet["facts"]["last_contact_at"] == said["last_contact_at"], "a quiet panel is not a dead one"
    assert health.panel({**said, "status": "offline"}, NOW)["reasons"] == ["It reported itself offline."]
    assert health.panel({**said, "last_contact_at": None}, NOW)["state"] == "NOT_KNOWN"


def test_a_drone_is_read_from_its_heartbeat_what_it_reports_and_its_parts():
    fine = {"status": "READY", "last_heartbeat_at": NOW - timedelta(seconds=5), "heartbeat_timeout_seconds": 30,
            "battery_level": 80, "battery_health": 95, "next_maintenance_at": None, "communication_status": "OK",
            "gps_status": "OK", "camera_status": "OK", "storage_status": "OK"}
    ok = health.drone(fine, NOW)
    assert ok["state"] == "OK" and ok["facts"]["battery_level"] == 80
    assert health.drone({**fine, "status": "DISABLED"}, NOW)["reasons"] == ["Disabled."]
    assert health.drone({**fine, "status": "MAINTENANCE"}, NOW) | {"facts": {}} == {
        "state": "OFF", "reasons": ["In maintenance."], "facts": {}}
    assert health.drone({**fine, "last_heartbeat_at": None}, NOW)["state"] == "NOT_KNOWN"
    late = health.drone({**fine, "last_heartbeat_at": NOW - timedelta(seconds=31)}, NOW)
    assert (late["state"], late["reasons"]) == ("DOWN", ["No heartbeat within the time one is expected in."])
    assert health.drone({**fine, "status": "COMMUNICATION_LOST"}, NOW)["reasons"] == ["It reports communication lost."]
    assert health.drone({**fine, "status": "CRITICAL"}, NOW)["state"] == "DOWN"
    parts = health.drone({**fine, "gps_status": "FAULT", "storage_status": "WARNING", "camera_status": "UNKNOWN"}, NOW)
    assert (parts["state"], parts["reasons"]) == ("DEGRADED", ["GPS: fault.", "Storage: warning."])
    assert health.drone({**fine, "status": "WARNING"}, NOW)["reasons"] == ["It reports a warning."]
    # A battery level is given as a fact. No threshold here turns it into a verdict.
    assert health.drone({**fine, "battery_level": 4, "battery_health": 20}, NOW)["state"] == "OK"


def test_nothing_is_claimed_that_nothing_measures():
    assert health.NOT_MEASURED == ("Frame rate", "Latency", "Packet loss",
                                   "The quality of the picture: darkness, glare, focus", "Gaps in a recording")
    for word in ("frame rate", "latency", "packet loss"):
        assert word in health.NOTE
    code = Path(health.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    # No figure for any of them is computed, stored or returned anywhere.
    for name in ("fps", "frame_rate", "latency_ms", "packet_loss", "jitter", "bitrate"):
        assert name not in code and name not in MIGRATION, name
    assert set(health.STATES) == {"OK", "DEGRADED", "DOWN", "NOT_KNOWN", "OFF"}
    assert health.LOOK_EVERY_S == 300 and "every five minutes" in health.AVAILABILITY_NOTE


def test_how_long_a_device_was_down_is_counted_from_what_was_kept_and_no_further_back():
    hour = timedelta(hours=1)
    start = NOW - 24 * hour
    assert health.down_time([], start, NOW) == {"known_from": None, "known_seconds": 0, "down_seconds": 0, "times_down": 0}
    changes = [{"state": "OK", "observed_at": NOW - 10 * hour}, {"state": "DOWN", "observed_at": NOW - 6 * hour},
               {"state": "OK", "observed_at": NOW - 4 * hour}, {"state": "DOWN", "observed_at": NOW - hour}]
    known = health.down_time(changes, start, NOW)
    # Nothing is known of the fourteen hours before the first reading that was kept, and nothing is assumed of them.
    assert known == {"known_from": NOW - 10 * hour, "known_seconds": 36000, "down_seconds": 3 * 3600, "times_down": 2}
    # A state that began before the period counts only from where the period starts.
    before = [{"state": "DOWN", "observed_at": NOW - 30 * hour}, {"state": "OK", "observed_at": NOW - 20 * hour}]
    assert health.down_time(before, start, NOW) == {"known_from": start, "known_seconds": 86400,
                                                    "down_seconds": 4 * 3600, "times_down": 1}
    assert health.down_time([{"state": "OK", "observed_at": NOW - hour}], start, NOW)["down_seconds"] == 0


# ─── B. Every device of an organisation ──────────────────────────────────────

async def test_every_device_is_read_what_wants_somebody_first_and_what_is_not_measured_is_named():
    w = await _world()
    fine = await _camera(w, "Gate 1")
    dark = await _camera(w, "Loading bay", streams=("offline",))
    await _hit(w, dark, "stream_disconnected", minutes_ago=200)
    unsteady = await _camera(w, "Car park")
    for minutes in (30, 90, 150, 300, 600):
        await _hit(w, unsteady, "stream_disconnected", minutes_ago=minutes)
        await _hit(w, unsteady, "stream_reconnected", minutes_ago=minutes - 1)
    bare = await _camera(w, "New camera", streams=())
    far = await _camera(w, "B gate", site="site_b", streams=("offline",))
    recorder = await _nvr(w, status="error")
    hot = await _sensor(w, status="critical")
    silent = await _sensor(w, "Flood sensor", read_seconds_ago=3600)
    panel = await _panel(w)
    gateway = await _gateway(w, seen_seconds_ago=None)
    async with _client() as c:
        made = await _drone(c, w, code="D-1")
        r = await c.get(f"{BASE}/health", headers=w["h"][OPERATOR])
        assert r.status_code == 200, r.text
        got = r.json()
        by = {i["device_id"]: i for i in got["items"]}
        assert {k: by[str(v)]["state"] for k, v in {
            "fine": fine, "dark": dark, "unsteady": unsteady, "bare": bare, "far": far, "recorder": recorder,
            "hot": hot, "silent": silent, "panel": panel, "gateway": gateway}.items()} == {
            "fine": "OK", "dark": "DOWN", "unsteady": "DEGRADED", "bare": "NOT_KNOWN", "far": "DOWN",
            "recorder": "DOWN", "hot": "OK", "silent": "DOWN", "panel": "OK", "gateway": "NOT_KNOWN"}
        # A sensor reading a dangerous value is a sensor that works.
        assert by[str(hot)]["reasons"] == []
        # A drone that has never flown or reported is not known, not fine.
        assert by[made["id"]]["state"] == "NOT_KNOWN" and by[made["id"]]["kind"] == "DRONE"
        states = [i["state"] for i in got["items"]]
        assert states == sorted(states, key=health.ATTENTION.get), "what is down comes first"
        # Since when: the camera's own record of going offline.
        went = datetime.fromisoformat(by[str(dark)]["since"])
        assert timedelta(minutes=199) < datetime.now(timezone.utc) - went < timedelta(minutes=201)
        assert by[str(dark)]["since_is_when_first_read"] is False and by[str(far)]["since"] is None
        assert by[str(unsteady)]["reasons"] == ["It disconnected 5 times in the last 24 hours."]
        assert by[str(recorder)]["site_id"] is None and by[str(fine)]["site_name"] == "Factory A"
        assert got["summary"]["devices"] == 11 and got["summary"]["by_state"] == {
            "OK": 3, "DEGRADED": 1, "DOWN": 4, "NOT_KNOWN": 3, "OFF": 0}
        cameras = next(k for k in got["summary"]["by_kind"] if k["kind"] == "CAMERA")
        assert (cameras["label"], cameras["devices"], cameras["DOWN"], cameras["OK"]) == ("Camera", 5, 2, 1)
        assert got["not_measured"] == list(health.NOT_MEASURED) and got["note"] == health.NOTE

        down = (await c.get(f"{BASE}/health", headers=w["h"][OPERATOR], params={"state": "DOWN", "kind": "CAMERA"})).json()
        assert {i["device_id"] for i in down["items"]} == {str(dark), str(far)}
        assert down["summary"]["devices"] == 5, "the counts are of what was looked at, not of what is shown"
        at_b = (await c.get(f"{BASE}/health", headers=w["h"][OPERATOR], params={"site_id": str(w["site_b"])})).json()
        assert [i["device_id"] for i in at_b["items"]] == [str(far)], "and a recorder, which has no site, is not at a site"
        for bad in ({"state": "BROKEN"}, {"kind": "TOASTER"}):
            assert (await c.get(f"{BASE}/health", headers=w["h"][OPERATOR], params=bad)).status_code == 422

        # Held to site A: nothing of site B, and no recorder — it belongs to the organisation, not to a site.
        mine = (await c.get(f"{BASE}/health", headers=w["h"][SUPERVISOR])).json()
        seen = {i["device_id"] for i in mine["items"]}
        assert str(far) not in seen and str(recorder) not in seen and str(dark) in seen
        assert (await c.get(f"{BASE}/health", headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404
        assert (await c.get(f"{BASE}/health/CAMERA/{far}", headers=w["h"][SUPERVISOR])).status_code == 404
        assert (await c.get(f"{BASE}/health/NVR/{recorder}", headers=w["h"][SUPERVISOR])).status_code == 404
        assert (await c.get(f"{BASE}/health", headers=w["h"][VIEWER])).status_code == 200
        assert (await c.get(f"{BASE}/health", headers=w["h"][GUARD])).status_code == 403
        assert (await c.get(f"{BASE}/health")).status_code == 401
    client, client_h = await _guard(w, "Building Owner", role=CLIENT)
    other = await _world()
    async with _client() as c:
        assert (await c.get(f"{BASE}/health", headers=client_h)).status_code == 403
        assert (await c.get(f"{BASE}/health", headers=other["h"][ADMIN])).json()["items"] == []
        assert (await c.get(f"{BASE}/health/CAMERA/{dark}", headers=other["h"][ADMIN])).status_code == 404
    # Reading it wrote nothing: only the scheduler's pass keeps anything.
    assert await _sql("SELECT 1 FROM device_health_changes WHERE tenant_id = :t", {"t": w["tenant"]}) == []


# ─── C. What is kept, and how long a device was down ─────────────────────────

async def test_a_state_is_kept_when_it_changes_and_the_time_down_is_counted_from_that():
    w = await _world()
    camera, sensor = await _camera(w), await _sensor(w)
    t0 = datetime.now(timezone.utc) - timedelta(hours=12)
    first = await _look(t0)
    assert first["changes"] >= 2 and await _kept(w, camera) == ["OK"] and await _kept(w, sensor) == ["OK"]
    assert (await _look(t0 + timedelta(minutes=5)))["from_health"] == 0
    assert await _kept(w, camera) == ["OK"], "the same state again is not kept again"

    await _sql("UPDATE streams SET status = 'offline' WHERE camera_id = :c", {"c": camera})
    await _look(t0 + timedelta(hours=2))
    await _sql("UPDATE streams SET status = 'online' WHERE camera_id = :c", {"c": camera})
    await _look(t0 + timedelta(hours=5))
    await _sql("UPDATE streams SET status = 'offline' WHERE camera_id = :c", {"c": camera})
    await _look(t0 + timedelta(hours=11))
    assert await _kept(w, camera) == ["OK", "DOWN", "OK", "DOWN"] and await _kept(w, sensor) == ["OK"]
    (row,) = await _sql("SELECT reasons, site_id FROM device_health_changes WHERE device_id = :d AND state = 'DOWN' "
                        "ORDER BY observed_at LIMIT 1", {"d": camera})
    assert row["reasons"] == ["Every stream is offline."] and row["site_id"] == w["site_a"]

    async with _client() as c:
        one = (await c.get(f"{BASE}/health/CAMERA/{camera}", headers=w["h"][VIEWER])).json()
        assert one["state"] == "DOWN" and one["name"] == "Gate 1" and one["not_measured"] == list(health.NOT_MEASURED)
        assert [h["state"] for h in one["history"]] == ["DOWN", "OK", "DOWN", "OK"], "newest first"
        week, month = one["down"]
        assert (week["days"], month["days"]) == (7, 30) and week["note"] == health.AVAILABILITY_NOTE
        # Down from 2 h to 5 h, and from 11 h to now (12 h): four hours, in two outages, out of twelve known.
        assert week["times_down"] == 2 and abs(week["down_seconds"] - 4 * 3600) < 60
        assert abs(week["known_seconds"] - 12 * 3600) < 60 and month["down_seconds"] == week["down_seconds"]
        # No record of the camera itself going offline, so "since" is when this state was first read.
        since = datetime.fromisoformat(one["since"])
        assert abs((since - (t0 + timedelta(hours=11))).total_seconds()) < 1 and one["since_is_when_first_read"] is False

        fresh = (await c.get(f"{BASE}/health/SENSOR/{sensor}", headers=w["h"][VIEWER])).json()
        # The only thing ever kept of it: that says when the platform began looking, not when it became fine.
        assert fresh["state"] == "OK" and fresh["since_is_when_first_read"] is True
        assert fresh["down"][0]["down_seconds"] == 0 and fresh["down"][0]["times_down"] == 0
        assert (await c.get(f"{BASE}/health/TOASTER/{camera}", headers=w["h"][VIEWER])).status_code == 404
        assert (await c.get(f"{BASE}/health/SENSOR/{camera}", headers=w["h"][VIEWER])).status_code == 404
    # Looking raised no order: nobody has asked for suggestions from health.
    assert await _sql("SELECT 1 FROM maintenance_work_orders WHERE tenant_id = :t", {"t": w["tenant"]}) == []
    (alerts,) = await _sql("SELECT count(*) AS n FROM alerts WHERE tenant_id = :t", {"t": w["tenant"]})
    assert alerts["n"] == 0


# ─── D. The register ─────────────────────────────────────────────────────────

async def test_an_asset_is_added_with_or_without_a_device_the_platform_knows():
    w = await _world()
    camera, far = await _camera(w, streams=("offline",)), await _camera(w, "B gate", site="site_b")
    async with _client() as c:
        r = await _add(c, w, site_id=str(w["site_a"]), make="APC", model="SMT1500", serial_number="AS-99",
                       vendor="PowerCo", installed_on="2024-03-01", warranty_until="2027-03-01", location="Comms room")
        assert r.status_code == 201, r.text
        ups = r.json()
        assert (ups["asset_code"], ups["kind"], ups["kind_label"], ups["status"]) == ("AST-0001", "UPS", "UPS", "IN_SERVICE")
        assert ups["site_name"] == "Factory A" and ups["created_by_name"] == "Role 8 User" and ups["warranty"] == "IN"
        # The platform does not know a UPS as a device, and says so instead of showing it as fine.
        assert ups["monitored"] is False and ups["health"] is None and ups["not_monitored"] == api.NOT_MONITORED
        assert ups["may"] == {"change": True, "retire": True, "restore": False} and ups["open_orders"] == 0

        cam = await _added(c, w, kind="CAMERA", name="Gate 1 camera", device_id=str(camera))
        assert cam["asset_code"] == "AST-0002" and cam["site_id"] == str(w["site_a"]), "the site is the camera's"
        assert cam["monitored"] is True and cam["health"]["state"] == "DOWN" and cam["not_monitored"] is None
        assert cam["health"]["reasons"] == ["Every stream is offline."] and cam["warranty"] == "NOT_RECORDED"
        again = await _add(c, w, kind="CAMERA", name="The same camera", device_id=str(camera))
        assert again.status_code == 409 and again.json()["detail"] == "That device is already in the register."

        for body, status, words in (
            ({"kind": "DRONE", "name": "Not a drone", "device_id": str(camera)}, 422, "No drone of that id is known."),
            ({"kind": "UPS", "name": "UPS", "device_id": str(camera)}, 422, "does not know a ups as a device"),
            ({"kind": "CAMERA", "name": "B gate", "device_id": str(far), "site_id": str(w["site_a"])}, 422, "another site"),
            ({"kind": "CAMERA", "name": "Ghost", "device_id": str(uuid.uuid4())}, 422, "No camera of that id"),
            ({"kind": "TOASTER", "name": "x"}, 422, None), ({"kind": "UPS", "name": "   "}, 422, "has a name"),
            ({"kind": "UPS", "name": "x", "status": "RETIRED"}, 422, None),
            ({"kind": "UPS", "name": "x", "asset_code": "AST-9999"}, 422, None),
            ({"kind": "UPS", "name": "x", "installed_on": "2026-01-01", "warranty_until": "2025-01-01"}, 422, "before the asset was installed"),
            ({"kind": "UPS", "name": "x", "site_id": str(uuid.uuid4())}, 404, "Site not found"),
        ):
            r = await c.post(BASE, headers=w["h"][MANAGER], json=body)
            assert r.status_code == status, (body, r.text)
            if words:
                assert words in str(r.json()["detail"]), (body, r.text)
        for role in (OPERATOR, VIEWER, GUARD):
            assert (await _add(c, w, who=role)).status_code == 403, role
        # Held to site A: says the site, and it is one of theirs.
        assert (await _add(c, w, who=SUPERVISOR)).status_code == 422
        assert (await _add(c, w, who=SUPERVISOR, site_id=str(w["site_b"]))).status_code == 404
        assert (await _add(c, w, who=SUPERVISOR, kind="CAMERA", name="B gate", device_id=str(far))).status_code == 422
        assert (await _add(c, w, who=SUPERVISOR, site_id=str(w["site_a"]))).json()["asset_code"] == "AST-0003"
        # An asset of the organisation, at no site: a server in a data centre.
        hq = await _added(c, w, kind="SERVER", name="Recording server")
        assert hq["site_id"] is None and hq["asset_code"] == "AST-0004"
        assert (await c.get(f"{BASE}/{hq['id']}", headers=w["h"][SUPERVISOR])).status_code == 404
    key = TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key
    try:
        async with _client() as c:
            r = await c.post(BASE, json={"kind": "UPS", "name": "By a key"})
            assert r.status_code == 403 and "not by an API key" in r.json()["detail"]
            assert (await c.get(BASE)).status_code == 200
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    first, second = (await _audit(w, "asset.create"))[:2]
    assert (first["detail"]["code"], first["detail"]["monitored"]) == ("AST-0001", False)
    assert (second["detail"]["code"], second["detail"]["monitored"]) == ("AST-0002", True)
    # Adding an asset changed nothing of the camera.
    (same,) = await _sql("SELECT name, is_active FROM cameras WHERE id = :c", {"c": camera})
    assert same["name"] == "Gate 1" and same["is_active"] is True


async def test_the_register_is_narrowed_and_kept_to_the_sites_it_concerns():
    w, other = await _world(), await _world()
    camera = await _camera(w)
    async with _client() as c:
        ups = await _added(c, w, site_id=str(w["site_a"]), serial_number="AS-99", warranty_until="2020-01-01")
        cam = await _added(c, w, kind="CAMERA", name="Gate 1 camera", device_id=str(camera), vendor="Percent % Ltd",
                           warranty_until="2099-01-01")
        far = await _added(c, w, kind="NETWORK", name="B switch", site_id=str(w["site_b"]))
        hq = await _added(c, w, kind="SERVER", name="Recording server")
        gone = await _added(c, w, name="Old UPS", site_id=str(w["site_a"]))
        assert (await c.post(f"{BASE}/{gone['id']}/retire", headers=w["h"][MANAGER], json={"reason": "Replaced."})).status_code == 200

        async def codes(who=ADMIN, headers=None, **params) -> list:
            r = await c.get(BASE, headers=headers or w["h"][who], params=params)
            assert r.status_code == 200, r.text
            return [i["asset_code"] for i in r.json()["items"]]

        assert await codes() == ["AST-0001", "AST-0002", "AST-0003", "AST-0004"], "by code, and not what is retired"
        assert await codes(status="RETIRED") == ["AST-0005"]
        assert await codes(kind=["UPS", "SERVER"]) == ["AST-0001", "AST-0004"]
        assert await codes(warranty="OUT") == ["AST-0001"] and await codes(warranty="IN") == ["AST-0002"]
        assert await codes(warranty="NOT_RECORDED") == ["AST-0003", "AST-0004"]
        assert await codes(q="as-99") == ["AST-0001"] and await codes(q="AST-0003") == ["AST-0003"]
        # The words as typed: a percent sign is looked for, not treated as "anything".
        assert await codes(q="%") == ["AST-0002"] and await codes(q="_") == []
        assert await codes(site_id=str(w["site_b"])) == ["AST-0003"]
        assert (await c.get(BASE, headers=w["h"][ADMIN], params={"kind": "TOASTER"})).status_code == 422
        page = (await c.get(BASE, headers=w["h"][VIEWER], params={"limit": 1})).json()
        assert len(page["items"]) == 1 and page["has_more"] is True and page["can_manage"] is False
        assert page["items"][0]["may"] == {"change": False, "retire": False, "restore": False}
        assert {k["key"]: k["monitored"] for k in page["kinds"]}["UPS"] is False
        listed = {i["asset_code"]: i for i in (await c.get(BASE, headers=w["h"][ADMIN])).json()["items"]}
        assert listed["AST-0002"]["health"]["state"] == "OK" and listed["AST-0001"]["health"] is None
        assert listed["AST-0001"]["warranty_days_left"] < 0

        # Held to site A: not site B's, and not what is at no site.
        assert await codes(who=SUPERVISOR) == ["AST-0001", "AST-0002"]
        assert (await c.get(BASE, headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404
        for hidden in (far, hq):
            assert (await c.get(f"{BASE}/{hidden['id']}", headers=w["h"][SUPERVISOR])).status_code == 404
            assert (await c.patch(f"{BASE}/{hidden['id']}", headers=w["h"][SUPERVISOR], json={"name": "Mine"})).status_code == 404
        assert await codes(headers=other["h"][ADMIN]) == []
        assert (await c.get(f"{BASE}/{ups['id']}", headers=other["h"][ADMIN])).status_code == 404
        for role in (GUARD,):
            assert (await c.get(BASE, headers=w["h"][role])).status_code == 403
        assert (await c.get(BASE)).status_code == 401 and cam["id"]
    _, client_h = await _guard(w, "Building Owner", role=CLIENT)
    async with _client() as c:
        assert (await c.get(BASE, headers=client_h)).status_code == 403


async def test_what_is_recorded_of_an_asset_is_changed_and_it_is_retired_not_removed():
    w = await _world()
    camera, second = await _camera(w), await _camera(w, "Gate 2")
    (place,) = await _sql("INSERT INTO site_places (tenant_id, site_id, kind, name, latitude, longitude) "
                          "VALUES (:t,:s,'BUILDING','Block A',1.3,103.8) RETURNING id", {"t": w["tenant"], "s": w["site_a"]})
    (elsewhere,) = await _sql("INSERT INTO site_places (tenant_id, site_id, kind, name, latitude, longitude) "
                              "VALUES (:t,:s,'BUILDING','Block B',1.35,103.85) RETURNING id", {"t": w["tenant"], "s": w["site_b"]})
    async with _client() as c:
        asset = await _added(c, w, kind="CAMERA", name="Gate camera", site_id=str(w["site_a"]), notes="Loose bracket")
        url = f"{BASE}/{asset['id']}"
        assert asset["monitored"] is False, "a camera in the register that is not yet said to be one the platform knows"
        r = await c.patch(url, headers=w["h"][SUPERVISOR], json={
            "name": " Gate 1 camera ", "device_id": str(camera), "make": "Axis", "serial_number": "ACC-1",
            "place_id": str(place["id"]), "status": "UNDER_REPAIR", "installed_on": "2025-01-05", "notes": None})
        assert r.status_code == 200, r.text
        now = r.json()
        assert (now["name"], now["make"], now["status"], now["notes"]) == ("Gate 1 camera", "Axis", "UNDER_REPAIR", None)
        assert now["place_name"] == "Block A" and now["monitored"] is True and now["health"]["state"] == "OK"
        assert now["updated_by_name"] == "Role 3 User" and now["asset_code"] == asset["asset_code"]
        assert (await c.patch(url, headers=w["h"][MANAGER], json={"device_id": None})).json()["monitored"] is False
        taken = await _added(c, w, kind="CAMERA", name="Gate 2 camera", device_id=str(second))
        for body, status, words in (
            ({}, 422, "Nothing to change."), ({"name": None}, 422, "has a name"), ({"name": "  "}, 422, "has a name"),
            ({"kind": "DRONE"}, 422, None), ({"asset_code": "AST-0009"}, 422, None), ({"status": "RETIRED"}, 422, None),
            ({"status": None}, 422, "has a status"),
            ({"warranty_until": "2024-12-31"}, 422, "before the asset was installed"),
            ({"place_id": str(elsewhere["id"])}, 422, "not an active place of the asset's site"),
            ({"site_id": str(w["site_b"])}, 422, "not an active place"),
            ({"device_id": str(second)}, 409, "already in the register"),
            ({"device_id": str(uuid.uuid4())}, 422, "No camera of that id"),
        ):
            r = await c.patch(url, headers=w["h"][MANAGER], json=body)
            assert r.status_code == status, (body, r.text)
            if words:
                assert words in str(r.json()["detail"]), (body, r.text)
        for role in (OPERATOR, VIEWER):
            assert (await c.patch(url, headers=w["h"][role], json={"name": "Theirs"})).status_code == 403

        # Retired with a reason, and kept.
        for body in ({}, {"reason": "  "}):
            assert (await c.post(f"{url}/retire", headers=w["h"][MANAGER], json=body)).status_code == 422
        assert (await c.post(f"{url}/retire", headers=w["h"][OPERATOR], json={"reason": "No."})).status_code == 403
        out = await c.post(f"{url}/retire", headers=w["h"][MANAGER], json={"reason": "Replaced by a dome camera."})
        assert out.status_code == 200 and out.json()["status"] == "RETIRED"
        assert out.json()["retire_reason"] == "Replaced by a dome camera." and out.json()["retired_by_name"] == "Role 8 User"
        assert out.json()["may"] == {"change": True, "retire": False, "restore": True}
        assert (await c.post(f"{url}/retire", headers=w["h"][MANAGER], json={"reason": "Twice."})).status_code == 409
        still = await c.patch(url, headers=w["h"][MANAGER], json={"name": "Renamed"})
        assert still.status_code == 409 and "Restore it to change it" in still.json()["detail"]
        assert (await c.get(url, headers=w["h"][VIEWER])).json()["status"] == "RETIRED", "it is still read"
        back = await c.post(f"{url}/restore", headers=w["h"][MANAGER])
        assert back.status_code == 200 and back.json()["status"] == "IN_SERVICE" and back.json()["retire_reason"] is None
        assert (await c.post(f"{url}/restore", headers=w["h"][MANAGER])).status_code == 409
        assert (await c.delete(url, headers=w["h"][ADMIN])).status_code == 405, "there is no removing an asset"
        assert taken["id"]
    (entry,) = [e for e in await _audit(w, "asset.update") if e["user_id"] == w["users"][SUPERVISOR]]
    assert entry["detail"]["changed"] == ["device_id", "installed_on", "make", "name", "notes", "place_id",
                                          "serial_number", "status"]
    assert len(await _audit(w, "asset.retire")) == 1 and len(await _audit(w, "asset.restore")) == 1
    # None of it touched a camera.
    rows = await _sql("SELECT name, is_active FROM cameras WHERE tenant_id = :t ORDER BY name", {"t": w["tenant"]})
    assert [(r["name"], r["is_active"]) for r in rows] == [("Gate 1", True), ("Gate 2", True)]


async def test_one_asset_shows_the_work_on_it_to_whoever_reads_maintenance():
    w = await _world()
    camera = await _camera(w, streams=("degraded",))
    async with _client() as c:
        asset = await _added(c, w, kind="CAMERA", name="Gate 1 camera", device_id=str(camera))
        raised = await c.post("/api/v1/maintenance/work-orders", headers=w["h"][MANAGER], json={
            "title": "Clean the lens", "asset_id": asset["id"]})
        assert raised.status_code == 201, raised.text
        seen = (await c.get(f"{BASE}/{asset['id']}", headers=w["h"][VIEWER])).json()
        assert seen["health"]["state"] == "DEGRADED" and seen["not_measured"] == list(health.NOT_MEASURED)
        assert seen["open_orders"] == 1 and [o["title"] for o in seen["work_orders"]] == ["Clean the lens"]
        assert seen["work_orders"][0]["number"] == "WO-0001"
        ups = await _added(c, w, site_id=str(w["site_a"]))
        bare = (await c.get(f"{BASE}/{ups['id']}", headers=w["h"][VIEWER])).json()
        assert bare["work_orders"] == [] and bare["not_measured"] == [], "nothing is measured of it at all, and that is said"
        assert bare["not_monitored"] == api.NOT_MONITORED


# ─── E. Devices the platform knows, put into the register ────────────────────

async def test_devices_the_platform_knows_are_put_into_the_register_as_they_are_known():
    w = await _world()
    camera, far = await _camera(w), await _camera(w, "B gate", site="site_b")
    recorder, sensor, panel, gateway = await _nvr(w), await _sensor(w), await _panel(w), await _gateway(w)
    async with _client() as c:
        drone = await _drone(c, w, code="D-7", manufacturer="DJI", model="M30T", serial_number="DJ-0007")
        already = await _added(c, w, kind="SENSOR", name="Temperature probe", device_id=str(sensor))
        r = await c.get(f"{BASE}/unregistered", headers=w["h"][MANAGER])
        assert r.status_code == 200, r.text
        found = {i["device_id"]: i for i in r.json()["items"]}
        assert set(found) == {str(camera), str(far), str(recorder), str(panel), str(gateway), drone["id"]}
        assert str(sensor) not in found, "it is already an asset"
        assert (found[drone["id"]]["make"], found[drone["id"]]["model"], found[drone["id"]]["serial_number"]) == (
            "DJI", "M30T", "DJ-0007")
        assert found[str(camera)]["location"] == "North fence" and found[str(camera)]["site_name"] == "Factory A"
        assert found[str(recorder)]["site_id"] is None and found[str(recorder)]["kind_label"] == "Recorder"
        # Held to site A: not site B's camera, and not the recorder.
        mine = {i["device_id"] for i in (await c.get(f"{BASE}/unregistered", headers=w["h"][SUPERVISOR])).json()["items"]}
        assert mine == {str(camera), str(panel), str(gateway), drone["id"]}
        assert (await c.get(f"{BASE}/unregistered", headers=w["h"][OPERATOR])).status_code == 403

        ghost = str(uuid.uuid4())
        body = {"devices": [{"kind": "CAMERA", "device_id": str(camera)}, {"kind": "DRONE", "device_id": drone["id"]},
                            {"kind": "ALARM_PANEL", "device_id": str(panel)}, {"kind": "SENSOR", "device_id": str(sensor)},
                            {"kind": "CAMERA", "device_id": ghost}, {"kind": "NVR", "device_id": str(recorder)}]}
        done = await c.post(f"{BASE}/register-devices", headers=w["h"][MANAGER], json=body)
        assert done.status_code == 201, done.text
        made = {m["device_id"]: m for m in done.json()["registered"]}
        assert set(made) == {str(camera), drone["id"], str(panel), str(recorder)}
        assert {(d["kind"], d["device_id"]) for d in done.json()["left"]} == {("SENSOR", str(sensor)), ("CAMERA", ghost)}
        assert sorted(m["asset_code"] for m in made.values()) == ["AST-0002", "AST-0003", "AST-0004", "AST-0005"]
        as_asset = (await c.get(f"{BASE}/{made[drone['id']]['id']}", headers=w["h"][VIEWER])).json()
        assert (as_asset["kind"], as_asset["name"], as_asset["make"], as_asset["serial_number"], as_asset["site_name"]) == (
            "DRONE", "Drone D-7", "DJI", "DJ-0007", "Factory A")
        assert as_asset["monitored"] is True and as_asset["health"]["state"] == "NOT_KNOWN"
        assert as_asset["vendor"] is None and as_asset["warranty"] == "NOT_RECORDED", "what the device does not say is left for a person"
        of_panel = (await c.get(f"{BASE}/{made[str(panel)]['id']}", headers=w["h"][VIEWER])).json()
        assert (of_panel["model"], of_panel["serial_number"]) == ("DSC PowerSeries", "SN-7781")
        # Asked again, nothing is entered twice.
        again = await c.post(f"{BASE}/register-devices", headers=w["h"][MANAGER], json=body)
        assert again.json()["registered"] == [] and len(again.json()["left"]) == 6
        # Held to site A: site B's camera and the recorder are not theirs to register.
        theirs = await c.post(f"{BASE}/register-devices", headers=w["h"][SUPERVISOR], json={"devices": [
            {"kind": "CAMERA", "device_id": str(far)}, {"kind": "EDGE_GATEWAY", "device_id": str(gateway)}]})
        assert [m["device_id"] for m in theirs.json()["registered"]] == [str(gateway)]
        for bad in ({"devices": []}, {"devices": [{"kind": "UPS", "device_id": ghost}]}, {}):
            assert (await c.post(f"{BASE}/register-devices", headers=w["h"][MANAGER], json=bad)).status_code == 422
        assert (await c.post(f"{BASE}/register-devices", headers=w["h"][VIEWER], json=body)).status_code == 403
        assert already["asset_code"] == "AST-0001"
    first = (await _audit(w, "asset.register_devices"))[0]
    assert (first["detail"]["registered"], first["detail"]["left"]) == (4, 2)
    assert first["detail"]["kinds"] == ["ALARM_PANEL", "CAMERA", "DRONE", "NVR"]


# ─── F. What the application role and the database refuse ────────────────────

async def test_what_the_application_role_cannot_do_to_the_register_and_the_health_log():
    w, other = await _world(), await _world()
    camera = await _camera(w)
    async with _client() as c:
        await _added(c, w, kind="CAMERA", name="Gate 1 camera", device_id=str(camera))
    await _look()
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        scoped = "tenant_id = current_setting('app.current_tenant')::uuid"
        for statement in (
            f"DELETE FROM asset_register WHERE {scoped}",
            f"UPDATE asset_register SET asset_code = 'AST-9999' WHERE {scoped}",
            f"UPDATE asset_register SET kind = 'DRONE' WHERE {scoped}",
            f"UPDATE asset_register SET created_by_user_id = NULL, created_at = now() WHERE {scoped}",
            # What was read of a device is kept as it was read.
            f"UPDATE device_health_changes SET state = 'OK' WHERE {scoped}",
            f"UPDATE device_health_changes SET observed_at = now() WHERE {scoped}",
            f"DELETE FROM device_health_changes WHERE {scoped}",
            f"DELETE FROM maintenance_schedules WHERE {scoped}",
            f"DELETE FROM maintenance_work_orders WHERE {scoped}",
        ):
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(statement))
            await db.rollback()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(other["tenant"])})
        for table in ("asset_register", "maintenance_schedules", "maintenance_work_orders"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 0, table
        seen = (await db.execute(text("SELECT count(*) FROM device_health_changes WHERE device_id = :d"), {"d": camera})).scalar()
        assert seen == 0, "another organisation's devices are not read"
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(text("INSERT INTO asset_register (tenant_id, asset_code, kind, name) "
                                  "VALUES (:t, 'AST-0099', 'UPS', 'Planted')"), {"t": w["tenant"]})
        await db.rollback()
    for table in TABLES:
        row = (await _sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :n", {"n": table}))[0]
        assert row["relrowsecurity"] and row["relforcerowsecurity"], table
        (can,) = await _sql("SELECT has_table_privilege('svc_app', :n, 'DELETE') AS d, "
                            "has_table_privilege('svc_app', :n, 'UPDATE') AS u", {"n": table})
        assert not can["d"] and not can["u"], f"{table}: no DELETE, and no UPDATE of the whole row"
    assert "GRANT ALL" not in MIGRATION and MIGRATION.count("REVOKE ALL ON {table} FROM svc_app") == 1
    assert "device_health_changes TO svc_app" not in MIGRATION.replace("GRANT SELECT, INSERT ON {table} TO svc_app", "")


async def test_what_the_database_refuses_of_an_asset():
    w = await _world()
    t = w["tenant"]
    camera, second = await _camera(w), await _camera(w, "Gate 2")
    async with _client() as c:
        drone = await _drone(c, w, code="D-9")
    make = ("INSERT INTO asset_register (tenant_id, asset_code, kind, name, status, camera_id, drone_id, retired_at, "
            "retire_reason) VALUES (:t, :code, :kind, :name, :status, :camera, :drone, :at, :why)")
    base = {"t": t, "code": "AST-0001", "kind": "CAMERA", "name": "Gate 1", "status": "IN_SERVICE", "camera": camera,
            "drone": None, "at": None, "why": None}
    for over, constraint in (
        ({"kind": "TOASTER", "camera": None}, "ck_asset_kind"), ({"status": "BROKEN"}, "ck_asset_status"),
        ({"name": "  "}, "ck_asset_name"),
        # Two devices cannot both be of the asset's one kind, so either rule may be the one that speaks.
        ({"drone": drone["id"]}, "ck_asset_(device_kind|one_device)"),
        # A camera in the register is the asset of a camera, not of a drone.
        ({"kind": "DRONE"}, "ck_asset_device_kind"), ({"kind": "UPS"}, "ck_asset_device_kind"),
        ({"status": "RETIRED"}, "ck_asset_retired"), ({"at": datetime.now(timezone.utc)}, "ck_asset_retired"),
        ({"status": "RETIRED", "at": datetime.now(timezone.utc), "why": " "}, "ck_asset_retired_why"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(make, {**base, **over})
    await _sql(make, base)
    with pytest.raises(DBAPIError, match="uq_asset_code"):
        await _sql(make, {**base, "camera": second})
    with pytest.raises(DBAPIError, match="uq_asset_camera_id"):
        await _sql(make, {**base, "code": "AST-0002"})
    for state, kind in (("BROKEN", "CAMERA"), ("OK", "TOASTER")):
        with pytest.raises(DBAPIError, match="ck_dhc_"):
            await _sql("INSERT INTO device_health_changes (tenant_id, device_kind, device_id, state) VALUES (:t,:k,:d,:s)",
                       {"t": t, "k": kind, "d": camera, "s": state})
    # The camera goes; the asset stays, as an asset of no device the platform knows.
    await _run([("DELETE FROM streams WHERE camera_id = :c", {"c": camera}), ("DELETE FROM cameras WHERE id = :c", {"c": camera})])
    (kept,) = await _sql("SELECT camera_id, name FROM asset_register WHERE asset_code = 'AST-0001' AND tenant_id = :t", {"t": t})
    assert kept["camera_id"] is None and kept["name"] == "Gate 1"


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


def test_every_route_asks_for_what_it_should():
    served = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if (path == BASE or path.startswith(BASE + "/")) and getattr(route, "endpoint", None):
                for method in route.methods - {"HEAD"}:
                    served[(method, path.replace(":uuid", "").removeprefix(BASE))] = _needs(route)
    read, manage = {"asset:read"}, {"asset:read", "asset:manage"}
    assert served == {
        ("GET", "/health"): read, ("GET", "/health/{kind}/{device_id}"): read, ("GET", ""): read, ("POST", ""): manage,
        ("GET", "/unregistered"): manage, ("POST", "/register-devices"): manage, ("GET", "/{asset_id}"): read,
        ("PATCH", "/{asset_id}"): manage, ("POST", "/{asset_id}/retire"): manage, ("POST", "/{asset_id}/restore"): manage,
    }
    assert not [m for m, _ in served if m == "DELETE"], "an asset is retired, never removed"


async def test_who_holds_the_four_permissions_and_the_devices_are_as_they_were():
    rows = await _sql("SELECT p.code, p.category, array_agg(rp.role_id ORDER BY rp.role_id) AS roles FROM permissions p "
                      "JOIN role_permissions rp ON rp.permission_id = p.id "
                      "WHERE p.code = ANY(:c) GROUP BY p.code, p.category",
                      {"c": ["asset:read", "asset:manage", "maintenance:read", "maintenance:manage"]})
    assert {r["code"]: list(r["roles"]) for r in rows} == {
        "asset:read": [2, 3, 4, 6, 8], "asset:manage": [2, 3, 8], "maintenance:read": [2, 3, 4, 6, 8],
        "maintenance:manage": [2, 3, 8]}
    assert {r["code"]: r["category"] for r in rows}["asset:read"] == "device"
    upgrade = MIGRATION.split("def upgrade")[1].split("def downgrade")[0]
    for table in ("cameras", "streams", "nvr_connections", "iot_sensors", "drones", "drone_edge_gateways",
                  "alarm_panels", "facility_defects", "equipment_items", "camera_health_events"):
        assert not re.search(rf"(ALTER TABLE|UPDATE|DELETE FROM|INSERT INTO|DROP TABLE)\s+{table}\b", upgrade), table
    assert "TRUNC" + "ATE" not in upgrade
    # Nothing here writes a device, an alert or an incident; the health module writes one table, and only that.
    for source in (Path(api.__file__), Path(health.__file__), Path(work.__file__)):
        code = source.read_text(encoding="utf-8").split('"""', 2)[2]
        for table in ("cameras", "streams", "nvr_connections", "iot_sensors", "drones", "drone_edge_gateways",
                      "alarm_panels", "facility_defects", "camera_health_events", "alerts", "incidents",
                      "security_events"):
            assert not re.search(rf"(INSERT INTO|UPDATE|DELETE FROM)\s+{table}\b", code), (source.name, table)
    written = set(re.findall(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_]+)",
                             Path(health.__file__).read_text(encoding="utf-8").split('"""', 2)[2]))
    assert written == {"device_health_changes"}
