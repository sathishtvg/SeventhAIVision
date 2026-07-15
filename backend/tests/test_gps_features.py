"""GPS Fleet Tracking — vehicle CRUD, position ingest, journeys, geofences, dashboard."""
import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _hdr(user_id, tenant_id, role_id=2) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=role_id)}"}


# ══════════════════════════════════════════════════════════════════════════════
# Vehicle CRUD
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_gps_vehicle_crud(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create
    r = await app_client.post("/api/v1/gps/vehicles", json={
        "name": "Patrol Car 1",
        "vehicle_type": "patrol_car",
        "plate_number": "SGA1234X",
        "color": "white",
    }, headers=h)
    assert r.status_code == 200, r.text
    vehicle_id = r.json()["id"]

    # List
    r = await app_client.get("/api/v1/gps/vehicles", headers=h)
    assert r.status_code == 200
    assert any(v["id"] == vehicle_id for v in r.json())

    # Get single
    r = await app_client.get(f"/api/v1/gps/vehicles/{vehicle_id}", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Patrol Car 1"
    assert data["vehicle_type"] == "patrol_car"

    # Update
    r = await app_client.put(f"/api/v1/gps/vehicles/{vehicle_id}",
                              json={"color": "silver"}, headers=h)
    assert r.status_code == 200

    # Soft-delete
    r = await app_client.delete(f"/api/v1/gps/vehicles/{vehicle_id}", headers=h)
    assert r.status_code == 200

    # Confirm gone from list
    r = await app_client.get("/api/v1/gps/vehicles", headers=h)
    assert r.status_code == 200
    assert vehicle_id not in [v["id"] for v in r.json()]


@pytest.mark.asyncio
async def test_gps_vehicle_invalid_type_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/gps/vehicles", json={
        "name": "X", "vehicle_type": "hovercraft",
    }, headers=h)
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Position ingest + journey auto-management
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_gps_position_ingest_and_journey(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/gps/vehicles", json={
        "name": "Motorcycle 1", "vehicle_type": "motorcycle",
    }, headers=h)
    assert r.status_code == 200
    vehicle_id = r.json()["id"]

    # Ingest a moving position (speed >= 5) — should start a journey
    r = await app_client.post(f"/api/v1/gps/vehicles/{vehicle_id}/positions", json={
        "lat": 1.3521, "lon": 103.8198, "speed": 30.0, "heading": 90,
    }, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "id" in body

    # Positions list should return our entry
    r = await app_client.get(f"/api/v1/gps/vehicles/{vehicle_id}/positions", headers=h)
    assert r.status_code == 200
    assert len(r.json()) >= 1

    # Ingest idle position (speed < 5) — should complete the journey
    r = await app_client.post(f"/api/v1/gps/vehicles/{vehicle_id}/positions", json={
        "lat": 1.3522, "lon": 103.8199, "speed": 0.0,
    }, headers=h)
    assert r.status_code == 200

    # Journey should now appear
    r = await app_client.get(f"/api/v1/gps/vehicles/{vehicle_id}/journeys", headers=h)
    assert r.status_code == 200
    journeys = r.json()
    assert len(journeys) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# Geofences
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_gps_geofence_crud(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    polygon = [
        {"lat": 1.35, "lon": 103.82},
        {"lat": 1.36, "lon": 103.82},
        {"lat": 1.36, "lon": 103.83},
        {"lat": 1.35, "lon": 103.83},
    ]

    # Create geofence
    r = await app_client.post("/api/v1/gps/geofences", json={
        "name": "HQ Zone", "polygon": polygon,
        "fence_type": "restricted", "alert_on": "entry",
    }, headers=h)
    assert r.status_code == 200, r.text
    fence_id = r.json()["id"]

    # List
    r = await app_client.get("/api/v1/gps/geofences", headers=h)
    assert r.status_code == 200
    assert any(f["id"] == fence_id for f in r.json())

    # Update
    r = await app_client.put(f"/api/v1/gps/geofences/{fence_id}",
                              json={"name": "HQ Zone Updated"}, headers=h)
    assert r.status_code == 200

    # Delete
    r = await app_client.delete(f"/api/v1/gps/geofences/{fence_id}", headers=h)
    assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard + geofence events
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_gps_dashboard(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/gps/dashboard", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "total" in data
    assert "vehicles" in data


@pytest.mark.asyncio
async def test_gps_geofence_events_list(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/gps/geofence-events", headers=h)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
