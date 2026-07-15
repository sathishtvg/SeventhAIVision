import uuid

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.core.security import create_access_token


@pytest.mark.asyncio
async def test_evidence_file_download(app_client, admin_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_ROOT", str(tmp_path))

    # Seed tenant + user
    tenant_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'T', :slug)"),
        {"id": tenant_id, "slug": f"t-{tenant_id.hex[:8]}"},
    )
    user_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
            "VALUES (:id, :tid, 4, :email, 'x')"
        ),
        {"id": user_id, "tid": tenant_id, "email": f"u@{tenant_id.hex[:8]}.test"},
    )
    # Create evidence file + DB row
    rel_path = f"{tenant_id}/2026/01/15/{uuid.uuid4()}.jpg"
    file_path = tmp_path / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"\xff\xd8\xff\xe0test-jpeg-bytes")

    evidence_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO evidence (id, tenant_id, media_type, storage_path, checksum_sha256, captured_at) "
            "VALUES (:id, :tid, 'image', :path, 'abc', now())"
        ),
        {"id": evidence_id, "tid": tenant_id, "path": rel_path},
    )
    await admin_session.commit()

    token = create_access_token(str(user_id), str(tenant_id), role_id=4)
    resp = await app_client.get(
        f"/api/v1/evidence/{evidence_id}/file",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.content == b"\xff\xd8\xff\xe0test-jpeg-bytes"
    assert "image/jpeg" in resp.headers.get("content-type", "")

    # Cleanup
    await admin_session.execute(text("DELETE FROM evidence WHERE tenant_id = :tid"), {"tid": tenant_id})
    await admin_session.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tenant_id})
    await admin_session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
    await admin_session.commit()


@pytest.mark.asyncio
async def test_evidence_file_not_found_returns_404(app_client, admin_session):
    tenant_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'T', :slug)"),
        {"id": tenant_id, "slug": f"t-{tenant_id.hex[:8]}"},
    )
    user_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
            "VALUES (:id, :tid, 4, :email, 'x')"
        ),
        {"id": user_id, "tid": tenant_id, "email": f"u@{tenant_id.hex[:8]}.test"},
    )
    await admin_session.commit()

    token = create_access_token(str(user_id), str(tenant_id), role_id=4)
    resp = await app_client.get(
        f"/api/v1/evidence/{uuid.uuid4()}/file",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404

    # Cleanup
    await admin_session.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tenant_id})
    await admin_session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
    await admin_session.commit()
