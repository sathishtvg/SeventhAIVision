"""Tests for Tier 2 Feature 8: Scheduled report delivery.

Covers:
- List schedules (empty)
- Create schedule (admin)
- Non-admin cannot create (403)
- Get single schedule
- Update schedule
- List deliveries (empty)
- run-now queues a delivery_id
- Delete schedule
- Tenant isolation: admin from tenant B cannot see tenant A's schedule
- Invalid report_type rejected (422)
- Invalid frequency rejected (422)
- Invalid hour_utc rejected (422)
"""

import uuid
import pytest
import pytest_asyncio

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _jwt(tenant_id: uuid.UUID, user_id: uuid.UUID, role_id: int) -> str:
    return create_access_token(str(user_id), str(tenant_id), role_id=role_id)


@pytest_asyncio.fixture
async def _admin(admin_session):
    """admin — role 2 — has report:schedule."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    return _jwt(tenant_id, user_id, 2), tenant_id, user_id


@pytest_asyncio.fixture
async def _operator(admin_session):
    """operator — role 4 — does NOT have report:schedule."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    return _jwt(tenant_id, user_id, 4), tenant_id, user_id


@pytest_asyncio.fixture
async def _admin_b(admin_session):
    """admin in a different tenant for isolation checks."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    return _jwt(tenant_id, user_id, 2), tenant_id, user_id


async def _create_schedule(app_client, jwt: str, **kwargs) -> dict:
    payload = {
        "name": "Daily Summary",
        "report_type": "site_summary",
        "frequency": "daily",
        "hour_utc": 8,
        "delivery_method": "email",
        "recipients": ["ops@example.com"],
    }
    payload.update(kwargs)
    resp = await app_client.post(
        "/api/v1/scheduled-reports",
        json=payload,
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200, f"create_schedule failed: {resp.json()}"
    return resp.json()


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_schedules_empty(_admin, app_client):
    jwt, _, _ = _admin
    resp = await app_client.get(
        "/api/v1/scheduled-reports",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_create_schedule(_admin, app_client):
    jwt, _, _ = _admin
    sched = await _create_schedule(app_client, jwt)
    assert sched["name"] == "Daily Summary"
    assert sched["report_type"] == "site_summary"
    assert sched["frequency"] == "daily"
    assert sched["hour_utc"] == 8
    assert sched["is_active"] is True
    assert "id" in sched
    assert sched["next_run_at"] is not None


@pytest.mark.asyncio
async def test_operator_cannot_create_schedule(_operator, app_client):
    jwt, _, _ = _operator
    resp = await app_client.post(
        "/api/v1/scheduled-reports",
        json={"name": "x", "report_type": "site_summary", "frequency": "daily",
              "hour_utc": 8, "delivery_method": "email"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_schedule(_admin, app_client):
    jwt, _, _ = _admin
    created = await _create_schedule(app_client, jwt, name="Get Test")
    resp = await app_client.get(
        f"/api/v1/scheduled-reports/{created['id']}",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


@pytest.mark.asyncio
async def test_get_nonexistent_schedule_404(_admin, app_client):
    jwt, _, _ = _admin
    resp = await app_client.get(
        f"/api/v1/scheduled-reports/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_schedule(_admin, app_client):
    jwt, _, _ = _admin
    created = await _create_schedule(app_client, jwt, name="Before Update")
    resp = await app_client.put(
        f"/api/v1/scheduled-reports/{created['id']}",
        json={"name": "After Update", "is_active": False},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "After Update"
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_list_schedules_returns_created(_admin, app_client):
    jwt, _, _ = _admin
    sched = await _create_schedule(app_client, jwt, name="Listed Schedule")
    resp = await app_client.get(
        "/api/v1/scheduled-reports",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert sched["id"] in ids


@pytest.mark.asyncio
async def test_list_deliveries_empty(_admin, app_client):
    jwt, _, _ = _admin
    sched = await _create_schedule(app_client, jwt, name="No Deliveries")
    resp = await app_client.get(
        f"/api/v1/scheduled-reports/{sched['id']}/deliveries",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_run_now_queues_delivery(_admin, app_client):
    jwt, _, _ = _admin
    sched = await _create_schedule(app_client, jwt, name="Run Now Test")
    resp = await app_client.post(
        f"/api/v1/scheduled-reports/{sched['id']}/run-now",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert "delivery_id" in result
    assert result["status"] == "queued"


@pytest.mark.asyncio
async def test_run_now_creates_pending_delivery(_admin, app_client):
    jwt, _, _ = _admin
    sched = await _create_schedule(app_client, jwt, name="Run Now Delivery")
    await app_client.post(
        f"/api/v1/scheduled-reports/{sched['id']}/run-now",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    deliveries_resp = await app_client.get(
        f"/api/v1/scheduled-reports/{sched['id']}/deliveries",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert deliveries_resp.status_code == 200
    deliveries = deliveries_resp.json()
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_delete_schedule(_admin, app_client):
    jwt, _, _ = _admin
    sched = await _create_schedule(app_client, jwt, name="To Delete")
    resp = await app_client.delete(
        f"/api/v1/scheduled-reports/{sched['id']}",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # Confirm gone
    get_resp = await app_client.get(
        f"/api/v1/scheduled-reports/{sched['id']}",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_tenant_isolation(_admin, _admin_b, app_client):
    jwt_a, _, _ = _admin
    jwt_b, _, _ = _admin_b
    sched_a = await _create_schedule(app_client, jwt_a, name="Tenant A Only")

    resp_b = await app_client.get(
        "/api/v1/scheduled-reports",
        headers={"Authorization": f"Bearer {jwt_b}"},
    )
    ids_b = [s["id"] for s in resp_b.json()]
    assert sched_a["id"] not in ids_b


@pytest.mark.asyncio
async def test_invalid_report_type_rejected(_admin, app_client):
    jwt, _, _ = _admin
    resp = await app_client.post(
        "/api/v1/scheduled-reports",
        json={"name": "bad", "report_type": "unknown_type", "frequency": "daily",
              "hour_utc": 8, "delivery_method": "email"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_frequency_rejected(_admin, app_client):
    jwt, _, _ = _admin
    resp = await app_client.post(
        "/api/v1/scheduled-reports",
        json={"name": "bad", "report_type": "site_summary", "frequency": "hourly",
              "hour_utc": 8, "delivery_method": "email"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_hour_utc_rejected(_admin, app_client):
    jwt, _, _ = _admin
    resp = await app_client.post(
        "/api/v1/scheduled-reports",
        json={"name": "bad", "report_type": "site_summary", "frequency": "daily",
              "hour_utc": 25, "delivery_method": "email"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_all_report_types_accepted(_admin, app_client):
    jwt, _, _ = _admin
    for rt in ("site_summary", "dob", "incident_summary"):
        resp = await app_client.post(
            "/api/v1/scheduled-reports",
            json={"name": f"Test {rt}", "report_type": rt, "frequency": "daily",
                  "hour_utc": 8, "delivery_method": "email"},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert resp.status_code == 200, f"report_type={rt} unexpectedly rejected: {resp.json()}"


@pytest.mark.asyncio
async def test_all_frequencies_accepted(_admin, app_client):
    jwt, _, _ = _admin
    for freq in ("daily", "weekly", "monthly"):
        resp = await app_client.post(
            "/api/v1/scheduled-reports",
            json={"name": f"Test {freq}", "report_type": "site_summary", "frequency": freq,
                  "hour_utc": 8, "delivery_method": "email"},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert resp.status_code == 200, f"frequency={freq} unexpectedly rejected: {resp.json()}"


@pytest.mark.asyncio
async def test_weekly_schedule_stores_day_of_week(_admin, app_client):
    jwt, _, _ = _admin
    sched = await _create_schedule(app_client, jwt, name="Weekly Mon",
                                   frequency="weekly", day_of_week=0)
    assert sched["frequency"] == "weekly"
    assert sched["day_of_week"] == 0


@pytest.mark.asyncio
async def test_monthly_schedule_stores_day_of_month(_admin, app_client):
    jwt, _, _ = _admin
    sched = await _create_schedule(app_client, jwt, name="Monthly 15th",
                                   frequency="monthly", day_of_month=15)
    assert sched["frequency"] == "monthly"
    assert sched["day_of_month"] == 15


@pytest.mark.asyncio
async def test_webhook_delivery_method(_admin, app_client):
    jwt, _, _ = _admin
    sched = await _create_schedule(
        app_client, jwt, name="Webhook Test",
        delivery_method="webhook",
        webhook_url="https://example.com/hook",
    )
    assert sched["delivery_method"] == "webhook"
    assert sched["webhook_url"] == "https://example.com/hook"
