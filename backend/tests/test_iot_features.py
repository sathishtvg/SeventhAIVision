"""IoT Smart Facilities Monitoring — sensor CRUD, readings, threshold alerts, dashboard."""
import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _hdr(user_id, tenant_id, role_id=2) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=role_id)}"}


# ══════════════════════════════════════════════════════════════════════════════
# Sensor CRUD
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_iot_sensor_crud(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create
    r = await app_client.post("/api/v1/iot/sensors", json={
        "name": "Tank Level A", "sensor_type": "water_tank",
        "threshold_warning_high": 90.0, "threshold_critical_high": 95.0,
    }, headers=h)
    assert r.status_code == 200, r.text
    sensor_id = r.json()["id"]

    # List — sensor appears
    r = await app_client.get("/api/v1/iot/sensors", headers=h)
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()]
    assert sensor_id in ids

    # Get single
    r = await app_client.get(f"/api/v1/iot/sensors/{sensor_id}", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Tank Level A"
    assert data["sensor_type"] == "water_tank"

    # Update
    r = await app_client.put(f"/api/v1/iot/sensors/{sensor_id}",
                              json={"name": "Tank Level Alpha"}, headers=h)
    assert r.status_code == 200

    # Soft-delete (is_active=False)
    r = await app_client.delete(f"/api/v1/iot/sensors/{sensor_id}", headers=h)
    assert r.status_code == 200

    # Confirm hidden from default list (is_active=True filter)
    r = await app_client.get("/api/v1/iot/sensors", headers=h)
    assert r.status_code == 200
    assert sensor_id not in [s["id"] for s in r.json()]


@pytest.mark.asyncio
async def test_iot_sensor_invalid_type_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/iot/sensors", json={
        "name": "Bad", "sensor_type": "nuclear_reactor",
    }, headers=h)
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Readings + threshold alerts
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_iot_reading_ingest_normal(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create sensor without critical thresholds
    r = await app_client.post("/api/v1/iot/sensors", json={
        "name": "Humidity Sensor", "sensor_type": "humidity",
    }, headers=h)
    assert r.status_code == 200
    sensor_id = r.json()["id"]

    # Ingest a normal reading
    r = await app_client.post(f"/api/v1/iot/sensors/{sensor_id}/readings",
                               json={"value": 55.0}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body.get("alert_created") is False

    # Reading should appear in history
    r = await app_client.get(f"/api/v1/iot/sensors/{sensor_id}/readings", headers=h)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    assert items[0]["value"] == 55.0


@pytest.mark.asyncio
async def test_iot_reading_breach_creates_alert(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Sensor with critical_high = 80
    r = await app_client.post("/api/v1/iot/sensors", json={
        "name": "Temp Sensor", "sensor_type": "temperature",
        "threshold_critical_high": 80.0,
    }, headers=h)
    assert r.status_code == 200
    sensor_id = r.json()["id"]

    # Value > critical_high → alert_created
    r = await app_client.post(f"/api/v1/iot/sensors/{sensor_id}/readings",
                               json={"value": 95.0}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body.get("alert_created") is True
    assert body.get("alert_severity") in ("warning", "critical")


@pytest.mark.asyncio
async def test_iot_alerts_list(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/iot/alerts", headers=h)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_iot_dashboard(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/iot/dashboard", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "total_sensors" in data
    assert "active_sensors" in data
