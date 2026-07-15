"""Functional smoke coverage for the remaining routers built in this pass
(watchlist, zones, alerts, incidents) — confirms actual CRUD behavior, not
just that main.py imports them cleanly."""

import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


@pytest.mark.asyncio
async def test_plate_watchlist_crud_round_trip(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await app_client.post(
        "/api/v1/watchlist/plates", json={"plate_number": "ABC123", "list_type": "block", "reason": "test"}, headers=headers
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]

    list_resp = await app_client.get("/api/v1/watchlist/plates", headers=headers)
    assert any(r["plate_number"] == "ABC123" and r["is_active"] for r in list_resp.json())

    delete_resp = await app_client.delete(f"/api/v1/watchlist/plates/{entry_id}", headers=headers)
    assert delete_resp.status_code == 200

    list_resp_2 = await app_client.get("/api/v1/watchlist/plates", headers=headers)
    matching = [r for r in list_resp_2.json() if r["id"] == entry_id]
    assert matching[0]["is_active"] is False


@pytest.mark.asyncio
async def test_face_watchlist_crud_round_trip(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    headers = {"Authorization": f"Bearer {token}"}

    embedding = [0.1] * 512
    create_resp = await app_client.post(
        "/api/v1/watchlist/faces", json={"person_name": "Jane Doe", "list_type": "allow", "embedding": embedding}, headers=headers
    )
    assert create_resp.status_code == 201

    list_resp = await app_client.get("/api/v1/watchlist/faces", headers=headers)
    assert any(r["person_name"] == "Jane Doe" for r in list_resp.json())


@pytest.mark.asyncio
async def test_zone_crud_round_trip(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    headers = {"Authorization": f"Bearer {token}"}

    camera_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, 'Cam')"), {"id": camera_id, "tid": tenant_id}
    )
    await admin_session.commit()

    create_resp = await app_client.post(
        "/api/v1/zones",
        json={
            "camera_id": str(camera_id), "name": "Server Room",
            "polygon": [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}, {"x": 0.9, "y": 0.9}, {"x": 0.1, "y": 0.9}],
            "severity": "critical",
        },
        headers=headers,
    )
    assert create_resp.status_code == 201

    list_resp = await app_client.get("/api/v1/zones", headers=headers)
    assert any(r["name"] == "Server Room" and r["severity"] == "critical" for r in list_resp.json())


@pytest.mark.asyncio
async def test_alert_acknowledge_flow(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    headers = {"Authorization": f"Bearer {token}"}

    camera_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, 'Cam')"), {"id": camera_id, "tid": tenant_id}
    )
    alert_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, title, status) "
            "VALUES (:id, :tid, :cid, 'lpr', 'critical', 'Test Alert', 'open')"
        ),
        {"id": alert_id, "tid": tenant_id, "cid": camera_id},
    )
    await admin_session.commit()

    list_resp = await app_client.get("/api/v1/alerts", headers=headers)
    assert any(r["id"] == str(alert_id) for r in list_resp.json()["items"])

    ack_resp = await app_client.post(f"/api/v1/alerts/{alert_id}/acknowledge", headers=headers)
    assert ack_resp.status_code == 200
    assert ack_resp.json()["status"] == "acknowledged"

    # Re-acknowledging an already-acknowledged alert should 404 (not "open" anymore).
    ack_again = await app_client.post(f"/api/v1/alerts/{alert_id}/acknowledge", headers=headers)
    assert ack_again.status_code == 404


@pytest.mark.asyncio
async def test_incident_note_and_resolve_flow(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    headers = {"Authorization": f"Bearer {token}"}

    camera_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, 'Cam')"), {"id": camera_id, "tid": tenant_id}
    )
    incident_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, is_auto_created) "
            "VALUES (:id, :tid, :cid, 'Test Incident', 'high', 'open', TRUE)"
        ),
        {"id": incident_id, "tid": tenant_id, "cid": camera_id},
    )
    await admin_session.commit()

    note_resp = await app_client.post(
        f"/api/v1/incidents/{incident_id}/notes", json={"note": "Investigating now"}, headers=headers
    )
    assert note_resp.status_code == 200

    resolve_resp = await app_client.post(f"/api/v1/incidents/{incident_id}/resolve", headers=headers)
    assert resolve_resp.status_code == 200

    list_resp = await app_client.get("/api/v1/incidents?status_filter=resolved", headers=headers)
    assert any(r["id"] == str(incident_id) for r in list_resp.json()["items"])
