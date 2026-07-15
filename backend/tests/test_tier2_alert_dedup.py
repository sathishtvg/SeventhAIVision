"""Tests for Tier 2 Feature 5: Alert deduplication rules.

Covers:
- CRUD for alert_dedup_rules (list, create, get, update, delete)
- Permission gate (operator cannot manage rules)
- Dedup check suppresses duplicate alerts within window
- Dedup check allows alert when no rule exists
- Dedup check allows alert after window expires (backdate trick)
- Camera-specific and module-specific rule targeting
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _jwt(tenant_id: uuid.UUID, user_id: uuid.UUID, role_id: int) -> str:
    return create_access_token(str(user_id), str(tenant_id), role_id=role_id)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def _admin(admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    return _jwt(tenant_id, user_id, 2), tenant_id, user_id


@pytest_asyncio.fixture
async def _operator(admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    return _jwt(tenant_id, user_id, 4), tenant_id, user_id


@pytest_asyncio.fixture
async def _supervisor(admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=3)
    return _jwt(tenant_id, user_id, 3), tenant_id, user_id


# ── Helper ────────────────────────────────────────────────────────────────────

async def _create_rule(app_client, jwt: str, *, module_type=None, camera_id=None, window_seconds=300):
    resp = await app_client.post(
        "/api/v1/alert-dedup-rules",
        json={"module_type": module_type, "camera_id": camera_id, "window_seconds": window_seconds},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200, f"create_rule failed: {resp.json()}"
    return resp.json()


async def _create_camera(app_client, jwt: str, name: str = "Cam-1") -> str:
    resp = await app_client.post(
        "/api/v1/cameras",
        json={"name": name, "location": "test"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code in (200, 201), f"create_camera failed: {resp.json()}"
    return resp.json()["id"]


async def _create_alert(app_client, jwt: str, camera_id: str, module_type: str = "lpr") -> dict:
    resp = await app_client.post(
        "/api/v1/alerts",
        json={"camera_id": camera_id, "module_type": module_type, "severity": "medium", "title": "Test alert"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    return resp.json()


# ── CRUD tests ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_rules_empty(_admin, app_client):
    jwt, _, _ = _admin
    resp = await app_client.get("/api/v1/alert-dedup-rules", headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_and_list_rule(_admin, app_client):
    jwt, _, _ = _admin
    rule = await _create_rule(app_client, jwt, module_type="lpr", window_seconds=120)
    assert rule["module_type"] == "lpr"
    assert rule["window_seconds"] == 120
    assert rule["is_active"] is True
    assert rule["camera_id"] is None

    resp = await app_client.get("/api/v1/alert-dedup-rules", headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == rule["id"]


@pytest.mark.asyncio
async def test_create_rule_requires_permission(_operator, app_client):
    jwt, _, _ = _operator
    resp = await app_client.post(
        "/api/v1/alert-dedup-rules",
        json={"window_seconds": 60},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_rule(_admin, app_client):
    jwt, _, _ = _admin
    rule = await _create_rule(app_client, jwt, module_type="face", window_seconds=60)
    resp = await app_client.get(
        f"/api/v1/alert-dedup-rules/{rule['id']}",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == rule["id"]
    assert resp.json()["module_type"] == "face"


@pytest.mark.asyncio
async def test_update_rule(_admin, app_client):
    jwt, _, _ = _admin
    rule = await _create_rule(app_client, jwt, window_seconds=60)
    resp = await app_client.put(
        f"/api/v1/alert-dedup-rules/{rule['id']}",
        json={"window_seconds": 900, "is_active": False},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    assert resp.json()["window_seconds"] == 900
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_delete_rule(_admin, app_client):
    jwt, _, _ = _admin
    rule = await _create_rule(app_client, jwt, window_seconds=60)
    del_resp = await app_client.delete(
        f"/api/v1/alert-dedup-rules/{rule['id']}",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert del_resp.status_code == 200

    # Confirm gone
    get_resp = await app_client.get(
        f"/api/v1/alert-dedup-rules/{rule['id']}",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert get_resp.status_code == 404


# ── Dedup logic tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dedup_suppresses_second_alert(_admin, app_client):
    """Within the dedup window, the second alert is suppressed and returns the first alert's id."""
    jwt, _, _ = _admin
    cam_id = await _create_camera(app_client, jwt)

    # Global rule: 5-minute window
    await _create_rule(app_client, jwt, window_seconds=300)

    # First alert: should be created fresh
    r1 = await _create_alert(app_client, jwt, cam_id, "lpr")
    assert r1["deduplicated"] is False
    first_id = r1["id"]

    # Second alert: same camera + module, within window → deduplicated
    r2 = await _create_alert(app_client, jwt, cam_id, "lpr")
    assert r2["deduplicated"] is True
    assert r2["id"] == first_id


@pytest.mark.asyncio
async def test_no_rule_allows_duplicates(_admin, app_client):
    """Without any dedup rule, duplicate alerts are created normally."""
    jwt, _, _ = _admin
    cam_id = await _create_camera(app_client, jwt, "Cam-no-rule")

    r1 = await _create_alert(app_client, jwt, cam_id, "intrusion")
    assert r1["deduplicated"] is False

    r2 = await _create_alert(app_client, jwt, cam_id, "intrusion")
    assert r2["deduplicated"] is False
    assert r1["id"] != r2["id"]


@pytest.mark.asyncio
async def test_dedup_allows_after_window_expires(_admin, app_client, admin_session):
    """An alert older than the dedup window does NOT suppress a new alert."""
    jwt, tenant_id, _ = _admin
    cam_id = await _create_camera(app_client, jwt, "Cam-expired")

    # Rule with 60-second window
    await _create_rule(app_client, jwt, window_seconds=60)

    # Create first alert, then backdate it by 2 minutes
    r1 = await _create_alert(app_client, jwt, cam_id, "lpr")
    assert r1["deduplicated"] is False

    # Backdate the alert so it falls outside the 60s window
    await admin_session.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})
    await admin_session.execute(
        text("UPDATE alerts SET created_at = now() - INTERVAL '5 minutes' WHERE id = :id"),
        {"id": r1["id"]},
    )
    await admin_session.commit()

    # Second alert: now allowed (first is outside window)
    r2 = await _create_alert(app_client, jwt, cam_id, "lpr")
    assert r2["deduplicated"] is False
    assert r2["id"] != r1["id"]


@pytest.mark.asyncio
async def test_camera_specific_rule_only_applies_to_that_camera(_admin, app_client):
    """A rule scoped to camera A should not suppress alerts on camera B."""
    jwt, _, _ = _admin
    cam_a = await _create_camera(app_client, jwt, "Cam-A")
    cam_b = await _create_camera(app_client, jwt, "Cam-B")

    # Rule targeting only cam_a
    await _create_rule(app_client, jwt, camera_id=cam_a, window_seconds=300)

    # First alert on cam_a
    r_a1 = await _create_alert(app_client, jwt, cam_a, "lpr")
    assert r_a1["deduplicated"] is False

    # Second alert on cam_a → deduped
    r_a2 = await _create_alert(app_client, jwt, cam_a, "lpr")
    assert r_a2["deduplicated"] is True

    # Alert on cam_b → NOT deduped (different camera, no global rule)
    r_b1 = await _create_alert(app_client, jwt, cam_b, "lpr")
    assert r_b1["deduplicated"] is False


@pytest.mark.asyncio
async def test_module_type_specific_rule(_admin, app_client):
    """A rule for module_type='lpr' should not suppress alerts for module_type='face'."""
    jwt, _, _ = _admin
    cam_id = await _create_camera(app_client, jwt, "Cam-mod")

    # Rule targeting only 'lpr'
    await _create_rule(app_client, jwt, module_type="lpr", window_seconds=300)

    # LPR alerts: first creates, second is deduped
    r_lpr1 = await _create_alert(app_client, jwt, cam_id, "lpr")
    assert r_lpr1["deduplicated"] is False

    r_lpr2 = await _create_alert(app_client, jwt, cam_id, "lpr")
    assert r_lpr2["deduplicated"] is True

    # Face alerts: not covered by lpr rule → both created
    r_face1 = await _create_alert(app_client, jwt, cam_id, "face")
    assert r_face1["deduplicated"] is False

    r_face2 = await _create_alert(app_client, jwt, cam_id, "face")
    assert r_face2["deduplicated"] is False
    assert r_face1["id"] != r_face2["id"]


@pytest.mark.asyncio
async def test_inactive_rule_does_not_dedup(_admin, app_client):
    """A rule with is_active=False must not suppress any alert."""
    jwt, _, _ = _admin
    cam_id = await _create_camera(app_client, jwt, "Cam-inactive")

    rule = await _create_rule(app_client, jwt, window_seconds=300)
    # Immediately deactivate it
    await app_client.put(
        f"/api/v1/alert-dedup-rules/{rule['id']}",
        json={"window_seconds": 300, "is_active": False},
        headers={"Authorization": f"Bearer {jwt}"},
    )

    r1 = await _create_alert(app_client, jwt, cam_id, "lpr")
    r2 = await _create_alert(app_client, jwt, cam_id, "lpr")
    assert r1["deduplicated"] is False
    assert r2["deduplicated"] is False
    assert r1["id"] != r2["id"]


@pytest.mark.asyncio
async def test_supervisor_cannot_manage_rules(_supervisor, app_client):
    """Supervisors (role 3) do not have alert:dedup:manage permission."""
    jwt, _, _ = _supervisor
    resp = await app_client.get("/api/v1/alert-dedup-rules", headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 403
