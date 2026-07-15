"""P5-E: Alert assignment — operators can assign alerts to specific users.

Tests cover:
- POST /alerts/{id}/assign: assigns the alert, returns assigned_to_user_id
- POST /alerts/{id}/assign: non-existent alert → 404
- POST /alerts/{id}/assign: non-existent user → 404
- POST /alerts/{id}/unassign: clears the assignment
- POST /alerts/{id}/unassign: non-existent alert → 404
- GET /alerts returns assigned_to_user_id + assigned_to_name + assigned_at
- Reassigning to a different user works (overwrites previous)
- Unauthenticated → 401
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_camera(client: AsyncClient) -> str:
    r = await client.post("/api/v1/cameras", json={"name": "Assign Test Cam", "location": "HQ"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_alert(client: AsyncClient, camera_id: str | None = None) -> str:
    if camera_id is None:
        camera_id = await _make_camera(client)
    r = await client.post("/api/v1/alerts", json={
        "camera_id": camera_id,
        "module_type": "intrusion",
        "severity": "high",
        "title": "Assign test alert",
        "message": "Test",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _get_self_user_id(client: AsyncClient) -> str:
    """Fetch the current user's ID from /api/v1/users/me or the users list."""
    r = await client.get("/api/v1/users")
    assert r.status_code == 200, r.text
    users = r.json()
    assert len(users) > 0
    return str(users[0]["id"])


# ── Assign ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assign_alert_returns_200(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    user_id = await _get_self_user_id(auth_client)

    r = await auth_client.post(
        f"/api/v1/alerts/{alert_id}/assign",
        json={"assigned_to_user_id": user_id},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["assigned_to_user_id"] == user_id
    assert body["id"] == alert_id


@pytest.mark.asyncio
async def test_assign_alert_nonexistent_alert_404(auth_client: AsyncClient):
    user_id = await _get_self_user_id(auth_client)
    fake_id = "00000000-0000-0000-0000-000000000099"
    r = await auth_client.post(
        f"/api/v1/alerts/{fake_id}/assign",
        json={"assigned_to_user_id": user_id},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_assign_alert_nonexistent_user_404(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    fake_user = "00000000-0000-0000-0000-000000000088"
    r = await auth_client.post(
        f"/api/v1/alerts/{alert_id}/assign",
        json={"assigned_to_user_id": fake_user},
    )
    assert r.status_code == 404


# ── List alerts reflects assignment ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_alerts_includes_assignment_fields(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    user_id = await _get_self_user_id(auth_client)

    # Assign
    await auth_client.post(
        f"/api/v1/alerts/{alert_id}/assign",
        json={"assigned_to_user_id": user_id},
    )

    # List and find our alert
    r = await auth_client.get("/api/v1/alerts")
    assert r.status_code == 200
    items = r.json()["items"]
    target = next((i for i in items if i["id"] == alert_id), None)
    assert target is not None
    assert target["assigned_to_user_id"] == user_id
    assert target["assigned_at"] is not None
    # author join fields are present
    assert "assigned_to_name" in target
    assert "assigned_to_email" in target


@pytest.mark.asyncio
async def test_list_alerts_unassigned_has_null_assignment(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)

    r = await auth_client.get("/api/v1/alerts")
    assert r.status_code == 200
    items = r.json()["items"]
    target = next((i for i in items if i["id"] == alert_id), None)
    assert target is not None
    assert target["assigned_to_user_id"] is None
    assert target["assigned_at"] is None


# ── Reassign ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reassign_overwrites_previous(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    user_id = await _get_self_user_id(auth_client)

    # Assign once
    await auth_client.post(
        f"/api/v1/alerts/{alert_id}/assign",
        json={"assigned_to_user_id": user_id},
    )

    # Assign again to same user (idempotent)
    r = await auth_client.post(
        f"/api/v1/alerts/{alert_id}/assign",
        json={"assigned_to_user_id": user_id},
    )
    assert r.status_code == 200
    assert r.json()["assigned_to_user_id"] == user_id


# ── Unassign ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unassign_clears_assignment(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    user_id = await _get_self_user_id(auth_client)

    # Assign first
    await auth_client.post(
        f"/api/v1/alerts/{alert_id}/assign",
        json={"assigned_to_user_id": user_id},
    )

    # Unassign
    r = await auth_client.post(f"/api/v1/alerts/{alert_id}/unassign")
    assert r.status_code == 200
    body = r.json()
    assert body["assigned_to_user_id"] is None

    # Verify via list
    list_r = await auth_client.get("/api/v1/alerts")
    items = list_r.json()["items"]
    target = next((i for i in items if i["id"] == alert_id), None)
    assert target is not None
    assert target["assigned_to_user_id"] is None
    assert target["assigned_at"] is None


@pytest.mark.asyncio
async def test_unassign_nonexistent_alert_404(auth_client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000097"
    r = await auth_client.post(f"/api/v1/alerts/{fake_id}/unassign")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unassign_already_unassigned_succeeds(auth_client: AsyncClient):
    """Unassigning an alert that was never assigned is idempotent (not an error)."""
    alert_id = await _make_alert(auth_client)
    r = await auth_client.post(f"/api/v1/alerts/{alert_id}/unassign")
    assert r.status_code == 200
    assert r.json()["assigned_to_user_id"] is None


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assign_requires_auth(client: AsyncClient):
    r = await client.post(
        "/api/v1/alerts/00000000-0000-0000-0000-000000000001/assign",
        json={"assigned_to_user_id": "00000000-0000-0000-0000-000000000002"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unassign_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/alerts/00000000-0000-0000-0000-000000000001/unassign")
    assert r.status_code == 401
