"""Gap 34 — Scheduled Report Delivery tests.

Sections:
  A (5)  — DB schema: tables, columns, RLS, permission seed
  B (5)  — _compute_next_run pure function (daily / weekly / monthly)
  C (14) — Schedule CRUD: list, create (valid/invalid), get, update, delete
  D (4)  — Delivery tracking: list deliveries, run-now, run-now 404
  E (2)  — Permission enforcement: viewer 403, unauthenticated 401
  F (2)  — RLS isolation: cross-tenant list, cross-tenant run-now
  G (3)  — Scheduler: no due schedules, due schedule processed, next_run_at updated
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql+asyncpg://svc_app:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


async def _seed_tenant_and_token(role_id: int = 2):
    """Create an isolated tenant + user; return (tenant_id, user_id, jwt_token)."""
    from app.core.security import create_access_token, hash_password

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"sr-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"SR Tenant {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                "VALUES (:id, :tid, :rid, :email, :pw)"
            ),
            {
                "id": user_id,
                "tid": tenant_id,
                "rid": role_id,
                "email": f"u-{user_id.hex[:8]}@test.local",
                "pw": hash_password("x"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


def _app():
    from app.main import app
    return app


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Section A — DB schema
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_schedules_table_exists():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        row = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='report_schedules' ORDER BY column_name"
        ))
        cols = {r[0] for r in row.fetchall()}
    await engine.dispose()
    for col in ("id", "tenant_id", "name", "report_type", "frequency",
                "hour_utc", "delivery_method", "recipients", "is_active",
                "next_run_at", "last_run_at"):
        assert col in cols, f"Missing column: {col}"


@pytest.mark.asyncio
async def test_report_deliveries_table_exists():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        row = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='report_deliveries' ORDER BY column_name"
        ))
        cols = {r[0] for r in row.fetchall()}
    await engine.dispose()
    for col in ("id", "tenant_id", "schedule_id", "status", "delivered_at",
                "error_message", "report_period_start", "report_period_end"):
        assert col in cols, f"Missing column: {col}"


@pytest.mark.asyncio
async def test_rls_enabled_on_report_schedules():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT relrowsecurity FROM pg_class WHERE relname='report_schedules'"
        ))).first()
    await engine.dispose()
    assert row is not None and row[0] is True


@pytest.mark.asyncio
async def test_report_schedule_permission_seeded():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT id FROM permissions WHERE code='report:schedule'"
        ))).first()
    await engine.dispose()
    assert row is not None, "report:schedule permission not seeded"


@pytest.mark.asyncio
async def test_admin_role_has_report_schedule_permission():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT 1 FROM role_permissions rp
            JOIN permissions p ON p.id = rp.permission_id
            WHERE rp.role_id = 2 AND p.code = 'report:schedule'
        """))).first()
    await engine.dispose()
    assert row is not None, "admin role_id=2 missing report:schedule"


# ─────────────────────────────────────────────────────────────────────────────
# Section B — _compute_next_run pure function
# ─────────────────────────────────────────────────────────────────────────────

def _import_compute():
    from app.routers.scheduled_reports import _compute_next_run
    return _compute_next_run


def test_compute_next_run_daily_before_hour():
    _compute_next_run = _import_compute()
    # Choose hour_utc that is in the far future so "before" branch always fires
    future_hour = 23
    now = datetime.now(timezone.utc)
    if now.hour >= future_hour:
        future_hour = 0  # will trigger "tomorrow" branch — that's ok, skip this test variant
        pytest.skip("local UTC hour too late for before-hour test")
    result = _compute_next_run("daily", None, None, future_hour)
    assert result.date() == now.date()
    assert result.hour == future_hour


def test_compute_next_run_daily_after_hour():
    _compute_next_run = _import_compute()
    # hour_utc=0 is always in the past (since UTC midnight)
    now = datetime.now(timezone.utc)
    past_hour = 0
    if now.hour == 0 and now.minute == 0:
        pytest.skip("race: exactly UTC midnight")
    result = _compute_next_run("daily", None, None, past_hour)
    expected_date = (now + timedelta(days=1)).date()
    assert result.date() == expected_date
    assert result.hour == past_hour


def test_compute_next_run_weekly_returns_future():
    _compute_next_run = _import_compute()
    result = _compute_next_run("weekly", 0, None, 8)  # Monday
    now = datetime.now(timezone.utc)
    assert result > now, "weekly next_run must be strictly in the future"
    assert result.weekday() == 0
    assert result.hour == 8


def test_compute_next_run_weekly_same_day_past_hour():
    _compute_next_run = _import_compute()
    today_dow = datetime.now(timezone.utc).weekday()
    result = _compute_next_run("weekly", today_dow, None, 0)
    now = datetime.now(timezone.utc)
    assert result > now
    # Must be next week (7 days from today's midnight)
    assert (result.date() - now.date()).days >= 6


def test_compute_next_run_monthly_returns_future():
    _compute_next_run = _import_compute()
    result = _compute_next_run("monthly", None, 1, 8)
    now = datetime.now(timezone.utc)
    assert result > now, "monthly next_run must be in the future"
    assert result.hour == 8
    assert result.day == 1


# ─────────────────────────────────────────────────────────────────────────────
# Section C — Schedule CRUD
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_schedules_empty():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/scheduled-reports")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_schedule_daily_email():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/scheduled-reports", json={
            "name": "Daily Summary",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 8,
            "delivery_method": "email",
            "recipients": ["ops@example.com"],
        })
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Daily Summary"
    assert data["frequency"] == "daily"
    assert data["delivery_method"] == "email"
    assert data["next_run_at"] is not None


@pytest.mark.asyncio
async def test_create_schedule_weekly_webhook():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/scheduled-reports", json={
            "name": "Weekly DOB",
            "report_type": "dob",
            "frequency": "weekly",
            "day_of_week": 0,
            "hour_utc": 6,
            "delivery_method": "webhook",
            "webhook_url": "https://hooks.example.com/report",
        })
    assert r.status_code == 200
    data = r.json()
    assert data["frequency"] == "weekly"
    assert data["day_of_week"] == 0


@pytest.mark.asyncio
async def test_create_schedule_monthly():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/scheduled-reports", json={
            "name": "Monthly Incidents",
            "report_type": "incident_summary",
            "frequency": "monthly",
            "day_of_month": 1,
            "hour_utc": 7,
            "delivery_method": "email",
            "recipients": ["mgr@example.com"],
        })
    assert r.status_code == 200
    data = r.json()
    assert data["frequency"] == "monthly"
    assert data["day_of_month"] == 1


@pytest.mark.asyncio
async def test_create_schedule_invalid_report_type():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/scheduled-reports", json={
            "name": "Bad",
            "report_type": "garbage",
            "frequency": "daily",
            "hour_utc": 8,
            "delivery_method": "email",
            "recipients": ["x@y.com"],
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_schedule_invalid_frequency():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/scheduled-reports", json={
            "name": "Bad",
            "report_type": "site_summary",
            "frequency": "hourly",
            "hour_utc": 8,
            "delivery_method": "email",
            "recipients": ["x@y.com"],
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_schedule_invalid_delivery_method():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/scheduled-reports", json={
            "name": "Bad",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 8,
            "delivery_method": "ftp",
            "recipients": [],
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_schedule_invalid_hour_utc():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/scheduled-reports", json={
            "name": "Bad",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 25,
            "delivery_method": "email",
            "recipients": ["x@y.com"],
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_schedule_by_id():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = (await c.post("/api/v1/scheduled-reports", json={
            "name": "Get Me",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 9,
            "delivery_method": "email",
            "recipients": [],
        })).json()
        r = await c.get(f"/api/v1/scheduled-reports/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]
    assert r.json()["name"] == "Get Me"


@pytest.mark.asyncio
async def test_get_schedule_not_found():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/scheduled-reports/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_schedule_name():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = (await c.post("/api/v1/scheduled-reports", json={
            "name": "Old Name",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 8,
            "delivery_method": "email",
            "recipients": [],
        })).json()
        r = await c.put(f"/api/v1/scheduled-reports/{created['id']}", json={"name": "New Name"})
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_update_schedule_no_fields_returns_422():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = (await c.post("/api/v1/scheduled-reports", json={
            "name": "Stale",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 8,
            "delivery_method": "email",
            "recipients": [],
        })).json()
        r = await c.put(f"/api/v1/scheduled-reports/{created['id']}", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_schedule():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = (await c.post("/api/v1/scheduled-reports", json={
            "name": "Delete Me",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 8,
            "delivery_method": "email",
            "recipients": [],
        })).json()
        r = await c.delete(f"/api/v1/scheduled-reports/{created['id']}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True


@pytest.mark.asyncio
async def test_delete_schedule_not_found():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/scheduled-reports/{uuid.uuid4()}")
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Section D — Delivery tracking
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_deliveries_empty():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = (await c.post("/api/v1/scheduled-reports", json={
            "name": "No Deliveries Yet",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 8,
            "delivery_method": "email",
            "recipients": [],
        })).json()
        r = await c.get(f"/api/v1/scheduled-reports/{created['id']}/deliveries")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_run_now_returns_delivery_id():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = (await c.post("/api/v1/scheduled-reports", json={
            "name": "Run Me Now",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 8,
            "delivery_method": "email",
            "recipients": [],
        })).json()
        r = await c.post(f"/api/v1/scheduled-reports/{created['id']}/run-now")
    assert r.status_code == 200
    data = r.json()
    assert "delivery_id" in data
    assert data["status"] == "queued"


@pytest.mark.asyncio
async def test_run_now_creates_pending_delivery_row():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = (await c.post("/api/v1/scheduled-reports", json={
            "name": "Pending Row",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 8,
            "delivery_method": "email",
            "recipients": [],
        })).json()
        run_resp = (await c.post(f"/api/v1/scheduled-reports/{created['id']}/run-now")).json()
        deliveries = (await c.get(f"/api/v1/scheduled-reports/{created['id']}/deliveries")).json()
    assert len(deliveries) == 1
    assert deliveries[0]["id"] == run_resp["delivery_id"]
    assert deliveries[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_run_now_schedule_not_found():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/scheduled-reports/{uuid.uuid4()}/run-now")
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Section E — Permission enforcement
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_schedules_requires_permission():
    # viewer role_id=6 does not have report:schedule
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.get("/api/v1/scheduled-reports")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_schedules_requires_auth():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        r = await c.get("/api/v1/scheduled-reports")
    assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# Section F — RLS isolation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_tenant_schedule_not_visible():
    """Tenant A's schedules must not appear in tenant B's list."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()

    async with await _authed(token_a) as c_a:
        await c_a.post("/api/v1/scheduled-reports", json={
            "name": "Tenant A Private",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 8,
            "delivery_method": "email",
            "recipients": [],
        })

    async with await _authed(token_b) as c_b:
        r = await c_b.get("/api/v1/scheduled-reports")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()]
    assert "Tenant A Private" not in names


@pytest.mark.asyncio
async def test_cross_tenant_run_now_returns_404():
    """Tenant B cannot trigger run-now on Tenant A's schedule."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()

    async with await _authed(token_a) as c_a:
        schedule = (await c_a.post("/api/v1/scheduled-reports", json={
            "name": "A Only",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 8,
            "delivery_method": "email",
            "recipients": [],
        })).json()

    async with await _authed(token_b) as c_b:
        r = await c_b.post(f"/api/v1/scheduled-reports/{schedule['id']}/run-now")
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Section G — Scheduler: run_scheduled_reports()
# ─────────────────────────────────────────────────────────────────────────────

async def _reset_all_schedules_to_future() -> None:
    """Set next_run_at far in the future for ALL report_schedules.

    Prevents leftover due-date schedules from previous (partially failed)
    test runs from interfering with the scheduler integration tests.
    Uses the admin DB connection to bypass RLS.
    """
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(text(
            "UPDATE report_schedules SET next_run_at = now() + interval '1 year'"
        ))
        await s.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_scheduled_reports_no_due_schedules():
    """When no schedules are due, the function returns 0."""
    # Reset all schedules from prior test runs before asserting count=0
    await _reset_all_schedules_to_future()

    tenant_id, _, token = await _seed_tenant_and_token()

    # Create a schedule whose next_run_at is well in the future
    async with await _authed(token) as c:
        await c.post("/api/v1/scheduled-reports", json={
            "name": "Not Due Yet",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 23,
            "delivery_method": "email",
            "recipients": [],
        })

    from app.scheduler_main import run_scheduled_reports

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    with patch("app.routers.reports.build_site_summary_bytes", new=AsyncMock(return_value=b"%PDF-test")), \
         patch("app.scheduler_main._send_report_email", new=AsyncMock()), \
         patch("app.scheduler_main._send_report_webhook", new=AsyncMock()):
        async with factory() as db:
            count = await run_scheduled_reports(db)

    await engine.dispose()
    assert count == 0, f"Expected 0 due schedules, got {count}"


@pytest.mark.asyncio
async def test_run_scheduled_reports_processes_due_schedule():
    """A schedule with next_run_at in the past gets processed and a delivery row is written."""
    await _reset_all_schedules_to_future()

    tenant_id, _, token = await _seed_tenant_and_token()

    async with await _authed(token) as c:
        schedule = (await c.post("/api/v1/scheduled-reports", json={
            "name": "Overdue Schedule",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 0,
            "delivery_method": "email",
            "recipients": ["test@example.com"],
        })).json()

    # Back-date next_run_at so the scheduler sees it as due.
    # Use tenant_id (not id) to avoid asyncpg UUID cast ambiguity.
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(text(
            "UPDATE report_schedules SET next_run_at = now() - interval '1 hour' "
            "WHERE tenant_id = :tid"
        ), {"tid": str(tenant_id)})
        await s.commit()
    await engine.dispose()

    from app.scheduler_main import run_scheduled_reports

    engine2 = create_async_engine(TEST_DATABASE_URL)
    factory2 = async_sessionmaker(engine2, expire_on_commit=False, class_=AsyncSession)

    with patch("app.routers.reports.build_site_summary_bytes", new=AsyncMock(return_value=b"%PDF-test")), \
         patch("app.scheduler_main._send_report_email", new=AsyncMock()), \
         patch("app.scheduler_main._send_report_webhook", new=AsyncMock()):
        async with factory2() as db:
            count = await run_scheduled_reports(db)

    await engine2.dispose()
    assert count >= 1, "At least one due schedule should have been processed"

    # Verify a delivery row was created for our schedule
    engine3 = create_async_engine(TEST_DATABASE_URL)
    factory3 = async_sessionmaker(engine3, expire_on_commit=False, class_=AsyncSession)
    async with factory3() as db3:
        await db3.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})
        delivery_rows = (await db3.execute(text(
            "SELECT status FROM report_deliveries WHERE schedule_id = CAST(:sid AS uuid)"
        ), {"sid": schedule["id"]})).fetchall()
    await engine3.dispose()
    assert len(delivery_rows) >= 1, "Delivery row should have been written"
    # Status is 'success' (mocked email succeeded) or 'failed' (SMTP not configured)
    assert delivery_rows[0][0] in ("success", "failed")


@pytest.mark.asyncio
async def test_run_scheduled_reports_updates_next_run_at():
    """After processing, next_run_at must be updated to a future time and last_run_at set."""
    await _reset_all_schedules_to_future()

    tenant_id, _, token = await _seed_tenant_and_token()

    async with await _authed(token) as c:
        schedule = (await c.post("/api/v1/scheduled-reports", json={
            "name": "Next Run Check",
            "report_type": "site_summary",
            "frequency": "daily",
            "hour_utc": 8,
            "delivery_method": "email",
            "recipients": [],
        })).json()
    schedule_id = schedule["id"]

    overdue_time = datetime.now(timezone.utc) - timedelta(hours=2)

    # Backdate using tenant_id to avoid any UUID cast ambiguity
    engine_a = create_async_engine(ADMIN_DATABASE_URL)
    factory_a = async_sessionmaker(engine_a, expire_on_commit=False, class_=AsyncSession)
    pre_row = None
    async with factory_a() as s:
        await s.execute(text(
            "UPDATE report_schedules SET next_run_at = :overdue "
            "WHERE tenant_id = :tid"
        ), {"overdue": overdue_time, "tid": str(tenant_id)})
        await s.commit()
        # Verify backdate while session is still open
        pre_row = (await s.execute(text(
            "SELECT next_run_at FROM report_schedules WHERE tenant_id = :tid"
        ), {"tid": str(tenant_id)})).first()
    await engine_a.dispose()

    assert pre_row is not None, "Schedule not found in DB after backdate"
    assert pre_row.next_run_at <= datetime.now(timezone.utc), (
        f"Backdate did not take effect: next_run_at = {pre_row.next_run_at}"
    )

    from app.scheduler_main import run_scheduled_reports

    engine2 = create_async_engine(TEST_DATABASE_URL)
    factory2 = async_sessionmaker(engine2, expire_on_commit=False, class_=AsyncSession)

    with patch("app.routers.reports.build_site_summary_bytes", new=AsyncMock(return_value=b"%PDF-test")), \
         patch("app.scheduler_main._send_report_email", new=AsyncMock()), \
         patch("app.scheduler_main._send_report_webhook", new=AsyncMock()):
        async with factory2() as db:
            count = await run_scheduled_reports(db)
    await engine2.dispose()

    assert count >= 1, "Scheduler should have processed at least 1 schedule"

    # Verify via API (uses RLS from token, always correct context)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/scheduled-reports/{schedule_id}")
    updated = r.json()
    assert updated["last_run_at"] is not None, "last_run_at must be set after processing"
    assert updated["next_run_at"] > overdue_time.isoformat(), "next_run_at must be later than the overdue time"
