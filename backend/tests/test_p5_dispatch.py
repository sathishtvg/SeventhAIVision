"""P5-P: Incident Dispatch, SLA Configuration & Evidence Chain of Custody.

Three sub-routers in dispatch.py — all completely untested at the HTTP level:

SLA router (/api/v1/sla):
- GET  /configs: list all severity-level SLA configs
- PUT  /configs/{severity}: upsert config; invalid severity → 422

Dispatch router (/api/v1/dispatch):
- POST /incidents/{id}: dispatch a guard; sets dispatched_guard_id + sla_deadline_at
- POST /incidents/{id}/arrived: mark guard on-site; 404 if not yet dispatched

Custody router (/api/v1/custody):
- GET  /{evidence_id}: chain-of-custody log (empty list for unknown id)
- POST /{evidence_id}: log an access action; invalid action → 422

Auth:
- All endpoints require authentication (401)
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_camera(client: AsyncClient, name: str = "Dispatch Cam") -> str:
    r = await client.post("/api/v1/cameras", json={"name": name, "location": "HQ"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_incident(client: AsyncClient, camera_id: str, *, severity: str = "high") -> str:
    r = await client.post("/api/v1/incidents", json={
        "camera_id": camera_id,
        "title": "Dispatch Test Incident",
        "description": "Created for dispatch testing",
        "severity": severity,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _get_user_id(client: AsyncClient) -> str:
    r = await client.get("/api/v1/users")
    assert r.status_code == 200, r.text
    users = r.json()
    assert len(users) >= 1, "Need at least one user in the tenant"
    return str(users[0]["id"])


async def _upsert_sla_config(
    client: AsyncClient,
    severity: str = "high",
    *,
    ack: int = 120,
    dispatch: int = 300,
    resolve: int = 3600,
) -> dict:
    r = await client.put(f"/api/v1/sla/configs/{severity}", json={
        "ack_within_seconds": ack,
        "dispatch_within_seconds": dispatch,
        "resolve_within_seconds": resolve,
    })
    assert r.status_code == 200, r.text
    return r.json()


# ── SLA Config: List ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_sla_configs_returns_list(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/sla/configs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_list_sla_configs_has_expected_fields_after_upsert(auth_client: AsyncClient):
    await _upsert_sla_config(auth_client, "critical", ack=60, dispatch=180, resolve=1800)

    r = await auth_client.get("/api/v1/sla/configs")
    assert r.status_code == 200
    configs = r.json()
    critical = next((c for c in configs if c["severity"] == "critical"), None)
    assert critical is not None
    for field in ("id", "severity", "ack_within_seconds", "dispatch_within_seconds", "resolve_within_seconds"):
        assert field in critical, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_list_sla_configs_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/sla/configs")
    assert r.status_code == 401


# ── SLA Config: Upsert ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_sla_config_creates_entry(auth_client: AsyncClient):
    config = await _upsert_sla_config(auth_client, "medium", ack=300, dispatch=600, resolve=7200)
    assert config["severity"] == "medium"
    assert config["ack_within_seconds"] == 300
    assert config["dispatch_within_seconds"] == 600
    assert config["resolve_within_seconds"] == 7200


@pytest.mark.asyncio
async def test_upsert_sla_config_all_valid_severities(auth_client: AsyncClient):
    for sev in ("critical", "high", "medium", "low", "info"):
        config = await _upsert_sla_config(auth_client, sev)
        assert config["severity"] == sev


@pytest.mark.asyncio
async def test_upsert_sla_config_idempotent(auth_client: AsyncClient):
    await _upsert_sla_config(auth_client, "low", ack=600, dispatch=1200, resolve=14400)
    # Upsert again with different values — should update, not duplicate
    config = await _upsert_sla_config(auth_client, "low", ack=900, dispatch=1800, resolve=21600)
    assert config["ack_within_seconds"] == 900

    r = await auth_client.get("/api/v1/sla/configs")
    low_rows = [c for c in r.json() if c["severity"] == "low"]
    assert len(low_rows) == 1  # exactly one row per severity, not duplicated


@pytest.mark.asyncio
async def test_upsert_sla_config_invalid_severity_422(auth_client: AsyncClient):
    r = await auth_client.put("/api/v1/sla/configs/catastrophic", json={
        "ack_within_seconds": 60,
        "dispatch_within_seconds": 180,
        "resolve_within_seconds": 3600,
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upsert_sla_config_requires_auth(client: AsyncClient):
    r = await client.put("/api/v1/sla/configs/high", json={
        "ack_within_seconds": 120,
        "dispatch_within_seconds": 300,
        "resolve_within_seconds": 3600,
    })
    assert r.status_code == 401


# ── Dispatch: Assign Guard ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_guard_sets_fields(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "DispatchGuardCam")
    incident_id = await _make_incident(auth_client, cam)
    guard_id = await _get_user_id(auth_client)

    r = await auth_client.post(f"/api/v1/dispatch/incidents/{incident_id}", json={
        "guard_user_id": guard_id,
        "dispatch_notes": "Proceed to entrance",
    })
    assert r.status_code == 200
    body = r.json()
    assert str(body["dispatched_guard_id"]) == guard_id
    assert body["dispatched_at"] is not None


@pytest.mark.asyncio
async def test_dispatch_guard_transitions_status_to_in_progress(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "DispatchStatusCam")
    incident_id = await _make_incident(auth_client, cam)
    guard_id = await _get_user_id(auth_client)

    r = await auth_client.post(f"/api/v1/dispatch/incidents/{incident_id}", json={
        "guard_user_id": guard_id,
    })
    assert r.status_code == 200
    assert r.json()["status"] == "in_progress"


@pytest.mark.asyncio
async def test_dispatch_guard_with_sla_config_sets_deadline(auth_client: AsyncClient):
    # Create SLA config first so dispatch can compute the deadline
    await _upsert_sla_config(auth_client, "high", ack=120, dispatch=300, resolve=3600)

    cam = await _make_camera(auth_client, "DispatchSLACam")
    incident_id = await _make_incident(auth_client, cam, severity="high")
    guard_id = await _get_user_id(auth_client)

    r = await auth_client.post(f"/api/v1/dispatch/incidents/{incident_id}", json={
        "guard_user_id": guard_id,
    })
    assert r.status_code == 200
    assert r.json()["sla_deadline_at"] is not None


@pytest.mark.asyncio
async def test_dispatch_nonexistent_incident_404(auth_client: AsyncClient):
    guard_id = await _get_user_id(auth_client)

    r = await auth_client.post(
        "/api/v1/dispatch/incidents/00000000-0000-0000-0000-000000000099",
        json={"guard_user_id": guard_id},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_dispatch_requires_auth(client: AsyncClient):
    r = await client.post(
        "/api/v1/dispatch/incidents/00000000-0000-0000-0000-000000000001",
        json={"guard_user_id": "00000000-0000-0000-0000-000000000002"},
    )
    assert r.status_code == 401


# ── Dispatch: Guard Arrived ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_guard_arrived_sets_guard_arrived_at(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "ArrivedCam")
    incident_id = await _make_incident(auth_client, cam)
    guard_id = await _get_user_id(auth_client)

    # Dispatch first
    await auth_client.post(f"/api/v1/dispatch/incidents/{incident_id}", json={
        "guard_user_id": guard_id,
    })

    # Mark arrived
    r = await auth_client.post(f"/api/v1/dispatch/incidents/{incident_id}/arrived")
    assert r.status_code == 200
    body = r.json()
    assert body["guard_arrived_at"] is not None


@pytest.mark.asyncio
async def test_guard_arrived_without_dispatch_404(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "NoDispatchCam")
    incident_id = await _make_incident(auth_client, cam)

    # Mark arrived without dispatching first
    r = await auth_client.post(f"/api/v1/dispatch/incidents/{incident_id}/arrived")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_guard_arrived_requires_auth(client: AsyncClient):
    r = await client.post(
        "/api/v1/dispatch/incidents/00000000-0000-0000-0000-000000000001/arrived"
    )
    assert r.status_code == 401


# ── Evidence Custody: Log Access ──────────────────────────────────────────────

_DUMMY_EVIDENCE_ID = "00000000-aaaa-bbbb-cccc-000000000001"


@pytest.mark.asyncio
async def test_log_evidence_access_returns_id(auth_client: AsyncClient):
    r = await auth_client.post(
        f"/api/v1/custody/{_DUMMY_EVIDENCE_ID}?action=view"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["logged"] is True
    assert "id" in body
    assert "accessed_at" in body


@pytest.mark.asyncio
async def test_log_evidence_access_all_valid_actions(auth_client: AsyncClient):
    for action in ("view", "download", "export", "custody_transfer"):
        r = await auth_client.post(
            f"/api/v1/custody/{_DUMMY_EVIDENCE_ID}?action={action}"
        )
        assert r.status_code == 200, f"Failed for action={action}: {r.text}"
        assert r.json()["logged"] is True


@pytest.mark.asyncio
async def test_log_evidence_access_invalid_action_422(auth_client: AsyncClient):
    r = await auth_client.post(
        f"/api/v1/custody/{_DUMMY_EVIDENCE_ID}?action=delete"   # not a valid action
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_log_evidence_access_requires_auth(client: AsyncClient):
    r = await client.post(
        f"/api/v1/custody/{_DUMMY_EVIDENCE_ID}?action=view"
    )
    assert r.status_code == 401


# ── Evidence Custody: Get Log ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_custody_log_returns_list(auth_client: AsyncClient):
    r = await auth_client.get(f"/api/v1/custody/{_DUMMY_EVIDENCE_ID}")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_get_custody_log_includes_logged_access(auth_client: AsyncClient):
    # Log a view access
    await auth_client.post(
        f"/api/v1/custody/{_DUMMY_EVIDENCE_ID}?action=view"
    )

    r = await auth_client.get(f"/api/v1/custody/{_DUMMY_EVIDENCE_ID}")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    actions = [row["action"] for row in rows]
    assert "view" in actions


@pytest.mark.asyncio
async def test_get_custody_log_has_expected_fields(auth_client: AsyncClient):
    await auth_client.post(
        f"/api/v1/custody/{_DUMMY_EVIDENCE_ID}?action=download"
    )

    r = await auth_client.get(f"/api/v1/custody/{_DUMMY_EVIDENCE_ID}")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    row = rows[0]
    for field in ("id", "evidence_id", "action", "accessed_at"):
        assert field in row, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_get_custody_log_unknown_evidence_returns_empty(auth_client: AsyncClient):
    unknown = "00000000-0000-0000-0000-999999999999"
    r = await auth_client.get(f"/api/v1/custody/{unknown}")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_get_custody_log_requires_auth(client: AsyncClient):
    r = await client.get(f"/api/v1/custody/{_DUMMY_EVIDENCE_ID}")
    assert r.status_code == 401
