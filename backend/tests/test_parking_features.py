"""Smart Parking — carpark/zone/bay CRUD, rates, sessions, occupancy, dashboard."""
import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _hdr(user_id, tenant_id, role_id=2) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=role_id)}"}


# ══════════════════════════════════════════════════════════════════════════════
# Carpark CRUD
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_parking_carpark_crud(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create carpark
    r = await app_client.post("/api/v1/carparks", json={
        "name": "Block A Carpark",
        "total_capacity": 100,
    }, headers=h)
    assert r.status_code == 200, r.text
    carpark_id = r.json()["id"]

    # List
    r = await app_client.get("/api/v1/carparks", headers=h)
    assert r.status_code == 200
    assert any(c["id"] == carpark_id for c in r.json())

    # Get single
    r = await app_client.get(f"/api/v1/carparks/{carpark_id}", headers=h)
    assert r.status_code == 200
    assert r.json()["name"] == "Block A Carpark"

    # Update
    r = await app_client.put(f"/api/v1/carparks/{carpark_id}",
                              json={"name": "Block A CP"}, headers=h)
    assert r.status_code == 200

    return carpark_id, tenant_id, user_id


@pytest.mark.asyncio
async def test_parking_zone_and_bays(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create carpark
    r = await app_client.post("/api/v1/carparks", json={
        "name": "Zone Test CP", "total_capacity": 50,
    }, headers=h)
    assert r.status_code == 200
    carpark_id = r.json()["id"]

    # Add zone
    r = await app_client.post(f"/api/v1/carparks/{carpark_id}/zones", json={
        "name": "Level 1", "zone_type": "standard",
    }, headers=h)
    assert r.status_code == 200, r.text
    zone_id = r.json()["id"]

    # Add bays
    r = await app_client.post(f"/api/v1/carparks/{carpark_id}/bays", json={
        "zone_id": zone_id, "bay_number": "A01", "bay_type": "standard",
    }, headers=h)
    assert r.status_code == 200, r.text
    bay_id = r.json()["id"]

    # List bays
    r = await app_client.get(f"/api/v1/carparks/{carpark_id}/bays", headers=h)
    assert r.status_code == 200
    assert any(b["id"] == bay_id for b in r.json())

    # Update bay status
    r = await app_client.put(f"/api/v1/parking/bays/{bay_id}",
                              json={"status": "reserved"}, headers=h)
    assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Rates
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_parking_rates(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/carparks", json={
        "name": "Rate Test CP", "total_capacity": 20,
    }, headers=h)
    assert r.status_code == 200
    carpark_id = r.json()["id"]

    # Create rate
    r = await app_client.post(f"/api/v1/carparks/{carpark_id}/rates", json={
        "name": "Weekday Rate",
        "rate_type": "hourly",
        "amount": 2.50,
    }, headers=h)
    assert r.status_code == 200, r.text

    # List rates
    r = await app_client.get(f"/api/v1/carparks/{carpark_id}/rates", headers=h)
    assert r.status_code == 200
    assert len(r.json()) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# Parking sessions — entry, exit, payment
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_parking_session_lifecycle(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Setup: carpark + bay
    r = await app_client.post("/api/v1/carparks", json={
        "name": "Session Test CP", "total_capacity": 10,
    }, headers=h)
    assert r.status_code == 200
    carpark_id = r.json()["id"]

    r = await app_client.post(f"/api/v1/carparks/{carpark_id}/zones", json={
        "name": "L1", "zone_type": "standard",
    }, headers=h)
    assert r.status_code == 200
    zone_id = r.json()["id"]

    r = await app_client.post(f"/api/v1/carparks/{carpark_id}/bays", json={
        "zone_id": zone_id, "bay_number": "B01", "bay_type": "standard",
    }, headers=h)
    assert r.status_code == 200
    bay_id = r.json()["id"]

    # Entry
    r = await app_client.post("/api/v1/parking/sessions/entry", json={
        "carpark_id": carpark_id,
        "bay_id": bay_id,
        "plate_number": "SGX1234Z",
    }, headers=h)
    assert r.status_code == 200, r.text
    session_id = r.json()["id"]

    # List sessions
    r = await app_client.get("/api/v1/parking/sessions", headers=h)
    assert r.status_code == 200
    assert any(s["id"] == session_id for s in r.json())

    # Exit
    r = await app_client.put(f"/api/v1/parking/sessions/{session_id}/exit", json={}, headers=h)
    assert r.status_code == 200

    # Payment
    r = await app_client.put(f"/api/v1/parking/sessions/{session_id}/payment", json={
        "amount_paid": 5.00,
        "payment_method": "cash",
    }, headers=h)
    assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Occupancy + dashboard
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_parking_occupancy(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/carparks", json={
        "name": "Occ Test CP", "total_capacity": 5,
    }, headers=h)
    assert r.status_code == 200
    carpark_id = r.json()["id"]

    r = await app_client.get(f"/api/v1/carparks/{carpark_id}/occupancy", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "occupancy_pct" in data or "total_bays" in data


@pytest.mark.asyncio
async def test_parking_dashboard(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/parking/dashboard", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "total_carparks" in data or "carparks" in data
