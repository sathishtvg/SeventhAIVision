"""Gap 84 — Saved Wall Layouts (isolated-tenant)

Tests backend/app/routers/wall_layouts.py.

Semantics:
  - GET lists my layouts + tenant-shared layouts (is_mine flag, owner_name)
  - POST 201; duplicate name for same owner → 409
  - PUT/DELETE: owner or roles 1-2 only; others → 404
  - Permission gate camera:read (viewer role 6 can save layouts)

Sections:
  A — CRUD (5 tests)
  B — Sharing semantics (4 tests)
  C — Permissions + RLS (3 tests)
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
    slug = f"wal-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Wall Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Wall Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"wal-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_extra_user(tenant_id: uuid.UUID, role_id: int):
    from app.core.security import create_access_token

    user_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Second Wall User')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"wal-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


_CELLS = [
    {"camera_id": str(uuid.uuid4()), "stream_id": str(uuid.uuid4()),
     "camera_name": "Lobby Cam", "site_name": "HQ"},
]


async def _create_layout(c: AsyncClient, name: str = "Night Shift",
                         is_shared: bool = False, grid_size: int = 4) -> dict:
    r = await c.post("/api/v1/wall-layouts", json={
        "name": name, "grid_size": grid_size, "cells": _CELLS, "is_shared": is_shared,
    })
    assert r.status_code == 201, f"create_layout failed: {r.text}"
    return r.json()


# ─── A. CRUD ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wal_list_empty_fresh_tenant():
    """GET /wall-layouts on fresh tenant returns 200 + []."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/wall-layouts")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_wal_create_returns_all_fields():
    """POST returns id, name, grid_size, cells, is_shared, timestamps."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        body = await _create_layout(c, "Day Shift", grid_size=9)
    for field in ("id", "user_id", "name", "grid_size", "cells", "is_shared",
                  "created_at", "updated_at"):
        assert field in body, f"Missing field: {field}"
    assert body["name"] == "Day Shift"
    assert body["grid_size"] == 9
    assert body["cells"][0]["camera_name"] == "Lobby Cam"
    assert body["is_shared"] is False


@pytest.mark.asyncio
async def test_wal_created_layout_appears_in_list_with_is_mine():
    """Created layout appears in GET list flagged is_mine=True + owner_name."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = await _create_layout(c)
        r = await c.get("/api/v1/wall-layouts")
    rows = r.json()
    row = next(rw for rw in rows if rw["id"] == created["id"])
    assert row["is_mine"] is True
    assert row["owner_name"] == "Wall Tester"


@pytest.mark.asyncio
async def test_wal_duplicate_name_same_owner_409():
    """Same owner reusing a layout name → 409."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await _create_layout(c, "Dup Layout")
        r = await c.post("/api/v1/wall-layouts", json={"name": "Dup Layout"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_wal_update_and_delete_round_trip():
    """PUT updates cells/grid; DELETE removes the layout from the list."""
    _, _, token = await _seed_tenant_and_token()
    new_cells = _CELLS + [{"camera_id": str(uuid.uuid4()), "stream_id": str(uuid.uuid4()),
                           "camera_name": "Gate Cam", "site_name": None}]
    async with await _authed(token) as c:
        created = await _create_layout(c)
        r_upd = await c.put(f"/api/v1/wall-layouts/{created['id']}",
                            json={"grid_size": 16, "cells": new_cells})
        assert r_upd.status_code == 200
        assert r_upd.json()["grid_size"] == 16
        assert len(r_upd.json()["cells"]) == 2
        r_del = await c.delete(f"/api/v1/wall-layouts/{created['id']}")
        assert r_del.status_code == 200
        assert r_del.json()["deleted"] is True
        r_list = await c.get("/api/v1/wall-layouts")
    assert all(row["id"] != created["id"] for row in r_list.json())


# ─── B. Sharing semantics ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wal_shared_layout_visible_to_other_user():
    """A shared layout appears in a colleague's list with is_mine=False."""
    tenant_id, _, owner_token = await _seed_tenant_and_token(role_id=3)
    _, other_token = await _seed_extra_user(tenant_id, role_id=4)
    async with await _authed(owner_token) as c:
        created = await _create_layout(c, "Control Room Standard", is_shared=True)
    async with await _authed(other_token) as c:
        r = await c.get("/api/v1/wall-layouts")
    rows = r.json()
    row = next(rw for rw in rows if rw["id"] == created["id"])
    assert row["is_mine"] is False
    assert row["owner_name"] == "Wall Tester"


@pytest.mark.asyncio
async def test_wal_private_layout_hidden_from_other_user():
    """A private layout does NOT appear in a colleague's list."""
    tenant_id, _, owner_token = await _seed_tenant_and_token(role_id=3)
    _, other_token = await _seed_extra_user(tenant_id, role_id=4)
    async with await _authed(owner_token) as c:
        created = await _create_layout(c, "My Private Wall", is_shared=False)
    async with await _authed(other_token) as c:
        r = await c.get("/api/v1/wall-layouts")
    assert all(row["id"] != created["id"] for row in r.json())


@pytest.mark.asyncio
async def test_wal_non_owner_cannot_update_or_delete_shared():
    """A non-owner, non-admin user gets 404 updating/deleting a shared layout."""
    tenant_id, _, owner_token = await _seed_tenant_and_token(role_id=3)
    _, other_token = await _seed_extra_user(tenant_id, role_id=4)
    async with await _authed(owner_token) as c:
        created = await _create_layout(c, "Locked Layout", is_shared=True)
    async with await _authed(other_token) as c:
        r_upd = await c.put(f"/api/v1/wall-layouts/{created['id']}", json={"name": "Hijacked"})
        r_del = await c.delete(f"/api/v1/wall-layouts/{created['id']}")
    assert r_upd.status_code == 404
    assert r_del.status_code == 404


@pytest.mark.asyncio
async def test_wal_admin_can_delete_any_layout():
    """Admin (role 2) can delete another user's layout."""
    tenant_id, _, admin_token = await _seed_tenant_and_token(role_id=2)
    _, guard_token = await _seed_extra_user(tenant_id, role_id=5)
    async with await _authed(guard_token) as c:
        created = await _create_layout(c, "Guard Layout", is_shared=True)
    async with await _authed(admin_token) as c:
        r = await c.delete(f"/api/v1/wall-layouts/{created['id']}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True


# ─── C. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wal_viewer_can_create_and_list():
    """Viewer (role 6) has camera:read → can save + list layouts."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        created = await _create_layout(c, "Viewer Wall")
        r = await c.get("/api/v1/wall-layouts")
    assert any(row["id"] == created["id"] for row in r.json())


@pytest.mark.asyncio
async def test_wal_unauth_401():
    """No token → 401."""
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    async with client as c:
        r = await c.get("/api/v1/wall-layouts")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_wal_rls_isolation():
    """Tenant B cannot see Tenant A's shared layouts."""
    _, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_a) as c:
        await _create_layout(c, "Tenant A Shared", is_shared=True)
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/wall-layouts")
    assert r.json() == []
