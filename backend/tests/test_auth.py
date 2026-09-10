import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from jose import jwt
from sqlalchemy import text

from app.core.config import settings
from app.core.security import decode_access_token, hash_password, InvalidTokenError


async def _seed_tenant_and_user(admin_session, email="alice@example.com", password="orbit-lantern-quay-42", role_id=2):
    tenant_id = uuid.uuid4()
    slug = f"tenant-{tenant_id.hex[:8]}"
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Test Tenant', :slug)"),
        {"id": tenant_id, "slug": slug},
    )
    user_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
            "                   totp_enabled) "
            "VALUES (:id, :tid, CAST(:rid AS smallint), :email, :pw, "
            "        CAST(:rid AS smallint) = 1)"
        ),
        {"id": user_id, "tid": tenant_id, "rid": role_id, "email": email, "pw": hash_password(password)},
    )
    await admin_session.commit()
    return tenant_id, slug, user_id


@pytest.mark.asyncio
async def test_login_valid_credentials(app_client, admin_session):
    tenant_id, slug, user_id = await _seed_tenant_and_user(admin_session)

    resp = await app_client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": slug, "email": "alice@example.com", "password": "orbit-lantern-quay-42"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body and "refresh_token" in body
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password_and_nonexistent_email_identical_shape(app_client, admin_session):
    tenant_id, slug, user_id = await _seed_tenant_and_user(admin_session)

    wrong_password = await app_client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": slug, "email": "alice@example.com", "password": "wrong"},
    )
    nonexistent_email = await app_client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": slug, "email": "nobody@example.com", "password": "wrong"},
    )
    assert wrong_password.status_code == 401
    assert nonexistent_email.status_code == 401
    assert wrong_password.json() == nonexistent_email.json()


@pytest.mark.asyncio
async def test_access_token_claims(app_client, admin_session):
    tenant_id, slug, user_id = await _seed_tenant_and_user(admin_session, role_id=3)

    resp = await app_client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": slug, "email": "alice@example.com", "password": "orbit-lantern-quay-42"},
    )
    access_token = resp.json()["access_token"]
    payload = decode_access_token(access_token)
    assert payload["sub"] == str(user_id)
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["role_id"] == 3
    assert payload["exp"] > datetime.now(timezone.utc).timestamp()


def test_expired_token_rejected():
    now = datetime.now(timezone.utc)
    expired_payload = {
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "role_id": 2,
        "iat": now - timedelta(minutes=30),
        "exp": now - timedelta(minutes=15),  # already expired
        "type": "access",
    }
    expired_token = jwt.encode(
        expired_payload, settings.JWT_SECRET_KEY_CURRENT, algorithm=settings.JWT_ALGORITHM,
        headers={"kid": settings.JWT_ACTIVE_KID},
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(expired_token)


@pytest.mark.asyncio
async def test_refresh_rotates_token(app_client, admin_session):
    tenant_id, slug, user_id = await _seed_tenant_and_user(admin_session)

    login_resp = await app_client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": slug, "email": "alice@example.com", "password": "orbit-lantern-quay-42"},
    )
    old_refresh = login_resp.json()["refresh_token"]

    refresh_resp = await app_client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert refresh_resp.status_code == 200
    new_refresh = refresh_resp.json()["refresh_token"]
    assert new_refresh != old_refresh

    # The old (now-revoked) refresh token must not work a second time.
    reuse_resp = await app_client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse_resp.status_code == 401



@pytest_asyncio.fixture
async def clear_login_rate_limit():
    """Clear the login limiter's slowapi keys before the test.

    /auth/login is capped at 5/minute and conftest flushes Redis only once per
    session, so by the time this file's later tests run the budget is spent and
    they get 429 instead of the status under test. The other tests here dodge
    this by minting JWTs directly (see the auth_client fixture), which is no use
    when the login endpoint IS the thing being tested.

    Deletes only the keys for this endpoint rather than flushing the database,
    so it cannot disturb anything else mid-suite.
    """
    import redis.asyncio as aioredis

    r = aioredis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    try:
        async for key in r.scan_iter(match="*auth/login*"):
            await r.delete(key)
    finally:
        await r.aclose()
    yield

# ─── A corrupt stored hash must not become a 500 ─────────────────────────────
#
# On 2026-09-10 a bcrypt hash was pasted into a double-quoted `psql -c` string.
# Bash expanded the `$2b` and `$12` in `$2b$12$...` as positional parameters —
# both empty — so a truncated remnant reached the database. bcrypt.checkpw
# raises ValueError("Invalid salt") on a malformed hash rather than returning
# False, that escaped the login route as a 500, and the sign-in page renders
# every failure as "Invalid credentials".
#
# The cost was not the corruption, which took one UPDATE to repair. It was that
# the product reported a wrong password for an account no password could open,
# and left the failure counter at zero so the row looked perfectly healthy.

def test_verify_password_returns_false_for_a_malformed_hash():
    """The unit-level claim: answer the question, do not raise."""
    from app.core.security import verify_password

    # What bash actually leaves behind after eating the $2b$12$ prefix.
    truncated = hash_password("orbit-lantern-quay-42")[7:]
    assert verify_password("orbit-lantern-quay-42", truncated) is False
    assert verify_password("anything-at-all", "not-a-hash") is False


def test_verify_password_returns_false_for_an_empty_hash():
    from app.core.security import verify_password

    assert verify_password("orbit-lantern-quay-42", "") is False


def test_verify_password_still_verifies_a_good_hash():
    """The regression guard. A function that never raises and never returns
    True would pass both tests above and lock everybody out."""
    from app.core.security import verify_password

    good = hash_password("orbit-lantern-quay-42")
    assert verify_password("orbit-lantern-quay-42", good) is True
    assert verify_password("the-wrong-one", good) is False


@pytest.mark.asyncio
async def test_login_against_a_corrupt_hash_is_401_not_500(app_client, admin_session, clear_login_rate_limit):
    """The test that would have caught it, at the layer the user meets."""
    tenant_id = uuid.uuid4()
    slug = f"tenant-{tenant_id.hex[:8]}"
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Corrupt Co', :slug)"),
        {"id": tenant_id, "slug": slug},
    )
    await admin_session.execute(
        text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
             "VALUES (:id, :tid, CAST(2 AS smallint), :email, :pw)"),
        {"id": uuid.uuid4(), "tid": tenant_id, "email": "corrupt@example.com",
         "pw": hash_password("orbit-lantern-quay-42")[7:]},
    )
    await admin_session.commit()

    resp = await app_client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": slug, "email": "corrupt@example.com",
              "password": "orbit-lantern-quay-42"},
    )
    assert resp.status_code == 401, (
        f"a malformed stored hash must be a clean 401, got {resp.status_code}"
    )


@pytest.mark.asyncio
async def test_a_corrupt_hash_is_indistinguishable_to_the_caller(app_client, admin_session, clear_login_rate_limit):
    """401 yes — but the body must not tell an attacker which accounts are
    broken. A corrupt row is a fact about the vendor, not about the caller."""
    tenant_id = uuid.uuid4()
    slug = f"tenant-{tenant_id.hex[:8]}"
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Corrupt Co', :slug)"),
        {"id": tenant_id, "slug": slug},
    )
    await admin_session.execute(
        text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
             "VALUES (:id, :tid, CAST(2 AS smallint), :email, :pw)"),
        {"id": uuid.uuid4(), "tid": tenant_id, "email": "corrupt@example.com",
         "pw": hash_password("orbit-lantern-quay-42")[7:]},
    )
    await admin_session.commit()

    corrupt = await app_client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": slug, "email": "corrupt@example.com", "password": "x"},
    )
    unknown = await app_client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": slug, "email": "nobody@example.com", "password": "x"},
    )
    assert corrupt.status_code == unknown.status_code == 401
    assert corrupt.json() == unknown.json()


@pytest.mark.asyncio
async def test_a_corrupt_hash_now_reaches_the_failed_attempt_counter(app_client, admin_session, clear_login_rate_limit):
    """Before the fix the exception jumped over the counter, so an account
    nobody could open still read failed_login_count = 0 — the one signal an
    operator would have looked at showed nothing wrong."""
    tenant_id = uuid.uuid4()
    slug = f"tenant-{tenant_id.hex[:8]}"
    user_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Corrupt Co', :slug)"),
        {"id": tenant_id, "slug": slug},
    )
    await admin_session.execute(
        text("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
             "VALUES (:id, :tid, CAST(2 AS smallint), :email, :pw)"),
        {"id": user_id, "tid": tenant_id, "email": "corrupt@example.com",
         "pw": hash_password("orbit-lantern-quay-42")[7:]},
    )
    await admin_session.commit()

    await app_client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": slug, "email": "corrupt@example.com", "password": "x"},
    )
    count = (await admin_session.execute(
        text("SELECT failed_login_count FROM users WHERE id = :id"), {"id": user_id},
    )).scalar()
    assert count == 1, "a failed login against a corrupt hash must still be counted"
