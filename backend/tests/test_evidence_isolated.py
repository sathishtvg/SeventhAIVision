"""Gap 78 — Evidence Router (isolated-tenant)

Isolated-tenant tests for backend/app/routers/evidence.py (55 lines).

Endpoints:
  GET /api/v1/evidence              — evidence:read; plain list (max 200); ?limit=
  GET /api/v1/evidence/{id}/file    — evidence:read; FileResponse or 307 for S3;
                                      404 if DB row missing; 404 if file not on disk

Permission: evidence:read granted to all roles (1-6).
Evidence table is partitioned by captured_at.

Sections:
  A — List (2 tests)
  B — File download (2 tests)
  C — Permissions + RLS (2 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

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
    slug = f"evi-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Evidence Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'Evidence Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"evi-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_evidence_row(
    tenant_id: uuid.UUID,
    storage_path: str = "nonexistent/path.jpg",
    media_type: str = "image",
) -> uuid.UUID:
    """Insert an evidence row via admin engine.
    pg_partman already pre-creates monthly partitions — no manual partition needed.
    """
    ev_id = uuid.uuid4()
    captured_at = datetime.now(timezone.utc)
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO evidence (id, tenant_id, media_type, storage_path, captured_at) "
                "VALUES (:id, :tid, :mtype, :spath, :cat)"
            ),
            {"id": ev_id, "tid": tenant_id, "mtype": media_type,
             "spath": storage_path, "cat": captured_at},
        )
        await s.commit()
    await engine.dispose()
    return ev_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. List ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evi_list_empty_fresh_tenant():
    """GET /evidence on fresh tenant returns 200 + []."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/evidence")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_evi_list_seeded_row_appears_with_fields():
    """Seeded evidence row appears in GET /evidence with all 7 expected fields."""
    tenant_id, _, token = await _seed_tenant_and_token()
    ev_id = await _seed_evidence_row(tenant_id, storage_path="test/snap.jpg")
    async with await _authed(token) as c:
        r = await c.get("/api/v1/evidence")
    assert r.status_code == 200
    rows = r.json()
    ids = [row["id"] for row in rows]
    assert str(ev_id) in ids
    row = next(rw for rw in rows if rw["id"] == str(ev_id))
    for field in ("id", "detection_id", "incident_id", "media_type",
                  "storage_path", "checksum_sha256", "captured_at"):
        assert field in row, f"Missing field: {field}"
    assert row["media_type"] == "image"
    assert row["storage_path"] == "test/snap.jpg"


# ─── B. File download ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evi_file_unknown_id_returns_404():
    """GET /evidence/{random_uuid}/file returns 404 when ID not in DB."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/evidence/{uuid.uuid4()}/file")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_evi_file_missing_on_disk_returns_404():
    """GET /evidence/{id}/file returns 404 when DB row exists but file not on disk."""
    tenant_id, _, token = await _seed_tenant_and_token()
    ev_id = await _seed_evidence_row(tenant_id, storage_path="no/such/file_xyz.jpg")
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/evidence/{ev_id}/file")
    assert r.status_code == 404


# ─── C. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evi_viewer_can_list():
    """Viewer (role 6) has evidence:read → GET /evidence returns 200."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/evidence")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_evi_rls_isolation():
    """Tenant B cannot see Tenant A's evidence rows."""
    tenant_a, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    await _seed_evidence_row(tenant_a, storage_path="secret/evidence.jpg")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/evidence")
    assert r.json() == []
