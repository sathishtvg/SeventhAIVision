import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


@pytest.mark.asyncio
async def test_audit_row_per_detection_processed(app_client, admin_session):
    """The AI worker pipelines write one audit_logs row per processed
    detection (verified for real end-to-end in
    ai-worker/tests/test_lpr_task.py / test_face_task.py / test_intrusion_task.py,
    which assert the join from lpr_events/face_events/intrusion_events to
    audit_logs succeeds). This test confirms the backend's read path surfaces
    that row correctly via the audit router."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    detection_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO audit_logs (tenant_id, user_id, action, resource_type, resource_id, detail) "
            "VALUES (:tid, NULL, 'lpr_detection_processed', 'detection', :did, :detail)"
        ),
        {"tid": tenant_id, "did": detection_id, "detail": '{"plate": "TEST123"}'},
    )
    await admin_session.commit()

    resp = await app_client.get("/api/v1/audit", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert len(rows) == 1
    assert rows[0]["action"] == "lpr_detection_processed"
    assert rows[0]["resource_type"] == "detection"
    assert rows[0]["resource_id"] == str(detection_id)


@pytest.mark.asyncio
async def test_system_actor_has_null_user_id(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    await admin_session.execute(
        text(
            "INSERT INTO audit_logs (tenant_id, user_id, action, resource_type, resource_id) "
            "VALUES (:tid, NULL, 'intrusion_detected', 'detection', :did)"
        ),
        {"tid": tenant_id, "did": uuid.uuid4()},
    )
    await admin_session.commit()

    resp = await app_client.get("/api/v1/audit", headers={"Authorization": f"Bearer {token}"})
    rows = resp.json()["items"]
    assert len(rows) == 1
    assert rows[0]["user_id"] is None
