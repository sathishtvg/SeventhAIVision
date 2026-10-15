"""Fleet tracking, phase 2 of the GPS fleet upgrade: devices, Traccar's forwarding, status.

  A — Reading what Traccar forwards, with nothing running
  B — The tracking status and its limits
  C — The platform's Traccar forwards a position
  D — One device, one vehicle, on the whole platform
  E — An organisation's own Traccar
  F — The minute pass, as the application, for two organisations
  G — Who may do what

Every request goes through the real app over ASGI, as svc_app with RLS enforced.
The forwarded body is the one Traccar 6.16 sent when it was tried on 15 October
2026 (docs/plans/2026-10-14-gps-fleet-tracking-design.md, section 2).
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.routers import fleet_tracking as router_module
from app.services import fleet_tracking as ft
from app.services.fleet_providers import Connection, ForwardRefused
from app.services.fleet_providers.traccar import TraccarAdapter
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _sql, _world
from tests.test_investigation_search import _audit

BASE = "/api/v1/fleet"
SECRET = "test-forward-secret-0123456789"

#: What Traccar 6.16 forwarded for one OsmAnd position (15 October 2026), times moved to "now".
TRACCAR_SAID = {
    "device": {"attributes": {}, "calendarId": 0, "category": None, "contact": None, "disabled": False,
               "expirationTime": None, "groupId": 0, "id": 1, "lastUpdate": "2026-10-15T04:07:59.247+00:00",
               "model": None, "name": "Probe car", "phone": None, "positionId": 0, "status": "online",
               "uniqueId": "PROBE123"},
    "position": {"accuracy": 8.0, "address": None, "altitude": 12.0,
                 "attributes": {"batteryLevel": 88.0, "distance": 0.0, "motion": True, "totalDistance": 0.0},
                 "course": 90.0, "deviceId": 1, "deviceTime": "2026-10-15T04:07:28.000+00:00",
                 "fixTime": "2026-10-15T04:07:28.000+00:00", "geofenceIds": None, "id": 0, "latitude": 1.3521,
                 "longitude": 103.8198, "network": None, "protocol": "osmand",
                 "serverTime": "2026-10-15T04:07:59.211+00:00", "speed": 10.0, "valid": True},
}


def said(device: str = "PROBE123", *, ago: timedelta = timedelta(seconds=30), **position) -> dict:
    body = copy.deepcopy(TRACCAR_SAID)
    body["device"]["uniqueId"] = device
    fix = (datetime.now(timezone.utc) - ago).isoformat()
    body["position"].update({"fixTime": fix, "deviceTime": fix, **position})
    return body


@pytest.fixture
def forwarding(monkeypatch):
    monkeypatch.setattr(settings, "TRACCAR_FORWARD_SECRET", SECRET)
    return {"X-Fleet-Secret": SECRET}


async def _vehicle(c, w, plate: str, *, site: str = "site_a") -> dict:
    r = await c.post(f"{BASE}/vehicles", headers=w["h"][ADMIN],
                     json={"name": f"Car {plate}", "plate_number": plate, "site_id": str(w[site])})
    assert r.status_code == 201, r.text
    return r.json()


async def _device(c, w, vehicle_id: str, identifier: str | None, who: int = ADMIN, **extra):
    return await c.put(f"{BASE}/vehicles/{vehicle_id}/device", headers=w["h"][who],
                       json={"device_identifier": identifier, "tracking_enabled": identifier is not None, **extra})


# ─── A. Reading what Traccar forwards ────────────────────────────────────────

def test_traccars_own_message_is_read_in_kmh_at_the_devices_time():
    pos = TraccarAdapter().parse_forward(TRACCAR_SAID, now=datetime(2026, 10, 15, 4, 8, tzinfo=timezone.utc))
    assert pos.device_identifier == "PROBE123"
    assert pos.fix_at == datetime(2026, 10, 15, 4, 7, 28, tzinfo=timezone.utc)
    assert (pos.lat, pos.lon, pos.speed_kmh, pos.heading, pos.accuracy, pos.altitude) == (1.3521, 103.8198, 18.52, 90, 8.0, 12.0)
    assert pos.valid is True and pos.ignition is None and pos.attributes["batteryLevel"] == 88.0


@pytest.mark.parametrize(("change", "reason"), [
    ({"device": {}}, "no_device_id"),
    ({"position": {"latitude": 91}}, "bad_coordinates"),
    ({"position": {"longitude": "east"}}, "bad_coordinates"),
    ({"position": {"latitude": 0.0, "longitude": 0.0}}, "zero_position"),
    ({"position": {"fixTime": None, "deviceTime": None}}, "no_fix_time"),
    ({"position": {"fixTime": "2026-10-15T05:00:00+00:00"}}, "fix_in_future"),
    ({"position": {"valid": False}}, "not_a_fix"),
])
def test_what_is_not_a_real_fix_is_refused_with_its_reason(change, reason):
    body = copy.deepcopy(TRACCAR_SAID)
    for part, values in change.items():
        if values:
            body[part].update(values)
        else:
            body[part] = values
    with pytest.raises(ForwardRefused) as refused:
        TraccarAdapter().parse_forward(body, now=datetime(2026, 10, 15, 4, 8, tzinfo=timezone.utc))
    assert refused.value.reason == reason
    with pytest.raises(ForwardRefused):
        TraccarAdapter().parse_forward("not json at all")


async def test_traccar_is_asked_with_its_token_and_a_refused_sign_in_is_said():
    seen = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        if request.url.path == "/api/server":
            return httpx.Response(200, json={"version": "6.16.0"})
        if request.headers.get("Authorization") != "Bearer good":
            return httpx.Response(401)
        if request.url.path == "/api/devices" and request.method == "GET":
            wanted = request.url.params.get("uniqueId")
            return httpx.Response(200, json=[{"id": 7, "uniqueId": "IMEI1", "status": "online",
                                              "lastUpdate": "2026-10-15T04:07:59Z"}] if wanted in (None, "IMEI1") else [])
        if request.url.path == "/api/devices" and request.method == "POST":
            return httpx.Response(200, json={"id": 8})
        return httpx.Response(404)

    traccar = TraccarAdapter(httpx.MockTransport(answer))
    good, bad = Connection("http://traccar:8082", "token", None, "good"), Connection("http://traccar:8082", "token", None, "bad")
    assert (await traccar.test(good)).ok and (await traccar.test(good)).detail == {"version": "6.16.0", "devices": 1}
    refused = await traccar.test(bad)
    assert not refused.ok and "did not accept the sign-in" in refused.message
    assert (await traccar.device(good, "IMEI1")).detail["traccar_id"] == 7
    assert "does not know this device" in (await traccar.device(good, "IMEI2")).message
    assert (await traccar.register_device(good, "IMEI2", "SBA 1 Patrol")).ok
    assert "Bearer good" in seen and "Bearer bad" in seen


# ─── B. The tracking status ──────────────────────────────────────────────────

def test_the_status_is_the_age_of_the_last_fix_and_nothing_else():
    now = datetime(2026, 10, 15, 4, 0, tzinfo=timezone.utc)
    s = lambda **k: ft.status_of(**{"tracking_enabled": True, "has_device": True, "last_fix_at": now,
                                    "online_minutes": 5, "offline_minutes": 30, "now": now, **k})
    assert s() == "online" and s(last_fix_at=now - timedelta(minutes=4, seconds=59)) == "online"
    assert s(last_fix_at=now - timedelta(minutes=5)) == "stale" and s(last_fix_at=now - timedelta(minutes=29)) == "stale"
    assert s(last_fix_at=now - timedelta(minutes=30)) == "offline" and s(last_fix_at=None) == "offline"
    assert s(tracking_enabled=False) == "not_configured" and s(has_device=False) == "not_configured"
    assert ft.check_limits(2, 10) == {"online_minutes": 2, "offline_minutes": 10}
    for bad in ((10, 10), (0, 5), (5, 2000), (True, 30), ("5", 30)):
        with pytest.raises(ValueError):
            ft.check_limits(*bad)


# ─── C. The platform's Traccar forwards a position ───────────────────────────

async def test_without_the_secret_nothing_is_taken(monkeypatch):
    monkeypatch.setattr(settings, "TRACCAR_FORWARD_SECRET", "")
    async with _client() as c:
        assert (await c.post(f"{BASE}/ingest/traccar", json=TRACCAR_SAID)).status_code == 503
    monkeypatch.setattr(settings, "TRACCAR_FORWARD_SECRET", SECRET)
    async with _client() as c:
        assert (await c.post(f"{BASE}/ingest/traccar", json=TRACCAR_SAID)).status_code == 401
        assert (await c.post(f"{BASE}/ingest/traccar", json=TRACCAR_SAID,
                             headers={"X-Fleet-Secret": SECRET[:-1]})).status_code == 401


async def test_a_forwarded_position_is_kept_once_at_its_fix_time_and_a_late_one_does_not_move_the_vehicle(forwarding):
    w = await _world(licensed=False)
    device = f"IMEI-{uuid.uuid4().hex[:10]}"
    async with _client() as c:
        v = await _vehicle(c, w, "SGP 1")
        assert (await _device(c, w, v["id"], device)).status_code == 200
        before = (await c.get(f"{BASE}/vehicles/{v['id']}/device", headers=w["h"][VIEWER])).json()
        assert before["status"] == "offline", "linked and switched on, but nothing has come: offline, never online"

        first = said(device, ago=timedelta(seconds=20))
        r = await c.post(f"{BASE}/ingest/traccar", json=first, headers=forwarding)
        assert r.json() == {"accepted": True, "latest": True, "status": "online"}
        again = await c.post(f"{BASE}/ingest/traccar", json=first, headers=forwarding)
        assert again.json() == {"accepted": False, "reason": "duplicate"}
        late = await c.post(f"{BASE}/ingest/traccar", json=said(device, ago=timedelta(minutes=3), latitude=1.30), headers=forwarding)
        assert late.json() == {"accepted": True, "latest": False}
        zero = await c.post(f"{BASE}/ingest/traccar", json=said(device, latitude=0.0, longitude=0.0), headers=forwarding)
        assert zero.json() == {"accepted": False, "reason": "zero_position"}

        seen = (await c.get(f"{BASE}/vehicles/{v['id']}/device", headers=w["h"][VIEWER])).json()
    assert seen["status"] == "online" and seen["last_lat"] == 1.3521, "the late fix did not move the vehicle back"
    assert float(seen["last_speed"]) == 18.52 and seen["last_heading"] == 90
    assert {r["reason"]: r["times"] for r in seen["rejects"]} == {"duplicate": 1, "zero_position": 1}
    kept = await _sql("SELECT recorded_at, speed, source FROM vehicle_positions WHERE vehicle_id = :v ORDER BY recorded_at",
                      {"v": v["id"]})
    assert len(kept) == 2 and {k["source"] for k in kept} == {"traccar"}
    assert [c["to_status"] for c in seen["status_changes"]] == ["online", "offline"]


async def test_a_device_nobody_registered_is_counted_where_no_organisation_can_read_it(forwarding):
    unknown = f"NOBODY-{uuid.uuid4().hex[:8]}"
    async with _client() as c:
        r = await c.post(f"{BASE}/ingest/traccar", json=said(unknown), headers=forwarding)
        assert r.json() == {"accepted": False, "reason": "unknown_device"}
        await c.post(f"{BASE}/ingest/traccar", json=said(unknown, ago=timedelta(seconds=5)), headers=forwarding)
    row = (await _sql("SELECT times FROM fleet_unknown_devices WHERE device_identifier = :d", {"d": unknown}))[0]
    assert row["times"] == 2
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("SELECT * FROM fleet_unknown_devices"))
        await db.rollback()


async def test_a_vehicle_with_tracking_switched_off_takes_no_position(forwarding):
    w = await _world(licensed=False)
    device = f"IMEI-{uuid.uuid4().hex[:10]}"
    async with _client() as c:
        v = await _vehicle(c, w, "OFF 1")
        await _device(c, w, v["id"], device, tracking_enabled=False)
        r = await c.post(f"{BASE}/ingest/traccar", json=said(device), headers=forwarding)
        assert r.json() == {"accepted": False, "reason": "tracking_off"}
        listed = (await c.get(f"{BASE}/vehicles", headers=w["h"][ADMIN], params={"tracking": "not_configured"})).json()
    assert [x["id"] for x in listed["items"]] == [v["id"]]


# ─── D. One device, one vehicle, on the whole platform ───────────────────────

async def test_a_device_is_one_vehicles_and_its_positions_go_to_that_organisation_only(forwarding):
    w, other = await _world(licensed=False), await _world(licensed=False)
    device = f"IMEI-{uuid.uuid4().hex[:10]}"
    async with _client() as c:
        mine, theirs = await _vehicle(c, w, "ONE 1"), await _vehicle(c, other, "TWO 2")
        assert (await _device(c, w, mine["id"], device)).status_code == 200
        taken = await _device(c, other, theirs["id"], device)
        assert taken.status_code == 409 and taken.json()["detail"] == "This device is already linked to another vehicle"
        second = await _vehicle(c, w, "ONE 2")
        assert (await _device(c, w, second["id"], device)).status_code == 409, "not twice in one organisation either"
        await c.post(f"{BASE}/ingest/traccar", json=said(device), headers=forwarding)
        assert (await c.get(f"{BASE}/vehicles/{mine['id']}/device", headers=other["h"][ADMIN])).status_code == 404
        assert (await c.get(f"{BASE}/vehicles", headers=other["h"][ADMIN], params={"tracking": "online"})).json()["items"] == []
        # Archiving frees the device for another vehicle; what it reported stays with the archived one.
        await c.post(f"{BASE}/vehicles/{mine['id']}/archive", headers=w["h"][ADMIN], json={"reason": "Sold"})
        assert (await _device(c, other, theirs["id"], device)).status_code == 200
    kept = await _sql("SELECT count(*) AS n FROM vehicle_positions WHERE vehicle_id = :v", {"v": mine["id"]})
    assert kept[0]["n"] == 1
    assert (await _audit(w, "fleet.vehicle.archived"))[0]["detail"]["device_released"] == device

    # The application reads and writes the register only through its functions.
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        assert not (await db.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("SELECT * FROM fleet_device_registry"))
        await db.rollback()
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        with pytest.raises(DBAPIError, match="not a vehicle of this organisation"):
            await db.execute(text("SELECT fleet_device_claim('builtin', 'STEAL-1', CAST(:v AS uuid))"), {"v": theirs["id"]})
        await db.rollback()


# ─── E. An organisation's own Traccar ────────────────────────────────────────

async def test_an_organisations_own_traccar_has_its_own_secret_and_its_own_devices(forwarding, monkeypatch):
    w, other = await _world(licensed=False), await _world(licensed=False)
    device = f"IMEI-{uuid.uuid4().hex[:10]}"
    async with _client() as c:
        for bad in ("http://localhost:8082", "http://169.254.169.254/latest", "ftp://traccar.example.test"):
            r = await c.post(f"{BASE}/providers", headers=w["h"][ADMIN],
                             json={"name": "X", "base_url": bad, "auth_kind": "token", "secret": "t"})
            assert r.status_code == 422, bad
        made = (await c.post(f"{BASE}/providers", headers=w["h"][ADMIN], json={
            "name": "Our Traccar", "base_url": "https://traccar.example.test", "auth_kind": "token", "secret": "plain-token"})).json()
        secret, cid = made["forward_secret"], made["id"]
        listed = (await c.get(f"{BASE}/providers", headers=w["h"][ADMIN])).json()
        assert "forward_secret" not in str(listed) and "secret_encrypted" not in str(listed) and "plain-token" not in str(listed)
        assert listed["items"][0]["forward_path"] == f"/api/v1/fleet/ingest/traccar/{cid}"
        v = await _vehicle(c, w, "OWN 1")
        assert (await _device(c, w, v["id"], device, provider_connection_id=cid)).status_code == 200
        url = f"{BASE}/ingest/traccar/{cid}"
        assert (await c.post(url, json=said(device), headers={"X-Fleet-Secret": SECRET})).status_code == 401
        assert (await c.post(url, json=said(device), headers={"X-Fleet-Secret": secret})).json()["accepted"] is True
        # The device is this connection's: the platform's Traccar does not deliver it.
        assert (await c.post(f"{BASE}/ingest/traccar", json=said(device, ago=timedelta(seconds=2)),
                             headers=forwarding)).json()["reason"] == "unknown_device"
        # Another organisation's connection cannot deliver it either.
        theirs = (await c.post(f"{BASE}/providers", headers=other["h"][ADMIN], json={
            "name": "Theirs", "base_url": "https://t2.example.test", "auth_kind": "token", "secret": "x"})).json()
        r = await c.post(f"{BASE}/ingest/traccar/{theirs['id']}", json=said(device, ago=timedelta(seconds=3)),
                         headers={"X-Fleet-Secret": theirs["forward_secret"]})
        assert r.json()["reason"] == "unknown_device"
        assert (await c.post(f"{BASE}/providers/{cid}/retire", headers=w["h"][MANAGER])).status_code == 200
        assert (await c.post(url, json=said(device, ago=timedelta(seconds=4)), headers={"X-Fleet-Secret": secret})).status_code == 401
    stored = (await _sql("SELECT secret_encrypted FROM fleet_provider_connections WHERE id = :i", {"i": cid}))[0]
    assert "plain-token" not in stored["secret_encrypted"], "kept encrypted"


async def test_testing_a_connection_says_what_traccar_answered(monkeypatch):
    w = await _world(licensed=False)

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "6.16.0"}) if request.url.path == "/api/server" else httpx.Response(401)

    monkeypatch.setattr(router_module, "adapter", lambda name: TraccarAdapter(httpx.MockTransport(answer)))
    async with _client() as c:
        made = (await c.post(f"{BASE}/providers", headers=w["h"][ADMIN], json={
            "name": "Ours", "base_url": "https://traccar.example.test", "auth_kind": "basic", "username": "fleet",
            "secret": "pw"})).json()
        r = (await c.post(f"{BASE}/providers/{made['id']}/test", headers=w["h"][ADMIN])).json()
        assert r["ok"] is False and "did not accept the sign-in" in r["message"]
        listed = (await c.get(f"{BASE}/providers", headers=w["h"][ADMIN])).json()["items"][0]
        assert listed["last_test_ok"] is False and listed["last_test_message"] == r["message"]


# ─── F. The minute pass ──────────────────────────────────────────────────────

async def test_the_minute_pass_records_stale_and_offline_once_for_each_organisation(forwarding):
    w, other = await _world(licensed=False), await _world(licensed=False)
    async with _client() as c:
        mine, theirs = await _vehicle(c, w, "MIN 1"), await _vehicle(c, other, "MIN 2")
        for world, v in ((w, mine), (other, theirs)):
            device = f"IMEI-{uuid.uuid4().hex[:10]}"
            await _device(c, world, v["id"], device)
            await c.post(f"{BASE}/ingest/traccar", json=said(device, ago=timedelta(seconds=10)), headers=forwarding)
        await c.put(f"{BASE}/settings", headers=other["h"][ADMIN], json={"online_minutes": 2, "offline_minutes": 60})
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
    later = datetime.now(timezone.utc) + timedelta(minutes=10)
    await ft.sweep(AsyncSessionLocal, None, now=later)
    await ft.sweep(AsyncSessionLocal, None, now=later)
    rows = {r["id"]: r for r in await _sql(
        "SELECT CAST(id AS text) AS id, tracking_status, current_status FROM vehicles WHERE id = ANY(CAST(:i AS uuid[]))",
        {"i": [mine["id"], theirs["id"]]})}
    assert rows[mine["id"]]["tracking_status"] == "stale", "ten minutes: stale under the defaults"
    assert rows[theirs["id"]]["tracking_status"] == "stale", "and under the other organisation's own limits too"
    await ft.sweep(AsyncSessionLocal, None, now=later + timedelta(minutes=40))
    rows = {r["id"]: r for r in await _sql(
        "SELECT CAST(id AS text) AS id, tracking_status, current_status FROM vehicles WHERE id = ANY(CAST(:i AS uuid[]))",
        {"i": [mine["id"], theirs["id"]]})}
    assert (rows[mine["id"]]["tracking_status"], rows[mine["id"]]["current_status"]) == ("offline", "offline")
    assert rows[theirs["id"]]["tracking_status"] == "stale", "its own offline limit is an hour"
    changes = await _sql("SELECT to_status FROM fleet_status_changes WHERE vehicle_id = :v ORDER BY changed_at",
                         {"v": mine["id"]})
    assert [x["to_status"] for x in changes] == ["offline", "online", "stale", "offline"], "each change once"


# ─── G. Who may do what ──────────────────────────────────────────────────────

async def test_who_may_link_devices_test_them_and_set_the_limits():
    w = await _world(licensed=False)
    async with _client() as c:
        a, b = await _vehicle(c, w, "WHO 1"), await _vehicle(c, w, "WHO 2", site="site_b")
        for role in (SUPERVISOR, OPERATOR, GUARD, VIEWER):
            assert (await _device(c, w, a["id"], "X-1", who=role)).status_code == 403
            assert (await c.get(f"{BASE}/providers", headers=w["h"][role])).status_code == 403
            assert (await c.put(f"{BASE}/settings", headers=w["h"][role],
                                json={"online_minutes": 2, "offline_minutes": 9})).status_code == 403
        assert (await c.get(f"{BASE}/vehicles/{a['id']}/device", headers=w["h"][SUPERVISOR])).status_code == 200
        assert (await c.get(f"{BASE}/vehicles/{b['id']}/device", headers=w["h"][SUPERVISOR])).status_code == 404
        assert (await _device(c, w, a["id"], "bad id with spaces")).status_code == 422
        assert (await c.put(f"{BASE}/vehicles/{a['id']}/device", headers=w["h"][MANAGER],
                            json={"tracking_enabled": True, "device_identifier": None})).status_code == 422
        assert (await c.put(f"{BASE}/settings", headers=w["h"][ADMIN],
                            json={"online_minutes": 30, "offline_minutes": 30})).status_code == 422
        assert (await c.put(f"{BASE}/settings", headers=w["h"][ADMIN],
                            json={"online_minutes": 3, "offline_minutes": 20})).json() == {"online_minutes": 3, "offline_minutes": 20}
        assert (await c.get(f"{BASE}/settings", headers=w["h"][GUARD])).json() == {"online_minutes": 3, "offline_minutes": 20}
        assert (await _device(c, w, a["id"], f"IMEI-{uuid.uuid4().hex[:8]}")).status_code == 200
    assert [r["detail"]["tracking_enabled"] for r in await _audit(w, "fleet.device.changed")] == [True]
    assert (await _audit(w, "fleet.settings.changed"))[0]["detail"] == {"online_minutes": 3, "offline_minutes": 20}
