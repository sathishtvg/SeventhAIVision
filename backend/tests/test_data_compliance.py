"""Gap 51 — Data Compliance Router

First test file for backend/app/routers/data_compliance.py (314 lines, 0 prior tests).
All four endpoints are covered for the first time.

Endpoints (prefix /api/v1/data-compliance):
  GET  /summary                  (settings:read)
  GET  /audit-stats              (settings:read)
  GET  /retention-config         (settings:read)
  POST /dsr-export/{user_id}     (audit:read)

Sections:
  A — Summary: 200 + generated_at; security sub-keys; data_protection sub-keys;
      watchlist + cameras sub-keys
  B — Audit stats: all top-level keys; top_actions_last_30d is a list
  C — Retention config: expected structure keys; defaults to environment_default
      when no tenant_settings row exists
  D — Retention config: shows 'tenant_settings' source when custom row present
  E — DSR export: unknown user → 404; cross-tenant user → 404 (RLS isolation)
  F — DSR export: valid user — all top-level keys; subject dict fields;
      audit_log + sessions + evidence are lists
  G — DSR export include_evidence_urls: False excludes storage_path;
      True includes storage_path
  H — Permissions: viewer cannot access summary → 403; unauth → 401 on summary;
      unauth → 401 on dsr-export
"""
from __future__ import annotations

import json
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

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql+asyncpg://svc_app:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 2):
    """Create isolated tenant + user; return (tenant_id, user_id, jwt_token)."""
    from app.core.security import create_access_token, hash_password

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"dco-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"DCo Test {slug}", "slug": slug},
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
                "email": f"dco-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_setting(tenant_id: uuid.UUID, user_id: uuid.UUID, key: str, value) -> None:
    """Insert (or upsert) a tenant_settings row via the superuser connection."""
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
                "VALUES (:tid, :key, CAST(:val AS jsonb), :uid) "
                "ON CONFLICT (tenant_id, setting_key) DO UPDATE "
                "SET setting_value = CAST(:val AS jsonb), updated_by_user_id = :uid"
            ),
            {"tid": tenant_id, "key": key, "val": json.dumps(value), "uid": user_id},
        )
        await s.commit()
    await engine.dispose()


async def _seed_evidence(tenant_id: uuid.UUID) -> None:
    """Insert a minimal evidence row so DSR include_evidence_urls tests have data."""
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO evidence (tenant_id, media_type, storage_path, "
                "checksum_sha256, captured_at) "
                "VALUES (:tid, 'image', '/data/dco-test-evidence.jpg', "
                "'dco123abc456def789', now())"
            ),
            {"tid": tenant_id},
        )
        await s.commit()
    await engine.dispose()


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. Summary endpoint ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summary_returns_200_with_generated_at():
    """GET /data-compliance/summary returns 200 with a generated_at timestamp."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/data-compliance/summary")
    assert r.status_code == 200
    data = r.json()
    assert "generated_at" in data
    assert data["generated_at"]  # non-empty ISO string


@pytest.mark.asyncio
async def test_summary_security_section_keys():
    """GET /data-compliance/summary security section contains all expected keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/data-compliance/summary")
    assert r.status_code == 200
    sec = r.json()["security"]
    for key in (
        "rls_enforced", "rbac_enabled", "two_factor_available",
        "two_factor_enrolled_users", "account_lockout_enabled",
        "session_management_enabled", "ip_allowlist_supported",
        "rate_limiting_enabled", "jwt_key_rotation_supported",
        "audit_logging_enabled",
    ):
        assert key in sec, f"security key {key!r} missing from summary"


@pytest.mark.asyncio
async def test_summary_data_protection_section_keys():
    """GET /data-compliance/summary data_protection section contains all expected keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/data-compliance/summary")
    assert r.status_code == 200
    dp = r.json()["data_protection"]
    for key in (
        "evidence_retention_days", "audit_retention_years",
        "pdpa_masking_available", "dsr_export_available",
        "cross_tenant_isolation",
    ):
        assert key in dp, f"data_protection key {key!r} missing from summary"


@pytest.mark.asyncio
async def test_summary_watchlist_and_cameras_sections():
    """GET /data-compliance/summary watchlist and cameras sections have expected keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/data-compliance/summary")
    assert r.status_code == 200
    data = r.json()
    assert "active_plates" in data["watchlist"]
    assert "active_faces" in data["watchlist"]
    assert "total" in data["cameras"]
    assert "active" in data["cameras"]
    assert "total" in data["users"]
    assert "active" in data["users"]


# ─── B. Audit stats endpoint ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_stats_all_top_level_keys():
    """GET /data-compliance/audit-stats returns all expected top-level keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/data-compliance/audit-stats")
    assert r.status_code == 200
    data = r.json()
    for key in (
        "generated_at", "total_entries", "earliest_entry", "latest_entry",
        "unique_users", "unique_actions", "unique_resource_types",
        "entries_last_30_days", "system_actor_entries", "top_actions_last_30d",
    ):
        assert key in data, f"Key {key!r} missing from audit-stats"


@pytest.mark.asyncio
async def test_audit_stats_top_actions_is_list_with_expected_shape():
    """GET /data-compliance/audit-stats top_actions_last_30d is a list of {action, count}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/data-compliance/audit-stats")
    assert r.status_code == 200
    top = r.json()["top_actions_last_30d"]
    assert isinstance(top, list)
    for item in top:
        assert "action" in item
        assert "count" in item


# ─── C. Retention config defaults ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retention_config_expected_structure():
    """GET /data-compliance/retention-config has evidence_retention and audit_retention dicts."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/data-compliance/retention-config")
    assert r.status_code == 200
    data = r.json()
    assert "generated_at" in data
    ev = data["evidence_retention"]
    au = data["audit_retention"]
    for key in ("days", "source", "last_updated", "note"):
        assert key in ev, f"evidence_retention key {key!r} missing"
    for key in ("years", "source", "last_updated", "note"):
        assert key in au, f"audit_retention key {key!r} missing"


@pytest.mark.asyncio
async def test_retention_config_defaults_to_environment_when_no_settings():
    """GET /data-compliance/retention-config shows source=environment_default for new tenants."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/data-compliance/retention-config")
    assert r.status_code == 200
    data = r.json()
    assert data["evidence_retention"]["source"] == "environment_default"
    assert data["evidence_retention"]["last_updated"] is None
    assert data["audit_retention"]["source"] == "environment_default"
    assert data["audit_retention"]["last_updated"] is None


# ─── D. Retention config with custom settings ─────────────────────────────────

@pytest.mark.asyncio
async def test_retention_config_shows_tenant_settings_source():
    """GET /data-compliance/retention-config shows source=tenant_settings when a row exists."""
    tenant_id, user_id, token = await _seed_tenant_and_token()
    # Insert a custom retention setting for this tenant
    await _seed_setting(tenant_id, user_id, "evidence.retention_days", 180)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/data-compliance/retention-config")
    assert r.status_code == 200
    ev = r.json()["evidence_retention"]
    assert ev["source"] == "tenant_settings"
    assert ev["days"] == 180
    assert ev["last_updated"] is not None  # ISO datetime string


# ─── E. DSR export edge cases ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dsr_export_unknown_user_returns_404():
    """POST /dsr-export/{id} for a completely unknown user ID returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/data-compliance/dsr-export/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_dsr_export_cross_tenant_user_returns_404():
    """POST /dsr-export/{id} for a user in another tenant returns 404 (RLS isolation)."""
    _, _, token_a = await _seed_tenant_and_token()
    _, user_b_id, _ = await _seed_tenant_and_token()
    # Token A requests export for user B — RLS blocks users table to tenant A
    async with await _authed(token_a) as c:
        r = await c.post(f"/api/v1/data-compliance/dsr-export/{user_b_id}")
    assert r.status_code == 404


# ─── F. DSR export valid user ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dsr_export_valid_user_all_top_level_keys():
    """POST /dsr-export/{id} for a valid user returns all expected top-level keys."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/data-compliance/dsr-export/{user_id}")
    assert r.status_code == 200
    data = r.json()
    for key in (
        "export_generated_at", "export_generated_by_user_id", "regulation",
        "subject", "audit_log_last_12_months", "active_sessions",
        "evidence_last_12_months", "notes",
    ):
        assert key in data, f"DSR export key {key!r} missing"


@pytest.mark.asyncio
async def test_dsr_export_subject_has_expected_fields():
    """POST /dsr-export/{id} subject dict contains id, email, role_id, is_active."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/data-compliance/dsr-export/{user_id}")
    assert r.status_code == 200
    subject = r.json()["subject"]
    for field in ("id", "email", "role_id", "is_active"):
        assert field in subject, f"subject field {field!r} missing from DSR export"
    assert subject["id"] == str(user_id)


@pytest.mark.asyncio
async def test_dsr_export_audit_log_and_sessions_are_lists():
    """POST /dsr-export/{id} audit_log_last_12_months and active_sessions are lists."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/data-compliance/dsr-export/{user_id}")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["audit_log_last_12_months"], list)
    assert isinstance(data["active_sessions"], list)
    assert isinstance(data["evidence_last_12_months"], list)


# ─── G. DSR include_evidence_urls flag ───────────────────────────────────────

@pytest.mark.asyncio
async def test_dsr_export_default_excludes_storage_path():
    """POST /dsr-export without include_evidence_urls omits storage_path from evidence items."""
    tenant_id, user_id, token = await _seed_tenant_and_token()
    await _seed_evidence(tenant_id)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/data-compliance/dsr-export/{user_id}")
    assert r.status_code == 200
    evidence_items = r.json()["evidence_last_12_months"]
    assert len(evidence_items) >= 1, "Expected at least one evidence item from seeded data"
    for item in evidence_items:
        assert "storage_path" not in item, "storage_path must not be exposed by default"


@pytest.mark.asyncio
async def test_dsr_export_with_urls_includes_storage_path():
    """POST /dsr-export with include_evidence_urls=true adds storage_path to evidence items."""
    tenant_id, user_id, token = await _seed_tenant_and_token()
    await _seed_evidence(tenant_id)
    async with await _authed(token) as c:
        r = await c.post(
            f"/api/v1/data-compliance/dsr-export/{user_id}",
            json={"include_evidence_urls": True},
        )
    assert r.status_code == 200
    evidence_items = r.json()["evidence_last_12_months"]
    assert len(evidence_items) >= 1, "Expected at least one evidence item from seeded data"
    for item in evidence_items:
        assert "storage_path" in item, "storage_path must be present when include_evidence_urls=True"


# ─── H. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_access_summary():
    """GET /data-compliance/summary requires settings:read — viewer (role 6) gets 403."""
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.get("/api/v1/data-compliance/summary")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_access_summary():
    """GET /data-compliance/summary without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/data-compliance/summary")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_cannot_post_dsr_export():
    """POST /data-compliance/dsr-export/{id} without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.post(f"/api/v1/data-compliance/dsr-export/{uuid.uuid4()}")
    assert r.status_code == 401
