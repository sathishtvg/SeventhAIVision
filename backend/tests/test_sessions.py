"""Gap 40 — Sessions Management

Covers all 5 endpoints in backend/app/routers/sessions.py:
  GET    /api/v1/sessions/me               — list caller's active sessions
  DELETE /api/v1/sessions/me/{session_id}  — revoke one of the caller's sessions
  DELETE /api/v1/sessions/me               — revoke ALL caller's sessions
  GET    /api/v1/sessions/users/{uid}      — admin: list another user's sessions
  DELETE /api/v1/sessions/users/{uid}      — admin: revoke all sessions for a user

Sections:
  A — DB schema: refresh_tokens table columns
  B — GET /me: returns active sessions, excludes expired/revoked
  C — DELETE /me/{id}: revoke one session; 404 on unknown or wrong-user
  D — DELETE /me: revoke all own sessions
  E — Admin: GET /users/{uid} and DELETE /users/{uid}
  F — Permissions: session:manage required for admin endpoints; unauth=401
  G — RLS: cross-tenant sessions invisible
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
    slug = f"ses-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Sessions Test {slug}", "slug": slug},
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
                "email": f"ses-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_extra_user(tenant_id: uuid.UUID, role_id: int = 5) -> uuid.UUID:
    """Insert a second user into an existing tenant; return user_id."""
    user_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                "VALUES (:id, :tid, :role, :email, 'x')"
            ),
            {
                "id": user_id,
                "tid": tenant_id,
                "role": role_id,
                "email": f"extra-{user_id.hex[:8]}@test.local",
            },
        )
        await s.commit()
    await engine.dispose()
    return user_id


async def _seed_session(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    device_name: str = "TestDevice",
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> uuid.UUID:
    """Insert a refresh_tokens row; return token id."""
    token_id = uuid.uuid4()
    if expires_at is None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO refresh_tokens "
                "(id, tenant_id, user_id, token_hash, expires_at, revoked_at, device_name) "
                "VALUES (:id, :tid, :uid, :hash, :expires, :revoked, :device)"
            ),
            {
                "id": token_id,
                "tid": tenant_id,
                "uid": user_id,
                "hash": f"hash-{token_id.hex}",
                "expires": expires_at,
                "revoked": revoked_at,
                "device": device_name,
            },
        )
        await s.commit()
    await engine.dispose()
    return token_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. DB Schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_tokens_expected_columns():
    """refresh_tokens has the columns needed for session management."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'refresh_tokens'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in ("id", "tenant_id", "user_id", "token_hash", "expires_at", "revoked_at", "device_name"):
        assert col in cols, f"Column {col!r} missing from refresh_tokens"


@pytest.mark.asyncio
async def test_active_filter_excludes_expired_and_revoked():
    """GET /sessions/me omits expired tokens and revoked tokens."""
    tid, uid, token = await _seed_tenant_and_token()

    active_id = await _seed_session(tid, uid, device_name="ActiveDevice")
    await _seed_session(
        tid, uid,
        device_name="ExpiredDevice",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    await _seed_session(
        tid, uid,
        device_name="RevokedDevice",
        revoked_at=datetime.now(timezone.utc),
    )

    async with await _authed(token) as c:
        r = await c.get("/api/v1/sessions/me")
    assert r.status_code == 200
    sessions = r.json()
    ids = [str(s["id"]) for s in sessions]
    device_names = [s.get("device_name") for s in sessions]

    assert str(active_id) in ids
    assert "ExpiredDevice" not in device_names
    assert "RevokedDevice" not in device_names


# ─── B. GET /sessions/me ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_my_sessions_returns_active_with_fields():
    """GET /sessions/me returns active session rows with expected response fields."""
    tid, uid, token = await _seed_tenant_and_token()
    tok_id = await _seed_session(tid, uid, device_name="My Laptop")

    async with await _authed(token) as c:
        r = await c.get("/api/v1/sessions/me")
    assert r.status_code == 200
    sessions = r.json()
    match = next((s for s in sessions if str(s["id"]) == str(tok_id)), None)
    assert match is not None, "Active session not found in list"
    assert match["device_name"] == "My Laptop"
    assert "expires_at" in match
    assert "created_at" in match


@pytest.mark.asyncio
async def test_list_my_sessions_empty_when_no_sessions():
    """GET /sessions/me returns [] for a user with no active refresh tokens."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/sessions/me")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_my_sessions_multiple_devices():
    """GET /sessions/me returns all active sessions across multiple devices."""
    tid, uid, token = await _seed_tenant_and_token()
    id1 = await _seed_session(tid, uid, device_name="Phone")
    id2 = await _seed_session(tid, uid, device_name="Tablet")

    async with await _authed(token) as c:
        r = await c.get("/api/v1/sessions/me")
    assert r.status_code == 200
    ids = [str(s["id"]) for s in r.json()]
    assert str(id1) in ids
    assert str(id2) in ids


# ─── C. DELETE /sessions/me/{session_id} ──────────────────────────────────────

@pytest.mark.asyncio
async def test_revoke_own_session_removes_it_from_list():
    """DELETE /sessions/me/{id} revokes the session; it is no longer listed."""
    tid, uid, token = await _seed_tenant_and_token()
    tok_id = await _seed_session(tid, uid)

    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/sessions/me/{tok_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["revoked"] is True
        assert str(data["id"]) == str(tok_id)

        sessions_after = (await c.get("/api/v1/sessions/me")).json()
    assert not any(str(s["id"]) == str(tok_id) for s in sessions_after)


@pytest.mark.asyncio
async def test_revoke_unknown_session_returns_404():
    """DELETE /sessions/me/{id} with a non-existent session ID returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/sessions/me/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cannot_revoke_another_users_session_via_me():
    """DELETE /sessions/me/{id} checks user_id; returns 404 for another user's session."""
    tid, _, token_a = await _seed_tenant_and_token(role_id=2)
    uid_b = await _seed_extra_user(tid)
    tok_b = await _seed_session(tid, uid_b, device_name="UserBPhone")

    async with await _authed(token_a) as c:
        r = await c.delete(f"/api/v1/sessions/me/{tok_b}")
    assert r.status_code == 404


# ─── D. DELETE /sessions/me (revoke all) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_revoke_all_my_sessions():
    """DELETE /sessions/me revokes all active sessions and list becomes empty."""
    tid, uid, token = await _seed_tenant_and_token()
    for i in range(3):
        await _seed_session(tid, uid, device_name=f"Device {i}")

    async with await _authed(token) as c:
        r = await c.delete("/api/v1/sessions/me")
        assert r.status_code == 200
        assert r.json()["revoked_count"] >= 3

        remaining = (await c.get("/api/v1/sessions/me")).json()
    assert remaining == []


@pytest.mark.asyncio
async def test_revoke_all_returns_zero_when_no_active_sessions():
    """DELETE /sessions/me returns revoked_count=0 when no active sessions exist."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.delete("/api/v1/sessions/me")
    assert r.status_code == 200
    assert r.json()["revoked_count"] == 0


# ─── E. Admin endpoints ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_list_another_users_sessions():
    """GET /sessions/users/{uid} allows admin to list a specific user's sessions."""
    tid, _, admin_token = await _seed_tenant_and_token(role_id=2)
    target_uid = await _seed_extra_user(tid)
    tok_id = await _seed_session(tid, target_uid, device_name="TargetPhone")

    async with await _authed(admin_token) as c:
        r = await c.get(f"/api/v1/sessions/users/{target_uid}")
    assert r.status_code == 200
    sessions = r.json()
    assert any(str(s["id"]) == str(tok_id) for s in sessions)


@pytest.mark.asyncio
async def test_admin_list_sessions_empty_for_user_with_none():
    """GET /sessions/users/{uid} returns [] when the user has no active sessions."""
    tid, _, admin_token = await _seed_tenant_and_token(role_id=2)
    target_uid = await _seed_extra_user(tid)

    async with await _authed(admin_token) as c:
        r = await c.get(f"/api/v1/sessions/users/{target_uid}")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_admin_revoke_all_user_sessions():
    """DELETE /sessions/users/{uid} revokes all sessions for the target user."""
    tid, _, admin_token = await _seed_tenant_and_token(role_id=2)
    target_uid = await _seed_extra_user(tid)
    for i in range(2):
        await _seed_session(tid, target_uid, device_name=f"UserDevice {i}")

    async with await _authed(admin_token) as c:
        r = await c.delete(f"/api/v1/sessions/users/{target_uid}")
    assert r.status_code == 200
    data = r.json()
    assert str(data["user_id"]) == str(target_uid)
    assert data["revoked_count"] >= 2


# ─── F. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_access_admin_session_endpoints():
    """GET /sessions/users/{uid} requires session:manage — viewer (role 6) gets 403."""
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.get(f"/api/v1/sessions/users/{uuid.uuid4()}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_returns_401():
    """All /sessions/me endpoints require a valid JWT; missing token → 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/sessions/me")
    assert r.status_code == 401


# ─── G. RLS — Tenant Isolation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_my_sessions_only_shows_own_tenant():
    """GET /sessions/me only returns sessions within the caller's tenant."""
    tid_a, uid_a, token_a = await _seed_tenant_and_token()
    tid_b, uid_b, token_b = await _seed_tenant_and_token()

    tok_a = await _seed_session(tid_a, uid_a, device_name="TenantADevice")

    async with await _authed(token_b) as c:
        r = await c.get("/api/v1/sessions/me")
    assert r.status_code == 200
    ids = [str(s["id"]) for s in r.json()]
    assert str(tok_a) not in ids


@pytest.mark.asyncio
async def test_rls_admin_cannot_revoke_cross_tenant_sessions():
    """DELETE /sessions/users/{uid} from Tenant A cannot affect Tenant B user's sessions."""
    tid_a, _, token_a = await _seed_tenant_and_token(role_id=2)
    tid_b, uid_b, _ = await _seed_tenant_and_token()
    await _seed_session(tid_b, uid_b, device_name="TenantBDevice")

    async with await _authed(token_a) as c:
        r = await c.delete(f"/api/v1/sessions/users/{uid_b}")
    assert r.status_code == 200
    assert r.json()["revoked_count"] == 0
