"""A tenant Admin must not be able to reach the platform admin sharing its tenant.

The hole these cover: authorization here has two layers and neither ranks
roles. RLS scopes `users` to a tenant, and require_permission asks only
"does your role hold this code" — so a tenant Admin holding user:update could
act on a role-1 user in the same tenant. The worst path was

    PUT /api/v1/users/{platform_admin_id}  {"new_password": "..."}

which returned 200 and let the Admin log straight in as the platform admin.
From there `tenants.py`'s get_raw_db bypasses RLS entirely, which is every
tenant on the installation. `_assert_assignable_role` already blocked
*becoming* role 1; this was the shorter route to the same place.

Role ids are deliberately compared against the one constant rather than by
arithmetic: 1..7 reads like a seniority order right up to 8 = Manager, which
the roster service treats as senior to Supervisor (3).
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


def _app():
    from app.main import app
    return app


async def _seed_tenant_with_platform_admin():
    """One tenant holding both a role-2 Admin and a role-1 platform admin.

    That pairing is the whole point — it is exactly the shape of the live demo
    data, and RLS does nothing about it because both rows share a tenant.
    """
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    platform_id = uuid.uuid4()
    slug = f"pag-{tenant_id.hex[:10]}"

    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Guard Test {slug}", "slug": slug},
        )
        for uid, role, label in (
            (admin_id, 2, "Tenant Admin"),
            (platform_id, 1, "Platform Admin"),
        ):
            await s.execute(
                text(
                    "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                    "VALUES (:id, :tid, :role, :email, 'hashed', :name)"
                ),
                {"id": uid, "tid": tenant_id, "role": role, "name": label,
                 "email": f"{label.split()[0].lower()}-{uid.hex[:8]}@test.local"},
            )
        await s.commit()
    await engine.dispose()

    return {
        "tenant_id": tenant_id,
        "admin_id": admin_id,
        "platform_id": platform_id,
        "admin_token": create_access_token(str(admin_id), str(tenant_id), 2),
        "platform_token": create_access_token(str(platform_id), str(tenant_id), 1),
    }


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. The Admin cannot see it ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pag_admin_list_omits_platform_admin():
    """GET /users hides role-1 rows from a tenant Admin — including the count.

    Reported from the Organization Hierarchy panel, which was printing the
    platform admin's own name to every viewer holding user:read.
    """
    env = await _seed_tenant_with_platform_admin()
    async with await _authed(env["admin_token"]) as c:
        r = await c.get("/api/v1/users")
    assert r.status_code == 200
    body = r.json()
    assert all(u["role_id"] != 1 for u in body)
    assert str(env["platform_id"]) not in [u["id"] for u in body]
    # Its own row is still there — this hides one role, not the whole page.
    assert str(env["admin_id"]) in [u["id"] for u in body]


@pytest.mark.asyncio
async def test_pag_admin_get_platform_admin_is_404():
    """404, not 403 — a 403 confirms the account exists, which is the fact
    being withheld."""
    env = await _seed_tenant_with_platform_admin()
    async with await _authed(env["admin_token"]) as c:
        r = await c.get(f"/api/v1/users/{env['platform_id']}")
    assert r.status_code == 404


# ─── B. The Admin cannot take it over ────────────────────────────────────────

@pytest.mark.asyncio
async def test_pag_admin_cannot_reset_platform_admin_password():
    """The full-takeover path: set the password, then log in as them."""
    env = await _seed_tenant_with_platform_admin()
    async with await _authed(env["admin_token"]) as c:
        r = await c.put(f"/api/v1/users/{env['platform_id']}",
                        json={"new_password": "takeover-attempt-123"})
    assert r.status_code == 404

    # And the stored hash is untouched, not merely the response refused.
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        stored = (await s.execute(
            text("SELECT hashed_password FROM users WHERE id = :id"),
            {"id": env["platform_id"]},
        )).scalar_one()
    await engine.dispose()
    assert stored == "hashed"


@pytest.mark.asyncio
async def test_pag_admin_cannot_demote_platform_admin():
    """Assigning role 1 was already blocked; demoting FROM it was not."""
    env = await _seed_tenant_with_platform_admin()
    async with await _authed(env["admin_token"]) as c:
        r = await c.put(f"/api/v1/users/{env['platform_id']}", json={"role_id": 2})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_pag_admin_cannot_deactivate_platform_admin():
    """Deactivation locked the platform admin out of their own installation."""
    env = await _seed_tenant_with_platform_admin()
    async with await _authed(env["admin_token"]) as c:
        r = await c.delete(f"/api/v1/users/{env['platform_id']}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_pag_admin_cannot_read_platform_admin_subresources():
    """Same hole by another door — documents and site scoping are per-user too."""
    env = await _seed_tenant_with_platform_admin()
    async with await _authed(env["admin_token"]) as c:
        docs = await c.get(f"/api/v1/users/{env['platform_id']}/documents")
        sites = await c.get(f"/api/v1/users/{env['platform_id']}/sites")
    assert docs.status_code == 404
    assert sites.status_code == 404


# ─── C. The platform admin is not locked out of itself ───────────────────────

@pytest.mark.asyncio
async def test_pag_platform_admin_still_sees_and_edits_itself():
    """The guard must not fire on the role it protects, or it is a lockout.

    Through /users/me now, not /users/{id}. Migration 0102 cut Super Admin to
    the four permissions that are the platform's own job, and user:read and
    user:update are not among them — they are permissions to act on OTHER
    people, and a platform operator has no business in a customer's staff list.

    Reading and editing YOURSELF is a different thing, and it is what
    /users/me is for. This test failing is what showed that the endpoint had to
    exist: without it, narrowing the role would have left a Super Admin unable
    to change its own password.
    """
    env = await _seed_tenant_with_platform_admin()
    async with await _authed(env["platform_token"]) as c:
        fetched = await c.get("/api/v1/users/me")
        renamed = await c.put("/api/v1/users/me", json={"full_name": "Renamed By Self"})
        # The management routes stay shut, which is the point of 0102.
        listing = await c.get("/api/v1/users")

    assert fetched.status_code == 200
    assert fetched.json()["id"] == str(env["platform_id"])
    assert renamed.status_code == 200
    assert renamed.json()["full_name"] == "Renamed By Self"
    assert listing.status_code == 403


@pytest.mark.asyncio
async def test_pag_admin_can_still_manage_ordinary_users():
    """The guard is one role wide. An Admin's normal work is unaffected."""
    env = await _seed_tenant_with_platform_admin()
    async with await _authed(env["admin_token"]) as c:
        created = await c.post("/api/v1/users", json={
            "email": f"ordinary-{uuid.uuid4().hex[:8]}@example.com",
            "password": "Secret123!",
            "role_id": 4,
            "full_name": "Ordinary Operator",
        })
        assert created.status_code == 201
        target = created.json()["id"]

        fetched = await c.get(f"/api/v1/users/{target}")
        updated = await c.put(f"/api/v1/users/{target}", json={"full_name": "Renamed"})
        removed = await c.delete(f"/api/v1/users/{target}")

    assert fetched.status_code == 200
    assert updated.status_code == 200
    assert removed.status_code == 200
