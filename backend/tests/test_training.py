"""Gap 58 — Training & Certification Router

Isolated-tenant tests for backend/app/routers/training.py (410 lines).
Three domain tables: training_courses, training_records, guard_certifications.

Endpoints (prefix /api/v1/training):
  GET  /courses                  — training:read; filters: category, is_active
  POST /courses                  — training:manage; 422 invalid category/score; 201
  PUT  /courses/{id}             — training:manage; 422 no-fields/invalid-cat; 404 not found
  GET  /records                  — training:read; filters: user_id, course_id, passed; JOIN users+courses
  POST /records                  — training:write; 404 if course not found; auto-computes expires_at
  DELETE /records/{id}           — training:write; hard delete; 404 not found
  GET  /certifications           — training:read; filters: user_id, is_valid, expiring_days
  POST /certifications           — training:write; 201 with {id}
  PUT  /certifications/{id}      — training:write; 422 no-fields; 404 not found
  POST /certifications/{id}/revoke — training:write; sets is_valid=FALSE; 404
  DELETE /certifications/{id}    — training:write; same as revoke (soft); 404
  GET  /dashboard                — training:read; 8 top-level keys

Permissions (typical assignments):
  training:read   — roles 1-6 (all authenticated)
  training:manage — roles 1-3 (super_admin, admin, supervisor)
  training:write  — roles 1-4 (super_admin, admin, supervisor, operator)

Sections:
  A — Courses (7 tests)
  B — Records (5 tests)
  C — Certifications (6 tests)
  D — Dashboard (2 tests)
  E — Permissions + RLS (3 tests)
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
    """Create isolated tenant + user; return (tenant_id, user_id, jwt_token)."""
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"trn-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Training Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, :tid, :role, :email, 'hashed', :fname)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"trn-{user_id.hex[:8]}@test.local",
             "fname": "Test Guard"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_course(c: AsyncClient, name: str = "First Aid",
                         category: str = "first_aid",
                         validity_months: int | None = None) -> str:
    body = {"name": name, "category": category, "passing_score": 70}
    if validity_months is not None:
        body["validity_months"] = validity_months
    r = await c.post("/api/v1/training/courses", json=body)
    assert r.status_code == 201, f"create_course failed: {r.text}"
    return r.json()["id"]


# ─── A. Courses ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_courses_empty_fresh_tenant():
    """GET /training/courses on a fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/training/courses")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_course_returns_201():
    """POST /training/courses returns 201 + {id}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/training/courses", json={
            "name": "Security Basics", "category": "security", "passing_score": 75,
        })
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_create_course_appears_in_list():
    """After POST /courses, the course is visible via GET /courses."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        course_id = await _create_course(c, name="Visible Course", category="general")
        r = await c.get("/api/v1/training/courses")
    ids = [item["id"] for item in r.json()]
    assert course_id in ids


@pytest.mark.asyncio
async def test_create_course_invalid_category_returns_422():
    """POST /courses with an invalid category returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/training/courses", json={
            "name": "Bad Cat", "category": "not_a_real_category",
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_course_passing_score_out_of_range_returns_422():
    """POST /courses with passing_score > 100 returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/training/courses", json={
            "name": "Bad Score", "category": "general", "passing_score": 150,
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_course_name():
    """PUT /courses/{id} updates the course name."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        course_id = await _create_course(c, name="Old Name")
        r = await c.put(f"/api/v1/training/courses/{course_id}", json={"name": "New Name"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_update_course_no_fields_returns_422():
    """PUT /courses/{id} with empty body returns 422."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        course_id = await _create_course(c)
        r = await c.put(f"/api/v1/training/courses/{course_id}", json={})
    assert r.status_code == 422


# ─── B. Records ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_record_returns_201():
    """POST /training/records returns 201 + {id, expires_at}."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        course_id = await _create_course(c, name="Record Course")
        r = await c.post("/api/v1/training/records", json={
            "user_id": str(user_id), "course_id": course_id,
            "completed_at": "2026-01-15", "score": 85, "passed": True,
        })
    assert r.status_code == 201
    body = r.json()
    assert "id" in body
    assert "expires_at" in body


@pytest.mark.asyncio
async def test_create_record_with_validity_months_computes_expires_at():
    """POST /records auto-computes expires_at when course has validity_months."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        course_id = await _create_course(c, name="Expires Course", validity_months=12)
        r = await c.post("/api/v1/training/records", json={
            "user_id": str(user_id), "course_id": course_id,
            "completed_at": "2026-01-01", "passed": True,
        })
    assert r.status_code == 201
    assert r.json()["expires_at"] == "2027-01-01"


@pytest.mark.asyncio
async def test_create_record_unknown_course_returns_404():
    """POST /records with a non-existent course_id returns 404."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/training/records", json={
            "user_id": str(user_id), "course_id": str(uuid.uuid4()), "passed": True,
        })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_records_by_course_id():
    """GET /records?course_id=X returns only records for that course."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cid_a = await _create_course(c, name="Course A", category="security")
        cid_b = await _create_course(c, name="Course B", category="fire_safety")
        await c.post("/api/v1/training/records",
                     json={"user_id": str(user_id), "course_id": cid_a, "passed": True})
        await c.post("/api/v1/training/records",
                     json={"user_id": str(user_id), "course_id": cid_b, "passed": True})
        r = await c.get(f"/api/v1/training/records?course_id={cid_a}")
    assert r.status_code == 200
    records = r.json()
    assert all(item["course_id"] == cid_a for item in records)
    assert len(records) == 1


@pytest.mark.asyncio
async def test_delete_record_returns_ok():
    """DELETE /records/{id} removes the record."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        course_id = await _create_course(c)
        create_r = await c.post("/api/v1/training/records",
                                json={"user_id": str(user_id), "course_id": course_id, "passed": True})
        record_id = create_r.json()["id"]
        r = await c.delete(f"/api/v1/training/records/{record_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ─── C. Certifications ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_certification_returns_201():
    """POST /certifications returns 201 + {id}."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/training/certifications", json={
            "user_id": str(user_id),
            "certification_type": "First Aid Level 1",
            "issuing_body": "Red Cross",
            "issued_at": "2026-01-01",
            "expires_at": "2028-01-01",
        })
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_list_certifications_shows_created():
    """GET /certifications returns the created certification with expiry_status."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/training/certifications", json={
            "user_id": str(user_id),
            "certification_type": "Security Officer",
            "expires_at": "2028-12-31",
        })
        cert_id = create_r.json()["id"]
        r = await c.get("/api/v1/training/certifications")
    assert r.status_code == 200
    items = r.json()
    assert any(item["id"] == cert_id for item in items)
    # Expiry status should be computed
    assert all("expiry_status" in item for item in items)


@pytest.mark.asyncio
async def test_list_certifications_is_valid_filter():
    """GET /certifications?is_valid=true returns only valid certifications."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        # Create one cert and revoke it
        r1 = await c.post("/api/v1/training/certifications",
                          json={"user_id": str(user_id), "certification_type": "To Revoke"})
        revoke_id = r1.json()["id"]
        await c.post(f"/api/v1/training/certifications/{revoke_id}/revoke")
        # Create one still-valid cert
        r2 = await c.post("/api/v1/training/certifications",
                          json={"user_id": str(user_id), "certification_type": "Still Valid"})
        valid_id = r2.json()["id"]
        # Filter to valid only
        r = await c.get("/api/v1/training/certifications?is_valid=true")
    ids = [item["id"] for item in r.json()]
    assert valid_id in ids
    assert revoke_id not in ids


@pytest.mark.asyncio
async def test_update_certification_no_fields_returns_422():
    """PUT /certifications/{id} with empty body returns 422."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cert_r = await c.post("/api/v1/training/certifications",
                              json={"user_id": str(user_id), "certification_type": "Test Cert"})
        cert_id = cert_r.json()["id"]
        r = await c.put(f"/api/v1/training/certifications/{cert_id}", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_revoke_certification_via_post():
    """POST /certifications/{id}/revoke sets is_valid=False."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cert_r = await c.post("/api/v1/training/certifications",
                              json={"user_id": str(user_id), "certification_type": "Revoke Me"})
        cert_id = cert_r.json()["id"]
        r = await c.post(f"/api/v1/training/certifications/{cert_id}/revoke")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_revoke_certification_via_delete():
    """DELETE /certifications/{id} is a soft revoke — returns ok:True."""
    _, user_id, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        cert_r = await c.post("/api/v1/training/certifications",
                              json={"user_id": str(user_id), "certification_type": "Soft Delete"})
        cert_id = cert_r.json()["id"]
        r = await c.delete(f"/api/v1/training/certifications/{cert_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ─── D. Dashboard ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_returns_required_keys():
    """GET /training/dashboard returns all 8 top-level keys."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/training/dashboard")
    assert r.status_code == 200
    body = r.json()
    for key in ("total_courses", "total_records", "total_certifications",
                "course_stats", "record_stats", "cert_stats",
                "expiring_certifications", "expired_training_records"):
        assert key in body, f"Missing dashboard key: {key}"


@pytest.mark.asyncio
async def test_dashboard_total_courses_increments():
    """total_courses increments after a course is created."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        before = (await c.get("/api/v1/training/dashboard")).json()["total_courses"]
        await _create_course(c, name="Dashboard Course")
        after = (await c.get("/api/v1/training/dashboard")).json()["total_courses"]
    assert after == before + 1


# ─── E. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_course_viewer_returns_403():
    """Viewer (role 6) cannot create courses — training:manage not granted."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/training/courses", json={
            "name": "Forbidden", "category": "general",
        })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_record_viewer_returns_403():
    """Viewer (role 6) cannot create training records — training:write not granted."""
    _, user_id, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/training/records", json={
            "user_id": str(user_id), "course_id": str(uuid.uuid4()), "passed": True,
        })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_courses_rls_isolation():
    """Tenant B cannot see Tenant A's courses."""
    _, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_a) as c:
        course_id = await _create_course(c, name="Tenant A Course")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/training/courses")
    ids = [item["id"] for item in r.json()]
    assert course_id not in ids
