"""Gap 65 — Dispatch, SLA Config, and Evidence Custody Routers

Isolated-tenant tests for backend/app/routers/dispatch.py (223 lines).
Three routers:
  router         — prefix /api/v1/dispatch  (incident:dispatch)
  sla_router     — prefix /api/v1/sla       (sla:manage)
  custody_router — prefix /api/v1/custody   (evidence:custody:read / evidence:read)

Endpoints:
  POST /dispatch/incidents/{id}           — assign guard; calculates SLA; returns dispatch fields
  POST /dispatch/incidents/{id}/arrived   — set guard_arrived_at; 404 if no guard dispatched
  GET  /sla/configs                       — list SLA configs ordered by severity
  PUT  /sla/configs/{severity}            — upsert SLA config; 422 invalid severity
  GET  /custody/{evidence_id}             — access log for evidence (empty list OK)
  POST /custody/{evidence_id}             — log access action; 422 invalid action

Sections:
  A — SLA Config (5 tests)
  B — Dispatch (4 tests)
  C — Custody (4 tests)
  D — Permissions (3 tests)
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
    slug = f"dsp-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Dispatch Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Dsp Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"dsp-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera(tenant_id: uuid.UUID) -> uuid.UUID:
    cam_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO cameras (id, tenant_id, name, location) VALUES (:id, :tid, :n, :l)"),
            {"id": cam_id, "tid": tenant_id, "n": "Dsp Cam", "l": "Gate"},
        )
        await s.commit()
    await engine.dispose()
    return cam_id


async def _seed_incident(tenant_id: uuid.UUID, camera_id: uuid.UUID) -> uuid.UUID:
    inc_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status) "
                "VALUES (:id, :tid, :cid, 'Test Incident', 'high', 'open')"
            ),
            {"id": inc_id, "tid": tenant_id, "cid": camera_id},
        )
        await s.commit()
    await engine.dispose()
    return inc_id


async def _seed_evidence(tenant_id: uuid.UUID) -> uuid.UUID:
    ev_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO evidence (id, tenant_id, media_type, storage_path, captured_at) "
                "VALUES (:id, :tid, 'image', 'test/path.jpg', now())"
            ),
            {"id": ev_id, "tid": tenant_id},
        )
        await s.commit()
    await engine.dispose()
    return ev_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. SLA Config ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dsp_list_sla_configs_empty_fresh_tenant():
    """GET /sla/configs on a fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/sla/configs")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_dsp_upsert_sla_config_returns_all_fields():
    """PUT /sla/configs/critical returns 200 + config row with all SLA fields."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put("/api/v1/sla/configs/critical", json={
            "ack_within_seconds": 60,
            "dispatch_within_seconds": 300,
            "resolve_within_seconds": 3600,
        })
    assert r.status_code == 200
    body = r.json()
    assert body["severity"] == "critical"
    assert body["ack_within_seconds"] == 60
    assert body["dispatch_within_seconds"] == 300
    assert body["resolve_within_seconds"] == 3600


@pytest.mark.asyncio
async def test_dsp_upsert_sla_config_appears_in_list():
    """After PUT /sla/configs/high, the config is visible via GET /sla/configs."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await c.put("/api/v1/sla/configs/high", json={
            "ack_within_seconds": 120,
            "dispatch_within_seconds": 600,
            "resolve_within_seconds": 7200,
        })
        r = await c.get("/api/v1/sla/configs")
    severities = [row["severity"] for row in r.json()]
    assert "high" in severities


@pytest.mark.asyncio
async def test_dsp_upsert_sla_config_invalid_severity_returns_422():
    """PUT /sla/configs/urgent returns 422 — 'urgent' is not a valid severity."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put("/api/v1/sla/configs/urgent", json={
            "ack_within_seconds": 60,
            "dispatch_within_seconds": 300,
            "resolve_within_seconds": 3600,
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_dsp_upsert_sla_config_idempotent_updates():
    """Second PUT /sla/configs/medium updates the existing row."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await c.put("/api/v1/sla/configs/medium", json={
            "ack_within_seconds": 90,
            "dispatch_within_seconds": 450,
            "resolve_within_seconds": 5400,
        })
        r = await c.put("/api/v1/sla/configs/medium", json={
            "ack_within_seconds": 180,
            "dispatch_within_seconds": 900,
            "resolve_within_seconds": 10800,
        })
    assert r.status_code == 200
    assert r.json()["ack_within_seconds"] == 180


# ─── B. Dispatch ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dsp_dispatch_guard_returns_dispatch_fields():
    """POST /dispatch/incidents/{id} returns dispatch fields including sla_deadline_at."""
    tenant_id, user_id, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    inc_id = await _seed_incident(tenant_id, cam_id)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/dispatch/incidents/{inc_id}", json={
            "guard_user_id": str(user_id),
            "dispatch_notes": "Responding immediately",
        })
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(inc_id)
    assert body["dispatched_guard_id"] == str(user_id)
    assert "dispatched_at" in body
    assert "sla_deadline_at" in body
    assert body["status"] == "in_progress"


@pytest.mark.asyncio
async def test_dsp_dispatch_unknown_incident_returns_404():
    """POST /dispatch/incidents/{random_uuid} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/dispatch/incidents/{uuid.uuid4()}", json={
            "guard_user_id": str(uuid.uuid4()),
        })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_dsp_guard_arrived_sets_timestamp():
    """POST /dispatch/incidents/{id}/arrived after dispatch returns guard_arrived_at."""
    tenant_id, user_id, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    inc_id = await _seed_incident(tenant_id, cam_id)
    async with await _authed(token) as c:
        # First dispatch
        await c.post(f"/api/v1/dispatch/incidents/{inc_id}", json={"guard_user_id": str(user_id)})
        # Then mark arrived
        r = await c.post(f"/api/v1/dispatch/incidents/{inc_id}/arrived")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(inc_id)
    assert body["guard_arrived_at"] is not None


@pytest.mark.asyncio
async def test_dsp_guard_arrived_without_dispatch_returns_404():
    """POST /dispatch/incidents/{id}/arrived before dispatch returns 404."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    inc_id = await _seed_incident(tenant_id, cam_id)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/dispatch/incidents/{inc_id}/arrived")
    assert r.status_code == 404


# ─── C. Custody ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dsp_custody_get_returns_empty_list_for_unknown_evidence():
    """GET /custody/{evidence_id} returns 200 + empty list when no access log exists."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/custody/{uuid.uuid4()}")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_dsp_custody_post_logs_access_action():
    """POST /custody/{evidence_id} logs the access and returns logged=True + id."""
    tenant_id, _, token = await _seed_tenant_and_token()
    ev_id = await _seed_evidence(tenant_id)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/custody/{ev_id}?action=view")
    assert r.status_code == 200
    body = r.json()
    assert body["logged"] is True
    assert "id" in body
    assert "accessed_at" in body


@pytest.mark.asyncio
async def test_dsp_custody_post_invalid_action_returns_422():
    """POST /custody/{evidence_id}?action=share returns 422 — invalid action."""
    tenant_id, _, token = await _seed_tenant_and_token()
    ev_id = await _seed_evidence(tenant_id)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/custody/{ev_id}?action=share")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_dsp_custody_get_shows_logged_access():
    """After POST /custody/{id}, the GET returns the logged entry."""
    tenant_id, _, token = await _seed_tenant_and_token()
    ev_id = await _seed_evidence(tenant_id)
    async with await _authed(token) as c:
        await c.post(f"/api/v1/custody/{ev_id}?action=download")
        r = await c.get(f"/api/v1/custody/{ev_id}")
    rows = r.json()
    assert len(rows) >= 1
    assert rows[0]["action"] == "download"


# ─── D. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dsp_viewer_cannot_list_sla_configs_403():
    """Viewer (role 6) does not have sla:manage — GET /sla/configs returns 403."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/sla/configs")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_dsp_viewer_cannot_dispatch_guard_403():
    """Viewer (role 6) does not have incident:dispatch — POST /dispatch/incidents/{id} returns 403."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/dispatch/incidents/{uuid.uuid4()}", json={
            "guard_user_id": str(uuid.uuid4()),
        })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_dsp_viewer_cannot_read_custody_log_403():
    """Viewer (role 6) does not have evidence:custody:read — GET /custody/{id} returns 403."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/custody/{uuid.uuid4()}")
    assert r.status_code == 403
