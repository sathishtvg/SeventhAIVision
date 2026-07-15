"""Contractor management — CRUD, vetting, work permits, deliveries, dashboard."""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _hdr(user_id, tenant_id, role_id=2) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=role_id)}"}


# ══════════════════════════════════════════════════════════════════════════════
# Contractor CRUD
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_contractor_crud(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create
    r = await app_client.post("/api/v1/contractors", json={
        "company_name": "BuildRight Pte Ltd",
        "contact_name": "Alice Tan",
        "contact_email": "alice@buildright.sg",
        "contact_phone": "+6591234567",
    }, headers=h)
    assert r.status_code == 200, r.text
    contractor_id = r.json()["id"]

    # List
    r = await app_client.get("/api/v1/contractors", headers=h)
    assert r.status_code == 200
    assert any(c["id"] == contractor_id for c in r.json())

    # Get single
    r = await app_client.get(f"/api/v1/contractors/{contractor_id}", headers=h)
    assert r.status_code == 200
    assert r.json()["company_name"] == "BuildRight Pte Ltd"

    # Update
    r = await app_client.put(f"/api/v1/contractors/{contractor_id}",
                              json={"contact_name": "Alice Wong"}, headers=h)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_contractor_vetting(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/contractors", json={
        "company_name": "Vet Me Corp",
        "contact_name": "Bob Lee",
    }, headers=h)
    assert r.status_code == 200
    contractor_id = r.json()["id"]

    # Approve vetting
    r = await app_client.put(f"/api/v1/contractors/{contractor_id}/vet",
                              json={"vetting_status": "approved", "notes": "Passed checks"}, headers=h)
    assert r.status_code == 200

    # Confirm status
    r = await app_client.get(f"/api/v1/contractors/{contractor_id}", headers=h)
    assert r.status_code == 200
    assert r.json()["vetting_status"] == "approved"


# ══════════════════════════════════════════════════════════════════════════════
# Work permits (require approved contractor)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_work_permit_lifecycle(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create contractor and approve it
    r = await app_client.post("/api/v1/contractors", json={
        "company_name": "PipeWorks Ltd", "contact_name": "C W",
    }, headers=h)
    assert r.status_code == 200
    contractor_id = r.json()["id"]
    await app_client.put(f"/api/v1/contractors/{contractor_id}/vet",
                          json={"vetting_status": "approved"}, headers=h)

    # Create work permit
    r = await app_client.post("/api/v1/work-permits", json={
        "contractor_id": contractor_id,
        "title": "Plumbing repair",
        "work_type": "maintenance",
        "planned_start": "2026-07-01T08:00:00Z",
        "planned_end": "2026-07-01T17:00:00Z",
    }, headers=h)
    assert r.status_code == 200, r.text
    permit_id = r.json()["id"]

    # List
    r = await app_client.get("/api/v1/work-permits", headers=h)
    assert r.status_code == 200
    assert any(p["id"] == permit_id for p in r.json())

    # Safety briefing
    r = await app_client.put(f"/api/v1/work-permits/{permit_id}/safety-briefing",
                              json={"briefed_by": str(user_id)}, headers=h)
    assert r.status_code == 200

    # Approve
    r = await app_client.put(f"/api/v1/work-permits/{permit_id}/approve",
                              json={"notes": "Looks good"}, headers=h)
    assert r.status_code == 200

    # Complete
    r = await app_client.put(f"/api/v1/work-permits/{permit_id}/complete",
                              json={}, headers=h)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_work_permit_requires_approved_contractor(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Un-vetted contractor
    r = await app_client.post("/api/v1/contractors", json={
        "company_name": "Unvetted Co", "contact_name": "Unv",
    }, headers=h)
    assert r.status_code == 200
    contractor_id = r.json()["id"]

    r = await app_client.post("/api/v1/work-permits", json={
        "contractor_id": contractor_id,
        "title": "Attempted job",
        "work_type": "maintenance",
        "planned_start": "2026-07-02T08:00:00Z",
        "planned_end": "2026-07-02T12:00:00Z",
    }, headers=h)
    assert r.status_code in (400, 422), r.text


# ══════════════════════════════════════════════════════════════════════════════
# Deliveries
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delivery_lifecycle(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create delivery
    r = await app_client.post("/api/v1/deliveries", json={
        "supplier_name": "Acme Supplies",
        "expected_at": "2026-07-05T10:00:00Z",
        "description": "Office chairs x10",
    }, headers=h)
    assert r.status_code == 200, r.text
    delivery_id = r.json()["id"]

    # List
    r = await app_client.get("/api/v1/deliveries", headers=h)
    assert r.status_code == 200
    assert any(d["id"] == delivery_id for d in r.json())

    # Receive
    r = await app_client.put(f"/api/v1/deliveries/{delivery_id}/receive",
                              json={"received_by": str(user_id), "notes": "All good"}, headers=h)
    assert r.status_code == 200

    # Collect
    r = await app_client.put(f"/api/v1/deliveries/{delivery_id}/collect",
                              json={"collected_by": "John Smith"}, headers=h)
    assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_contractors_dashboard(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/contractors-dashboard", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "total_contractors" in data


# ══════════════════════════════════════════════════════════════════════════════
# Contractor scheduler (check_contractor_expiry)
# ══════════════════════════════════════════════════════════════════════════════

async def _seed_contractor_with_camera(session):
    """Returns (tenant_id, contractor_id) with an approved contractor and one camera."""
    tenant_id, user_id = await _seed_user_with_role(session, role_id=2)
    cam_id = uuid.uuid4()
    await session.execute(
        text("INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) "
             "VALUES (:id, :tid, 'SchedulerCam', '[]'::jsonb)"),
        {"id": cam_id, "tid": tenant_id},
    )
    contractor_id = uuid.uuid4()
    await session.execute(
        text("""
            INSERT INTO contractors
                (id, tenant_id, company_name, vetting_status)
            VALUES (:id, :tid, 'SchedulerCo', 'approved')
        """),
        {"id": contractor_id, "tid": tenant_id},
    )
    await session.commit()
    return tenant_id, contractor_id


@pytest.mark.asyncio
async def test_contractor_permit_auto_expire(admin_session):
    """Past-end work permits are set to 'expired' by the scheduler."""
    from app.scheduler_main import check_contractor_expiry

    tenant_id, contractor_id = await _seed_contractor_with_camera(admin_session)

    past_end = datetime.now(timezone.utc) - timedelta(hours=2)
    permit_id = uuid.uuid4()
    await admin_session.execute(
        text("""
            INSERT INTO work_permits
                (id, tenant_id, contractor_id, work_description, start_at, end_at, status)
            VALUES (:id, :tid, :cid, 'Old plumbing job', :s, :e, 'approved')
        """),
        {"id": permit_id, "tid": tenant_id, "cid": contractor_id,
         "s": past_end - timedelta(days=1), "e": past_end},
    )
    await admin_session.commit()

    result = await check_contractor_expiry(admin_session, AsyncMock(), tenant_ids=[tenant_id])
    assert result["expired_permits"] >= 1

    row = (await admin_session.execute(
        text("SELECT status FROM work_permits WHERE id = :id"),
        {"id": permit_id},
    )).first()
    assert row is not None and row[0] == "expired"


@pytest.mark.asyncio
async def test_contractor_permit_expiring_alert(admin_session):
    """Permits ending within 4 hours create a contractor.permit_expiring alert."""
    from app.scheduler_main import check_contractor_expiry

    tenant_id, contractor_id = await _seed_contractor_with_camera(admin_session)

    ending_soon = datetime.now(timezone.utc) + timedelta(hours=2)
    permit_id = uuid.uuid4()
    await admin_session.execute(
        text("""
            INSERT INTO work_permits
                (id, tenant_id, contractor_id, work_description, start_at, end_at, status)
            VALUES (:id, :tid, :cid, 'Expiring soon job', :s, :e, 'approved')
        """),
        {"id": permit_id, "tid": tenant_id, "cid": contractor_id,
         "s": datetime.now(timezone.utc) - timedelta(hours=6), "e": ending_soon},
    )
    await admin_session.commit()

    mock_redis = AsyncMock()
    result = await check_contractor_expiry(admin_session, mock_redis, tenant_ids=[tenant_id])
    assert result["permit_alerts"] >= 1

    row = (await admin_session.execute(
        text("""
            SELECT id FROM alerts
            WHERE alert_code = 'contractor.permit_expiring'
              AND (message_params->>'permit_id')::text = :pid
        """),
        {"pid": str(permit_id)},
    )).first()
    assert row is not None, "Expected contractor.permit_expiring alert"
    mock_redis.publish.assert_called()


@pytest.mark.asyncio
async def test_contractor_accreditation_expiring_alert(admin_session):
    """Accreditations expiring within 30 days create a contractor.accreditation_expiring alert."""
    from app.scheduler_main import check_contractor_expiry

    tenant_id, contractor_id = await _seed_contractor_with_camera(admin_session)

    expires_soon = (datetime.now(timezone.utc) + timedelta(days=10)).date()
    accred_id = uuid.uuid4()
    await admin_session.execute(
        text("""
            INSERT INTO contractor_accreditations
                (id, tenant_id, contractor_id, document_type, expires_at)
            VALUES (:id, :tid, :cid, 'BizSafe Star', :exp)
        """),
        {"id": accred_id, "tid": tenant_id, "cid": contractor_id, "exp": expires_soon},
    )
    await admin_session.commit()

    mock_redis = AsyncMock()
    result = await check_contractor_expiry(admin_session, mock_redis, tenant_ids=[tenant_id])
    assert result["accreditation_alerts"] >= 1

    row = (await admin_session.execute(
        text("""
            SELECT id FROM alerts
            WHERE alert_code = 'contractor.accreditation_expiring'
              AND (message_params->>'accreditation_id')::text = :aid
        """),
        {"aid": str(accred_id)},
    )).first()
    assert row is not None, "Expected contractor.accreditation_expiring alert"
    mock_redis.publish.assert_called()
