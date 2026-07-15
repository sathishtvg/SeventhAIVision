"""Gap 36 — Stream Management CRUD + Camera Health tests.

Sections:
  A (3)  — DB schema: streams, recordings, camera_health_events tables
  B (8)  — Stream CRUD: list, create, update, delete + validation
  C (4)  — Stream permissions: auth required, role gates
  D (4)  — Camera health events: empty list, seeded events, auth
  E (4)  — Recording extras: global list, download 404/409, status filter
  F (3)  — Global stream list + RLS isolation
  G (2)  — SSRF protection: private IP blocked, public IP accepted
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

# TEST-NET-3 (RFC 5737) — guaranteed unreachable but NOT in any private/blocked range
SAFE_RTSP = "rtsp://203.0.113.1:554/live/cam0"
SAFE_RTSP_2 = "rtsp://203.0.113.2:554/live/cam0"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


async def _seed_tenant_and_token(role_id: int = 2):
    """Create isolated tenant + user; return (tenant_id, user_id, jwt_token)."""
    from app.core.security import create_access_token, hash_password

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"st-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"ST Tenant {slug}", "slug": slug},
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


async def _seed_camera(tenant_id: uuid.UUID, name: str = "TestCam") -> uuid.UUID:
    """Insert a bare camera row; return its id."""
    camera_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, :name)"),
            {"id": camera_id, "tid": tenant_id, "name": name},
        )
        await s.commit()
    await engine.dispose()
    return camera_id


def _app():
    from app.main import app
    return app


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Section A — DB schema
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_streams_table_exists():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        row = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='streams' ORDER BY column_name"
        ))
        cols = {r[0] for r in row.fetchall()}
    await engine.dispose()
    for col in ("id", "tenant_id", "camera_id", "protocol", "url",
                "auth_config", "status", "last_frame_at", "created_at", "updated_at"):
        assert col in cols, f"Missing column in streams: {col}"


@pytest.mark.asyncio
async def test_recordings_table_exists():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        row = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='recordings' ORDER BY column_name"
        ))
        cols = {r[0] for r in row.fetchall()}
    await engine.dispose()
    for col in ("id", "tenant_id", "camera_id", "stream_id", "status",
                "started_at", "ended_at", "file_path", "file_size_bytes",
                "duration_seconds", "created_at"):
        assert col in cols, f"Missing column in recordings: {col}"


@pytest.mark.asyncio
async def test_camera_health_events_table_exists():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        row = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='camera_health_events' ORDER BY column_name"
        ))
        cols = {r[0] for r in row.fetchall()}
    await engine.dispose()
    for col in ("id", "tenant_id", "camera_id", "event_type", "detail", "occurred_at"):
        assert col in cols, f"Missing column in camera_health_events: {col}"


# ─────────────────────────────────────────────────────────────────────────────
# Section B — Stream CRUD
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_streams_empty():
    """A new camera has no streams."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/cameras/{cam_id}/streams")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_stream_returns_201():
    """Creating a stream with a valid RTSP URL returns 201 with id."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "rtsp",
        })
    assert r.status_code == 201, r.text
    body = r.json()
    assert "id" in body
    assert body["url"] == SAFE_RTSP
    assert body["status"] == "offline"


@pytest.mark.asyncio
async def test_create_stream_appears_in_list():
    """A created stream appears in GET /{camera_id}/streams."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        post_r = await c.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "rtsp",
        })
        assert post_r.status_code == 201
        stream_id = post_r.json()["id"]

        list_r = await c.get(f"/api/v1/cameras/{cam_id}/streams")
    assert list_r.status_code == 200
    ids = [s["id"] for s in list_r.json()]
    assert stream_id in ids


@pytest.mark.asyncio
async def test_create_stream_invalid_protocol():
    """An unsupported protocol returns 422."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "ftp",
        })
    assert r.status_code == 422, f"Invalid protocol should return 422, got {r.status_code}"


@pytest.mark.asyncio
async def test_create_stream_camera_not_found():
    """Posting to an unknown camera_id returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/cameras/{uuid.uuid4()}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "rtsp",
        })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_stream_url():
    """PUT on a stream updates its URL and returns the updated row."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        post_r = await c.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "rtsp",
        })
        stream_id = post_r.json()["id"]

        put_r = await c.put(f"/api/v1/cameras/{cam_id}/streams/{stream_id}", json={
            "url": SAFE_RTSP_2,
        })
    assert put_r.status_code == 200, put_r.text
    assert put_r.json()["url"] == SAFE_RTSP_2


@pytest.mark.asyncio
async def test_update_stream_no_fields_422():
    """PUT with no fields returns 422."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        post_r = await c.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "rtsp",
        })
        stream_id = post_r.json()["id"]

        put_r = await c.put(f"/api/v1/cameras/{cam_id}/streams/{stream_id}", json={})
    assert put_r.status_code == 422


@pytest.mark.asyncio
async def test_delete_stream():
    """DELETE returns 204 and the stream disappears from the list."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        post_r = await c.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "rtsp",
        })
        stream_id = post_r.json()["id"]

        del_r = await c.delete(f"/api/v1/cameras/{cam_id}/streams/{stream_id}")
        assert del_r.status_code == 204

        list_r = await c.get(f"/api/v1/cameras/{cam_id}/streams")
    assert stream_id not in [s["id"] for s in list_r.json()]


# ─────────────────────────────────────────────────────────────────────────────
# Section C — Stream permissions
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_streams_requires_auth():
    """Unauthenticated GET /{camera_id}/streams returns 401 or 403."""
    async with AsyncClient(
        transport=ASGITransport(_app()), base_url="http://test"
    ) as c:
        r = await c.get(f"/api/v1/cameras/{uuid.uuid4()}/streams")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_create_stream_requires_camera_create():
    """Viewer (role 6) cannot create a stream (no camera:create)."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=6)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "rtsp",
        })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_update_stream_requires_camera_update():
    """Security guard (role 5) cannot update a stream."""
    tenant_id, _, token_admin = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)

    # Admin creates the stream
    async with await _authed(token_admin) as c:
        post_r = await c.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "rtsp",
        })
    stream_id = post_r.json()["id"]

    # Guard tries to update it — needs a token for the same tenant
    from app.core.security import create_access_token
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    guard_id = uuid.uuid4()
    async with factory() as s:
        from app.core.security import hash_password
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                "VALUES (:id, :tid, 5, :email, :pw)"
            ),
            {"id": guard_id, "tid": tenant_id,
             "email": f"guard-{guard_id.hex[:6]}@test.local", "pw": hash_password("x")},
        )
        await s.commit()
    await engine.dispose()
    guard_token = create_access_token(str(guard_id), str(tenant_id), role_id=5)

    async with await _authed(guard_token) as c:
        r = await c.put(f"/api/v1/cameras/{cam_id}/streams/{stream_id}", json={"url": SAFE_RTSP_2})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_live_stream_requires_valid_token():
    """GET /live without a valid token query param returns 401."""
    tenant_id, _, _ = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with AsyncClient(
        transport=ASGITransport(_app()), base_url="http://test"
    ) as c:
        r = await c.get(
            f"/api/v1/cameras/{cam_id}/streams/{uuid.uuid4()}/live",
            params={"token": "not-a-valid-jwt"},
        )
    assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# Section D — Camera health events
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_camera_health_empty():
    """A new camera has no health events."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/cameras/{cam_id}/health")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_camera_health_returns_seeded_events():
    """Health events seeded in DB appear in GET /{camera_id}/health."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)

    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    event_id = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO camera_health_events "
                "(id, tenant_id, camera_id, event_type, detail) "
                "VALUES (:id, :tid, :cid, 'offline', 'Connection lost')"
            ),
            {"id": event_id, "tid": tenant_id, "cid": cam_id},
        )
        await s.commit()
    await engine.dispose()

    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/cameras/{cam_id}/health")
    assert r.status_code == 200
    events = r.json()
    assert any(e["event_type"] == "offline" for e in events), "Seeded event not found"


@pytest.mark.asyncio
async def test_camera_health_requires_auth():
    """Unauthenticated GET /health returns 401 or 403."""
    async with AsyncClient(
        transport=ASGITransport(_app()), base_url="http://test"
    ) as c:
        r = await c.get(f"/api/v1/cameras/{uuid.uuid4()}/health")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_camera_health_respects_limit():
    """GET /health?limit=2 returns at most 2 events (and does not crash)."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)

    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        for _ in range(5):
            await s.execute(
                text(
                    "INSERT INTO camera_health_events "
                    "(id, tenant_id, camera_id, event_type) "
                    "VALUES (:id, :tid, :cid, 'offline')"
                ),
                {"id": uuid.uuid4(), "tid": tenant_id, "cid": cam_id},
            )
        await s.commit()
    await engine.dispose()

    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/cameras/{cam_id}/health", params={"limit": 2})
    assert r.status_code == 200
    assert len(r.json()) <= 2


# ─────────────────────────────────────────────────────────────────────────────
# Section E — Recording extras
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_global_recordings_list_empty():
    """GET /api/v1/recordings returns empty list for fresh tenant."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/recordings")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_download_recording_not_found():
    """GET /cameras/recordings/{id}/download with unknown ID returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/cameras/recordings/{uuid.uuid4()}/download")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_download_recording_not_completed():
    """Downloading a 'recording' (in-progress) row returns 409."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        # Create stream via API
        stream_r = await c.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "rtsp",
        })
        assert stream_r.status_code == 201, stream_r.text
        stream_id = stream_r.json()["id"]

        # Start recording via API — background task fails silently (RTSP unreachable)
        start_r = await c.post(
            f"/api/v1/cameras/{cam_id}/streams/{stream_id}/recordings/start"
        )
        assert start_r.status_code == 201, start_r.text
        recording_id = start_r.json()["recording_id"]

        # Immediately try to download — status is still 'recording' → 409
        dl_r = await c.get(f"/api/v1/cameras/recordings/{recording_id}/download")
    assert dl_r.status_code == 409, f"In-progress recording should return 409, got {dl_r.status_code}"


@pytest.mark.asyncio
async def test_global_recordings_requires_auth():
    """Unauthenticated GET /recordings returns 401 or 403."""
    async with AsyncClient(
        transport=ASGITransport(_app()), base_url="http://test"
    ) as c:
        r = await c.get("/api/v1/recordings")
    assert r.status_code in (401, 403)


# ─────────────────────────────────────────────────────────────────────────────
# Section F — Global streams list + RLS isolation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_global_streams_list_empty():
    """GET /api/v1/streams returns empty list for fresh tenant."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/streams")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_global_streams_returns_created():
    """A created stream appears in the global streams list."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        post_r = await c.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "rtsp",
        })
        stream_id = post_r.json()["id"]

        list_r = await c.get("/api/v1/streams")
    assert list_r.status_code == 200
    ids = [s["id"] for s in list_r.json()]
    assert stream_id in ids


@pytest.mark.asyncio
async def test_cross_tenant_stream_not_visible():
    """Tenant B cannot see tenant A's streams via the global list."""
    tenant_a, _, token_a = await _seed_tenant_and_token(role_id=2)
    cam_a = await _seed_camera(tenant_a)

    # Create stream in tenant A
    async with await _authed(token_a) as c:
        post_r = await c.post(f"/api/v1/cameras/{cam_a}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "rtsp",
        })
        stream_id_a = post_r.json()["id"]

    # Tenant B sees nothing
    _, _, token_b = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token_b) as c:
        r = await c.get("/api/v1/streams")
    assert stream_id_a not in [s["id"] for s in r.json()]


# ─────────────────────────────────────────────────────────────────────────────
# Section G — SSRF protection
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_stream_private_ip_blocked():
    """Creating a stream with a private-range IP returns 422 (SSRF guard)."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": "rtsp://192.168.1.100:554/stream",  # RFC-1918 private
            "protocol": "rtsp",
        })
    assert r.status_code == 422, (
        f"Private IP should be blocked by SSRF guard, got {r.status_code}"
    )


@pytest.mark.asyncio
async def test_create_stream_public_ip_accepted():
    """Creating a stream with a TEST-NET IP (public, unreachable) returns 201."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/cameras/{cam_id}/streams", json={
            "url": SAFE_RTSP,
            "protocol": "rtsp",
        })
    assert r.status_code == 201, (
        f"Public TEST-NET IP should pass SSRF guard and create stream, got {r.status_code}: {r.text}"
    )
