"""Gap 72 — Digital Occurrence Book Router

Isolated-tenant tests for backend/app/routers/dob.py (141 lines).
Append-only log (POST/GET only — no edit, no delete).

Endpoints:
  POST /api/v1/dob              — dob:write; valid entry_type required
  GET  /api/v1/dob              — dob:read; filter by site_id/shift_id/entry_type/date
  GET  /api/v1/dob/{entry_id}  — dob:read; 404 unknown

Sections:
  A — Basic CRUD (5 tests)
  B — Filters + join fields (2 tests)
  C — Validation (1 test)
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
    slug = f"dob-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"DOB Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'DOB Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"dob-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_entry(c: AsyncClient, entry_type: str = "general", body: str = "Test entry") -> dict:
    r = await c.post("/api/v1/dob", json={"entry_type": entry_type, "body": body})
    assert r.status_code == 200, f"create_entry failed: {r.text}"
    return r.json()


# ─── A. Basic CRUD ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dob_list_empty_fresh_tenant():
    """GET /dob on fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/dob")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_dob_create_returns_required_fields():
    """POST /dob returns {id, entry_type, body, severity, occurred_at}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/dob", json={
            "entry_type": "general",
            "body": "Guard started shift at North Gate",
            "severity": "low",
        })
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["entry_type"] == "general"
    assert body["body"] == "Guard started shift at North Gate"
    assert body["severity"] == "low"
    assert "occurred_at" in body


@pytest.mark.asyncio
async def test_dob_created_entry_appears_in_list():
    """After POST /dob, the entry appears in GET /dob."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        entry = await _create_entry(c, entry_type="patrol_start", body="Patrol begun")
        r = await c.get("/api/v1/dob")
    ids = [e["id"] for e in r.json()]
    assert entry["id"] in ids


@pytest.mark.asyncio
async def test_dob_get_by_id_returns_entry():
    """GET /dob/{id} returns the entry with author_name field."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        entry = await _create_entry(c, body="Equipment check complete")
        r = await c.get(f"/api/v1/dob/{entry['id']}")
    assert r.status_code == 200
    row = r.json()
    assert row["id"] == entry["id"]
    assert row["body"] == "Equipment check complete"
    assert "author_name" in row


@pytest.mark.asyncio
async def test_dob_get_unknown_entry_returns_404():
    """GET /dob/{random_uuid} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/dob/{uuid.uuid4()}")
    assert r.status_code == 404


# ─── B. Filters + join fields ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dob_entry_type_filter_isolates():
    """?entry_type=alarm_activation returns only that type."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await _create_entry(c, entry_type="general", body="General entry")
        await _create_entry(c, entry_type="alarm_activation", body="Alarm fired")
        r = await c.get("/api/v1/dob?entry_type=alarm_activation")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    assert all(row["entry_type"] == "alarm_activation" for row in rows)


@pytest.mark.asyncio
async def test_dob_list_includes_join_fields():
    """GET /dob list rows include author_name and site_name fields from JOINs."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await _create_entry(c, body="Join fields test")
        r = await c.get("/api/v1/dob")
    rows = r.json()
    assert len(rows) >= 1
    row = rows[0]
    assert "author_name" in row
    assert "site_name" in row


# ─── C. Validation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dob_invalid_entry_type_returns_422():
    """POST /dob with an invalid entry_type returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/dob", json={"entry_type": "coffee_break", "body": "Went for coffee"})
    assert r.status_code == 422


# ─── D. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dob_viewer_cannot_create_entry_403():
    """Viewer (role 6) does not have dob:write → POST /dob returns 403."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/dob", json={"entry_type": "general", "body": "Viewer entry"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_dob_rls_isolation():
    """Tenant B cannot see Tenant A's DOB entries via GET /dob."""
    _, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_a) as c:
        await _create_entry(c, body="Tenant A confidential entry")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/dob")
    assert r.json() == []
