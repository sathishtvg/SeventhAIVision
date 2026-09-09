"""A guard stands one post (isolated-tenant).

Tests migration 0101's trigger and the duty-assignment endpoints in
backend/app/routers/sites.py.

The rule, and why it has three parts:

  - Operator (4) and Security Guard (5) stand a twelve-hour post at ONE place.
    A second posting is a promise nobody can keep, and the auto-scheduler
    drawing from that team produces a roster that cannot be worked.
  - Supervisor (3), Manager (8) and the admin roles cover several sites by
    design. That is the job, not a violation.
  - Anyone flagged is_standby relieves across sites. The roster still puts them
    at one site at a time; this table only records where they may be sent.

"One posting" settles day-versus-night without a second rule: a constrained
guard cannot hold both shifts anywhere, because they cannot hold two of
anything.

Sections:
  A — The constraint (4 tests)
  B — Who is exempt (3 tests)
  C — The API's answer (3 tests)
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

# Module level, not lazy — see test_guardhouse_registers.py for why.
from app.main import app as _fastapi_app  # noqa: E402


def _app():
    return _fastapi_app


async def _exec(statements: list[tuple[str, dict]]):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            for sql, params in statements:
                await s.execute(text(sql), params)
            await s.commit()
    finally:
        await engine.dispose()


async def _seed_tenant(role_id: int = 2):
    from app.core.security import create_access_token

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    slug = f"dp-{tenant_id.hex[:10]}"
    await _exec([
        ("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)",
         {"id": tenant_id, "name": f"Duty Test {slug}", "slug": slug}),
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
         "VALUES (:id, :tid, :role, :email, 'hashed', 'DP Admin')",
         {"id": user_id, "tid": tenant_id, "role": role_id,
          "email": f"dp-{user_id.hex[:8]}@test.local"}),
    ])
    return tenant_id, user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _seed_user(tenant_id, role_id: int, name: str = "DP Guard",
                     is_standby: bool = False) -> uuid.UUID:
    user_id = uuid.uuid4()
    await _exec([
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
         " full_name, is_standby) "
         "VALUES (:id, :tid, :role, :email, 'hashed', :name, :standby)",
         {"id": user_id, "tid": tenant_id, "role": role_id, "name": name,
          "email": f"dp-{user_id.hex[:8]}@test.local", "standby": is_standby}),
    ])
    return user_id


async def _seed_site(tenant_id, name: str) -> uuid.UUID:
    site_id = uuid.uuid4()
    await _exec([
        ("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :name)",
         {"id": site_id, "tid": tenant_id, "name": name}),
    ])
    return site_id


async def _authed(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


async def _post(c: AsyncClient, site_id, guard_id, shift="day"):
    return await c.post(f"/api/v1/sites/{site_id}/duty-assignments",
                        json={"guard_user_id": str(guard_id), "shift_type": shift})


# ─── A. The constraint ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_guard_cannot_stand_a_post_at_two_sites():
    """The bug this fixes: the same officer was on three sites at once."""
    tenant_id, _, token = await _seed_tenant()
    site_a = await _seed_site(tenant_id, "Marina Bay Tower")
    site_b = await _seed_site(tenant_id, "Jurong Logistics Hub")
    guard = await _seed_user(tenant_id, 5, "Tan Wei Ming")

    async with await _authed(token) as c:
        assert (await _post(c, site_a, guard, "day")).status_code == 201
        r = await _post(c, site_b, guard, "night")
        assert r.status_code == 409
        # The message has to name where they already are, or somebody goes
        # hunting through every site to find out.
        assert "Marina Bay Tower" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_guard_cannot_hold_day_and_night_at_one_site():
    """Twelve plus twelve is a twenty-four hour day. One posting settles it
    without needing a rule of its own."""
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id, "Marina Bay Tower")
    guard = await _seed_user(tenant_id, 5)

    async with await _authed(token) as c:
        assert (await _post(c, site_id, guard, "day")).status_code == 201
        assert (await _post(c, site_id, guard, "night")).status_code == 409


@pytest.mark.asyncio
async def test_operators_are_constrained_too():
    """A control-room operator standing a post is standing a post."""
    tenant_id, _, token = await _seed_tenant()
    site_a = await _seed_site(tenant_id, "A")
    site_b = await _seed_site(tenant_id, "B")
    operator = await _seed_user(tenant_id, 4, "Control Room")

    async with await _authed(token) as c:
        assert (await _post(c, site_a, operator)).status_code == 201
        assert (await _post(c, site_b, operator)).status_code == 409


@pytest.mark.asyncio
async def test_freeing_the_post_lets_them_take_another():
    """The rule is one AT A TIME, not one ever — guards move between sites."""
    tenant_id, _, token = await _seed_tenant()
    site_a = await _seed_site(tenant_id, "A")
    site_b = await _seed_site(tenant_id, "B")
    guard = await _seed_user(tenant_id, 5)

    async with await _authed(token) as c:
        first = (await _post(c, site_a, guard)).json()
        assert (await _post(c, site_b, guard)).status_code == 409

        r = await c.delete(f"/api/v1/sites/{site_a}/duty-assignments/{first['id']}")
        assert r.status_code == 200
        assert (await _post(c, site_b, guard)).status_code == 201


# ─── B. Who is exempt ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_supervisor_covers_several_sites():
    """Covering sites is what a supervisor is for. In the database that
    prompted this, the Supervisor on two sites was the one row that was
    RIGHT."""
    tenant_id, _, token = await _seed_tenant()
    site_a = await _seed_site(tenant_id, "A")
    site_b = await _seed_site(tenant_id, "B")
    supervisor = await _seed_user(tenant_id, 3, "David Lim")

    async with await _authed(token) as c:
        assert (await _post(c, site_a, supervisor, "day")).status_code == 201
        assert (await _post(c, site_b, supervisor, "night")).status_code == 201
        # And both shifts at one site, since they are not standing either.
        assert (await _post(c, site_a, supervisor, "night")).status_code == 201


@pytest.mark.asyncio
async def test_a_manager_covers_several_sites():
    tenant_id, _, token = await _seed_tenant()
    site_a = await _seed_site(tenant_id, "A")
    site_b = await _seed_site(tenant_id, "B")
    manager = await _seed_user(tenant_id, 8, "Ops Manager")

    async with await _authed(token) as c:
        assert (await _post(c, site_a, manager)).status_code == 201
        assert (await _post(c, site_b, manager)).status_code == 201


@pytest.mark.asyncio
async def test_a_standby_guard_relieves_across_sites():
    """Standby is the escape hatch the refusal message points at."""
    tenant_id, _, token = await _seed_tenant()
    site_a = await _seed_site(tenant_id, "A")
    site_b = await _seed_site(tenant_id, "B")
    relief = await _seed_user(tenant_id, 5, "Relief Officer", is_standby=True)

    async with await _authed(token) as c:
        assert (await _post(c, site_a, relief, "day")).status_code == 201
        assert (await _post(c, site_b, relief, "night")).status_code == 201


# ─── C. The API's answer ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_marking_a_guard_standby_frees_them_immediately():
    """The refusal tells the operator to mark them standby, so that has to
    actually work from the users endpoint."""
    tenant_id, _, token = await _seed_tenant()
    site_a = await _seed_site(tenant_id, "A")
    site_b = await _seed_site(tenant_id, "B")
    guard = await _seed_user(tenant_id, 5)

    async with await _authed(token) as c:
        assert (await _post(c, site_a, guard)).status_code == 201
        assert (await _post(c, site_b, guard)).status_code == 409

        r = await c.put(f"/api/v1/users/{guard}", json={"is_standby": True})
        assert r.status_code == 200
        assert (await _post(c, site_b, guard)).status_code == 201


@pytest.mark.asyncio
async def test_re_adding_to_the_same_team_stays_a_no_op():
    """The ON CONFLICT path predates this rule and must survive it: adding
    somebody already on the team updates the note rather than erroring."""
    tenant_id, _, token = await _seed_tenant()
    site_id = await _seed_site(tenant_id, "A")
    guard = await _seed_user(tenant_id, 5)

    async with await _authed(token) as c:
        first = (await _post(c, site_id, guard, "day")).json()
        r = await c.post(f"/api/v1/sites/{site_id}/duty-assignments", json={
            "guard_user_id": str(guard), "shift_type": "day", "notes": "Gate 2",
        })
        assert r.status_code == 201
        assert r.json()["id"] == first["id"]
        assert r.json()["notes"] == "Gate 2"


@pytest.mark.asyncio
async def test_postings_endpoint_reports_who_stands_where():
    """The duty page uses this to stop offering an officer it cannot post."""
    tenant_id, _, token = await _seed_tenant()
    site_a = await _seed_site(tenant_id, "Marina Bay Tower")
    site_b = await _seed_site(tenant_id, "Jurong Logistics Hub")
    guard = await _seed_user(tenant_id, 5, "Tan Wei Ming")
    supervisor = await _seed_user(tenant_id, 3, "David Lim")

    async with await _authed(token) as c:
        await _post(c, site_a, guard, "day")
        await _post(c, site_a, supervisor, "day")
        await _post(c, site_b, supervisor, "night")

        rows = (await c.get("/api/v1/sites/duty-assignments/postings")).json()
        by_guard = {}
        for row in rows:
            by_guard.setdefault(row["guard_user_id"], []).append(row)

        assert len(by_guard[str(guard)]) == 1
        assert len(by_guard[str(supervisor)]) == 2
        # Enough to decide exemption without a second lookup.
        assert by_guard[str(supervisor)][0]["role_id"] == 3
        assert by_guard[str(guard)][0]["is_standby"] is False
        assert {r["site_name"] for r in rows} == {"Marina Bay Tower", "Jurong Logistics Hub"}
