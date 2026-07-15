"""P5-N: Guard Training & Certification Tracking — /api/v1/training

Full test coverage for:

Courses (training:manage / training:read):
- POST /courses: create with valid category; invalid category → 422; passing_score > 100 → 422
- GET  /courses: list; filter by ?category=; filter by ?is_active=true
- PUT  /courses/{id}: update name, deactivate, invalid category → 422, non-existent → 404

Training Records (training:write / training:read):
- POST /records: create; validates course exists (404); auto-computes expires_at from validity_months
- GET  /records: list with expected fields (guard_name, course_name); filter by user_id, passed
- DELETE /records/{id}: hard delete; non-existent → 404

Certifications (training:write / training:read):
- POST /certifications: create; returned id
- GET  /certifications: list; expected fields (guard_name, expiry_status); filter by is_valid
- PUT  /certifications/{id}: update type, update is_valid; empty body → 422; non-existent → 404
- POST /certifications/{id}/revoke: sets is_valid=False; non-existent → 404

Dashboard (training:read):
- GET /dashboard: all expected top-level keys; all stats are non-negative ints
- dashboard reflects newly created course in counts

Auth:
- All list and create endpoints require auth (401)
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_user_id(client: AsyncClient) -> str:
    """Return the id of the first user visible to the auth_client."""
    r = await client.get("/api/v1/users")
    assert r.status_code == 200, r.text
    users = r.json()
    assert len(users) >= 1, "No users found in tenant"
    return str(users[0]["id"])


async def _create_course(
    client: AsyncClient,
    *,
    name: str = "Basic Security",
    category: str = "security",
    passing_score: int = 75,
    validity_months: int | None = None,
) -> str:
    body: dict = {
        "name": name,
        "category": category,
        "passing_score": passing_score,
    }
    if validity_months is not None:
        body["validity_months"] = validity_months
    r = await client.post("/api/v1/training/courses", json=body)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _create_record(
    client: AsyncClient,
    user_id: str,
    course_id: str,
    *,
    passed: bool = True,
    score: int | None = 80,
) -> str:
    body: dict = {"user_id": user_id, "course_id": course_id, "passed": passed}
    if score is not None:
        body["score"] = score
    r = await client.post("/api/v1/training/records", json=body)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _create_cert(
    client: AsyncClient,
    user_id: str,
    *,
    certification_type: str = "CCTV Operator",
    expires_at: str | None = "2028-12-31",
) -> str:
    body: dict = {"user_id": user_id, "certification_type": certification_type}
    if expires_at:
        body["expires_at"] = expires_at
    r = await client.post("/api/v1/training/certifications", json=body)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


# ── Courses: Create ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_course_201(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/training/courses", json={
        "name": "Fire Safety Awareness",
        "category": "fire_safety",
        "passing_score": 70,
    })
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_create_course_invalid_category_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/training/courses", json={
        "name": "Intro Course",
        "category": "advanced_magic",   # not a valid category
        "passing_score": 70,
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_course_passing_score_above_100_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/training/courses", json={
        "name": "Impossible Exam",
        "category": "security",
        "passing_score": 101,
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_course_all_valid_categories(auth_client: AsyncClient):
    for cat in ("general", "fire_safety", "first_aid", "security", "cctv",
                "legal", "physical", "emergency_response"):
        r = await auth_client.post("/api/v1/training/courses", json={
            "name": f"Course-{cat}", "category": cat, "passing_score": 70,
        })
        assert r.status_code == 201, f"Failed for category={cat}: {r.text}"


@pytest.mark.asyncio
async def test_create_course_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/training/courses", json={
        "name": "Unauth Course", "category": "security", "passing_score": 70,
    })
    assert r.status_code == 401


# ── Courses: List ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_courses_includes_created(auth_client: AsyncClient):
    course_id = await _create_course(auth_client, name="List Me Course")

    r = await auth_client.get("/api/v1/training/courses")
    assert r.status_code == 200
    ids = [row["id"] for row in r.json()]
    assert course_id in ids


@pytest.mark.asyncio
async def test_list_courses_has_expected_fields(auth_client: AsyncClient):
    await _create_course(auth_client, name="Field Check Course")

    r = await auth_client.get("/api/v1/training/courses")
    assert r.status_code == 200
    assert len(r.json()) >= 1
    row = r.json()[0]
    for field in ("id", "name", "category", "passing_score", "is_active", "created_at"):
        assert field in row, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_list_courses_filter_by_category(auth_client: AsyncClient):
    await _create_course(auth_client, name="Legal Basics", category="legal")

    r = await auth_client.get("/api/v1/training/courses?category=legal")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    for row in rows:
        assert row["category"] == "legal"


@pytest.mark.asyncio
async def test_list_courses_filter_is_active_true(auth_client: AsyncClient):
    await _create_course(auth_client, name="Active Course")

    r = await auth_client.get("/api/v1/training/courses?is_active=true")
    assert r.status_code == 200
    for row in r.json():
        assert row["is_active"] is True


@pytest.mark.asyncio
async def test_list_courses_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/training/courses")
    assert r.status_code == 401


# ── Courses: Update ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_course_name(auth_client: AsyncClient):
    course_id = await _create_course(auth_client, name="Old Course Name")

    r = await auth_client.put(f"/api/v1/training/courses/{course_id}", json={"name": "New Course Name"})
    assert r.status_code == 200

    courses = (await auth_client.get("/api/v1/training/courses")).json()
    names = [c["name"] for c in courses if c["id"] == course_id]
    assert "New Course Name" in names


@pytest.mark.asyncio
async def test_update_course_deactivate(auth_client: AsyncClient):
    course_id = await _create_course(auth_client, name="Deactivate Course")

    r = await auth_client.put(f"/api/v1/training/courses/{course_id}", json={"is_active": False})
    assert r.status_code == 200

    courses = (await auth_client.get("/api/v1/training/courses")).json()
    row = next((c for c in courses if c["id"] == course_id), None)
    assert row is not None
    assert row["is_active"] is False


@pytest.mark.asyncio
async def test_update_course_invalid_category_422(auth_client: AsyncClient):
    course_id = await _create_course(auth_client, name="Valid Course")

    r = await auth_client.put(f"/api/v1/training/courses/{course_id}", json={"category": "not_a_category"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_nonexistent_course_404(auth_client: AsyncClient):
    r = await auth_client.put(
        "/api/v1/training/courses/00000000-0000-0000-0000-000000000099",
        json={"name": "Ghost"},
    )
    assert r.status_code == 404


# ── Training Records: Create ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_training_record_201(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    course_id = await _create_course(auth_client, name="Record Course")

    r = await auth_client.post("/api/v1/training/records", json={
        "user_id": user_id,
        "course_id": course_id,
        "passed": True,
        "score": 85,
    })
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_create_record_nonexistent_course_404(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)

    r = await auth_client.post("/api/v1/training/records", json={
        "user_id": user_id,
        "course_id": "00000000-0000-0000-0000-000000000099",
        "passed": True,
    })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_record_with_validity_months_computes_expires_at(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    # validity_months=6 → expires_at should be auto-computed
    course_id = await _create_course(auth_client, name="ValidityMonths Course", validity_months=6)

    r = await auth_client.post("/api/v1/training/records", json={
        "user_id": user_id,
        "course_id": course_id,
        "passed": True,
    })
    assert r.status_code == 201
    body = r.json()
    assert body["expires_at"] is not None


@pytest.mark.asyncio
async def test_create_record_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/training/records", json={
        "user_id": "00000000-0000-0000-0000-000000000001",
        "course_id": "00000000-0000-0000-0000-000000000002",
        "passed": True,
    })
    assert r.status_code == 401


# ── Training Records: List ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_records_includes_created(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    course_id = await _create_course(auth_client, name="List Records Course")
    record_id = await _create_record(auth_client, user_id, course_id)

    r = await auth_client.get("/api/v1/training/records")
    assert r.status_code == 200
    ids = [row["id"] for row in r.json()]
    assert record_id in ids


@pytest.mark.asyncio
async def test_list_records_has_expected_fields(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    course_id = await _create_course(auth_client, name="Fields Course")
    await _create_record(auth_client, user_id, course_id)

    r = await auth_client.get("/api/v1/training/records")
    assert r.status_code == 200
    assert len(r.json()) >= 1
    row = r.json()[0]
    for field in ("id", "user_id", "course_id", "passed", "guard_name", "course_name", "created_at"):
        assert field in row, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_list_records_filter_by_passed(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    course_id = await _create_course(auth_client, name="Pass Filter Course")
    await _create_record(auth_client, user_id, course_id, passed=True)

    r = await auth_client.get("/api/v1/training/records?passed=true")
    assert r.status_code == 200
    for row in r.json():
        assert row["passed"] is True


@pytest.mark.asyncio
async def test_list_records_filter_by_user_id(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    course_id = await _create_course(auth_client, name="User Filter Course")
    await _create_record(auth_client, user_id, course_id)

    r = await auth_client.get(f"/api/v1/training/records?user_id={user_id}")
    assert r.status_code == 200
    for row in r.json():
        assert str(row["user_id"]) == user_id


@pytest.mark.asyncio
async def test_list_records_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/training/records")
    assert r.status_code == 401


# ── Training Records: Delete ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_training_record(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    course_id = await _create_course(auth_client, name="Delete Record Course")
    record_id = await _create_record(auth_client, user_id, course_id)

    r = await auth_client.delete(f"/api/v1/training/records/{record_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_delete_nonexistent_record_404(auth_client: AsyncClient):
    r = await auth_client.delete(
        "/api/v1/training/records/00000000-0000-0000-0000-000000000099"
    )
    assert r.status_code == 404


# ── Certifications: Create ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_certification_201(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)

    r = await auth_client.post("/api/v1/training/certifications", json={
        "user_id": user_id,
        "certification_type": "Fire Warden",
        "issuing_body": "SCDF",
        "expires_at": "2027-06-30",
    })
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_create_certification_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/training/certifications", json={
        "user_id": "00000000-0000-0000-0000-000000000001",
        "certification_type": "Fire Warden",
    })
    assert r.status_code == 401


# ── Certifications: List ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_certifications_includes_created(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    cert_id = await _create_cert(auth_client, user_id, certification_type="CCTV Operator Cert")

    r = await auth_client.get("/api/v1/training/certifications")
    assert r.status_code == 200
    ids = [row["id"] for row in r.json()]
    assert cert_id in ids


@pytest.mark.asyncio
async def test_list_certifications_has_expected_fields(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    await _create_cert(auth_client, user_id, certification_type="Field Check Cert")

    r = await auth_client.get("/api/v1/training/certifications")
    assert r.status_code == 200
    assert len(r.json()) >= 1
    row = r.json()[0]
    for field in ("id", "user_id", "certification_type", "is_valid", "guard_name", "expiry_status"):
        assert field in row, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_list_certifications_expiry_status_valid_for_future_date(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    await _create_cert(auth_client, user_id,
                       certification_type="Far Future Cert",
                       expires_at="2099-12-31")

    r = await auth_client.get("/api/v1/training/certifications")
    certs = [c for c in r.json() if c.get("certification_type") == "Far Future Cert"]
    assert len(certs) >= 1
    assert certs[0]["expiry_status"] == "valid"


@pytest.mark.asyncio
async def test_list_certifications_filter_is_valid(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    await _create_cert(auth_client, user_id, certification_type="Valid Filter Cert")

    r = await auth_client.get("/api/v1/training/certifications?is_valid=true")
    assert r.status_code == 200
    for row in r.json():
        assert row["is_valid"] is True


@pytest.mark.asyncio
async def test_list_certifications_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/training/certifications")
    assert r.status_code == 401


# ── Certifications: Update ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_certification_type(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    cert_id = await _create_cert(auth_client, user_id, certification_type="Old Type")

    r = await auth_client.put(f"/api/v1/training/certifications/{cert_id}", json={
        "certification_type": "New Type",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_update_certification_empty_body_422(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    cert_id = await _create_cert(auth_client, user_id)

    r = await auth_client.put(f"/api/v1/training/certifications/{cert_id}", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_nonexistent_certification_404(auth_client: AsyncClient):
    r = await auth_client.put(
        "/api/v1/training/certifications/00000000-0000-0000-0000-000000000099",
        json={"is_valid": False},
    )
    assert r.status_code == 404


# ── Certifications: Revoke ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_revoke_certification_via_post(auth_client: AsyncClient):
    user_id = await _get_user_id(auth_client)
    cert_id = await _create_cert(auth_client, user_id, certification_type="To Revoke")

    r = await auth_client.post(f"/api/v1/training/certifications/{cert_id}/revoke")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    certs = (await auth_client.get("/api/v1/training/certifications")).json()
    revoked = next((c for c in certs if c["id"] == cert_id), None)
    assert revoked is not None
    assert revoked["is_valid"] is False


@pytest.mark.asyncio
async def test_revoke_nonexistent_certification_404(auth_client: AsyncClient):
    r = await auth_client.post(
        "/api/v1/training/certifications/00000000-0000-0000-0000-000000000099/revoke"
    )
    assert r.status_code == 404


# ── Dashboard ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_returns_expected_keys(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/training/dashboard")
    assert r.status_code == 200
    body = r.json()
    for key in ("total_courses", "total_records", "total_certifications",
                "course_stats", "record_stats", "cert_stats",
                "expiring_certifications", "expired_training_records"):
        assert key in body, f"Missing dashboard key: {key}"


@pytest.mark.asyncio
async def test_dashboard_stats_non_negative(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/training/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert body["total_courses"] >= 0
    assert body["total_records"] >= 0
    assert body["total_certifications"] >= 0


@pytest.mark.asyncio
async def test_dashboard_reflects_created_course(auth_client: AsyncClient):
    before = (await auth_client.get("/api/v1/training/dashboard")).json()["total_courses"]
    await _create_course(auth_client, name="Dashboard Count Course")
    after = (await auth_client.get("/api/v1/training/dashboard")).json()["total_courses"]
    assert after >= before + 1


@pytest.mark.asyncio
async def test_dashboard_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/training/dashboard")
    assert r.status_code == 401
