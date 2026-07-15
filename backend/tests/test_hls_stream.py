"""Gap 90 — HLS live streaming (isolated-tenant)

Tests the path-safety functions in app/services/hls_stream.py and the auth /
validation behaviour of the HLS endpoints. The ffmpeg subprocess and RTSP
capture are NOT exercised (no real camera in CI) — exactly like the MJPEG
`live` and recording `play` endpoints, we test auth + guards, not media.

Sections:
  A — is_safe_segment_name (pure) (4 tests)
  B — segment_file_path resolution (3 tests)
  C — Playlist endpoint auth (3 tests)
  D — Segment endpoint validation (3 tests)
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

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

SAFE_RTSP = "rtsp://203.0.113.1:554/live/cam0"


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 2):
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"hls-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"HLS Test {slug}", "slug": slug},
        )
        await s.execute(
            text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                 "VALUES (:id, :tid, :role, :email, 'hashed', 'HLS Tester')"),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"hls-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera_and_stream(tenant_id: uuid.UUID, site_id: uuid.UUID | None = None):
    camera_id = uuid.uuid4()
    stream_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:id, :tid, :sid, 'HLS Cam')"),
            {"id": camera_id, "tid": tenant_id, "sid": site_id},
        )
        await s.execute(
            text("INSERT INTO streams (id, tenant_id, camera_id, url) VALUES (:id, :tid, :cid, :url)"),
            {"id": stream_id, "tid": tenant_id, "cid": camera_id, "url": SAFE_RTSP},
        )
        await s.commit()
    await engine.dispose()
    return camera_id, stream_id


async def _seed_site(tenant_id: uuid.UUID) -> uuid.UUID:
    site_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, 'HLS Site')"),
            {"id": site_id, "tid": tenant_id},
        )
        await s.commit()
    await engine.dispose()
    return site_id


async def _client(token: str | None) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    if token:
        c.headers.update({"Authorization": f"Bearer {token}"})
    return c


# ─── A. is_safe_segment_name ─────────────────────────────────────────────────

def test_hls_safe_name_accepts_real_segments():
    from app.services.hls_stream import is_safe_segment_name
    for name in ("seg_00001.ts", "index.m3u8", "seg_99999.ts", "init.m4s"):
        assert is_safe_segment_name(name) is True, name


def test_hls_safe_name_rejects_traversal():
    from app.services.hls_stream import is_safe_segment_name
    for name in ("..", "../seg.ts", "a/b.ts", "a\\b.ts", "seg..ts", ".hidden.ts"):
        assert is_safe_segment_name(name) is False, name


def test_hls_safe_name_rejects_wrong_extension():
    from app.services.hls_stream import is_safe_segment_name
    for name in ("evil.txt", "passwd", "seg.exe", "config.yml"):
        assert is_safe_segment_name(name) is False, name


def test_hls_safe_name_rejects_overlong():
    from app.services.hls_stream import is_safe_segment_name
    assert is_safe_segment_name("a" * 100 + ".ts") is False


# ─── B. segment_file_path ─────────────────────────────────────────────────────

def test_hls_segment_path_inside_dir():
    from app.services.hls_stream import HLS_ROOT, segment_file_path
    path = segment_file_path("streamA", "seg_00001.ts")
    assert path is not None
    base = os.path.realpath(os.path.join(HLS_ROOT, "streamA"))
    assert path.startswith(base + os.sep)
    assert path.endswith("seg_00001.ts")


def test_hls_segment_path_rejects_traversal_name():
    from app.services.hls_stream import segment_file_path
    assert segment_file_path("streamA", "../evil.ts") is None
    assert segment_file_path("streamA", "..") is None


def test_hls_segment_path_rejects_wrong_extension():
    from app.services.hls_stream import segment_file_path
    assert segment_file_path("streamA", "secret.txt") is None


# ─── C. Playlist endpoint auth ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hls_playlist_invalid_token_401():
    tenant_id, _, _ = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id)
    async with await _client(None) as c:
        r = await c.get(f"/api/v1/cameras/{camera_id}/streams/{stream_id}/hls/index.m3u8?token=garbage")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_hls_playlist_missing_permission_403():
    from app.core.security import create_access_token
    tenant_id, _, _ = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id)
    # Fabricate a token for a role that has no permissions at all
    bad = create_access_token(str(uuid.uuid4()), str(tenant_id), 99)
    async with await _client(None) as c:
        r = await c.get(f"/api/v1/cameras/{camera_id}/streams/{stream_id}/hls/index.m3u8?token={bad}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_hls_playlist_unknown_stream_404():
    tenant_id, _, token = await _seed_tenant_and_token()
    async with await _client(None) as c:
        r = await c.get(
            f"/api/v1/cameras/{uuid.uuid4()}/streams/{uuid.uuid4()}/hls/index.m3u8?token={token}")
    assert r.status_code == 404


# ─── D. Segment endpoint validation ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_hls_segment_bad_name_422():
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id)
    async with await _client(None) as c:
        r = await c.get(
            f"/api/v1/cameras/{camera_id}/streams/{stream_id}/hls/evil.exe?token={token}")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_hls_segment_missing_file_404():
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id)
    async with await _client(None) as c:
        r = await c.get(
            f"/api/v1/cameras/{camera_id}/streams/{stream_id}/hls/seg_00001.ts?token={token}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_hls_segment_site_scoped_client_404():
    """Client (role 7) with no site assignment is denied even a segment
    request for a real stream (Gap 81 fail-closed)."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    from app.core.security import create_access_token
    site_id = await _seed_site(tenant_id)
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id, site_id=site_id)
    client_user = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                 "VALUES (:id, :tid, 7, :email, 'hashed')"),
            {"id": client_user, "tid": tenant_id, "email": f"hls-{client_user.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    client_token = create_access_token(str(client_user), str(tenant_id), 7)
    async with await _client(None) as c:
        r = await c.get(
            f"/api/v1/cameras/{camera_id}/streams/{stream_id}/hls/seg_00001.ts?token={client_token}")
    assert r.status_code == 404
