"""Phase 10 (Enterprise UX Upgrade) feature tests.

Covers:
- Sites: CRUD round-trip, site–camera assignment, site enrichment on camera list
- Tenant module licensing: list all 8 modules, enable/disable, non-super-admin rejected
- Recordings: start / stop / list lifecycle
- Stream validation: invalid URL returns valid=False (not a 5xx)
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


# ─── Sites ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_site_crud_round_trip(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)  # admin
    headers = {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=2)}"}

    # Create
    resp = await app_client.post(
        "/api/v1/sites",
        json={"name": "Marina Bay HQ", "address": "1 Marina Boulevard, Singapore"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    site_id = resp.json()["id"]

    # Get by ID
    resp = await app_client.get(f"/api/v1/sites/{site_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Marina Bay HQ"
    assert data["address"] == "1 Marina Boulevard, Singapore"

    # List
    resp = await app_client.get("/api/v1/sites", headers=headers)
    assert resp.status_code == 200
    assert any(s["id"] == site_id for s in resp.json())

    # Update
    resp = await app_client.put(
        f"/api/v1/sites/{site_id}",
        json={"name": "Marina Bay HQ (Updated)"},
        headers=headers,
    )
    assert resp.status_code == 200

    # Deactivate
    resp = await app_client.delete(f"/api/v1/sites/{site_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_site_camera_assignment(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    headers = {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=2)}"}

    # Create a site via API
    site_resp = await app_client.post(
        "/api/v1/sites",
        json={"name": "Changi Warehouse", "address": "10 Changi North Way"},
        headers=headers,
    )
    assert site_resp.status_code == 201
    site_id = site_resp.json()["id"]

    # Seed a camera assigned to this site directly (the camera:create permission
    # is separate from site:manage; easier to seed via SQL for this test)
    camera_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name, site_id) VALUES (:id, :tid, 'Gate Cam', :sid)"),
        {"id": camera_id, "tid": tenant_id, "sid": site_id},
    )
    await admin_session.commit()

    # GET /sites/{site_id}/cameras shows the assigned camera
    resp = await app_client.get(f"/api/v1/sites/{site_id}/cameras", headers=headers)
    assert resp.status_code == 200
    cameras = resp.json()
    assert any(str(c["id"]) == str(camera_id) for c in cameras)


@pytest.mark.asyncio
async def test_camera_list_includes_site_name(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    headers = {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=2)}"}

    # Seed a site and camera with FK
    site_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO sites (id, tenant_id, name) VALUES (:sid, :tid, 'North Wing')"),
        {"sid": site_id, "tid": tenant_id},
    )
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name, site_id) VALUES (:cid, :tid, 'Lobby Cam', :sid)"),
        {"sid": site_id, "tid": tenant_id, "cid": camera_id},
    )
    await admin_session.commit()

    resp = await app_client.get("/api/v1/cameras", headers=headers)
    assert resp.status_code == 200
    cameras = resp.json()
    cam = next((c for c in cameras if str(c["id"]) == str(camera_id)), None)
    assert cam is not None
    assert cam["site_name"] == "North Wing"


# ─── Tenant module licensing ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_license_list_returns_all_modules(app_client, admin_session):
    """Super-admin listing licenses for a tenant sees all 11 modules."""
    # Create the subject tenant
    subject_tenant_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'LicenseTenant', :slug)"),
        {"id": subject_tenant_id, "slug": f"lt-{subject_tenant_id.hex[:8]}"},
    )
    await admin_session.commit()

    # Super-admin user in a separate tenant
    sa_tenant_id, sa_user_id = await _seed_user_with_role(admin_session, role_id=1)
    headers = {"Authorization": f"Bearer {create_access_token(str(sa_user_id), str(sa_tenant_id), role_id=1)}"}

    resp = await app_client.get(f"/api/v1/licenses/{subject_tenant_id}", headers=headers)
    assert resp.status_code == 200
    modules = resp.json()
    module_types = {m["module_type"] for m in modules}
    assert module_types == {
        "lpr", "face", "intrusion", "ppe", "crowd", "fire_smoke", "weapon", "behavior",
        "tampering", "abandoned", "fall",
    }
    # New tenant has no rows yet — all should show as disabled
    for m in modules:
        assert m["is_enabled"] is False


@pytest.mark.asyncio
async def test_license_upsert_enables_module(app_client, admin_session):
    subject_tenant_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'LicTenant2', :slug)"),
        {"id": subject_tenant_id, "slug": f"lt2-{subject_tenant_id.hex[:8]}"},
    )
    await admin_session.commit()

    sa_tenant_id, sa_user_id = await _seed_user_with_role(admin_session, role_id=1)
    headers = {"Authorization": f"Bearer {create_access_token(str(sa_user_id), str(sa_tenant_id), role_id=1)}"}

    # Enable LPR module for subject tenant
    resp = await app_client.put(
        f"/api/v1/licenses/{subject_tenant_id}/lpr",
        json={"is_enabled": True, "max_cameras": 5},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["is_enabled"] is True

    # Verify in list
    list_resp = await app_client.get(f"/api/v1/licenses/{subject_tenant_id}", headers=headers)
    lpr = next(m for m in list_resp.json() if m["module_type"] == "lpr")
    assert lpr["is_enabled"] is True
    assert lpr["max_cameras"] == 5


@pytest.mark.asyncio
async def test_license_admin_cannot_access(app_client, admin_session):
    """role_id=2 (admin) does not have license:manage permission."""
    subject_tenant_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'LicTenant3', :slug)"),
        {"id": subject_tenant_id, "slug": f"lt3-{subject_tenant_id.hex[:8]}"},
    )
    await admin_session.commit()

    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    headers = {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=2)}"}

    resp = await app_client.get(f"/api/v1/licenses/{subject_tenant_id}", headers=headers)
    assert resp.status_code == 403


# ─── Recordings ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recording_start_stop_list(app_client, admin_session):
    """Start a recording, verify it appears in list, stop it, verify completed status."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    headers = {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=2)}"}

    # Seed camera + stream (bypass API to avoid needing a real RTSP URL at camera creation)
    camera_id = uuid.uuid4()
    stream_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, 'RecordCam')"),
        {"id": camera_id, "tid": tenant_id},
    )
    await admin_session.execute(
        text(
            "INSERT INTO streams (id, tenant_id, camera_id, url, protocol) "
            "VALUES (:id, :tid, :cid, 'rtsp://invalid.test/stream', 'rtsp')"
        ),
        {"id": stream_id, "tid": tenant_id, "cid": camera_id},
    )
    await admin_session.commit()

    # Start recording — background task will fail (no real RTSP), but API returns 201
    start_resp = await app_client.post(
        f"/api/v1/cameras/{camera_id}/streams/{stream_id}/recordings/start",
        headers=headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    recording_id = start_resp.json()["recording_id"]

    # List recordings for this stream
    list_resp = await app_client.get(
        f"/api/v1/cameras/{camera_id}/streams/{stream_id}/recordings",
        headers=headers,
    )
    assert list_resp.status_code == 200
    recordings = list_resp.json()
    assert any(r["id"] == recording_id for r in recordings)

    # Stop recording
    stop_resp = await app_client.post(
        f"/api/v1/cameras/{camera_id}/streams/{stream_id}/recordings/{recording_id}/stop",
        headers=headers,
    )
    assert stop_resp.status_code == 200
    # Status should be completed (either by stop or by the already-failed background task)
    assert stop_resp.json()["status"] in ("completed", "failed")


# ─── Stream validation ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_stream_unreachable_returns_valid_false(app_client, admin_session):
    """An unreachable RTSP URL must return valid=False, never a 5xx."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    headers = {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=2)}"}

    resp = await app_client.post(
        "/api/v1/cameras/validate-stream",
        json={"url": "rtsp://192.0.2.1:554/nonexistent"},  # RFC 5737 TEST-NET, guaranteed unreachable
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is False
    assert body["error"] is not None
