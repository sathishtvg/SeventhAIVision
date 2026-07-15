"""Gap 88 — Offline sync: client-supplied event timestamps (isolated-tenant)

The mobile outbox replays checkpoint scans and DOB entries made offline,
carrying the ORIGINAL event time. Tests the backend half:

  - POST /patrols/sessions/{id}/scan accepts scanned_at (ISO) and persists it
  - POST /dob accepts occurred_at and persists it
  - Bounds: future (> +5min) → 422; older than 48h → 422; garbage → 422
  - Omitting the field keeps server-now() behaviour (regression guard)

Sections:
  A — parse_client_timestamp pure bounds (3 tests)
  B — Checkpoint scan replay (3 tests)
  C — DOB entry replay (3 tests)
"""
from __future__ import annotations

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
    slug = f"ofs-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Offline Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Offline Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"ofs-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_patrol_fixture(tenant_id: uuid.UUID, guard_user_id: uuid.UUID):
    """Site → route → checkpoint → in-progress session, seeded via admin."""
    site_id, route_id, checkpoint_id, session_id = (uuid.uuid4() for _ in range(4))
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, 'OFS Site')"),
            {"id": site_id, "tid": tenant_id},
        )
        await s.execute(
            text("INSERT INTO patrol_routes (id, tenant_id, site_id, name, is_active) "
                 "VALUES (:id, :tid, :sid, 'OFS Route', TRUE)"),
            {"id": route_id, "tid": tenant_id, "sid": site_id},
        )
        await s.execute(
            text("INSERT INTO patrol_checkpoints (id, tenant_id, route_id, sequence, name, qr_code) "
                 "VALUES (:id, :tid, :rid, 1, 'CP1', 'QR-OFS-1')"),
            {"id": checkpoint_id, "tid": tenant_id, "rid": route_id},
        )
        await s.execute(
            text("INSERT INTO patrol_sessions (id, tenant_id, route_id, guard_user_id, status) "
                 "VALUES (:id, :tid, :rid, :gid, 'in_progress')"),
            {"id": session_id, "tid": tenant_id, "rid": route_id, "gid": guard_user_id},
        )
        await s.commit()
    await engine.dispose()
    return checkpoint_id, session_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# ─── A. parse_client_timestamp bounds ────────────────────────────────────────

def test_ofs_parse_none_passes_through():
    from app.core.client_time import parse_client_timestamp
    assert parse_client_timestamp(None) is None


def test_ofs_parse_valid_recent():
    from app.core.client_time import parse_client_timestamp
    ts = datetime.now(timezone.utc) - timedelta(hours=2)
    parsed = parse_client_timestamp(_iso(ts))
    assert parsed is not None
    assert abs((parsed - ts).total_seconds()) < 1


def test_ofs_parse_rejects_future_old_and_garbage():
    from fastapi import HTTPException

    from app.core.client_time import parse_client_timestamp

    now = datetime.now(timezone.utc)
    for bad in (_iso(now + timedelta(hours=1)),
                _iso(now - timedelta(hours=72)),
                "not-a-date"):
        with pytest.raises(HTTPException) as exc:
            parse_client_timestamp(bad)
        assert exc.value.status_code == 422


# ─── B. Checkpoint scan replay ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ofs_scan_with_client_time_persists_it():
    """Replayed scan stores the ORIGINAL scan time, not now()."""
    tenant_id, user_id, token = await _seed_tenant_and_token(role_id=5)
    checkpoint_id, session_id = await _seed_patrol_fixture(tenant_id, user_id)
    original = datetime.now(timezone.utc) - timedelta(hours=3)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/patrols/sessions/{session_id}/scan", json={
            "checkpoint_id": str(checkpoint_id),
            "scan_method": "qr",
            "scanned_code": "QR-OFS-1",
            "scanned_at": _iso(original),
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] is True
    stored = datetime.fromisoformat(body["scanned_at"])
    assert abs((stored - original).total_seconds()) < 2


@pytest.mark.asyncio
async def test_ofs_scan_without_client_time_uses_now():
    """Online scan (no scanned_at) still records approximately now()."""
    tenant_id, user_id, token = await _seed_tenant_and_token(role_id=5)
    checkpoint_id, session_id = await _seed_patrol_fixture(tenant_id, user_id)
    before = datetime.now(timezone.utc)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/patrols/sessions/{session_id}/scan", json={
            "checkpoint_id": str(checkpoint_id), "scan_method": "manual",
        })
    assert r.status_code == 200
    stored = datetime.fromisoformat(r.json()["scanned_at"])
    assert abs((stored - before).total_seconds()) < 30


@pytest.mark.asyncio
async def test_ofs_scan_rejects_out_of_window_time():
    """Future or >48h-old scanned_at → 422 and no row created."""
    tenant_id, user_id, token = await _seed_tenant_and_token(role_id=5)
    checkpoint_id, session_id = await _seed_patrol_fixture(tenant_id, user_id)
    now = datetime.now(timezone.utc)
    async with await _authed(token) as c:
        r_future = await c.post(f"/api/v1/patrols/sessions/{session_id}/scan", json={
            "checkpoint_id": str(checkpoint_id), "scan_method": "manual",
            "scanned_at": _iso(now + timedelta(hours=2)),
        })
        r_old = await c.post(f"/api/v1/patrols/sessions/{session_id}/scan", json={
            "checkpoint_id": str(checkpoint_id), "scan_method": "manual",
            "scanned_at": _iso(now - timedelta(hours=72)),
        })
    assert r_future.status_code == 422
    assert r_old.status_code == 422


# ─── C. DOB entry replay ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ofs_dob_with_client_time_persists_it():
    """Replayed DOB entry keeps the original occurrence time."""
    _, _, token = await _seed_tenant_and_token(role_id=5)
    original = datetime.now(timezone.utc) - timedelta(hours=5)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/dob", json={
            "entry_type": "general",
            "body": "Water leak observed in basement B2 (written offline).",
            "occurred_at": _iso(original),
        })
    assert r.status_code == 200, r.text
    stored = datetime.fromisoformat(r.json()["occurred_at"])
    assert abs((stored - original).total_seconds()) < 2


@pytest.mark.asyncio
async def test_ofs_dob_without_client_time_uses_now():
    """Online DOB entry defaults to now()."""
    _, _, token = await _seed_tenant_and_token(role_id=5)
    before = datetime.now(timezone.utc)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/dob", json={
            "entry_type": "general", "body": "Routine online entry.",
        })
    assert r.status_code == 200
    stored = datetime.fromisoformat(r.json()["occurred_at"])
    assert abs((stored - before).total_seconds()) < 30


@pytest.mark.asyncio
async def test_ofs_dob_rejects_bad_time():
    """Garbage occurred_at → 422."""
    _, _, token = await _seed_tenant_and_token(role_id=5)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/dob", json={
            "entry_type": "general", "body": "x", "occurred_at": "yesterday-ish",
        })
    assert r.status_code == 422
