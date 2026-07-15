"""Body-Worn Camera (BWC) — camera CRUD, assign/unassign, recordings, events, dashboard."""
import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _hdr(user_id, tenant_id, role_id=2) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=role_id)}"}


# ══════════════════════════════════════════════════════════════════════════════
# Camera CRUD
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bwc_camera_register_and_list(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Register
    r = await app_client.post("/api/v1/bwc/cameras", json={
        "serial_number": "BWC-00001",
        "model": "Axon Body 3",
        "storage_total_gb": 64,
    }, headers=h)
    assert r.status_code == 200, r.text
    camera_id = r.json()["id"]

    # List
    r = await app_client.get("/api/v1/bwc/cameras", headers=h)
    assert r.status_code == 200
    assert any(c["id"] == camera_id for c in r.json())

    # Get single
    r = await app_client.get(f"/api/v1/bwc/cameras/{camera_id}", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["serial_number"] == "BWC-00001"
    assert "recent_recordings" in data

    return camera_id, tenant_id, user_id, h


@pytest.mark.asyncio
async def test_bwc_camera_update(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/bwc/cameras", json={
        "serial_number": "BWC-00002", "model": "Axon Flex 2",
    }, headers=h)
    assert r.status_code == 200
    camera_id = r.json()["id"]

    r = await app_client.put(f"/api/v1/bwc/cameras/{camera_id}",
                              json={"notes": "Updated notes"}, headers=h)
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_bwc_camera_missing_serial_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/bwc/cameras", json={
        "model": "No Serial",
    }, headers=h)
    assert r.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# Assign / unassign
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bwc_assign_and_unassign(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/bwc/cameras", json={
        "serial_number": "BWC-ASSIGN-01",
    }, headers=h)
    assert r.status_code == 200
    camera_id = r.json()["id"]

    # Assign
    r = await app_client.post(f"/api/v1/bwc/cameras/{camera_id}/assign",
                               json={"user_id": str(user_id)}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # Assignments list
    r = await app_client.get(f"/api/v1/bwc/cameras/{camera_id}/assignments", headers=h)
    assert r.status_code == 200
    assert len(r.json()) >= 1

    # Unassign
    r = await app_client.post(f"/api/v1/bwc/cameras/{camera_id}/unassign",
                               json={"notes": "End of shift"}, headers=h)
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_bwc_assign_to_unavailable_camera_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/bwc/cameras", json={
        "serial_number": "BWC-STATUS-01",
    }, headers=h)
    assert r.status_code == 200
    camera_id = r.json()["id"]

    # Assign once (status → assigned)
    await app_client.post(f"/api/v1/bwc/cameras/{camera_id}/assign",
                           json={"user_id": str(user_id)}, headers=h)

    # Assign again while already assigned → 422
    r = await app_client.post(f"/api/v1/bwc/cameras/{camera_id}/assign",
                               json={"user_id": str(user_id)}, headers=h)
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Recordings
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bwc_recording_start_stop(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/bwc/cameras", json={
        "serial_number": "BWC-REC-01",
    }, headers=h)
    assert r.status_code == 200
    camera_id = r.json()["id"]

    # Start recording (camera is 'available')
    r = await app_client.post(f"/api/v1/bwc/cameras/{camera_id}/recordings/start",
                               json={"trigger_type": "manual", "title": "Test Recording"}, headers=h)
    assert r.status_code == 200, r.text
    recording_id = r.json()["id"]

    # List recordings
    r = await app_client.get("/api/v1/bwc/recordings", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert any(rec["id"] == recording_id for rec in body["items"])

    # Stop recording
    r = await app_client.post(
        f"/api/v1/bwc/cameras/{camera_id}/recordings/{recording_id}/stop",
        json={"duration_seconds": 120, "file_size_mb": 45.0},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Events + telemetry + dashboard
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bwc_events_list(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/bwc/events", headers=h)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_bwc_telemetry_update(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/bwc/cameras", json={
        "serial_number": "BWC-TELEM-01",
    }, headers=h)
    assert r.status_code == 200
    camera_id = r.json()["id"]

    # Push telemetry — low battery → status change + event
    r = await app_client.put(f"/api/v1/bwc/cameras/{camera_id}/telemetry",
                              json={"battery_pct": 10, "storage_used_gb": 30.0}, headers=h)
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_bwc_dashboard(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/bwc/dashboard", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "total_cameras" in data
    assert "available" in data
