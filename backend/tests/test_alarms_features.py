"""Alarm panel integration — panels, zones, arm/disarm, webhook ingest, events, dashboard."""
import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _hdr(user_id, tenant_id, role_id=2) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=role_id)}"}


# ══════════════════════════════════════════════════════════════════════════════
# Panel CRUD
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_alarm_panel_crud(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create panel
    r = await app_client.post("/api/v1/alarms/panels", json={
        "name": "Main Entrance Panel",
        "protocol": "webhook",
        "model": "DSC PowerSeries Neo",
    }, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    panel_id = data["id"]
    # api_key only exposed on creation
    assert "api_key" in data
    api_key = data["api_key"]

    # List (api_key should be hidden)
    r = await app_client.get("/api/v1/alarms/panels", headers=h)
    assert r.status_code == 200
    panels = r.json()
    assert any(p["id"] == panel_id for p in panels)
    panel_entry = next(p for p in panels if p["id"] == panel_id)
    assert "api_key" not in panel_entry

    # Get single (api_key not exposed)
    r = await app_client.get(f"/api/v1/alarms/panels/{panel_id}", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Main Entrance Panel"
    assert "api_key" not in data

    # Update
    r = await app_client.put(f"/api/v1/alarms/panels/{panel_id}",
                              json={"name": "Main Entrance Panel Updated"}, headers=h)
    assert r.status_code == 200

    return panel_id, api_key


@pytest.mark.asyncio
async def test_alarm_panel_invalid_protocol_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/alarms/panels", json={
        "name": "Bad Panel", "protocol": "zigbee",
    }, headers=h)
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Zones
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_alarm_zone_create(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/alarms/panels", json={
        "name": "Zone Panel", "protocol": "webhook",
    }, headers=h)
    assert r.status_code == 200
    panel_id = r.json()["id"]

    r = await app_client.post(f"/api/v1/alarms/panels/{panel_id}/zones", json={
        "zone_number": 1, "name": "Front Door", "zone_type": "door",
    }, headers=h)
    assert r.status_code == 200, r.text
    zone_data = r.json()
    assert zone_data["zone_number"] == 1
    assert zone_data["current_state"] == "normal"

    # Duplicate zone_number should conflict
    r = await app_client.post(f"/api/v1/alarms/panels/{panel_id}/zones", json={
        "zone_number": 1, "name": "Duplicate", "zone_type": "motion",
    }, headers=h)
    assert r.status_code == 409


# ══════════════════════════════════════════════════════════════════════════════
# Arm / Disarm
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_alarm_arm_disarm(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/alarms/panels", json={
        "name": "Arm Test Panel", "protocol": "webhook",
    }, headers=h)
    assert r.status_code == 200
    panel_id = r.json()["id"]

    # Arm (away mode)
    r = await app_client.put(f"/api/v1/alarms/panels/{panel_id}/arm",
                              json={"mode": "away"}, headers=h)
    assert r.status_code == 200
    assert r.json()["arm_state"] == "armed_away"

    # Disarm
    r = await app_client.put(f"/api/v1/alarms/panels/{panel_id}/disarm", headers=h)
    assert r.status_code == 200
    assert r.json()["arm_state"] == "disarmed"

    # Invalid arm mode
    r = await app_client.put(f"/api/v1/alarms/panels/{panel_id}/arm",
                              json={"mode": "full"}, headers=h)
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Key rotation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_alarm_rotate_key(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/alarms/panels", json={
        "name": "Key Rotate Panel", "protocol": "webhook",
    }, headers=h)
    assert r.status_code == 200
    panel_id = r.json()["id"]
    old_key = r.json()["api_key"]

    r = await app_client.post(f"/api/v1/alarms/panels/{panel_id}/rotate-key", headers=h)
    assert r.status_code == 200
    new_key = r.json()["api_key"]
    assert new_key.startswith("pak_")
    assert new_key != old_key


# ══════════════════════════════════════════════════════════════════════════════
# Webhook ingest (X-Panel-Key auth)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_alarm_event_ingest_zone_alarm_creates_alert(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create panel + zone + seed a camera (alerts need a camera_id)
    camera_id = uuid.uuid4()
    await admin_session.execute(text("""
        INSERT INTO cameras (id, tenant_id, name, is_active)
        VALUES (:id, :tid, 'Alarm Cam', TRUE)
    """), {"id": camera_id, "tid": tenant_id})
    await admin_session.commit()

    r = await app_client.post("/api/v1/alarms/panels", json={
        "name": "Ingest Panel", "protocol": "webhook",
    }, headers=h)
    assert r.status_code == 200
    panel_id = r.json()["id"]
    api_key = r.json()["api_key"]

    # Add zone with linked camera
    r = await app_client.post(f"/api/v1/alarms/panels/{panel_id}/zones", json={
        "zone_number": 5, "name": "Back Door",
        "zone_type": "door", "linked_camera_id": str(camera_id),
    }, headers=h)
    assert r.status_code == 200

    # Ingest zone_alarm event via webhook (no JWT needed, uses X-Panel-Key)
    r = await app_client.post("/api/v1/alarms/events/ingest",
                               json={
                                   "event_type": "zone_alarm",
                                   "zone_number": 5,
                                   "description": "Back door forced open",
                               },
                               headers={"X-Panel-Key": api_key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["severity"] == "high"
    assert body["alert_id"] is not None


@pytest.mark.asyncio
async def test_alarm_event_ingest_invalid_key_rejected(app_client, admin_session):
    r = await app_client.post("/api/v1/alarms/events/ingest",
                               json={"event_type": "zone_alarm"},
                               headers={"X-Panel-Key": "pak_invalid_totally_wrong"})
    assert r.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# Events list + dashboard
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_alarm_events_list(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/alarms/events", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "has_more" in body


@pytest.mark.asyncio
async def test_alarm_dashboard(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/alarms/dashboard", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "panel_summary" in data
    assert "zone_summary" in data
    assert "panels" in data
