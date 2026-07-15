"""Gap 80 — Settings Router (isolated-tenant)

Isolated-tenant tests for backend/app/routers/settings.py (61 lines).
Admin-editable per-tenant runtime configuration (plan §8).

Endpoints:
  GET /api/v1/settings                — settings:read; list all tenant settings rows
  PUT /api/v1/settings/{setting_key}  — settings:write; upsert setting value

Permission:
  Both endpoints: settings:read / settings:write → roles 1 (super_admin) and 2 (admin) only.
  Supervisor (role 3), operator (role 4), viewer (role 6) → 403.

Valid setting keys (from config_keys.py):
  range (0.0–1.0): lpr.confidence_threshold, face.match_threshold,
                   ppe.confidence_threshold, crowd.alert_threshold_ratio,
                   fire_smoke.confidence_threshold, weapon.confidence_threshold,
                   tampering.score_threshold, fall.confidence_threshold
  positive int:    intrusion.breach_cooldown_seconds, evidence.retention_days,
                   crowd.breach_cooldown_seconds, behavior.loitering_dwell_seconds,
                   behavior.breach_cooldown_seconds, abandoned.dwell_seconds,
                   2fa.grace_hours
  bool:            2fa.required

Sections:
  A — GET behavior (3 tests)
  B — PUT behavior (3 tests)
  C — Validation (3 tests)
  D — Permissions + RLS (2 tests)
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


async def _seed_tenant_and_token(role_id: int = 2):
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"stg-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Settings Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Settings Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"stg-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. GET behavior ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stg_list_empty_fresh_tenant():
    """GET /settings on fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/settings")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_stg_list_after_upsert_shows_row():
    """After PUT, GET /settings shows the upserted row."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await c.put("/api/v1/settings/evidence.retention_days",
                    json={"setting_value": 60})
        r = await c.get("/api/v1/settings")
    assert r.status_code == 200
    rows = r.json()
    keys = [row["setting_key"] for row in rows]
    assert "evidence.retention_days" in keys


@pytest.mark.asyncio
async def test_stg_list_row_has_required_fields():
    """Each settings row has setting_key, setting_value, updated_by_user_id, updated_at."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await c.put("/api/v1/settings/lpr.confidence_threshold",
                    json={"setting_value": 0.6})
        r = await c.get("/api/v1/settings")
    rows = r.json()
    assert len(rows) >= 1
    row = next(rw for rw in rows if rw["setting_key"] == "lpr.confidence_threshold")
    for field in ("setting_key", "setting_value", "updated_by_user_id", "updated_at"):
        assert field in row, f"Missing field: {field}"
    assert row["setting_value"] == 0.6


# ─── B. PUT behavior ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stg_put_range_setting_returns_correct_value():
    """PUT lpr.confidence_threshold with valid 0.0–1.0 value → 200 + echoes value."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put("/api/v1/settings/face.match_threshold",
                        json={"setting_value": 0.75})
    assert r.status_code == 200
    body = r.json()
    assert body["setting_key"] == "face.match_threshold"
    assert body["setting_value"] == 0.75
    assert "updated_by_user_id" in body
    assert "updated_at" in body


@pytest.mark.asyncio
async def test_stg_put_positive_int_setting():
    """PUT intrusion.breach_cooldown_seconds with valid positive int → 200."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put("/api/v1/settings/intrusion.breach_cooldown_seconds",
                        json={"setting_value": 120})
    assert r.status_code == 200
    assert r.json()["setting_value"] == 120


@pytest.mark.asyncio
async def test_stg_put_upsert_updates_not_duplicates():
    """PUT same key twice updates the row; GET returns exactly one row for that key."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await c.put("/api/v1/settings/evidence.retention_days",
                    json={"setting_value": 30})
        r2 = await c.put("/api/v1/settings/evidence.retention_days",
                         json={"setting_value": 45})
        assert r2.status_code == 200
        assert r2.json()["setting_value"] == 45
        r_list = await c.get("/api/v1/settings")
    matching = [row for row in r_list.json()
                if row["setting_key"] == "evidence.retention_days"]
    assert len(matching) == 1, "Upsert should not duplicate rows"
    assert matching[0]["setting_value"] == 45


# ─── C. Validation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stg_put_threshold_above_max_returns_422():
    """PUT lpr.confidence_threshold with value > 1.0 → 422 Unprocessable."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put("/api/v1/settings/lpr.confidence_threshold",
                        json={"setting_value": 1.5})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_stg_put_negative_int_returns_422():
    """PUT evidence.retention_days with negative value → 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put("/api/v1/settings/evidence.retention_days",
                        json={"setting_value": -5})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_stg_put_unknown_key_returns_400():
    """PUT /settings/{unknown_key} → 400 Bad Request."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put("/api/v1/settings/not.a.real.key",
                        json={"setting_value": 99})
    assert r.status_code == 400


# ─── D. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stg_operator_cannot_list_403():
    """Operator (role 4) lacks settings:read → GET /settings returns 403."""
    _, _, token = await _seed_tenant_and_token(role_id=4)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/settings")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_stg_rls_isolation():
    """Tenant B cannot see Tenant A's settings rows."""
    _, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_a) as c:
        await c.put("/api/v1/settings/evidence.retention_days",
                    json={"setting_value": 180})
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/settings")
    assert r.json() == []
