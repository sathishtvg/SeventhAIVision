"""Gap 48 — Body Worn Camera (BWC) Router

Covers edge cases and untested paths in backend/app/routers/bwc.py.
The existing test_bwc_features.py (9 shared-fixture tests) covers happy paths
for camera CRUD, assign/unassign, recording start+stop, events list, telemetry,
and dashboard basics.  This file adds isolated-tenant tests for validation
errors, filters, assignment edge cases, recording lifecycle, permissions, and RLS.

Endpoints (prefix /api/v1/bwc):
  GET  /dashboard
  GET  /cameras                        (status filter)
  POST /cameras                        (serial required)
  GET  /cameras/{id}                   (404; recent_events key)
  PUT  /cameras/{id}                   (no valid fields → 400)
  PUT  /cameras/{id}/telemetry
  POST /cameras/{id}/assign            (missing user_id → 400; 404)
  POST /cameras/{id}/unassign
  GET  /cameras/{id}/assignments
  GET  /recordings                     (camera_id filter)
  POST /cameras/{id}/recordings/start  (bad camera status → 422)
  POST /cameras/{id}/recordings/{id}/stop  (not found → 404)
  PUT  /recordings/{id}/link-incident  (recording 404; incident 404)
  GET  /events                         (event_type filter)

Sections:
  A — DB schema: body_cameras + bwc_recordings columns
  B — Camera validation: 404 / update no-valid-fields 400
  C — Camera list status filter + GET includes recent_events key
  D — Assign validation: missing user_id 400 / camera not found 404
  E — Recording lifecycle: bad camera status 422 / stop not-found 404
  F — Recording list camera_id filter
  G — Link-incident: recording not found 404 / incident not found 404
  H — Events event_type filter
  I — Dashboard: all 8 keys present
  J — Permissions: bwc:manage requires admin; unauth → 401
  K — RLS: tenant A's cameras invisible to tenant B
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
    slug = f"bwc-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"BWC Test {slug}", "slug": slug},
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
                "email": f"bwc-{user_id.hex[:8]}@test.local",
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


async def _create_camera(client: AsyncClient, serial: str = "BWC-GAP-001", **kwargs) -> str:
    r = await client.post("/api/v1/bwc/cameras", json={"serial_number": serial, **kwargs})
    assert r.status_code == 200, f"create_camera failed: {r.text}"
    return r.json()["id"]


# ─── A. DB Schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_body_cameras_table_expected_columns():
    """body_cameras table has all columns the BWC router depends on."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'body_cameras'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "serial_number", "model", "firmware_version",
        "status", "is_active", "is_recording", "battery_pct",
        "storage_total_gb", "storage_used_gb", "assigned_user_id",
        "assigned_at", "last_sync_at", "notes",
    ):
        assert col in cols, f"Column {col!r} missing from body_cameras"


@pytest.mark.asyncio
async def test_bwc_recordings_table_expected_columns():
    """bwc_recordings table has all columns the BWC router depends on."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'bwc_recordings'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "camera_id", "user_id", "incident_id",
        "title", "trigger_type", "status", "started_at", "ended_at",
        "duration_seconds", "file_size_mb",
    ):
        assert col in cols, f"Column {col!r} missing from bwc_recordings"


# ─── B. Camera validation ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_camera_get_returns_404_for_unknown_id():
    """GET /bwc/cameras/{id} with unknown id returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/bwc/cameras/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_camera_update_no_valid_fields_returns_400():
    """PUT /bwc/cameras/{id} with no recognised fields returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_camera(c, serial="BWC-NOFLD-001")
        r = await c.put(f"/api/v1/bwc/cameras/{cid}", json={"ghost_field": "x"})
    assert r.status_code == 400


# ─── C. Camera list status filter + recent_events key ─────────────────────────

@pytest.mark.asyncio
async def test_camera_list_status_filter():
    """GET /bwc/cameras?status=available returns only available cameras."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await _create_camera(c, serial="BWC-AVAIL-001")
        r = await c.get("/api/v1/bwc/cameras?status=available")
    assert r.status_code == 200
    for cam in r.json():
        assert cam["status"] == "available"


@pytest.mark.asyncio
async def test_camera_get_includes_recent_events_key():
    """GET /bwc/cameras/{id} response includes recent_events list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_camera(c, serial="BWC-EVT-001")
        r = await c.get(f"/api/v1/bwc/cameras/{cid}")
    assert r.status_code == 200
    assert "recent_events" in r.json()
    assert isinstance(r.json()["recent_events"], list)


# ─── D. Assign validation ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assign_missing_user_id_returns_400():
    """POST /cameras/{id}/assign without user_id returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_camera(c, serial="BWC-NUID-001")
        r = await c.post(f"/api/v1/bwc/cameras/{cid}/assign", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_assign_unknown_camera_returns_404():
    """POST /cameras/{id}/assign for a non-existent camera returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(
            f"/api/v1/bwc/cameras/{uuid.uuid4()}/assign",
            json={"user_id": str(uuid.uuid4())},
        )
    assert r.status_code == 404


# ─── E. Recording lifecycle validation ───────────────────────────────────────

@pytest.mark.asyncio
async def test_recording_start_bad_camera_status_returns_422():
    """POST /cameras/{id}/recordings/start when camera is low_battery (not assignable) returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_camera(c, serial="BWC-BATT-001")
        # Push telemetry with battery=10 → status becomes 'low_battery'
        await c.put(f"/api/v1/bwc/cameras/{cid}/telemetry", json={"battery_pct": 10})
        # Now try to start a recording — 'low_battery' not in (assigned, available)
        r = await c.post(
            f"/api/v1/bwc/cameras/{cid}/recordings/start",
            json={"trigger_type": "manual"},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_recording_stop_not_found_returns_404():
    """POST /cameras/{id}/recordings/{rec_id}/stop for unknown recording returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_camera(c, serial="BWC-STOP-001")
        r = await c.post(
            f"/api/v1/bwc/cameras/{cid}/recordings/{uuid.uuid4()}/stop",
            json={},
        )
    assert r.status_code == 404


# ─── F. Recording list camera_id filter ──────────────────────────────────────

@pytest.mark.asyncio
async def test_recordings_list_camera_id_filter():
    """GET /bwc/recordings?camera_id=X returns only recordings for that camera."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_camera(c, serial="BWC-REC-FILT-001")
        # Start + immediately stop a recording so it appears in list
        start_r = await c.post(
            f"/api/v1/bwc/cameras/{cid}/recordings/start",
            json={"trigger_type": "manual", "title": "Filter Test"},
        )
        rec_id = start_r.json()["id"]
        await c.post(
            f"/api/v1/bwc/cameras/{cid}/recordings/{rec_id}/stop",
            json={"duration_seconds": 5},
        )
        r = await c.get(f"/api/v1/bwc/recordings?camera_id={cid}")
    assert r.status_code == 200
    for rec in r.json()["items"]:
        assert rec["camera_id"] == cid


# ─── G. Link-incident validation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_link_incident_recording_not_found_returns_404():
    """PUT /bwc/recordings/{id}/link-incident for unknown recording returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put(
            f"/api/v1/bwc/recordings/{uuid.uuid4()}/link-incident",
            json={"incident_id": str(uuid.uuid4())},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_link_incident_incident_not_found_returns_404():
    """PUT /bwc/recordings/{id}/link-incident with unknown incident_id returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_camera(c, serial="BWC-LINK-001")
        start_r = await c.post(
            f"/api/v1/bwc/cameras/{cid}/recordings/start",
            json={"trigger_type": "manual"},
        )
        rec_id = start_r.json()["id"]
        # Link to a non-existent incident
        r = await c.put(
            f"/api/v1/bwc/recordings/{rec_id}/link-incident",
            json={"incident_id": str(uuid.uuid4())},
        )
    assert r.status_code == 404


# ─── H. Events event_type filter ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_events_event_type_filter():
    """GET /bwc/events?event_type=assigned returns only assigned events."""
    _, _, token = await _seed_tenant_and_token()
    _, user_id_a, _ = await _seed_tenant_and_token()  # extra user for assign
    async with await _authed(token) as c:
        cid = await _create_camera(c, serial="BWC-EVT-FILT-001")
        # Assign camera to generate an 'assigned' event
        await c.post(f"/api/v1/bwc/cameras/{cid}/assign", json={"user_id": str(user_id_a)})
        r = await c.get("/api/v1/bwc/events?event_type=assigned")
    assert r.status_code == 200
    for ev in r.json():
        assert ev["event_type"] == "assigned"


# ─── I. Dashboard all 8 keys ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_returns_all_keys():
    """GET /bwc/dashboard returns all 8 expected summary keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/bwc/dashboard")
    assert r.status_code == 200
    data = r.json()
    for key in (
        "total_cameras", "available", "assigned", "recording",
        "low_battery", "storage_warning", "active_recordings", "recordings_today",
    ):
        assert key in data, f"Key {key!r} missing from BWC dashboard"


# ─── J. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_register_camera():
    """POST /bwc/cameras requires bwc:manage — viewer (role 6) gets 403."""
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.post("/api/v1/bwc/cameras", json={"serial_number": "BWC-VIEWER-001"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_cameras():
    """GET /bwc/cameras without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/bwc/cameras")
    assert r.status_code == 401


# ─── K. RLS — Tenant Isolation ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_cameras_isolated_by_tenant():
    """GET /bwc/cameras only returns cameras belonging to the caller's tenant."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()

    async with await _authed(token_a) as c:
        cid_a = await _create_camera(c, serial="BWC-RLS-A-001")

    async with await _authed(token_b) as c:
        await _create_camera(c, serial="BWC-RLS-B-001")
        r = await c.get("/api/v1/bwc/cameras")

    ids = [cam["id"] for cam in r.json()]
    assert cid_a not in ids, "Tenant B must not see Tenant A's cameras"
