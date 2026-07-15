"""Gap 52 — Reports Router

First dedicated isolated-tenant test file for backend/app/routers/reports.py
(494 lines, previously only 14 shared-fixture tests covering a fraction of paths).

Endpoints (prefix /api/v1/reports):
  GET /site-summary         — PDF of alerts+incidents for a site over a date range
  GET /dob                  — PDF of occurrence-book entries (optional site_id filter)
  GET /incident/{id}        — PDF of a single incident with notes

No require_permission dependency — any authenticated user can generate reports.
Responses are StreamingResponse with media_type='application/pdf'.

Sections:
  A — reportlab availability in container
  B — Site summary: 200 + PDF magic bytes, Content-Disposition filename,
      site not found → 404, cross-tenant site → 404 (RLS)
  C — DOB report: 200 + PDF, no-entries period still returns PDF (not 404),
      optional site_id filter accepted, Content-Disposition filename
  D — Incident report: 200 + PDF, unknown → 404,
      cross-tenant incident → 404 (RLS), Content-Disposition filename
  E — Pure functions: _severity_color known/unknown, _check_reportlab no-raise
  F — Auth: unauthenticated → 401 on each endpoint
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
    slug = f"rpt-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Rpt Test {slug}", "slug": slug},
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
                "email": f"rpt-{user_id.hex[:8]}@test.local",
                "pw": hash_password("test-pass"),
            },
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _seed_site(tenant_id: uuid.UUID) -> str:
    """Insert a site row; return its UUID string."""
    site_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO sites (id, tenant_id, name, address) "
                "VALUES (:id, :tid, :name, :addr)"
            ),
            {
                "id": site_id,
                "tid": tenant_id,
                "name": f"Site {site_id.hex[:6]}",
                "addr": "123 Test St",
            },
        )
        await s.commit()
    await engine.dispose()
    return str(site_id)


async def _seed_camera(tenant_id: uuid.UUID, site_id: str | None = None) -> str:
    """Insert a camera row; return its UUID string."""
    cam_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO cameras (id, tenant_id, name, site_id) "
                "VALUES (:id, :tid, :name, CAST(:sid AS uuid))"
            ),
            {"id": cam_id, "tid": tenant_id, "name": f"Cam {cam_id.hex[:6]}", "sid": site_id},
        )
        await s.commit()
    await engine.dispose()
    return str(cam_id)


async def _seed_incident(tenant_id: uuid.UUID, cam_id: str) -> str:
    """Insert a minimal incident row; return its UUID string."""
    inc_id = uuid.uuid4()
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, is_auto_created) "
                "VALUES (:id, :tid, CAST(:cid AS uuid), :title, 'high', 'open', FALSE)"
            ),
            {"id": inc_id, "tid": tenant_id, "cid": cam_id, "title": "Test Report Incident"},
        )
        await s.commit()
    await engine.dispose()
    return str(inc_id)


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── A. reportlab availability ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reportlab_is_available_in_container():
    """reportlab must be importable — endpoints return 503 otherwise."""
    try:
        import reportlab  # noqa: F401
        available = True
    except ImportError:
        available = False
    assert available, "reportlab is not installed; rebuild the container with reportlab>=4.2"


# ─── B. Site summary report ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_site_summary_returns_200():
    """GET /reports/site-summary with a valid site returns 200."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        r = await c.get(
            "/api/v1/reports/site-summary",
            params={"site_id": site_id, "date_from": "2020-01-01", "date_until": "2030-12-31"},
        )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_site_summary_response_is_pdf():
    """GET /reports/site-summary returns application/pdf with %PDF magic bytes."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        r = await c.get(
            "/api/v1/reports/site-summary",
            params={"site_id": site_id, "date_from": "2020-01-01", "date_until": "2030-12-31"},
        )
    assert r.status_code == 200
    assert "application/pdf" in r.headers.get("content-type", "")
    assert r.content[:4] == b"%PDF", "Response body must start with PDF magic bytes"


@pytest.mark.asyncio
async def test_site_summary_content_disposition_has_filename():
    """GET /reports/site-summary Content-Disposition attachment contains a .pdf filename."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        r = await c.get(
            "/api/v1/reports/site-summary",
            params={"site_id": site_id, "date_from": "2020-01-01", "date_until": "2030-12-31"},
        )
    assert r.status_code == 200
    disp = r.headers.get("content-disposition", "")
    assert "attachment" in disp
    assert ".pdf" in disp


@pytest.mark.asyncio
async def test_site_summary_unknown_site_returns_404():
    """GET /reports/site-summary with an unknown site_id returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(
            "/api/v1/reports/site-summary",
            params={"site_id": str(uuid.uuid4()), "date_from": "2020-01-01", "date_until": "2030-12-31"},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_site_summary_cross_tenant_site_returns_404():
    """GET /reports/site-summary with a site from another tenant returns 404 (RLS)."""
    _, _, token_a = await _seed_tenant_and_token()
    tenant_b_id, _, _ = await _seed_tenant_and_token()
    site_b_id = await _seed_site(tenant_b_id)
    async with await _authed(token_a) as c:
        r = await c.get(
            "/api/v1/reports/site-summary",
            params={"site_id": site_b_id, "date_from": "2020-01-01", "date_until": "2030-12-31"},
        )
    assert r.status_code == 404


# ─── C. DOB report ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dob_report_returns_200():
    """GET /reports/dob returns 200 for a valid date range."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(
            "/api/v1/reports/dob",
            params={"date_from": "2020-01-01", "date_until": "2030-12-31"},
        )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_dob_report_response_is_pdf():
    """GET /reports/dob response is application/pdf with PDF magic bytes."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(
            "/api/v1/reports/dob",
            params={"date_from": "2020-01-01", "date_until": "2030-12-31"},
        )
    assert r.status_code == 200
    assert "application/pdf" in r.headers.get("content-type", "")
    assert r.content[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_dob_report_empty_period_still_returns_pdf():
    """GET /reports/dob with no entries in range still returns 200 PDF (not 404/500)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(
            "/api/v1/reports/dob",
            params={"date_from": "2000-01-01", "date_until": "2000-01-02"},
        )
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF", "Empty-period DOB must still produce a valid PDF"


@pytest.mark.asyncio
async def test_dob_report_with_site_filter_accepted():
    """GET /reports/dob with optional site_id param returns 200 (no 422/500)."""
    tenant_id, _, token = await _seed_tenant_and_token()
    site_id = await _seed_site(tenant_id)
    async with await _authed(token) as c:
        r = await c.get(
            "/api/v1/reports/dob",
            params={"date_from": "2020-01-01", "date_until": "2030-12-31", "site_id": site_id},
        )
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_dob_report_content_disposition_filename():
    """GET /reports/dob Content-Disposition contains 'daily_occurrence_book.pdf'."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(
            "/api/v1/reports/dob",
            params={"date_from": "2020-01-01", "date_until": "2030-12-31"},
        )
    assert r.status_code == 200
    disp = r.headers.get("content-disposition", "")
    assert "daily_occurrence_book.pdf" in disp


# ─── D. Incident report ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_incident_report_returns_200():
    """GET /reports/incident/{id} for a valid incident returns 200."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    inc_id = await _seed_incident(tenant_id, cam_id)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/reports/incident/{inc_id}")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_incident_report_response_is_pdf():
    """GET /reports/incident/{id} returns application/pdf with PDF magic bytes."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    inc_id = await _seed_incident(tenant_id, cam_id)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/reports/incident/{inc_id}")
    assert r.status_code == 200
    assert "application/pdf" in r.headers.get("content-type", "")
    assert r.content[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_incident_report_unknown_returns_404():
    """GET /reports/incident/{id} with an unknown ID returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/reports/incident/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_incident_report_cross_tenant_returns_404():
    """GET /reports/incident/{id} for an incident in another tenant returns 404 (RLS)."""
    _, _, token_a = await _seed_tenant_and_token()
    tenant_b_id, _, _ = await _seed_tenant_and_token()
    cam_b_id = await _seed_camera(tenant_b_id)
    inc_b_id = await _seed_incident(tenant_b_id, cam_b_id)
    async with await _authed(token_a) as c:
        r = await c.get(f"/api/v1/reports/incident/{inc_b_id}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_incident_report_content_disposition_has_filename():
    """GET /reports/incident/{id} Content-Disposition contains a .pdf filename."""
    tenant_id, _, token = await _seed_tenant_and_token()
    cam_id = await _seed_camera(tenant_id)
    inc_id = await _seed_incident(tenant_id, cam_id)
    async with await _authed(token) as c:
        r = await c.get(f"/api/v1/reports/incident/{inc_id}")
    assert r.status_code == 200
    disp = r.headers.get("content-disposition", "")
    assert "attachment" in disp
    assert ".pdf" in disp
    assert inc_id[:8] in disp, "Filename must contain the first 8 chars of the incident ID"


# ─── E. Pure functions ────────────────────────────────────────────────────────

def test_severity_color_known_severities():
    """_severity_color maps all known severity levels to correct hex colors."""
    from app.routers.reports import _severity_color
    assert _severity_color("critical") == "#FF4560"
    assert _severity_color("high") == "#FF7F50"
    assert _severity_color("medium") == "#FFA500"
    assert _severity_color("low") == "#00E396"
    assert _severity_color("info") == "#6C63FF"


def test_severity_color_unknown_returns_gray():
    """_severity_color returns '#888888' for unrecognised severity strings."""
    from app.routers.reports import _severity_color
    assert _severity_color("unknown") == "#888888"
    assert _severity_color("") == "#888888"


def test_check_reportlab_does_not_raise_when_installed():
    """_check_reportlab() must not raise when reportlab is available."""
    from app.routers.reports import _check_reportlab, REPORTLAB_AVAILABLE
    if not REPORTLAB_AVAILABLE:
        pytest.skip("reportlab not installed")
    _check_reportlab()  # should not raise


# ─── F. Auth — unauthenticated → 401 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_unauthenticated_cannot_generate_site_summary():
    """GET /reports/site-summary without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get(
            "/api/v1/reports/site-summary",
            params={"site_id": str(uuid.uuid4()), "date_from": "2020-01-01", "date_until": "2030-12-31"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_cannot_generate_dob_report():
    """GET /reports/dob without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get(
            "/api/v1/reports/dob",
            params={"date_from": "2020-01-01", "date_until": "2030-12-31"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_cannot_generate_incident_report():
    """GET /reports/incident/{id} without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get(f"/api/v1/reports/incident/{uuid.uuid4()}")
    assert r.status_code == 401
