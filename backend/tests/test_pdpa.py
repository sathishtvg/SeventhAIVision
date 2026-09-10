"""Gap 60 — PDPA / Privacy Masking Router

Isolated-tenant tests for backend/app/routers/pdpa.py (319 lines).
Two routers registered in main.py:
  router      — prefix /api/v1/privacy (privacy masking zones)
  pdpa_router — prefix /api/v1/pdpa   (PDPA consent management + DSAR)

Endpoints:
  GET    /api/v1/privacy/zones                — privacy:manage; opt camera_id filter; JOIN camera_name
  POST   /api/v1/privacy/zones                — privacy:manage; camera_id FK required
  DELETE /api/v1/privacy/zones/{id}           — privacy:manage; hard delete; 404 if not found
  GET    /api/v1/privacy/zones/camera/{cam}   — NO auth; SECURITY DEFINER fn (AI workers)
  GET    /api/v1/pdpa/consents                — pdpa:read; filters: site_id, consent_type, consented
  POST   /api/v1/pdpa/consents                — pdpa:admin; returns {id, consent_type, consented,...}
  GET    /api/v1/pdpa/dsar                    — pdpa:read; dsar_status filter; is_overdue computed
  POST   /api/v1/pdpa/dsar                    — pdpa:admin; 422 on invalid request_type; returns {id,...}
  PUT    /api/v1/pdpa/dsar/{id}               — pdpa:admin; 422 invalid status; 404 if not found

Sections:
  A — Privacy Zones (7 tests)
  B — PDPA Consents (5 tests)
  C — DSAR (5 tests)
  D — Permissions + RLS (3 tests)
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
    slug = f"pdpa-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"PDPA Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'PDPA Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"pdpa-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera(tenant_id: uuid.UUID) -> str:
    """Seed a camera row via the admin engine (bypasses RLS)."""
    cam_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO cameras (id, tenant_id, name, location) "
                "VALUES (:id, :tid, :name, :loc)"
            ),
            {"id": cam_id, "tid": tenant_id, "name": "Test Cam", "loc": "Entrance"},
        )
        await s.commit()
    await engine.dispose()
    return str(cam_id)


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


_POLYGON = [{"x": 0.1, "y": 0.2}, {"x": 0.5, "y": 0.2}, {"x": 0.5, "y": 0.8}, {"x": 0.1, "y": 0.8}]


async def _create_zone(c: AsyncClient, cam_id: str, name: str = "Test Zone") -> str:
    r = await c.post("/api/v1/privacy/zones", json={
        "camera_id": cam_id, "name": name,
        "polygon": _POLYGON, "fill_color": "#000000",
    })
    assert r.status_code == 200, f"create_zone failed: {r.text}"
    return r.json()["id"]


# ─── A. Privacy Zones ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_privacy_zones_empty_fresh_tenant():
    """GET /privacy/zones on a fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/privacy/zones")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_privacy_zone_returns_200():
    """POST /privacy/zones returns 200 + {id, camera_id, name, polygon, fill_color, is_active}."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/privacy/zones", json={
            "camera_id": cam_id, "name": "Entrance Privacy",
            "polygon": _POLYGON, "fill_color": "#FF0000",
        })
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["fill_color"] == "#FF0000"
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_create_privacy_zone_appears_in_list():
    """After POST /privacy/zones, the zone is visible via GET /privacy/zones."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        zone_id = await _create_zone(c, cam_id, name="Appears In List")
        r = await c.get("/api/v1/privacy/zones")
    ids = [item["id"] for item in r.json()]
    assert zone_id in ids


@pytest.mark.asyncio
async def test_list_privacy_zones_camera_filter():
    """GET /privacy/zones?camera_id=X returns only zones for that camera."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_a = await _seed_camera(tenant_id)
    cam_b = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        zone_a = await _create_zone(c, cam_a, name="Zone for A")
        await _create_zone(c, cam_b, name="Zone for B")
        r = await c.get(f"/api/v1/privacy/zones?camera_id={cam_a}")
    items = r.json()
    ids = [item["id"] for item in items]
    assert zone_a in ids
    assert all(item["camera_id"] == cam_a for item in items)


@pytest.mark.asyncio
async def test_delete_privacy_zone_returns_deleted_true():
    """DELETE /privacy/zones/{id} returns {deleted: True}."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        zone_id = await _create_zone(c, cam_id)
        r = await c.delete(f"/api/v1/privacy/zones/{zone_id}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True


@pytest.mark.asyncio
async def test_delete_privacy_zone_unknown_id_returns_404():
    """DELETE /privacy/zones/{random_uuid} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/privacy/zones/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_camera_privacy_zones_public_no_auth():
    """GET /privacy/zones/camera/{id} requires no JWT — returns active zones."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        zone_id = await _create_zone(c, cam_id, name="Public Zone")
    # Call without any auth header
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get(f"/api/v1/privacy/zones/camera/{cam_id}")
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()]
    assert zone_id in ids


# ─── B. PDPA Consents ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_consents_empty_fresh_tenant():
    """GET /pdpa/consents on a fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/pdpa/consents")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_consent_returns_200():
    """POST /pdpa/consents returns 200 + {id, consent_type, consented, valid_from, valid_until}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/pdpa/consents", json={
            "data_subject_name": "John Doe",
            "consent_type": "face_recognition",
            "consented": True,
            "consent_method": "digital_form",
        })
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["consent_type"] == "face_recognition"
    assert body["consented"] is True


@pytest.mark.asyncio
async def test_create_consent_appears_in_list():
    """After POST /pdpa/consents, the consent is visible via GET /pdpa/consents."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/pdpa/consents", json={
            "data_subject_name": "Jane Smith",
            "consent_type": "lpr_surveillance",
            "consented": True,
        })
        consent_id = r.json()["id"]
        r2 = await c.get("/api/v1/pdpa/consents")
    ids = [item["id"] for item in r2.json()]
    assert consent_id in ids


@pytest.mark.asyncio
async def test_list_consents_consent_type_filter():
    """GET /pdpa/consents?consent_type=X returns only matching consents."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r1 = await c.post("/api/v1/pdpa/consents",
                          json={"data_subject_name": "A", "consent_type": "face_recognition", "consented": True})
        r2 = await c.post("/api/v1/pdpa/consents",
                          json={"data_subject_name": "B", "consent_type": "lpr_surveillance", "consented": True})
        face_id = r1.json()["id"]
        r = await c.get("/api/v1/pdpa/consents?consent_type=face_recognition")
    items = r.json()
    ids = [item["id"] for item in items]
    assert face_id in ids
    assert all(item["consent_type"] == "face_recognition" for item in items)


@pytest.mark.asyncio
async def test_list_consents_consented_false_filter():
    """GET /pdpa/consents?consented=false returns only withdrawn consents."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r1 = await c.post("/api/v1/pdpa/consents",
                          json={"data_subject_name": "Opted Out", "consent_type": "face_recognition", "consented": False})
        withdrawn_id = r1.json()["id"]
        await c.post("/api/v1/pdpa/consents",
                     json={"data_subject_name": "Opted In", "consent_type": "face_recognition", "consented": True})
        r = await c.get("/api/v1/pdpa/consents?consented=false")
    items = r.json()
    ids = [item["id"] for item in items]
    assert withdrawn_id in ids
    assert all(item["consented"] is False for item in items)


# ─── C. DSAR ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_dsar_empty_fresh_tenant():
    """GET /pdpa/dsar on a fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/pdpa/dsar")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_dsar_returns_200():
    """POST /pdpa/dsar returns 200 + {id, request_type, status, deadline_at}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/pdpa/dsar", json={
            "request_type": "access",
            "data_subject_name": "Alice Wong",
            "data_subject_email": "alice@example.com",
        })
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["request_type"] == "access"
    assert "status" in body
    assert "deadline_at" in body


@pytest.mark.asyncio
async def test_create_dsar_invalid_type_returns_422():
    """POST /pdpa/dsar with an invalid request_type returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/pdpa/dsar", json={
            "request_type": "deletion",  # invalid — should be 'erasure'
            "data_subject_name": "Bob Lee",
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_dsar_status():
    """PUT /pdpa/dsar/{id} updates the status to in_review."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/pdpa/dsar", json={
            "request_type": "erasure",
            "data_subject_name": "Charlie Brown",
        })
        dsar_id = create_r.json()["id"]
        r = await c.put(f"/api/v1/pdpa/dsar/{dsar_id}",
                        json={"status": "in_review", "fulfillment_notes": "Under investigation"})
    assert r.status_code == 200
    assert r.json()["status"] == "in_review"


@pytest.mark.asyncio
async def test_update_dsar_unknown_id_returns_404():
    """PUT /pdpa/dsar/{random_uuid} returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put(f"/api/v1/pdpa/dsar/{uuid.uuid4()}",
                        json={"status": "fulfilled"})
    assert r.status_code == 404


# ─── D. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_create_privacy_zone_403():
    """Viewer (role 6) cannot create privacy zones — privacy:manage not granted."""
    tenant_id, _, token = await _seed_tenant_and_token(role_id=6)
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/privacy/zones", json={
            "camera_id": cam_id, "name": "Forbidden", "polygon": _POLYGON,
        })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_create_consent_403():
    """Viewer (role 6) cannot create PDPA consents — pdpa:admin not granted."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/pdpa/consents", json={
            "data_subject_name": "X", "consent_type": "face_recognition", "consented": True,
        })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_privacy_zones_rls_isolation():
    """Tenant B cannot see Tenant A's privacy zones."""
    tid_a, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    cam_a = await _seed_camera(tid_a)
    async with await _authed(tok_a) as c:
        zone_id = await _create_zone(c, cam_a, name="Tenant A Zone")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/privacy/zones")
    ids = [item["id"] for item in r.json()]
    assert zone_id not in ids
