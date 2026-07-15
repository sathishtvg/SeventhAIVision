"""Gap 35 — Emergency Mass Notification tests.

Sections:
  A (3)  — DB schema: tables exist, permissions seeded
  B (3)  — Permission grants: operator can send, guard cannot, viewer can read
  C (5)  — Create broadcast: type=all, type=role, validation errors
  D (4)  — List and get: empty list, created appears, detail, not-found 404
  E (2)  — My broadcasts: sender is recipient (type=all)
  F (3)  — Acknowledge: updates acknowledged_at, increments count, idempotent
  G (3)  — RLS isolation + auth: cross-tenant blocked, unauthenticated blocked
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


async def _seed_tenant_and_token(role_id: int = 2):
    """Create an isolated tenant + user; return (tenant_id, user_id, jwt_token)."""
    from app.core.security import create_access_token, hash_password

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"em-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"EM Tenant {slug}", "slug": slug},
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
async def test_emergency_broadcasts_table_exists():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        row = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='emergency_broadcasts' ORDER BY column_name"
        ))
        cols = {r[0] for r in row.fetchall()}
    await engine.dispose()
    for col in ("id", "tenant_id", "title", "message", "severity",
                "broadcast_type", "target_role_ids", "recipient_count",
                "acknowledged_count", "created_by_user_id", "sent_at",
                "status", "created_at"):
        assert col in cols, f"Missing column in emergency_broadcasts: {col}"


@pytest.mark.asyncio
async def test_broadcast_recipients_table_exists():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        row = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='broadcast_recipients' ORDER BY column_name"
        ))
        cols = {r[0] for r in row.fetchall()}
    await engine.dispose()
    for col in ("id", "tenant_id", "broadcast_id", "user_id",
                "delivered_at", "acknowledged_at", "created_at"):
        assert col in cols, f"Missing column in broadcast_recipients: {col}"


@pytest.mark.asyncio
async def test_broadcast_permissions_seeded():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        row = await conn.execute(text(
            "SELECT code FROM permissions WHERE code LIKE 'broadcast:%' ORDER BY code"
        ))
        codes = {r[0] for r in row.fetchall()}
    await engine.dispose()
    assert "broadcast:send" in codes
    assert "broadcast:read" in codes
    assert "broadcast:acknowledge" in codes


# ─────────────────────────────────────────────────────────────────────────────
# Section B — Permission grants
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_send_granted_to_supervisor():
    """Supervisor (role 3) should be able to POST a broadcast (broadcast:send)."""
    _, _, token = await _seed_tenant_and_token(role_id=3)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Supervisor Test",
            "message": "Supervisor broadcast",
            "severity": "info",
            "broadcast_type": "all",
        })
    assert r.status_code == 201, f"Supervisor should be able to send broadcast, got {r.status_code}"


@pytest.mark.asyncio
async def test_broadcast_send_not_granted_to_operator():
    """Operator (role 4) has no broadcast:send — must get 403."""
    _, _, token = await _seed_tenant_and_token(role_id=4)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Op Test",
            "message": "Operator broadcast attempt",
            "severity": "info",
            "broadcast_type": "all",
        })
    assert r.status_code == 403, f"Operator should be denied broadcast:send, got {r.status_code}"


@pytest.mark.asyncio
async def test_broadcast_read_granted_to_viewer():
    """Viewer (role 6) has broadcast:read — GET /broadcasts must return 200."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/emergency/broadcasts")
    assert r.status_code == 200, f"Viewer should be able to read broadcasts, got {r.status_code}"


# ─────────────────────────────────────────────────────────────────────────────
# Section C — Create broadcast
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_broadcast_type_all():
    """Creating a type=all broadcast returns 201 with a recipient_count >= 1."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "All-Hands Alert",
            "message": "Please evacuate building A immediately.",
            "severity": "critical",
            "broadcast_type": "all",
        })
    assert r.status_code == 201, r.text
    body = r.json()
    assert "id" in body
    assert "recipient_count" in body
    assert body["recipient_count"] >= 1, "At least the sender should be a recipient"
    assert "sent_at" in body


@pytest.mark.asyncio
async def test_create_broadcast_type_role():
    """Creating a type=role broadcast with target_role_ids returns 201."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Admin Drill",
            "message": "This is a drill for admins only.",
            "severity": "drill",
            "broadcast_type": "role",
            "target_role_ids": [2],
        })
    assert r.status_code == 201, r.text
    body = r.json()
    assert "id" in body
    assert body["recipient_count"] >= 1


@pytest.mark.asyncio
async def test_create_broadcast_invalid_severity():
    """Invalid severity value must return 422."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Bad Sev",
            "message": "test",
            "severity": "urgent",
            "broadcast_type": "all",
        })
    assert r.status_code == 422, f"Invalid severity should return 422, got {r.status_code}"


@pytest.mark.asyncio
async def test_create_broadcast_invalid_type():
    """Invalid broadcast_type must return 422."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Bad Type",
            "message": "test",
            "severity": "info",
            "broadcast_type": "department",
        })
    assert r.status_code == 422, f"Invalid broadcast_type should return 422, got {r.status_code}"


@pytest.mark.asyncio
async def test_create_broadcast_type_role_without_target_role_ids():
    """broadcast_type=role without target_role_ids must return 422."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Missing Roles",
            "message": "test",
            "severity": "warning",
            "broadcast_type": "role",
        })
    assert r.status_code == 422, (
        f"type=role without target_role_ids should return 422, got {r.status_code}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section D — List and get
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_broadcasts_empty():
    """A fresh tenant with no broadcasts returns an empty list."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/emergency/broadcasts")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_broadcasts_returns_created():
    """A broadcast created via POST appears in the GET list."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        post_r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "List Test",
            "message": "Visible in list",
            "severity": "info",
            "broadcast_type": "all",
        })
        assert post_r.status_code == 201
        broadcast_id = post_r.json()["id"]

        list_r = await c.get("/api/v1/emergency/broadcasts")
    assert list_r.status_code == 200
    items = list_r.json()
    ids = [item["id"] for item in items]
    assert broadcast_id in ids, "Created broadcast should appear in list"


@pytest.mark.asyncio
async def test_get_broadcast_by_id():
    """GET /{broadcast_id} returns the broadcast detail with a recipients list."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        post_r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Detail Check",
            "message": "Test detail endpoint",
            "severity": "warning",
            "broadcast_type": "all",
        })
        assert post_r.status_code == 201
        broadcast_id = post_r.json()["id"]

        get_r = await c.get(f"/api/v1/emergency/broadcasts/{broadcast_id}")
    assert get_r.status_code == 200
    body = get_r.json()
    assert body["id"] == broadcast_id
    assert body["title"] == "Detail Check"
    assert body["severity"] == "warning"
    assert "recipients" in body
    assert isinstance(body["recipients"], list)


@pytest.mark.asyncio
async def test_get_broadcast_not_found():
    """GET with an unknown broadcast_id returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/emergency/broadcasts/{uuid.uuid4()}")
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Section E — My broadcasts
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_my_broadcasts_as_recipient():
    """After a type=all broadcast, the sender appears as a recipient in /my."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        post_r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "My Broadcast Test",
            "message": "Sender is also recipient",
            "severity": "info",
            "broadcast_type": "all",
        })
        assert post_r.status_code == 201
        broadcast_id = post_r.json()["id"]

        my_r = await c.get("/api/v1/emergency/broadcasts/my")
    assert my_r.status_code == 200
    items = my_r.json()
    ids = [item["id"] for item in items]
    assert broadcast_id in ids, "Sender should appear as a recipient of their own type=all broadcast"


@pytest.mark.asyncio
async def test_my_broadcasts_empty_for_non_recipient():
    """A user who was not targeted by any broadcast sees an empty /my list."""
    # Create broadcast in tenant A (only recipient = tenant A's user)
    _, _, token_a = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token_a) as c:
        await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Tenant A Only",
            "message": "Not visible to tenant B",
            "severity": "info",
            "broadcast_type": "all",
        })

    # Tenant B user has no broadcasts
    _, _, token_b = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token_b) as c:
        r = await c.get("/api/v1/emergency/broadcasts/my")
    assert r.status_code == 200
    assert r.json() == []


# ─────────────────────────────────────────────────────────────────────────────
# Section F — Acknowledge
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_acknowledge_broadcast():
    """A recipient can acknowledge a broadcast; response is {ok: True}."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        post_r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Ack Test",
            "message": "Please acknowledge",
            "severity": "warning",
            "broadcast_type": "all",
        })
        broadcast_id = post_r.json()["id"]

        ack_r = await c.post(f"/api/v1/emergency/broadcasts/{broadcast_id}/acknowledge")
    assert ack_r.status_code == 200
    assert ack_r.json().get("ok") is True


@pytest.mark.asyncio
async def test_acknowledge_increments_count():
    """Acknowledging a broadcast increments acknowledged_count in the DB."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        post_r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Count Test",
            "message": "Check count increment",
            "severity": "info",
            "broadcast_type": "all",
        })
        broadcast_id = post_r.json()["id"]

        # Acknowledge
        await c.post(f"/api/v1/emergency/broadcasts/{broadcast_id}/acknowledge")

        # Re-fetch to check count
        get_r = await c.get(f"/api/v1/emergency/broadcasts/{broadcast_id}")
    assert get_r.status_code == 200
    assert get_r.json()["acknowledged_count"] >= 1, "acknowledged_count must be >= 1 after one ack"


@pytest.mark.asyncio
async def test_acknowledge_idempotent():
    """Acknowledging the same broadcast twice does not double-count."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        post_r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Idempotent Ack",
            "message": "Ack twice, count once",
            "severity": "drill",
            "broadcast_type": "all",
        })
        broadcast_id = post_r.json()["id"]

        # Acknowledge twice
        await c.post(f"/api/v1/emergency/broadcasts/{broadcast_id}/acknowledge")
        ack_r2 = await c.post(f"/api/v1/emergency/broadcasts/{broadcast_id}/acknowledge")
        assert ack_r2.status_code == 200  # second ack returns ok:True (no error)

        # Count should still be 1
        get_r = await c.get(f"/api/v1/emergency/broadcasts/{broadcast_id}")
    assert get_r.json()["acknowledged_count"] == 1, (
        "Acknowledging twice must not increment count beyond 1 (idempotent)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section G — RLS isolation + auth
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_tenant_broadcast_not_visible():
    """Tenant B cannot see tenant A's emergency broadcasts."""
    # Create a broadcast in tenant A
    _, _, token_a = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token_a) as c:
        post_r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Tenant A Secret",
            "message": "Only for tenant A",
            "severity": "critical",
            "broadcast_type": "all",
        })
        broadcast_id_a = post_r.json()["id"]

    # Tenant B should see 0 broadcasts
    _, _, token_b = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token_b) as c:
        list_r = await c.get("/api/v1/emergency/broadcasts")
        assert list_r.status_code == 200
        ids = [item["id"] for item in list_r.json()]
        assert broadcast_id_a not in ids, "Tenant B must not see tenant A's broadcasts"

        # Direct GET by ID must return 404 (RLS blocks it)
        get_r = await c.get(f"/api/v1/emergency/broadcasts/{broadcast_id_a}")
        assert get_r.status_code == 404, (
            f"Cross-tenant GET must return 404, got {get_r.status_code}"
        )


@pytest.mark.asyncio
async def test_list_broadcasts_requires_auth():
    """Unauthenticated GET /broadcasts must return 401 or 403."""
    async with AsyncClient(
        transport=ASGITransport(_app()), base_url="http://test"
    ) as c:
        r = await c.get("/api/v1/emergency/broadcasts")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_create_broadcast_requires_auth():
    """Unauthenticated POST /broadcasts must return 401 or 403."""
    async with AsyncClient(
        transport=ASGITransport(_app()), base_url="http://test"
    ) as c:
        r = await c.post("/api/v1/emergency/broadcasts", json={
            "title": "Unauth Test",
            "message": "Should fail",
            "severity": "info",
            "broadcast_type": "all",
        })
    assert r.status_code in (401, 403)
