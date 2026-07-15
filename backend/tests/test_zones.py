"""Gap 43 — Restricted Zones Router

Covers the untested endpoints in backend/app/routers/zones.py
(PUT /zones/{id}/schedule is already covered by test_p5_zone_schedule.py):

  GET    /api/v1/zones              — list zones with is_currently_active field
  POST   /api/v1/zones              — create restricted zone (requires camera_id)
  DELETE /api/v1/zones/{id}         — soft-deactivate (is_active → FALSE)
  POST   /api/v1/zones/bulk-bypass  — suppress detection for up to 1440 minutes
  POST   /api/v1/zones/bulk-restore — clear bypass_until immediately

Sections:
  A — DB schema: restricted_zones columns present
  B — GET /zones: empty list; populated list with expected fields
  C — POST /zones: 201 + id; default severity; invalid severity; missing polygon
  D — DELETE /zones/{id}: response shape; appears in list as inactive
  E — POST /zones/bulk-bypass: count+zones response; empty ids; bad minutes; inactive skipped
  F — POST /zones/bulk-restore: restores bypass; empty ids; non-bypassed skipped
  G — is_currently_active: False when bypassed, True for always-on active zone
  H — Permissions: zone:manage required; viewer=403, unauth=401
  I — RLS: tenant A cannot see tenant B's zones
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
    slug = f"zon-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Zone Test {slug}", "slug": slug},
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
                "email": f"zon-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera(tenant_id: uuid.UUID) -> uuid.UUID:
    """Insert a minimal camera row; return camera_id."""
    camera_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO cameras (id, tenant_id, name, is_active) "
                "VALUES (:id, :tid, :name, TRUE)"
            ),
            {"id": camera_id, "tid": tenant_id, "name": f"cam-{camera_id.hex[:8]}"},
        )
        await s.commit()
    await engine.dispose()
    return camera_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


_POLYGON = [
    {"x": 0.1, "y": 0.1},
    {"x": 0.9, "y": 0.1},
    {"x": 0.9, "y": 0.9},
    {"x": 0.1, "y": 0.9},
]


async def _create_zone(client: AsyncClient, camera_id: uuid.UUID | str, **kwargs) -> dict:
    body = {
        "camera_id": str(camera_id),
        "name": kwargs.get("name", "Test Zone"),
        "polygon": kwargs.get("polygon", _POLYGON),
        "severity": kwargs.get("severity", "medium"),
    }
    r = await client.post("/api/v1/zones", json=body)
    assert r.status_code == 201, f"zone create failed: {r.text}"
    return r.json()


# ─── A. DB Schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restricted_zones_expected_columns():
    """restricted_zones table has all columns needed for zone management."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'restricted_zones'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "camera_id", "name", "polygon", "severity",
        "is_active", "bypass_until", "schedule_enabled",
        "active_days", "active_start_time", "active_end_time",
    ):
        assert col in cols, f"Column {col!r} missing from restricted_zones"


# ─── B. GET /zones ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_zones_empty_initially():
    """GET /zones returns [] for a fresh tenant with no zones."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/zones")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_zones_shows_created_zone_with_fields():
    """GET /zones returns created zone with expected response fields."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        zone = await _create_zone(c, cam_id, name="Lobby Zone", severity="high")
        r = await c.get("/api/v1/zones")
    assert r.status_code == 200
    items = r.json()
    match = next((z for z in items if str(z["id"]) == str(zone["id"])), None)
    assert match is not None, "Created zone not found in list"
    assert match["name"] == "Lobby Zone"
    assert match["severity"] == "high"
    assert match["is_active"] is True
    assert "is_currently_active" in match
    assert "camera_id" in match
    assert "polygon" in match


# ─── C. POST /zones ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_zone_returns_201_with_id():
    """POST /zones returns 201 with an id field."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/zones",
            json={"camera_id": str(cam_id), "name": "Gate Zone", "polygon": _POLYGON},
        )
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_create_zone_defaults_severity_to_medium():
    """POST /zones without severity stores 'medium' in the DB."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        zone = await _create_zone(c, cam_id, name="Default Severity Zone")
        items = (await c.get("/api/v1/zones")).json()
    match = next((z for z in items if str(z["id"]) == str(zone["id"])), None)
    assert match is not None
    assert match["severity"] == "medium"


@pytest.mark.asyncio
async def test_create_zone_invalid_severity_returns_422():
    """POST /zones with an unknown severity value returns 422."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/zones",
            json={
                "camera_id": str(cam_id),
                "name": "Bad Zone",
                "polygon": _POLYGON,
                "severity": "extreme",
            },
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_zone_missing_polygon_returns_422():
    """POST /zones without polygon returns 422."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/zones",
            json={"camera_id": str(cam_id), "name": "No Poly Zone"},
        )
    assert r.status_code == 422


# ─── D. DELETE /zones/{id} ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deactivate_zone_returns_id_and_is_active_false():
    """DELETE /zones/{id} returns {id, is_active: false}."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        zone = await _create_zone(c, cam_id)
        r = await c.delete(f"/api/v1/zones/{zone['id']}")
    assert r.status_code == 200
    data = r.json()
    assert str(data["id"]) == str(zone["id"])
    assert data["is_active"] is False


@pytest.mark.asyncio
async def test_deactivated_zone_shows_in_list_as_inactive():
    """After DELETE, zone appears in GET /zones with is_active=False and is_currently_active=False."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        zone = await _create_zone(c, cam_id)
        await c.delete(f"/api/v1/zones/{zone['id']}")
        items = (await c.get("/api/v1/zones")).json()
    match = next((z for z in items if str(z["id"]) == str(zone["id"])), None)
    assert match is not None, "Deactivated zone must still appear in list"
    assert match["is_active"] is False
    assert match["is_currently_active"] is False


# ─── E. POST /zones/bulk-bypass ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_bypass_returns_bypassed_count_and_zones():
    """POST /zones/bulk-bypass sets bypass_until and returns correct response shape."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        z1 = await _create_zone(c, cam_id, name="Zone Alpha")
        z2 = await _create_zone(c, cam_id, name="Zone Beta")
        r = await c.post(
            "/api/v1/zones/bulk-bypass",
            json={"ids": [str(z1["id"]), str(z2["id"])], "bypass_minutes": 30},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["bypassed"] == 2
    assert data["skipped"] == 0
    assert data["bypass_minutes"] == 30
    assert len(data["zones"]) == 2
    for entry in data["zones"]:
        assert "id" in entry
        assert "bypass_until" in entry


@pytest.mark.asyncio
async def test_bulk_bypass_empty_ids_returns_422():
    """POST /zones/bulk-bypass with empty ids returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/zones/bulk-bypass", json={"ids": [], "bypass_minutes": 30})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_bypass_minutes_zero_returns_422():
    """POST /zones/bulk-bypass with bypass_minutes=0 (below minimum) returns 422."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        zone = await _create_zone(c, cam_id)
        r = await c.post(
            "/api/v1/zones/bulk-bypass",
            json={"ids": [str(zone["id"])], "bypass_minutes": 0},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_bypass_minutes_over_max_returns_422():
    """POST /zones/bulk-bypass with bypass_minutes=1441 (above maximum) returns 422."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        zone = await _create_zone(c, cam_id)
        r = await c.post(
            "/api/v1/zones/bulk-bypass",
            json={"ids": [str(zone["id"])], "bypass_minutes": 1441},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_bypass_inactive_zone_is_skipped():
    """POST /zones/bulk-bypass skips deactivated zones (is_active=FALSE)."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        zone = await _create_zone(c, cam_id)
        await c.delete(f"/api/v1/zones/{zone['id']}")
        r = await c.post(
            "/api/v1/zones/bulk-bypass",
            json={"ids": [str(zone["id"])], "bypass_minutes": 60},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["bypassed"] == 0
    assert data["skipped"] == 1


# ─── F. POST /zones/bulk-restore ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_restore_clears_bypass_returns_restored():
    """POST /zones/bulk-restore clears bypass_until on a previously bypassed zone."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        zone = await _create_zone(c, cam_id)
        await c.post(
            "/api/v1/zones/bulk-bypass",
            json={"ids": [str(zone["id"])], "bypass_minutes": 60},
        )
        r = await c.post(
            "/api/v1/zones/bulk-restore",
            json={"ids": [str(zone["id"])]},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["restored"] == 1
    assert data["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_restore_empty_ids_returns_422():
    """POST /zones/bulk-restore with empty ids returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/zones/bulk-restore", json={"ids": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_restore_non_bypassed_zone_returns_skipped():
    """POST /zones/bulk-restore on a zone with no bypass set returns skipped=1."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        zone = await _create_zone(c, cam_id)
        r = await c.post(
            "/api/v1/zones/bulk-restore",
            json={"ids": [str(zone["id"])]},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["restored"] == 0
    assert data["skipped"] == 1


# ─── G. is_currently_active ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_is_currently_active_false_when_bypassed():
    """GET /zones shows is_currently_active=False for a bypassed zone."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        zone = await _create_zone(c, cam_id)
        await c.post(
            "/api/v1/zones/bulk-bypass",
            json={"ids": [str(zone["id"])], "bypass_minutes": 60},
        )
        items = (await c.get("/api/v1/zones")).json()
    match = next((z for z in items if str(z["id"]) == str(zone["id"])), None)
    assert match is not None
    assert match["is_currently_active"] is False


@pytest.mark.asyncio
async def test_is_currently_active_true_for_always_on_active_zone():
    """GET /zones shows is_currently_active=True for an active zone with schedule_enabled=False."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        zone = await _create_zone(c, cam_id)
        items = (await c.get("/api/v1/zones")).json()
    match = next((z for z in items if str(z["id"]) == str(zone["id"])), None)
    assert match is not None
    assert match["is_currently_active"] is True


# ─── H. Permissions ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_list_zones():
    """GET /zones requires zone:manage — viewer (role 6) gets 403."""
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.get("/api/v1/zones")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_zones():
    """GET /zones without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/zones")
    assert r.status_code == 401


# ─── I. RLS — Tenant Isolation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_zones_isolated_by_tenant():
    """GET /zones only returns zones belonging to the caller's tenant."""
    tid_a, _, token_a = await _seed_tenant_and_token()
    tid_b, _, token_b = await _seed_tenant_and_token()

    cam_a = await _seed_camera(tid_a)
    cam_b = await _seed_camera(tid_b)

    async with await _authed(token_a) as c:
        zone_a = await _create_zone(c, cam_a, name="Tenant A Zone")

    async with await _authed(token_b) as c:
        await _create_zone(c, cam_b, name="Tenant B Zone")
        items = (await c.get("/api/v1/zones")).json()

    ids = [str(z["id"]) for z in items]
    assert str(zone_a["id"]) not in ids, "Tenant B must not see Tenant A's zones"
