"""Gap 77 — Audit Router (isolated-tenant)

Isolated-tenant tests for backend/app/routers/audit.py (116 lines).
Paginated audit log listing + tamper-detection chain verification.

Endpoints:
  GET /api/v1/audit         — audit:read; paginated; ?action=&resource_type=&limit=&offset=
  GET /api/v1/audit/verify  — audit:verify; returns chain integrity report

Permission grants:
  audit:read   — roles 1,2,3 (supervisor) and 6 (viewer); NOT operator(4)/security_guard(5)
  audit:verify — roles 1,2 only

Sections:
  A — List endpoint (4 tests)
  B — Verify endpoint (3 tests)
  C — Permissions + RLS (2 tests)
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
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"aui-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"AuditI Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Audit Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"aui-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_audit_row(
    tenant_id: uuid.UUID,
    action: str = "test.action",
    resource_type: str = "camera",
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert an audit_log row via admin engine (bypasses RLS)."""
    row_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO audit_logs "
                "(id, tenant_id, user_id, action, resource_type, created_at) "
                "VALUES (:id, :tid, :uid, :action, :rtype, now())"
            ),
            {"id": row_id, "tid": tenant_id, "uid": user_id,
             "action": action, "rtype": resource_type},
        )
        await s.commit()
    await engine.dispose()
    return row_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. List endpoint ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_aui_list_empty_fresh_tenant():
    """GET /audit on fresh tenant returns 200 + paginated shape with empty items."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/audit")
    assert r.status_code == 200
    body = r.json()
    for key in ("items", "total", "limit", "offset", "has_more"):
        assert key in body, f"Missing key: {key}"
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_aui_list_seeded_row_appears():
    """Seeded audit_log row appears in GET /audit list with all expected fields."""
    tenant_id, user_id, token = await _seed_tenant_and_token()
    row_id = await _seed_audit_row(tenant_id, action="camera.create",
                                   resource_type="camera", user_id=user_id)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/audit")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    ids = [item["id"] for item in items]
    assert str(row_id) in ids
    row = next(i for i in items if i["id"] == str(row_id))
    for key in ("id", "user_id", "action", "resource_type",
                "resource_id", "ip_address", "detail", "created_at", "has_hash"):
        assert key in row, f"Missing field: {key}"
    assert row["action"] == "camera.create"
    assert row["has_hash"] is False   # seeded without row_hash


@pytest.mark.asyncio
async def test_aui_list_action_filter():
    """?action=special.event returns only rows with that action."""
    tenant_id, _, token = await _seed_tenant_and_token()
    await _seed_audit_row(tenant_id, action="special.event", resource_type="camera")
    await _seed_audit_row(tenant_id, action="other.event", resource_type="user")
    async with await _authed(token) as c:
        r = await c.get("/api/v1/audit?action=special.event")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    assert all(item["action"] == "special.event" for item in items)


@pytest.mark.asyncio
async def test_aui_list_resource_type_filter():
    """?resource_type=zone returns only rows with that resource_type."""
    tenant_id, _, token = await _seed_tenant_and_token()
    await _seed_audit_row(tenant_id, action="zone.create", resource_type="zone")
    await _seed_audit_row(tenant_id, action="cam.update", resource_type="camera")
    async with await _authed(token) as c:
        r = await c.get("/api/v1/audit?resource_type=zone")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    assert all(item["resource_type"] == "zone" for item in items)


# ─── B. Verify endpoint ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_aui_verify_empty_tenant_verified_true():
    """Fresh tenant with no signed rows → verified=True, total_checked=0."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/audit/verify")
    assert r.status_code == 200
    body = r.json()
    assert body["verified"] is True
    assert body["total_checked"] == 0
    assert body["tampered_count"] == 0
    assert body["chain_broken"] is False


@pytest.mark.asyncio
async def test_aui_verify_returns_all_required_keys():
    """GET /audit/verify response contains all 5 required keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/audit/verify")
    assert r.status_code == 200
    body = r.json()
    for key in ("verified", "total_checked", "tampered_count", "tampered_ids", "chain_broken"):
        assert key in body, f"Missing key: {key}"
    assert isinstance(body["tampered_ids"], list)


@pytest.mark.asyncio
async def test_aui_verify_requires_audit_verify_permission():
    """Supervisor (role 3) has audit:read but NOT audit:verify → 403."""
    _, _, token = await _seed_tenant_and_token(role_id=3)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/audit/verify")
    assert r.status_code == 403


# ─── C. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_aui_viewer_can_list():
    """Viewer (role 6) has audit:read → GET /audit returns 200."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/audit")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_aui_rls_isolation():
    """Tenant B cannot see Tenant A's audit log rows."""
    tenant_a, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    await _seed_audit_row(tenant_a, action="secret.action")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/audit")
    assert r.json()["items"] == []
