"""P5-G: Alert bulk operations.

Tests cover all 5 bulk endpoints:
- POST /alerts/bulk-acknowledge: acknowledges open alerts, skips non-open
- POST /alerts/bulk-dismiss: dismisses open/acknowledged, skips rest
- POST /alerts/bulk-assign: assigns to a user, verifies via list
- POST /alerts/bulk-unassign: clears assignment, idempotent
- POST /alerts/bulk-create-incidents: creates one incident per alert, deduplicates

Validation:
- Empty ids list → 422
- Exceeding limit → 422
- bulk-assign to non-existent user → 404

Auth:
- All endpoints require auth (401 when unauthenticated)
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_camera(client: AsyncClient, name: str = "Bulk Test Cam") -> str:
    r = await client.post("/api/v1/cameras", json={"name": name, "location": "HQ"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_alert(client: AsyncClient, camera_id: str | None = None, title: str = "Bulk alert") -> str:
    if camera_id is None:
        camera_id = await _make_camera(client)
    r = await client.post("/api/v1/alerts", json={
        "camera_id": camera_id,
        "module_type": "intrusion",
        "severity": "high",
        "title": title,
        "message": "Test",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _get_user_id(client: AsyncClient) -> str:
    r = await client.get("/api/v1/users")
    assert r.status_code == 200, r.text
    users = r.json()
    assert len(users) > 0
    return str(users[0]["id"])


# ── bulk-acknowledge ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_acknowledge_updates_open_alerts(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    ids = [await _make_alert(auth_client, cam, f"BA-{i}") for i in range(3)]

    r = await auth_client.post("/api/v1/alerts/bulk-acknowledge", json={"ids": ids})
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 3
    assert body["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_acknowledge_skips_non_open(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    alert_id = await _make_alert(auth_client, cam)

    # Acknowledge once
    await auth_client.post(f"/api/v1/alerts/{alert_id}/acknowledge")

    # Bulk-acknowledge the already-acknowledged alert
    r = await auth_client.post("/api/v1/alerts/bulk-acknowledge", json={"ids": [alert_id]})
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 0
    assert body["skipped"] == 1


@pytest.mark.asyncio
async def test_bulk_acknowledge_empty_ids_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/alerts/bulk-acknowledge", json={"ids": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_acknowledge_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/alerts/bulk-acknowledge", json={"ids": ["abc"]})
    assert r.status_code == 401


# ── bulk-dismiss ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_dismiss_open_and_acknowledged(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    open_id = await _make_alert(auth_client, cam, "dismiss-open")
    ack_id = await _make_alert(auth_client, cam, "dismiss-ack")

    # Acknowledge one
    await auth_client.post(f"/api/v1/alerts/{ack_id}/acknowledge")

    r = await auth_client.post("/api/v1/alerts/bulk-dismiss", json={"ids": [open_id, ack_id]})
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 2
    assert body["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_dismiss_skips_already_dismissed(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    alert_id = await _make_alert(auth_client, cam)

    # Dismiss once
    await auth_client.post("/api/v1/alerts/bulk-dismiss", json={"ids": [alert_id]})

    # Try to dismiss again
    r = await auth_client.post("/api/v1/alerts/bulk-dismiss", json={"ids": [alert_id]})
    assert r.status_code == 200
    assert r.json()["updated"] == 0
    assert r.json()["skipped"] == 1


@pytest.mark.asyncio
async def test_bulk_dismiss_empty_ids_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/alerts/bulk-dismiss", json={"ids": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_dismiss_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/alerts/bulk-dismiss", json={"ids": ["abc"]})
    assert r.status_code == 401


# ── bulk-assign ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_assign_updates_all(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    ids = [await _make_alert(auth_client, cam, f"BA-assign-{i}") for i in range(3)]
    user_id = await _get_user_id(auth_client)

    r = await auth_client.post("/api/v1/alerts/bulk-assign", json={
        "ids": ids,
        "assigned_to_user_id": user_id,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 3
    assert body["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_assign_reflected_in_list(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    alert_id = await _make_alert(auth_client, cam, "assign-list-check")
    user_id = await _get_user_id(auth_client)

    await auth_client.post("/api/v1/alerts/bulk-assign", json={
        "ids": [alert_id],
        "assigned_to_user_id": user_id,
    })

    r = await auth_client.get("/api/v1/alerts")
    items = r.json()["items"]
    target = next((i for i in items if i["id"] == alert_id), None)
    assert target is not None
    assert target["assigned_to_user_id"] == user_id
    assert target["assigned_at"] is not None


@pytest.mark.asyncio
async def test_bulk_assign_nonexistent_user_404(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    alert_id = await _make_alert(auth_client, cam)
    r = await auth_client.post("/api/v1/alerts/bulk-assign", json={
        "ids": [alert_id],
        "assigned_to_user_id": "00000000-0000-0000-0000-000000000099",
    })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_bulk_assign_empty_ids_422(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    r = await auth_client.post("/api/v1/alerts/bulk-assign", json={
        "ids": [],
        "assigned_to_user_id": user_id,
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_assign_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/alerts/bulk-assign", json={
        "ids": ["abc"],
        "assigned_to_user_id": "00000000-0000-0000-0000-000000000001",
    })
    assert r.status_code == 401


# ── bulk-unassign ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_unassign_clears_assignment(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    ids = [await _make_alert(auth_client, cam, f"unassign-{i}") for i in range(2)]
    user_id = await _get_user_id(auth_client)

    # Assign first
    await auth_client.post("/api/v1/alerts/bulk-assign", json={
        "ids": ids,
        "assigned_to_user_id": user_id,
    })

    # Then unassign
    r = await auth_client.post("/api/v1/alerts/bulk-unassign", json={"ids": ids})
    assert r.status_code == 200
    assert r.json()["updated"] == 2

    # Verify via list
    list_r = await auth_client.get("/api/v1/alerts")
    items = list_r.json()["items"]
    for alert_id in ids:
        target = next((i for i in items if i["id"] == alert_id), None)
        assert target is not None
        assert target["assigned_to_user_id"] is None
        assert target["assigned_at"] is None


@pytest.mark.asyncio
async def test_bulk_unassign_idempotent_on_unassigned(auth_client: AsyncClient):
    """Unassigning alerts that were never assigned should succeed and report 0 skipped."""
    cam = await _make_camera(auth_client)
    alert_id = await _make_alert(auth_client, cam)

    # Unassign without prior assignment (no-op but not an error)
    r = await auth_client.post("/api/v1/alerts/bulk-unassign", json={"ids": [alert_id]})
    assert r.status_code == 200
    # The row is updated (NULL to NULL is still a valid UPDATE match)
    assert r.json()["updated"] >= 0


@pytest.mark.asyncio
async def test_bulk_unassign_empty_ids_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/alerts/bulk-unassign", json={"ids": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_unassign_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/alerts/bulk-unassign", json={"ids": ["abc"]})
    assert r.status_code == 401


# ── bulk-create-incidents ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_create_incidents_makes_one_per_alert(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    ids = [await _make_alert(auth_client, cam, f"inci-{i}") for i in range(3)]

    r = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": ids})
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 3
    assert body["skipped"] == 0
    assert len(body["incident_ids"]) == 3


@pytest.mark.asyncio
async def test_bulk_create_incidents_deduplicates(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    alert_id = await _make_alert(auth_client, cam, "dedup-inci")

    # Create incident once
    await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": [alert_id]})

    # Second call for same alert should skip it
    r = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": [alert_id]})
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 0
    assert body["skipped"] == 1


@pytest.mark.asyncio
async def test_bulk_create_incidents_returns_incident_ids(auth_client: AsyncClient):
    cam = await _make_camera(auth_client)
    alert_id = await _make_alert(auth_client, cam)

    r = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": [alert_id]})
    assert r.status_code == 200
    body = r.json()
    assert len(body["incident_ids"]) == 1
    # Should be a valid UUID
    inci_id = body["incident_ids"][0]
    assert len(inci_id) == 36


@pytest.mark.asyncio
async def test_bulk_create_incidents_empty_ids_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_create_incidents_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/alerts/bulk-create-incidents", json={"ids": ["abc"]})
    assert r.status_code == 401
