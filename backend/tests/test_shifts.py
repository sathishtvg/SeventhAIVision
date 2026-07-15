"""Gap 38 — Guard Shift Management: CRUD + Lifecycle + Handover + Briefing + SOS + Permissions + RLS

Sections:
  A (2)  — DB schema: shifts + shift_handovers tables with key columns
  B (5)  — Shift CRUD: create, list returns created, get by ID, 404 not found, guard filter
  C (4)  — Shift lifecycle: start (scheduled→active), end (active→completed), start-404, end-non-active-404
  D (3)  — Handover: generate report, retrieve report, 404 when no handover exists
  E (2)  — Briefing: structured response with all sections, 404 for unknown shift
  F (2)  — SOS panic: returns sos_triggered=True, creates critical occurrence_book_entry
  G (2)  — Filters: status=scheduled returns scheduled shifts, status=active excludes scheduled
  H (2)  — Permissions: viewer read=200 manage=403, unauth 401
  I (2)  — RLS isolation: tenant B list excludes tenant A's shifts, get-by-ID returns 404
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


async def _seed_tenant_and_token(role_id: int = 2):
    """Create isolated tenant + user; return (tenant_id, user_id, jwt_token)."""
    from app.core.security import create_access_token, hash_password

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"shf-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Shift Tenant {slug}", "slug": slug},
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


def _future_window():
    """Return (scheduled_start, scheduled_end) ISO strings 1h and 9h from now."""
    now = datetime.now(timezone.utc)
    return (
        (now + timedelta(hours=1)).isoformat(),
        (now + timedelta(hours=9)).isoformat(),
    )


async def _create_shift(client: AsyncClient, **kwargs) -> dict:
    """POST /api/v1/shifts and assert 200; return the JSON body."""
    start, end = _future_window()
    payload = {"scheduled_start": start, "scheduled_end": end, **kwargs}
    r = await client.post("/api/v1/shifts", json=payload)
    assert r.status_code == 200, f"create_shift failed: {r.text}"
    return r.json()


# ── Section A — DB Schema ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shifts_table_has_key_columns():
    """shifts table contains all operational lifecycle columns."""
    expected = {
        "id", "tenant_id", "guard_user_id", "site_id",
        "scheduled_start", "scheduled_end", "actual_start", "actual_end",
        "status", "handover_notes", "created_by_user_id",
    }
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'shifts'"
        ))
        cols = {r[0] for r in rows}
    await engine.dispose()
    assert expected.issubset(cols), f"Missing columns: {expected - cols}"


@pytest.mark.asyncio
async def test_shift_handovers_table_has_summary_columns():
    """shift_handovers table stores patrol + alert count summary fields."""
    expected = {
        "id", "tenant_id", "shift_id", "outgoing_guard_id", "incoming_guard_id",
        "open_incidents_count", "open_alerts_count",
        "patrol_routes_completed", "patrol_routes_total",
        "checkpoints_scanned", "checkpoints_total",
        "outgoing_notes", "summary_json",
    }
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'shift_handovers'"
        ))
        cols = {r[0] for r in rows}
    await engine.dispose()
    assert expected.issubset(cols), f"Missing columns: {expected - cols}"


# ── Section B — Shift CRUD ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_shift_returns_scheduled_status():
    """POST /shifts creates a shift and returns status='scheduled'."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    start, end = _future_window()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/shifts", json={
            "scheduled_start": start,
            "scheduled_end": end,
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "scheduled"
    assert "id" in body


@pytest.mark.asyncio
async def test_list_shifts_returns_created():
    """GET /shifts includes the newly created shift in the response list."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_shift(c)
        r = await c.get("/api/v1/shifts")
    assert r.status_code == 200, r.text
    ids = [s["id"] for s in r.json()]
    assert created["id"] in ids


@pytest.mark.asyncio
async def test_get_shift_by_id_includes_guard_name():
    """GET /shifts/{id} returns the shift with a joined guard_name field."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_shift(c)
        r = await c.get(f"/api/v1/shifts/{created['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == created["id"]
    assert "guard_name" in body


@pytest.mark.asyncio
async def test_get_shift_not_found_returns_404():
    """GET /shifts/{unknown_id} returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/shifts/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_shifts_guard_user_id_filter():
    """GET /shifts?guard_user_id= returns only shifts assigned to that guard."""
    tid, uid, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_shift(c, guard_user_id=str(uid))
        r = await c.get(f"/api/v1/shifts?guard_user_id={uid}")
    assert r.status_code == 200, r.text
    ids = [s["id"] for s in r.json()]
    assert created["id"] in ids


# ── Section C — Shift Lifecycle ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_shift_transitions_to_active():
    """POST /shifts/{id}/start changes status from 'scheduled' to 'active'."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_shift(c)
        r = await c.post(f"/api/v1/shifts/{created['id']}/start")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"


@pytest.mark.asyncio
async def test_end_shift_transitions_to_completed():
    """POST /shifts/{id}/end on an active shift changes status to 'completed'."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_shift(c)
        await c.post(f"/api/v1/shifts/{created['id']}/start")
        r = await c.post(f"/api/v1/shifts/{created['id']}/end")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_start_nonexistent_shift_returns_404():
    """POST /shifts/{unknown}/start returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/shifts/{uuid.uuid4()}/start")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_end_scheduled_shift_returns_404():
    """POST /shifts/{id}/end on a 'scheduled' (not active) shift returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_shift(c)
        # Attempt end without starting first — shift is still 'scheduled'
        r = await c.post(f"/api/v1/shifts/{created['id']}/end")
    assert r.status_code == 404


# ── Section D — Handover ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_handover_returns_summary_fields():
    """POST /shifts/{id}/handover returns a summary with incident/alert/patrol counts."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_shift(c)
        r = await c.post(f"/api/v1/shifts/{created['id']}/handover")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shift_id"] == created["id"]
    for field in ("open_incidents", "open_alerts", "patrol_routes_completed",
                  "patrol_routes_total", "checkpoints_scanned", "id"):
        assert field in body, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_get_handover_after_generate():
    """GET /shifts/{id}/handover returns the stored report after it has been generated."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_shift(c)
        post_r = await c.post(f"/api/v1/shifts/{created['id']}/handover")
        assert post_r.status_code == 200
        get_r = await c.get(f"/api/v1/shifts/{created['id']}/handover")
    assert get_r.status_code == 200, get_r.text
    assert get_r.json()["shift_id"] == created["id"]


@pytest.mark.asyncio
async def test_get_handover_without_prior_generate_returns_404():
    """GET /shifts/{id}/handover returns 404 when no handover has been generated yet."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_shift(c)
        r = await c.get(f"/api/v1/shifts/{created['id']}/handover")
    assert r.status_code == 404


# ── Section E — Briefing ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_briefing_returns_all_sections():
    """GET /shifts/{id}/briefing returns work_permits, visitors, alerts, deliveries, summary."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_shift(c)
        r = await c.get(f"/api/v1/shifts/{created['id']}/briefing")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("shift", "active_work_permits", "expected_visitors",
                "open_alerts", "pending_deliveries", "summary"):
        assert key in body, f"Missing section: {key}"
    assert body["shift"]["id"] == created["id"]


@pytest.mark.asyncio
async def test_briefing_unknown_shift_returns_404():
    """GET /shifts/{unknown}/briefing returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/shifts/{uuid.uuid4()}/briefing")
    assert r.status_code == 404


# ── Section F — SOS Panic ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sos_returns_triggered_true_and_entry_id():
    """POST /shifts/sos returns sos_triggered=True with entry_id and occurred_at."""
    _, _, token = await _seed_tenant_and_token(role_id=5)  # security_guard has guard:sos
    async with await _authed(token) as c:
        r = await c.post("/api/v1/shifts/sos", json={
            "latitude": 1.3521,
            "longitude": 103.8198,
            "description": "Emergency — guard needs assistance",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sos_triggered"] is True
    assert "entry_id" in body
    assert "occurred_at" in body


@pytest.mark.asyncio
async def test_sos_creates_critical_occurrence_book_entry():
    """POST /shifts/sos inserts a critical occurrence_book_entries row in the DB."""
    _, _, token = await _seed_tenant_and_token(role_id=5)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/shifts/sos", json={"description": "SOS DB verify"})
    assert r.status_code == 200
    entry_id = r.json()["entry_id"]

    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        row = (await s.execute(
            text(
                "SELECT severity, entry_type FROM occurrence_book_entries "
                "WHERE id = CAST(:eid AS uuid)"
            ),
            {"eid": entry_id},
        )).first()
    await engine.dispose()
    assert row is not None, "occurrence_book_entries row not found after SOS"
    assert row.severity == "critical"
    assert row.entry_type == "incident"


# ── Section G — Filters ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_shifts_status_filter_scheduled_matches():
    """GET /shifts?shift_status=scheduled returns only scheduled shifts, including new one."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_shift(c)
        r = await c.get("/api/v1/shifts?shift_status=scheduled")
    assert r.status_code == 200, r.text
    body = r.json()
    assert all(s["status"] == "scheduled" for s in body)
    assert created["id"] in [s["id"] for s in body]


@pytest.mark.asyncio
async def test_list_shifts_status_filter_active_excludes_scheduled():
    """GET /shifts?shift_status=active does not return a newly created scheduled shift."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_shift(c)
        r = await c.get("/api/v1/shifts?shift_status=active")
    assert r.status_code == 200, r.text
    ids = [s["id"] for s in r.json()]
    assert created["id"] not in ids


# ── Section H — Permissions ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_can_read_but_not_manage_shifts():
    """Role 6 (viewer) can list shifts (shift:read=200) but cannot create (shift:manage=403)."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    start, end = _future_window()
    async with await _authed(token) as c:
        get_r = await c.get("/api/v1/shifts")
        post_r = await c.post("/api/v1/shifts", json={
            "scheduled_start": start,
            "scheduled_end": end,
        })
    assert get_r.status_code == 200
    assert post_r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_shifts_returns_401():
    """Requests to /shifts without a token return 401."""
    async with AsyncClient(
        transport=ASGITransport(_app()), base_url="http://test"
    ) as c:
        r = await c.get("/api/v1/shifts")
    assert r.status_code == 401


# ── Section I — RLS Isolation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_tenant_b_cannot_list_tenant_a_shifts():
    """Tenant B's shift list does not contain Tenant A's shifts."""
    _, _, token_a = await _seed_tenant_and_token(role_id=2)
    _, _, token_b = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token_a) as c:
        shift_a = await _create_shift(c)
    async with await _authed(token_b) as c:
        r = await c.get("/api/v1/shifts")
    assert r.status_code == 200, r.text
    ids = [s["id"] for s in r.json()]
    assert shift_a["id"] not in ids


@pytest.mark.asyncio
async def test_rls_tenant_b_get_tenant_a_shift_by_id_returns_404():
    """Tenant B cannot fetch Tenant A's shift by ID — RLS makes it appear non-existent."""
    _, _, token_a = await _seed_tenant_and_token(role_id=2)
    _, _, token_b = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token_a) as c:
        shift_a = await _create_shift(c)
    async with await _authed(token_b) as c:
        r = await c.get(f"/api/v1/shifts/{shift_a['id']}")
    assert r.status_code == 404
