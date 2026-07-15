"""Guard Tour Compliance — schedules, occurrences, resolve, report, dashboard."""
import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _hdr(user_id, tenant_id, role_id=2) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=role_id)}"}


async def _seed_patrol_route(admin_session, tenant_id: uuid.UUID) -> str:
    """Seed a site + patrol_route; returns route_id string."""
    site_id = uuid.uuid4()
    await admin_session.execute(text("""
        INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, 'Compliance Site')
    """), {"id": site_id, "tid": tenant_id})

    route_id = uuid.uuid4()
    await admin_session.execute(text("""
        INSERT INTO patrol_routes (id, tenant_id, site_id, name, is_active)
        VALUES (:id, :tid, :sid, 'Main Route', TRUE)
    """), {"id": route_id, "tid": tenant_id, "sid": site_id})
    await admin_session.commit()
    return str(route_id)


# ══════════════════════════════════════════════════════════════════════════════
# Tour schedule CRUD
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_compliance_schedule_crud(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    route_id = await _seed_patrol_route(admin_session, tenant_id)
    h = _hdr(user_id, tenant_id)

    # Create schedule
    r = await app_client.post("/api/v1/compliance/schedules", json={
        "route_id": route_id,
        "name": "Daily Morning Tour",
        "scheduled_time": "08:00",
        "recurrence": "daily",
        "window_minutes": 30,
    }, headers=h)
    assert r.status_code == 200, r.text
    schedule_id = r.json()["id"]

    # List
    r = await app_client.get("/api/v1/compliance/schedules", headers=h)
    assert r.status_code == 200
    assert any(s["id"] == schedule_id for s in r.json())

    # Get single
    r = await app_client.get(f"/api/v1/compliance/schedules/{schedule_id}", headers=h)
    assert r.status_code == 200
    assert r.json()["name"] == "Daily Morning Tour"

    # Update
    r = await app_client.put(f"/api/v1/compliance/schedules/{schedule_id}",
                              json={"name": "Morning Tour Updated"}, headers=h)
    assert r.status_code == 200
    assert r.json()["name"] == "Morning Tour Updated"


@pytest.mark.asyncio
async def test_compliance_schedule_invalid_recurrence_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    route_id = await _seed_patrol_route(admin_session, tenant_id)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/compliance/schedules", json={
        "route_id": route_id,
        "name": "Bad Schedule",
        "scheduled_time": "09:00",
        "recurrence": "monthly",  # invalid
    }, headers=h)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_compliance_schedule_custom_requires_days(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    route_id = await _seed_patrol_route(admin_session, tenant_id)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/compliance/schedules", json={
        "route_id": route_id,
        "name": "Custom No Days",
        "scheduled_time": "10:00",
        "recurrence": "custom",
        # days_of_week omitted → should be rejected
    }, headers=h)
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Generate occurrences + list
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_compliance_generate_and_list_occurrences(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    route_id = await _seed_patrol_route(admin_session, tenant_id)
    h = _hdr(user_id, tenant_id)

    # Create schedule
    r = await app_client.post("/api/v1/compliance/schedules", json={
        "route_id": route_id,
        "name": "Gen Test Tour",
        "scheduled_time": "07:00",
        "recurrence": "daily",
        "window_minutes": 20,
    }, headers=h)
    assert r.status_code == 200
    schedule_id = r.json()["id"]

    # Generate 3 days of occurrences
    r = await app_client.post(f"/api/v1/compliance/schedules/{schedule_id}/generate?days=3",
                               headers=h)
    assert r.status_code == 200
    gen = r.json()
    assert gen["generated"] >= 3
    assert gen["days"] == 3

    # List occurrences
    r = await app_client.get("/api/v1/compliance/occurrences", headers=h)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 3


# ══════════════════════════════════════════════════════════════════════════════
# Resolve occurrence
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_compliance_resolve_occurrence(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    route_id = await _seed_patrol_route(admin_session, tenant_id)
    h = _hdr(user_id, tenant_id)

    # Create schedule + generate
    r = await app_client.post("/api/v1/compliance/schedules", json={
        "route_id": route_id, "name": "Resolve Test",
        "scheduled_time": "06:00", "recurrence": "daily",
    }, headers=h)
    schedule_id = r.json()["id"]
    await app_client.post(f"/api/v1/compliance/schedules/{schedule_id}/generate?days=1",
                           headers=h)

    # Get occurrence
    r = await app_client.get("/api/v1/compliance/occurrences", headers=h)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    occurrence_id = items[0]["id"]

    # Resolve as completed
    r = await app_client.put(f"/api/v1/compliance/occurrences/{occurrence_id}/resolve", json={
        "status": "completed", "compliance_score": 95.0, "notes": "All checkpoints hit",
    }, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["compliance_score"] == 95.0


@pytest.mark.asyncio
async def test_compliance_resolve_invalid_status_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    route_id = await _seed_patrol_route(admin_session, tenant_id)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/compliance/schedules", json={
        "route_id": route_id, "name": "Inv Status",
        "scheduled_time": "05:00", "recurrence": "daily",
    }, headers=h)
    schedule_id = r.json()["id"]
    await app_client.post(f"/api/v1/compliance/schedules/{schedule_id}/generate?days=1", headers=h)
    r = await app_client.get("/api/v1/compliance/occurrences", headers=h)
    occurrence_id = r.json()[0]["id"]

    r = await app_client.put(f"/api/v1/compliance/occurrences/{occurrence_id}/resolve",
                              json={"status": "abandoned"}, headers=h)
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Report + dashboard
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_compliance_report(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/compliance/report?date_from=2026-06-01&date_to=2026-06-30",
                              headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "summary" in data
    assert "by_guard" in data
    assert "by_route" in data
    assert "daily_trend" in data


@pytest.mark.asyncio
async def test_compliance_dashboard(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/compliance/dashboard", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "tours_today" in data
    assert "active_schedules" in data
