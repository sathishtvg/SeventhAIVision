"""P4-E: Bulk operations.

Covers:
- Bulk-acknowledge alerts
- Bulk-dismiss alerts
- Bulk-create incidents from alerts
- Bulk-resolve incidents
- Bulk-update-status incidents
- Bulk-bypass zones (timed suppression)
- Bulk-restore zones
"""
import pytest
from httpx import AsyncClient


# ── helpers ───────────────────────────────────────────────────────────────────

async def _make_camera(auth_client: AsyncClient) -> str:
    r = await auth_client.post("/api/v1/cameras", json={"name": "Bulk-test cam"})
    assert r.status_code in (200, 201)
    return r.json()["id"]


async def _make_alert(auth_client: AsyncClient, camera_id: str, severity: str = "medium") -> str:
    r = await auth_client.post(
        "/api/v1/alerts",
        json={"camera_id": camera_id, "module_type": "lpr", "severity": severity, "title": "Bulk test alert"},
    )
    assert r.status_code == 200
    return r.json()["id"]


async def _make_zone(auth_client: AsyncClient, camera_id: str) -> str:
    r = await auth_client.post(
        "/api/v1/zones",
        json={
            "camera_id": camera_id,
            "name": "Bulk test zone",
            "polygon": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}],
            "severity": "high",
        },
    )
    assert r.status_code == 201
    return r.json()["id"]


# ── Bulk-acknowledge alerts ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_acknowledge_updates_open_alerts(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    id1 = await _make_alert(auth_client, cam)
    id2 = await _make_alert(auth_client, cam)

    r = await auth_client.post("/api/v1/alerts/bulk-acknowledge", json={"ids": [id1, id2]})
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 2
    assert body["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_acknowledge_skips_already_acknowledged(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    id1 = await _make_alert(auth_client, cam)

    # Acknowledge once
    await auth_client.post("/api/v1/alerts/bulk-acknowledge", json={"ids": [id1]})
    # Acknowledge again — already done, should skip
    r = await auth_client.post("/api/v1/alerts/bulk-acknowledge", json={"ids": [id1]})
    assert r.status_code == 200
    assert r.json()["updated"] == 0
    assert r.json()["skipped"] == 1


@pytest.mark.asyncio
async def test_bulk_acknowledge_empty_ids_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/alerts/bulk-acknowledge", json={"ids": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_acknowledge_over_100_ids_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/alerts/bulk-acknowledge", json={"ids": ["x"] * 101})
    assert r.status_code == 422


# ── Bulk-dismiss alerts ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_dismiss_open_alerts(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    id1 = await _make_alert(auth_client, cam)
    id2 = await _make_alert(auth_client, cam)

    r = await auth_client.post("/api/v1/alerts/bulk-dismiss", json={"ids": [id1, id2]})
    assert r.status_code == 200
    assert r.json()["updated"] == 2


# ── Bulk-create incidents from alerts ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_create_incidents_creates_one_per_alert(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    id1 = await _make_alert(auth_client, cam)
    id2 = await _make_alert(auth_client, cam)

    r = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": [id1, id2]})
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 2
    assert body["skipped"] == 0
    assert len(body["incident_ids"]) == 2


@pytest.mark.asyncio
async def test_bulk_create_incidents_skips_already_linked(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    id1 = await _make_alert(auth_client, cam)

    # First call creates the incident
    r1 = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": [id1]})
    assert r1.json()["created"] == 1

    # Second call with the same alert should skip it
    r2 = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": [id1]})
    assert r2.status_code == 200
    assert r2.json()["created"] == 0
    assert r2.json()["skipped"] == 1


@pytest.mark.asyncio
async def test_bulk_create_incidents_empty_ids_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_create_incidents_over_50_ids_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": ["x"] * 51})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_create_incidents_returns_incident_ids_in_db(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    id1 = await _make_alert(auth_client, cam)

    r = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": [id1]})
    assert r.status_code == 200
    incident_id = r.json()["incident_ids"][0]

    # Verify incident exists in the incidents list
    inc_list = (await auth_client.get("/api/v1/incidents")).json()
    ids_in_list = [str(i["id"]) for i in inc_list["items"]]
    assert incident_id in ids_in_list


# ── Bulk-resolve incidents ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_resolve_incidents(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    a1 = await _make_alert(auth_client, cam)
    a2 = await _make_alert(auth_client, cam)

    ri = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": [a1, a2]})
    iids = ri.json()["incident_ids"]

    r = await auth_client.post("/api/v1/incidents/bulk-resolve", json={"ids": iids})
    assert r.status_code == 200
    assert r.json()["updated"] == 2
    assert r.json()["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_resolve_skips_already_resolved(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    a1 = await _make_alert(auth_client, cam)

    ri = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": [a1]})
    iid = ri.json()["incident_ids"][0]

    await auth_client.post("/api/v1/incidents/bulk-resolve", json={"ids": [iid]})
    r = await auth_client.post("/api/v1/incidents/bulk-resolve", json={"ids": [iid]})
    assert r.json()["skipped"] == 1


# ── Bulk-update-status incidents ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_update_status_dispatched(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    a1 = await _make_alert(auth_client, cam)
    ri = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": [a1]})
    iid = ri.json()["incident_ids"][0]

    r = await auth_client.post("/api/v1/incidents/bulk-update-status", json={"ids": [iid], "status": "dispatched"})
    assert r.status_code == 200
    assert r.json()["updated"] == 1


@pytest.mark.asyncio
async def test_bulk_update_status_invalid_status_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/incidents/bulk-update-status", json={"ids": ["x"], "status": "flying"})
    assert r.status_code == 422


# ── Bulk-bypass zones ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_bypass_zones_sets_bypass_until(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    z1 = await _make_zone(auth_client, cam)

    r = await auth_client.post("/api/v1/zones/bulk-bypass", json={"ids": [z1], "bypass_minutes": 30})
    assert r.status_code == 200
    body = r.json()
    assert body["bypassed"] == 1
    assert body["skipped"] == 0
    assert body["bypass_minutes"] == 30
    assert len(body["zones"]) == 1
    assert "bypass_until" in body["zones"][0]


@pytest.mark.asyncio
async def test_bulk_bypass_zones_returns_in_list(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    z1 = await _make_zone(auth_client, cam)

    await auth_client.post("/api/v1/zones/bulk-bypass", json={"ids": [z1], "bypass_minutes": 60})

    zones = (await auth_client.get("/api/v1/zones")).json()
    zone = next((z for z in zones if z["id"] == z1), None)
    assert zone is not None
    assert zone["bypass_until"] is not None


@pytest.mark.asyncio
async def test_bulk_bypass_zones_empty_ids_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/zones/bulk-bypass", json={"ids": [], "bypass_minutes": 60})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_bypass_zones_over_50_ids_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/zones/bulk-bypass", json={"ids": ["x"] * 51, "bypass_minutes": 60})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_bypass_zones_invalid_minutes_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/zones/bulk-bypass", json={"ids": ["x"], "bypass_minutes": 0})
    assert r.status_code == 422

    r2 = await auth_client.post("/api/v1/zones/bulk-bypass", json={"ids": ["x"], "bypass_minutes": 1441})
    assert r2.status_code == 422


# ── Bulk-restore zones ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_restore_zones_clears_bypass_until(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    z1 = await _make_zone(auth_client, cam)

    await auth_client.post("/api/v1/zones/bulk-bypass", json={"ids": [z1], "bypass_minutes": 60})

    r = await auth_client.post("/api/v1/zones/bulk-restore", json={"ids": [z1]})
    assert r.status_code == 200
    assert r.json()["restored"] == 1
    assert r.json()["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_restore_zones_zone_no_longer_bypassed(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    z1 = await _make_zone(auth_client, cam)

    await auth_client.post("/api/v1/zones/bulk-bypass", json={"ids": [z1], "bypass_minutes": 60})
    await auth_client.post("/api/v1/zones/bulk-restore", json={"ids": [z1]})

    zones = (await auth_client.get("/api/v1/zones")).json()
    zone = next((z for z in zones if z["id"] == z1), None)
    assert zone is not None
    assert zone["bypass_until"] is None


@pytest.mark.asyncio
async def test_bulk_restore_zones_skips_not_bypassed(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    z1 = await _make_zone(auth_client, cam)

    r = await auth_client.post("/api/v1/zones/bulk-restore", json={"ids": [z1]})
    assert r.status_code == 200
    assert r.json()["restored"] == 0
    assert r.json()["skipped"] == 1


@pytest.mark.asyncio
async def test_bulk_restore_zones_empty_ids_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/zones/bulk-restore", json={"ids": []})
    assert r.status_code == 422
