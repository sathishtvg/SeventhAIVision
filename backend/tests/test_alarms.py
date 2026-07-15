"""Gap 41 — Alarm Panel Integration

Covers all 14 endpoints in backend/app/routers/alarms.py:
  GET  /api/v1/alarms/dashboard
  GET  /api/v1/alarms/panels
  POST /api/v1/alarms/panels
  GET  /api/v1/alarms/panels/{panel_id}
  PUT  /api/v1/alarms/panels/{panel_id}
  PUT  /api/v1/alarms/panels/{panel_id}/arm
  PUT  /api/v1/alarms/panels/{panel_id}/disarm
  GET  /api/v1/alarms/panels/{panel_id}/events
  POST /api/v1/alarms/panels/{panel_id}/rotate-key
  POST /api/v1/alarms/panels/{panel_id}/zones
  PUT  /api/v1/alarms/zones/{zone_id}
  PUT  /api/v1/alarms/zones/{zone_id}/bypass
  POST /api/v1/alarms/events/ingest
  GET  /api/v1/alarms/events

Sections:
  A — DB schema: alarm_panels + alarm_zones columns
  B — Panel CRUD: create (api_key returned once), list (no api_key), get-by-ID, 404
  C — Arm/Disarm: arm away, invalid mode 422, disarm
  D — Zones: create valid type, invalid type 422, duplicate number 409
  E — Zone operations: update name, bypass toggle
  F — Ingest: info event, zone_alarm creates alert+incident, invalid key 401
  G — Events list: structure + event_type filter
  H — Panel events + key rotation
  I — Permissions: viewer 403, unauth 401
  J — RLS: cross-tenant panels invisible
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
    slug = f"alm-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Alarm Test {slug}", "slug": slug},
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
                "email": f"alm-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera(tenant_id: uuid.UUID) -> uuid.UUID:
    """Insert a minimal camera row (needed for alarm alert creation)."""
    camera_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) "
                "VALUES (:id, :tid, 'Alarm Cam', '[]')"
            ),
            {"id": camera_id, "tid": tenant_id},
        )
        await s.commit()
    await engine.dispose()
    return camera_id


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_panel(client: AsyncClient, **kwargs) -> dict:
    """Helper: POST /alarms/panels with defaults; returns JSON body."""
    payload = {"name": "Test Panel", "protocol": "webhook", **kwargs}
    r = await client.post("/api/v1/alarms/panels", json=payload)
    assert r.status_code == 200, f"create_panel failed: {r.text}"
    return r.json()


async def _create_zone(client: AsyncClient, panel_id: str, **kwargs) -> dict:
    """Helper: POST /alarms/panels/{id}/zones; returns JSON body."""
    payload = {"zone_number": 1, "name": "Main Door", "zone_type": "door", **kwargs}
    r = await client.post(f"/api/v1/alarms/panels/{panel_id}/zones", json=payload)
    assert r.status_code == 200, f"create_zone failed: {r.text}"
    return r.json()


# ─── A. DB Schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alarm_panels_key_columns():
    """alarm_panels table has operational lifecycle columns."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'alarm_panels'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in ("id", "tenant_id", "name", "protocol", "api_key",
                "arm_state", "status", "last_contact_at", "is_active"):
        assert col in cols, f"Column {col!r} missing from alarm_panels"


@pytest.mark.asyncio
async def test_alarm_zones_key_columns():
    """alarm_zones table has zone management columns."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'alarm_zones'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in ("id", "tenant_id", "panel_id", "zone_number",
                "name", "zone_type", "current_state", "is_active"):
        assert col in cols, f"Column {col!r} missing from alarm_zones"


# ─── B. Panel CRUD ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_panel_returns_api_key():
    """POST /alarms/panels returns the api_key (shown only once) and expected fields."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/alarms/panels", json={"name": "Gate Panel", "protocol": "webhook"})
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
    assert data["api_key"].startswith("pak_")
    assert data["arm_state"] == "disarmed"


@pytest.mark.asyncio
async def test_list_panels_omits_api_key():
    """GET /alarms/panels response rows must not contain api_key."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await _create_panel(c, name="SecretPanel")
        r = await c.get("/api/v1/alarms/panels")
    assert r.status_code == 200
    panels = r.json()
    assert len(panels) >= 1
    for panel in panels:
        assert "api_key" not in panel, "api_key must not appear in list response"


@pytest.mark.asyncio
async def test_get_panel_by_id_includes_zones_and_events():
    """GET /alarms/panels/{id} returns panel detail with zones list and recent_events list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c, name="Detail Panel")
        panel_id = panel["id"]
        await _create_zone(c, panel_id, zone_number=1, name="Zone Alpha")
        r = await c.get(f"/api/v1/alarms/panels/{panel_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Detail Panel"
    assert isinstance(data["zones"], list)
    assert isinstance(data["recent_events"], list)
    assert any(z["name"] == "Zone Alpha" for z in data["zones"])


@pytest.mark.asyncio
async def test_get_panel_404():
    """GET /alarms/panels/{id} with an unknown ID returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/alarms/panels/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_panel_name():
    """PUT /alarms/panels/{id} updates the panel name."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c, name="Old Name")
        r = await c.put(f"/api/v1/alarms/panels/{panel['id']}", json={"name": "New Name"})
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"


# ─── C. Arm / Disarm ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_arm_panel_away_sets_arm_state():
    """PUT /panels/{id}/arm with mode=away sets arm_state=armed_away."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c)
        r = await c.put(f"/api/v1/alarms/panels/{panel['id']}/arm", json={"mode": "away"})
    assert r.status_code == 200
    assert r.json()["arm_state"] == "armed_away"


@pytest.mark.asyncio
async def test_arm_panel_invalid_mode_422():
    """PUT /panels/{id}/arm with invalid mode returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c)
        r = await c.put(f"/api/v1/alarms/panels/{panel['id']}/arm", json={"mode": "full"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_disarm_panel_sets_disarmed():
    """PUT /panels/{id}/disarm sets arm_state=disarmed."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c)
        await c.put(f"/api/v1/alarms/panels/{panel['id']}/arm", json={"mode": "away"})
        r = await c.put(f"/api/v1/alarms/panels/{panel['id']}/disarm")
    assert r.status_code == 200
    assert r.json()["arm_state"] == "disarmed"


# ─── D. Zones ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_zone_valid_type():
    """POST /panels/{id}/zones with a valid zone_type returns zone fields."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c)
        r = await c.post(
            f"/api/v1/alarms/panels/{panel['id']}/zones",
            json={"zone_number": 1, "name": "Motion Sensor", "zone_type": "motion"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["zone_number"] == 1
    assert data["name"] == "Motion Sensor"
    assert data["zone_type"] == "motion"
    assert data["current_state"] == "normal"


@pytest.mark.asyncio
async def test_create_zone_invalid_type_422():
    """POST /panels/{id}/zones with an invalid zone_type returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c)
        r = await c.post(
            f"/api/v1/alarms/panels/{panel['id']}/zones",
            json={"zone_number": 1, "name": "Bad Zone", "zone_type": "laser_beam"},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_zone_duplicate_number_409():
    """POST /panels/{id}/zones with a duplicate zone_number returns 409 Conflict."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c)
        await _create_zone(c, panel["id"], zone_number=5, name="First Zone")
        r = await c.post(
            f"/api/v1/alarms/panels/{panel['id']}/zones",
            json={"zone_number": 5, "name": "Duplicate Zone", "zone_type": "door"},
        )
    assert r.status_code == 409


# ─── E. Zone Operations ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_zone_name():
    """PUT /zones/{zone_id} updates the zone name."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c)
        zone = await _create_zone(c, panel["id"], name="Old Zone Name")
        r = await c.put(f"/api/v1/alarms/zones/{zone['id']}", json={"name": "New Zone Name"})
    assert r.status_code == 200
    assert r.json()["name"] == "New Zone Name"


@pytest.mark.asyncio
async def test_bypass_zone_toggles_state():
    """PUT /zones/{zone_id}/bypass toggles current_state between normal and bypass."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c)
        zone = await _create_zone(c, panel["id"])
        zone_id = zone["id"]

        # First toggle: normal → bypass
        r1 = await c.put(f"/api/v1/alarms/zones/{zone_id}/bypass")
        assert r1.status_code == 200
        assert r1.json()["current_state"] == "bypass"

        # Second toggle: bypass → normal
        r2 = await c.put(f"/api/v1/alarms/zones/{zone_id}/bypass")
        assert r2.status_code == 200
        assert r2.json()["current_state"] == "normal"


# ─── F. Event Ingest ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_info_event_creates_event_row():
    """POST /events/ingest with an info-level event stores the event; no alert."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c)
    api_key = panel["api_key"]

    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/alarms/events/ingest",
            json={"event_type": "panel_disarmed"},
            headers={"X-Panel-Key": api_key},
        )
    assert r.status_code == 200
    data = r.json()
    assert "event_id" in data
    assert data["alert_id"] is None
    assert data["severity"] == "info"


@pytest.mark.asyncio
async def test_ingest_zone_alarm_creates_alert_and_incident():
    """POST /events/ingest with zone_alarm creates an alert row (high severity)."""
    tid, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tid)

    async with await _authed(token) as c:
        panel = await _create_panel(c)
        panel_id = panel["id"]
        api_key = panel["api_key"]
        # Zone linked to the seeded camera so alert can be created
        await c.post(
            f"/api/v1/alarms/panels/{panel_id}/zones",
            json={"zone_number": 1, "name": "Entrance", "zone_type": "door",
                  "linked_camera_id": str(cam_id)},
        )

    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/alarms/events/ingest",
            json={"event_type": "zone_alarm", "zone_number": 1,
                  "description": "Intrusion detected"},
            headers={"X-Panel-Key": api_key},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["severity"] == "high"
    assert data["alert_id"] is not None


@pytest.mark.asyncio
async def test_ingest_invalid_key_returns_401():
    """POST /events/ingest with an unrecognised X-Panel-Key returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/alarms/events/ingest",
            json={"event_type": "panel_disarmed"},
            headers={"X-Panel-Key": "pak_invalid_key_does_not_exist"},
        )
    assert r.status_code == 401


# ─── G. Events List ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_events_returns_items_and_has_more_keys():
    """GET /events returns {items: [...], has_more: bool} structure."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/alarms/events")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert "has_more" in data
    assert isinstance(data["items"], list)
    assert isinstance(data["has_more"], bool)


@pytest.mark.asyncio
async def test_list_events_filter_by_event_type():
    """GET /events?event_type=panel_disarmed returns only matching events."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c)
    api_key = panel["api_key"]

    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        await c.post(
            "/api/v1/alarms/events/ingest",
            json={"event_type": "panel_disarmed"},
            headers={"X-Panel-Key": api_key},
        )

    async with await _authed(token) as c:
        r = await c.get("/api/v1/alarms/events", params={"event_type": "panel_disarmed"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    assert all(item["event_type"] == "panel_disarmed" for item in items)


# ─── H. Panel Events + Key Rotation ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_panel_events_returns_events_for_panel():
    """GET /panels/{id}/events returns only events for that panel."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c)
        panel_id = panel["id"]
    api_key = panel["api_key"]

    # Ingest an event
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        await c.post(
            "/api/v1/alarms/events/ingest",
            json={"event_type": "test_signal"},
            headers={"X-Panel-Key": api_key},
        )

    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/alarms/panels/{panel_id}/events")
    assert r.status_code == 200
    events = r.json()
    assert isinstance(events, list)
    assert any(e["event_type"] == "test_signal" for e in events)


@pytest.mark.asyncio
async def test_rotate_key_returns_new_key():
    """POST /panels/{id}/rotate-key returns a new api_key with pak_ prefix."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        panel = await _create_panel(c)
        old_key = panel["api_key"]
        r = await c.post(f"/api/v1/alarms/panels/{panel['id']}/rotate-key")
    assert r.status_code == 200
    data = r.json()
    assert "api_key" in data
    new_key = data["api_key"]
    assert new_key.startswith("pak_")
    assert new_key != old_key
    assert "warning" in data


# ─── I. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_create_alarm_panel():
    """POST /alarms/panels requires alarm:manage — viewer (role 6) gets 403."""
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.post("/api/v1/alarms/panels", json={"name": "Test", "protocol": "webhook"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_returns_401():
    """GET /alarms/panels without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/alarms/panels")
    assert r.status_code == 401


# ─── J. RLS — Tenant Isolation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_cross_tenant_panels_not_visible():
    """Tenant B cannot see Tenant A's alarm panels in the list."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()

    async with await _authed(token_a) as c:
        panel_a = await _create_panel(c, name="Tenant A Panel")
    panel_a_id = panel_a["id"]

    async with await _authed(token_b) as c:
        r = await c.get("/api/v1/alarms/panels")
    assert r.status_code == 200
    ids = [str(p["id"]) for p in r.json()]
    assert panel_a_id not in ids


@pytest.mark.asyncio
async def test_rls_cross_tenant_get_panel_404():
    """Tenant B cannot retrieve Tenant A's panel by ID — returns 404."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()

    async with await _authed(token_a) as c:
        panel_a = await _create_panel(c)
    panel_a_id = panel_a["id"]

    async with await _authed(token_b) as c:
        r = await c.get(f"/api/v1/alarms/panels/{panel_a_id}")
    assert r.status_code == 404


# ─── K. Dashboard ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_returns_expected_structure():
    """GET /alarms/dashboard returns panel_summary, zone_summary, event_summary, recent_alarms, panels."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/alarms/dashboard")
    assert r.status_code == 200
    data = r.json()
    for key in ("panel_summary", "zone_summary", "event_summary", "recent_alarms", "panels"):
        assert key in data, f"Key {key!r} missing from dashboard response"
    assert "total_panels" in data["panel_summary"]
    assert "total_zones" in data["zone_summary"]
    assert "events_24h" in data["event_summary"]
