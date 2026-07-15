"""Gap 53 — Licenses Router

First dedicated isolated-tenant test file for backend/app/routers/licenses.py
(157 lines, previously only ~3 incidental tests in shared-fixture files).

All 3 endpoints require license:manage permission (super_admin, role_id=1 only).
The router uses get_raw_db (no automatic RLS) and manually sets the GUC to the
target tenant before every query.

Sections:
  A — ALL_MODULES constant: 11 modules
  B — List licenses: 11 entries, all fields, new-tenant defaults, 404, 403
  C — Upsert (PUT): enable module, licensed_at updated, max_cameras persisted,
      unknown module 400, unknown tenant 404, requires super_admin 403
  D — Revoke (DELETE): sets is_enabled=False, unknown module 400,
      nonexistent row is no-op (200), requires super_admin 403
  E — Audit logs: upsert writes license.upsert, revoke writes license.revoke
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 1):
    """Create isolated tenant + user; return (tenant_id, user_id, jwt_token)."""
    from app.core.security import create_access_token, hash_password

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"lic-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Lic Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                "VALUES (:id, :tid, :role, :email, :pw)"
            ),
            {
                "id": user_id,
                "tid": tenant_id,
                "role": role_id,
                "email": f"lic-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_target_tenant() -> uuid.UUID:
    """Create a bare tenant (no license rows); return its UUID."""
    target_id = uuid.uuid4()
    slug = f"lic-tgt-{target_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": target_id, "name": f"Lic Target {slug}", "slug": slug},
        )
        await s.commit()
    await engine.dispose()
    return target_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _read_audit_logs(tenant_id: uuid.UUID, action: str) -> list[dict]:
    """Read audit_logs rows for a tenant+action via admin engine (bypasses RLS)."""
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    rows = []
    async with factory() as s:
        result = await s.execute(
            text(
                "SELECT id, action, resource_type, detail FROM audit_logs "
                "WHERE tenant_id = :tid AND action = :action ORDER BY created_at DESC LIMIT 5"
            ),
            {"tid": tenant_id, "action": action},
        )
        rows = [dict(row._mapping) for row in result]
    await engine.dispose()
    return rows


# ─── A. ALL_MODULES constant ─────────────────────────────────────────────────

def test_all_modules_contains_11_entries():
    """ALL_MODULES must list all 11 AI detection module types."""
    from app.routers.licenses import ALL_MODULES
    assert len(ALL_MODULES) == 11
    expected = {
        "lpr", "face", "intrusion", "ppe", "crowd",
        "fire_smoke", "weapon", "behavior",
        "tampering", "abandoned", "fall",
    }
    assert set(ALL_MODULES) == expected


# ─── B. List licenses ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_licenses_returns_11_entries():
    """GET /licenses/{id} returns 11 entries — one per module."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/licenses/{target}")
    assert r.status_code == 200
    assert len(r.json()) == 11


@pytest.mark.asyncio
async def test_list_licenses_all_required_fields_present():
    """Each license entry must have all 7 expected fields."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/licenses/{target}")
    assert r.status_code == 200
    required = {"module_type", "is_enabled", "max_cameras", "licensed_at", "expires_at", "notes", "updated_at"}
    for entry in r.json():
        assert required.issubset(entry.keys()), f"Missing fields in {entry}"


@pytest.mark.asyncio
async def test_list_licenses_new_tenant_all_disabled():
    """A tenant with no license rows returns all 11 modules with is_enabled=False."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/licenses/{target}")
    assert r.status_code == 200
    assert all(not entry["is_enabled"] for entry in r.json()), "All modules must default to disabled"


@pytest.mark.asyncio
async def test_list_licenses_unknown_tenant_returns_404():
    """GET /licenses/{unknown} returns 404 when tenant does not exist."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/licenses/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_licenses_admin_role_returns_403():
    """license:manage is super_admin only; admin (role 2) should receive 403."""
    _, _, admin_token = await _seed_tenant_and_token(role_id=2)
    target = await _seed_target_tenant()
    async with await _authed(admin_token) as c:
        r = await c.get(f"/api/v1/licenses/{target}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_licenses_unauthenticated_returns_401():
    """GET /licenses/{id} without a JWT returns 401."""
    target = await _seed_target_tenant()
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get(f"/api/v1/licenses/{target}")
    assert r.status_code == 401


# ─── C. Upsert (PUT) ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_license_enables_module():
    """PUT /{tenant}/{module} with is_enabled=true returns 200 with is_enabled=True."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        r = await c.put(
            f"/api/v1/licenses/{target}/lpr",
            json={"is_enabled": True},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["is_enabled"] is True
    assert body["module_type"] == "lpr"


@pytest.mark.asyncio
async def test_upsert_license_licensed_at_set_when_enabling():
    """Enabling a previously-disabled module sets licensed_at in the DB."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        await c.put(f"/api/v1/licenses/{target}/face", json={"is_enabled": True})
        r_list = await c.get(f"/api/v1/licenses/{target}")
    entries = {e["module_type"]: e for e in r_list.json()}
    assert entries["face"]["licensed_at"] is not None, "licensed_at must be set after enabling"


@pytest.mark.asyncio
async def test_upsert_license_max_cameras_persisted():
    """PUT with max_cameras=5 persists and is visible in GET /licenses/{id}."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        await c.put(
            f"/api/v1/licenses/{target}/intrusion",
            json={"is_enabled": True, "max_cameras": 5},
        )
        r_list = await c.get(f"/api/v1/licenses/{target}")
    entries = {e["module_type"]: e for e in r_list.json()}
    assert entries["intrusion"]["max_cameras"] == 5


@pytest.mark.asyncio
async def test_upsert_license_notes_persisted():
    """PUT with notes='test note' persists and is visible in GET."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        await c.put(
            f"/api/v1/licenses/{target}/ppe",
            json={"is_enabled": True, "notes": "test note"},
        )
        r_list = await c.get(f"/api/v1/licenses/{target}")
    entries = {e["module_type"]: e for e in r_list.json()}
    assert entries["ppe"]["notes"] == "test note"


@pytest.mark.asyncio
async def test_upsert_license_unknown_module_returns_400():
    """PUT /{tenant}/unknown_module returns 400."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        r = await c.put(
            f"/api/v1/licenses/{target}/unknown_module",
            json={"is_enabled": True},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_upsert_license_unknown_tenant_returns_404():
    """PUT with an unknown tenant_id returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    async with await _authed(token) as c:
        r = await c.put(
            f"/api/v1/licenses/{uuid.uuid4()}/lpr",
            json={"is_enabled": True},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_upsert_license_requires_super_admin():
    """PUT /licenses/{id}/{module} with admin role (2) returns 403."""
    _, _, admin_token = await _seed_tenant_and_token(role_id=2)
    target = await _seed_target_tenant()
    async with await _authed(admin_token) as c:
        r = await c.put(f"/api/v1/licenses/{target}/lpr", json={"is_enabled": True})
    assert r.status_code == 403


# ─── D. Revoke (DELETE) ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_revoke_license_sets_is_enabled_false():
    """DELETE /{tenant}/{module} returns 200 with is_enabled=False."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        await c.put(f"/api/v1/licenses/{target}/crowd", json={"is_enabled": True})
        r = await c.delete(f"/api/v1/licenses/{target}/crowd")
    assert r.status_code == 200
    assert r.json()["is_enabled"] is False


@pytest.mark.asyncio
async def test_revoke_license_disabled_visible_in_list():
    """After DELETE, GET /licenses/{id} shows the module as is_enabled=False."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        await c.put(f"/api/v1/licenses/{target}/weapon", json={"is_enabled": True})
        await c.delete(f"/api/v1/licenses/{target}/weapon")
        r_list = await c.get(f"/api/v1/licenses/{target}")
    entries = {e["module_type"]: e for e in r_list.json()}
    assert entries["weapon"]["is_enabled"] is False


@pytest.mark.asyncio
async def test_revoke_license_unknown_module_returns_400():
    """DELETE /{tenant}/unknown_module returns 400."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/licenses/{target}/unknown_module")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_revoke_license_nonexistent_row_is_noop():
    """DELETE on a module with no DB row is a no-op UPDATE — still returns 200."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/licenses/{target}/behavior")
    assert r.status_code == 200
    assert r.json()["is_enabled"] is False


@pytest.mark.asyncio
async def test_revoke_license_requires_super_admin():
    """DELETE /licenses/{id}/{module} with admin role (2) returns 403."""
    _, _, admin_token = await _seed_tenant_and_token(role_id=2)
    target = await _seed_target_tenant()
    async with await _authed(admin_token) as c:
        r = await c.delete(f"/api/v1/licenses/{target}/lpr")
    assert r.status_code == 403


# ─── E. Audit logs ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_writes_license_upsert_audit_log():
    """PUT /licenses/{id}/{module} writes a 'license.upsert' audit_log row."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        await c.put(f"/api/v1/licenses/{target}/fall", json={"is_enabled": True})
    rows = await _read_audit_logs(target, "license.upsert")
    assert len(rows) >= 1, "Expected at least one license.upsert audit_log row"
    assert rows[0]["resource_type"] == "tenant_module_license"


@pytest.mark.asyncio
async def test_revoke_writes_license_revoke_audit_log():
    """DELETE /licenses/{id}/{module} writes a 'license.revoke' audit_log row."""
    _, _, token = await _seed_tenant_and_token(role_id=1)
    target = await _seed_target_tenant()
    async with await _authed(token) as c:
        await c.put(f"/api/v1/licenses/{target}/abandoned", json={"is_enabled": True})
        await c.delete(f"/api/v1/licenses/{target}/abandoned")
    rows = await _read_audit_logs(target, "license.revoke")
    assert len(rows) >= 1, "Expected at least one license.revoke audit_log row"
    assert rows[0]["resource_type"] == "tenant_module_license"
