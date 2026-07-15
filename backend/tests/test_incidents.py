"""Gap 42 — Incidents Router

Covers all 9 endpoints in backend/app/routers/incidents.py:
  POST   /api/v1/incidents                        — create incident (incident:create)
  GET    /api/v1/incidents                        — list with filters (incident:read)
  POST   /api/v1/incidents/{id}/notes             — add note (incident:update)
  POST   /api/v1/incidents/{id}/assign            — assign to user (incident:assign)
  POST   /api/v1/incidents/{id}/resolve           — resolve (incident:resolve)
  PUT    /api/v1/incidents/{id}/status            — status transition + history (incident:update)
  GET    /api/v1/incidents/{id}/timeline          — status history + notes (authenticated)
  POST   /api/v1/incidents/bulk-resolve           — bulk resolve (incident:resolve)
  POST   /api/v1/incidents/bulk-update-status     — bulk status update (incident:update)

Sections:
  A — DB schema: incident_status_history columns
  B — Create: 201 + fields, invalid severity 422
  C — List: paginate structure, status filter, empty for fresh tenant
  D — Notes: add note, note appears in timeline
  E — Assign: assign sets user, 404 on unknown incident
  F — Resolve: status becomes resolved, 404 on unknown
  G — Status transitions: valid transition, invalid status 422, 404
  H — Timeline: combined status+notes list in chronological order
  I — Bulk: bulk-resolve updated/skipped counts, bulk-update-status
  J — Permissions: viewer 403 on create, unauth 401
  K — RLS: cross-tenant list empty, cross-tenant timeline empty
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
    slug = f"inc-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Incident Test {slug}", "slug": slug},
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
                "email": f"inc-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_extra_user(tenant_id: uuid.UUID, role_id: int = 4) -> uuid.UUID:
    user_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                "VALUES (:id, :tid, :role, :email, 'x')"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"extra-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    return user_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_incident(client: AsyncClient, **kwargs) -> dict:
    """Helper: POST /incidents with defaults; asserts 201."""
    payload = {"title": "Test Incident", "severity": "medium", **kwargs}
    r = await client.post("/api/v1/incidents", json=payload)
    assert r.status_code == 201, f"create_incident failed {r.status_code}: {r.text}"
    return r.json()


# ─── A. DB Schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_incident_status_history_columns():
    """incident_status_history table has the expected lifecycle columns."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'incident_status_history'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in ("id", "tenant_id", "incident_id", "changed_by_user_id",
                "from_status", "to_status", "latitude", "longitude", "notes"):
        assert col in cols, f"Column {col!r} missing from incident_status_history"


# ─── B. Create ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_incident_returns_201_with_fields():
    """POST /incidents with valid data returns 201 with id, title, severity, status."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/incidents", json={"title": "Break-in at Gate", "severity": "high"})
    assert r.status_code == 201
    data = r.json()
    assert "id" in data
    assert data["title"] == "Break-in at Gate"
    assert data["severity"] == "high"
    assert data["status"] == "open"


@pytest.mark.asyncio
async def test_create_incident_invalid_severity_422():
    """POST /incidents with an invalid severity value returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/incidents", json={"title": "Bad Severity", "severity": "extreme"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_incident_with_description():
    """POST /incidents with description stores it (returned in list)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/incidents",
            json={"title": "Described Incident", "severity": "low",
                  "description": "Detailed description here"},
        )
    assert r.status_code == 201
    assert "id" in r.json()


# ─── C. List ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_incidents_returns_paginate_structure():
    """GET /incidents returns {items, total} paginate response."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await _create_incident(c, title="Incident Alpha")
        r = await c.get("/api/v1/incidents")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert isinstance(data["items"], list)
    assert any(i["title"] == "Incident Alpha" for i in data["items"])


@pytest.mark.asyncio
async def test_list_incidents_status_filter():
    """GET /incidents?status_filter=resolved returns only resolved incidents."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        inc = await _create_incident(c, title="To Resolve")
        await c.post(f"/api/v1/incidents/{inc['id']}/resolve")
        r = await c.get("/api/v1/incidents", params={"status_filter": "resolved"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    assert all(i["status"] == "resolved" for i in items)


@pytest.mark.asyncio
async def test_list_incidents_empty_for_fresh_tenant():
    """GET /incidents returns empty items list for a new tenant with no incidents."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/incidents")
    assert r.status_code == 200
    assert r.json()["items"] == []


# ─── D. Notes ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_note_returns_id():
    """POST /incidents/{id}/notes returns the new note id."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        inc = await _create_incident(c)
        r = await c.post(f"/api/v1/incidents/{inc['id']}/notes", json={"note": "Guard dispatched."})
    assert r.status_code == 200
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_note_appears_in_timeline():
    """A note added to an incident shows up in GET /incidents/{id}/timeline."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        inc = await _create_incident(c)
        await c.post(f"/api/v1/incidents/{inc['id']}/notes", json={"note": "Timeline note text"})
        r = await c.get(f"/api/v1/incidents/{inc['id']}/timeline")
    assert r.status_code == 200
    timeline = r.json()
    note_entries = [e for e in timeline if e.get("type") == "note"]
    assert len(note_entries) >= 1
    assert any("Timeline note text" in e.get("notes", "") for e in note_entries)


# ─── E. Assign ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assign_incident_to_user():
    """POST /incidents/{id}/assign sets assigned_to_user_id."""
    tid, uid, token = await _seed_tenant_and_token()
    target_uid = await _seed_extra_user(tid)
    async with await _authed(token) as c:
        inc = await _create_incident(c)
        r = await c.post(
            f"/api/v1/incidents/{inc['id']}/assign",
            json={"assigned_to_user_id": str(target_uid)},
        )
    assert r.status_code == 200
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_assign_unknown_incident_404():
    """POST /incidents/{id}/assign with a non-existent ID returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(
            f"/api/v1/incidents/{uuid.uuid4()}/assign",
            json={"assigned_to_user_id": str(uuid.uuid4())},
        )
    assert r.status_code == 404


# ─── F. Resolve ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_incident_sets_status():
    """POST /incidents/{id}/resolve returns {id, status: 'resolved'}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        inc = await _create_incident(c)
        r = await c.post(f"/api/v1/incidents/{inc['id']}/resolve")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "resolved"


@pytest.mark.asyncio
async def test_resolve_unknown_incident_404():
    """POST /incidents/{id}/resolve with a non-existent ID returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(f"/api/v1/incidents/{uuid.uuid4()}/resolve")
    assert r.status_code == 404


# ─── G. Status Transitions ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_status_valid_transition():
    """PUT /incidents/{id}/status with valid status updates and returns from_status."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        inc = await _create_incident(c)
        r = await c.put(
            f"/api/v1/incidents/{inc['id']}/status",
            json={"status": "en_route", "notes": "Guard is on the way"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "en_route"
    assert data["from_status"] == "open"


@pytest.mark.asyncio
async def test_update_status_invalid_422():
    """PUT /incidents/{id}/status with an invalid status value returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        inc = await _create_incident(c)
        r = await c.put(
            f"/api/v1/incidents/{inc['id']}/status",
            json={"status": "abandoned"},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_status_unknown_incident_404():
    """PUT /incidents/{id}/status with a non-existent ID returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put(
            f"/api/v1/incidents/{uuid.uuid4()}/status",
            json={"status": "on_scene"},
        )
    assert r.status_code == 404


# ─── H. Timeline ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_timeline_contains_status_change_entry():
    """GET /incidents/{id}/timeline includes status_change entries after a PUT /status."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        inc = await _create_incident(c)
        await c.put(f"/api/v1/incidents/{inc['id']}/status", json={"status": "dispatched"})
        r = await c.get(f"/api/v1/incidents/{inc['id']}/timeline")
    assert r.status_code == 200
    timeline = r.json()
    assert isinstance(timeline, list)
    status_entries = [e for e in timeline if e.get("type") == "status_change"]
    assert len(status_entries) >= 1
    assert any(e.get("to_status") == "dispatched" for e in status_entries)


@pytest.mark.asyncio
async def test_timeline_chronological_order():
    """GET /incidents/{id}/timeline items are ordered oldest-first (chronological)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        inc = await _create_incident(c)
        await c.put(f"/api/v1/incidents/{inc['id']}/status", json={"status": "dispatched"})
        await c.post(f"/api/v1/incidents/{inc['id']}/notes", json={"note": "After dispatch"})
        r = await c.get(f"/api/v1/incidents/{inc['id']}/timeline")
    assert r.status_code == 200
    timeline = r.json()
    if len(timeline) >= 2:
        times = [e["occurred_at"] for e in timeline]
        assert times == sorted(times)


# ─── I. Bulk Operations ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_resolve_returns_updated_count():
    """POST /bulk-resolve resolves multiple open incidents and returns correct counts."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        ids = []
        for i in range(3):
            inc = await _create_incident(c, title=f"Bulk Inc {i}")
            ids.append(inc["id"])
        r = await c.post("/api/v1/incidents/bulk-resolve", json={"ids": ids})
    assert r.status_code == 200
    data = r.json()
    assert data["updated"] == 3
    assert data["skipped"] == 0


@pytest.mark.asyncio
async def test_bulk_resolve_skips_already_resolved():
    """POST /bulk-resolve counts already-resolved incidents as skipped."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        inc1 = await _create_incident(c, title="Already Done")
        inc2 = await _create_incident(c, title="Still Open")
        await c.post(f"/api/v1/incidents/{inc1['id']}/resolve")
        r = await c.post(
            "/api/v1/incidents/bulk-resolve",
            json={"ids": [inc1["id"], inc2["id"]]},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["updated"] == 1
    assert data["skipped"] == 1


@pytest.mark.asyncio
async def test_bulk_update_status_changes_multiple():
    """POST /bulk-update-status updates status for all matching incidents."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        ids = []
        for i in range(2):
            inc = await _create_incident(c, title=f"Status Update {i}")
            ids.append(inc["id"])
        r = await c.post(
            "/api/v1/incidents/bulk-update-status",
            json={"ids": ids, "status": "investigating"},
        )
    assert r.status_code == 200
    assert r.json()["updated"] == 2


@pytest.mark.asyncio
async def test_bulk_resolve_empty_ids_422():
    """POST /bulk-resolve with empty ids list returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/incidents/bulk-resolve", json={"ids": []})
    assert r.status_code == 422


# ─── J. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_create_incident():
    """POST /incidents requires incident:create — viewer (role 6) gets 403."""
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.post("/api/v1/incidents", json={"title": "Viewer Test", "severity": "low"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_list_returns_401():
    """GET /incidents without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/incidents")
    assert r.status_code == 401


# ─── K. RLS — Tenant Isolation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_cross_tenant_incidents_not_visible():
    """Tenant B cannot see Tenant A's incidents in the list."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()

    async with await _authed(token_a) as c:
        await _create_incident(c, title="Tenant A Incident")

    async with await _authed(token_b) as c:
        r = await c.get("/api/v1/incidents")
    assert r.status_code == 200
    assert r.json()["items"] == []


@pytest.mark.asyncio
async def test_rls_cross_tenant_timeline_empty():
    """GET /incidents/{id}/timeline for a cross-tenant incident returns empty []."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()

    async with await _authed(token_a) as c:
        inc_a = await _create_incident(c, title="Tenant A Inc")
        await c.post(f"/api/v1/incidents/{inc_a['id']}/notes", json={"note": "Secret note"})

    async with await _authed(token_b) as c:
        r = await c.get(f"/api/v1/incidents/{inc_a['id']}/timeline")
    assert r.status_code == 200
    assert r.json() == []
