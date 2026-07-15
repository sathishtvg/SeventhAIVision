"""P5-Q: CSV Export — real HTTP integration tests.

The existing test_exports.py only unit-tests the _make_csv/_csv_response pure
functions against a mocked DB. These tests make real HTTP calls and verify that
each endpoint actually responds correctly against the live test database.

Four endpoints under /api/v1/export:
- GET /alerts        — requires alert:read
- GET /incidents     — requires incident:read
- GET /detections    — requires detection:read
- GET /audit-logs    — requires audit:read

All return text/csv with Content-Disposition: attachment.
Filters (date_from, date_to, severity, status, module_type, etc.) are query params.
"""
import csv
import io
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_camera(client: AsyncClient, name: str = "Export Cam") -> str:
    r = await client.post("/api/v1/cameras", json={"name": name, "location": "HQ"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_alert(
    client: AsyncClient, camera_id: str, *, severity: str = "critical"
) -> str:
    r = await client.post("/api/v1/alerts", json={
        "camera_id": camera_id,
        "module_type": "intrusion",
        "severity": severity,
        "title": "Export Test Alert",
        "message": "Created for export testing",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _make_incident(client: AsyncClient, camera_id: str) -> str:
    r = await client.post("/api/v1/incidents", json={
        "camera_id": camera_id,
        "title": "Export Test Incident",
        "description": "Created for export testing",
        "severity": "high",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _csv_rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def _csv_fieldnames(text: str) -> list[str]:
    reader = csv.DictReader(io.StringIO(text))
    _ = next(reader, None)  # advance to read fieldnames
    return list(reader.fieldnames or [])


# ── Export Alerts ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_alerts_returns_200_csv(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/alerts")
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_export_alerts_has_attachment_content_disposition(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/alerts")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".csv" in cd


@pytest.mark.asyncio
async def test_export_alerts_has_expected_columns(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/alerts")
    assert r.status_code == 200
    reader = csv.DictReader(io.StringIO(r.text))
    # Read at least the header by peeking
    fieldnames = reader.fieldnames or []
    for col in ("id", "camera_name", "module_type", "severity", "title", "status", "created_at"):
        assert col in fieldnames, f"Missing column in alerts CSV: {col}"


@pytest.mark.asyncio
async def test_export_alerts_includes_created_alert(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "AlertExportCam")
    alert_id = await _make_alert(auth_client, cam, severity="critical")

    r = await auth_client.get("/api/v1/export/alerts")
    assert r.status_code == 200
    ids = [row["id"] for row in _csv_rows(r.text)]
    assert alert_id in ids


@pytest.mark.asyncio
async def test_export_alerts_filter_by_severity_includes_match(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "SevFilterCam")
    critical_id = await _make_alert(auth_client, cam, severity="critical")

    r = await auth_client.get("/api/v1/export/alerts?severity=critical")
    assert r.status_code == 200
    ids = [row["id"] for row in _csv_rows(r.text)]
    assert critical_id in ids


@pytest.mark.asyncio
async def test_export_alerts_filter_by_severity_excludes_nonmatch(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "SevExcludeCam")
    low_id = await _make_alert(auth_client, cam, severity="low")

    r = await auth_client.get("/api/v1/export/alerts?severity=critical")
    assert r.status_code == 200
    ids = [row["id"] for row in _csv_rows(r.text)]
    assert low_id not in ids


@pytest.mark.asyncio
async def test_export_alerts_filter_by_status(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "StatusExportCam")
    alert_id = await _make_alert(auth_client, cam)

    r = await auth_client.get("/api/v1/export/alerts?status=open")
    assert r.status_code == 200
    ids = [row["id"] for row in _csv_rows(r.text)]
    assert alert_id in ids


@pytest.mark.asyncio
async def test_export_alerts_filter_by_module_type(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "ModuleExportCam")
    alert_id = await _make_alert(auth_client, cam)  # module_type="intrusion"

    r = await auth_client.get("/api/v1/export/alerts?module_type=intrusion")
    assert r.status_code == 200
    ids = [row["id"] for row in _csv_rows(r.text)]
    assert alert_id in ids


@pytest.mark.asyncio
async def test_export_alerts_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/export/alerts")
    assert r.status_code == 401


# ── Export Incidents ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_incidents_returns_200_csv(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/incidents")
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_export_incidents_has_attachment_header(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/incidents")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".csv" in cd


@pytest.mark.asyncio
async def test_export_incidents_has_expected_columns(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/incidents")
    assert r.status_code == 200
    reader = csv.DictReader(io.StringIO(r.text))
    fieldnames = reader.fieldnames or []
    for col in ("id", "camera_name", "title", "severity", "status", "created_at"):
        assert col in fieldnames, f"Missing column in incidents CSV: {col}"


@pytest.mark.asyncio
async def test_export_incidents_includes_created_incident(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "IncExportCam")
    incident_id = await _make_incident(auth_client, cam)

    r = await auth_client.get("/api/v1/export/incidents")
    assert r.status_code == 200
    ids = [row["id"] for row in _csv_rows(r.text)]
    assert incident_id in ids


@pytest.mark.asyncio
async def test_export_incidents_filter_by_severity(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "IncSevCam")
    incident_id = await _make_incident(auth_client, cam)   # severity=high

    r = await auth_client.get("/api/v1/export/incidents?severity=high")
    assert r.status_code == 200
    ids = [row["id"] for row in _csv_rows(r.text)]
    assert incident_id in ids


@pytest.mark.asyncio
async def test_export_incidents_filter_by_status(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "IncStatusCam")
    incident_id = await _make_incident(auth_client, cam)

    r = await auth_client.get("/api/v1/export/incidents?status=open")
    assert r.status_code == 200
    ids = [row["id"] for row in _csv_rows(r.text)]
    assert incident_id in ids


@pytest.mark.asyncio
async def test_export_incidents_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/export/incidents")
    assert r.status_code == 401


# ── Export Detections ─────────────────────────────────────────────────────────
#
# Detections are written by AI workers — no direct creation API in tests.
# These tests verify the endpoint works and returns well-formed CSV even when
# the detections table is empty (AI workers haven't run against the test DB).

@pytest.mark.asyncio
async def test_export_detections_returns_200_csv(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/detections")
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_export_detections_has_attachment_header(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/detections")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".csv" in cd


@pytest.mark.asyncio
async def test_export_detections_has_expected_columns(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/detections")
    assert r.status_code == 200
    reader = csv.DictReader(io.StringIO(r.text))
    fieldnames = reader.fieldnames or []
    for col in ("id", "camera_name", "module_type", "confidence", "detected_at"):
        assert col in fieldnames, f"Missing column in detections CSV: {col}"


@pytest.mark.asyncio
async def test_export_detections_filter_by_module_type_only_returns_matches(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/detections?module_type=intrusion")
    assert r.status_code == 200
    rows = _csv_rows(r.text)
    for row in rows:
        assert row["module_type"] == "intrusion", f"Filter leaked wrong module_type: {row['module_type']}"


@pytest.mark.asyncio
async def test_export_detections_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/export/detections")
    assert r.status_code == 401


# ── Export Audit Logs ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_audit_logs_returns_200_csv(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/audit-logs")
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_export_audit_logs_has_attachment_header(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/audit-logs")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".csv" in cd


@pytest.mark.asyncio
async def test_export_audit_logs_has_expected_columns(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/audit-logs")
    assert r.status_code == 200
    reader = csv.DictReader(io.StringIO(r.text))
    fieldnames = reader.fieldnames or []
    for col in ("id", "user_email", "action", "resource_type", "ip_address", "created_at"):
        assert col in fieldnames, f"Missing column in audit-logs CSV: {col}"


@pytest.mark.asyncio
async def test_export_audit_logs_filter_by_resource_type_only_returns_matches(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/export/audit-logs?resource_type=camera")
    assert r.status_code == 200
    rows = _csv_rows(r.text)
    for row in rows:
        assert row["resource_type"] == "camera", f"Filter leaked wrong resource_type: {row['resource_type']}"


@pytest.mark.asyncio
async def test_export_audit_logs_filter_by_action_ilike(auth_client: AsyncClient):
    # Create a camera to generate an audit log entry (action contains "camera")
    await _make_camera(auth_client, "AuditFilterCam")

    r = await auth_client.get("/api/v1/export/audit-logs?action=camera")
    assert r.status_code == 200
    rows = _csv_rows(r.text)
    # If rows returned, they should all have "camera" in action (ILIKE %camera%)
    for row in rows:
        assert "camera" in row["action"].lower(), f"ILIKE filter leaked: {row['action']}"


@pytest.mark.asyncio
async def test_export_audit_logs_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/export/audit-logs")
    assert r.status_code == 401
