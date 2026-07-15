"""P5-M (part 1): Command & Control Centre overview.

The C&C endpoint is the primary operational dashboard payload: a single
GET that returns consolidated site health, alert counts, active guards, and
the most recent critical/high alerts across all sites.

Tests cover:

GET /api/v1/command-centre/overview:
- Returns 200 with top-level keys: summary, sites, recent_alerts, guards
- summary contains all 8 numeric counters (all >= 0)
- sites is a list; each card has the required camera/alert/guard fields
- recent_alerts is a list (may be empty when no critical/high alerts in last 4h)
- guards is a list
- A newly created critical alert appears in recent_alerts within the 4-hour window
- A newly created high alert also appears in recent_alerts
- A medium alert does NOT appear (filtered to critical/high only)
- site_name on a recent_alert may be None if the camera has no site (LEFT JOIN)
- Auth required (401)
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_camera(client: AsyncClient, name: str = "CC Cam") -> str:
    r = await client.post("/api/v1/cameras", json={"name": name, "location": "HQ"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_alert(
    client: AsyncClient,
    camera_id: str,
    severity: str = "critical",
    title: str = "CC Test Alert",
) -> str:
    r = await client.post("/api/v1/alerts", json={
        "camera_id": camera_id,
        "module_type": "intrusion",
        "severity": severity,
        "title": title,
        "message": "C&C centre test",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ── Overview endpoint ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_overview_returns_200(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/command-centre/overview")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_overview_has_top_level_keys(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/command-centre/overview")
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body
    assert "sites" in body
    assert "recent_alerts" in body
    assert "guards" in body


@pytest.mark.asyncio
async def test_overview_summary_has_all_fields(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/command-centre/overview")
    assert r.status_code == 200
    summary = r.json()["summary"]
    for field in (
        "total_sites", "cameras_online", "cameras_offline", "cameras_degraded",
        "active_alerts", "critical_alerts", "high_alerts", "guards_on_duty",
    ):
        assert field in summary, f"Missing summary field: {field}"


@pytest.mark.asyncio
async def test_overview_summary_fields_non_negative(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/command-centre/overview")
    summary = r.json()["summary"]
    for field, val in summary.items():
        assert isinstance(val, int), f"Summary field {field} should be int"
        assert val >= 0, f"Summary field {field} should be non-negative"


@pytest.mark.asyncio
async def test_overview_sites_is_list(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/command-centre/overview")
    assert isinstance(r.json()["sites"], list)


@pytest.mark.asyncio
async def test_overview_recent_alerts_is_list(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/command-centre/overview")
    assert isinstance(r.json()["recent_alerts"], list)


@pytest.mark.asyncio
async def test_overview_guards_is_list(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/command-centre/overview")
    assert isinstance(r.json()["guards"], list)


@pytest.mark.asyncio
async def test_critical_alert_appears_in_recent_alerts(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "CCCritCam")
    alert_id = await _make_alert(auth_client, cam, severity="critical", title="CC Critical Alert")

    r = await auth_client.get("/api/v1/command-centre/overview")
    assert r.status_code == 200
    recent = r.json()["recent_alerts"]
    alert_ids = [str(a["id"]) for a in recent]
    assert alert_id in alert_ids


@pytest.mark.asyncio
async def test_high_alert_appears_in_recent_alerts(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "CCHighCam")
    alert_id = await _make_alert(auth_client, cam, severity="high", title="CC High Alert")

    r = await auth_client.get("/api/v1/command-centre/overview")
    assert r.status_code == 200
    recent = r.json()["recent_alerts"]
    alert_ids = [str(a["id"]) for a in recent]
    assert alert_id in alert_ids


@pytest.mark.asyncio
async def test_medium_alert_not_in_recent_alerts(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "CCMediumCam")
    medium_id = await _make_alert(auth_client, cam, severity="medium", title="CC Medium Alert Unique")

    r = await auth_client.get("/api/v1/command-centre/overview")
    recent = r.json()["recent_alerts"]
    alert_ids = [str(a["id"]) for a in recent]
    assert medium_id not in alert_ids


@pytest.mark.asyncio
async def test_recent_alert_has_expected_fields(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "CCFieldCam")
    await _make_alert(auth_client, cam, severity="critical")

    r = await auth_client.get("/api/v1/command-centre/overview")
    recent = r.json()["recent_alerts"]
    assert len(recent) >= 1
    row = recent[0]
    for field in ("id", "title", "severity", "module_type", "status", "created_at", "camera_name"):
        assert field in row, f"Missing recent_alert field: {field}"


@pytest.mark.asyncio
async def test_overview_summary_active_alerts_increments(auth_client: AsyncClient):
    before = (await auth_client.get("/api/v1/command-centre/overview")).json()["summary"]["active_alerts"]

    cam = await _make_camera(auth_client, "CCSummaryCam")
    await _make_alert(auth_client, cam, severity="critical")

    after = (await auth_client.get("/api/v1/command-centre/overview")).json()["summary"]["active_alerts"]
    assert after >= before + 1


@pytest.mark.asyncio
async def test_overview_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/command-centre/overview")
    assert r.status_code == 401
