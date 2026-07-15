"""Gap 66 — Unified Cross-Module Search Router

Isolated-tenant tests for backend/app/routers/search.py (212 lines).
Single endpoint: GET /api/v1/search?q=...&modules=...&limit=...&offset=...

Permission: alert:read (all roles including viewer).
Returns: {query, modules, items, total, limit, offset, has_more}.
Each item: {module, id, title, summary, severity, status, created_at}.

Modules: alerts, incidents, cameras, sites, watchlist, face_watchlist, zones, users.
Default modules (when not specified): alerts,incidents,cameras,sites,watchlist,face_watchlist,zones.

Sections:
  A — Basic response shape (4 tests)
  B — Multi-module search (4 tests)
  C — Validation (3 tests)
  D — Permissions + RLS (2 tests)
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
    slug = f"src-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Search Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Src Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"src-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera(tenant_id: uuid.UUID, name: str = "Search Cam") -> uuid.UUID:
    cam_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO cameras (id, tenant_id, name, location) VALUES (:id, :tid, :n, :l)"),
            {"id": cam_id, "tid": tenant_id, "n": name, "l": "Gate"},
        )
        await s.commit()
    await engine.dispose()
    return cam_id


async def _seed_alert(tenant_id: uuid.UUID, camera_id: uuid.UUID, title: str) -> uuid.UUID:
    alert_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, title, status, alert_code) "
                "VALUES (:id, :tid, :cid, 'lpr', 'medium', :title, 'open', 'test.alert')"
            ),
            {"id": alert_id, "tid": tenant_id, "cid": camera_id, "title": title},
        )
        await s.commit()
    await engine.dispose()
    return alert_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. Basic response shape ──────────────────────────────────────────────────

_RESPONSE_KEYS = {"query", "modules", "items", "total", "limit", "offset", "has_more"}
_ITEM_KEYS = {"module", "id", "title", "summary", "severity", "status", "created_at"}


@pytest.mark.asyncio
async def test_src_response_has_all_7_keys():
    """GET /search?q=xyz returns a response with all 7 top-level keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/search?q=xyznothing")
    assert r.status_code == 200
    assert _RESPONSE_KEYS <= set(r.json().keys())


@pytest.mark.asyncio
async def test_src_empty_results_fresh_tenant():
    """GET /search?q=<unique> on fresh tenant returns items=[], total=0, has_more=False."""
    _, _, token = await _seed_tenant_and_token()
    unique_q = f"uq-{uuid.uuid4().hex}"
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/search?q={unique_q}")
    body = r.json()
    assert r.status_code == 200
    assert body["items"] == []
    assert body["total"] == 0
    assert body["has_more"] is False


@pytest.mark.asyncio
async def test_src_response_echoes_query_and_limit():
    """Response echoes back query, limit, offset, and the active modules list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/search?q=test&limit=10&offset=5")
    body = r.json()
    assert body["query"] == "test"
    assert body["limit"] == 10
    assert body["offset"] == 5
    assert isinstance(body["modules"], list)
    assert len(body["modules"]) > 0


@pytest.mark.asyncio
async def test_src_short_query_returns_422():
    """q param of only 1 character returns 422 (min_length=2)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/search?q=x")
    assert r.status_code == 422


# ─── B. Multi-module search ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_src_camera_name_match_returns_item():
    """Seeding a camera with a unique name makes it appear in search results."""
    tenant_id, _, token = await _seed_tenant_and_token()
    unique = f"camuniq-{uuid.uuid4().hex[:8]}"
    await _seed_camera(tenant_id, name=f"{unique} Gate Camera")
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/search?q={unique}&modules=cameras")
    body = r.json()
    assert r.status_code == 200
    assert body["total"] >= 1
    assert any(item["module"] == "camera" for item in body["items"])
    match = next(i for i in body["items"] if i["module"] == "camera")
    assert unique in match["title"]
    assert _ITEM_KEYS <= set(match.keys())


@pytest.mark.asyncio
async def test_src_alert_title_match_returns_item():
    """Seeding an alert with a unique title makes it appear in search results."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    unique = f"alrtuniq-{uuid.uuid4().hex[:8]}"
    await _seed_alert(tenant_id, cam_id, title=f"Alert {unique} Test")
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/search?q={unique}&modules=alerts")
    body = r.json()
    assert body["total"] >= 1
    assert any(item["module"] == "alert" for item in body["items"])


@pytest.mark.asyncio
async def test_src_multiple_modules_combined_results():
    """Search across two modules returns items from both."""
    tenant_id, _, token = await _seed_tenant_and_token()
    unique = f"multi-{uuid.uuid4().hex[:8]}"
    cam_id = await _seed_camera(tenant_id, name=f"Cam {unique}")
    await _seed_alert(tenant_id, cam_id, title=f"Alert {unique}")
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/search?q={unique}&modules=cameras&modules=alerts")
    body = r.json()
    modules_found = {item["module"] for item in body["items"]}
    assert "camera" in modules_found
    assert "alert" in modules_found


@pytest.mark.asyncio
async def test_src_modules_filter_excludes_other_modules():
    """Restricting to modules=['alerts'] means cameras are NOT in results."""
    tenant_id, _, token = await _seed_tenant_and_token()
    unique = f"filt-{uuid.uuid4().hex[:8]}"
    await _seed_camera(tenant_id, name=f"Camera {unique}")
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/search?q={unique}&modules=alerts")
    body = r.json()
    assert all(item["module"] != "camera" for item in body["items"])


# ─── C. Validation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_src_unknown_module_returns_422():
    """modules=['unknownxyz'] returns 422 — not a valid module name."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/search?q=test&modules=unknownxyz")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_src_explicit_valid_modules_param_returns_200():
    """modules=['cameras','alerts'] is explicitly valid — returns 200."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/search?q=test&modules=cameras&modules=alerts")
    assert r.status_code == 200
    body = r.json()
    assert set(body["modules"]) == {"cameras", "alerts"}


@pytest.mark.asyncio
async def test_src_pagination_has_more_true():
    """When total > limit+offset, has_more=True."""
    tenant_id, _, token = await _seed_tenant_and_token()
    prefix = f"pg-{uuid.uuid4().hex[:8]}"
    for i in range(3):
        await _seed_camera(tenant_id, name=f"Camera {prefix} {i}")
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/search?q={prefix}&modules=cameras&limit=1&offset=0")
    body = r.json()
    assert body["total"] >= 3
    assert body["has_more"] is True


# ─── D. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_src_viewer_can_search():
    """Viewer (role 6) has alert:read → GET /search returns 200."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/search?q=anything")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_src_rls_isolation():
    """Tenant B cannot see Tenant A's camera in search results."""
    tenant_a, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    unique = f"rls-{uuid.uuid4().hex[:8]}"
    await _seed_camera(tenant_a, name=f"Camera {unique}")
    async with await _authed(tok_b) as c:
        r = await c.get(f"/api/v1/search?q={unique}&modules=cameras")
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []
