"""Multi-screen Live Wall profiles (isolated-tenant)

Tests backend/app/routers/wall_profiles.py.

Semantics:
  - A profile groups N screens; each screen is a wall_layouts row carrying
    profile_id + screen_index + analytics_modules
  - GET lists my profiles + tenant-shared ones, screens nested by screen_index
  - POST 201; duplicate name for same owner → 409
  - POST /{id}/screens appends one screen and bumps screen_count
  - PUT/DELETE/add-screen: owner or roles 1-2 only; others → 404
  - Permission gate camera:read (same as wall_layouts — display config)
  - Deleting a profile cascades its child wall_layouts rows
  - Profile screens must NOT leak into the standalone /wall-layouts list

Sections:
  A — CRUD + screens (6 tests)
  B — Sharing semantics (4 tests)
  C — Permissions + RLS + isolation (5 tests)
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
    slug = f"wpr-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Profile Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'Profile Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"wpr-{user_id.hex[:8]}@test.local"},
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
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'Second Profile User', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"wpr-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _cells(name: str = "Lobby Cam"):
    return [{"camera_id": str(uuid.uuid4()), "stream_id": str(uuid.uuid4()),
             "camera_name": name, "site_name": "HQ"}]


def _screen(grid_size: int = 4, modules=None, cam: str = "Lobby Cam"):
    return {"grid_size": grid_size, "cells": _cells(cam),
            "analytics_modules": modules if modules is not None else []}


async def _create_profile(c: AsyncClient, name: str = "Control Room",
                          is_shared: bool = False, screens=None) -> dict:
    r = await c.post("/api/v1/wall-profiles", json={
        "name": name,
        "is_shared": is_shared,
        "screens": screens if screens is not None else [
            _screen(4, ["intrusion"], "Gate Cam"),
            _screen(9, [], "Perimeter Cam"),
        ],
    })
    assert r.status_code == 201, f"create_profile failed: {r.text}"
    return r.json()


# ─── A. CRUD + screens ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wpr_list_empty_fresh_tenant():
    """GET /wall-profiles on a fresh tenant returns 200 + []."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/wall-profiles")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_wpr_create_returns_profile_with_nested_screens():
    """POST returns the profile plus its screens ordered by screen_index.

    Regression guard: create commits and then re-reads the profile. Because
    app.current_tenant is a transaction-scoped GUC, the commit clears it and
    the read-back previously blew up with `invalid input syntax for type
    uuid: ""` from the RLS policy. A 201 with populated screens proves the
    tenant context is restored before the re-read.
    """
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        body = await _create_profile(c, "Night Ops")

    for field in ("id", "user_id", "name", "screen_count", "is_shared",
                  "screens", "created_at", "updated_at"):
        assert field in body, f"Missing field: {field}"
    assert body["name"] == "Night Ops"
    assert body["screen_count"] == 2
    assert len(body["screens"]) == 2
    assert [s["screen_index"] for s in body["screens"]] == [0, 1]
    assert body["screens"][0]["grid_size"] == 4
    assert body["screens"][0]["analytics_modules"] == ["intrusion"]
    assert body["screens"][1]["grid_size"] == 9
    assert body["screens"][0]["cells"][0]["camera_name"] == "Gate Cam"


@pytest.mark.asyncio
async def test_wpr_created_profile_appears_in_list_with_is_mine():
    """Created profile appears in GET list flagged is_mine=True + owner_name."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = await _create_profile(c)
        r = await c.get("/api/v1/wall-profiles")
    row = next(rw for rw in r.json() if rw["id"] == created["id"])
    assert row["is_mine"] is True
    assert row["owner_name"] == "Profile Tester"
    assert len(row["screens"]) == 2


@pytest.mark.asyncio
async def test_wpr_duplicate_name_same_owner_conflicts():
    """Second profile with the same name for the same owner → 409."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await _create_profile(c, "Main Wall")
        r = await c.post("/api/v1/wall-profiles", json={
            "name": "Main Wall", "is_shared": False, "screens": [_screen()],
        })
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_wpr_add_screen_appends_and_bumps_count():
    """POST /{id}/screens appends one screen and increments screen_count.

    This is the "need more screen later" path — existing screens must survive.
    """
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = await _create_profile(c, "Growable")
        original_ids = [s["id"] for s in created["screens"]]
        r = await c.post(f"/api/v1/wall-profiles/{created['id']}/screens",
                         json=_screen(1, ["face"], "Added Cam"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["screen_count"] == 3
    assert len(body["screens"]) == 3
    assert [s["screen_index"] for s in body["screens"]] == [0, 1, 2]
    # pre-existing screens untouched
    assert original_ids == [s["id"] for s in body["screens"][:2]]
    assert body["screens"][2]["cells"][0]["camera_name"] == "Added Cam"
    assert body["screens"][2]["analytics_modules"] == ["face"]


@pytest.mark.asyncio
async def test_wpr_delete_cascades_child_screens():
    """DELETE removes the profile and its child wall_layouts rows."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = await _create_profile(c, "Disposable")
        screen_ids = [s["id"] for s in created["screens"]]
        r = await c.delete(f"/api/v1/wall-profiles/{created['id']}")
        assert r.status_code == 200
        listing = await c.get("/api/v1/wall-profiles")
    assert all(p["id"] != created["id"] for p in listing.json())

    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        remaining = (await s.execute(
            text("SELECT count(*) FROM wall_layouts WHERE id = ANY(:ids)"),
            {"ids": [uuid.UUID(i) for i in screen_ids]},
        )).scalar()
    await engine.dispose()
    assert remaining == 0, "child screens should cascade-delete with the profile"


# ─── B. Sharing semantics ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wpr_shared_profile_visible_to_other_user():
    """is_shared=True profile is visible to another user in the same tenant."""
    tenant_id, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = await _create_profile(c, "Shared Wall", is_shared=True)
    _, other_token = await _seed_extra_user(tenant_id, role_id=4)
    async with await _authed(other_token) as c2:
        r = await c2.get("/api/v1/wall-profiles")
    row = next((rw for rw in r.json() if rw["id"] == created["id"]), None)
    assert row is not None, "shared profile should be visible tenant-wide"
    assert row["is_mine"] is False
    assert row["owner_name"] == "Profile Tester"


@pytest.mark.asyncio
async def test_wpr_private_profile_hidden_from_other_user():
    """is_shared=False profile is invisible to another user in the same tenant."""
    tenant_id, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = await _create_profile(c, "Private Wall", is_shared=False)
    _, other_token = await _seed_extra_user(tenant_id, role_id=4)
    async with await _authed(other_token) as c2:
        r = await c2.get("/api/v1/wall-profiles")
    assert all(rw["id"] != created["id"] for rw in r.json())


@pytest.mark.asyncio
async def test_wpr_non_owner_cannot_delete_or_add_screen():
    """A non-owner, non-admin gets 404 on delete and add-screen."""
    tenant_id, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = await _create_profile(c, "Owner Only", is_shared=True)
    _, other_token = await _seed_extra_user(tenant_id, role_id=4)
    async with await _authed(other_token) as c2:
        d = await c2.delete(f"/api/v1/wall-profiles/{created['id']}")
        a = await c2.post(f"/api/v1/wall-profiles/{created['id']}/screens", json=_screen())
    assert d.status_code == 404
    assert a.status_code == 404


@pytest.mark.asyncio
async def test_wpr_admin_can_delete_any_profile():
    """Role 2 (admin) can delete a profile they don't own."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=4)
    async with await _authed(token) as c:
        created = await _create_profile(c, "Operator Wall")
    _, admin_token = await _seed_extra_user(tenant_id, role_id=2)
    async with await _authed(admin_token) as c2:
        r = await c2.delete(f"/api/v1/wall-profiles/{created['id']}")
    assert r.status_code == 200


# ─── C. Permissions + RLS + isolation ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_wpr_unauthenticated_rejected():
    """No bearer token → 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/wall-profiles")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_wpr_viewer_can_create_and_list():
    """Gate is camera:read, so a viewer (role 6) can save a screen profile."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        created = await _create_profile(c, "Viewer Wall")
        r = await c.get("/api/v1/wall-profiles")
    assert any(rw["id"] == created["id"] for rw in r.json())


@pytest.mark.asyncio
async def test_wpr_rls_isolation_across_tenants():
    """Tenant A never sees tenant B's profiles, even shared ones."""
    _, _, token_a = await _seed_tenant_and_token()
    async with await _authed(token_a) as ca:
        created_a = await _create_profile(ca, "Tenant A Wall", is_shared=True)

    _, _, token_b = await _seed_tenant_and_token()
    async with await _authed(token_b) as cb:
        listing = await cb.get("/api/v1/wall-profiles")
        direct = await cb.delete(f"/api/v1/wall-profiles/{created_a['id']}")
    assert all(rw["id"] != created_a["id"] for rw in listing.json())
    assert direct.status_code == 404


@pytest.mark.asyncio
async def test_wpr_screens_do_not_leak_into_standalone_layout_list():
    """A profile's screens are wall_layouts rows, but must not show up in the
    standalone /wall-layouts list — that list is only for non-profile layouts."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        created = await _create_profile(c, "Hidden Screens")
        r = await c.get("/api/v1/wall-layouts")
    assert r.status_code == 200
    screen_ids = {s["id"] for s in created["screens"]}
    returned_ids = {row["id"] for row in r.json()}
    assert not (screen_ids & returned_ids), "profile screens leaked into wall-layouts list"


@pytest.mark.asyncio
async def test_wpr_rejects_more_than_max_screens():
    """Creating a profile with more than 8 screens is rejected by validation."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/wall-profiles", json={
            "name": "Too Many", "is_shared": False,
            "screens": [_screen() for _ in range(9)],
        })
    assert r.status_code == 422
