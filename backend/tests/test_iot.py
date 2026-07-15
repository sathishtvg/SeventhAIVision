"""Gap 47 — IoT Smart Facilities Monitoring Router

Covers edge cases and untested paths in backend/app/routers/iot.py.
The existing test_iot_features.py (6 shared-fixture tests) covers happy paths
for sensor CRUD, invalid type, reading ingest normal + breach, alerts list,
and dashboard basics.  This file adds isolated-tenant tests for validation
errors, threshold logic, alert operations, filters, permissions, and RLS.

Endpoints (prefix /api/v1/iot):
  GET  /sensors                    (sensor_type filter)
  POST /sensors                    (name/type validation)
  GET  /sensors/{id}               (404; readings key)
  PUT  /sensors/{id}               (no-valid-fields → 422)
  DELETE /sensors/{id}
  POST /sensors/{id}/readings      (unknown sensor 404; inactive 409; threshold logic)
  GET  /sensors/{id}/readings      (hours filter)
  GET  /alerts                     (sensor_id filter)
  PUT  /alerts/{id}/acknowledge    (ok; already-acked 404)
  PUT  /alerts/{id}/resolve        (ok)
  GET  /dashboard                  (all 4 keys + summary sub-keys)

Sections:
  A — DB schema: iot_sensors + iot_alerts columns
  B — Sensor validation: 404 / no-valid-fields 422 / GET includes readings key
  C — Sensor list sensor_type filter
  D — Reading ingest validation: unknown sensor 404 / inactive sensor 409
  E — Threshold alert logic: critical-high / critical-low / warning breach
  F — Reading ingest response fields present
  G — Readings list hours filter
  H — IoT alert sensor_id filter
  I — Alert acknowledge + resolve
  J — Dashboard: all 4 keys + summary sub-keys
  K — Permissions: iot:write requires admin; unauth → 401
  L — RLS: tenant A's sensors invisible to tenant B
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
    slug = f"iot-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"IoT Test {slug}", "slug": slug},
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
                "email": f"iot-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_sensor(client: AsyncClient, name: str = "Test Sensor",
                          sensor_type: str = "temperature", **kwargs) -> str:
    payload = {"name": name, "sensor_type": sensor_type, **kwargs}
    r = await client.post("/api/v1/iot/sensors", json=payload)
    assert r.status_code == 200, f"create_sensor failed: {r.text}"
    return r.json()["id"]


# ─── A. DB Schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_iot_sensors_table_expected_columns():
    """iot_sensors table has all columns the IoT router depends on."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'iot_sensors'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "site_id", "name", "sensor_type", "unit",
        "location", "description", "is_active", "current_status",
        "last_reading_at", "last_reading_value",
        "threshold_warning_low", "threshold_warning_high",
        "threshold_critical_low", "threshold_critical_high",
        "expected_interval_seconds",
    ):
        assert col in cols, f"Column {col!r} missing from iot_sensors"


@pytest.mark.asyncio
async def test_iot_alerts_table_expected_columns():
    """iot_alerts table has all columns the IoT router depends on."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'iot_alerts'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "sensor_id", "alert_type", "severity",
        "value", "message", "status", "created_at",
        "acknowledged_by_user_id", "acknowledged_at",
    ):
        assert col in cols, f"Column {col!r} missing from iot_alerts"


# ─── B. Sensor validation ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sensor_get_returns_404_for_unknown_id():
    """GET /iot/sensors/{id} with unknown id returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/iot/sensors/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_sensor_update_no_valid_fields_returns_422():
    """PUT /iot/sensors/{id} with no recognised fields returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        sid = await _create_sensor(c)
        r = await c.put(f"/api/v1/iot/sensors/{sid}", json={"nonexistent": "x"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_sensor_get_includes_readings_key():
    """GET /iot/sensors/{id} includes a readings list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        sid = await _create_sensor(c, name="Readings-Key Sensor")
        r = await c.get(f"/api/v1/iot/sensors/{sid}")
    assert r.status_code == 200
    assert "readings" in r.json()
    assert isinstance(r.json()["readings"], list)


# ─── C. Sensor list sensor_type filter ───────────────────────────────────────

@pytest.mark.asyncio
async def test_sensor_list_sensor_type_filter():
    """GET /iot/sensors?sensor_type=humidity returns only humidity sensors."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        await _create_sensor(c, name="Humidity One", sensor_type="humidity")
        await _create_sensor(c, name="Temperature One", sensor_type="temperature")
        r = await c.get("/api/v1/iot/sensors?sensor_type=humidity")
    assert r.status_code == 200
    for s in r.json():
        assert s["sensor_type"] == "humidity"


# ─── D. Reading ingest validation ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reading_ingest_unknown_sensor_returns_404():
    """POST /iot/sensors/{id}/readings for a non-existent sensor returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(
            f"/api/v1/iot/sensors/{uuid.uuid4()}/readings",
            json={"value": 50.0},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_reading_ingest_inactive_sensor_returns_409():
    """POST /iot/sensors/{id}/readings on a deactivated sensor returns 409."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        sid = await _create_sensor(c, name="Inactive Sensor")
        await c.delete(f"/api/v1/iot/sensors/{sid}")
        r = await c.post(f"/api/v1/iot/sensors/{sid}/readings", json={"value": 50.0})
    assert r.status_code == 409


# ─── E. Threshold alert logic ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reading_critical_high_threshold_breach():
    """Value exceeding critical_high → status=critical and alert_created=True."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        sid = await _create_sensor(c, name="Critical High Sensor",
                                    sensor_type="temperature",
                                    threshold_critical_high=80.0)
        r = await c.post(f"/api/v1/iot/sensors/{sid}/readings", json={"value": 99.0})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "critical"
    assert body["alert_created"] is True
    assert body["alert_severity"] == "critical"


@pytest.mark.asyncio
async def test_reading_critical_low_threshold_breach():
    """Value below critical_low → status=critical and alert_created=True."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        sid = await _create_sensor(c, name="Critical Low Sensor",
                                    sensor_type="water_tank",
                                    threshold_critical_low=10.0)
        r = await c.post(f"/api/v1/iot/sensors/{sid}/readings", json={"value": 5.0})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "critical"
    assert body["alert_created"] is True


@pytest.mark.asyncio
async def test_reading_warning_threshold_breach():
    """Value exceeding warning_high (but not critical) → status=warning, alert_severity=medium."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        sid = await _create_sensor(c, name="Warning Sensor",
                                    sensor_type="humidity",
                                    threshold_warning_high=75.0,
                                    threshold_critical_high=90.0)
        r = await c.post(f"/api/v1/iot/sensors/{sid}/readings", json={"value": 80.0})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "warning"
    assert body["alert_severity"] == "medium"
    assert body["alert_created"] is True


# ─── F. Reading ingest response fields ────────────────────────────────────────

@pytest.mark.asyncio
async def test_reading_ingest_response_has_required_fields():
    """POST /sensors/{id}/readings response includes id, status, alert_created, alert_severity."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        sid = await _create_sensor(c, name="Response Fields Sensor")
        r = await c.post(f"/api/v1/iot/sensors/{sid}/readings", json={"value": 42.0})
    assert r.status_code == 200
    body = r.json()
    for key in ("id", "status", "alert_created", "alert_severity"):
        assert key in body, f"Key {key!r} missing from reading ingest response"


# ─── G. Readings list hours filter ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_readings_list_returns_list():
    """GET /iot/sensors/{id}/readings returns a list after ingesting a value."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        sid = await _create_sensor(c, name="Readings List Sensor")
        await c.post(f"/api/v1/iot/sensors/{sid}/readings", json={"value": 30.0})
        r = await c.get(f"/api/v1/iot/sensors/{sid}/readings?hours=1")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


# ─── H. IoT alert sensor_id filter ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_iot_alerts_sensor_id_filter():
    """GET /iot/alerts?sensor_id=X returns only alerts for that sensor."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        sid = await _create_sensor(c, name="Alert Filter Sensor",
                                    sensor_type="temperature",
                                    threshold_critical_high=50.0)
        await c.post(f"/api/v1/iot/sensors/{sid}/readings", json={"value": 99.0})
        r = await c.get(f"/api/v1/iot/alerts?sensor_id={sid}")
    assert r.status_code == 200
    alerts = r.json()
    assert len(alerts) >= 1
    for a in alerts:
        assert a["sensor_id"] == sid


# ─── I. Alert acknowledge + resolve ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_iot_alert_acknowledge():
    """PUT /iot/alerts/{id}/acknowledge changes status from open to acknowledged."""
    _, _, token = await _seed_tenant_and_token()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with await _authed(token) as c:
        sid = await _create_sensor(c, name="Ack Alert Sensor",
                                    sensor_type="temperature",
                                    threshold_critical_high=50.0)
        await c.post(f"/api/v1/iot/sensors/{sid}/readings", json={"value": 99.0})

        # Get the open alert id
        alerts_r = await c.get(f"/api/v1/iot/alerts?sensor_id={sid}")
        alert_id = alerts_r.json()[0]["id"]

        r = await c.put(f"/api/v1/iot/alerts/{alert_id}/acknowledge")
    await engine.dispose()
    assert r.status_code == 200
    assert r.json().get("ok") is True


@pytest.mark.asyncio
async def test_iot_alert_resolve():
    """PUT /iot/alerts/{id}/resolve changes status to resolved."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        sid = await _create_sensor(c, name="Resolve Alert Sensor",
                                    sensor_type="temperature",
                                    threshold_critical_high=50.0)
        await c.post(f"/api/v1/iot/sensors/{sid}/readings", json={"value": 99.0})
        alerts_r = await c.get(f"/api/v1/iot/alerts?sensor_id={sid}")
        alert_id = alerts_r.json()[0]["id"]
        r = await c.put(f"/api/v1/iot/alerts/{alert_id}/resolve")
    assert r.status_code == 200
    assert r.json().get("ok") is True


# ─── J. Dashboard keys ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_returns_all_top_level_keys():
    """GET /iot/dashboard returns total_sensors, active_sensors, summary, sensors."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/iot/dashboard")
    assert r.status_code == 200
    data = r.json()
    for key in ("total_sensors", "active_sensors", "summary", "sensors"):
        assert key in data, f"Key {key!r} missing from IoT dashboard"


@pytest.mark.asyncio
async def test_dashboard_summary_has_all_status_keys():
    """GET /iot/dashboard summary includes total/normal/warning/critical/offline/open_alerts."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/iot/dashboard")
    assert r.status_code == 200
    summary = r.json()["summary"]
    for key in ("total", "normal", "warning", "critical", "offline", "open_alerts"):
        assert key in summary, f"Key {key!r} missing from IoT dashboard summary"


# ─── K. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_create_sensor():
    """POST /iot/sensors requires iot:write — viewer (role 6) gets 403."""
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.post("/api/v1/iot/sensors",
                          json={"name": "Viewer Sensor", "sensor_type": "temperature"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_sensors():
    """GET /iot/sensors without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/iot/sensors")
    assert r.status_code == 401


# ─── L. RLS — Tenant Isolation ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_sensors_isolated_by_tenant():
    """GET /iot/sensors only returns sensors belonging to the caller's tenant."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()

    async with await _authed(token_a) as c:
        sid_a = await _create_sensor(c, name="Tenant A Sensor")

    async with await _authed(token_b) as c:
        await _create_sensor(c, name="Tenant B Sensor")
        r = await c.get("/api/v1/iot/sensors")

    ids = [s["id"] for s in r.json()]
    assert sid_a not in ids, "Tenant B must not see Tenant A's sensors"
