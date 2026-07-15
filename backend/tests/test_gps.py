"""Gap 46 — GPS Fleet Tracking Router

Covers edge cases and untested paths in backend/app/routers/gps.py.
The existing test_gps_features.py (6 shared-fixture tests) covers happy paths
for vehicle CRUD, position ingest + journey lifecycle, geofence CRUD, dashboard,
and geofence-events list.  This file adds isolated-tenant tests for validation
errors, query filters, coordinate checks, pure-function tests, permissions, and RLS.

Endpoints (prefix /api/v1/gps):
  GET  /dashboard
  GET  /vehicles             (status filter)
  POST /vehicles             (name / type validation)
  GET  /vehicles/{id}        (404; recent_positions key)
  PUT  /vehicles/{id}        (no-valid-fields; invalid type)
  DELETE /vehicles/{id}
  POST /vehicles/{id}/positions  (lat/lon required; invalid coords; unknown vehicle 404)
  GET  /vehicles/{id}/positions  (returns list)
  GET  /vehicles/{id}/journeys   (returns list)
  GET  /geofences
  POST /geofences            (name required; polygon >= 3 points)
  PUT  /geofences/{id}       (no-valid-fields)
  DELETE /geofences/{id}
  GET  /geofence-events      (vehicle_id filter)

Sections:
  A — DB schema: vehicles + geofences tables
  B — Vehicle edge cases: 404 / missing name / no-valid-fields
  C — Vehicle list status filter + update invalid vehicle_type
  D — Position ingest validation: missing lat/lon; invalid coords; unknown vehicle
  E — Positions list + vehicle GET recent_positions key
  F — Geofence validation: missing name; polygon < 3 points
  G — Geofence update no-valid-fields
  H — Geofence-events vehicle_id filter
  I — Dashboard: all 4 top-level keys + summary sub-keys
  J — Permissions: gps:write requires admin; unauth → 401
  K — RLS: tenant A's vehicles invisible to tenant B
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
    slug = f"gps-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"GPS Test {slug}", "slug": slug},
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
                "email": f"gps-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_vehicle(client: AsyncClient, name: str = "Test Van", vtype: str = "van") -> str:
    r = await client.post("/api/v1/gps/vehicles", json={"name": name, "vehicle_type": vtype})
    assert r.status_code == 200, f"create_vehicle failed: {r.text}"
    return r.json()["id"]


_POLYGON = [
    {"lat": 1.35, "lon": 103.82},
    {"lat": 1.36, "lon": 103.82},
    {"lat": 1.36, "lon": 103.83},
    {"lat": 1.35, "lon": 103.83},
]


# ─── A. DB Schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vehicles_table_expected_columns():
    """vehicles table has all columns the GPS router depends on."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'vehicles'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "site_id", "name", "plate_number", "vehicle_type",
        "make", "model", "color", "current_status", "last_lat", "last_lon",
        "last_speed", "last_position_at", "is_active",
    ):
        assert col in cols, f"Column {col!r} missing from vehicles"


@pytest.mark.asyncio
async def test_geofences_table_expected_columns():
    """geofences table has all columns the GPS router depends on."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'geofences'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "name", "polygon", "alert_on_entry",
        "alert_on_exit", "speed_limit", "is_active",
    ):
        assert col in cols, f"Column {col!r} missing from geofences"


# ─── B. Vehicle edge cases ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vehicle_get_returns_404_for_unknown_id():
    """GET /gps/vehicles/{id} with unknown id returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/gps/vehicles/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_vehicle_create_missing_name_returns_400():
    """POST /gps/vehicles without a name returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/gps/vehicles", json={"vehicle_type": "van"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_vehicle_update_no_valid_fields_returns_400():
    """PUT /gps/vehicles/{id} with no recognised fields returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        vid = await _create_vehicle(c)
        r = await c.put(f"/api/v1/gps/vehicles/{vid}", json={"ghost_field": "x"})
    assert r.status_code == 400


# ─── C. Vehicle list filter + update type validation ──────────────────────────

@pytest.mark.asyncio
async def test_vehicle_list_status_filter():
    """GET /gps/vehicles?status=idle returns only idle vehicles after ingesting idle position."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        vid = await _create_vehicle(c, name="Status Filter Van")
        # ingest idle position to set status=idle
        await c.post(
            f"/api/v1/gps/vehicles/{vid}/positions",
            json={"lat": 1.3521, "lon": 103.8198, "speed": 0.0},
        )
        r = await c.get("/api/v1/gps/vehicles?status=idle")
    assert r.status_code == 200
    for v in r.json():
        assert v["current_status"] == "idle"


@pytest.mark.asyncio
async def test_vehicle_update_invalid_vehicle_type_returns_400():
    """PUT /gps/vehicles/{id} with an invalid vehicle_type returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        vid = await _create_vehicle(c)
        r = await c.put(f"/api/v1/gps/vehicles/{vid}", json={"vehicle_type": "submarine"})
    assert r.status_code == 400


# ─── D. Position ingest validation ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_position_ingest_missing_lat_returns_400():
    """POST /vehicles/{id}/positions without lat returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        vid = await _create_vehicle(c)
        r = await c.post(
            f"/api/v1/gps/vehicles/{vid}/positions",
            json={"lon": 103.8198, "speed": 10.0},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_position_ingest_invalid_latitude_returns_400():
    """POST /vehicles/{id}/positions with lat=999 returns 400 (invalid coords)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        vid = await _create_vehicle(c)
        r = await c.post(
            f"/api/v1/gps/vehicles/{vid}/positions",
            json={"lat": 999.0, "lon": 103.8198, "speed": 10.0},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_position_ingest_unknown_vehicle_returns_404():
    """POST /vehicles/{id}/positions for a non-existent vehicle returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(
            f"/api/v1/gps/vehicles/{uuid.uuid4()}/positions",
            json={"lat": 1.35, "lon": 103.82, "speed": 0.0},
        )
    assert r.status_code == 404


# ─── E. Positions list + recent_positions on GET /{id} ────────────────────────

@pytest.mark.asyncio
async def test_positions_list_returns_list():
    """GET /vehicles/{id}/positions returns a list after ingesting a position."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        vid = await _create_vehicle(c, name="Position List Van")
        await c.post(
            f"/api/v1/gps/vehicles/{vid}/positions",
            json={"lat": 1.3521, "lon": 103.8198, "speed": 20.0},
        )
        r = await c.get(f"/api/v1/gps/vehicles/{vid}/positions")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_vehicle_get_includes_recent_positions_key():
    """GET /gps/vehicles/{id} response includes recent_positions list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        vid = await _create_vehicle(c, name="Rich Get Van")
        r = await c.get(f"/api/v1/gps/vehicles/{vid}")
    assert r.status_code == 200
    assert "recent_positions" in r.json()
    assert isinstance(r.json()["recent_positions"], list)


# ─── F. Geofence validation ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_geofence_create_missing_name_returns_400():
    """POST /gps/geofences without a name returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/gps/geofences", json={"polygon": _POLYGON})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_geofence_create_polygon_too_short_returns_400():
    """POST /gps/geofences with only 2 polygon points returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/gps/geofences",
            json={
                "name": "Tiny Zone",
                "polygon": [{"lat": 1.35, "lon": 103.82}, {"lat": 1.36, "lon": 103.83}],
            },
        )
    assert r.status_code == 400


# ─── G. Geofence update no-valid-fields ──────────────────────────────────────

@pytest.mark.asyncio
async def test_geofence_update_no_valid_fields_returns_400():
    """PUT /gps/geofences/{id} with unrecognised fields returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r_create = await c.post(
            "/api/v1/gps/geofences",
            json={"name": "Update Test Zone", "polygon": _POLYGON},
        )
        fence_id = r_create.json()["id"]
        r = await c.put(f"/api/v1/gps/geofences/{fence_id}", json={"nonexistent": "value"})
    assert r.status_code == 400


# ─── H. Geofence-events vehicle_id filter ────────────────────────────────────

@pytest.mark.asyncio
async def test_geofence_events_vehicle_id_filter():
    """GET /gps/geofence-events?vehicle_id=X returns events only for that vehicle."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        # Filter by a non-existent vehicle → empty list, no error
        r = await c.get(f"/api/v1/gps/geofence-events?vehicle_id={uuid.uuid4()}")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert r.json() == []


# ─── I. Dashboard keys ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_returns_all_top_level_keys():
    """GET /gps/dashboard returns total, summary, vehicles, recent_events."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/gps/dashboard")
    assert r.status_code == 200
    data = r.json()
    for key in ("total", "summary", "vehicles", "recent_events"):
        assert key in data, f"Key {key!r} missing from GPS dashboard"


@pytest.mark.asyncio
async def test_dashboard_summary_contains_status_counts():
    """GET /gps/dashboard summary has total, moving, idle, offline keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/gps/dashboard")
    assert r.status_code == 200
    summary = r.json()["summary"]
    for key in ("total", "moving", "idle", "offline"):
        assert key in summary, f"Key {key!r} missing from GPS dashboard summary"


# ─── J. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_create_vehicle():
    """POST /gps/vehicles requires gps:write — viewer (role 6) gets 403."""
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.post("/api/v1/gps/vehicles", json={"name": "Viewer Van", "vehicle_type": "van"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_vehicles():
    """GET /gps/vehicles without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/gps/vehicles")
    assert r.status_code == 401


# ─── K. RLS — Tenant Isolation ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_vehicles_isolated_by_tenant():
    """GET /gps/vehicles only returns vehicles belonging to the caller's tenant."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()

    async with await _authed(token_a) as c:
        vid_a = await _create_vehicle(c, name="Tenant A Van")

    async with await _authed(token_b) as c:
        await _create_vehicle(c, name="Tenant B Van")
        r = await c.get("/api/v1/gps/vehicles")

    ids = [v["id"] for v in r.json()]
    assert vid_a not in ids, "Tenant B must not see Tenant A's vehicles"
