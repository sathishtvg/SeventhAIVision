"""Gap 85 — Continuous recording + playback timeline (isolated-tenant)

Tests: streams.continuous_recording column + CRUD, supervisor decision
functions (needs_rotation / find_streams_needing_recording /
find_recordings_to_rotate), retention purge, the timeline endpoint, and
inline playback auth. The cv2 capture loop itself is not exercised (no RTSP
source in CI) — the supervisor's DB-level decisions are what's tested.

Sections:
  A — Schema + stream CRUD (3 tests)
  B — Rotation / discovery decision functions (4 tests)
  C — Retention purge (2 tests)
  D — Timeline endpoint (4 tests)
  E — Playback endpoint auth (3 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
APP_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql+asyncpg://svc_app:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

SAFE_RTSP = "rtsp://203.0.113.1:554/live/cam0"  # TEST-NET-3 — unreachable, not SSRF-blocked


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 2):
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"crec-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"CRec Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'CRec Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"crec-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera_and_stream(tenant_id: uuid.UUID, continuous: bool = False):
    camera_id = uuid.uuid4()
    stream_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, 'CRec Cam')"),
            {"id": camera_id, "tid": tenant_id},
        )
        await s.execute(
            text(
                "INSERT INTO streams (id, tenant_id, camera_id, url, continuous_recording) "
                "VALUES (:id, :tid, :cid, :url, :cont)"
            ),
            {"id": stream_id, "tid": tenant_id, "cid": camera_id,
             "url": SAFE_RTSP, "cont": continuous},
        )
        await s.commit()
    await engine.dispose()
    return camera_id, stream_id


async def _seed_recording(tenant_id: uuid.UUID, camera_id: uuid.UUID,
                          stream_id: uuid.UUID, status: str = "completed",
                          started_offset_minutes: int = 60,
                          duration_minutes: int = 15,
                          file_path: str | None = None) -> uuid.UUID:
    rec_id = uuid.uuid4()
    started = datetime.now(timezone.utc) - timedelta(minutes=started_offset_minutes)
    ended = None if status == "recording" else started + timedelta(minutes=duration_minutes)
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO recordings (id, tenant_id, camera_id, stream_id, status, "
                "started_at, ended_at, file_path) "
                "VALUES (:id, :tid, :cid, :sid, :status, :sat, :eat, :fp)"
            ),
            {"id": rec_id, "tid": tenant_id, "cid": camera_id, "sid": stream_id,
             "status": status, "sat": started, "eat": ended,
             "fp": file_path or f"{tenant_id}/{camera_id}/{rec_id}.mp4"},
        )
        await s.commit()
    await engine.dispose()
    return rec_id


async def _app_session(tenant_id: uuid.UUID):
    """Engine+session under svc_app with the tenant GUC set. Caller must
    dispose the engine."""
    engine = create_async_engine(APP_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    return engine, session


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. Schema + stream CRUD ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crec_column_defaults_false():
    """streams.continuous_recording exists and defaults to FALSE."""
    tenant_id, _, _ = await _seed_tenant_and_token()
    _, stream_id = await _seed_camera_and_stream(tenant_id, continuous=False)
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        row = (await s.execute(
            text("SELECT continuous_recording FROM streams WHERE id = :id"),
            {"id": stream_id},
        )).first()
    await engine.dispose()
    assert row is not None and row[0] is False


@pytest.mark.asyncio
async def test_crec_stream_update_toggles_flag():
    """PUT stream with continuous_recording=true persists and echoes the flag."""
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id)
    async with await _authed(token) as c:
        r = await c.put(f"/api/v1/cameras/{camera_id}/streams/{stream_id}",
                        json={"continuous_recording": True})
        assert r.status_code == 200, r.text
        assert r.json()["continuous_recording"] is True
        r_list = await c.get(f"/api/v1/cameras/{camera_id}/streams")
    rows = r_list.json()
    assert rows[0]["continuous_recording"] is True


@pytest.mark.asyncio
async def test_crec_stream_create_accepts_flag():
    """POST stream with continuous_recording=true returns it."""
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id, _ = await _seed_camera_and_stream(tenant_id)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/cameras/{camera_id}/streams", json={
            "url": "rtsp://203.0.113.2:554/live/cam1",
            "continuous_recording": True,
        })
    assert r.status_code == 201, r.text
    assert r.json()["continuous_recording"] is True


# ─── B. Rotation / discovery decision functions ──────────────────────────────

def test_crec_needs_rotation_boundaries():
    """needs_rotation: false before the segment length, true at/after it."""
    from app.services.continuous_recording import needs_rotation

    now = datetime.now(timezone.utc)
    assert needs_rotation(now - timedelta(minutes=14), now, 15) is False
    assert needs_rotation(now - timedelta(minutes=15), now, 15) is True
    assert needs_rotation(now - timedelta(minutes=60), now, 15) is True


@pytest.mark.asyncio
async def test_crec_find_streams_needing_recording():
    """Flagged stream with no active recording is discovered; unflagged isn't."""
    from app.services.continuous_recording import find_streams_needing_recording

    tenant_id, _, _ = await _seed_tenant_and_token()
    _, flagged_stream = await _seed_camera_and_stream(tenant_id, continuous=True)
    await _seed_camera_and_stream(tenant_id, continuous=False)
    engine, session = await _app_session(tenant_id)
    try:
        rows = await find_streams_needing_recording(session)
    finally:
        await session.close()
        await engine.dispose()
    assert [str(r["stream_id"]) for r in rows] == [str(flagged_stream)]


@pytest.mark.asyncio
async def test_crec_stream_with_active_recording_not_rediscovered():
    """A flagged stream that already has an active recording is skipped."""
    from app.services.continuous_recording import find_streams_needing_recording

    tenant_id, _, _ = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id, continuous=True)
    await _seed_recording(tenant_id, camera_id, stream_id, status="recording",
                          started_offset_minutes=5)
    engine, session = await _app_session(tenant_id)
    try:
        rows = await find_streams_needing_recording(session)
    finally:
        await session.close()
        await engine.dispose()
    assert rows == []


@pytest.mark.asyncio
async def test_crec_find_recordings_to_rotate():
    """Active recording older than the segment length is flagged for rotation;
    a fresh one is not."""
    from app.services.continuous_recording import find_recordings_to_rotate

    tenant_id, _, _ = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id, continuous=True)
    old_rec = await _seed_recording(tenant_id, camera_id, stream_id,
                                    status="recording", started_offset_minutes=30)
    cam2, stream2 = await _seed_camera_and_stream(tenant_id, continuous=True)
    await _seed_recording(tenant_id, cam2, stream2,
                          status="recording", started_offset_minutes=2)
    engine, session = await _app_session(tenant_id)
    try:
        ids = await find_recordings_to_rotate(session, segment_minutes=15)
    finally:
        await session.close()
        await engine.dispose()
    assert ids == [str(old_rec)]


# ─── C. Retention purge ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crec_purge_removes_old_row_and_file(tmp_path):
    """Expired completed recording: file deleted from disk + row removed."""
    from app.services.continuous_recording import purge_expired_recordings

    tenant_id, _, _ = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id)
    rel = f"{tenant_id}/{camera_id}/old-segment.mp4"
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"fake mp4 bytes")
    old_rec = await _seed_recording(tenant_id, camera_id, stream_id,
                                    status="completed",
                                    started_offset_minutes=60 * 24 * 10,  # 10 days old
                                    file_path=rel)
    engine, session = await _app_session(tenant_id)
    try:
        purged = await purge_expired_recordings(session, str(tmp_path), retention_days=7)
        remaining = (await session.execute(
            text("SELECT COUNT(*) FROM recordings WHERE id = :id"), {"id": old_rec}
        )).scalar()
    finally:
        await session.close()
        await engine.dispose()
    assert purged == 1
    assert not full.exists()
    assert remaining == 0


@pytest.mark.asyncio
async def test_crec_purge_keeps_recent_recording(tmp_path):
    """A recording inside the retention window survives the purge."""
    from app.services.continuous_recording import purge_expired_recordings

    tenant_id, _, _ = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id)
    recent = await _seed_recording(tenant_id, camera_id, stream_id,
                                   status="completed", started_offset_minutes=60)
    engine, session = await _app_session(tenant_id)
    try:
        purged = await purge_expired_recordings(session, str(tmp_path), retention_days=7)
        remaining = (await session.execute(
            text("SELECT COUNT(*) FROM recordings WHERE id = :id"), {"id": recent}
        )).scalar()
    finally:
        await session.close()
        await engine.dispose()
    assert purged == 0
    assert remaining == 1


# ─── D. Timeline endpoint ─────────────────────────────────────────────────────

# The tenants table defaults timezone to 'Asia/Singapore' and _seed_tenant_and_token
# does not override it, so that is the timezone /recordings/timeline resolves a
# date in. Computing "today" in UTC instead made these tests fail for the eight
# hours a day when UTC-today is already tomorrow in Singapore.
TENANT_TZ = ZoneInfo("Asia/Singapore")


def _tenant_today() -> str:
    """Today as the tenant sees it — the date the timeline endpoint expects."""
    return datetime.now(TENANT_TZ).strftime("%Y-%m-%d")


@pytest.mark.asyncio
async def test_crec_timeline_empty_day():
    """Timeline for a camera with no footage returns empty segments + alerts."""
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id, _ = await _seed_camera_and_stream(tenant_id)
    today = _tenant_today()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/recordings/timeline?camera_id={camera_id}&date={today}")
    assert r.status_code == 200
    body = r.json()
    assert body["segments"] == []
    assert body["alerts"] == []
    assert body["camera_name"] == "CRec Cam"


@pytest.mark.asyncio
async def test_crec_timeline_returns_segments_and_alert_markers():
    """Seeded recording + alert for today both appear on the timeline."""
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id)
    rec_id = await _seed_recording(tenant_id, camera_id, stream_id,
                                   status="completed", started_offset_minutes=30)
    alert_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, title, status) "
                 "VALUES (:id, :tid, :cid, 'intrusion', 'high', 'Timeline Alert', 'open')"),
            {"id": alert_id, "tid": tenant_id, "cid": camera_id},
        )
        await s.commit()
    await engine.dispose()
    today = _tenant_today()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/recordings/timeline?camera_id={camera_id}&date={today}")
    body = r.json()
    assert [s["id"] for s in body["segments"]] == [str(rec_id)]
    assert [a["title"] for a in body["alerts"]] == ["Timeline Alert"]


@pytest.mark.asyncio
async def test_crec_timeline_other_day_excluded():
    """A recording from yesterday does not appear on today's timeline."""
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id)
    await _seed_recording(tenant_id, camera_id, stream_id, status="completed",
                          started_offset_minutes=60 * 26)  # started 26h ago, 15min long
    today = _tenant_today()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/recordings/timeline?camera_id={camera_id}&date={today}")
    assert r.json()["segments"] == []


@pytest.mark.asyncio
async def test_crec_timeline_bad_date_and_unknown_camera():
    """Malformed date → 422; unknown camera → 404."""
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id, _ = await _seed_camera_and_stream(tenant_id)
    async with await _authed(token) as c:
        r_bad = await c.get(f"/api/v1/recordings/timeline?camera_id={camera_id}&date=04-07-2026")
        r_unknown = await c.get(f"/api/v1/recordings/timeline?camera_id={uuid.uuid4()}&date=2026-07-04")
    assert r_bad.status_code == 422
    assert r_unknown.status_code == 404


# ─── E. Playback endpoint auth ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crec_play_invalid_token_401():
    """Garbage token → 401."""
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    async with client as c:
        r = await c.get(f"/api/v1/recordings/{uuid.uuid4()}/play?token=not-a-jwt")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_crec_play_unknown_recording_404():
    """Valid token, unknown recording id → 404."""
    _, _, token = await _seed_tenant_and_token()
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    async with client as c:
        r = await c.get(f"/api/v1/recordings/{uuid.uuid4()}/play?token={token}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_crec_play_completed_recording_streams_mp4():
    """Completed recording with a real file → 200 video/mp4."""
    recordings_root = os.environ.get("RECORDINGS_ROOT", "/data/recordings")
    tenant_id, _, token = await _seed_tenant_and_token()
    camera_id, stream_id = await _seed_camera_and_stream(tenant_id)
    rel = f"{tenant_id}/{camera_id}/play-test.mp4"
    full = os.path.join(recordings_root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42fake-video-bytes")
    try:
        rec_id = await _seed_recording(tenant_id, camera_id, stream_id,
                                       status="completed", started_offset_minutes=10,
                                       file_path=rel)
        client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
        async with client as c:
            r = await c.get(f"/api/v1/recordings/{rec_id}/play?token={token}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "video/mp4"
        assert r.content.startswith(b"\x00\x00\x00\x18ftyp")
    finally:
        if os.path.exists(full):
            os.remove(full)
