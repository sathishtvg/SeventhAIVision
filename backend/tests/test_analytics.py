"""Gap 63 — Analytics Router

Isolated-tenant tests for backend/app/routers/analytics.py (237 lines).
All endpoints require 'alert:read' (granted to all roles, including viewer).

Endpoints (prefix /api/v1/analytics):
  GET /summary                  — 8-key dict; optional ?site_id=
  GET /alerts/by-severity       — list [{severity, count}]; ?days=30
  GET /alerts/by-module         — list [{module_type, count}]; ?days=30
  GET /detections/trend         — list [{day, count}]; ?days=7
  GET /alerts/trend             — list [{day, count}]; ?days=7
  GET /top-cameras              — list [{camera_id, camera_name, alert_count}]; ?days=30&limit=10
  GET /heatmap                  — list per-camera [{camera_id, ...12 fields}]; ?hours=24
  GET /incidents/resolution-time — {resolved_count, avg_hours, p95_hours}; ?days=30

Sections:
  A — Summary endpoint (5 tests)
  B — Alerts by-severity (2 tests)
  C — Alerts by-module (2 tests)
  D — Detections + alerts trend (2 tests)
  E — Top cameras (2 tests)
  F — Heatmap (3 tests)
  G — Incidents resolution time (2 tests)
  H — Permissions (1 test)
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
    """Create isolated tenant + user; return (tenant_id, user_id, token)."""
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"anl-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Analytics Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', 'Anl Tester')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"anl-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera(tenant_id: uuid.UUID) -> uuid.UUID:
    """Seed a camera row via admin engine (bypasses RLS). Returns camera_id."""
    cam_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO cameras (id, tenant_id, name, location, is_active) "
                "VALUES (:id, :tid, :name, :loc, TRUE)"
            ),
            {"id": cam_id, "tid": tenant_id, "name": "Anl Cam", "loc": "Lobby"},
        )
        await s.commit()
    await engine.dispose()
    return cam_id


async def _seed_alert(tenant_id: uuid.UUID, camera_id: uuid.UUID,
                      severity: str = "medium", status: str = "open",
                      module_type: str = "lpr") -> uuid.UUID:
    """Seed an alert row via admin engine. Returns alert_id."""
    alert_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, "
                "title, status, alert_code) "
                "VALUES (:id, :tid, :cid, :mod, :sev, 'Test Alert', :status, 'test.alert')"
            ),
            {"id": alert_id, "tid": tenant_id, "cid": camera_id,
             "mod": module_type, "sev": severity, "status": status},
        )
        await s.commit()
    await engine.dispose()
    return alert_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. Summary ───────────────────────────────────────────────────────────────

_SUMMARY_KEYS = {
    "open_alerts", "open_incidents", "active_cameras", "detections_today",
    "alerts_today", "alerts_7d", "detections_7d", "active_recordings",
}


@pytest.mark.asyncio
async def test_summary_has_all_8_keys():
    """GET /analytics/summary returns 200 + all 8 required keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/summary")
    assert r.status_code == 200
    assert _SUMMARY_KEYS <= set(r.json().keys())


@pytest.mark.asyncio
async def test_summary_all_zeros_fresh_tenant():
    """GET /analytics/summary on a fresh tenant returns all zero counts."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/summary")
    body = r.json()
    assert body["open_alerts"] == 0
    assert body["active_cameras"] == 0
    assert body["open_incidents"] == 0


@pytest.mark.asyncio
async def test_summary_open_alerts_increments():
    """After seeding an open alert, open_alerts increases to 1."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    await _seed_alert(tenant_id, cam_id, status="open")
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/summary")
    assert r.json()["open_alerts"] == 1


@pytest.mark.asyncio
async def test_summary_active_cameras_increments():
    """After seeding an active camera, active_cameras increases to 1."""
    tenant_id, _, token = await _seed_tenant_and_token()
    await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/summary")
    assert r.json()["active_cameras"] == 1


@pytest.mark.asyncio
async def test_summary_with_site_id_filter_returns_all_8_keys():
    """GET /analytics/summary?site_id=<uuid> returns 8 keys (site-scoped variant)."""
    _, _, token = await _seed_tenant_and_token()
    dummy_site_id = str(uuid.uuid4())
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/analytics/summary?site_id={dummy_site_id}")
    assert r.status_code == 200
    assert _SUMMARY_KEYS <= set(r.json().keys())


# ─── B. Alerts by-severity ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alerts_by_severity_empty_fresh_tenant():
    """GET /analytics/alerts/by-severity on a fresh tenant returns empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/alerts/by-severity")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_alerts_by_severity_shows_entry_after_alert():
    """After seeding a critical alert, by-severity returns an entry with count >= 1."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    await _seed_alert(tenant_id, cam_id, severity="critical")
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/alerts/by-severity")
    rows = r.json()
    assert len(rows) >= 1
    severities = [row["severity"] for row in rows]
    assert "critical" in severities
    match = next(row for row in rows if row["severity"] == "critical")
    assert match["count"] >= 1


# ─── C. Alerts by-module ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alerts_by_module_empty_fresh_tenant():
    """GET /analytics/alerts/by-module on a fresh tenant returns empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/alerts/by-module")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_alerts_by_module_shows_module_after_alert():
    """After seeding an lpr alert, by-module returns lpr with count >= 1."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    await _seed_alert(tenant_id, cam_id, module_type="lpr")
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/alerts/by-module")
    rows = r.json()
    modules = [row["module_type"] for row in rows]
    assert "lpr" in modules


# ─── D. Trend endpoints ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detections_trend_returns_list():
    """GET /analytics/detections/trend returns 200 + a list (empty on fresh tenant)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/detections/trend")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_alerts_trend_returns_list():
    """GET /analytics/alerts/trend returns 200 + a list (empty on fresh tenant)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/alerts/trend")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ─── E. Top cameras ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_top_cameras_empty_fresh_tenant():
    """GET /analytics/top-cameras on a fresh tenant returns empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/top-cameras")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_top_cameras_shows_camera_after_alert():
    """After seeding an alert, top-cameras includes that camera."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    await _seed_alert(tenant_id, cam_id)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/top-cameras")
    rows = r.json()
    assert len(rows) >= 1
    camera_ids = [row["camera_id"] for row in rows]
    assert str(cam_id) in camera_ids
    match = next(row for row in rows if row["camera_id"] == str(cam_id))
    assert match["alert_count"] >= 1


# ─── F. Heatmap ───────────────────────────────────────────────────────────────

_HEATMAP_FIELDS = {
    "camera_id", "camera_name", "location", "latitude", "longitude",
    "site_id", "site_name", "stream_status",
    "total_detections", "total_alerts", "open_alerts",
    "critical_alerts", "high_alerts",
}


@pytest.mark.asyncio
async def test_heatmap_empty_when_no_cameras():
    """GET /analytics/heatmap returns [] when tenant has no cameras."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/heatmap")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_heatmap_shows_active_camera():
    """After seeding an active camera, heatmap returns that camera."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/heatmap")
    rows = r.json()
    assert len(rows) >= 1
    camera_ids = [row["camera_id"] for row in rows]
    assert str(cam_id) in camera_ids


@pytest.mark.asyncio
async def test_heatmap_row_has_all_required_fields():
    """Heatmap response rows contain all 13 required fields."""
    tenant_id, _, token = await _seed_tenant_and_token()
    await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/heatmap")
    rows = r.json()
    assert rows  # at least one row
    assert _HEATMAP_FIELDS <= set(rows[0].keys())


# ─── G. Incidents resolution time ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolution_time_zero_when_no_data():
    """GET /analytics/incidents/resolution-time on fresh tenant: resolved_count=0, nulls for averages."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/incidents/resolution-time")
    assert r.status_code == 200
    body = r.json()
    assert body["resolved_count"] == 0
    assert body["avg_hours"] is None
    assert body["p95_hours"] is None


@pytest.mark.asyncio
async def test_resolution_time_accepts_days_param():
    """GET /analytics/incidents/resolution-time?days=7 returns 200."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/incidents/resolution-time?days=7")
    assert r.status_code == 200


# ─── H. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_can_access_analytics_summary():
    """Viewer (role 6) has alert:read → analytics/summary returns 200."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/analytics/summary")
    assert r.status_code == 200
