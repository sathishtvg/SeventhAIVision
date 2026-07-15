"""Gap 44 — Guard Tour Compliance Router

Covers endpoints in backend/app/routers/compliance.py.
The existing test_compliance_features.py (8 tests, shared fixtures) covers happy
paths for CRUD + generate + resolve + report + dashboard.  This file adds
isolated-tenant tests for edge cases, filters, permissions, and RLS.

Endpoints:
  GET  /api/v1/compliance/dashboard
  GET  /api/v1/compliance/schedules          (is_active filter)
  POST /api/v1/compliance/schedules
  GET  /api/v1/compliance/schedules/{id}
  PUT  /api/v1/compliance/schedules/{id}
  POST /api/v1/compliance/schedules/{id}/generate
  GET  /api/v1/compliance/occurrences        (schedule_id / status / limit filters)
  PUT  /api/v1/compliance/occurrences/{id}/resolve
  GET  /api/v1/compliance/report

Sections:
  A — DB schema: tour_schedules + tour_occurrences columns
  B — POST /schedules: response fields; nonexistent route → 404
  C — GET /schedules: is_active filter; GET /{id} includes route/site names; 404; update no-fields 422
  D — POST /schedules/{id}/generate: daily recurrence; idempotent (ON CONFLICT); inactive → 404
  E — GET /occurrences: schedule_id filter; status filter; limit param
  F — PUT /occurrences/{id}/resolve: sets status + score; 404; invalid status 422
  G — GET /report: all 4 sections; missing required date params → 422
  H — GET /dashboard: all required keys present
  I — Permissions: compliance:manage requires role with permission; unauth → 401
  J — RLS: tenant A's schedules invisible to tenant B
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
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


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 2):
    """Create isolated tenant + user; return (tenant_id, user_id, jwt_token)."""
    from app.core.security import create_access_token, hash_password

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"cpl-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Compliance Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                "VALUES (:id, :tid, :role, :email, :pw)"
            ),
            {
                "id": user_id,
                "tid": tenant_id,
                "role": role_id,
                "email": f"cpl-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_route(tenant_id: uuid.UUID) -> str:
    """Seed a site + active patrol route; return route_id as str."""
    site_id = uuid.uuid4()
    route_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :name)"),
            {"id": site_id, "tid": tenant_id, "name": f"CPL Site {site_id.hex[:6]}"},
        )
        await s.execute(
            text(
                "INSERT INTO patrol_routes (id, tenant_id, site_id, name, is_active) "
                "VALUES (:id, :tid, :sid, :name, TRUE)"
            ),
            {"id": route_id, "tid": tenant_id, "sid": site_id, "name": f"Route {route_id.hex[:6]}"},
        )
        await s.commit()
    await engine.dispose()
    return str(route_id)


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_schedule(client: AsyncClient, route_id: str, **kwargs) -> dict:
    body = {
        "route_id": route_id,
        "name": kwargs.get("name", "Test Schedule"),
        "scheduled_time": kwargs.get("scheduled_time", "08:00"),
        "recurrence": kwargs.get("recurrence", "daily"),
        "window_minutes": kwargs.get("window_minutes", 30),
    }
    r = await client.post("/api/v1/compliance/schedules", json=body)
    assert r.status_code == 200, f"create schedule failed: {r.text}"
    return r.json()


# ─── A. DB Schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tour_schedules_expected_columns():
    """tour_schedules table has all columns needed for compliance management."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'tour_schedules'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "route_id", "name", "recurrence",
        "days_of_week", "scheduled_time", "window_minutes",
        "assigned_guard_user_id", "is_active", "created_at",
    ):
        assert col in cols, f"Column {col!r} missing from tour_schedules"


@pytest.mark.asyncio
async def test_tour_occurrences_expected_columns():
    """tour_occurrences table has all columns needed for tour tracking."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'tour_occurrences'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "schedule_id", "session_id",
        "scheduled_at", "window_end", "status",
        "compliance_score", "missed_checkpoints", "notes",
    ):
        assert col in cols, f"Column {col!r} missing from tour_occurrences"


# ─── B. POST /schedules ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_schedule_returns_schedule_fields():
    """POST /schedules returns schedule data with expected fields."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/compliance/schedules",
            json={
                "route_id": route_id,
                "name": "Hourly Lobby Check",
                "scheduled_time": "09:00",
                "recurrence": "daily",
                "window_minutes": 15,
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
    assert data["name"] == "Hourly Lobby Check"
    assert data["recurrence"] == "daily"
    assert data["window_minutes"] == 15
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_create_schedule_nonexistent_route_returns_404():
    """POST /schedules with an unknown route_id returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/compliance/schedules",
            json={
                "route_id": str(uuid.uuid4()),
                "name": "Ghost Route Schedule",
                "scheduled_time": "10:00",
                "recurrence": "daily",
            },
        )
    assert r.status_code == 404


# ─── C. GET /schedules + GET /{id} + PUT /{id} ────────────────────────────────

@pytest.mark.asyncio
async def test_list_schedules_is_active_filter():
    """GET /schedules?is_active=false excludes active schedules."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        sched = await _create_schedule(c, route_id, name="Active Schedule")
        # deactivate it
        await c.put(
            f"/api/v1/compliance/schedules/{sched['id']}",
            json={"is_active": False},
        )
        # list only inactive
        r = await c.get("/api/v1/compliance/schedules?is_active=false")
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()]
        assert str(sched["id"]) in ids

        # list only active → our deactivated one must not appear
        r2 = await c.get("/api/v1/compliance/schedules?is_active=true")
        assert r2.status_code == 200
        ids2 = [s["id"] for s in r2.json()]
        assert str(sched["id"]) not in ids2


@pytest.mark.asyncio
async def test_get_schedule_includes_route_and_site_names():
    """GET /schedules/{id} joins patrol_routes + sites and returns their names."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        sched = await _create_schedule(c, route_id, name="Named Fields Schedule")
        r = await c.get(f"/api/v1/compliance/schedules/{sched['id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Named Fields Schedule"
    assert "route_name" in data
    assert "site_name" in data


@pytest.mark.asyncio
async def test_get_schedule_returns_404_for_unknown_id():
    """GET /schedules/{id} with a non-existent id returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/compliance/schedules/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_schedule_no_fields_returns_422():
    """PUT /schedules/{id} with an empty body (no updatable fields) returns 422."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        sched = await _create_schedule(c, route_id)
        r = await c.put(f"/api/v1/compliance/schedules/{sched['id']}", json={})
    assert r.status_code == 422


# ─── D. POST /schedules/{id}/generate ────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_occurrences_daily_recurrence():
    """POST /generate with days=5 produces generated>=5 for a daily schedule."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        sched = await _create_schedule(c, route_id, name="Daily Gen Schedule")
        r = await c.post(
            f"/api/v1/compliance/schedules/{sched['id']}/generate?days=5"
        )
    assert r.status_code == 200
    data = r.json()
    assert data["generated"] >= 5
    assert data["days"] == 5


@pytest.mark.asyncio
async def test_generate_occurrences_idempotent_no_duplicate_rows():
    """POST /generate twice with same days does not create duplicate occurrence rows."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        sched = await _create_schedule(c, route_id, name="Idempotent Gen")
        await c.post(f"/api/v1/compliance/schedules/{sched['id']}/generate?days=3")
        await c.post(f"/api/v1/compliance/schedules/{sched['id']}/generate?days=3")
        # List occurrences for this schedule — ON CONFLICT DO NOTHING means no duplicates
        r = await c.get(f"/api/v1/compliance/occurrences?schedule_id={sched['id']}&limit=50")
    assert r.status_code == 200
    assert len(r.json()) <= 3


@pytest.mark.asyncio
async def test_generate_occurrences_inactive_schedule_returns_404():
    """POST /generate on a deactivated schedule returns 404."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        sched = await _create_schedule(c, route_id, name="Soon Inactive")
        await c.put(
            f"/api/v1/compliance/schedules/{sched['id']}",
            json={"is_active": False},
        )
        r = await c.post(
            f"/api/v1/compliance/schedules/{sched['id']}/generate?days=3"
        )
    assert r.status_code == 404


# ─── E. GET /occurrences ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_occurrences_schedule_id_filter():
    """GET /occurrences?schedule_id=X returns only occurrences for that schedule."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        sched_a = await _create_schedule(c, route_id, name="Schedule A")
        sched_b = await _create_schedule(c, route_id, name="Schedule B")
        await c.post(f"/api/v1/compliance/schedules/{sched_a['id']}/generate?days=2")
        await c.post(f"/api/v1/compliance/schedules/{sched_b['id']}/generate?days=2")
        r = await c.get(f"/api/v1/compliance/occurrences?schedule_id={sched_a['id']}&limit=50")
    assert r.status_code == 200
    for occ in r.json():
        assert str(occ["schedule_id"]) == str(sched_a["id"])


@pytest.mark.asyncio
async def test_list_occurrences_status_filter():
    """GET /occurrences?status=missed returns only missed occurrences."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        sched = await _create_schedule(c, route_id, name="Status Filter Schedule")
        await c.post(f"/api/v1/compliance/schedules/{sched['id']}/generate?days=1")
        # resolve one as missed
        r_list = await c.get(f"/api/v1/compliance/occurrences?schedule_id={sched['id']}&limit=5")
        occ_id = r_list.json()[0]["id"]
        await c.put(
            f"/api/v1/compliance/occurrences/{occ_id}/resolve",
            json={"status": "missed", "notes": "Guard absent"},
        )
        r = await c.get("/api/v1/compliance/occurrences?status=missed&limit=50")
    assert r.status_code == 200
    for occ in r.json():
        assert occ["status"] == "missed"


@pytest.mark.asyncio
async def test_list_occurrences_limit_param():
    """GET /occurrences?limit=1 returns at most 1 result."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        sched = await _create_schedule(c, route_id, name="Limit Test Schedule")
        await c.post(f"/api/v1/compliance/schedules/{sched['id']}/generate?days=5")
        r = await c.get(f"/api/v1/compliance/occurrences?schedule_id={sched['id']}&limit=1")
    assert r.status_code == 200
    assert len(r.json()) == 1


# ─── F. PUT /occurrences/{id}/resolve ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_occurrence_returns_status_and_score():
    """PUT /occurrences/{id}/resolve with status=late returns correct response."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        sched = await _create_schedule(c, route_id, name="Resolve Late")
        await c.post(f"/api/v1/compliance/schedules/{sched['id']}/generate?days=1")
        r_list = await c.get(f"/api/v1/compliance/occurrences?schedule_id={sched['id']}&limit=2")
        occ_id = r_list.json()[0]["id"]
        r = await c.put(
            f"/api/v1/compliance/occurrences/{occ_id}/resolve",
            json={"status": "late", "compliance_score": 78.5},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "late"
    assert data["compliance_score"] == 78.5
    assert str(data["id"]) == str(occ_id)


@pytest.mark.asyncio
async def test_resolve_occurrence_404_for_unknown_id():
    """PUT /occurrences/{id}/resolve with unknown id returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put(
            f"/api/v1/compliance/occurrences/{uuid.uuid4()}/resolve",
            json={"status": "missed"},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_resolve_occurrence_invalid_status_returns_422():
    """PUT /occurrences/{id}/resolve with status='abandoned' returns 422."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        sched = await _create_schedule(c, route_id, name="Invalid Status Sched")
        await c.post(f"/api/v1/compliance/schedules/{sched['id']}/generate?days=1")
        r_list = await c.get(f"/api/v1/compliance/occurrences?schedule_id={sched['id']}&limit=2")
        occ_id = r_list.json()[0]["id"]
        r = await c.put(
            f"/api/v1/compliance/occurrences/{occ_id}/resolve",
            json={"status": "abandoned"},
        )
    assert r.status_code == 422


# ─── G. GET /report ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compliance_report_returns_all_sections():
    """GET /report returns summary, by_guard, by_route, and daily_trend keys."""
    _, _, token = await _seed_tenant_and_token()
    today = datetime.now(timezone.utc).date()
    past = (today - timedelta(days=7)).isoformat()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/compliance/report?date_from={past}&date_to={today.isoformat()}")
    assert r.status_code == 200
    data = r.json()
    for key in ("date_from", "date_to", "summary", "by_guard", "by_route", "daily_trend"):
        assert key in data, f"Key {key!r} missing from compliance report"


@pytest.mark.asyncio
async def test_compliance_report_missing_date_from_returns_422():
    """GET /report without required date_from param returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/compliance/report?date_to=2026-07-01")
    assert r.status_code == 422


# ─── H. GET /dashboard ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_returns_all_required_keys():
    """GET /dashboard returns all KPI keys and list keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/compliance/dashboard")
    assert r.status_code == 200
    data = r.json()
    for key in (
        "tours_today", "completed_today", "missed_today", "late_today",
        "incomplete_today", "compliance_rate_7d", "compliance_rate_30d",
        "avg_score_7d", "active_schedules", "daily_trend", "recent_missed",
    ):
        assert key in data, f"Key {key!r} missing from compliance dashboard"


@pytest.mark.asyncio
async def test_dashboard_active_schedules_increments_on_create():
    """GET /dashboard active_schedules count increases after adding a schedule."""
    tid, _, token = await _seed_tenant_and_token()
    route_id = await _seed_route(tid)
    async with await _authed(token) as c:
        before = (await c.get("/api/v1/compliance/dashboard")).json()["active_schedules"]
        await _create_schedule(c, route_id, name="New Schedule for Dashboard")
        after = (await c.get("/api/v1/compliance/dashboard")).json()["active_schedules"]
    assert after == before + 1


# ─── I. Permissions ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_create_compliance_schedule():
    """POST /schedules requires compliance:manage — viewer (role 6) gets 403."""
    tid, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    route_id = await _seed_route(tid)
    async with await _authed(viewer_token) as c:
        r = await c.post(
            "/api/v1/compliance/schedules",
            json={
                "route_id": route_id,
                "name": "Viewer Schedule",
                "scheduled_time": "08:00",
                "recurrence": "daily",
            },
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_read_dashboard():
    """GET /dashboard without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/compliance/dashboard")
    assert r.status_code == 401


# ─── J. RLS — Tenant Isolation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_schedules_isolated_by_tenant():
    """GET /schedules only returns schedules belonging to the caller's tenant."""
    tid_a, _, token_a = await _seed_tenant_and_token()
    tid_b, _, token_b = await _seed_tenant_and_token()

    route_a = await _seed_route(tid_a)
    route_b = await _seed_route(tid_b)

    async with await _authed(token_a) as c:
        sched_a = await _create_schedule(c, route_a, name="Tenant A Schedule")

    async with await _authed(token_b) as c:
        await _create_schedule(c, route_b, name="Tenant B Schedule")
        r = await c.get("/api/v1/compliance/schedules")

    ids = [s["id"] for s in r.json()]
    assert str(sched_a["id"]) not in ids, "Tenant B must not see Tenant A's schedules"
