"""P5-F: Zone schedule — time-based activation windows for restricted zones.

Tests cover:
- PUT /zones/{id}/schedule: sets schedule fields, returns them
- PUT /zones/{id}/schedule: non-existent zone → 404
- PUT /zones/{id}/schedule: active_days empty → 422
- PUT /zones/{id}/schedule: active_days out of range (< 0 or > 6) → 422
- PUT /zones/{id}/schedule: invalid time format → 422
- GET /zones: response includes schedule fields on all zones
- GET /zones: is_currently_active is False when schedule_enabled=False but zone inactive
- GET /zones: is_currently_active is True when schedule_enabled=False and zone active
- GET /zones: is_currently_active is False when under bypass even if scheduled active
- Disabling schedule (enabled=False) makes zone always-on again
- Schedule with all 7 days and full time window = always-on equivalent
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_camera(client: AsyncClient, name: str = "Schedule Cam") -> str:
    r = await client.post("/api/v1/cameras", json={"name": name, "location": "HQ"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_zone(client: AsyncClient, camera_id: str, name: str = "Test Zone") -> str:
    r = await client.post("/api/v1/zones", json={
        "camera_id": camera_id,
        "name": name,
        "polygon": [{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.1}, {"x": 0.5, "y": 0.5}],
        "severity": "high",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── Set schedule ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_schedule_returns_updated_fields(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    zone_id = await _make_zone(auth_client, cam)

    r = await auth_client.put(f"/api/v1/zones/{zone_id}/schedule", json={
        "enabled": True,
        "timezone": "Asia/Singapore",
        "active_days": [0, 1, 2, 3, 4],   # Mon–Fri
        "active_start_time": "18:00:00",
        "active_end_time": "07:00:00",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["schedule_enabled"] is True
    assert body["schedule_timezone"] == "Asia/Singapore"
    assert 0 in body["active_days"]
    assert 4 in body["active_days"]
    assert body["active_start_time"] == "18:00:00"
    assert body["active_end_time"] == "07:00:00"


@pytest.mark.asyncio
async def test_set_schedule_nonexistent_zone_404(auth_client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000099"
    r = await auth_client.put(f"/api/v1/zones/{fake_id}/schedule", json={
        "enabled": True,
    })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_set_schedule_empty_active_days_422(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    zone_id = await _make_zone(auth_client, cam)
    r = await auth_client.put(f"/api/v1/zones/{zone_id}/schedule", json={
        "enabled": True,
        "active_days": [],
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_set_schedule_invalid_day_value_422(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    zone_id = await _make_zone(auth_client, cam)
    r = await auth_client.put(f"/api/v1/zones/{zone_id}/schedule", json={
        "enabled": True,
        "active_days": [0, 7],   # 7 is out of range
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_set_schedule_negative_day_422(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    zone_id = await _make_zone(auth_client, cam)
    r = await auth_client.put(f"/api/v1/zones/{zone_id}/schedule", json={
        "enabled": True,
        "active_days": [-1, 0, 1],
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_set_schedule_invalid_time_format_422(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    zone_id = await _make_zone(auth_client, cam)
    r = await auth_client.put(f"/api/v1/zones/{zone_id}/schedule", json={
        "enabled": True,
        "active_start_time": "25:00",   # invalid hour
    })
    assert r.status_code == 422


# ── list_zones returns schedule fields ────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_zones_includes_schedule_fields(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    await _make_zone(auth_client, cam)

    r = await auth_client.get("/api/v1/zones")
    assert r.status_code == 200
    zones = r.json()
    assert len(zones) > 0
    zone = zones[0]
    for field in (
        "schedule_enabled", "schedule_timezone",
        "active_days", "active_start_time", "active_end_time",
        "is_currently_active",
    ):
        assert field in zone, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_new_zone_schedule_disabled_by_default(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    zone_id = await _make_zone(auth_client, cam, "Default Schedule Zone")

    r = await auth_client.get("/api/v1/zones")
    assert r.status_code == 200
    zone = next((z for z in r.json() if z["id"] == zone_id), None)
    assert zone is not None
    assert zone["schedule_enabled"] is False
    # No schedule → zone is always-on (is_currently_active = True for active zone)
    assert zone["is_currently_active"] is True


@pytest.mark.asyncio
async def test_is_currently_active_false_when_deactivated(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    zone_id = await _make_zone(auth_client, cam, "Deactivated Zone")

    # Deactivate the zone
    await auth_client.delete(f"/api/v1/zones/{zone_id}")

    r = await auth_client.get("/api/v1/zones")
    assert r.status_code == 200
    zone = next((z for z in r.json() if z["id"] == zone_id), None)
    assert zone is not None
    assert zone["is_currently_active"] is False


@pytest.mark.asyncio
async def test_is_currently_active_false_when_bypassed(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    zone_id = await _make_zone(auth_client, cam, "Bypassed Zone")

    # Bypass for 60 min
    await auth_client.post("/api/v1/zones/bulk-bypass", json={
        "ids": [zone_id],
        "bypass_minutes": 60,
    })

    r = await auth_client.get("/api/v1/zones")
    assert r.status_code == 200
    zone = next((z for z in r.json() if z["id"] == zone_id), None)
    assert zone is not None
    assert zone["is_currently_active"] is False


# ── Disable schedule reverts to always-on ────────────────────────────────────

@pytest.mark.asyncio
async def test_disable_schedule_makes_zone_always_on(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    zone_id = await _make_zone(auth_client, cam, "Re-enable Zone")

    # Enable a schedule (weekdays only, office hours — may or may not be active right now)
    await auth_client.put(f"/api/v1/zones/{zone_id}/schedule", json={
        "enabled": True,
        "active_days": [0, 1, 2, 3, 4],
        "active_start_time": "09:00:00",
        "active_end_time": "17:00:00",
    })

    # Now disable the schedule
    r = await auth_client.put(f"/api/v1/zones/{zone_id}/schedule", json={
        "enabled": False,
        "active_days": [0],
        "active_start_time": "09:00:00",
        "active_end_time": "17:00:00",
    })
    assert r.status_code == 200
    assert r.json()["schedule_enabled"] is False

    # Zone should be always-on now (schedule_enabled=False → always active)
    zones_r = await auth_client.get("/api/v1/zones")
    zone = next((z for z in zones_r.json() if z["id"] == zone_id), None)
    assert zone is not None
    assert zone["is_currently_active"] is True


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_schedule_requires_auth(client: AsyncClient):
    r = await client.put(
        "/api/v1/zones/00000000-0000-0000-0000-000000000001/schedule",
        json={"enabled": True},
    )
    assert r.status_code == 401


# ── Dedup of active_days ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_deduplicates_active_days(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    zone_id = await _make_zone(auth_client, cam, "Dedup Zone")

    r = await auth_client.put(f"/api/v1/zones/{zone_id}/schedule", json={
        "enabled": True,
        "active_days": [1, 1, 2, 2, 3],   # duplicates
    })
    assert r.status_code == 200
    # Should be deduplicated
    days = r.json()["active_days"]
    assert len(days) == len(set(days))
