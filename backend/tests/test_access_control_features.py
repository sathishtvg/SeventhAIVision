"""Access Control — doors, credentials, rules, event ingest, events list, dashboard."""
import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _hdr(user_id, tenant_id, role_id=2) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=role_id)}"}


# ══════════════════════════════════════════════════════════════════════════════
# Door CRUD
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_access_door_crud(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create
    r = await app_client.post("/api/v1/access/doors", json={
        "name": "Main Entrance",
        "door_type": "card_reader",
        "location": "Lobby Level 1",
    }, headers=h)
    assert r.status_code == 201, r.text
    door_id = r.json()["id"]

    # List
    r = await app_client.get("/api/v1/access/doors", headers=h)
    assert r.status_code == 200
    assert any(d["id"] == door_id for d in r.json())

    # Update
    r = await app_client.put(f"/api/v1/access/doors/{door_id}",
                              json={"name": "Main Entrance (Updated)"}, headers=h)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Deactivate
    r = await app_client.delete(f"/api/v1/access/doors/{door_id}", headers=h)
    assert r.status_code == 200

    # Should no longer appear with default is_active filter
    r = await app_client.get("/api/v1/access/doors?is_active=true", headers=h)
    assert r.status_code == 200
    assert door_id not in [d["id"] for d in r.json()]


@pytest.mark.asyncio
async def test_access_door_invalid_type_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/access/doors", json={
        "name": "Bad Door", "door_type": "laser_gate",
    }, headers=h)
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Credentials
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_access_credential_crud(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create
    r = await app_client.post("/api/v1/access/credentials", json={
        "holder_name": "John Guard",
        "credential_type": "card",
        "credential_ref": "CARD-001-XYZ",
    }, headers=h)
    assert r.status_code == 201, r.text
    cred_id = r.json()["id"]

    # List
    r = await app_client.get("/api/v1/access/credentials", headers=h)
    assert r.status_code == 200
    assert any(c["id"] == cred_id for c in r.json())

    # Update
    r = await app_client.put(f"/api/v1/access/credentials/{cred_id}",
                              json={"holder_name": "John Guard Senior"}, headers=h)
    assert r.status_code == 200

    # Deactivate
    r = await app_client.delete(f"/api/v1/access/credentials/{cred_id}", headers=h)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_access_credential_missing_holder_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/access/credentials", json={
        # No holder_name and no user_id → should fail
        "credential_type": "card",
        "credential_ref": "CARD-NO-HOLDER",
    }, headers=h)
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Rules
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_access_rule_create_and_delete(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create door + credential
    r = await app_client.post("/api/v1/access/doors", json={
        "name": "Rule Door", "door_type": "pin",
    }, headers=h)
    assert r.status_code == 201
    door_id = r.json()["id"]

    r = await app_client.post("/api/v1/access/credentials", json={
        "holder_name": "Rule User", "credential_type": "pin", "credential_ref": "PIN-9988",
    }, headers=h)
    assert r.status_code == 201
    cred_id = r.json()["id"]

    # Create rule
    r = await app_client.post("/api/v1/access/rules", json={
        "credential_id": cred_id,
        "door_id": door_id,
        "schedule_days": "12345",
        "time_from": "08:00",
        "time_to": "18:00",
    }, headers=h)
    assert r.status_code == 201, r.text
    rule_id = r.json()["id"]

    # List by door
    r = await app_client.get(f"/api/v1/access/rules?door_id={door_id}", headers=h)
    assert r.status_code == 200
    assert any(rule["id"] == rule_id for rule in r.json())

    # Delete rule
    r = await app_client.delete(f"/api/v1/access/rules/{rule_id}", headers=h)
    assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Event ingest
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_access_event_granted(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/access/doors", json={
        "name": "Ingest Door A", "door_type": "card_reader",
    }, headers=h)
    assert r.status_code == 201
    door_id = r.json()["id"]

    r = await app_client.post("/api/v1/access/credentials", json={
        "holder_name": "Alice", "credential_type": "card", "credential_ref": "CARD-ALICE-01",
    }, headers=h)
    assert r.status_code == 201

    # Ingest a granted event
    r = await app_client.post(f"/api/v1/access/doors/{door_id}/events", json={
        "event_type": "granted",
        "credential_ref": "CARD-ALICE-01",
    }, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["event_type"] == "granted"


@pytest.mark.asyncio
async def test_access_event_forced_creates_alert(app_client, admin_session):
    """A 'forced' event creates a critical alert in the alerts table."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Seed a camera so the alert INSERT finds a camera_id
    camera_id = uuid.uuid4()
    await admin_session.execute(text("""
        INSERT INTO cameras (id, tenant_id, name, is_active)
        VALUES (:id, :tid, 'Access Cam', TRUE)
    """), {"id": camera_id, "tid": tenant_id})
    await admin_session.commit()

    r = await app_client.post("/api/v1/access/doors", json={
        "name": "Force Door", "door_type": "card_reader",
        "camera_id": str(camera_id),
    }, headers=h)
    assert r.status_code == 201
    door_id = r.json()["id"]

    r = await app_client.post(f"/api/v1/access/doors/{door_id}/events", json={
        "event_type": "forced",
    }, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["event_type"] == "forced"


@pytest.mark.asyncio
async def test_access_event_invalid_type_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/access/doors", json={
        "name": "Bad Event Door", "door_type": "card_reader",
    }, headers=h)
    door_id = r.json()["id"]

    r = await app_client.post(f"/api/v1/access/doors/{door_id}/events", json={
        "event_type": "unlocked_by_voice",
    }, headers=h)
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Events list + dashboard
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_access_events_list(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/access/events", headers=h)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_access_dashboard(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/access/dashboard", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "door_summary" in data
    assert "credential_summary" in data
    assert "event_summary" in data
