"""Comprehensive test suite for Phase 5 features:
- Client portal role (role_id=7) permissions
- Advanced detection endpoints (tampering, abandoned, falls)
- False positive flagging flow
- Heatmap analytics endpoint
- Analytics summary correctness
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token, hash_password


# ─── Seed helpers ─────────────────────────────────────────────────────────────

async def _seed_user_with_role(admin_session, role_id: int) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'T', :slug)"),
        {"id": tenant_id, "slug": f"t-{tenant_id.hex[:8]}"},
    )
    user_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
            "                   totp_enabled) "
            "VALUES (:id, :tid, CAST(:rid AS smallint), :email, :pw, "
            "        CAST(:rid AS smallint) = 1)"
        ),
        {"id": user_id, "tid": tenant_id, "rid": role_id,
         "email": f"{user_id}@example.com", "pw": hash_password("x")},
    )
    await admin_session.commit()
    return tenant_id, user_id


async def _seed_camera(admin_session, tenant_id: uuid.UUID) -> uuid.UUID:
    camera_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO cameras (id, tenant_id, name, is_active) "
            "VALUES (:id, :tid, 'Test Camera', TRUE)"
        ),
        {"id": camera_id, "tid": tenant_id},
    )
    await admin_session.commit()
    return camera_id


async def _seed_alert(
    admin_session,
    tenant_id: uuid.UUID,
    camera_id: uuid.UUID,
    *,
    status: str = "open",
    severity: str = "medium",
) -> uuid.UUID:
    alert_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, title, status) "
            "VALUES (:id, :tid, :cid, 'lpr', :severity, 'Test Alert', :status)"
        ),
        {"id": alert_id, "tid": tenant_id, "cid": camera_id,
         "severity": severity, "status": status},
    )
    await admin_session.commit()
    return alert_id


# ─── Client role (7) permission tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_role_can_read_alerts(app_client, admin_session):
    """Client (role 7) has alert:read — alert list must return 200."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=7)
    token = create_access_token(str(user_id), str(tenant_id), role_id=7)
    resp = await app_client.get(
        "/api/v1/alerts", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio
async def test_client_role_cannot_create_cameras(app_client, admin_session):
    """Client (role 7) has no camera:create — must get 403."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=7)
    token = create_access_token(str(user_id), str(tenant_id), role_id=7)
    resp = await app_client.post(
        "/api/v1/cameras",
        json={"name": "Client Cam"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_client_role_cannot_acknowledge_alerts(app_client, admin_session):
    """Client (role 7) has no alert:acknowledge — must get 403."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=7)
    camera_id = await _seed_camera(admin_session, tenant_id)
    alert_id = await _seed_alert(admin_session, tenant_id, camera_id)
    token = create_access_token(str(user_id), str(tenant_id), role_id=7)
    resp = await app_client.post(
        f"/api/v1/alerts/{alert_id}/acknowledge",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_client_role_can_access_heatmap(app_client, admin_session):
    """Client (role 7) has alert:read — heatmap endpoint must return 200."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=7)
    token = create_access_token(str(user_id), str(tenant_id), role_id=7)
    resp = await app_client.get(
        "/api/v1/analytics/heatmap",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_client_role_can_read_advanced_detections(app_client, admin_session):
    """Client (role 7) has tampering:read, abandoned:read, fall:read (migration 0012)."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=7)
    token = create_access_token(str(user_id), str(tenant_id), role_id=7)
    headers = {"Authorization": f"Bearer {token}"}
    for endpoint in (
        "/api/v1/advanced-detections/tampering",
        "/api/v1/advanced-detections/abandoned",
        "/api/v1/advanced-detections/falls",
    ):
        resp = await app_client.get(endpoint, headers=headers)
        assert resp.status_code == 200, f"{endpoint} returned {resp.status_code}"
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_client_role_cannot_manage_users(app_client, admin_session):
    """Client (role 7) has no user:read — user list must return 403."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=7)
    token = create_access_token(str(user_id), str(tenant_id), role_id=7)
    resp = await app_client.get(
        "/api/v1/users", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


# ─── Advanced detection permission tests ─────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_access_advanced_detections(app_client, admin_session):
    """Viewer (role 6) has no tampering:read/abandoned:read/fall:read (migration 0011 grants
    these to roles 1-5 only; migration 0012 grants to role 7 only)."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)
    token = create_access_token(str(user_id), str(tenant_id), role_id=6)
    headers = {"Authorization": f"Bearer {token}"}
    for endpoint in (
        "/api/v1/advanced-detections/tampering",
        "/api/v1/advanced-detections/abandoned",
        "/api/v1/advanced-detections/falls",
    ):
        resp = await app_client.get(endpoint, headers=headers)
        assert resp.status_code == 403, (
            f"{endpoint} returned {resp.status_code} for viewer (expected 403)"
        )


@pytest.mark.asyncio
async def test_operator_can_access_advanced_detections(app_client, admin_session):
    """Operator (role 4) has tampering:read, abandoned:read, fall:read (migration 0011 roles 1-5)."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    token = create_access_token(str(user_id), str(tenant_id), role_id=4)
    headers = {"Authorization": f"Bearer {token}"}
    for endpoint in (
        "/api/v1/advanced-detections/tampering",
        "/api/v1/advanced-detections/abandoned",
        "/api/v1/advanced-detections/falls",
    ):
        resp = await app_client.get(endpoint, headers=headers)
        assert resp.status_code == 200, (
            f"{endpoint} returned {resp.status_code} for operator (expected 200)"
        )


@pytest.mark.asyncio
async def test_security_guard_can_access_advanced_detections(app_client, admin_session):
    """Security guard (role 5) has tampering:read/abandoned:read/fall:read (migration 0011)."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=5)
    token = create_access_token(str(user_id), str(tenant_id), role_id=5)
    headers = {"Authorization": f"Bearer {token}"}
    for endpoint in (
        "/api/v1/advanced-detections/tampering",
        "/api/v1/advanced-detections/abandoned",
        "/api/v1/advanced-detections/falls",
    ):
        resp = await app_client.get(endpoint, headers=headers)
        assert resp.status_code == 200, (
            f"{endpoint} returned {resp.status_code} for security_guard (expected 200)"
        )


@pytest.mark.asyncio
async def test_unauthenticated_cannot_access_advanced_detections(app_client):
    """No token → all advanced-detection endpoints must reject with 401 or 403."""
    for endpoint in (
        "/api/v1/advanced-detections/tampering",
        "/api/v1/advanced-detections/abandoned",
        "/api/v1/advanced-detections/falls",
    ):
        resp = await app_client.get(endpoint)
        assert resp.status_code in (401, 403), (
            f"{endpoint} returned {resp.status_code} without auth (expected 401/403)"
        )


@pytest.mark.asyncio
async def test_advanced_detections_return_list(app_client, admin_session):
    """All three endpoints return a JSON list (empty for a fresh tenant with no events)."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    headers = {"Authorization": f"Bearer {token}"}
    for endpoint in (
        "/api/v1/advanced-detections/tampering",
        "/api/v1/advanced-detections/abandoned",
        "/api/v1/advanced-detections/falls",
    ):
        resp = await app_client.get(endpoint, headers=headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list), f"{endpoint} did not return a list"


# ─── Heatmap endpoint tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_heatmap_unauthenticated_rejected(app_client):
    resp = await app_client.get("/api/v1/analytics/heatmap")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_heatmap_returns_list(app_client, admin_session):
    """Heatmap returns a list (empty if no cameras for this tenant)."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    resp = await app_client.get(
        "/api/v1/analytics/heatmap",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_heatmap_includes_seeded_camera(app_client, admin_session):
    """A tenant with an active camera sees it in the heatmap response."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    camera_id = await _seed_camera(admin_session, tenant_id)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    resp = await app_client.get(
        "/api/v1/analytics/heatmap",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    camera_ids = [str(row["camera_id"]) for row in resp.json()]
    assert str(camera_id) in camera_ids


@pytest.mark.asyncio
async def test_heatmap_row_has_required_fields(app_client, admin_session):
    """Each heatmap row must carry the fields the frontend SVG renderer expects."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    await _seed_camera(admin_session, tenant_id)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    resp = await app_client.get(
        "/api/v1/analytics/heatmap",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert rows, "Expected at least one camera row"
    row = rows[0]
    for field in (
        "camera_id", "camera_name", "stream_status",
        "total_detections", "total_alerts", "open_alerts",
        "critical_alerts", "high_alerts",
    ):
        assert field in row, f"Heatmap row missing field: {field}"


@pytest.mark.asyncio
async def test_heatmap_hours_param_validation(app_client, admin_session):
    """hours=0 and hours=721 must be rejected with 422; hours=24 must succeed."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await app_client.get("/api/v1/analytics/heatmap?hours=0",   headers=headers)).status_code == 422
    assert (await app_client.get("/api/v1/analytics/heatmap?hours=721", headers=headers)).status_code == 422
    assert (await app_client.get("/api/v1/analytics/heatmap?hours=24",  headers=headers)).status_code == 200
    assert (await app_client.get("/api/v1/analytics/heatmap?hours=720", headers=headers)).status_code == 200
    assert (await app_client.get("/api/v1/analytics/heatmap?hours=1",   headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_heatmap_tenant_isolation(app_client, admin_session):
    """Tenant A must not see tenant B's cameras in the heatmap."""
    tenant_a, user_a = await _seed_user_with_role(admin_session, role_id=2)
    tenant_b, user_b = await _seed_user_with_role(admin_session, role_id=2)
    camera_b = await _seed_camera(admin_session, tenant_b)

    token_a = create_access_token(str(user_a), str(tenant_a), role_id=2)
    resp = await app_client.get(
        "/api/v1/analytics/heatmap",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 200
    camera_ids = {str(row["camera_id"]) for row in resp.json()}
    assert str(camera_b) not in camera_ids, "Tenant A must not see tenant B's cameras in heatmap"


# ─── False positive flagging tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_false_positive_succeeds(app_client, admin_session):
    """Operator (alert:acknowledge) can flag an open alert as false_positive."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    camera_id = await _seed_camera(admin_session, tenant_id)
    alert_id = await _seed_alert(admin_session, tenant_id, camera_id, status="open")
    token = create_access_token(str(user_id), str(tenant_id), role_id=4)
    resp = await app_client.post(
        f"/api/v1/alerts/{alert_id}/false-positive",
        json={"fp_reason": "background flicker, not an actual event"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "false_positive"
    assert str(body["id"]) == str(alert_id)


@pytest.mark.asyncio
async def test_mark_acknowledged_alert_as_false_positive_succeeds(app_client, admin_session):
    """An acknowledged alert can also be downgraded to false_positive."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    camera_id = await _seed_camera(admin_session, tenant_id)
    alert_id = await _seed_alert(admin_session, tenant_id, camera_id, status="acknowledged")
    token = create_access_token(str(user_id), str(tenant_id), role_id=4)
    resp = await app_client.post(
        f"/api/v1/alerts/{alert_id}/false-positive",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "false_positive"


@pytest.mark.asyncio
async def test_mark_already_false_positive_alert_returns_404(app_client, admin_session):
    """Flagging a false_positive alert again must return 404 (status guard in the UPDATE)."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    camera_id = await _seed_camera(admin_session, tenant_id)
    alert_id = await _seed_alert(admin_session, tenant_id, camera_id, status="false_positive")
    token = create_access_token(str(user_id), str(tenant_id), role_id=4)
    resp = await app_client.post(
        f"/api/v1/alerts/{alert_id}/false-positive",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_viewer_cannot_mark_false_positive(app_client, admin_session):
    """Viewer (role 6) has no alert:acknowledge — must get 403."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)
    camera_id = await _seed_camera(admin_session, tenant_id)
    alert_id = await _seed_alert(admin_session, tenant_id, camera_id)
    token = create_access_token(str(user_id), str(tenant_id), role_id=6)
    resp = await app_client.post(
        f"/api/v1/alerts/{alert_id}/false-positive",
        json={"fp_reason": "unauthorized attempt"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_client_cannot_mark_false_positive(app_client, admin_session):
    """Client (role 7) has no alert:acknowledge — must get 403."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=7)
    camera_id = await _seed_camera(admin_session, tenant_id)
    alert_id = await _seed_alert(admin_session, tenant_id, camera_id)
    token = create_access_token(str(user_id), str(tenant_id), role_id=7)
    resp = await app_client.post(
        f"/api/v1/alerts/{alert_id}/false-positive",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_false_positive_tenant_isolation(app_client, admin_session):
    """Tenant A operator cannot flag tenant B's alert (RLS hides it → 404, not 200)."""
    tenant_a, user_a = await _seed_user_with_role(admin_session, role_id=4)
    tenant_b, user_b = await _seed_user_with_role(admin_session, role_id=4)
    camera_b = await _seed_camera(admin_session, tenant_b)
    alert_b = await _seed_alert(admin_session, tenant_b, camera_b, status="open")

    token_a = create_access_token(str(user_a), str(tenant_a), role_id=4)
    resp = await app_client.post(
        f"/api/v1/alerts/{alert_b}/false-positive",
        json={"fp_reason": "cross-tenant test"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    # RLS makes tenant B's alert invisible in tenant A's session → UPDATE matches nothing → 404
    assert resp.status_code == 404, (
        "Cross-tenant false-positive must not succeed; RLS should make the alert invisible"
    )


# ─── Analytics summary tests ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_summary_required_fields(app_client, admin_session):
    """Summary response must carry all KPI fields the dashboard depends on."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    resp = await app_client.get(
        "/api/v1/analytics/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    for key in ("open_alerts", "open_incidents", "active_cameras", "detections_today", "alerts_today"):
        assert key in body, f"Analytics summary missing required field: {key}"


@pytest.mark.asyncio
async def test_analytics_summary_counts_seeded_data(app_client, admin_session):
    """After seeding 1 active camera and 1 open alert the summary counts must reflect them."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    camera_id = await _seed_camera(admin_session, tenant_id)
    await _seed_alert(admin_session, tenant_id, camera_id, status="open")
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)
    resp = await app_client.get(
        "/api/v1/analytics/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["open_alerts"] >= 1, "Expected at least 1 open alert in summary"
    assert body["active_cameras"] >= 1, "Expected at least 1 active camera in summary"


@pytest.mark.asyncio
async def test_analytics_summary_tenant_isolation(app_client, admin_session):
    """Tenant A's summary must not count tenant B's alerts."""
    tenant_a, user_a = await _seed_user_with_role(admin_session, role_id=2)
    tenant_b, user_b = await _seed_user_with_role(admin_session, role_id=2)
    camera_b = await _seed_camera(admin_session, tenant_b)
    for _ in range(3):
        await _seed_alert(admin_session, tenant_b, camera_b, status="open")

    token_a = create_access_token(str(user_a), str(tenant_a), role_id=2)
    resp = await app_client.get(
        "/api/v1/analytics/summary",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 200
    assert resp.json()["open_alerts"] == 0, (
        "Tenant A should see 0 open alerts; tenant B's 3 alerts must be isolated"
    )


@pytest.mark.asyncio
async def test_analytics_summary_unauthenticated_rejected(app_client):
    resp = await app_client.get("/api/v1/analytics/summary")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_client_role_can_access_analytics_summary(app_client, admin_session):
    """Client (role 7) has alert:read — analytics/summary must return 200."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=7)
    token = create_access_token(str(user_id), str(tenant_id), role_id=7)
    resp = await app_client.get(
        "/api/v1/analytics/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
