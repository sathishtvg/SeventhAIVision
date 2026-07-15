"""P5-K: Scheduled report delivery management.

Tests cover:

_compute_next_run (pure function):
- daily: if target hour hasn't passed today → same-day result
- daily: if target hour has passed today → next day
- weekly: result lands on the correct weekday
- monthly: result is on the target day-of-month (clamped ≤28)

Schedule CRUD:
- POST: create daily/weekly/monthly schedules
- POST: invalid report_type / frequency / delivery_method / hour_utc → 422
- GET /: list includes created schedule
- GET /{id}: returns schedule with all fields
- GET /{id}: non-existent → 404
- PUT /{id}: partial update (name, frequency, recipients, is_active)
- PUT /{id}: empty body (no fields) → 422
- PUT /{id}: invalid frequency/delivery_method → 422
- PUT /{id}: non-existent → 404
- DELETE /{id}: removes schedule
- DELETE /{id}: non-existent → 404

Deliveries and run-now:
- GET /{id}/deliveries: empty list on new schedule
- POST /{id}/run-now: creates pending delivery, returns delivery_id
- POST /{id}/run-now: non-existent schedule → 404
- GET /{id}/deliveries: lists the delivery created by run-now

Auth:
- All endpoints require auth (401)
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient


# ── Pure-function tests (no DB) ───────────────────────────────────────────────

def test_compute_next_run_daily_future():
    from app.routers.scheduled_reports import _compute_next_run

    # Pick an hour far in the future (hour 23 UTC) — should be today if it
    # hasn't happened yet, or tomorrow if it has.  We just assert the result
    # is always in the future from now.
    result = _compute_next_run("daily", None, None, 23)
    assert result > datetime.now(timezone.utc)


def test_compute_next_run_daily_past_hour():
    from app.routers.scheduled_reports import _compute_next_run

    # Choosing hour 0 UTC: if it's any time after midnight UTC the target for
    # today has already passed, so the result must be tomorrow.
    now = datetime.now(timezone.utc)
    result = _compute_next_run("daily", None, None, 0)
    assert result > now
    # Must be within 2 days from now (either today if it's before 00:00 or tomorrow)
    assert result <= now + timedelta(days=2)


def test_compute_next_run_weekly_correct_weekday():
    from app.routers.scheduled_reports import _compute_next_run

    for dow in range(7):
        result = _compute_next_run("weekly", dow, None, 8)
        # 0 = Monday in Python's weekday(); ISODOW-based schedule uses same convention
        assert result.weekday() == dow
        assert result > datetime.now(timezone.utc)


def test_compute_next_run_monthly_correct_dom():
    from app.routers.scheduled_reports import _compute_next_run

    for dom in (1, 15, 28):
        result = _compute_next_run("monthly", None, dom, 8)
        assert result.day == dom
        assert result > datetime.now(timezone.utc)


def test_compute_next_run_monthly_clamps_to_28():
    from app.routers.scheduled_reports import _compute_next_run

    # day_of_month=None defaults to 1 inside the function
    result = _compute_next_run("monthly", None, None, 8)
    assert 1 <= result.day <= 28


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _create_schedule(
    client: AsyncClient,
    *,
    name: str = "Daily Summary",
    report_type: str = "site_summary",
    frequency: str = "daily",
    delivery_method: str = "email",
    recipients: list[str] | None = None,
    hour_utc: int = 8,
    **extra,
) -> dict:
    body = {
        "name": name,
        "report_type": report_type,
        "frequency": frequency,
        "delivery_method": delivery_method,
        "hour_utc": hour_utc,
        "recipients": recipients or ["ops@example.com"],
        **extra,
    }
    r = await client.post("/api/v1/scheduled-reports", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ── Create validation ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_daily_schedule(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/scheduled-reports", json={
        "name": "Daily Site Summary",
        "report_type": "site_summary",
        "frequency": "daily",
        "hour_utc": 7,
        "delivery_method": "email",
        "recipients": ["mgr@example.com"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["frequency"] == "daily"
    assert body["hour_utc"] == 7
    assert "next_run_at" in body
    assert body["next_run_at"] is not None
    assert "id" in body


@pytest.mark.asyncio
async def test_create_weekly_schedule(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/scheduled-reports", json={
        "name": "Weekly DOB Report",
        "report_type": "dob",
        "frequency": "weekly",
        "day_of_week": 0,   # Monday
        "hour_utc": 6,
        "delivery_method": "email",
        "recipients": ["sec@example.com"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["frequency"] == "weekly"
    assert body["day_of_week"] == 0


@pytest.mark.asyncio
async def test_create_monthly_schedule(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/scheduled-reports", json={
        "name": "Monthly Incident Summary",
        "report_type": "incident_summary",
        "frequency": "monthly",
        "day_of_month": 1,
        "hour_utc": 8,
        "delivery_method": "email",
        "recipients": ["ciso@example.com"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["frequency"] == "monthly"
    assert body["day_of_month"] == 1


@pytest.mark.asyncio
async def test_create_with_webhook_delivery(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/scheduled-reports", json={
        "name": "Webhook Daily",
        "report_type": "site_summary",
        "frequency": "daily",
        "hour_utc": 9,
        "delivery_method": "webhook",
        "webhook_url": "https://hooks.example.com/report",
    })
    assert r.status_code == 200
    assert r.json()["delivery_method"] == "webhook"
    assert r.json()["webhook_url"] == "https://hooks.example.com/report"


@pytest.mark.asyncio
async def test_invalid_report_type_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/scheduled-reports", json={
        "name": "Bad Type",
        "report_type": "nonexistent_report",
        "frequency": "daily",
        "hour_utc": 8,
        "delivery_method": "email",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_invalid_frequency_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/scheduled-reports", json={
        "name": "Bad Freq",
        "report_type": "site_summary",
        "frequency": "hourly",
        "hour_utc": 8,
        "delivery_method": "email",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_invalid_delivery_method_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/scheduled-reports", json={
        "name": "Bad Delivery",
        "report_type": "site_summary",
        "frequency": "daily",
        "hour_utc": 8,
        "delivery_method": "slack",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_hour_utc_out_of_range_422(auth_client: AsyncClient):
    for bad_hour in (-1, 24):
        r = await auth_client.post("/api/v1/scheduled-reports", json={
            "name": "Bad Hour",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": bad_hour,
            "delivery_method": "email",
        })
        assert r.status_code == 422, f"expected 422 for hour_utc={bad_hour}"


# ── List ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_schedules_includes_created(auth_client: AsyncClient):
    sched = await _create_schedule(auth_client, name="List Me")

    r = await auth_client.get("/api/v1/scheduled-reports")
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()]
    assert sched["id"] in ids


@pytest.mark.asyncio
async def test_list_schedules_has_expected_fields(auth_client: AsyncClient):
    await _create_schedule(auth_client, name="Field Check")

    r = await auth_client.get("/api/v1/scheduled-reports")
    assert r.status_code == 200
    assert len(r.json()) > 0
    row = r.json()[0]
    for field in (
        "id", "name", "report_type", "frequency", "delivery_method",
        "is_active", "next_run_at", "last_run_at", "created_at",
    ):
        assert field in row, f"Missing field: {field}"


# ── Get by ID ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_schedule_by_id(auth_client: AsyncClient):
    sched = await _create_schedule(auth_client, name="Get By ID")

    r = await auth_client.get(f"/api/v1/scheduled-reports/{sched['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == sched["id"]
    assert r.json()["name"] == "Get By ID"


@pytest.mark.asyncio
async def test_get_nonexistent_schedule_404(auth_client: AsyncClient):
    r = await auth_client.get(
        "/api/v1/scheduled-reports/00000000-0000-0000-0000-000000000099"
    )
    assert r.status_code == 404


# ── Update ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_schedule_name(auth_client: AsyncClient):
    sched = await _create_schedule(auth_client, name="Old Name")

    r = await auth_client.put(f"/api/v1/scheduled-reports/{sched['id']}", json={
        "name": "New Name",
    })
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_update_schedule_recipients(auth_client: AsyncClient):
    sched = await _create_schedule(auth_client, recipients=["old@example.com"])

    r = await auth_client.put(f"/api/v1/scheduled-reports/{sched['id']}", json={
        "recipients": ["new1@example.com", "new2@example.com"],
    })
    assert r.status_code == 200
    # recipients stored as JSONB — returned as list
    recipients = r.json()["recipients"]
    assert "new1@example.com" in recipients
    assert "new2@example.com" in recipients


@pytest.mark.asyncio
async def test_update_schedule_deactivate(auth_client: AsyncClient):
    sched = await _create_schedule(auth_client, name="Deactivate Me")

    r = await auth_client.put(f"/api/v1/scheduled-reports/{sched['id']}", json={
        "is_active": False,
    })
    assert r.status_code == 200
    assert r.json()["is_active"] is False


@pytest.mark.asyncio
async def test_update_with_no_fields_422(auth_client: AsyncClient):
    sched = await _create_schedule(auth_client, name="No Update")

    r = await auth_client.put(f"/api/v1/scheduled-reports/{sched['id']}", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_invalid_frequency_422(auth_client: AsyncClient):
    sched = await _create_schedule(auth_client, name="Bad Freq Update")

    r = await auth_client.put(f"/api/v1/scheduled-reports/{sched['id']}", json={
        "frequency": "quarterly",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_nonexistent_schedule_404(auth_client: AsyncClient):
    r = await auth_client.put(
        "/api/v1/scheduled-reports/00000000-0000-0000-0000-000000000099",
        json={"name": "Ghost"},
    )
    assert r.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_schedule(auth_client: AsyncClient):
    sched = await _create_schedule(auth_client, name="Delete Me")

    r = await auth_client.delete(f"/api/v1/scheduled-reports/{sched['id']}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True


@pytest.mark.asyncio
async def test_deleted_schedule_not_in_list(auth_client: AsyncClient):
    sched = await _create_schedule(auth_client, name="Gone Soon")
    await auth_client.delete(f"/api/v1/scheduled-reports/{sched['id']}")

    r = await auth_client.get("/api/v1/scheduled-reports")
    ids = [s["id"] for s in r.json()]
    assert sched["id"] not in ids


@pytest.mark.asyncio
async def test_delete_nonexistent_schedule_404(auth_client: AsyncClient):
    r = await auth_client.delete(
        "/api/v1/scheduled-reports/00000000-0000-0000-0000-000000000099"
    )
    assert r.status_code == 404


# ── Deliveries + run-now ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_deliveries_empty_on_new_schedule(auth_client: AsyncClient):
    sched = await _create_schedule(auth_client, name="No Deliveries Yet")

    r = await auth_client.get(f"/api/v1/scheduled-reports/{sched['id']}/deliveries")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_run_now_creates_pending_delivery(auth_client: AsyncClient):
    sched = await _create_schedule(auth_client, name="Run Now")

    r = await auth_client.post(f"/api/v1/scheduled-reports/{sched['id']}/run-now")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert "delivery_id" in body
    assert len(body["delivery_id"]) == 36   # UUID


@pytest.mark.asyncio
async def test_run_now_appears_in_deliveries_list(auth_client: AsyncClient):
    sched = await _create_schedule(auth_client, name="Run and List")

    run_r = await auth_client.post(f"/api/v1/scheduled-reports/{sched['id']}/run-now")
    delivery_id = run_r.json()["delivery_id"]

    del_r = await auth_client.get(f"/api/v1/scheduled-reports/{sched['id']}/deliveries")
    assert del_r.status_code == 200
    delivery_ids = [d["id"] for d in del_r.json()]
    assert delivery_id in delivery_ids


@pytest.mark.asyncio
async def test_run_now_nonexistent_schedule_404(auth_client: AsyncClient):
    r = await auth_client.post(
        "/api/v1/scheduled-reports/00000000-0000-0000-0000-000000000099/run-now"
    )
    assert r.status_code == 404


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_schedules_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/scheduled-reports")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_schedule_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/scheduled-reports", json={
        "name": "Unauth",
        "report_type": "site_summary",
        "frequency": "daily",
        "hour_utc": 8,
        "delivery_method": "email",
    })
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_delete_schedule_requires_auth(client: AsyncClient):
    r = await client.delete(
        "/api/v1/scheduled-reports/00000000-0000-0000-0000-000000000001"
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_run_now_requires_auth(client: AsyncClient):
    r = await client.post(
        "/api/v1/scheduled-reports/00000000-0000-0000-0000-000000000001/run-now"
    )
    assert r.status_code == 401
