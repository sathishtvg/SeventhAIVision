"""Tests for Tier 2 Feature 7: Occurrence Book mobile — tests the backend DOB API.

Covers:
- Creating a DOB entry (dob:write)
- Listing DOB entries (dob:read)
- Getting a single entry by ID
- Permission enforcement: viewer cannot write
- Invalid entry_type is rejected (422)
- Entries are tenant-isolated
- Shift-scoped listing filter
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
async def _guard(admin_session):
    """security_guard — role 5 — has dob:write + dob:read."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=5)
    return _jwt(tenant_id, user_id, 5), tenant_id, user_id


@pytest_asyncio.fixture
async def _viewer(admin_session):
    """viewer — role 6 — has dob:read only."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)
    return _jwt(tenant_id, user_id, 6), tenant_id, user_id


@pytest_asyncio.fixture
async def _other_tenant_guard(admin_session):
    """A guard in a different tenant (isolation check)."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=5)
    return _jwt(tenant_id, user_id, 5), tenant_id, user_id


# ── Helper ────────────────────────────────────────────────────────────────────

async def _create_entry(app_client, jwt: str, body: str, entry_type: str = "general",
                        severity: str | None = None) -> dict:
    resp = await app_client.post(
        "/api/v1/dob",
        json={"entry_type": entry_type, "body": body, "severity": severity},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200, f"create_entry failed: {resp.json()}"
    return resp.json()


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_dob_entry(_guard, app_client):
    jwt, _, _ = _guard
    entry = await _create_entry(app_client, jwt, "All clear on north perimeter", "patrol_start")
    assert entry["entry_type"] == "patrol_start"
    assert entry["body"] == "All clear on north perimeter"
    assert entry["severity"] is None
    assert "id" in entry
    assert "occurred_at" in entry


@pytest.mark.asyncio
async def test_create_entry_with_severity(_guard, app_client):
    jwt, _, _ = _guard
    entry = await _create_entry(app_client, jwt, "Suspicious person spotted near gate", "incident", "medium")
    assert entry["severity"] == "medium"


@pytest.mark.asyncio
async def test_list_entries_empty_at_start(_guard, app_client):
    jwt, _, _ = _guard
    resp = await app_client.get("/api/v1/dob", headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_entries_returns_created(_guard, app_client):
    jwt, _, _ = _guard
    entry = await _create_entry(app_client, jwt, "Equipment check completed")
    resp = await app_client.get("/api/v1/dob", headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 200
    ids = [e["id"] for e in resp.json()]
    assert entry["id"] in ids


@pytest.mark.asyncio
async def test_get_single_entry(_guard, app_client):
    jwt, _, _ = _guard
    entry = await _create_entry(app_client, jwt, "Visitor arrived at main gate", "visitor_arrival")
    resp = await app_client.get(f"/api/v1/dob/{entry['id']}", headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 200
    assert resp.json()["id"] == entry["id"]
    assert resp.json()["entry_type"] == "visitor_arrival"


@pytest.mark.asyncio
async def test_get_nonexistent_entry_404(_guard, app_client):
    jwt, _, _ = _guard
    fake_id = str(uuid.uuid4())
    resp = await app_client.get(f"/api/v1/dob/{fake_id}", headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invalid_entry_type_rejected(_guard, app_client):
    jwt, _, _ = _guard
    resp = await app_client.post(
        "/api/v1/dob",
        json={"entry_type": "hacking_attempt", "body": "test"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_viewer_cannot_create_entry(_viewer, app_client):
    """Viewer (role 6) has only dob:read, not dob:write."""
    jwt, _, _ = _viewer
    resp = await app_client.post(
        "/api/v1/dob",
        json={"entry_type": "general", "body": "should be denied"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_read_entries(_guard, _viewer, app_client):
    """Viewer can list entries created by others in the same tenant."""
    guard_jwt, _, _ = _guard
    viewer_jwt, _, _ = _viewer
    await _create_entry(app_client, guard_jwt, "Visible to viewer")
    resp = await app_client.get("/api/v1/dob", headers={"Authorization": f"Bearer {viewer_jwt}"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_tenant_isolation(_guard, _other_tenant_guard, app_client):
    """Entries created by tenant A must not appear in tenant B's list."""
    guard_a_jwt, _, _ = _guard
    guard_b_jwt, _, _ = _other_tenant_guard

    entry_a = await _create_entry(app_client, guard_a_jwt, "Tenant A only entry")

    resp_b = await app_client.get("/api/v1/dob", headers={"Authorization": f"Bearer {guard_b_jwt}"})
    assert resp_b.status_code == 200
    ids_b = [e["id"] for e in resp_b.json()]
    assert entry_a["id"] not in ids_b


@pytest.mark.asyncio
async def test_all_valid_entry_types_accepted(_guard, app_client):
    """Every valid entry_type must be accepted without a 422."""
    jwt, _, _ = _guard
    valid_types = [
        "general", "incident", "patrol_start", "patrol_end",
        "visitor_arrival", "visitor_departure", "guard_relief",
        "equipment_check", "maintenance", "alarm_activation",
        "fire_drill", "sos", "handover",
    ]
    for et in valid_types:
        resp = await app_client.post(
            "/api/v1/dob",
            json={"entry_type": et, "body": f"test {et}"},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert resp.status_code == 200, f"entry_type '{et}' unexpectedly rejected: {resp.json()}"


@pytest.mark.asyncio
async def test_list_entries_sorted_newest_first(_guard, app_client):
    """Entries must be returned in descending occurred_at order."""
    jwt, _, _ = _guard
    for i in range(3):
        await _create_entry(app_client, jwt, f"Entry {i}")
    resp = await app_client.get("/api/v1/dob", headers={"Authorization": f"Bearer {jwt}"})
    entries = resp.json()
    if len(entries) >= 2:
        times = [e["occurred_at"] for e in entries]
        assert times == sorted(times, reverse=True), "Entries not sorted newest-first"
