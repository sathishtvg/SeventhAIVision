"""Tests for P4-E bulk operations:
- POST /api/v1/alerts/bulk-acknowledge
- POST /api/v1/alerts/bulk-dismiss
- POST /api/v1/incidents/bulk-resolve
- POST /api/v1/incidents/bulk-update-status
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


async def _seed_camera(admin_session, tenant_id: uuid.UUID) -> uuid.UUID:
    camera_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, 'Cam')"),
        {"id": camera_id, "tid": tenant_id},
    )
    await admin_session.commit()
    return camera_id


async def _seed_alert(
    admin_session,
    tenant_id: uuid.UUID,
    camera_id: uuid.UUID,
    *,
    status: str = "open",
) -> uuid.UUID:
    alert_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, title, status) "
            "VALUES (:id, :tid, :cid, 'lpr', 'critical', 'Test Alert', :status)"
        ),
        {"id": alert_id, "tid": tenant_id, "cid": camera_id, "status": status},
    )
    await admin_session.commit()
    return alert_id


async def _seed_incident(
    admin_session,
    tenant_id: uuid.UUID,
    camera_id: uuid.UUID,
    *,
    status: str = "open",
) -> uuid.UUID:
    incident_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, is_auto_created) "
            "VALUES (:id, :tid, :cid, 'Test Incident', 'high', :status, TRUE)"
        ),
        {"id": incident_id, "tid": tenant_id, "cid": camera_id, "status": status},
    )
    await admin_session.commit()
    return incident_id


# ── Alert: bulk-acknowledge ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_acknowledge_requires_permission(app_client, admin_session):
    """Role 6 (viewer) has no alert:acknowledge — must 403."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)
    token = create_access_token(str(user_id), str(tenant_id), role_id=6)
    resp = await app_client.post(
        "/api/v1/alerts/bulk-acknowledge",
        json={"ids": [str(uuid.uuid4())]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_bulk_acknowledge_happy_path(app_client, admin_session):
    """Two open alerts are acknowledged; result reflects updated+skipped counts."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    camera_id = await _seed_camera(admin_session, tenant_id)
    a1 = await _seed_alert(admin_session, tenant_id, camera_id, status="open")
    a2 = await _seed_alert(admin_session, tenant_id, camera_id, status="open")
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    resp = await app_client.post(
        "/api/v1/alerts/bulk-acknowledge",
        json={"ids": [str(a1), str(a2)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 2
    assert body["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_acknowledge_skips_non_open(app_client, admin_session):
    """Already-acknowledged alert is not double-counted; skipped=1."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    camera_id = await _seed_camera(admin_session, tenant_id)
    a_open = await _seed_alert(admin_session, tenant_id, camera_id, status="open")
    a_acked = await _seed_alert(admin_session, tenant_id, camera_id, status="acknowledged")
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    resp = await app_client.post(
        "/api/v1/alerts/bulk-acknowledge",
        json={"ids": [str(a_open), str(a_acked)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 1
    assert body["skipped"] == 1


@pytest.mark.asyncio
async def test_bulk_acknowledge_rejects_over_100(app_client, admin_session):
    """More than 100 IDs must return 422."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    resp = await app_client.post(
        "/api/v1/alerts/bulk-acknowledge",
        json={"ids": [str(uuid.uuid4()) for _ in range(101)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bulk_acknowledge_rejects_empty(app_client, admin_session):
    """Empty ids list must return 422."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    resp = await app_client.post(
        "/api/v1/alerts/bulk-acknowledge",
        json={"ids": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


# ── Alert: bulk-dismiss ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_dismiss_happy_path(app_client, admin_session):
    """Open + acknowledged alerts both get dismissed."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    camera_id = await _seed_camera(admin_session, tenant_id)
    a1 = await _seed_alert(admin_session, tenant_id, camera_id, status="open")
    a2 = await _seed_alert(admin_session, tenant_id, camera_id, status="acknowledged")
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    resp = await app_client.post(
        "/api/v1/alerts/bulk-dismiss",
        json={"ids": [str(a1), str(a2)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 2
    assert body["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_dismiss_skips_already_dismissed(app_client, admin_session):
    """Already-dismissed alert is skipped."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    camera_id = await _seed_camera(admin_session, tenant_id)
    a_open = await _seed_alert(admin_session, tenant_id, camera_id, status="open")
    a_dismissed = await _seed_alert(admin_session, tenant_id, camera_id, status="dismissed")
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    resp = await app_client.post(
        "/api/v1/alerts/bulk-dismiss",
        json={"ids": [str(a_open), str(a_dismissed)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 1
    assert body["skipped"] == 1


# ── Incident: bulk-resolve ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_resolve_happy_path(app_client, admin_session):
    """Two open incidents are resolved; updated=2 skipped=0."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    camera_id = await _seed_camera(admin_session, tenant_id)
    i1 = await _seed_incident(admin_session, tenant_id, camera_id, status="open")
    i2 = await _seed_incident(admin_session, tenant_id, camera_id, status="investigating")
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    resp = await app_client.post(
        "/api/v1/incidents/bulk-resolve",
        json={"ids": [str(i1), str(i2)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 2
    assert body["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_resolve_skips_already_resolved(app_client, admin_session):
    """Already-resolved incident is skipped."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    camera_id = await _seed_camera(admin_session, tenant_id)
    i_open = await _seed_incident(admin_session, tenant_id, camera_id, status="open")
    i_resolved = await _seed_incident(admin_session, tenant_id, camera_id, status="resolved")
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    resp = await app_client.post(
        "/api/v1/incidents/bulk-resolve",
        json={"ids": [str(i_open), str(i_resolved)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 1
    assert body["skipped"] == 1


# ── Incident: bulk-update-status ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_update_status_happy_path(app_client, admin_session):
    """Two open incidents moved to 'dispatched'."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    camera_id = await _seed_camera(admin_session, tenant_id)
    i1 = await _seed_incident(admin_session, tenant_id, camera_id, status="open")
    i2 = await _seed_incident(admin_session, tenant_id, camera_id, status="open")
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    resp = await app_client.post(
        "/api/v1/incidents/bulk-update-status",
        json={"ids": [str(i1), str(i2)], "status": "dispatched"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 2
    assert body["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_update_status_rejects_invalid_status(app_client, admin_session):
    """Unknown status value must return 422."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    resp = await app_client.post(
        "/api/v1/incidents/bulk-update-status",
        json={"ids": [str(uuid.uuid4())], "status": "not_a_real_status"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bulk_update_status_rejects_over_100(app_client, admin_session):
    """More than 100 IDs must return 422."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    resp = await app_client.post(
        "/api/v1/incidents/bulk-update-status",
        json={"ids": [str(uuid.uuid4()) for _ in range(101)], "status": "open"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
