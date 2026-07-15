"""Gap 61 — Notification Channels, Rules & Logs Router (isolated-tenant integration tests)

Complements test_notifications.py (Phase 4 unit/mock tests).
Uses the isolated-tenant pattern to test the HTTP layer of
backend/app/routers/notifications.py (294 lines) end-to-end.

All endpoints require the 'notification:manage' permission (admin/super_admin).

Endpoints (prefix /api/v1/notifications):
  GET    /channels                  — list channels
  POST   /channels                  — 201; validates channel_type; {id, name, channel_type,...}
  PUT    /channels/{id}             — 422 no-fields; 404 not found
  DELETE /channels/{id}             — 204 no body
  POST   /channels/{id}/test        — 404 not found; 400 if inactive
  GET    /rules                     — list rules
  POST   /rules                     — 201; validates min_severity; 404 if channel not found
  PUT    /rules/{id}                — 422 no-fields; 404 not found
  DELETE /rules/{id}                — 204 no body
  GET    /logs                      — list with optional channel_id / status_filter filters

Sections:
  A — Channels (8 tests)
  B — Channel test endpoint (2 tests)
  C — Rules (7 tests)
  D — Logs (2 tests)
  E — Permissions + RLS (2 tests)
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


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 2):
    """Create isolated tenant + user; return (tenant_id, user_id, token)."""
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"notif-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Notif Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Notif Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"notif-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_channel(c: AsyncClient, name: str = "Test Email",
                           channel_type: str = "email",
                           config: dict | None = None) -> str:
    r = await c.post("/api/v1/notifications/channels", json={
        "name": name, "channel_type": channel_type, "config": config or {},
    })
    assert r.status_code == 201, f"create_channel failed: {r.text}"
    return r.json()["id"]


# ─── A. Channels ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ni_list_channels_empty_fresh_tenant():
    """GET /notifications/channels on a fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/notifications/channels")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_ni_create_email_channel_returns_201():
    """POST /notifications/channels with type=email returns 201 + {id, name, channel_type, ...}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/notifications/channels", json={
            "name": "Security Email", "channel_type": "email",
            "config": {"to": "security@corp.com"},
        })
    assert r.status_code == 201
    body = r.json()
    assert "id" in body
    assert body["channel_type"] == "email"
    assert body["is_active"] is True
    assert "created_at" in body


@pytest.mark.asyncio
async def test_ni_create_webhook_channel_returns_201():
    """POST /notifications/channels with type=webhook returns 201."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/notifications/channels", json={
            "name": "Slack Webhook", "channel_type": "webhook",
            "config": {"url": "https://hooks.slack.com/services/XXXX"},
        })
    assert r.status_code == 201
    assert r.json()["channel_type"] == "webhook"


@pytest.mark.asyncio
async def test_ni_create_channel_invalid_type_returns_422():
    """POST /notifications/channels with an unrecognised channel_type returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/notifications/channels", json={
            "name": "Bad Channel", "channel_type": "telegram",
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_ni_create_channel_appears_in_list():
    """After POST /channels, the channel is visible via GET /channels."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        channel_id = await _create_channel(c, name="Visible Channel")
        r = await c.get("/api/v1/notifications/channels")
    ids = [item["id"] for item in r.json()]
    assert channel_id in ids


@pytest.mark.asyncio
async def test_ni_update_channel_name():
    """PUT /notifications/channels/{id} updates the channel name."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        channel_id = await _create_channel(c, name="Old Name")
        r = await c.put(f"/api/v1/notifications/channels/{channel_id}",
                        json={"name": "New Name"})
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_ni_update_channel_no_fields_returns_422():
    """PUT /notifications/channels/{id} with empty body returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        channel_id = await _create_channel(c)
        r = await c.put(f"/api/v1/notifications/channels/{channel_id}", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_ni_delete_channel_returns_204():
    """DELETE /notifications/channels/{id} returns 204 with no body."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        channel_id = await _create_channel(c, name="To Delete")
        r = await c.delete(f"/api/v1/notifications/channels/{channel_id}")
    assert r.status_code == 204


# ─── B. Channel test endpoint ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ni_test_channel_unknown_id_returns_404():
    """POST /notifications/channels/{random_uuid}/test returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/notifications/channels/{uuid.uuid4()}/test")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ni_test_channel_inactive_returns_400():
    """POST /notifications/channels/{id}/test on a deactivated channel returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        channel_id = await _create_channel(c, name="Soon Inactive")
        await c.put(f"/api/v1/notifications/channels/{channel_id}", json={"is_active": False})
        r = await c.post(f"/api/v1/notifications/channels/{channel_id}/test")
    assert r.status_code == 400


# ─── C. Rules ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ni_list_rules_empty_fresh_tenant():
    """GET /notifications/rules on a fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/notifications/rules")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_ni_create_rule_returns_201():
    """POST /notifications/rules returns 201 + {id, channel_id, min_severity, ...}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        channel_id = await _create_channel(c)
        r = await c.post("/api/v1/notifications/rules", json={
            "channel_id": channel_id,
            "min_severity": "high",
            "module_types": ["lpr", "face"],
        })
    assert r.status_code == 201
    body = r.json()
    assert "id" in body
    assert body["channel_id"] == channel_id
    assert body["min_severity"] == "high"


@pytest.mark.asyncio
async def test_ni_create_rule_invalid_severity_returns_422():
    """POST /notifications/rules with an invalid min_severity returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        channel_id = await _create_channel(c)
        r = await c.post("/api/v1/notifications/rules", json={
            "channel_id": channel_id, "min_severity": "urgent",
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_ni_create_rule_unknown_channel_returns_404():
    """POST /notifications/rules with a non-existent channel_id returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/notifications/rules", json={
            "channel_id": str(uuid.uuid4()), "min_severity": "medium",
        })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ni_create_rule_appears_in_list():
    """After POST /rules, the rule is visible via GET /rules."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        channel_id = await _create_channel(c)
        r_create = await c.post("/api/v1/notifications/rules",
                                json={"channel_id": channel_id, "min_severity": "low"})
        rule_id = r_create.json()["id"]
        r = await c.get("/api/v1/notifications/rules")
    ids = [item["id"] for item in r.json()]
    assert rule_id in ids


@pytest.mark.asyncio
async def test_ni_update_rule_min_severity():
    """PUT /notifications/rules/{id} updates min_severity."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        channel_id = await _create_channel(c)
        r_create = await c.post("/api/v1/notifications/rules",
                                json={"channel_id": channel_id, "min_severity": "low"})
        rule_id = r_create.json()["id"]
        r = await c.put(f"/api/v1/notifications/rules/{rule_id}",
                        json={"min_severity": "critical"})
    assert r.status_code == 200
    assert r.json()["min_severity"] == "critical"


@pytest.mark.asyncio
async def test_ni_update_rule_no_fields_returns_422():
    """PUT /notifications/rules/{id} with empty body returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        channel_id = await _create_channel(c)
        r_create = await c.post("/api/v1/notifications/rules",
                                json={"channel_id": channel_id, "min_severity": "medium"})
        rule_id = r_create.json()["id"]
        r = await c.put(f"/api/v1/notifications/rules/{rule_id}", json={})
    assert r.status_code == 422


# ─── D. Logs ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ni_list_logs_empty_fresh_tenant():
    """GET /notifications/logs on a fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/notifications/logs")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_ni_list_logs_with_channel_filter_returns_200():
    """GET /notifications/logs?channel_id=X returns 200 (empty list when no logs)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        channel_id = await _create_channel(c)
        r = await c.get(f"/api/v1/notifications/logs?channel_id={channel_id}")
    assert r.status_code == 200
    assert r.json() == []


# ─── E. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ni_viewer_cannot_list_channels_403():
    """Viewer (role 6) cannot list notification channels — notification:manage not granted."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/notifications/channels")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ni_channels_rls_isolation():
    """Tenant B cannot see Tenant A's notification channels."""
    _, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_a) as c:
        channel_id = await _create_channel(c, name="Tenant A Channel")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/notifications/channels")
    ids = [item["id"] for item in r.json()]
    assert channel_id not in ids
