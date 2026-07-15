import pytest

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


@pytest.mark.asyncio
async def test_non_admin_cannot_write_settings(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)  # operator: no settings:write
    token = create_access_token(str(user_id), str(tenant_id), role_id=4)

    resp = await app_client.put(
        "/api/v1/settings/lpr.confidence_threshold",
        json={"setting_value": 0.7},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invalid_threshold_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)  # admin: has settings:write
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    resp = await app_client.put(
        "/api/v1/settings/lpr.confidence_threshold",
        json={"setting_value": 1.5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unknown_key_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    resp = await app_client.put(
        "/api/v1/settings/not.a.real.key",
        json={"setting_value": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_valid_upsert_then_list_round_trip(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    headers = {"Authorization": f"Bearer {token}"}

    put_resp = await app_client.put(
        "/api/v1/settings/intrusion.breach_cooldown_seconds", json={"setting_value": 30}, headers=headers
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["setting_value"] == 30

    list_resp = await app_client.get("/api/v1/settings", headers=headers)
    assert list_resp.status_code == 200
    keys = {row["setting_key"]: row["setting_value"] for row in list_resp.json()}
    assert keys["intrusion.breach_cooldown_seconds"] == 30

    # Upserting again (same key) updates in place, doesn't duplicate.
    put_again = await app_client.put(
        "/api/v1/settings/intrusion.breach_cooldown_seconds", json={"setting_value": 45}, headers=headers
    )
    assert put_again.status_code == 200
    list_resp_2 = await app_client.get("/api/v1/settings", headers=headers)
    matching = [r for r in list_resp_2.json() if r["setting_key"] == "intrusion.breach_cooldown_seconds"]
    assert len(matching) == 1
    assert matching[0]["setting_value"] == 45
