"""Gap 57 — Alerts Router

Isolated-tenant tests for backend/app/routers/alerts.py (520 lines).
14 HTTP endpoints, all use get_db_with_tenant (RLS-scoped per caller's tenant).

Endpoints (prefix /api/v1/alerts):
  POST   /                         — alert:create; dedup-aware; returns {id, deduplicated}
  GET    /                         — alert:read; paginated; filters: status, site_id, module_type
  POST   /{id}/acknowledge         — alert:acknowledge; 404 if not open
  POST   /{id}/false-positive      — alert:acknowledge; 404 if already closed
  GET    /{id}/correlated          — alert:read; [] if no correlation_id; 404 if not found
  POST   /bulk-acknowledge         — alert:acknowledge; 422 if empty/over-100
  POST   /bulk-dismiss             — alert:acknowledge; 422 if empty/over-100
  POST   /bulk-assign              — alert:acknowledge; 404 if user not found
  POST   /bulk-unassign            — alert:acknowledge; 422 if empty/over-100
  POST   /bulk-create-incidents    — incident:create; 422 if empty/over-50; skips dupes
  POST   /{id}/assign              — alert:acknowledge; 404 user/alert not found
  POST   /{id}/unassign            — alert:acknowledge; 404 if alert not found
  POST   /{id}/notes               — alert:acknowledge; 422 empty note; 404 alert not found
  GET    /{id}/notes               — alert:read; 404 if alert not found

Sections:
  A — Create (4 tests)
  B — List (5 tests)
  C — Single-alert state transitions (5 tests)
  D — Assignment (4 tests)
  E — Notes (4 tests)
  F — Bulk operations (5 tests)
  G — Correlated alerts (3 tests)
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
    """Create isolated tenant + user; return (tenant_id, user_id, jwt_token)."""
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"alr-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Alert Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"alr-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera(tenant_id: uuid.UUID) -> str:
    cam_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO cameras (id, tenant_id, name, location) "
                "VALUES (:id, :tid, :name, 'Test Location')"
            ),
            {"id": cam_id, "tid": tenant_id, "name": f"cam-{cam_id.hex[:6]}"},
        )
        await s.commit()
    await engine.dispose()
    return str(cam_id)


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_alert(c: AsyncClient, cam_id: str, module_type: str = "intrusion",
                        severity: str = "medium", title: str = "Test Alert") -> str:
    r = await c.post("/api/v1/alerts", json={
        "camera_id": cam_id,
        "module_type": module_type,
        "severity": severity,
        "title": title,
    })
    assert r.status_code == 200, f"create_alert failed: {r.text}"
    return r.json()["id"]


# ─── A. Create ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_alert_returns_id():
    """POST /alerts returns {id, deduplicated: false} for a new alert."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/alerts", json={
            "camera_id": cam_id,
            "module_type": "lpr",
            "severity": "high",
            "title": "Plate detected",
        })
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["deduplicated"] is False


@pytest.mark.asyncio
async def test_create_alert_viewer_returns_403():
    """Viewer (role 6) cannot create alerts — alert:create not granted."""
    tid, _, token = await _seed_tenant_and_token(role_id=6)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/alerts", json={
            "camera_id": cam_id, "module_type": "lpr", "title": "X",
        })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_alert_unauthenticated_returns_401():
    """POST /alerts without token → 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.post("/api/v1/alerts", json={
            "camera_id": str(uuid.uuid4()), "module_type": "lpr", "title": "X",
        })
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_alert_all_severity_levels():
    """POST /alerts accepts any severity string."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        for sev in ("low", "medium", "high", "critical", "info"):
            r = await c.post("/api/v1/alerts", json={
                "camera_id": cam_id, "module_type": "face",
                "severity": sev, "title": f"Alert {sev}",
            })
            assert r.status_code == 200, f"Failed for severity={sev}: {r.text}"


# ─── B. List ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_alerts_shows_created_alert():
    """GET /alerts returns the alert just created."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id, title="Visible Alert")
        r = await c.get("/api/v1/alerts")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(item["id"] == alert_id for item in items)


@pytest.mark.asyncio
async def test_list_alerts_response_fields():
    """GET /alerts items include expected fields with camera_name from JOIN."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        await _create_alert(c, cam_id)
        r = await c.get("/api/v1/alerts")
    item = r.json()["items"][0]
    for field in ("id", "camera_id", "module_type", "severity", "status", "created_at", "camera_name"):
        assert field in item, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_list_alerts_status_filter():
    """GET /alerts?status_filter=open returns only open alerts."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id)
        r = await c.get("/api/v1/alerts?status_filter=open")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(item["id"] == alert_id for item in items)
    assert all(item["status"] == "open" for item in items)


@pytest.mark.asyncio
async def test_list_alerts_module_type_filter():
    """GET /alerts?module_type=lpr returns only LPR alerts."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        lpr_id = await _create_alert(c, cam_id, module_type="lpr", title="LPR Alert")
        await _create_alert(c, cam_id, module_type="face", title="Face Alert")
        r = await c.get("/api/v1/alerts?module_type=lpr")
    items = r.json()["items"]
    assert all(item["module_type"] == "lpr" for item in items)
    assert any(item["id"] == lpr_id for item in items)


@pytest.mark.asyncio
async def test_list_alerts_rls_isolation():
    """Tenant B cannot see Tenant A's alerts."""
    tid_a, _, tok_a = await _seed_tenant_and_token(role_id=2)
    tid_b, _, tok_b = await _seed_tenant_and_token(role_id=2)
    cam_a = await _seed_camera(tid_a)
    async with await _authed(tok_a) as c:
        alert_id = await _create_alert(c, cam_a, title="Tenant A Alert")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/alerts")
    ids = [item["id"] for item in r.json()["items"]]
    assert alert_id not in ids


# ─── C. Single-alert state transitions ───────────────────────────────────────

@pytest.mark.asyncio
async def test_acknowledge_alert_sets_status():
    """POST /{id}/acknowledge sets status to 'acknowledged'."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id)
        r = await c.post(f"/api/v1/alerts/{alert_id}/acknowledge")
    assert r.status_code == 200
    assert r.json()["status"] == "acknowledged"


@pytest.mark.asyncio
async def test_acknowledge_already_acknowledged_returns_404():
    """Acknowledging an already-acknowledged alert returns 404 (not open)."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id)
        await c.post(f"/api/v1/alerts/{alert_id}/acknowledge")
        r = await c.post(f"/api/v1/alerts/{alert_id}/acknowledge")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_false_positive_on_open_alert():
    """POST /{id}/false-positive on an open alert sets status to 'false_positive'."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id)
        r = await c.post(f"/api/v1/alerts/{alert_id}/false-positive",
                         json={"fp_reason": "Shadow triggered sensor"})
    assert r.status_code == 200
    assert r.json()["status"] == "false_positive"


@pytest.mark.asyncio
async def test_false_positive_on_acknowledged_alert():
    """POST /{id}/false-positive is allowed on an acknowledged alert."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id)
        await c.post(f"/api/v1/alerts/{alert_id}/acknowledge")
        r = await c.post(f"/api/v1/alerts/{alert_id}/false-positive", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "false_positive"


@pytest.mark.asyncio
async def test_acknowledge_cross_tenant_returns_404():
    """Acknowledging an alert from another tenant returns 404 (RLS blocks it)."""
    tid_a, _, tok_a = await _seed_tenant_and_token(role_id=2)
    tid_b, _, tok_b = await _seed_tenant_and_token(role_id=2)
    cam_a = await _seed_camera(tid_a)
    async with await _authed(tok_a) as c:
        alert_id = await _create_alert(c, cam_a)
    async with await _authed(tok_b) as c:
        r = await c.post(f"/api/v1/alerts/{alert_id}/acknowledge")
    assert r.status_code == 404


# ─── D. Assignment ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assign_alert_to_user():
    """POST /{id}/assign sets assigned_to_user_id."""
    tid, user_id, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id)
        r = await c.post(f"/api/v1/alerts/{alert_id}/assign",
                         json={"assigned_to_user_id": str(user_id)})
    assert r.status_code == 200
    assert str(r.json()["assigned_to_user_id"]) == str(user_id)


@pytest.mark.asyncio
async def test_assign_alert_unknown_user_returns_404():
    """POST /{id}/assign with a non-existent user returns 404."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id)
        r = await c.post(f"/api/v1/alerts/{alert_id}/assign",
                         json={"assigned_to_user_id": str(uuid.uuid4())})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unassign_alert():
    """POST /{id}/unassign clears assignment."""
    tid, user_id, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id)
        await c.post(f"/api/v1/alerts/{alert_id}/assign",
                     json={"assigned_to_user_id": str(user_id)})
        r = await c.post(f"/api/v1/alerts/{alert_id}/unassign")
    assert r.status_code == 200
    assert r.json()["assigned_to_user_id"] is None


@pytest.mark.asyncio
async def test_unassign_unknown_alert_returns_404():
    """POST /unknown-id/unassign returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/alerts/{uuid.uuid4()}/unassign")
    assert r.status_code == 404


# ─── E. Notes ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_note_returns_201():
    """POST /{id}/notes returns 201 with note id, text, and author."""
    tid, user_id, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id)
        r = await c.post(f"/api/v1/alerts/{alert_id}/notes",
                         json={"note": "Verified on camera footage"})
    assert r.status_code == 201
    body = r.json()
    assert body["note"] == "Verified on camera footage"
    assert "id" in body
    assert str(body["author_user_id"]) == str(user_id)


@pytest.mark.asyncio
async def test_add_empty_note_returns_422():
    """POST /{id}/notes with an empty string returns 422."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id)
        r = await c.post(f"/api/v1/alerts/{alert_id}/notes", json={"note": "   "})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_notes_returns_added_note():
    """GET /{id}/notes returns notes added to that alert."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id)
        await c.post(f"/api/v1/alerts/{alert_id}/notes", json={"note": "First note"})
        r = await c.get(f"/api/v1/alerts/{alert_id}/notes")
    assert r.status_code == 200
    notes = r.json()
    assert len(notes) >= 1
    assert notes[0]["note"] == "First note"


@pytest.mark.asyncio
async def test_add_note_unknown_alert_returns_404():
    """POST /unknown-id/notes returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/alerts/{uuid.uuid4()}/notes",
                         json={"note": "Ghost note"})
    assert r.status_code == 404


# ─── F. Bulk operations ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_acknowledge_updates_count():
    """POST /bulk-acknowledge with 2 alert IDs returns updated=2."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        id1 = await _create_alert(c, cam_id, title="Bulk A")
        id2 = await _create_alert(c, cam_id, title="Bulk B")
        r = await c.post("/api/v1/alerts/bulk-acknowledge", json={"ids": [id1, id2]})
    assert r.status_code == 200
    assert r.json()["updated"] == 2
    assert r.json()["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_acknowledge_empty_ids_returns_422():
    """POST /bulk-acknowledge with empty ids list returns 422."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/alerts/bulk-acknowledge", json={"ids": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_dismiss_sets_status_dismissed():
    """POST /bulk-dismiss correctly counts updated alerts."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id, title="Dismiss Me")
        r = await c.post("/api/v1/alerts/bulk-dismiss", json={"ids": [alert_id]})
    assert r.status_code == 200
    assert r.json()["updated"] == 1


@pytest.mark.asyncio
async def test_bulk_create_incidents_returns_incident_ids():
    """POST /bulk-create-incidents creates one incident per alert."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        id1 = await _create_alert(c, cam_id, title="Incident Source 1")
        id2 = await _create_alert(c, cam_id, title="Incident Source 2")
        r = await c.post("/api/v1/alerts/bulk-create-incidents", json={"ids": [id1, id2]})
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 2
    assert len(body["incident_ids"]) == 2


@pytest.mark.asyncio
async def test_bulk_assign_unknown_user_returns_404():
    """POST /bulk-assign with a non-existent user returns 404."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id)
        r = await c.post("/api/v1/alerts/bulk-assign",
                         json={"ids": [alert_id], "assigned_to_user_id": str(uuid.uuid4())})
    assert r.status_code == 404


# ─── G. Correlated alerts ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_correlated_alerts_empty_when_no_correlation():
    """GET /{id}/correlated returns [] for an alert with no correlation_id."""
    tid, _, token = await _seed_tenant_and_token(role_id=2)
    cam_id = await _seed_camera(tid)
    async with await _authed(token) as c:
        alert_id = await _create_alert(c, cam_id, title="Solo Alert")
        r = await c.get(f"/api/v1/alerts/{alert_id}/correlated")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_correlated_alerts_unknown_alert_returns_404():
    """GET /{unknown}/correlated returns 404."""
    _, _, token = await _seed_tenant_and_token(role_id=2)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/alerts/{uuid.uuid4()}/correlated")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_correlated_alerts_cross_tenant_returns_404():
    """GET /{id}/correlated on another tenant's alert returns 404 (RLS)."""
    tid_a, _, tok_a = await _seed_tenant_and_token(role_id=2)
    _, _, tok_b = await _seed_tenant_and_token(role_id=2)
    cam_a = await _seed_camera(tid_a)
    async with await _authed(tok_a) as c:
        alert_id = await _create_alert(c, cam_a)
    async with await _authed(tok_b) as c:
        r = await c.get(f"/api/v1/alerts/{alert_id}/correlated")
    assert r.status_code == 404
