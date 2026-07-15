import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token, hash_password


async def _seed_user_with_role(admin_session, role_id: int) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'T', :slug)"),
        {"id": tenant_id, "slug": f"t-{tenant_id.hex[:8]}"},
    )
    user_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
            "VALUES (:id, :tid, :rid, :email, :pw)"
        ),
        {"id": user_id, "tid": tenant_id, "rid": role_id, "email": f"{user_id}@example.com", "pw": hash_password("x")},
    )
    await admin_session.commit()
    return tenant_id, user_id


@pytest.mark.asyncio
async def test_permission_denied_returns_403(app_client, admin_session):
    """viewer (role_id=6) does not have camera:create in the seed data
    (0001_initial_schema.py) — confirms require_permission actually blocks an
    authenticated-but-unauthorized caller, not just unauthenticated ones."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)  # viewer
    token = create_access_token(str(user_id), str(tenant_id), role_id=6)

    resp = await app_client.post(
        "/api/v1/cameras", json={"name": "Sneaky Cam"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_permission_granted_succeeds(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)  # admin
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    resp = await app_client.get("/api/v1/cameras", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []  # no cameras seeded for this tenant, but access is granted


@pytest.mark.asyncio
async def test_role_seed_matches_eight_roles(admin_session):
    # Built-in roles have tenant_id IS NULL; tenant-custom roles (Gap 91) may
    # also exist, so assert the built-in seed is intact rather than an exact
    # total row count.
    rows = (await admin_session.execute(
        text("SELECT code FROM roles WHERE tenant_id IS NULL ORDER BY id")
    )).fetchall()
    assert [r.code for r in rows] == [
        "super_admin", "admin", "supervisor", "operator", "security_guard", "viewer", "client", "manager",
    ]
