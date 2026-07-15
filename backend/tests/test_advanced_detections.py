"""Gap 74 — Advanced Detections Router

Isolated-tenant tests for backend/app/routers/advanced_detections.py (124 lines).
Three GET-only endpoints: tampering, abandoned, falls.

Endpoints:
  GET /api/v1/advanced-detections/tampering  — tampering:read; ?camera_id=&tampering_type=
  GET /api/v1/advanced-detections/abandoned  — abandoned:read; ?camera_id=&min_dwell_seconds=
  GET /api/v1/advanced-detections/falls      — fall:read;      ?camera_id=&min_confidence=

All return plain lists. Tables are partitioned (detected_at required in INSERT).

Sections:
  A — Tampering events (3 tests)
  B — Abandoned object events (2 tests)
  C — Fall events (2 tests)
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
    slug = f"adv-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Adv Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Adv Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"adv-{user_id.hex[:8]}@test.local"},
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
            {"id": cam_id, "tid": tenant_id, "n": "Adv Cam", "l": "Gate"},
        )
        await s.commit()
    await engine.dispose()
    return cam_id


async def _seed_detection(tenant_id: uuid.UUID, camera_id: uuid.UUID,
                          module_type: str = "tampering") -> tuple[uuid.UUID, datetime]:
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


async def _seed_tampering_event(tenant_id: uuid.UUID, camera_id: uuid.UUID) -> uuid.UUID:
    det_id, detected_at = await _seed_detection(tenant_id, camera_id, "tampering")
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        # Ensure partition exists for current month
        yr = detected_at.year
        mo = detected_at.month
        next_yr, next_mo = (yr + 1, 1) if mo == 12 else (yr, mo + 1)
        await s.execute(text(
            f"CREATE TABLE IF NOT EXISTS tampering_events_p{yr}_{mo:02d} "
            f"PARTITION OF tampering_events "
            f"FOR VALUES FROM ('{yr}-{mo:02d}-01') TO ('{next_yr}-{next_mo:02d}-01')"
        ))
        await s.commit()
        await s.execute(
            text(
                "INSERT INTO tampering_events "
                "(detection_id, detected_at, tenant_id, camera_id, tampering_type, score) "
                "VALUES (:did, :dat, :tid, :cid, 'blocked', 0.95)"
            ),
            {"did": det_id, "dat": detected_at, "tid": tenant_id, "cid": camera_id},
        )
        await s.commit()
    await engine.dispose()
    return det_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. Tampering events ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adv_tampering_empty_fresh_tenant():
    """GET /advanced-detections/tampering on fresh tenant returns 200 + []."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/advanced-detections/tampering")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert r.json() == []


@pytest.mark.asyncio
async def test_adv_tampering_shows_seeded_row():
    """Seeded tampering_event appears in GET /advanced-detections/tampering."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    det_id = await _seed_tampering_event(tenant_id, cam_id)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/advanced-detections/tampering")
    rows = r.json()
    assert len(rows) >= 1
    ids = [str(row["detection_id"]) for row in rows]
    assert str(det_id) in ids


@pytest.mark.asyncio
async def test_adv_tampering_type_filter_accepted():
    """?tampering_type=coverage_block is accepted (200)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/advanced-detections/tampering?tampering_type=coverage_block")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ─── B. Abandoned object events ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adv_abandoned_empty_fresh_tenant():
    """GET /advanced-detections/abandoned on fresh tenant returns 200 + []."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/advanced-detections/abandoned")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_adv_abandoned_min_dwell_filter_accepted():
    """?min_dwell_seconds=30 filter is accepted (200)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/advanced-detections/abandoned?min_dwell_seconds=30")
    assert r.status_code == 200


# ─── C. Fall events ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adv_falls_empty_fresh_tenant():
    """GET /advanced-detections/falls on fresh tenant returns 200 + []."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/advanced-detections/falls")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_adv_falls_min_confidence_filter_accepted():
    """?min_confidence=0.7 filter is accepted (200)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/advanced-detections/falls?min_confidence=0.7")
    assert r.status_code == 200


# ─── D. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adv_viewer_cannot_read_tampering_403():
    """Viewer (role 6) does not have tampering:read → GET /advanced-detections/tampering returns 403."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/advanced-detections/tampering")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_adv_rls_isolation():
    """Tenant B cannot see Tenant A's tampering events."""
    tenant_a, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_a)
    await _seed_tampering_event(tenant_a, cam_id)
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/advanced-detections/tampering")
    assert r.json() == []
