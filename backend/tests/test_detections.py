"""Gap 67 — Detections Router

Isolated-tenant tests for backend/app/routers/detections.py (201 lines).
All endpoints require detection:read (all roles including viewer).

Endpoints (prefix /api/v1/detections):
  GET /                   — paginated; ?module_type=lpr
  GET /lpr-events         — paginated; ?plate_number=&watchlist_match=
  GET /face-events        — paginated; ?watchlist_match=
  GET /intrusion-events   — paginated; ?zone_id=
  GET /ppe-events         — list (no pagination); ?limit=
  GET /crowd-events       — list; ?zone_id=
  GET /fire-smoke-events  — list; ?detection_type=
  GET /weapon-events      — list; ?weapon_type=
  GET /behavior-events    — list; ?behavior_type=

Sections:
  A — Detections list + module_type filter (4 tests)
  B — LPR Events (3 tests)
  C — Phase 3 event lists (5 tests)
  D — Permissions + RLS (2 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

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


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 2):
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"det-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Det Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'Det Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"det-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera(tenant_id: uuid.UUID) -> uuid.UUID:
    cam_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO cameras (id, tenant_id, name, location) VALUES (:id, :tid, :n, :l)"),
            {"id": cam_id, "tid": tenant_id, "n": "Det Cam", "l": "Gate"},
        )
        await s.commit()
    await engine.dispose()
    return cam_id


async def _seed_detection(tenant_id: uuid.UUID, camera_id: uuid.UUID,
                          module_type: str = "lpr") -> tuple[uuid.UUID, datetime]:
    """Insert a detection row (partitioned table). Returns (detection_id, detected_at)."""
    det_id = uuid.uuid4()
    detected_at = datetime.now(timezone.utc)
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, detected_at) "
                "VALUES (:id, :tid, :cid, :mod, 0.90, :dat)"
            ),
            {"id": det_id, "tid": tenant_id, "cid": camera_id,
             "mod": module_type, "dat": detected_at},
        )
        await s.commit()
    await engine.dispose()
    return det_id, detected_at


async def _seed_lpr_event(tenant_id: uuid.UUID, camera_id: uuid.UUID,
                          plate: str = "SGB1234X") -> uuid.UUID:
    """Insert detection + lpr_event rows. Returns detection_id."""
    det_id, detected_at = await _seed_detection(tenant_id, camera_id, "lpr")
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO lpr_events "
                "(detection_id, detected_at, tenant_id, camera_id, plate_number, plate_confidence, watchlist_match) "
                "VALUES (:did, :dat, :tid, :cid, :plate, 0.90, 'block')"
            ),
            {"did": det_id, "dat": detected_at, "tid": tenant_id,
             "cid": camera_id, "plate": plate},
        )
        await s.commit()
    await engine.dispose()
    return det_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


_PAGINATED_KEYS = {"items", "total", "limit", "offset", "has_more"}


# ─── A. Detections list ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_det_list_detections_empty_fresh_tenant():
    """GET /detections on fresh tenant returns 200 + pagination struct with 0 items."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections")
    assert r.status_code == 200
    body = r.json()
    assert _PAGINATED_KEYS <= set(body.keys())
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_det_list_detections_module_type_filter_accepted():
    """GET /detections?module_type=lpr returns 200 — filter param accepted."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections?module_type=lpr")
    assert r.status_code == 200
    assert _PAGINATED_KEYS <= set(r.json().keys())


@pytest.mark.asyncio
async def test_det_list_detections_shows_seeded_row():
    """After seeding a detection, it appears in GET /detections."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    det_id, _ = await _seed_detection(tenant_id, cam_id, "lpr")
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections")
    body = r.json()
    assert body["total"] >= 1
    ids = [item["id"] for item in body["items"]]
    assert str(det_id) in ids


@pytest.mark.asyncio
async def test_det_list_detections_module_type_filter_isolates():
    """Seeding lpr detection then filtering by module_type=face → 0 items."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    await _seed_detection(tenant_id, cam_id, "lpr")
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections?module_type=face")
    assert r.json()["total"] == 0


# ─── B. LPR Events ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_det_lpr_events_empty_fresh_tenant():
    """GET /detections/lpr-events on fresh tenant returns pagination with 0 items."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections/lpr-events")
    assert r.status_code == 200
    assert _PAGINATED_KEYS <= set(r.json().keys())
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_det_lpr_events_plate_filter_accepted():
    """GET /detections/lpr-events?plate_number=SGH returns 200 (filter accepted)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections/lpr-events?plate_number=SGH")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_det_lpr_events_shows_seeded_row():
    """Seeded lpr_event appears in GET /detections/lpr-events."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    det_id = await _seed_lpr_event(tenant_id, cam_id, plate="SGA9999Z")
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections/lpr-events")
    body = r.json()
    assert body["total"] >= 1
    ids = [str(item["detection_id"]) for item in body["items"]]
    assert str(det_id) in ids


# ─── C. Phase 3 event lists ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_det_face_events_empty_returns_pagination():
    """GET /detections/face-events on fresh tenant returns 200 + pagination."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections/face-events")
    assert r.status_code == 200
    assert _PAGINATED_KEYS <= set(r.json().keys())
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_det_intrusion_events_empty():
    """GET /detections/intrusion-events on fresh tenant returns 200 + pagination."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections/intrusion-events")
    assert r.status_code == 200
    assert _PAGINATED_KEYS <= set(r.json().keys())


@pytest.mark.asyncio
async def test_det_ppe_events_returns_list():
    """GET /detections/ppe-events on fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections/ppe-events")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_det_fire_smoke_events_with_filter():
    """GET /detections/fire-smoke-events?detection_type=fire returns 200."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections/fire-smoke-events?detection_type=fire")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_det_weapon_events_returns_list():
    """GET /detections/weapon-events on fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections/weapon-events")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ─── D. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_det_viewer_can_list_detections():
    """Viewer (role 6) has detection:read → GET /detections returns 200."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/detections")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_det_rls_isolation():
    """Tenant B cannot see Tenant A's detection rows via GET /detections."""
    tenant_a, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_a)
    await _seed_detection(tenant_a, cam_id, "lpr")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/detections")
    assert r.json()["total"] == 0
