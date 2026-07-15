"""P5-R: PDF Report generation — integration tests for reports.py.

Three endpoints under /api/v1/reports:
- GET /site-summary?site_id=...&date_from=...&date_until=...
  Returns application/pdf; 404 if site not found
- GET /dob?date_from=...&date_until=...&site_id=...
  Returns application/pdf; empty table section if no DOB entries
- GET /incident/{incident_id}
  Returns application/pdf; 404 if incident not found

All endpoints call _check_reportlab() as their first action — if reportlab is not
installed in the container, every endpoint returns 503. Auth (Depends(get_token_payload))
is resolved by FastAPI before the function body, so 401 is always reliable regardless
of reportlab availability.

Tests assert:
- 401 without a valid token (always reliable)
- 200 + application/pdf headers, OR 503 when reportlab is absent (resilient)
- 404 for missing resources, OR 503 when reportlab is absent (resilient)
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_site(client: AsyncClient, name: str = "Report Site") -> str:
    r = await client.post("/api/v1/sites", json={"name": name, "address": "1 Test St"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_camera(client: AsyncClient, name: str = "Report Cam") -> str:
    r = await client.post("/api/v1/cameras", json={"name": name, "location": "HQ"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_incident(client: AsyncClient, camera_id: str, title: str = "Report Test Incident") -> str:
    r = await client.post("/api/v1/incidents", json={
        "camera_id": camera_id,
        "title": title,
        "description": "Created for report testing",
        "severity": "high",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _is_pdf(r) -> bool:
    return "application/pdf" in r.headers.get("content-type", "")


def _has_attachment(r) -> bool:
    return "attachment" in r.headers.get("content-disposition", "")


# ── DOB Report ────────────────────────────────────────────────────────────────
# Simplest endpoint — no required DB resources, just a date range.
# When no occurrence_book entries exist the PDF still renders with "No entries found".

@pytest.mark.asyncio
async def test_dob_report_returns_pdf_or_503(auth_client: AsyncClient):
    r = await auth_client.get(
        "/api/v1/reports/dob?date_from=2024-01-01&date_until=2030-12-31"
    )
    assert r.status_code in (200, 503), r.text


@pytest.mark.asyncio
async def test_dob_report_has_pdf_content_type_when_available(auth_client: AsyncClient):
    r = await auth_client.get(
        "/api/v1/reports/dob?date_from=2024-01-01&date_until=2030-12-31"
    )
    if r.status_code == 503:
        pytest.skip("reportlab not installed in this container")
    assert r.status_code == 200
    assert _is_pdf(r), f"Expected application/pdf, got: {r.headers.get('content-type')}"


@pytest.mark.asyncio
async def test_dob_report_has_attachment_header_when_available(auth_client: AsyncClient):
    r = await auth_client.get(
        "/api/v1/reports/dob?date_from=2024-01-01&date_until=2030-12-31"
    )
    if r.status_code == 503:
        pytest.skip("reportlab not installed in this container")
    assert r.status_code == 200
    assert _has_attachment(r), f"Missing attachment disposition: {r.headers.get('content-disposition')}"


@pytest.mark.asyncio
async def test_dob_report_pdf_has_content(auth_client: AsyncClient):
    r = await auth_client.get(
        "/api/v1/reports/dob?date_from=2024-01-01&date_until=2030-12-31"
    )
    if r.status_code == 503:
        pytest.skip("reportlab not installed in this container")
    assert r.status_code == 200
    assert len(r.content) > 100, "PDF body too small — likely empty/corrupt"


@pytest.mark.asyncio
async def test_dob_report_optional_site_filter(auth_client: AsyncClient):
    r = await auth_client.get(
        "/api/v1/reports/dob"
        "?date_from=2024-01-01&date_until=2030-12-31"
        "&site_id=00000000-0000-0000-0000-000000000001"
    )
    # Unknown site just means empty result — not a 404 for DOB (unlike site-summary)
    assert r.status_code in (200, 503), r.text


@pytest.mark.asyncio
async def test_dob_report_requires_auth(client: AsyncClient):
    r = await client.get(
        "/api/v1/reports/dob?date_from=2024-01-01&date_until=2030-12-31"
    )
    assert r.status_code == 401


# ── Site Summary Report ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_site_summary_unknown_site_404_or_503(auth_client: AsyncClient):
    r = await auth_client.get(
        "/api/v1/reports/site-summary"
        "?site_id=00000000-0000-0000-0000-000000000099"
        "&date_from=2024-01-01&date_until=2030-12-31"
    )
    # 503 if reportlab not installed (checked before DB); 404 if installed and site missing
    assert r.status_code in (404, 503), r.text


@pytest.mark.asyncio
async def test_site_summary_with_valid_site_returns_pdf(auth_client: AsyncClient):
    site_id = await _make_site(auth_client, "SummarySite")

    r = await auth_client.get(
        f"/api/v1/reports/site-summary"
        f"?site_id={site_id}&date_from=2024-01-01&date_until=2030-12-31"
    )
    if r.status_code == 503:
        pytest.skip("reportlab not installed in this container")
    assert r.status_code == 200
    assert _is_pdf(r), f"Expected application/pdf, got: {r.headers.get('content-type')}"
    assert _has_attachment(r)
    assert len(r.content) > 100


@pytest.mark.asyncio
async def test_site_summary_filename_contains_site_name(auth_client: AsyncClient):
    site_id = await _make_site(auth_client, "NamedSite")

    r = await auth_client.get(
        f"/api/v1/reports/site-summary"
        f"?site_id={site_id}&date_from=2024-01-01&date_until=2030-12-31"
    )
    if r.status_code == 503:
        pytest.skip("reportlab not installed in this container")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "NamedSite" in cd or "named" in cd.lower()


@pytest.mark.asyncio
async def test_site_summary_requires_auth(client: AsyncClient):
    r = await client.get(
        "/api/v1/reports/site-summary"
        "?site_id=00000000-0000-0000-0000-000000000001"
        "&date_from=2024-01-01&date_until=2030-12-31"
    )
    assert r.status_code == 401


# ── Incident Detail Report ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_incident_report_unknown_incident_404_or_503(auth_client: AsyncClient):
    r = await auth_client.get(
        "/api/v1/reports/incident/00000000-0000-0000-0000-000000000099"
    )
    assert r.status_code in (404, 503), r.text


@pytest.mark.asyncio
async def test_incident_report_with_valid_incident_returns_pdf(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "IncReportCam")
    incident_id = await _make_incident(auth_client, cam)

    r = await auth_client.get(f"/api/v1/reports/incident/{incident_id}")
    if r.status_code == 503:
        pytest.skip("reportlab not installed in this container")
    assert r.status_code == 200
    assert _is_pdf(r), f"Expected application/pdf, got: {r.headers.get('content-type')}"
    assert _has_attachment(r)
    assert len(r.content) > 100


@pytest.mark.asyncio
async def test_incident_report_filename_contains_incident_id(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "IncReportFileCam")
    incident_id = await _make_incident(auth_client, cam)

    r = await auth_client.get(f"/api/v1/reports/incident/{incident_id}")
    if r.status_code == 503:
        pytest.skip("reportlab not installed in this container")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    # filename is "incident_{first8chars}.pdf"
    assert incident_id[:8] in cd


@pytest.mark.asyncio
async def test_incident_report_requires_auth(client: AsyncClient):
    r = await client.get(
        "/api/v1/reports/incident/00000000-0000-0000-0000-000000000001"
    )
    assert r.status_code == 401
