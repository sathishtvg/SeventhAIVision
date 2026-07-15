"""Gap 37 — Visitor Management: Full CRUD + QR Check-In Flow + Permissions + RLS

Sections:
  A (3)  — DB schema: visitors + visitor_logs tables with QR enhancement columns
  B (5)  — Visitor CRUD: create, list, status-filter, deactivate, not-found 404
  C (4)  — QR code image + lookup: PNG response, by-qr lookup, invalid token 404, not-found 404
  D (4)  — QR scan check-in: arrival, auto-departure on re-scan, invalid QR 404, status updated
  E (3)  — Manual checkin/checkout by ID: arrival, departure, not-found 404
  F (3)  — Visitor logs: unfiltered list, filter by visitor_id, unregistered walk-in
  G (2)  — Upcoming visitors: no future → empty, future expected_from → visible
  H (2)  — Send QR email: 422 for visitor with no email, 200 for visitor with email
  I (3)  — Unregistered walk-in via POST /checkin: success, 422 without full_name, event created
  J (3)  — Permissions: viewer read=200 manage=403, operator checkin=200 manage=403, unauth 401
  K (2)  — RLS isolation: tenant B list excludes tenant A's visitors
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
    slug = f"vis-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Vis Tenant {slug}", "slug": slug},
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


async def _create_visitor(client: AsyncClient, **kwargs) -> dict:
    """Helper: POST /api/v1/visitors and return the JSON body."""
    payload = {"full_name": "Test Visitor", **kwargs}
    r = await client.post("/api/v1/visitors", json=payload)
    assert r.status_code == 201, f"create_visitor failed: {r.text}"
    return r.json()


# ── Section A — DB Schema ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_visitors_table_columns():
    """visitors table has core columns including QR and status columns from migration 0033."""
    expected = {
        "id", "tenant_id", "site_id", "full_name", "id_number", "company",
        "host_user_id", "host_name", "purpose", "vehicle_plate",
        "expected_from", "expected_until", "is_active", "created_by_user_id",
        "created_at", "updated_at",
        # Added in 0033:
        "qr_token", "visitor_email", "qr_email_sent_at", "status",
    }
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'visitors'"
        ))
        cols = {r[0] for r in rows}
    await engine.dispose()
    missing = expected - cols
    assert not missing, f"Missing columns: {missing}"


@pytest.mark.asyncio
async def test_visitor_logs_table_columns():
    """visitor_logs table has core columns including QR enhancement columns from 0033."""
    expected = {
        "id", "tenant_id", "visitor_id", "site_id", "guard_user_id",
        "event_type", "badge_number", "notes", "is_unregistered", "occurred_at",
        # Added in 0033:
        "checkin_method", "qr_token_used",
    }
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'visitor_logs'"
        ))
        cols = {r[0] for r in rows}
    await engine.dispose()
    missing = expected - cols
    assert not missing, f"Missing visitor_logs columns: {missing}"


@pytest.mark.asyncio
async def test_visitor_permissions_seeded():
    """visitor:manage, visitor:read, and visitor:checkin permission codes are in the DB."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT code FROM permissions WHERE code IN "
            "('visitor:manage', 'visitor:read', 'visitor:checkin')"
        ))
        codes = {r[0] for r in rows}
    await engine.dispose()
    assert codes == {"visitor:manage", "visitor:read", "visitor:checkin"}


# ── Section B — Visitor CRUD ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_visitor_returns_201():
    """POST /visitors creates a visitor and returns id + qr_token."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/visitors", json={
            "full_name": "Alice Chen",
            "company": "Acme Corp",
            "purpose": "Meeting",
        })
    assert r.status_code == 201, r.text
    body = r.json()
    assert "id" in body
    assert "qr_token" in body
    assert body["full_name"] == "Alice Chen"


@pytest.mark.asyncio
async def test_list_visitors_returns_created():
    """GET /visitors lists the visitor just created."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        await _create_visitor(c, full_name="Bob Lee")
        r = await c.get("/api/v1/visitors")
    assert r.status_code == 200, r.text
    body = r.json()
    names = [v["full_name"] for v in body["items"]]
    assert "Bob Lee" in names


@pytest.mark.asyncio
async def test_list_visitors_status_filter():
    """GET /visitors?status=pending returns only pending visitors."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        await _create_visitor(c, full_name="Pending Visitor")
        r = await c.get("/api/v1/visitors", params={"status": "pending"})
    assert r.status_code == 200, r.text
    body = r.json()
    statuses = {v["status"] for v in body["items"]}
    assert statuses <= {"pending"}, f"Non-pending status found: {statuses}"


@pytest.mark.asyncio
async def test_deactivate_visitor():
    """DELETE /visitors/{id} sets is_active=False."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        v = await _create_visitor(c, full_name="To Deactivate")
        r = await c.delete(f"/api/v1/visitors/{v['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_active"] is False


@pytest.mark.asyncio
async def test_deactivate_nonexistent_visitor_404():
    """DELETE /visitors/{random-uuid} returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/visitors/{uuid.uuid4()}")
    assert r.status_code == 404


# ── Section C — QR Code Image + Lookup ───────────────────────────────────────

@pytest.mark.asyncio
async def test_get_visitor_qr_png():
    """GET /visitors/{id}/qr.png returns 200 with Content-Type image/png."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        v = await _create_visitor(c, full_name="QR Test")
        r = await c.get(f"/api/v1/visitors/{v['id']}/qr.png")
    assert r.status_code == 200, r.text
    assert "image/png" in r.headers.get("content-type", "")
    assert len(r.content) > 100


@pytest.mark.asyncio
async def test_get_visitor_qr_png_not_found():
    """GET /visitors/{random-uuid}/qr.png returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/visitors/{uuid.uuid4()}/qr.png")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_lookup_by_qr_token():
    """GET /visitors/by-qr/{token} returns the visitor's data."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        v = await _create_visitor(c, full_name="QR Lookup")
        qr_token = v["qr_token"]
        r = await c.get(f"/api/v1/visitors/by-qr/{qr_token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["full_name"] == "QR Lookup"
    assert body["qr_token"] == qr_token


@pytest.mark.asyncio
async def test_lookup_by_qr_invalid_token_404():
    """GET /visitors/by-qr/nonexistent-token returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/visitors/by-qr/this-token-does-not-exist")
    assert r.status_code == 404


# ── Section D — QR Scan Check-In ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_qr_scan_first_scan_is_arrival():
    """First POST /qr-scan with a valid token creates an arrival event."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        v = await _create_visitor(c, full_name="QR Arrival")
        r = await c.post("/api/v1/visitors/qr-scan", json={"qr_token": v["qr_token"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event_type"] == "arrival"


@pytest.mark.asyncio
async def test_qr_scan_second_scan_is_departure():
    """Second POST /qr-scan on an 'arrived' visitor creates a departure event."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        v = await _create_visitor(c, full_name="QR Depart")
        qr_token = v["qr_token"]
        # First scan → arrival
        r1 = await c.post("/api/v1/visitors/qr-scan", json={"qr_token": qr_token})
        assert r1.status_code == 200
        assert r1.json()["event_type"] == "arrival"
        # Second scan → departure (visitor is now 'arrived')
        r2 = await c.post("/api/v1/visitors/qr-scan", json={"qr_token": qr_token})
    assert r2.status_code == 200, r2.text
    assert r2.json()["event_type"] == "departure"


@pytest.mark.asyncio
async def test_qr_scan_invalid_token_404():
    """POST /qr-scan with unknown QR token returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/visitors/qr-scan", json={"qr_token": "invalid-token-xyz"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_qr_scan_updates_visitor_status_to_arrived():
    """After QR scan arrival, GET /by-qr shows status='arrived'."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        v = await _create_visitor(c, full_name="Status Check")
        qr_token = v["qr_token"]
        await c.post("/api/v1/visitors/qr-scan", json={"qr_token": qr_token})
        r = await c.get(f"/api/v1/visitors/by-qr/{qr_token}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "arrived"


# ── Section E — Manual Checkin / Checkout ─────────────────────────────────────

@pytest.mark.asyncio
async def test_manual_checkin_by_id():
    """POST /visitors/{id}/checkin records arrival and returns event_type='arrival'."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        v = await _create_visitor(c, full_name="Manual In")
        r = await c.post(f"/api/v1/visitors/{v['id']}/checkin", json={"notes": "manual test"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event_type"] == "arrival"


@pytest.mark.asyncio
async def test_manual_checkout_by_id():
    """POST /visitors/{id}/checkout records departure and returns event_type='departure'."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        v = await _create_visitor(c, full_name="Manual Out")
        await c.post(f"/api/v1/visitors/{v['id']}/checkin", json={})
        r = await c.post(f"/api/v1/visitors/{v['id']}/checkout", json={})
    assert r.status_code == 200, r.text
    assert r.json()["event_type"] == "departure"


@pytest.mark.asyncio
async def test_manual_checkin_not_found_404():
    """POST /visitors/{random-uuid}/checkin returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/visitors/{uuid.uuid4()}/checkin", json={})
    assert r.status_code == 404


# ── Section F — Visitor Logs ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_visitor_logs_after_checkin():
    """GET /visitors/logs returns log entries created by check-in operations."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        v = await _create_visitor(c, full_name="Log Test")
        await c.post(f"/api/v1/visitors/{v['id']}/checkin", json={})
        r = await c.get("/api/v1/visitors/logs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    assert any(entry["event_type"] == "arrival" for entry in body)


@pytest.mark.asyncio
async def test_list_visitor_logs_filter_by_visitor_id():
    """GET /visitors/logs?visitor_id=X returns only that visitor's logs."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        v1 = await _create_visitor(c, full_name="Visitor One")
        v2 = await _create_visitor(c, full_name="Visitor Two")
        await c.post(f"/api/v1/visitors/{v1['id']}/checkin", json={})
        await c.post(f"/api/v1/visitors/{v2['id']}/checkin", json={})
        r = await c.get("/api/v1/visitors/logs", params={"visitor_id": v1["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    for entry in body:
        assert str(entry["visitor_id"]) == v1["id"], f"Unexpected visitor_id: {entry['visitor_id']}"


@pytest.mark.asyncio
async def test_unregistered_walkin_via_checkin_endpoint():
    """POST /visitors/checkin with no visitor_id but with full_name returns 200 (walk-in)."""
    _, _, token = await _seed_tenant_and_token(role_id=4)  # operator role
    async with await _authed(token) as c:
        r = await c.post("/api/v1/visitors/checkin", json={
            "full_name": "Walk-In Guest",
            "event_type": "arrival",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event_type"] == "arrival"
    # visitor_id should be None since this is a walk-in
    assert body.get("visitor_id") is None


# ── Section G — Upcoming Visitors ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upcoming_visitors_empty_when_no_future():
    """GET /visitors/upcoming returns empty list when no visitors have future expected_from."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        # Create a visitor with no expected_from — won't appear in upcoming
        await _create_visitor(c, full_name="No Date Visitor")
        r = await c.get("/api/v1/visitors/upcoming")
    assert r.status_code == 200, r.text
    body = r.json()
    # Visitor without expected_from should not appear in upcoming
    names = [v["full_name"] for v in body]
    assert "No Date Visitor" not in names


@pytest.mark.asyncio
async def test_upcoming_visitors_shows_future_expected():
    """GET /visitors/upcoming returns visitors with expected_from within the next 24h."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    async with await _authed(token) as c:
        await _create_visitor(c, full_name="Coming Soon", expected_from=soon)
        r = await c.get("/api/v1/visitors/upcoming", params={"hours": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    names = [v["full_name"] for v in body]
    assert "Coming Soon" in names


# ── Section H — Send QR Email ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_qr_email_no_email_returns_422():
    """POST /visitors/{id}/send-qr returns 422 when visitor has no email on file."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        v = await _create_visitor(c, full_name="No Email Visitor")
        # No visitor_email set
        r = await c.post(f"/api/v1/visitors/{v['id']}/send-qr")
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_send_qr_email_with_email_returns_200():
    """POST /visitors/{id}/send-qr returns 200 when visitor has an email (SMTP fails silently)."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        v = await _create_visitor(c, full_name="Email Visitor", visitor_email="visitor@example.com")
        r = await c.post(f"/api/v1/visitors/{v['id']}/send-qr")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["email"] == "visitor@example.com"


# ── Section I — Unregistered Walk-In ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_unregistered_walkin_without_full_name_422():
    """POST /visitors/checkin with no visitor_id and no full_name returns 422."""
    _, _, token = await _seed_tenant_and_token(role_id=4)  # operator
    async with await _authed(token) as c:
        r = await c.post("/api/v1/visitors/checkin", json={
            "event_type": "arrival",
            # no visitor_id, no full_name
        })
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_unregistered_walkin_with_full_name_200():
    """POST /visitors/checkin with full_name and no visitor_id succeeds as walk-in."""
    _, _, token = await _seed_tenant_and_token(role_id=5)  # security_guard
    async with await _authed(token) as c:
        r = await c.post("/api/v1/visitors/checkin", json={
            "full_name": "Unregistered Bob",
            "company": "Surprise Inc",
            "event_type": "arrival",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event_type"] == "arrival"


@pytest.mark.asyncio
async def test_unregistered_walkin_log_visible_in_logs():
    """Walk-in log entries appear in GET /visitors/logs with visitor_id=null."""
    _, _, token = await _seed_tenant_and_token(role_id=4)  # operator
    async with await _authed(token) as c:
        await c.post("/api/v1/visitors/checkin", json={
            "full_name": "Walk-In Carol",
            "event_type": "arrival",
        })
        r = await c.get("/api/v1/visitors/logs")
    assert r.status_code == 200, r.text
    body = r.json()
    walk_ins = [e for e in body if e.get("visitor_id") is None]
    assert len(walk_ins) >= 1


# ── Section J — Permissions ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_can_read_but_not_manage():
    """Role 6 (viewer) can GET /visitors but cannot POST /visitors."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r_read = await c.get("/api/v1/visitors")
        r_create = await c.post("/api/v1/visitors", json={"full_name": "Blocked"})
    assert r_read.status_code == 200, f"viewer read failed: {r_read.text}"
    assert r_create.status_code == 403, f"viewer should be blocked from creating: {r_create.status_code}"


@pytest.mark.asyncio
async def test_operator_can_checkin_but_not_manage():
    """Role 4 (operator) can POST /checkin but cannot POST /visitors (create)."""
    _, _, token = await _seed_tenant_and_token(role_id=4)
    async with await _authed(token) as c:
        # operator can check in (visitor:checkin)
        r_checkin = await c.post("/api/v1/visitors/checkin", json={
            "full_name": "Op Walk-In",
            "event_type": "arrival",
        })
        # operator cannot create pre-registered visitors (visitor:manage required)
        r_create = await c.post("/api/v1/visitors", json={"full_name": "Blocked"})
    assert r_checkin.status_code == 200, f"operator checkin failed: {r_checkin.text}"
    assert r_create.status_code == 403, f"operator should be blocked from create: {r_create.status_code}"


@pytest.mark.asyncio
async def test_unauthenticated_returns_401():
    """Requests without Authorization header return 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/visitors")
    assert r.status_code == 401


# ── Section K — RLS Isolation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_tenant_a_visitor_not_visible_to_tenant_b():
    """Tenant A's visitor is not visible in Tenant B's visitor list."""
    _, _, token_a = await _seed_tenant_and_token(role_id=2)
    _, _, token_b = await _seed_tenant_and_token(role_id=2)

    async with await _authed(token_a) as c:
        v = await _create_visitor(c, full_name="Tenant A Visitor")
        visitor_id = v["id"]

    async with await _authed(token_b) as c:
        r = await c.get("/api/v1/visitors")

    assert r.status_code == 200
    ids = [item["id"] for item in r.json()["items"]]
    assert visitor_id not in ids, "Tenant B should not see Tenant A's visitor"


@pytest.mark.asyncio
async def test_rls_tenant_b_list_empty_if_no_own_visitors():
    """A fresh tenant with no visitors gets an empty list (RLS enforced, count=0)."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/visitors")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
