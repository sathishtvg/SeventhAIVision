"""Super Admin is the platform operator, and entering a tenant is an event.

Migration 0102 cut Super Admin from 142 permissions to four. Before it, Super
Admin held everything Admin held plus tenant:manage and license:manage — so the
platform operator was a tenant administrator with two extra switches, and a
customer's rosters, payroll and employment records sat in front of somebody
whose job never needs them.

Reading a tenant's data is now a support session: named tenant, written reason,
an hour at the outside, revoked the moment it ends, and recorded in the
CUSTOMER's own audit log as well as the platform's.

Sections:
  A — Super Admin's scope (5 tests)
  B — Opening a session (5 tests)
  C — What the session token can and cannot do (6 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Imported at module level on purpose: app.main pulls the ML stack, and inside a
# test function that 120s import lands on whichever test runs first and trips
# pytest-timeout. At import time it is paid during collection instead.
from app.main import app
from app.core.security import (
    SUPPORT_TOKEN_MAX_MINUTES, create_access_token, create_support_token,
)

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

SUPER_ADMIN, ADMIN, GUARD = 1, 2, 5

#: The platform operator's whole job — mirrors migration 0102.
PLATFORM_PERMISSIONS = {"tenant:manage", "license:manage", "audit:read", "support:manage"}


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


async def _seed_tenant(role_id: int = SUPER_ADMIN):
    """A tenant with one user of the given role. Returns (tenant_id, user_id, token)."""
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    slug = f"supp-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Support Test {slug}", "slug": slug},
        )
        await s.execute(
            text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                 "VALUES (:id, :tid, :role, :email, 'hashed', 'Support Tester')"),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"supp-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return tenant_id, user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _client(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


async def _sql(statement: str, params: dict | None = None):
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        result = await s.execute(text(statement), params or {})
        # An UPDATE has no rows to fetch, and asking closes the result.
        rows = result.all() if result.returns_rows else []
        await s.commit()
    await engine.dispose()
    return rows


async def _open_session(token, target_tenant_id, reason=None, minutes=30):
    async with await _client(token) as c:
        return await c.post("/api/v1/support-sessions", json={
            "tenant_id": str(target_tenant_id),
            "reason": reason or "Ticket 4821 - the roster grid is empty for September",
            "minutes": minutes,
        })


# ─── A. Super Admin's scope ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_super_admin_holds_only_the_platform_permissions():
    """Four, not 142. The number is the whole point of migration 0102."""
    rows = await _sql(
        "SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
        " WHERE rp.role_id = :rid", {"rid": SUPER_ADMIN})
    assert {r[0] for r in rows} == PLATFORM_PERMISSIONS


@pytest.mark.asyncio
async def test_admin_keeps_the_operational_set():
    """The customer's own administrator did not lose anything — the work moved
    to whom it belonged to, it did not disappear."""
    rows = await _sql(
        "SELECT count(*) FROM role_permissions WHERE role_id = :rid", {"rid": ADMIN})
    assert rows[0][0] >= 130


@pytest.mark.asyncio
async def test_the_permission_endpoint_agrees_with_the_database():
    """The client builds its nav from this, so a disagreement here shows the
    operator a menu full of pages the API will refuse."""
    _, _, token = await _seed_tenant(SUPER_ADMIN)
    async with await _client(token) as c:
        r = await c.get("/api/v1/auth/me/permissions")
    assert r.status_code == 200
    assert set(r.json()["permissions"]) == PLATFORM_PERMISSIONS


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/users", "/api/v1/shifts", "/api/v1/keys"])
async def test_super_admin_is_refused_the_customers_business(path):
    _, _, token = await _seed_tenant(SUPER_ADMIN)
    async with await _client(token) as c:
        r = await c.get(path)
    assert r.status_code == 403, f"{path} answered {r.status_code}"


@pytest.mark.asyncio
async def test_super_admin_can_still_do_its_own_job():
    _, _, token = await _seed_tenant(SUPER_ADMIN)
    async with await _client(token) as c:
        r = await c.get("/api/v1/tenants")
    assert r.status_code == 200


# ─── B. Opening a session ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_only_super_admin_may_open_a_session():
    """Otherwise a tenant's own admin could mint a token for somewhere else."""
    _, _, admin_token = await _seed_tenant(ADMIN)
    target_id, _, _ = await _seed_tenant(ADMIN)
    r = await _open_session(admin_token, target_id)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_a_reason_is_required_and_has_to_say_something():
    """An access record nobody has to justify is a log, not a control."""
    _, _, token = await _seed_tenant(SUPER_ADMIN)
    target_id, _, _ = await _seed_tenant(ADMIN)
    assert (await _open_session(token, target_id, reason="x")).status_code == 422
    assert (await _open_session(token, target_id, reason="   ")).status_code == 422


@pytest.mark.asyncio
async def test_opening_records_it_in_the_customers_own_audit_log():
    """The half that matters. A customer's auditor should not have to ask the
    vendor whether the vendor came in."""
    _, platform_user, token = await _seed_tenant(SUPER_ADMIN)
    target_id, _, _ = await _seed_tenant(ADMIN)

    reason = "Ticket 9182 - customer cannot see September attendance"
    r = await _open_session(token, target_id, reason=reason)
    assert r.status_code == 201, r.text

    rows = await _sql(
        "SELECT detail FROM audit_logs "
        " WHERE tenant_id = :tid AND action = 'support_session.opened'",
        {"tid": target_id})
    assert len(rows) == 1, "the customer's log should carry exactly one entry"
    # detail is JSONB, so it arrives as a dict rather than the raw string.
    detail = rows[0][0]
    assert detail["reason"] == reason
    assert detail["by_platform_user"] == str(platform_user), "it must name who came in"


@pytest.mark.asyncio
async def test_two_live_sessions_at_once_are_refused():
    """Two open sessions means two tenants' tokens in the same hands."""
    _, _, token = await _seed_tenant(SUPER_ADMIN)
    a, _, _ = await _seed_tenant(ADMIN)
    b, _, _ = await _seed_tenant(ADMIN)
    assert (await _open_session(token, a)).status_code == 201
    r = await _open_session(token, b)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_a_session_cannot_outlive_an_hour():
    """Whatever the caller asks for. An eight-hour 'temporary' is a standing
    grant with extra steps."""
    _, _, token = await _seed_tenant(SUPER_ADMIN)
    target_id, _, _ = await _seed_tenant(ADMIN)
    r = await _open_session(token, target_id, minutes=SUPPORT_TOKEN_MAX_MINUTES + 60)
    assert r.status_code == 422


# ─── C. What the session token can and cannot do ─────────────────────────────

@pytest.mark.asyncio
async def test_the_session_token_reads_the_target_tenant():
    _, _, token = await _seed_tenant(SUPER_ADMIN)
    target_id, _, _ = await _seed_tenant(ADMIN)
    r = await _open_session(token, target_id)
    async with await _client(r.json()["access_token"]) as c:
        assert (await c.get("/api/v1/users")).status_code == 200


@pytest.mark.asyncio
async def test_the_session_token_is_not_a_promotion():
    """It stands in for the customer's administrator, not for a super admin —
    otherwise a support session would be a way to reach every other tenant."""
    _, _, token = await _seed_tenant(SUPER_ADMIN)
    target_id, _, _ = await _seed_tenant(ADMIN)
    r = await _open_session(token, target_id)
    async with await _client(r.json()["access_token"]) as c:
        assert (await c.get("/api/v1/tenants")).status_code == 403


@pytest.mark.asyncio
async def test_ending_a_session_revokes_the_token_immediately():
    """Not at expiry. A token that outlives its authorisation is the whole
    failure mode the per-request check exists to prevent."""
    _, _, token = await _seed_tenant(SUPER_ADMIN)
    target_id, _, _ = await _seed_tenant(ADMIN)
    opened = (await _open_session(token, target_id)).json()

    async with await _client(token) as c:
        assert (await c.post(
            f"/api/v1/support-sessions/{opened['id']}/end")).status_code == 200

    async with await _client(opened["access_token"]) as c:
        r = await c.get("/api/v1/users")
    assert r.status_code == 403
    assert "ended or expired" in r.json()["detail"]


@pytest.mark.asyncio
async def test_an_expired_session_refuses_a_token_that_has_not_expired_yet():
    """The session is the authority, not the JWT's own exp."""
    _, _, token = await _seed_tenant(SUPER_ADMIN)
    target_id, _, _ = await _seed_tenant(ADMIN)
    opened = (await _open_session(token, target_id)).json()

    await _sql("UPDATE tenant_support_sessions SET expires_at = now() - interval '1 minute' "
               " WHERE id = CAST(:sid AS uuid)", {"sid": opened["id"]})

    async with await _client(opened["access_token"]) as c:
        assert (await c.get("/api/v1/users")).status_code == 403


@pytest.mark.asyncio
async def test_a_session_token_cannot_be_pointed_at_another_tenant():
    """The isolation test. A live session for tenant B must not authorise a
    hand-built token naming tenant C — the session row and the token have to
    agree about where they are."""
    _, platform_user, token = await _seed_tenant(SUPER_ADMIN)
    b, _, _ = await _seed_tenant(ADMIN)
    c_tenant, _, _ = await _seed_tenant(ADMIN)
    opened = (await _open_session(token, b)).json()

    forged = create_support_token(str(platform_user), str(c_tenant), opened["id"], 30)
    async with await _client(forged) as cl:
        r = await cl.get("/api/v1/users")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_a_session_token_cannot_be_used_by_another_user():
    """Stolen-token shape: the session names who opened it, and the check
    compares against the token's subject."""
    _, _, token = await _seed_tenant(SUPER_ADMIN)
    _, other_user, _ = await _seed_tenant(SUPER_ADMIN)
    target_id, _, _ = await _seed_tenant(ADMIN)
    opened = (await _open_session(token, target_id)).json()

    forged = create_support_token(str(other_user), str(target_id), opened["id"], 30)
    async with await _client(forged) as c:
        assert (await c.get("/api/v1/users")).status_code == 403
