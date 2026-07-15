"""Tier 2 Feature 2: IP allowlist per-tenant access control tests.

Design:
- No active rules → tenant is fully open (default)
- ≥1 active rule  → only matching CIDRs are allowed; others get 403
- Enforcement is in get_db_with_tenant via get_tenant_ip_allowlist() SECURITY DEFINER fn
- Client IP is read from X-Forwarded-For first, then request.client.host
"""

import uuid

import pytest
import pytest_asyncio

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _jwt(tenant_id: uuid.UUID, user_id: uuid.UUID, role_id: int) -> str:
    return create_access_token(str(user_id), str(tenant_id), role_id=role_id)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def _admin(admin_session):
    """Seed tenant+admin, return (jwt, tenant_id)."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    return _jwt(tenant_id, user_id, 2), tenant_id


# ── CRUD tests ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_rules_empty(_admin, app_client):
    jwt, _ = _admin
    resp = await app_client.get(
        "/api/v1/ip-allowlist",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_add_rule(_admin, app_client):
    jwt, _ = _admin
    resp = await app_client.post(
        "/api/v1/ip-allowlist",
        json={"cidr": "192.168.1.0/24", "description": "Office LAN"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cidr"] == "192.168.1.0/24"
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_add_bare_ip_normalized(_admin, app_client):
    """A bare IP without /prefix should be accepted (treated as /32)."""
    jwt, _ = _admin
    resp = await app_client.post(
        "/api/v1/ip-allowlist",
        json={"cidr": "10.0.0.1"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_add_invalid_cidr_rejected(_admin, app_client):
    jwt, _ = _admin
    resp = await app_client.post(
        "/api/v1/ip-allowlist",
        json={"cidr": "not-an-ip"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_add_duplicate_cidr_conflict(_admin, app_client):
    jwt, _ = _admin
    # Use 127.0.0.0/8 so the test-client IP (127.0.0.1) stays allowed after the
    # first add, and we can verify the second add returns 409 rather than 403.
    await app_client.post(
        "/api/v1/ip-allowlist",
        json={"cidr": "127.0.0.0/8"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    resp2 = await app_client.post(
        "/api/v1/ip-allowlist",
        json={"cidr": "127.0.0.0/8"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_delete_rule(_admin, app_client):
    jwt, _ = _admin
    # Include the test-client IP (127.0.0.1) so subsequent requests aren't blocked.
    create_resp = await app_client.post(
        "/api/v1/ip-allowlist",
        json={"cidr": "127.0.0.0/8"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    rule_id = create_resp.json()["id"]

    del_resp = await app_client.delete(
        f"/api/v1/ip-allowlist/{rule_id}",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    # Gone from list
    list_resp = await app_client.get(
        "/api/v1/ip-allowlist",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert all(r["id"] != rule_id for r in list_resp.json())


@pytest.mark.asyncio
async def test_delete_nonexistent_rule_404(_admin, app_client):
    jwt, _ = _admin
    resp = await app_client.delete(
        f"/api/v1/ip-allowlist/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_toggle_rule(_admin, app_client):
    jwt, _ = _admin
    # Use loopback CIDR so test-client (127.0.0.1) stays allowed between toggle calls.
    create_resp = await app_client.post(
        "/api/v1/ip-allowlist",
        json={"cidr": "127.0.0.0/8"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    rule_id = create_resp.json()["id"]

    toggle_resp = await app_client.patch(
        f"/api/v1/ip-allowlist/{rule_id}/toggle",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert toggle_resp.status_code == 200
    assert toggle_resp.json()["is_active"] is False  # toggled off
    # After disabling, no active rules remain → test-client still allowed (open)

    toggle_resp2 = await app_client.patch(
        f"/api/v1/ip-allowlist/{rule_id}/toggle",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert toggle_resp2.json()["is_active"] is True  # toggled back on


@pytest.mark.asyncio
async def test_non_admin_cannot_manage(admin_session, app_client):
    """Viewer (role 6) cannot list or add IP rules."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)
    jwt = _jwt(tenant_id, user_id, 6)

    r1 = await app_client.get(
        "/api/v1/ip-allowlist", headers={"Authorization": f"Bearer {jwt}"}
    )
    assert r1.status_code == 403

    r2 = await app_client.post(
        "/api/v1/ip-allowlist",
        json={"cidr": "10.0.0.0/8"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert r2.status_code == 403


# ── Enforcement tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_rules_all_ips_pass(_admin, app_client):
    """Default-open: no allowlist rules → any IP can hit protected endpoints."""
    jwt, _ = _admin
    resp = await app_client.get(
        "/api/v1/alerts",
        headers={
            "Authorization": f"Bearer {jwt}",
            "X-Forwarded-For": "1.2.3.4",
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_matching_ip_passes(_admin, app_client):
    """An IP inside the CIDR is allowed."""
    jwt, _ = _admin
    await app_client.post(
        "/api/v1/ip-allowlist",
        json={"cidr": "10.10.0.0/16"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    resp = await app_client.get(
        "/api/v1/alerts",
        headers={
            "Authorization": f"Bearer {jwt}",
            "X-Forwarded-For": "10.10.5.100",
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_nonmatching_ip_blocked(_admin, app_client):
    """An IP outside all active CIDRs is rejected with 403."""
    jwt, _ = _admin
    await app_client.post(
        "/api/v1/ip-allowlist",
        json={"cidr": "10.10.0.0/16"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    resp = await app_client.get(
        "/api/v1/alerts",
        headers={
            "Authorization": f"Bearer {jwt}",
            "X-Forwarded-For": "192.168.99.1",
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_disabled_rule_not_enforced(_admin, app_client):
    """A toggled-off rule does NOT contribute to the allowlist (tenant reverts to open)."""
    jwt, _ = _admin
    # Use loopback CIDR so the test-client (127.0.0.1) can toggle it off.
    create_resp = await app_client.post(
        "/api/v1/ip-allowlist",
        json={"cidr": "127.0.0.0/8"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    rule_id = create_resp.json()["id"]

    # Disable the rule — still allowed because 127.0.0.1 ∈ 127.0.0.0/8 (active)
    toggle_resp = await app_client.patch(
        f"/api/v1/ip-allowlist/{rule_id}/toggle",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert toggle_resp.status_code == 200
    assert toggle_resp.json()["is_active"] is False

    # Tenant now has zero active rules → any IP should be open again
    resp = await app_client.get(
        "/api/v1/alerts",
        headers={
            "Authorization": f"Bearer {jwt}",
            "X-Forwarded-For": "99.99.99.99",
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_tenant_isolation_rules(admin_session, app_client):
    """Tenant A's allowlist rules do NOT affect tenant B's requests."""
    # Tenant A — add restrictive CIDR
    tid_a, uid_a = await _seed_user_with_role(admin_session, role_id=2)
    jwt_a = _jwt(tid_a, uid_a, 2)
    await app_client.post(
        "/api/v1/ip-allowlist",
        json={"cidr": "10.0.0.0/8"},
        headers={"Authorization": f"Bearer {jwt_a}"},
    )

    # Tenant B — no rules; should still be open to any IP
    tid_b, uid_b = await _seed_user_with_role(admin_session, role_id=2)
    jwt_b = _jwt(tid_b, uid_b, 2)

    resp_b = await app_client.get(
        "/api/v1/alerts",
        headers={
            "Authorization": f"Bearer {jwt_b}",
            "X-Forwarded-For": "99.99.99.99",
        },
    )
    assert resp_b.status_code == 200  # B is open regardless of A's rules
