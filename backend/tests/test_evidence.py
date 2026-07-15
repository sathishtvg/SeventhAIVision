import hashlib
import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


@pytest.mark.asyncio
async def test_snapshot_checksum_matches_file(app_client, admin_session):
    """Evidence rows are written by the AI workers (verified for real against
    a real file in ai-worker/tests/test_storage.py); this test confirms the
    backend's read path surfaces the same checksum/path fields unmodified."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    camera_id = uuid.uuid4()
    detection_id = uuid.uuid4()
    fake_bytes = b"not a real jpeg, just bytes for a checksum test"
    checksum = hashlib.sha256(fake_bytes).hexdigest()
    rel_path = f"{tenant_id}/2026/06/16/{detection_id}.jpg"

    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, 'Cam')"),
        {"id": camera_id, "tid": tenant_id},
    )
    await admin_session.execute(
        text(
            "INSERT INTO evidence (tenant_id, detection_id, media_type, storage_path, checksum_sha256) "
            "VALUES (:tid, :did, 'image', :path, :checksum)"
        ),
        {"tid": tenant_id, "did": detection_id, "path": rel_path, "checksum": checksum},
    )
    await admin_session.commit()

    resp = await app_client.get("/api/v1/evidence", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["checksum_sha256"] == checksum
    assert rows[0]["storage_path"] == rel_path


@pytest.mark.asyncio
async def test_storage_path_convention(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    token = create_access_token(str(user_id), str(tenant_id), role_id=2)

    camera_id = uuid.uuid4()
    detection_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, 'Cam')"),
        {"id": camera_id, "tid": tenant_id},
    )
    await admin_session.execute(
        text(
            "INSERT INTO evidence (tenant_id, detection_id, media_type, storage_path, checksum_sha256) "
            "VALUES (:tid, :did, 'image', :path, 'x')"
        ),
        {"tid": tenant_id, "did": detection_id, "path": f"{tenant_id}/2026/06/16/{detection_id}.jpg"},
    )
    await admin_session.commit()

    resp = await app_client.get("/api/v1/evidence", headers={"Authorization": f"Bearer {token}"})
    path = resp.json()[0]["storage_path"]
    parts = path.split("/")
    assert parts[0] == str(tenant_id)
    assert parts[1:4] == ["2026", "06", "16"]
    assert parts[4] == f"{detection_id}.jpg"
