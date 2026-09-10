"""Gap 92 — Camera overlay endpoint (isolated-tenant)

Tests GET /api/v1/cameras/{id}/overlay: returns the camera's active
restricted zones (normalized polygons) + recent detections (pixel bboxes +
frame dims), site-scoped.

Sections:
  A — Zones + detections shape (4 tests)
  B — Permissions + scoping (3 tests)
"""
from __future__ import annotations

import json
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


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 2):
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"ovl-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Overlay Test {slug}", "slug": slug},
        )
        await s.execute(
            text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                 "                   full_name, totp_enabled) "
                 "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'Overlay Tester', CAST(:role AS smallint) = 1)"),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"ovl-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return tenant_id, user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_extra_user(tenant_id: uuid.UUID, role_id: int):
    from app.core.security import create_access_token
    user_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                 "                   totp_enabled) "
                 "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', CAST(:role AS smallint) = 1)"),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"ovl-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_site(tenant_id: uuid.UUID) -> uuid.UUID:
    site_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, 'Ovl Site')"),
                        {"id": site_id, "tid": tenant_id})
        await s.commit()
    await engine.dispose()
    return site_id


async def _seed_camera(tenant_id: uuid.UUID, site_id: uuid.UUID | None = None) -> uuid.UUID:
    camera_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(text("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:id, :tid, :sid, 'Ovl Cam')"),
                        {"id": camera_id, "tid": tenant_id, "sid": site_id})
        await s.commit()
    await engine.dispose()
    return camera_id


async def _seed_zone(tenant_id: uuid.UUID, camera_id: uuid.UUID, name: str = "Server Room"):
    zone_id = uuid.uuid4()
    polygon = [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}, {"x": 0.9, "y": 0.9}, {"x": 0.1, "y": 0.9}]
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO restricted_zones (id, tenant_id, camera_id, name, polygon, severity, is_active) "
                 "VALUES (:id, :tid, :cid, :name, CAST(:poly AS jsonb), 'high', TRUE)"),
            {"id": zone_id, "tid": tenant_id, "cid": camera_id, "name": name, "poly": json.dumps(polygon)},
        )
        await s.commit()
    await engine.dispose()
    return zone_id


async def _seed_detection(tenant_id: uuid.UUID, camera_id: uuid.UUID,
                          frame_dims: tuple[int, int] | None = (1920, 1080),
                          age_seconds: int = 5):
    det_id = uuid.uuid4()
    detected = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    bbox = {"x1": 100, "y1": 200, "x2": 300, "y2": 500}
    meta = {"model_version": "test"}
    if frame_dims:
        meta["frame_width"], meta["frame_height"] = frame_dims
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, "
                 "bounding_box, raw_metadata, detected_at) "
                 "VALUES (:id, :tid, :cid, 'intrusion', 0.91, CAST(:bbox AS jsonb), "
                 "CAST(:meta AS jsonb), :dt)"),
            {"id": det_id, "tid": tenant_id, "cid": camera_id,
             "bbox": json.dumps(bbox), "meta": json.dumps(meta), "dt": detected},
        )
        await s.commit()
    await engine.dispose()
    return det_id


async def _authed(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


# ─── A. Zones + detections shape ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ovl_empty_camera():
    """A camera with no zones/detections returns empty lists."""
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/cameras/{camera_id}/overlay")
    assert r.status_code == 200
    body = r.json()
    assert body["zones"] == []
    assert body["detections"] == []


@pytest.mark.asyncio
async def test_ovl_returns_zone_polygon():
    """An active restricted zone appears with its normalized polygon."""
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id = await _seed_camera(tenant_id)
    await _seed_zone(tenant_id, camera_id)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/cameras/{camera_id}/overlay")
    zones = r.json()["zones"]
    assert len(zones) == 1
    assert zones[0]["name"] == "Server Room"
    assert zones[0]["severity"] == "high"
    assert zones[0]["polygon"][0] == {"x": 0.1, "y": 0.1}


@pytest.mark.asyncio
async def test_ovl_returns_recent_detection_with_frame_dims():
    """A recent detection appears with bbox, module, confidence, frame dims."""
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id = await _seed_camera(tenant_id)
    await _seed_detection(tenant_id, camera_id, frame_dims=(1920, 1080), age_seconds=3)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/cameras/{camera_id}/overlay?seconds=15")
    dets = r.json()["detections"]
    assert len(dets) == 1
    d = dets[0]
    assert d["module_type"] == "intrusion"
    assert d["bounding_box"] == {"x1": 100, "y1": 200, "x2": 300, "y2": 500}
    assert d["frame_width"] == 1920 and d["frame_height"] == 1080


@pytest.mark.asyncio
async def test_ovl_excludes_old_detections():
    """A detection older than the window is excluded; frame dims may be null."""
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id = await _seed_camera(tenant_id)
    await _seed_detection(tenant_id, camera_id, frame_dims=None, age_seconds=300)  # 5 min old
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/cameras/{camera_id}/overlay?seconds=15")
    assert r.json()["detections"] == []


# ─── B. Permissions + scoping ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ovl_unknown_camera_404():
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/cameras/{uuid.uuid4()}/overlay")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ovl_site_scoped_404():
    """Guard assigned to Site A gets 404 for a Site B camera's overlay."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    guard_id, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    site_a = await _seed_site(tenant_id)
    site_b = await _seed_site(tenant_id)
    cam_b = await _seed_camera(tenant_id, site_b)
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(text("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:u, :s, :t)"),
                        {"u": guard_id, "s": site_a, "t": tenant_id})
        await s.commit()
    await engine.dispose()
    async with await _authed(guard_token) as c:
        r = await c.get(f"/api/v1/cameras/{cam_b}/overlay")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ovl_rls_isolation():
    """Tenant B cannot read Tenant A's camera overlay."""
    tenant_a, _, _ = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    cam_a = await _seed_camera(tenant_a)
    async with await _authed(tok_b) as c:
        r = await c.get(f"/api/v1/cameras/{cam_a}/overlay")
    assert r.status_code == 404
