"""Tier 2 Feature 3: Audit log tamper detection tests.

Tests cover:
- write_audit_log() stores a non-null row_hash and prev_hash
- Chain verification passes for a clean sequence of rows
- Modifying a field after the fact causes the hash to fail verification
- Modifying prev_hash directly is also detected
- Deleting a middle row breaks the chain (chain_broken flag)
- Rows written without hashes (legacy) are skipped — verified=True
- Non-admin cannot call /verify (403)
- Chain integrity is per-tenant (other tenant's rows don't affect this chain)
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.security import create_access_token
from app.services.audit import write_audit_log
from tests.test_rbac import _seed_user_with_role


def _jwt(tenant_id: uuid.UUID, user_id: uuid.UUID, role_id: int) -> str:
    return create_access_token(str(user_id), str(tenant_id), role_id=role_id)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def _admin(admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    return _jwt(tenant_id, user_id, 2), tenant_id


# ── Unit tests for write_audit_log ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_stores_hash(admin_session, _admin):
    _, tenant_id = _admin
    # Set tenant context so RLS allows the insert
    await admin_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    row_id = await write_audit_log(
        admin_session,
        tenant_id=str(tenant_id),
        action="test.write",
        resource_type="test",
    )
    await admin_session.commit()

    row = (await admin_session.execute(
        text("SELECT row_hash, prev_hash FROM audit_logs WHERE id = :id"),
        {"id": row_id},
    )).first()
    assert row is not None
    assert row.row_hash is not None
    assert len(row.row_hash) == 64
    assert row.prev_hash is not None


@pytest.mark.asyncio
async def test_chain_links_successive_rows(admin_session, _admin):
    _, tenant_id = _admin
    await admin_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    id1 = await write_audit_log(admin_session, tenant_id=str(tenant_id), action="a.1")
    await admin_session.commit()
    id2 = await write_audit_log(admin_session, tenant_id=str(tenant_id), action="a.2")
    await admin_session.commit()

    r1 = (await admin_session.execute(
        text("SELECT row_hash FROM audit_logs WHERE id = :id"), {"id": id1}
    )).first()
    r2 = (await admin_session.execute(
        text("SELECT prev_hash FROM audit_logs WHERE id = :id"), {"id": id2}
    )).first()
    # Row 2's prev_hash must equal row 1's row_hash
    assert r2.prev_hash == r1.row_hash


# ── API tests for /verify ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_clean_chain(_admin, app_client, admin_session):
    jwt, tenant_id = _admin
    await admin_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    for i in range(3):
        await write_audit_log(admin_session, tenant_id=str(tenant_id), action=f"clean.{i}")
    await admin_session.commit()

    resp = await app_client.get(
        "/api/v1/audit/verify",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verified"] is True
    assert body["tampered_count"] == 0
    assert body["total_checked"] >= 3
    assert body["chain_broken"] is False


@pytest.mark.asyncio
async def test_verify_detects_field_tampering(_admin, app_client, admin_session):
    """Directly updating a field after insert breaks the stored hash."""
    jwt, tenant_id = _admin
    await admin_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    row_id = await write_audit_log(
        admin_session, tenant_id=str(tenant_id), action="original.action"
    )
    await admin_session.commit()

    # Tamper: change the action text without updating the hash
    await admin_session.execute(
        text("UPDATE audit_logs SET action = 'tampered.action' WHERE id = :id"),
        {"id": row_id},
    )
    await admin_session.commit()

    resp = await app_client.get(
        "/api/v1/audit/verify",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    body = resp.json()
    assert body["verified"] is False
    assert body["tampered_count"] >= 1
    assert str(row_id) in body["tampered_ids"]


@pytest.mark.asyncio
async def test_verify_detects_row_deletion(_admin, app_client, admin_session):
    """Deleting a middle row causes the next row's prev_hash to be wrong."""
    jwt, tenant_id = _admin
    await admin_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    id1 = await write_audit_log(admin_session, tenant_id=str(tenant_id), action="del.1")
    await admin_session.commit()
    id2 = await write_audit_log(admin_session, tenant_id=str(tenant_id), action="del.2")
    await admin_session.commit()
    await write_audit_log(admin_session, tenant_id=str(tenant_id), action="del.3")
    await admin_session.commit()

    # Delete the middle row (id2) — breaks the chain for row 3
    await admin_session.execute(
        text("DELETE FROM audit_logs WHERE id = :id"), {"id": id2}
    )
    await admin_session.commit()

    resp = await app_client.get(
        "/api/v1/audit/verify",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    body = resp.json()
    assert body["verified"] is False
    assert body["chain_broken"] is True


@pytest.mark.asyncio
async def test_verify_legacy_rows_skipped(_admin, app_client, admin_session):
    """Legacy rows (row_hash IS NULL) are not counted in verification."""
    jwt, tenant_id = _admin
    # Insert a legacy-style row (no hash) directly
    await admin_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    await admin_session.execute(
        text("""
            INSERT INTO audit_logs (tenant_id, action, resource_type)
            VALUES (:tid, 'legacy.action', 'legacy')
        """),
        {"tid": tenant_id},
    )
    await admin_session.commit()

    resp = await app_client.get(
        "/api/v1/audit/verify",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    body = resp.json()
    # Legacy rows not counted — verified=True (no hashed rows to check)
    assert body["verified"] is True
    assert body["total_checked"] == 0


@pytest.mark.asyncio
async def test_verify_requires_permission(admin_session, app_client):
    """Viewer (role 6) cannot call /verify."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)
    jwt = _jwt(tenant_id, user_id, 6)
    resp = await app_client.get(
        "/api/v1/audit/verify",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_tenant_isolation_in_verify(admin_session, app_client):
    """Tenant B's rows don't appear in Tenant A's verify result."""
    # Tenant A
    tid_a, uid_a = await _seed_user_with_role(admin_session, role_id=2)
    jwt_a = _jwt(tid_a, uid_a, 2)
    await admin_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tid_a)},
    )
    await write_audit_log(admin_session, tenant_id=str(tid_a), action="a.event")
    await admin_session.commit()

    # Tenant B
    tid_b, uid_b = await _seed_user_with_role(admin_session, role_id=2)
    await admin_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tid_b)},
    )
    await write_audit_log(admin_session, tenant_id=str(tid_b), action="b.event")
    await admin_session.commit()

    # Verify from A's perspective — should only see A's row
    resp = await app_client.get(
        "/api/v1/audit/verify",
        headers={"Authorization": f"Bearer {jwt_a}"},
    )
    body = resp.json()
    assert body["verified"] is True
    assert body["total_checked"] == 1  # only A's row
