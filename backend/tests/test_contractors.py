"""Gap 49 — Contractor & Delivery Management Router

Covers edge cases and untested paths in backend/app/routers/contractors.py.
The existing test_contractor_features.py (9 tests) covers CRUD happy paths,
vetting approval, work permit full lifecycle, delivery lifecycle, dashboard
basics, and scheduler expiry jobs.  This file adds isolated-tenant tests for
validation errors, filters, accreditations, permit reject, delivery reject and
collect edge cases, permissions, and RLS.

Endpoints:
  GET  /api/v1/contractors                         (vetting_status filter)
  POST /api/v1/contractors                         (company_name required)
  GET  /api/v1/contractors/{id}                    (accreditations + recent_permits keys)
  PUT  /api/v1/contractors/{id}                    (no valid fields → 400)
  PUT  /api/v1/contractors/{id}/vet                (invalid status → 400)
  POST /api/v1/contractors/{id}/accreditations     (document_type required)
  GET  /api/v1/work-permits                        (contractor_id filter)
  POST /api/v1/work-permits                        (missing fields; contractor not found;
                                                    unapproved contractor → 422)
  PUT  /api/v1/work-permits/{id}/reject
  GET  /api/v1/deliveries                          (status filter)
  POST /api/v1/deliveries
  PUT  /api/v1/deliveries/{id}/collect             (collected_by_name required)
  PUT  /api/v1/deliveries/{id}/reject
  GET  /api/v1/contractors-dashboard

Sections:
  A — DB schema: contractors + work_permits columns
  B — Contractor validation: missing company_name → 400; update no-valid-fields → 400
  C — Contractor list vetting_status filter + GET includes accreditations/recent_permits keys
  D — Vet validation: invalid vetting_status → 400
  E — Accreditations: missing document_type → 400 + happy path
  F — Work permit validation: missing fields + contractor not found + unapproved → 422
  G — Work permit reject
  H — Work permit contractor_id filter
  I — Delivery edge cases: collect missing collected_by_name → 400 + reject
  J — Dashboard: all 7 keys present
  K — Permissions: contractor:write requires admin; unauth → 401
  L — RLS: tenant A's contractors invisible to tenant B
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
    slug = f"con-test-{tenant_id.hex[:8]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Con Test {slug}", "slug": slug},
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
                "email": f"con-{user_id.hex[:8]}@test.local",
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


async def _create_contractor(client: AsyncClient, name: str = "TestCo Pte Ltd") -> str:
    r = await client.post("/api/v1/contractors", json={"company_name": name})
    assert r.status_code == 200, f"create_contractor failed: {r.text}"
    return r.json()["id"]


async def _approve_contractor(client: AsyncClient, contractor_id: str) -> None:
    r = await client.put(
        f"/api/v1/contractors/{contractor_id}/vet",
        json={"vetting_status": "approved"},
    )
    assert r.status_code == 200, f"approve_contractor failed: {r.text}"


async def _create_approved_contractor(client: AsyncClient, name: str = "Approved Co") -> str:
    cid = await _create_contractor(client, name)
    await _approve_contractor(client, cid)
    return cid


async def _create_permit(client: AsyncClient, contractor_id: str) -> str:
    r = await client.post("/api/v1/work-permits", json={
        "contractor_id": contractor_id,
        "work_description": "Gap49 maintenance work",
        "start_at": "2026-08-01T08:00:00Z",
        "end_at": "2026-08-01T17:00:00Z",
    })
    assert r.status_code == 200, f"create_permit failed: {r.text}"
    return r.json()["id"]


async def _create_delivery(client: AsyncClient) -> str:
    r = await client.post("/api/v1/deliveries", json={
        "recipient_name": "Reception Desk",
        "description": "Gap49 test package",
    })
    assert r.status_code == 200, f"create_delivery failed: {r.text}"
    return r.json()["id"]


# ─── A. DB Schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_contractors_table_expected_columns():
    """contractors table has all columns the contractor router depends on."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'contractors'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "company_name", "registration_number",
        "contact_name", "contact_phone", "contact_email", "address",
        "specialization", "vetting_status", "vetting_notes",
        "vetted_by_user_id", "vetted_at", "is_active",
    ):
        assert col in cols, f"Column {col!r} missing from contractors"


@pytest.mark.asyncio
async def test_work_permits_table_expected_columns():
    """work_permits table has all columns the work permit router depends on."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'work_permits'"
            )
        )
        cols = {r[0] for r in result}
    await engine.dispose()
    for col in (
        "id", "tenant_id", "contractor_id", "site_id", "permit_number",
        "work_description", "work_type", "status", "start_at", "end_at",
        "safety_briefing_done", "approved_by_user_id", "approved_at",
        "rejection_reason",
    ):
        assert col in cols, f"Column {col!r} missing from work_permits"


# ─── B. Contractor validation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_contractor_create_missing_company_name_returns_400():
    """POST /contractors without company_name returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/contractors", json={"contact_name": "Alice"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_contractor_update_no_valid_fields_returns_400():
    """PUT /contractors/{id} with no recognised fields returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_contractor(c, "NoFields Co")
        r = await c.put(f"/api/v1/contractors/{cid}", json={"ghost_field": "x"})
    assert r.status_code == 400


# ─── C. Contractor list filter + GET includes keys ────────────────────────────

@pytest.mark.asyncio
async def test_contractor_list_vetting_status_filter():
    """GET /contractors?vetting_status=approved returns only approved contractors."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_contractor(c, "FilterApproved Co")
        await _approve_contractor(c, cid)
        # Also create a pending contractor so the filter is meaningful
        await _create_contractor(c, "FilterPending Co")
        r = await c.get("/api/v1/contractors?vetting_status=approved")
    assert r.status_code == 200
    assert len(r.json()) >= 1
    for contractor in r.json():
        assert contractor["vetting_status"] == "approved"


@pytest.mark.asyncio
async def test_contractor_get_includes_accreditations_and_permits_keys():
    """GET /contractors/{id} response includes accreditations and recent_permits keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_contractor(c, "KeysTest Co")
        r = await c.get(f"/api/v1/contractors/{cid}")
    assert r.status_code == 200
    data = r.json()
    assert "accreditations" in data, "accreditations key missing from GET contractor"
    assert "recent_permits" in data, "recent_permits key missing from GET contractor"
    assert isinstance(data["accreditations"], list)
    assert isinstance(data["recent_permits"], list)


# ─── D. Vet validation ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vet_invalid_status_returns_400():
    """PUT /contractors/{id}/vet with invalid vetting_status returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_contractor(c, "VetInvalid Co")
        r = await c.put(
            f"/api/v1/contractors/{cid}/vet",
            json={"vetting_status": "not_a_real_status"},
        )
    assert r.status_code == 400


# ─── E. Accreditations ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_accreditation_missing_document_type_returns_400():
    """POST /contractors/{id}/accreditations without document_type returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_contractor(c, "AccredMissing Co")
        r = await c.post(
            f"/api/v1/contractors/{cid}/accreditations",
            json={"document_number": "ACC-001"},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_accreditation_add_happy_path():
    """POST /contractors/{id}/accreditations with document_type creates accreditation."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_contractor(c, "AccredHappy Co")
        r = await c.post(
            f"/api/v1/contractors/{cid}/accreditations",
            json={
                "document_type": "BizSafe Star",
                "document_number": "BS-2026-001",
                "issued_by": "MOM Singapore",
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["document_type"] == "BizSafe Star"
    assert data["contractor_id"] == cid


# ─── F. Work permit validation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_work_permit_create_missing_contractor_id_returns_400():
    """POST /work-permits without contractor_id returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/work-permits", json={
            "work_description": "Test job",
            "start_at": "2026-08-01T08:00:00Z",
            "end_at": "2026-08-01T17:00:00Z",
        })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_work_permit_create_contractor_not_found_returns_404():
    """POST /work-permits with an unknown contractor_id returns 404."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/work-permits", json={
            "contractor_id": str(uuid.uuid4()),
            "work_description": "Test job",
            "start_at": "2026-08-01T08:00:00Z",
            "end_at": "2026-08-01T17:00:00Z",
        })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_work_permit_create_unapproved_contractor_returns_422():
    """POST /work-permits with a pending (unapproved) contractor returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        # Contractor left in default 'pending' vetting_status
        cid = await _create_contractor(c, "Unapproved Co")
        r = await c.post("/api/v1/work-permits", json={
            "contractor_id": cid,
            "work_description": "Cannot do this job",
            "start_at": "2026-08-01T08:00:00Z",
            "end_at": "2026-08-01T17:00:00Z",
        })
    assert r.status_code == 422


# ─── G. Work permit reject ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_work_permit_reject():
    """PUT /work-permits/{id}/reject sets the permit to rejected status."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid = await _create_approved_contractor(c, "RejectPermit Co")
        pid = await _create_permit(c, cid)
        r = await c.put(
            f"/api/v1/work-permits/{pid}/reject",
            json={"reason": "Site access not available"},
        )
    assert r.status_code == 200


# ─── H. Work permit contractor_id filter ─────────────────────────────────────

@pytest.mark.asyncio
async def test_work_permits_list_contractor_id_filter():
    """GET /work-permits?contractor_id=X returns only permits for that contractor."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid_a = await _create_approved_contractor(c, "FilterPerm A Co")
        cid_b = await _create_approved_contractor(c, "FilterPerm B Co")
        pid_a = await _create_permit(c, cid_a)
        await _create_permit(c, cid_b)
        r = await c.get(f"/api/v1/work-permits?contractor_id={cid_a}")
    assert r.status_code == 200
    permits = r.json()
    ids_returned = [p["id"] for p in permits]
    assert pid_a in ids_returned
    for p in permits:
        assert p["contractor_id"] == cid_a, (
            f"Expected contractor_id={cid_a!r}, got {p['contractor_id']!r}"
        )


# ─── I. Delivery edge cases ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delivery_collect_missing_collected_by_returns_400():
    """PUT /deliveries/{id}/collect without collected_by_name returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        did = await _create_delivery(c)
        r = await c.put(f"/api/v1/deliveries/{did}/collect", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delivery_reject():
    """PUT /deliveries/{id}/reject sets delivery to rejected status."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        did = await _create_delivery(c)
        r = await c.put(
            f"/api/v1/deliveries/{did}/reject",
            json={"reason": "Wrong recipient address"},
        )
    assert r.status_code == 200


# ─── J. Dashboard all 7 keys ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_returns_all_7_keys():
    """GET /contractors-dashboard returns all 7 expected summary keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/contractors-dashboard")
    assert r.status_code == 200
    data = r.json()
    for key in (
        "total_contractors", "pending_vetting", "approved_contractors",
        "pending_permits", "active_permits",
        "pending_deliveries", "received_deliveries",
    ):
        assert key in data, f"Key {key!r} missing from contractors dashboard"


# ─── K. Permissions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_create_contractor():
    """POST /contractors requires contractor:write — viewer (role 6) gets 403."""
    _, _, viewer_token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(viewer_token) as c:
        r = await c.post("/api/v1/contractors", json={"company_name": "ViewerTest Co"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_contractors():
    """GET /contractors without a JWT returns 401."""
    async with AsyncClient(transport=ASGITransport(_app()), base_url="http://test") as c:
        r = await c.get("/api/v1/contractors")
    assert r.status_code == 401


# ─── L. RLS — Tenant Isolation ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rls_contractors_isolated_by_tenant():
    """GET /contractors only returns contractors belonging to the caller's tenant."""
    _, _, token_a = await _seed_tenant_and_token()
    _, _, token_b = await _seed_tenant_and_token()

    async with await _authed(token_a) as c:
        cid_a = await _create_contractor(c, "RLS Tenant A Co")

    async with await _authed(token_b) as c:
        await _create_contractor(c, "RLS Tenant B Co")
        r = await c.get("/api/v1/contractors")

    ids = [con["id"] for con in r.json()]
    assert cid_a not in ids, "Tenant B must not see Tenant A's contractors"
