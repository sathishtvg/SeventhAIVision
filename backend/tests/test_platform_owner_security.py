"""The account that can reach every customer, held to a higher standard.

A Super Admin can open a support session into any tenant, change every
customer's price and read the whole ledger. 2FA existed and worked but was
opt-in, so all of that was protected by a password at the vendor's option —
which is the same as at nobody's.

TWO THINGS HAVE TO HOLD TOGETHER, and they pull against each other.

    The powers are withheld without 2FA.
    Nobody gets locked out.

Refusing the login of an unenrolled Super Admin would satisfy the first and
break the second: they cannot enrol without signing in. So the login succeeds,
the platform permissions are withheld, and the way out — their own account, the
2FA setup pages — stays reachable. Both halves are tested below, because a
security control that bricks the owner's account gets ripped out within a week
and then protects nothing at all.

Sections:
  A — MFA gating (5 tests)
  B — The password policy (7 tests)
  C — New-location detection (3 tests)
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
from app.core.security import create_access_token
from app.services import password_policy

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

SUPER_ADMIN, ADMIN, GUARD = 1, 2, 5


async def _sql(statement: str, params: dict | None = None):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        result = await s.execute(text(statement), params or {})
        rows = result.all() if result.returns_rows else []
        await s.commit()
    await engine.dispose()
    return rows


async def _user(role_id: int, *, totp: bool = False):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _sql("INSERT INTO tenants (id, name, slug, is_platform) "
               "VALUES (:id, 'Sec', :slug, :platform)",
               {"id": tenant_id, "slug": f"sec-{tenant_id.hex[:10]}",
                "platform": role_id == SUPER_ADMIN})
    await _sql("INSERT INTO users (id, tenant_id, role_id, email, "
               "                   hashed_password, totp_enabled) "
               "VALUES (:id, :tid, :role, :email, 'hashed', :totp)",
               {"id": user_id, "tid": tenant_id, "role": role_id,
                "email": f"sec-{user_id.hex[:8]}@test.local", "totp": totp})
    return user_id, create_access_token(str(user_id), str(tenant_id), role_id)


async def _client(token: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


async def _set_policy(key: str, value: str):
    await _sql("INSERT INTO platform_settings (key, value) VALUES (:k, :v) "
               " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
               {"k": key, "v": value})


# ─── A. MFA gating ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_platform_owner_without_2fa_is_refused_platform_powers():
    await _set_policy("security.require_mfa_for_platform_owner", "true")
    _, token = await _user(SUPER_ADMIN, totp=False)
    async with await _client(token) as c:
        r = await c.get("/api/v1/platform/dashboard")
    assert r.status_code == 403
    assert "two-factor" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_enrolling_restores_them():
    await _set_policy("security.require_mfa_for_platform_owner", "true")
    _, token = await _user(SUPER_ADMIN, totp=True)
    async with await _client(token) as c:
        assert (await c.get("/api/v1/platform/dashboard")).status_code == 200


@pytest.mark.asyncio
async def test_the_unenrolled_owner_can_still_reach_their_own_account():
    """The half that stops this being a lockout. Refusing everything would be
    correct and unusable — they cannot enrol without getting in — so the way
    out has to stay reachable from where they are standing."""
    await _set_policy("security.require_mfa_for_platform_owner", "true")
    async with await _client((await _user(SUPER_ADMIN, totp=False))[1]) as c:
        assert (await c.get("/api/v1/users/me")).status_code == 200


@pytest.mark.asyncio
async def test_the_gate_does_not_apply_to_a_tenant_admin():
    """A guard signing in on a phone in the rain is not the platform owner, and
    holding them to the same bar would be theatre."""
    await _set_policy("security.require_mfa_for_platform_owner", "true")
    _, token = await _user(ADMIN, totp=False)
    async with await _client(token) as c:
        # Refused for want of the permission, not for want of 2FA.
        r = await c.get("/api/v1/platform/dashboard")
    assert r.status_code == 403
    assert "two-factor" not in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_a_deleted_policy_row_leaves_the_control_on():
    """Absent means required. A security control must not switch itself off
    because somebody removed a row."""
    await _sql("DELETE FROM platform_settings "
               " WHERE key = 'security.require_mfa_for_platform_owner'")
    try:
        _, token = await _user(SUPER_ADMIN, totp=False)
        async with await _client(token) as c:
            assert (await c.get("/api/v1/platform/dashboard")).status_code == 403
    finally:
        await _set_policy("security.require_mfa_for_platform_owner", "true")


# ─── B. The password policy ──────────────────────────────────────────────────

def test_a_short_password_is_refused():
    assert password_policy.problems("Sh0rt!")


def test_length_beats_composition():
    """NIST SP 800-63B, and every hour of cracking research: a passphrase of
    ordinary words is stronger than a short string of symbols."""
    assert password_policy.problems("P@ssw0rd!") != []
    assert password_policy.problems("the cat sat on the alarm panel") == []


def test_the_obvious_guesses_are_refused_by_name():
    for guess in ("password123456", "administrator01", "seventhaivision1"):
        assert password_policy.problems(guess), guess


def test_keyboard_runs_are_refused():
    assert password_policy.problems("qwertyuiop1234")


def test_your_own_email_is_not_a_password():
    assert password_policy.problems("sathish-and-more-words",
                                    email="sathish@example.com")


def test_the_platform_owner_is_held_to_a_longer_floor():
    """That account can open a support session into any customer. A single
    standard would be too weak for the vendor or unreasonable for a guard."""
    fourteen = "correct horse b"
    assert password_policy.problems(fourteen, role_id=GUARD) == []
    assert password_policy.problems(fourteen, role_id=SUPER_ADMIN) != []


def test_every_problem_is_reported_at_once():
    """A form that reports one failure at a time makes somebody guess four
    times, and what they land on is usually worse than what they started
    with."""
    assert len(password_policy.problems("admin")) >= 2


@pytest.mark.asyncio
async def test_the_api_refuses_a_weak_password_not_just_the_browser():
    """A policy the client enforces is a policy anybody with curl skips."""
    _, token = await _user(ADMIN, totp=False)
    async with await _client(token) as c:
        r = await c.post("/api/v1/users", json={
            "email": f"weak-{uuid.uuid4().hex[:8]}@test.local",
            "password": "password", "role_id": GUARD,
        })
    assert r.status_code == 422
    assert "characters" in r.json()["detail"].lower()


# ─── C. New-location detection ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_new_address_is_recorded_and_reported_once():
    user_id, _ = await _user(SUPER_ADMIN, totp=True)
    first = (await _sql(
        "SELECT platform_record_login_location(CAST(:uid AS uuid), '203.0.113.7')",
        {"uid": user_id}))[0][0]
    second = (await _sql(
        "SELECT platform_record_login_location(CAST(:uid AS uuid), '203.0.113.7')",
        {"uid": user_id}))[0][0]
    assert first is True, "the first sighting is new"
    assert second is False, "the second is not, or every login is an alert"


@pytest.mark.asyncio
async def test_the_login_count_accumulates():
    user_id, _ = await _user(SUPER_ADMIN, totp=True)
    for _ in range(3):
        await _sql("SELECT platform_record_login_location("
                   "  CAST(:uid AS uuid), '198.51.100.4')", {"uid": user_id})
    rows = await _sql("SELECT logins FROM platform_login_locations "
                      " WHERE user_id = :uid", {"uid": user_id})
    assert rows[0][0] == 3


@pytest.mark.asyncio
async def test_a_different_address_is_new_again():
    user_id, _ = await _user(SUPER_ADMIN, totp=True)
    await _sql("SELECT platform_record_login_location("
               "  CAST(:uid AS uuid), '203.0.113.1')", {"uid": user_id})
    other = (await _sql(
        "SELECT platform_record_login_location(CAST(:uid AS uuid), '203.0.113.2')",
        {"uid": user_id}))[0][0]
    assert other is True


@pytest.mark.asyncio
async def test_an_unenrolled_owner_can_actually_reach_the_enrolment_endpoint():
    """The test that would have caught the lockout.

    Every other MFA test here seeds totp_enabled directly, which quietly
    assumes the owner could have got there. They could not: migration 0102 cut
    Super Admin to seven permissions and 2fa:manage was not among them, so
    GET /2fa/setup answered 403 — told to enrol, and handed a door they could
    not open.

    Seeding the end state is not the same as walking the path, and this is what
    the difference costs.
    """
    await _set_policy("security.require_mfa_for_platform_owner", "true")
    _, token = await _user(SUPER_ADMIN, totp=False)
    async with await _client(token) as c:
        setup = await c.get("/api/v1/2fa/setup")
        blocked = await c.get("/api/v1/platform/dashboard")

    assert setup.status_code == 200, (
        "an unenrolled platform owner must be able to reach 2FA setup, "
        "or the requirement is a lockout"
    )
    assert blocked.status_code == 403, "and everything else stays shut until they do"


@pytest.mark.asyncio
async def test_the_setup_response_carries_what_an_authenticator_needs():
    """A secret and a QR code. Without both, "set it up under My Account" is
    an instruction with nothing behind it."""
    _, token = await _user(SUPER_ADMIN, totp=False)
    async with await _client(token) as c:
        body = (await c.get("/api/v1/2fa/setup")).json()
    assert body.get("secret"), body
    assert body.get("qr_code_uri", "").startswith("data:image"), body
    assert body.get("manual_entry_key"), "an authenticator app that cannot scan needs the key typed in"
