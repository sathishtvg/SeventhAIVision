"""Gap 87 — Post orders / site documents (isolated-tenant)

Tests backend/app/routers/post_orders.py.

Key semantics:
  - Acks are per version; editing title/body bumps version and invalidates
    earlier acknowledgments
  - DELETE is soft (is_active=FALSE) — ack history is compliance data
  - shift:read to read/acknowledge; shift:manage (roles 1-3) to author
  - Site scoping (Gap 81): site-assigned guards see only their sites' orders
  - /acks compliance view: acknowledged list + pending site-assigned users

Sections:
  A — CRUD (5 tests)
  B — Acknowledgments + versioning (5 tests)
  C — Compliance view (2 tests)
  D — Permissions + scoping + RLS (4 tests)
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
    slug = f"pod-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"PostOrder Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'PO Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"pod-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_extra_user(tenant_id: uuid.UUID, role_id: int, name: str = "PO Guard"):
    from app.core.security import create_access_token

    user_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', :name, CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id, "name": name,
             "email": f"pod-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_site(tenant_id: uuid.UUID, name: str = "PO Site") -> uuid.UUID:
    site_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :name)"),
            {"id": site_id, "tid": tenant_id, "name": name},
        )
        await s.commit()
    await engine.dispose()
    return site_id


async def _assign_site(tenant_id: uuid.UUID, user_id: uuid.UUID, site_id: uuid.UUID):
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:uid, :sid, :tid)"),
            {"uid": user_id, "sid": site_id, "tid": tenant_id},
        )
        await s.commit()
    await engine.dispose()


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_order(c: AsyncClient, site_id, title: str = "Access Procedure",
                        body_text: str = "1. Check ID. 2. Log entry.",
                        category: str = "access") -> dict:
    r = await c.post("/api/v1/post-orders", json={
        "site_id": str(site_id), "title": title, "body": body_text, "category": category,
    })
    assert r.status_code == 201, f"create_order failed: {r.text}"
    return r.json()


# ─── A. CRUD ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pod_list_empty():
    """Fresh tenant has no post orders."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/post-orders")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_pod_create_returns_fields():
    """POST → 201 with version=1 and all fields."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        body = await _create_order(c, site_id)
    for field in ("id", "site_id", "title", "body", "category", "version",
                  "is_active", "requires_acknowledgment", "created_at"):
        assert field in body, f"Missing field: {field}"
    assert body["version"] == 1
    assert body["category"] == "access"


@pytest.mark.asyncio
async def test_pod_invalid_category_and_unknown_site():
    """Bad category → 422; unknown site → 404."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        r_cat = await c.post("/api/v1/post-orders", json={
            "site_id": str(site_id), "title": "X", "body": "Y", "category": "nonsense"})
        r_site = await c.post("/api/v1/post-orders", json={
            "site_id": str(uuid.uuid4()), "title": "X", "body": "Y"})
    assert r_cat.status_code == 422
    assert r_site.status_code == 404


@pytest.mark.asyncio
async def test_pod_body_edit_bumps_version():
    """Editing body bumps version; toggling requires_acknowledgment does not."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        created = await _create_order(c, site_id)
        r1 = await c.put(f"/api/v1/post-orders/{created['id']}",
                         json={"body": "Updated procedure."})
        assert r1.json()["version"] == 2
        r2 = await c.put(f"/api/v1/post-orders/{created['id']}",
                         json={"requires_acknowledgment": False})
        assert r2.json()["version"] == 2  # unchanged


@pytest.mark.asyncio
async def test_pod_soft_delete():
    """DELETE deactivates; row still retrievable, excluded from default list."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        created = await _create_order(c, site_id)
        r_del = await c.delete(f"/api/v1/post-orders/{created['id']}")
        assert r_del.status_code == 200
        assert r_del.json()["is_active"] is False
        r_list = await c.get("/api/v1/post-orders")
        r_list_all = await c.get("/api/v1/post-orders?include_inactive=true")
        r_get = await c.get(f"/api/v1/post-orders/{created['id']}")
    assert all(row["id"] != created["id"] for row in r_list.json())
    assert any(row["id"] == created["id"] for row in r_list_all.json())
    assert r_get.status_code == 200  # still readable (compliance history)


# ─── B. Acknowledgments + versioning ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_pod_acknowledge_sets_flag():
    """POST /acknowledge → acknowledged=True in list and detail."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        created = await _create_order(c, site_id)
        r_before = await c.get(f"/api/v1/post-orders/{created['id']}")
        assert r_before.json()["acknowledged"] is False
        r_ack = await c.post(f"/api/v1/post-orders/{created['id']}/acknowledge")
        assert r_ack.status_code == 200
        assert r_ack.json()["version"] == 1
        r_after = await c.get(f"/api/v1/post-orders/{created['id']}")
    assert r_after.json()["acknowledged"] is True


@pytest.mark.asyncio
async def test_pod_acknowledge_idempotent():
    """Acknowledging twice is a no-op, not an error."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        created = await _create_order(c, site_id)
        r1 = await c.post(f"/api/v1/post-orders/{created['id']}/acknowledge")
        r2 = await c.post(f"/api/v1/post-orders/{created['id']}/acknowledge")
    assert r1.status_code == 200
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_pod_version_bump_invalidates_ack():
    """After a body edit, the earlier acknowledgment no longer counts."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        created = await _create_order(c, site_id)
        await c.post(f"/api/v1/post-orders/{created['id']}/acknowledge")
        await c.put(f"/api/v1/post-orders/{created['id']}",
                    json={"body": "New rules — re-read required."})
        r = await c.get(f"/api/v1/post-orders/{created['id']}")
        assert r.json()["acknowledged"] is False
        # Re-acknowledge the new version
        r_ack = await c.post(f"/api/v1/post-orders/{created['id']}/acknowledge")
        assert r_ack.json()["version"] == 2
        r2 = await c.get(f"/api/v1/post-orders/{created['id']}")
    assert r2.json()["acknowledged"] is True


@pytest.mark.asyncio
async def test_pod_acknowledge_inactive_conflict():
    """Acknowledging a deactivated post order → 409."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        created = await _create_order(c, site_id)
        await c.delete(f"/api/v1/post-orders/{created['id']}")
        r = await c.post(f"/api/v1/post-orders/{created['id']}/acknowledge")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_pod_acknowledge_unknown_404():
    """Acknowledging a nonexistent post order → 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/post-orders/{uuid.uuid4()}/acknowledge")
    assert r.status_code == 404


# ─── C. Compliance view ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pod_acks_lists_acknowledged_and_pending():
    """/acks shows who acknowledged and which site-assigned guards haven't."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    acked_guard_id, acked_token = await _seed_extra_user(tenant_id, 5, "Acked Guard")
    pending_guard_id, _ = await _seed_extra_user(tenant_id, 5, "Pending Guard")
    await _assign_site(tenant_id, acked_guard_id, site_id)
    await _assign_site(tenant_id, pending_guard_id, site_id)
    async with await _authed(admin_token) as c:
        created = await _create_order(c, site_id)
    async with await _authed(acked_token) as c:
        await c.post(f"/api/v1/post-orders/{created['id']}/acknowledge")
    async with await _authed(admin_token) as c:
        r = await c.get(f"/api/v1/post-orders/{created['id']}/acks")
    body = r.json()
    assert body["current_version"] == 1
    assert [a["full_name"] for a in body["acknowledged"]] == ["Acked Guard"]
    assert [p["full_name"] for p in body["pending"]] == ["Pending Guard"]


@pytest.mark.asyncio
async def test_pod_acks_pending_clears_after_ack():
    """Pending list empties once everyone assigned has acknowledged."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    guard_id, guard_token = await _seed_extra_user(tenant_id, 5)
    await _assign_site(tenant_id, guard_id, site_id)
    async with await _authed(admin_token) as c:
        created = await _create_order(c, site_id)
    async with await _authed(guard_token) as c:
        await c.post(f"/api/v1/post-orders/{created['id']}/acknowledge")
    async with await _authed(admin_token) as c:
        r = await c.get(f"/api/v1/post-orders/{created['id']}/acks")
    assert r.json()["pending"] == []


# ─── D. Permissions + scoping + RLS ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_pod_guard_can_read_and_ack_but_not_create():
    """Guard (role 5): list 200, acknowledge 200; create → 403 (shift:manage)."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    _, guard_token = await _seed_extra_user(tenant_id, 5)
    async with await _authed(admin_token) as c:
        created = await _create_order(c, site_id)
    async with await _authed(guard_token) as c:
        r_list = await c.get("/api/v1/post-orders")
        r_ack = await c.post(f"/api/v1/post-orders/{created['id']}/acknowledge")
        r_create = await c.post("/api/v1/post-orders", json={
            "site_id": str(site_id), "title": "Nope", "body": "Nope"})
    assert r_list.status_code == 200
    assert r_ack.status_code == 200
    assert r_create.status_code == 403


@pytest.mark.asyncio
async def test_pod_acks_view_requires_manage():
    """Guard (role 5) cannot open the /acks compliance view → 403."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    _, guard_token = await _seed_extra_user(tenant_id, 5)
    async with await _authed(admin_token) as c:
        created = await _create_order(c, site_id)
    async with await _authed(guard_token) as c:
        r = await c.get(f"/api/v1/post-orders/{created['id']}/acks")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_pod_site_scoped_guard_sees_only_assigned_site():
    """Guard assigned to Site A sees only Site A's post orders; Site B's
    detail returns 404 (Gap 81 scoping)."""
    tenant_id, _, admin_token = await _seed_tenant_and_token()
    site_a = await _seed_site(tenant_id, "PO Site A")
    site_b = await _seed_site(tenant_id, "PO Site B")
    guard_id, guard_token = await _seed_extra_user(tenant_id, 5)
    await _assign_site(tenant_id, guard_id, site_a)
    async with await _authed(admin_token) as c:
        order_a = await _create_order(c, site_a, title="A Orders")
        order_b = await _create_order(c, site_b, title="B Orders")
    async with await _authed(guard_token) as c:
        r_list = await c.get("/api/v1/post-orders")
        r_b = await c.get(f"/api/v1/post-orders/{order_b['id']}")
    titles = [o["title"] for o in r_list.json()]
    assert titles == ["A Orders"]
    assert r_b.status_code == 404


@pytest.mark.asyncio
async def test_pod_rls_isolation():
    """Tenant B cannot see Tenant A's post orders."""
    tenant_a, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    site_a = await _seed_site(tenant_a)
    async with await _authed(tok_a) as c:
        await _create_order(c, site_a)
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/post-orders")
    assert r.json() == []
