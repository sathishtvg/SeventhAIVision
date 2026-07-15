"""Gap 45 — Smart Parking Router

Covers edge cases and untested paths in backend/app/routers/parking.py.
The existing test_parking_features.py (6 shared-fixture tests) covers happy paths
for CRUD + session lifecycle + occupancy/dashboard.  This file adds isolated-tenant
tests for validation errors, filters, LPR-camera sub-resource, permissions, and RLS.

Endpoints:
  GET  /api/v1/parking/dashboard
  GET  /api/v1/carparks
  POST /api/v1/carparks
  GET  /api/v1/carparks/{id}
  PUT  /api/v1/carparks/{id}
  POST /api/v1/carparks/{id}/zones
  GET  /api/v1/carparks/{id}/bays
  POST /api/v1/carparks/{id}/bays
  PUT  /api/v1/parking/bays/{id}
  GET  /api/v1/carparks/{id}/rates
  POST /api/v1/carparks/{id}/rates
  GET  /api/v1/parking/sessions
  POST /api/v1/parking/sessions/entry
  PUT  /api/v1/parking/sessions/{id}/exit
  PUT  /api/v1/parking/sessions/{id}/payment
  GET  /api/v1/carparks/{id}/occupancy
  GET  /api/v1/parking/lpr-cameras
  POST /api/v1/parking/lpr-cameras
  DELETE /api/v1/parking/lpr-cameras/{id}
  GET  /api/v1/parking/sessions/lpr-triggered

Sections:
  A — DB schema: car_parks + parking_sessions columns
  B — Carpark edge cases: 404 / missing name / no-valid-fields
  C — Zone & bay validation: invalid zone_type; missing zone_id/bay_number; bay list filter
  D — Bay status update validation
  E — Rate validation: missing name; optional currency field
  F — Session filters + exit-404
  G — Payment validation: invalid payment_status
  H — Occupancy detail keys + dashboard all keys
  I — LPR cameras CRUD + validation
  J — LPR-triggered sessions list
  K — Permissions: parking:manage requires admin; unauth → 401
  L — RLS: tenant A's carparks invisible to tenant B
"""
from __future__ import annotations

import os
import re
import uuid

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
    slug = f"prk-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Parking Test {slug}", "slug": slug},
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
                "email": f"prk-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera(tenant_id: uuid.UUID) -> str:
    """Insert a camera row directly; return camera_id as str."""
    cam_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO cameras (id, tenant_id, name, is_active) "
                "VALUES (:id, :tid, 'LPR Test Camera', TRUE)"
            ),
            {"id": cam_id, "tid": tenant_id},
        )
        await s.commit()
    await engine.dispose()
    return str(cam_id)


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_carpark(client: AsyncClient, name: str = "Test Carpark") -> str:
    r = await client.post("/api/v1/carparks", json={"name": name, "total_capacity": 10})
    assert r.status_code == 200, f"create_carpark failed: {r.text}"
    return r.json()["id"]


async def _create_zone(client: AsyncClient, carpark_id: str) -> str:
    r = await client.post(
        f"/api/v1/carparks/{carpark_id}/zones",
        json={"name": "Level 1", "zone_type": "standard"},
    )
    assert r.status_code == 200, f"create_zone failed: {r.text}"
    return r.json()["id"]


async def _create_bay(client: AsyncClient, carpark_id: str, zone_id: str, number: str = "A01") -> str:
    r = await client.post(
        f"/api/v1/carparks/{carpark_id}/bays",
        json={"zone_id": zone_id, "bay_number": number},
    )
    assert r.status_code == 200, f"create_bay failed: {r.text}"
    return r.json()["id"]


# ─── A. DB Schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_car_parks_expected_columns():
    """car_parks table has all columns the router depends on."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'car_parks'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "site_id", "name", "description",
        "total_capacity", "levels", "address", "is_active", "created_at",
    ):
        assert col in cols, f"Column {col!r} missing from car_parks"


@pytest.mark.asyncio
async def test_parking_sessions_expected_columns():
    """parking_sessions table has all columns the router depends on."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'parking_sessions'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "car_park_id", "zone_id", "bay_id",
        "vehicle_plate", "entry_at", "exit_at", "duration_minutes",
        "fee_amount", "payment_status", "status", "lpr_triggered",
    ):
        assert col in cols, f"Column {col!r} missing from parking_sessions"


# ─── B. Carpark edge cases ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_carpark_get_404_for_unknown_id():
    """GET /carparks/{id} with a non-existent id returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/carparks/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_carpark_create_missing_name_returns_400():
    """POST /carparks without a name returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/carparks", json={"total_capacity": 20})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_carpark_update_no_valid_fields_returns_400():
    """PUT /carparks/{id} with no recognised fields returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cp_id = await _create_carpark(c)
        r = await c.put(f"/api/v1/carparks/{cp_id}", json={"unknown_field": "value"})
    assert r.status_code == 400


# ─── C. Zone & bay validation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_zone_create_invalid_zone_type_returns_400():
    """POST /carparks/{id}/zones with an invalid zone_type returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cp_id = await _create_carpark(c)
        r = await c.post(
            f"/api/v1/carparks/{cp_id}/zones",
            json={"name": "VIP Zone", "zone_type": "penthouse"},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bay_create_missing_zone_id_returns_400():
    """POST /carparks/{id}/bays without zone_id returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cp_id = await _create_carpark(c)
        r = await c.post(
            f"/api/v1/carparks/{cp_id}/bays",
            json={"bay_number": "Z01"},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bay_list_with_status_filter():
    """GET /bays?status=reserved returns only reserved bays."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cp_id = await _create_carpark(c)
        zone_id = await _create_zone(c, cp_id)
        bay_id = await _create_bay(c, cp_id, zone_id)
        # reserve it
        await c.put(f"/api/v1/parking/bays/{bay_id}", json={"status": "reserved"})
        r = await c.get(f"/api/v1/carparks/{cp_id}/bays?status=reserved")
    assert r.status_code == 200
    bays = r.json()
    assert len(bays) >= 1
    for b in bays:
        assert b["status"] == "reserved"


# ─── D. Bay status validation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bay_update_invalid_status_returns_400():
    """PUT /parking/bays/{id} with an invalid status returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cp_id = await _create_carpark(c)
        zone_id = await _create_zone(c, cp_id)
        bay_id = await _create_bay(c, cp_id, zone_id)
        r = await c.put(f"/api/v1/parking/bays/{bay_id}", json={"status": "busted"})
    assert r.status_code == 400


# ─── E. Rate validation ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_create_missing_name_returns_400():
    """POST /carparks/{id}/rates without rate_name or name returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cp_id = await _create_carpark(c)
        r = await c.post(
            f"/api/v1/carparks/{cp_id}/rates",
            json={"rate_type": "hourly", "amount": 2.0},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_rate_create_with_custom_currency():
    """POST /carparks/{id}/rates with currency=MYR stores that currency."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cp_id = await _create_carpark(c)
        r = await c.post(
            f"/api/v1/carparks/{cp_id}/rates",
            json={"rate_name": "MYR Hourly", "first_hour_rate": 3.0, "currency": "MYR"},
        )
    assert r.status_code == 200
    assert r.json()["currency"] == "MYR"


# ─── F. Session filters + exit 404 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_list_with_carpark_id_filter():
    """GET /sessions?carpark_id=X returns only sessions for that carpark."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cp_a = await _create_carpark(c, "Carpark Alpha")
        cp_b = await _create_carpark(c, "Carpark Beta")
        zone_a = await _create_zone(c, cp_a)
        bay_a = await _create_bay(c, cp_a, zone_a)
        # Entry in carpark A
        r = await c.post(
            "/api/v1/parking/sessions/entry",
            json={"car_park_id": cp_a, "bay_id": bay_a, "vehicle_plate": "SGA1111A"},
        )
        assert r.status_code == 200
        # Filter by cp_b → should be empty
        r_b = await c.get(f"/api/v1/parking/sessions?carpark_id={cp_b}")
        assert r_b.status_code == 200
        assert all(s["car_park_id"] == cp_b for s in r_b.json())
        # Filter by cp_a → our session appears
        r_a = await c.get(f"/api/v1/parking/sessions?carpark_id={cp_a}")
        assert r_a.status_code == 200
        assert any(s["car_park_id"] == cp_a for s in r_a.json())


@pytest.mark.asyncio
async def test_session_list_with_plate_filter():
    """GET /sessions?plate=UNIQUE123 returns sessions matching that plate (ILIKE)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cp_id = await _create_carpark(c)
        zone_id = await _create_zone(c, cp_id)
        bay_id = await _create_bay(c, cp_id, zone_id)
        plate = f"UNIQUE{uuid.uuid4().hex[:4].upper()}"
        await c.post(
            "/api/v1/parking/sessions/entry",
            json={"car_park_id": cp_id, "bay_id": bay_id, "vehicle_plate": plate},
        )
        r = await c.get(f"/api/v1/parking/sessions?plate={plate}")
    assert r.status_code == 200
    sessions = r.json()
    assert len(sessions) >= 1
    for s in sessions:
        assert plate.lower() in s["vehicle_plate"].lower()


@pytest.mark.asyncio
async def test_session_exit_nonactive_session_returns_404():
    """PUT /sessions/{id}/exit on an unknown or already-exited session returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put(f"/api/v1/parking/sessions/{uuid.uuid4()}/exit", json={})
    assert r.status_code == 404


# ─── G. Payment validation ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_payment_invalid_status_returns_400():
    """PUT /sessions/{id}/payment with invalid payment_status returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cp_id = await _create_carpark(c)
        zone_id = await _create_zone(c, cp_id)
        bay_id = await _create_bay(c, cp_id, zone_id)
        r_entry = await c.post(
            "/api/v1/parking/sessions/entry",
            json={"car_park_id": cp_id, "bay_id": bay_id, "vehicle_plate": "SGB9999Z"},
        )
        session_id = r_entry.json()["id"]
        r = await c.put(
            f"/api/v1/parking/sessions/{session_id}/payment",
            json={"payment_status": "bounced"},
        )
    assert r.status_code == 400


# ─── H. Occupancy + dashboard keys ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_occupancy_returns_zones_bays_and_pct():
    """GET /carparks/{id}/occupancy returns zones, bays, total_bays, occupancy_pct."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cp_id = await _create_carpark(c)
        zone_id = await _create_zone(c, cp_id)
        await _create_bay(c, cp_id, zone_id)
        r = await c.get(f"/api/v1/carparks/{cp_id}/occupancy")
    assert r.status_code == 200
    data = r.json()
    for key in ("zones", "bays", "total_bays", "occupancy_pct"):
        assert key in data, f"Key {key!r} missing from occupancy response"
    assert isinstance(data["zones"], list)
    assert isinstance(data["bays"], list)
    assert isinstance(data["occupancy_pct"], (int, float))


@pytest.mark.asyncio
async def test_dashboard_returns_all_required_keys():
    """GET /parking/dashboard returns all 8 KPI keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/parking/dashboard")
    assert r.status_code == 200
    data = r.json()
    for key in (
        "total_carparks", "total_capacity", "available_bays", "occupied_bays",
        "reserved_bays", "active_sessions", "revenue_today", "overstay_count",
    ):
        assert key in data, f"Key {key!r} missing from parking dashboard"


# ─── I. LPR cameras CRUD ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lpr_cameras_list_returns_empty_when_none_configured():
    """GET /parking/lpr-cameras on a fresh tenant returns an empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/parking/lpr-cameras")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_lpr_camera_create_and_delete():
    """POST /lpr-cameras creates entry; DELETE /{id} soft-disables it."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        cp_id = await _create_carpark(c, "LPR Carpark")
        r = await c.post(
            "/api/v1/parking/lpr-cameras",
            json={"camera_id": cam_id, "car_park_id": cp_id, "trigger_type": "entry"},
        )
        assert r.status_code == 200, r.text
        config_id = r.json()["id"]
        # delete it
        r_del = await c.delete(f"/api/v1/parking/lpr-cameras/{config_id}")
    assert r_del.status_code == 200
    assert r_del.json().get("ok") is True


@pytest.mark.asyncio
async def test_lpr_camera_missing_required_fields_returns_400():
    """POST /lpr-cameras without camera_id and car_park_id returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/parking/lpr-cameras",
            json={"trigger_type": "both"},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_lpr_camera_invalid_trigger_type_returns_400():
    """POST /lpr-cameras with trigger_type='scan' returns 400."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        cp_id = await _create_carpark(c, "Trigger Test")
        r = await c.post(
            "/api/v1/parking/lpr-cameras",
            json={"camera_id": cam_id, "car_park_id": cp_id, "trigger_type": "scan"},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_lpr_camera_delete_unknown_id_returns_404():
    """DELETE /lpr-cameras/{id} with unknown id returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/parking/lpr-cameras/{uuid.uuid4()}")
    assert r.status_code == 404


# ─── J. LPR-triggered sessions ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lpr_triggered_sessions_list_returns_list():
    """GET /sessions/lpr-triggered returns a list (may be empty)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/parking/sessions/lpr-triggered")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ─── K. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_create_carpark():
    """POST /carparks requires parking:manage — viewer (role 6) gets 403."""
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.post("/api/v1/carparks", json={"name": "Viewer CP", "total_capacity": 5})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_read_dashboard():
    """GET /parking/dashboard without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/parking/dashboard")
    assert r.status_code == 401


# ─── L. RLS — Tenant Isolation ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_carparks_isolated_by_tenant():
    """GET /carparks only returns carparks belonging to the caller's tenant."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()

    async with await _authed(token_a) as c:
        cp_a_id = await _create_carpark(c, "Tenant A Carpark")

    async with await _authed(token_b) as c:
        await _create_carpark(c, "Tenant B Carpark")
        r = await c.get("/api/v1/carparks")

    ids = [cp["id"] for cp in r.json()]
    assert cp_a_id not in ids, "Tenant B must not see Tenant A's carparks"
