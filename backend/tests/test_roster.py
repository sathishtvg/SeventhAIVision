"""Gap 86 — Guard roster / recurring shift patterns (isolated-tenant)

Tests shift_patterns CRUD, idempotent roster generation, the coverage
window, and permissions/RLS.

Key semantics:
  - days_of_week: 0=Monday .. 6=Sunday; 422 on empty/out-of-range
  - POST /shifts/roster/generate expands active patterns over the next N
    days; unique (pattern_id, scheduled_start) makes re-runs no-ops
  - shift:read (viewer OK) for list/coverage; shift:manage (roles 1-3) for
    create/update/delete/generate

Sections:
  A — Pattern CRUD (5 tests)
  B — Validation (3 tests)
  C — Generation (4 tests)
  D — Coverage (1 test)
  E — Permissions + RLS (3 tests)
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

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 2):
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"rst-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Roster Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'Roster Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"rst-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_guard(tenant_id: uuid.UUID) -> uuid.UUID:
    guard_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, 5, :email, 'hashed', 'Roster Guard')"
            ),
            {"id": guard_id, "tid": tenant_id,
             "email": f"rst-{guard_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return guard_id


async def _seed_site(tenant_id: uuid.UUID, name: str = "Roster Site") -> uuid.UUID:
    site_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :name)"),
            {"id": site_id, "tid": tenant_id, "name": name},
        )
        await s.commit()
    await engine.dispose()
    return site_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_pattern(c: AsyncClient, site_id, guard_id,
                          days: list[int] = ALL_DAYS,
                          start_time: str = "08:00",
                          duration: int = 480,
                          label: str = "Day Shift") -> dict:
    r = await c.post("/api/v1/shifts/roster/patterns", json={
        "site_id": str(site_id), "guard_user_id": str(guard_id),
        "days_of_week": days, "start_time": start_time,
        "duration_minutes": duration, "label": label,
    })
    assert r.status_code == 201, f"create_pattern failed: {r.text}"
    return r.json()


# ─── A. Pattern CRUD ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rst_patterns_list_empty():
    """Fresh tenant has no roster patterns."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/shifts/roster/patterns")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_rst_create_pattern_returns_fields():
    """POST pattern → 201 with all fields; start_time normalised to HH:MM."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    guard_id = await _seed_guard(tenant_id)
    async with await _authed(token) as c:
        body = await _create_pattern(c, site_id, guard_id, days=[0, 2, 4])
    for field in ("id", "site_id", "guard_user_id", "label", "days_of_week",
                  "start_time", "duration_minutes", "is_active", "created_at"):
        assert field in body, f"Missing field: {field}"
    assert body["days_of_week"] == [0, 2, 4]
    assert body["start_time"] == "08:00"
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_rst_pattern_appears_in_list_with_names():
    """List includes guard_name and site_name from JOINs."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id, "HQ Tower")
    guard_id = await _seed_guard(tenant_id)
    async with await _authed(token) as c:
        created = await _create_pattern(c, site_id, guard_id)
        r = await c.get("/api/v1/shifts/roster/patterns")
    rows = r.json()
    row = next(rw for rw in rows if rw["id"] == created["id"])
    assert row["site_name"] == "HQ Tower"
    assert row["guard_name"] == "Roster Guard"


@pytest.mark.asyncio
async def test_rst_update_pattern():
    """PUT updates days/duration/is_active; 404 for unknown id."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    guard_id = await _seed_guard(tenant_id)
    async with await _authed(token) as c:
        created = await _create_pattern(c, site_id, guard_id)
        r = await c.put(f"/api/v1/shifts/roster/patterns/{created['id']}",
                        json={"days_of_week": [5, 6], "duration_minutes": 720,
                              "is_active": False})
        assert r.status_code == 200
        assert r.json()["days_of_week"] == [5, 6]
        assert r.json()["duration_minutes"] == 720
        assert r.json()["is_active"] is False
        r404 = await c.put(f"/api/v1/shifts/roster/patterns/{uuid.uuid4()}",
                           json={"label": "x"})
    assert r404.status_code == 404


@pytest.mark.asyncio
async def test_rst_delete_pattern():
    """DELETE removes the pattern; second delete → 404."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    guard_id = await _seed_guard(tenant_id)
    async with await _authed(token) as c:
        created = await _create_pattern(c, site_id, guard_id)
        r1 = await c.delete(f"/api/v1/shifts/roster/patterns/{created['id']}")
        r2 = await c.delete(f"/api/v1/shifts/roster/patterns/{created['id']}")
    assert r1.status_code == 200 and r1.json()["deleted"] is True
    assert r2.status_code == 404


# ─── B. Validation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rst_invalid_days_422():
    """Empty days list and out-of-range day both → 422."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    guard_id = await _seed_guard(tenant_id)
    base = {"site_id": str(site_id), "guard_user_id": str(guard_id),
            "start_time": "08:00", "duration_minutes": 480}
    async with await _authed(token) as c:
        r_empty = await c.post("/api/v1/shifts/roster/patterns",
                               json={**base, "days_of_week": []})
        r_range = await c.post("/api/v1/shifts/roster/patterns",
                               json={**base, "days_of_week": [7]})
    assert r_empty.status_code == 422
    assert r_range.status_code == 422


@pytest.mark.asyncio
async def test_rst_invalid_time_and_duration_422():
    """Bad start_time format and out-of-range duration → 422."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    guard_id = await _seed_guard(tenant_id)
    base = {"site_id": str(site_id), "guard_user_id": str(guard_id),
            "days_of_week": [0]}
    async with await _authed(token) as c:
        r_time = await c.post("/api/v1/shifts/roster/patterns",
                              json={**base, "start_time": "8am", "duration_minutes": 480})
        r_dur = await c.post("/api/v1/shifts/roster/patterns",
                             json={**base, "start_time": "08:00", "duration_minutes": 0})
    assert r_time.status_code == 422
    assert r_dur.status_code == 422


@pytest.mark.asyncio
async def test_rst_unknown_site_or_guard_404():
    """Pattern referencing a missing site or guard → 404."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    guard_id = await _seed_guard(tenant_id)
    async with await _authed(token) as c:
        r_site = await c.post("/api/v1/shifts/roster/patterns", json={
            "site_id": str(uuid.uuid4()), "guard_user_id": str(guard_id),
            "days_of_week": [0], "start_time": "08:00", "duration_minutes": 480})
        r_guard = await c.post("/api/v1/shifts/roster/patterns", json={
            "site_id": str(site_id), "guard_user_id": str(uuid.uuid4()),
            "days_of_week": [0], "start_time": "08:00", "duration_minutes": 480})
    assert r_site.status_code == 404
    assert r_guard.status_code == 404


# ─── C. Generation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rst_generate_creates_shifts_all_days():
    """Every-day pattern over 7 days → exactly 7 scheduled shifts, linked
    to the pattern, 8h long."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    guard_id = await _seed_guard(tenant_id)
    async with await _authed(token) as c:
        pattern = await _create_pattern(c, site_id, guard_id, days=ALL_DAYS)
        r = await c.post("/api/v1/shifts/roster/generate", json={"days_ahead": 7})
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 7
        r_shifts = await c.get(f"/api/v1/shifts?guard_user_id={guard_id}&limit=50")
    rows = r_shifts.json()
    assert len(rows) == 7
    assert all(row["status"] == "scheduled" for row in rows)
    one = rows[0]
    start = datetime.fromisoformat(one["scheduled_start"])
    end = datetime.fromisoformat(one["scheduled_end"])
    assert end - start == timedelta(minutes=480)


@pytest.mark.asyncio
async def test_rst_generate_idempotent():
    """Second generate call over the same window creates 0 new shifts."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    guard_id = await _seed_guard(tenant_id)
    async with await _authed(token) as c:
        await _create_pattern(c, site_id, guard_id, days=ALL_DAYS)
        r1 = await c.post("/api/v1/shifts/roster/generate", json={"days_ahead": 7})
        r2 = await c.post("/api/v1/shifts/roster/generate", json={"days_ahead": 7})
    assert r1.json()["created"] == 7
    assert r2.json()["created"] == 0


@pytest.mark.asyncio
async def test_rst_generate_respects_days_of_week():
    """A single-weekday pattern creates exactly one shift in a 7-day window,
    and it lands on that weekday."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    guard_id = await _seed_guard(tenant_id)
    target_day = (datetime.now(timezone.utc).weekday() + 2) % 7  # some day this week
    async with await _authed(token) as c:
        await _create_pattern(c, site_id, guard_id, days=[target_day])
        r = await c.post("/api/v1/shifts/roster/generate", json={"days_ahead": 7})
        assert r.json()["created"] == 1
        r_shifts = await c.get(f"/api/v1/shifts?guard_user_id={guard_id}")
    rows = r_shifts.json()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_rst_generate_skips_inactive_pattern():
    """Deactivated patterns generate nothing."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    guard_id = await _seed_guard(tenant_id)
    async with await _authed(token) as c:
        pattern = await _create_pattern(c, site_id, guard_id, days=ALL_DAYS)
        await c.put(f"/api/v1/shifts/roster/patterns/{pattern['id']}",
                    json={"is_active": False})
        r = await c.post("/api/v1/shifts/roster/generate", json={"days_ahead": 7})
    assert r.json()["created"] == 0


# ─── D. Coverage ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rst_coverage_window():
    """Coverage returns generated shifts with guard + site names."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id, "Coverage Site")
    guard_id = await _seed_guard(tenant_id)
    async with await _authed(token) as c:
        await _create_pattern(c, site_id, guard_id, days=ALL_DAYS)
        await c.post("/api/v1/shifts/roster/generate", json={"days_ahead": 3})
        r = await c.get("/api/v1/shifts/roster/coverage?days=3")
    body = r.json()
    assert body["days"] == 3
    assert len(body["shifts"]) >= 1
    row = body["shifts"][0]
    assert row["site_name"] == "Coverage Site"
    assert row["guard_name"] == "Roster Guard"
    assert row["pattern_id"] is not None


# ─── E. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rst_viewer_can_list_but_not_create():
    """Viewer (role 6) has shift:read → list 200; lacks shift:manage → 403."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    from app.core.security import create_access_token
    viewer_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                 "VALUES (:id, :tid, 6, :email, 'hashed')"),
            {"id": viewer_id, "tid": tenant_id,
             "email": f"rst-{viewer_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    viewer_token = create_access_token(str(viewer_id), str(tenant_id), 6)
    site_id = await _seed_site(tenant_id)
    guard_id = await _seed_guard(tenant_id)
    async with await _authed(viewer_token) as c:
        r_list = await c.get("/api/v1/shifts/roster/patterns")
        r_create = await c.post("/api/v1/shifts/roster/patterns", json={
            "site_id": str(site_id), "guard_user_id": str(guard_id),
            "days_of_week": [0], "start_time": "08:00", "duration_minutes": 480})
    assert r_list.status_code == 200
    assert r_create.status_code == 403


@pytest.mark.asyncio
async def test_rst_generate_requires_manage():
    """Viewer cannot trigger generation (shift:manage) → 403."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    from app.core.security import create_access_token
    viewer_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                 "VALUES (:id, :tid, 6, :email, 'hashed')"),
            {"id": viewer_id, "tid": tenant_id,
             "email": f"rst-{viewer_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    viewer_token = create_access_token(str(viewer_id), str(tenant_id), 6)
    async with await _authed(viewer_token) as c:
        r = await c.post("/api/v1/shifts/roster/generate", json={"days_ahead": 7})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_rst_rls_isolation():
    """Tenant B cannot see Tenant A's patterns."""
    tenant_a, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    site_a = await _seed_site(tenant_a)
    guard_a = await _seed_guard(tenant_a)
    async with await _authed(tok_a) as c:
        await _create_pattern(c, site_a, guard_a)
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/shifts/roster/patterns")
    assert r.json() == []
