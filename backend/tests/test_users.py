import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token, hash_password


async def _seed_tenant_and_user(admin_session, role_id: int) -> tuple[uuid.UUID, uuid.UUID, str]:
    tenant_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'T', :slug)"),
        {"id": tenant_id, "slug": f"t-{tenant_id.hex[:8]}"},
    )
    user_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
            "VALUES (:id, :tid, :rid, :email, :pw, 'Tester')"
        ),
        {"id": user_id, "tid": tenant_id, "rid": role_id, "email": f"u@{tenant_id.hex[:8]}.test", "pw": hash_password("x")},
    )
    await admin_session.commit()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


@pytest.mark.asyncio
async def test_user_crud_round_trip(app_client, admin_session):
    tenant_id, admin_id, token = await _seed_tenant_and_user(admin_session, role_id=2)  # admin
    headers = {"Authorization": f"Bearer {token}"}

    # Create
    create_resp = await app_client.post(
        "/api/v1/users",
        json={"email": f"op@{tenant_id.hex[:8]}.test", "password": "oppass", "role_id": 4, "full_name": "Operator"},
        headers=headers,
    )
    assert create_resp.status_code == 201
    new_id = create_resp.json()["id"]
    assert "hashed_password" not in create_resp.json()

    # List
    list_resp = await app_client.get("/api/v1/users", headers=headers)
    assert list_resp.status_code == 200
    ids = [u["id"] for u in list_resp.json()]
    assert new_id in ids

    # Get single
    get_resp = await app_client.get(f"/api/v1/users/{new_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["role_id"] == 4

    # Update
    update_resp = await app_client.put(
        f"/api/v1/users/{new_id}",
        json={"full_name": "Senior Operator", "role_id": 3},
        headers=headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["role_id"] == 3

    # Deactivate
    del_resp = await app_client.delete(f"/api/v1/users/{new_id}", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["is_active"] is False

    # Cleanup
    await admin_session.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tenant_id})
    await admin_session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
    await admin_session.commit()


@pytest.mark.asyncio
async def test_non_admin_cannot_create_users(app_client, admin_session):
    tenant_id, _, token = await _seed_tenant_and_user(admin_session, role_id=4)  # operator — no user:create
    headers = {"Authorization": f"Bearer {token}"}

    resp = await app_client.post(
        "/api/v1/users",
        json={"email": f"new@{tenant_id.hex[:8]}.test", "password": "pw", "role_id": 6},
        headers=headers,
    )
    assert resp.status_code == 403

    # Cleanup
    await admin_session.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tenant_id})
    await admin_session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
    await admin_session.commit()


@pytest.mark.asyncio
async def test_user_not_found_returns_404(app_client, admin_session):
    tenant_id, _, token = await _seed_tenant_and_user(admin_session, role_id=2)  # admin
    headers = {"Authorization": f"Bearer {token}"}

    resp = await app_client.get(f"/api/v1/users/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404

    # Cleanup
    await admin_session.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tenant_id})
    await admin_session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
    await admin_session.commit()
