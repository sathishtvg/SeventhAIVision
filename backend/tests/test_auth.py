import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt
from sqlalchemy import text

from app.core.config import settings
from app.core.security import decode_access_token, hash_password, InvalidTokenError


async def _seed_tenant_and_user(admin_session, email="alice@example.com", password="correct-password", role_id=2):
    tenant_id = uuid.uuid4()
    slug = f"tenant-{tenant_id.hex[:8]}"
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Test Tenant', :slug)"),
        {"id": tenant_id, "slug": slug},
    )
    user_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
            "VALUES (:id, :tid, :rid, :email, :pw)"
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
        json={"tenant_slug": slug, "email": "alice@example.com", "password": "correct-password"},
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
        json={"tenant_slug": slug, "email": "alice@example.com", "password": "correct-password"},
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
        json={"tenant_slug": slug, "email": "alice@example.com", "password": "correct-password"},
    )
    old_refresh = login_resp.json()["refresh_token"]

    refresh_resp = await app_client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert refresh_resp.status_code == 200
    new_refresh = refresh_resp.json()["refresh_token"]
    assert new_refresh != old_refresh

    # The old (now-revoked) refresh token must not work a second time.
    reuse_resp = await app_client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse_resp.status_code == 401
