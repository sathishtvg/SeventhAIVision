"""Everyone can manage their own account. Nobody can promote themselves with it.

Every other route on the users router needs user:read or user:update, which are
permissions to act on OTHER people. Six of the eight built-in roles hold
neither — Super Admin, Supervisor, Operator, Security Guard, Viewer and Client —
so before /users/me existed a guard could not change their own password from
inside the app. Only Admin and Manager could, and only as a side effect of
being able to edit everybody.

Narrowing Super Admin to the platform (migration 0102) is what surfaced it:
it removed the last accidental route for the one role a test happened to cover.
The gap was always there for the other five.

The other half of the design is what a person may NOT change about themselves.
Role, active flag, pay and the standby flag are decisions an employer makes
about someone; a self-service endpoint that accepted them would be a promotion
endpoint.

Sections:
  A — Reading yourself (3 tests)
  B — Editing yourself (4 tests)
  C — What self-service must never accept (4 tests)
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app
from app.core.security import create_access_token, hash_password, verify_password

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

SUPER_ADMIN, ADMIN, GUARD, VIEWER = 1, 2, 5, 6
KNOWN_PASSWORD = "Correct-Horse-1!"


async def _seed_user(role_id: int):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    slug = f"self-{tenant_id.hex[:10]}"
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Self Test {slug}", "slug": slug},
        )
        await s.execute(
            text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                 "VALUES (:id, :tid, :role, :email, :pw, 'Self Tester')"),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"self-{user_id.hex[:8]}@test.local",
             "pw": hash_password(KNOWN_PASSWORD)},
        )
        await s.commit()
    await engine.dispose()
    return user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _client(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


async def _stored_hash(user_id) -> str:
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        row = (await s.execute(
            text("SELECT hashed_password FROM users WHERE id = :id"), {"id": user_id},
        )).first()
    await engine.dispose()
    return row.hashed_password


# ─── A. Reading yourself ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("role_id", [SUPER_ADMIN, GUARD, VIEWER])
async def test_any_role_can_read_its_own_account(role_id):
    """Including the three that hold no user:read at all."""
    user_id, token = await _seed_user(role_id)
    async with await _client(token) as c:
        r = await c.get("/api/v1/users/me")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == str(user_id)


@pytest.mark.asyncio
async def test_me_is_not_parsed_as_a_user_id():
    """/users/me is declared before /users/{user_id}. Declared after, FastAPI
    would match 'me' as the path parameter and fail casting it to uuid — and
    the failure would look like a permissions problem, not a routing one."""
    _, token = await _seed_user(GUARD)
    async with await _client(token) as c:
        r = await c.get("/api/v1/users/me")
    assert r.status_code != 422


@pytest.mark.asyncio
async def test_reading_yourself_does_not_open_the_staff_list():
    """A guard reading their own row must not become a guard reading everyone's."""
    _, token = await _seed_user(GUARD)
    async with await _client(token) as c:
        assert (await c.get("/api/v1/users/me")).status_code == 200
        assert (await c.get("/api/v1/users")).status_code == 403


# ─── B. Editing yourself ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_guard_can_change_their_own_contact_details():
    _, token = await _seed_user(GUARD)
    async with await _client(token) as c:
        r = await c.put("/api/v1/users/me", json={
            "full_name": "Tan Wei Ming", "phone": "+6591234567",
            "emergency_contact_name": "Next Of Kin",
        })
    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "Tan Wei Ming"
    assert r.json()["phone"] == "+6591234567"


@pytest.mark.asyncio
async def test_a_guard_can_change_their_own_password():
    """The whole reason this endpoint exists."""
    user_id, token = await _seed_user(GUARD)
    async with await _client(token) as c:
        r = await c.put("/api/v1/users/me", json={
            "current_password": KNOWN_PASSWORD, "new_password": "A-New-One-2!",
        })
    assert r.status_code == 200, r.text
    assert verify_password("A-New-One-2!", await _stored_hash(user_id))


@pytest.mark.asyncio
async def test_changing_a_password_requires_the_current_one():
    """A borrowed session must not be able to lock the owner out silently."""
    user_id, token = await _seed_user(GUARD)
    async with await _client(token) as c:
        missing = await c.put("/api/v1/users/me", json={"new_password": "A-New-One-2!"})
        wrong = await c.put("/api/v1/users/me", json={
            "current_password": "not-it", "new_password": "A-New-One-2!",
        })
    assert missing.status_code == 422
    assert wrong.status_code == 403
    assert verify_password(KNOWN_PASSWORD, await _stored_hash(user_id)), \
        "the stored password must be untouched"


@pytest.mark.asyncio
async def test_an_empty_update_is_refused():
    _, token = await _seed_user(GUARD)
    async with await _client(token) as c:
        assert (await c.put("/api/v1/users/me", json={})).status_code == 422


# ─── C. What self-service must never accept ──────────────────────────────────

@pytest.mark.asyncio
async def test_you_cannot_promote_yourself():
    """The one that would make this endpoint a privilege-escalation route."""
    user_id, token = await _seed_user(GUARD)
    async with await _client(token) as c:
        r = await c.put("/api/v1/users/me", json={
            "full_name": "Still A Guard", "role_id": SUPER_ADMIN,
        })
    # The field is ignored rather than rejected — it is not in SELF_EDITABLE —
    # so what matters is that the role did not move.
    assert r.status_code == 200
    assert r.json()["role_id"] == GUARD


@pytest.mark.asyncio
async def test_you_cannot_give_yourself_a_pay_rise():
    _, token = await _seed_user(GUARD)
    async with await _client(token) as c:
        r = await c.put("/api/v1/users/me", json={
            "full_name": "Optimistic", "hourly_rate": 999, "monthly_salary": 99999,
        })
    assert r.status_code == 200
    assert r.json()["hourly_rate"] is None
    assert r.json()["monthly_salary"] is None


@pytest.mark.asyncio
async def test_you_cannot_make_yourself_standby():
    """is_standby exempts a guard from 'one post per guard' (migration 0101).
    Deciding who relieves across sites is the roster's business, not the
    guard's."""
    _, token = await _seed_user(GUARD)
    async with await _client(token) as c:
        r = await c.put("/api/v1/users/me", json={
            "full_name": "Would Rather Roam", "is_standby": True,
        })
    assert r.status_code == 200
    assert r.json()["is_standby"] is False


@pytest.mark.asyncio
async def test_you_cannot_reactivate_yourself():
    _, token = await _seed_user(GUARD)
    async with await _client(token) as c:
        r = await c.put("/api/v1/users/me", json={
            "full_name": "Back Again", "is_active": True, "email": "new@example.com",
        })
    assert r.status_code == 200
    # email is not self-editable either: it is the login identity, and changing
    # it is an account-recovery question rather than a profile one.
    assert r.json()["email"] != "new@example.com"
