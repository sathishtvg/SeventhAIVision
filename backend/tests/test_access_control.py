"""Gap 50 — Access Control Router

Covers edge cases and untested paths in backend/app/routers/access_control.py.
The existing test_access_control_features.py (10 tests) covers door CRUD, invalid
door_type on create, credential CRUD, no-holder validation, rule create/list/delete,
event ingest (granted + forced + invalid type), events list, and dashboard basics.

This file adds isolated-tenant tests for update/delete edge cases, filter paths,
event ingest door-not-found + denied alert creation, dashboard completeness,
permissions, and RLS.

Endpoints (prefix /api/v1/access):
  GET    /doors                         (site_id / is_active filter)
  POST   /doors                         (door_type required)
  PUT    /doors/{id}                    (no valid fields → 422; invalid type → 422; not found → 404)
  DELETE /doors/{id}                    (not found → 404)
  GET    /credentials                   (is_active filter)
  POST   /credentials                   (invalid credential_type → 422)
  PUT    /credentials/{id}              (no valid fields → 422; invalid type → 422; not found → 404)
  DELETE /credentials/{id}              (not found → 404)
  GET    /rules                         (credential_id filter)
  POST   /rules
  DELETE /rules/{id}                    (not found → 404)
  POST   /doors/{id}/events             (door not found → 404; denied → high alert)
  GET    /events                        (door_id filter; event_type filter)
  GET    /dashboard                     (all 4 keys; event_summary sub-keys)

Sections:
  A — DB schema: access_doors + access_events columns
  B — Door update edge cases: no valid fields → 422; invalid door_type → 422
  C — Door delete: not found → 404
  D — Credential edge cases: create invalid type → 422; update no-valid-fields → 422;
      update invalid type → 422; update not found → 404; delete not found → 404
  E — Rules: credential_id filter; delete not found → 404
  F — Event ingest: door not found → 404; denied event creates high-severity alert
  G — Events list: door_id filter; event_type filter
  H — Dashboard: all 4 top-level keys + event_summary sub-keys
  I — Permissions: access:write requires admin; unauth → 401
  J — RLS: tenant A's doors invisible to tenant B
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
    slug = f"acc-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Acc Test {slug}", "slug": slug},
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
                "email": f"acc-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_camera(tenant_id: uuid.UUID) -> str:
    """Insert a minimal camera row for the tenant so alert queries can find a camera_id."""
    cam_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) "
                "VALUES (:id, :tid, 'AccessTestCam', '[]'::jsonb)"
            ),
            {"id": cam_id, "tid": tenant_id},
        )
        await s.commit()
    await engine.dispose()
    return str(cam_id)


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_door(client: AsyncClient, name: str = "TestDoor", **kwargs) -> str:
    body = {"name": name, "door_type": "card_reader", **kwargs}
    r = await client.post("/api/v1/access/doors", json=body)
    assert r.status_code == 201, f"create_door failed: {r.text}"
    return r.json()["id"]


async def _create_credential(client: AsyncClient, ref: str = "CRED-GAP50-001") -> str:
    r = await client.post("/api/v1/access/credentials", json={
        "holder_name": "Gap50 Holder",
        "credential_type": "card",
        "credential_ref": ref,
    })
    assert r.status_code == 201, f"create_credential failed: {r.text}"
    return r.json()["id"]


# ─── A. DB Schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_access_doors_table_expected_columns():
    """access_doors table has all columns the access control router depends on."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'access_doors'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "name", "location", "door_type",
        "site_id", "camera_id", "is_active", "created_at", "updated_at",
    ):
        assert col in cols, f"Column {col!r} missing from access_doors"


@pytest.mark.asyncio
async def test_access_events_table_expected_columns():
    """access_events table has all columns the access control router depends on."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'access_events'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "door_id", "credential_id",
        "event_type", "denial_reason", "occurred_at",
    ):
        assert col in cols, f"Column {col!r} missing from access_events"


# ─── B. Door update edge cases ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_door_update_no_valid_fields_returns_422():
    """PUT /access/doors/{id} with no recognised fields returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        door_id = await _create_door(c, "NoFieldsDoor")
        r = await c.put(f"/api/v1/access/doors/{door_id}", json={"ghost_field": "x"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_door_update_invalid_door_type_returns_422():
    """PUT /access/doors/{id} with invalid door_type returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        door_id = await _create_door(c, "BadTypeDoor")
        r = await c.put(
            f"/api/v1/access/doors/{door_id}",
            json={"door_type": "laser_gate"},
        )
    assert r.status_code == 422


# ─── C. Door delete not found ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_door_delete_not_found_returns_404():
    """DELETE /access/doors/{id} for an unknown door returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/access/doors/{uuid.uuid4()}")
    assert r.status_code == 404


# ─── D. Credential edge cases ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_credential_create_invalid_type_returns_422():
    """POST /access/credentials with invalid credential_type returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/access/credentials", json={
            "holder_name": "TypeTest Person",
            "credential_type": "retina_scan",
            "credential_ref": "RET-001",
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_credential_update_no_valid_fields_returns_422():
    """PUT /access/credentials/{id} with no recognised fields returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cred_id = await _create_credential(c, "CRED-NOFLD-001")
        r = await c.put(f"/api/v1/access/credentials/{cred_id}", json={"ghost": "x"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_credential_update_invalid_type_returns_422():
    """PUT /access/credentials/{id} with invalid credential_type returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cred_id = await _create_credential(c, "CRED-BADTYPE-001")
        r = await c.put(
            f"/api/v1/access/credentials/{cred_id}",
            json={"credential_type": "voice_print"},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_credential_update_not_found_returns_404():
    """PUT /access/credentials/{id} for unknown credential returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.put(
            f"/api/v1/access/credentials/{uuid.uuid4()}",
            json={"holder_name": "Nobody"},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_credential_delete_not_found_returns_404():
    """DELETE /access/credentials/{id} for unknown credential returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/access/credentials/{uuid.uuid4()}")
    assert r.status_code == 404


# ─── E. Rules ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rules_list_credential_id_filter():
    """GET /access/rules?credential_id=X returns only rules for that credential."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        door_id = await _create_door(c, "RuleFilterDoor")
        cred_a = await _create_credential(c, "CRED-RULEFILTER-A")
        cred_b = await _create_credential(c, "CRED-RULEFILTER-B")
        r_a = await c.post("/api/v1/access/rules", json={
            "credential_id": cred_a, "door_id": door_id, "schedule_days": "12345",
        })
        rule_a_id = r_a.json()["id"]
        await c.post("/api/v1/access/rules", json={
            "credential_id": cred_b, "door_id": door_id, "schedule_days": "67",
        })
        r = await c.get(f"/api/v1/access/rules?credential_id={cred_a}")
    assert r.status_code == 200
    rules = r.json()
    ids = [ru["id"] for ru in rules]
    assert rule_a_id in ids
    for ru in rules:
        assert ru["credential_id"] == cred_a, (
            f"Expected credential_id={cred_a!r}, got {ru['credential_id']!r}"
        )


@pytest.mark.asyncio
async def test_rule_delete_not_found_returns_404():
    """DELETE /access/rules/{id} for an unknown rule returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.delete(f"/api/v1/access/rules/{uuid.uuid4()}")
    assert r.status_code == 404


# ─── F. Event ingest edge cases ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_ingest_door_not_found_returns_404():
    """POST /access/doors/{id}/events for an unknown door returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(
            f"/api/v1/access/doors/{uuid.uuid4()}/events",
            json={"event_type": "granted"},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_event_ingest_denied_creates_high_severity_alert():
    """POST /doors/{id}/events with event_type=denied creates a high-severity alert."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    async with await _authed(token) as c:
        door_id = await _create_door(c, "DeniedAlertDoor", camera_id=cam_id)
        r = await c.post(
            f"/api/v1/access/doors/{door_id}/events",
            json={"event_type": "denied", "denial_reason": "Card not authorized"},
        )
    assert r.status_code == 201
    assert r.json()["event_type"] == "denied"
    # Verify alert row was created with correct severity
    engine = create_async_engine(ADMIN_DATABASE_URL)
    async with engine.connect() as conn:
        row = (await conn.execute(
            text(
                "SELECT severity FROM alerts "
                "WHERE tenant_id = :tid AND alert_code = 'access.denied' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"tid": str(tenant_id)},
        )).first()
    await engine.dispose()
    assert row is not None, "Expected access.denied alert to be created"
    assert row[0] == "high", f"Expected severity='high', got {row[0]!r}"


# ─── G. Events list filters ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_events_list_door_id_filter():
    """GET /access/events?door_id=X returns only events for that door."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        door_a = await _create_door(c, "FilterEvtDoor-A")
        door_b = await _create_door(c, "FilterEvtDoor-B")
        # Ingest one event on each door
        r_a = await c.post(
            f"/api/v1/access/doors/{door_a}/events",
            json={"event_type": "granted"},
        )
        assert r_a.status_code == 201
        await c.post(f"/api/v1/access/doors/{door_b}/events", json={"event_type": "granted"})
        r = await c.get(f"/api/v1/access/events?door_id={door_a}&hours=1")
    assert r.status_code == 200
    events = r.json()
    assert len(events) >= 1
    for ev in events:
        assert ev["door_id"] == door_a, (
            f"Expected door_id={door_a!r}, got {ev['door_id']!r}"
        )


@pytest.mark.asyncio
async def test_events_list_event_type_filter():
    """GET /access/events?event_type=granted returns only granted events."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        door_id = await _create_door(c, "FilterTypeDoor")
        await c.post(
            f"/api/v1/access/doors/{door_id}/events",
            json={"event_type": "granted"},
        )
        r = await c.get("/api/v1/access/events?event_type=granted&hours=1")
    assert r.status_code == 200
    for ev in r.json():
        assert ev["event_type"] == "granted"


# ─── H. Dashboard completeness ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_returns_all_4_keys_and_event_summary_subkeys():
    """GET /access/dashboard returns all 4 top-level keys and event_summary sub-keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/access/dashboard")
    assert r.status_code == 200
    data = r.json()
    for key in ("door_summary", "credential_summary", "event_summary", "recent_events"):
        assert key in data, f"Top-level key {key!r} missing from access dashboard"
    assert isinstance(data["recent_events"], list)
    es = data["event_summary"]
    for sub in ("events_today", "granted_today", "denied_today", "forced_today", "tamper_today"):
        assert sub in es, f"event_summary sub-key {sub!r} missing"


# ─── I. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_create_door():
    """POST /access/doors requires access:write — viewer (role 6) gets 403."""
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.post("/api/v1/access/doors", json={"name": "ViewerDoor"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_doors():
    """GET /access/doors without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/access/doors")
    assert r.status_code == 401


# ─── J. RLS — Tenant Isolation ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_doors_isolated_by_tenant():
    """GET /access/doors only returns doors belonging to the caller's tenant."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()

    async with await _authed(token_a) as c:
        door_id_a = await _create_door(c, "RLS-TenantA-Door")

    async with await _authed(token_b) as c:
        await _create_door(c, "RLS-TenantB-Door")
        r = await c.get("/api/v1/access/doors")

    ids = [d["id"] for d in r.json()]
    assert door_id_a not in ids, "Tenant B must not see Tenant A's doors"
