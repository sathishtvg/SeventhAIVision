"""P5-L (part 2): Analytics — aggregated metrics for the reporting dashboard.

Tests cover:

GET /analytics/summary:
- Returns all 8 expected numeric fields
- open_alerts reflects newly created alerts
- ?site_id= filter with non-existent site returns zeros

GET /analytics/alerts/by-severity:
- Returns list of {severity, count} items
- Accepts ?days= parameter; days > 365 capped (no error)
- Reflects newly created alerts by severity

GET /analytics/alerts/by-module:
- Returns list of {module_type, count} items
- Reflects newly created alerts by module_type

GET /analytics/detections/trend:
- Returns list of {day, count} items
- Accepts ?days= parameter

GET /analytics/alerts/trend:
- Returns list of {day, count} items
- Today's count reflects alerts created during the session

GET /analytics/top-cameras:
- Returns list of {camera_id, camera_name, alert_count} items
- ?limit= caps the result size (server-enforced)

GET /analytics/heatmap:
- Returns per-camera rows with all spatial and count fields
- ?module_type= filter: no 500 error
- ?site_id= filter: no 500 error
- ?hours= validated: ge=1, le=720 → out-of-range → 422

GET /analytics/incidents/resolution-time:
- Returns {resolved_count, avg_hours, p95_hours}
- Works when no resolved incidents exist (returns resolved_count=0)

Auth:
- All endpoints require auth (401)
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_camera(client: AsyncClient, name: str = "Analytics Cam") -> str:
    r = await client.post("/api/v1/cameras", json={"name": name, "location": "HQ"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_alert(
    client: AsyncClient,
    camera_id: str,
    severity: str = "medium",
    module_type: str = "intrusion",
) -> dict:
    r = await client.post("/api/v1/alerts", json={
        "camera_id": camera_id,
        "module_type": module_type,
        "severity": severity,
        "title": f"Analytics test alert ({severity}/{module_type})",
        "message": "Analytics test",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


# ── GET /analytics/summary ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summary_returns_expected_fields(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/summary")
    assert r.status_code == 200
    body = r.json()
    for field in (
        "open_alerts", "open_incidents", "active_cameras",
        "detections_today", "alerts_today", "alerts_7d",
        "detections_7d", "active_recordings",
    ):
        assert field in body, f"Missing field: {field}"
        assert isinstance(body[field], int), f"Field {field} should be int"
        assert body[field] >= 0


@pytest.mark.asyncio
async def test_summary_open_alerts_reflects_created_alert(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "SummaryOpenAlertCam")
    await _make_alert(auth_client, cam, severity="high")

    r = await auth_client.get("/api/v1/analytics/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["open_alerts"] >= 1
    assert body["active_cameras"] >= 1
    assert body["alerts_today"] >= 1
    assert body["alerts_7d"] >= 1


@pytest.mark.asyncio
async def test_summary_with_nonexistent_site_id_returns_zeros(auth_client: AsyncClient):
    nil_site = "00000000-0000-0000-0000-000000000000"
    r = await auth_client.get(f"/api/v1/analytics/summary?site_id={nil_site}")
    assert r.status_code == 200
    body = r.json()
    assert body["open_alerts"] == 0
    assert body["open_incidents"] == 0
    assert body["active_cameras"] == 0
    assert body["detections_today"] == 0


@pytest.mark.asyncio
async def test_summary_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/analytics/summary")
    assert r.status_code == 401


# ── GET /analytics/alerts/by-severity ────────────────────────────────────────

@pytest.mark.asyncio
async def test_alerts_by_severity_returns_list(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/alerts/by-severity")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_alerts_by_severity_shape(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "SeverityShapeCam")
    await _make_alert(auth_client, cam, severity="critical")

    r = await auth_client.get("/api/v1/analytics/alerts/by-severity")
    assert r.status_code == 200
    body = r.json()
    assert len(body) >= 1
    for row in body:
        assert "severity" in row
        assert "count" in row
        assert isinstance(row["count"], int)
        assert row["count"] >= 1


@pytest.mark.asyncio
async def test_alerts_by_severity_custom_days(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/alerts/by-severity?days=7")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_alerts_by_severity_days_cap_no_error(auth_client: AsyncClient):
    # days clamped to 365 internally; should not error
    r = await auth_client.get("/api/v1/analytics/alerts/by-severity?days=400")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_alerts_by_severity_reflects_critical_alert(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "CritSeverityReflectCam")
    await _make_alert(auth_client, cam, severity="critical")

    r = await auth_client.get("/api/v1/analytics/alerts/by-severity")
    rows = {row["severity"]: row["count"] for row in r.json()}
    assert "critical" in rows
    assert rows["critical"] >= 1


@pytest.mark.asyncio
async def test_alerts_by_severity_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/analytics/alerts/by-severity")
    assert r.status_code == 401


# ── GET /analytics/alerts/by-module ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_alerts_by_module_returns_list(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/alerts/by-module")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_alerts_by_module_shape(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "ModuleShapeCam")
    await _make_alert(auth_client, cam, module_type="lpr")

    r = await auth_client.get("/api/v1/analytics/alerts/by-module")
    assert r.status_code == 200
    body = r.json()
    assert len(body) >= 1
    for row in body:
        assert "module_type" in row
        assert "count" in row
        assert isinstance(row["count"], int)


@pytest.mark.asyncio
async def test_alerts_by_module_reflects_face_alert(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "FaceModuleReflectCam")
    await _make_alert(auth_client, cam, module_type="face")

    r = await auth_client.get("/api/v1/analytics/alerts/by-module")
    modules = {row["module_type"]: row["count"] for row in r.json()}
    assert "face" in modules
    assert modules["face"] >= 1


@pytest.mark.asyncio
async def test_alerts_by_module_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/analytics/alerts/by-module")
    assert r.status_code == 401


# ── GET /analytics/detections/trend ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_detections_trend_returns_list(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/detections/trend")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_detections_trend_shape(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/detections/trend")
    body = r.json()
    for row in body:
        assert "day" in row
        assert "count" in row
        assert isinstance(row["count"], int)
        assert row["count"] >= 0


@pytest.mark.asyncio
async def test_detections_trend_custom_days(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/detections/trend?days=14")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_detections_trend_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/analytics/detections/trend")
    assert r.status_code == 401


# ── GET /analytics/alerts/trend ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alerts_trend_returns_list(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/alerts/trend")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_alerts_trend_shape(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/alerts/trend")
    for row in r.json():
        assert "day" in row
        assert "count" in row
        assert isinstance(row["count"], int)


@pytest.mark.asyncio
async def test_alerts_trend_reflects_todays_alerts(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "TrendReflectCam")
    await _make_alert(auth_client, cam)

    # days=1 covers today only
    r = await auth_client.get("/api/v1/analytics/alerts/trend?days=1")
    assert r.status_code == 200
    body = r.json()
    assert len(body) >= 1
    total = sum(row["count"] for row in body)
    assert total >= 1


@pytest.mark.asyncio
async def test_alerts_trend_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/analytics/alerts/trend")
    assert r.status_code == 401


# ── GET /analytics/top-cameras ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_top_cameras_returns_list(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/top-cameras")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_top_cameras_shape(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "TopCamShape")
    await _make_alert(auth_client, cam)

    r = await auth_client.get("/api/v1/analytics/top-cameras")
    assert r.status_code == 200
    body = r.json()
    assert len(body) >= 1
    for row in body:
        assert "camera_id" in row
        assert "camera_name" in row
        assert "alert_count" in row
        assert isinstance(row["alert_count"], int)
        assert row["alert_count"] >= 0


@pytest.mark.asyncio
async def test_top_cameras_limit_caps_results(auth_client: AsyncClient):
    for i in range(3):
        cam = await _make_camera(auth_client, f"LimitTestCam{i}")
        await _make_alert(auth_client, cam)

    r = await auth_client.get("/api/v1/analytics/top-cameras?limit=2")
    assert r.status_code == 200
    assert len(r.json()) <= 2


@pytest.mark.asyncio
async def test_top_cameras_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/analytics/top-cameras")
    assert r.status_code == 401


# ── GET /analytics/heatmap ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_heatmap_returns_camera_fields(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "HeatmapFieldsCam")

    r = await auth_client.get("/api/v1/analytics/heatmap")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)

    # Our camera should appear (active, no site filter)
    ids = [row["camera_id"] for row in body]
    assert cam in ids

    row = next(row for row in body if row["camera_id"] == cam)
    for field in (
        "camera_id", "camera_name", "location",
        "total_detections", "total_alerts", "open_alerts",
        "critical_alerts", "high_alerts", "stream_status",
    ):
        assert field in row, f"Missing heatmap field: {field}"


@pytest.mark.asyncio
async def test_heatmap_counts_are_non_negative(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/heatmap")
    assert r.status_code == 200
    for row in r.json():
        assert row["total_detections"] >= 0
        assert row["total_alerts"] >= 0
        assert row["open_alerts"] >= 0
        assert row["critical_alerts"] >= 0
        assert row["high_alerts"] >= 0


@pytest.mark.asyncio
async def test_heatmap_with_module_type_filter(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/heatmap?module_type=intrusion")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_heatmap_with_site_id_filter(auth_client: AsyncClient):
    nil_site = "00000000-0000-0000-0000-000000000000"
    r = await auth_client.get(f"/api/v1/analytics/heatmap?site_id={nil_site}")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_heatmap_hours_param(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/heatmap?hours=1")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_heatmap_hours_max_valid(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/heatmap?hours=720")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_heatmap_hours_zero_is_invalid(auth_client: AsyncClient):
    # Query param ge=1 → hours=0 → 422
    r = await auth_client.get("/api/v1/analytics/heatmap?hours=0")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_heatmap_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/analytics/heatmap")
    assert r.status_code == 401


# ── GET /analytics/incidents/resolution-time ──────────────────────────────────

@pytest.mark.asyncio
async def test_resolution_time_returns_expected_fields(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/incidents/resolution-time")
    assert r.status_code == 200
    body = r.json()
    for field in ("resolved_count", "avg_hours", "p95_hours"):
        assert field in body, f"Missing field: {field}"
    assert isinstance(body["resolved_count"], int)
    assert body["resolved_count"] >= 0


@pytest.mark.asyncio
async def test_resolution_time_no_resolved_incidents_count_zero(auth_client: AsyncClient):
    # Use a 0-day window so no incidents qualify → resolved_count = 0
    r = await auth_client.get("/api/v1/analytics/incidents/resolution-time?days=0")
    assert r.status_code == 200
    body = r.json()
    assert body["resolved_count"] == 0
    # avg_hours and p95_hours may be None when there are no resolved incidents
    assert body["avg_hours"] is None or isinstance(body["avg_hours"], (int, float))
    assert body["p95_hours"] is None or isinstance(body["p95_hours"], (int, float))


@pytest.mark.asyncio
async def test_resolution_time_custom_days(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/analytics/incidents/resolution-time?days=7")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_resolution_time_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/analytics/incidents/resolution-time")
    assert r.status_code == 401
