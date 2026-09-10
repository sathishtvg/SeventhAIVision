"""Gap 91 — Custom roles (isolated-tenant)

Tests the roles router, the /auth/me/permissions endpoint, custom-role
enforcement (a user assigned a custom role gets exactly its permissions), and
tenant isolation of custom roles.

Permission model note: after migration 0057, role:manage is granted to admin
(role 2) as well as super_admin (role 1). Supervisor (role 3) lacks it.

Sections:
  A — /me/permissions (2 tests)
  B — Roles list + catalogue + gate (3 tests)
  C — Custom role CRUD (5 tests)
  D — Enforcement + isolation (4 tests)
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


def _make_token(user_id, tenant_id, role_id):
    from app.core.security import create_access_token
    return create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_tenant_and_token(role_id: int = 2):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"rol-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Roles Test {slug}", "slug": slug},
        )
        await s.execute(
            text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                 "                   full_name, totp_enabled) "
                 "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'Roles Tester', CAST(:role AS smallint) = 1)"),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"rol-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return tenant_id, user_id, _make_token(user_id, tenant_id, role_id)


async def _authed(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


async def _create_role(c: AsyncClient, name: str, codes: list[str]) -> dict:
    r = await c.post("/api/v1/roles", json={"name": name, "permission_codes": codes})
    assert r.status_code == 201, f"create_role failed: {r.text}"
    return r.json()


# ─── A. /me/permissions ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rol_me_permissions_admin():
    """Admin's permission set includes role:manage (granted in 0057)."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/auth/me/permissions")
    assert r.status_code == 200
    body = r.json()
    assert body["role_id"] == 2
    assert "role:manage" in body["permissions"]
    assert "camera:read" in body["permissions"]


@pytest.mark.asyncio
async def test_rol_me_permissions_viewer():
    """Viewer's set is read-only — no camera:create."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/auth/me/permissions")
    perms = r.json()["permissions"]
    assert "camera:read" in perms
    assert "camera:create" not in perms


# ─── B. Roles list + catalogue + gate ────────────────────────────────────────

@pytest.mark.asyncio
async def test_rol_list_includes_builtins():
    """GET /roles lists the built-in roles with permission_codes + user_count."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/roles")
    assert r.status_code == 200
    rows = r.json()
    by_id = {row["id"]: row for row in rows}
    assert 1 in by_id and 2 in by_id and 6 in by_id
    assert by_id[1]["is_custom"] is False
    assert "camera:read" in by_id[2]["permission_codes"]
    # The seeded admin counts against role 2 in this tenant
    assert by_id[2]["user_count"] >= 1


@pytest.mark.asyncio
async def test_rol_permission_catalogue():
    """GET /roles/permissions returns the full catalogue with metadata."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/roles/permissions")
    rows = r.json()
    codes = {row["code"] for row in rows}
    assert "camera:read" in codes and "evidence:read" in codes
    sample = next(row for row in rows if row["code"] == "camera:read")
    assert "category" in sample and "description" in sample


@pytest.mark.asyncio
async def test_rol_supervisor_cannot_manage_403():
    """Supervisor (role 3) lacks role:manage → 403."""
    _, _, token = await _seed_tenant_and_token(role_id=3)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/roles")
    assert r.status_code == 403


# ─── C. Custom role CRUD ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rol_create_custom_role():
    """POST /roles → 201 with id>=100 and echoed permission codes."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        body = await _create_role(c, "Control Room Operator",
                                  ["camera:read", "alert:read", "alert:acknowledge"])
    assert body["id"] >= 100
    assert body["is_custom"] is True
    assert set(body["permission_codes"]) == {"camera:read", "alert:read", "alert:acknowledge"}


@pytest.mark.asyncio
async def test_rol_custom_role_appears_in_list():
    """Created custom role shows in GET /roles with is_custom + user_count=0."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_role(c, "Night Desk", ["camera:read"])
        r = await c.get("/api/v1/roles")
    row = next(x for x in r.json() if x["id"] == created["id"])
    assert row["is_custom"] is True
    assert row["user_count"] == 0
    assert row["permission_codes"] == ["camera:read"]


@pytest.mark.asyncio
async def test_rol_update_custom_role_permissions():
    """PUT replaces the permission set; GET reflects it."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_role(c, "Editable Role", ["camera:read"])
        r = await c.put(f"/api/v1/roles/{created['id']}",
                        json={"permission_codes": ["alert:read", "incident:read"]})
        assert r.status_code == 200
        rlist = await c.get("/api/v1/roles")
    row = next(x for x in rlist.json() if x["id"] == created["id"])
    assert set(row["permission_codes"]) == {"alert:read", "incident:read"}


@pytest.mark.asyncio
async def test_rol_delete_custom_role():
    """DELETE removes an unused custom role."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        created = await _create_role(c, "Temp Role", ["camera:read"])
        r = await c.delete(f"/api/v1/roles/{created['id']}")
        assert r.status_code == 200 and r.json()["deleted"] is True
        rlist = await c.get("/api/v1/roles")
    assert all(x["id"] != created["id"] for x in rlist.json())


@pytest.mark.asyncio
async def test_rol_create_unknown_permission_422():
    """A permission code that doesn't exist → 422."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/roles",
                         json={"name": "Bad", "permission_codes": ["camera:read", "not:a:perm"]})
    assert r.status_code == 422


# ─── D. Enforcement + isolation ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rol_custom_role_enforced_end_to_end():
    """A user assigned a custom role gets exactly that role's permissions."""
    tenant_id, _, admin_token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(admin_token) as c:
        role = await _create_role(c, "Camera Only", ["camera:read"])
        # Create a user holding the custom role
        ur = await c.post("/api/v1/users", json={
            "email": f"rol-{uuid.uuid4().hex[:8]}@test.local",
            "password": "orbit-lantern-quay-42", "role_id": role["id"], "full_name": "Custom User"})
        assert ur.status_code == 201, ur.text
        new_user_id = ur.json()["id"]
    user_token = _make_token(new_user_id, tenant_id, role["id"])
    async with await _authed(user_token) as c:
        perms = (await c.get("/api/v1/auth/me/permissions")).json()["permissions"]
        # Has the granted permission…
        cams = await c.get("/api/v1/cameras")
        # …and lacks one it wasn't granted (user:read)
        users = await c.get("/api/v1/users")
    assert perms == ["camera:read"]
    assert cams.status_code == 200
    assert users.status_code == 403


@pytest.mark.asyncio
async def test_rol_delete_role_in_use_409():
    """Deleting a custom role still assigned to a user → 409."""
    tenant_id, _, admin_token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(admin_token) as c:
        role = await _create_role(c, "Held Role", ["camera:read"])
        await c.post("/api/v1/users", json={
            "email": f"rol-{uuid.uuid4().hex[:8]}@test.local",
            "password": "orbit-lantern-quay-42", "role_id": role["id"]})
        r = await c.delete(f"/api/v1/roles/{role['id']}")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_rol_tenant_cannot_see_other_tenants_custom_role():
    """Tenant B's role list excludes Tenant A's custom role, and PUT/DELETE
    of it 404."""
    _, _, tok_a = await _seed_tenant_and_token(role_id=2)
    _, _, tok_b = await _seed_tenant_and_token(role_id=2)
    async with await _authed(tok_a) as c:
        role_a = await _create_role(c, "A Secret Role", ["camera:read"])
    async with await _authed(tok_b) as c:
        rlist = await c.get("/api/v1/roles")
        r_put = await c.put(f"/api/v1/roles/{role_a['id']}", json={"name": "hijack"})
        r_del = await c.delete(f"/api/v1/roles/{role_a['id']}")
    assert all(x["id"] != role_a["id"] for x in rlist.json())
    assert r_put.status_code == 404
    assert r_del.status_code == 404


@pytest.mark.asyncio
async def test_rol_cannot_assign_other_tenants_custom_role():
    """Creating a user with another tenant's custom role_id → 422."""
    _, _, tok_a = await _seed_tenant_and_token(role_id=2)
    _, _, tok_b = await _seed_tenant_and_token(role_id=2)
    async with await _authed(tok_a) as c:
        role_a = await _create_role(c, "A Role", ["camera:read"])
    async with await _authed(tok_b) as c:
        r = await c.post("/api/v1/users", json={
            "email": f"rol-{uuid.uuid4().hex[:8]}@test.local",
            "password": "orbit-lantern-quay-42", "role_id": role_a["id"]})
    assert r.status_code == 422
