"""P5-O: Privacy Masking Zones + PDPA Consent & Data Subject Requests.

Two routers in pdpa.py, both completely untested:

Privacy router (/api/v1/privacy):
- POST /zones: create masking zone tied to a camera
- GET  /zones: list zones; optional ?camera_id= filter
- DELETE /zones/{zone_id}: hard delete; 404 on missing
- GET  /zones/camera/{camera_id}: PUBLIC read (no auth) — called by AI workers

PDPA router (/api/v1/pdpa):
- GET  /consents: list with filters (consent_type, consented, site_id)
- POST /consents: record consent; consented=True/False
- GET  /dsar: list data subject access requests; filter by dsar_status;
              each row has computed is_overdue field
- POST /dsar: create DSAR; request_type must be one of
              (access, erasure, correction, portability, objection)
- PUT  /dsar/{id}: update status (in_review, fulfilled, rejected, partial);
                   invalid status → 422; non-existent → 404

Auth:
- Privacy endpoints require privacy:manage permission (401 without auth)
- PDPA endpoints require pdpa:read (GET) / pdpa:admin (POST/PUT) (401 without auth)
- /zones/camera/{id} is PUBLIC — no auth required
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

_POLYGON = [{"x": 0.1, "y": 0.1}, {"x": 0.4, "y": 0.1},
            {"x": 0.4, "y": 0.4}, {"x": 0.1, "y": 0.4}]


async def _make_camera(client: AsyncClient, name: str = "Privacy Cam") -> str:
    r = await client.post("/api/v1/cameras", json={"name": name, "location": "Office"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _create_privacy_zone(
    client: AsyncClient,
    camera_id: str,
    *,
    name: str = "Masking Zone",
    fill_color: str = "#000000",
) -> dict:
    r = await client.post("/api/v1/privacy/zones", json={
        "camera_id": camera_id,
        "name": name,
        "polygon": _POLYGON,
        "fill_color": fill_color,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _create_consent(
    client: AsyncClient,
    *,
    subject_name: str = "Alice Tan",
    consent_type: str = "face_recognition",
    consented: bool = True,
) -> dict:
    r = await client.post("/api/v1/pdpa/consents", json={
        "data_subject_name": subject_name,
        "consent_type": consent_type,
        "consented": consented,
        "consent_method": "physical_form",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _create_dsar(
    client: AsyncClient,
    *,
    subject_name: str = "Bob Lim",
    request_type: str = "access",
) -> dict:
    r = await client.post("/api/v1/pdpa/dsar", json={
        "request_type": request_type,
        "data_subject_name": subject_name,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


# ── Privacy Zones: Create ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_privacy_zone_returns_id(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "CreateZoneCam")
    zone = await _create_privacy_zone(auth_client, cam, name="Corridor Zone")
    assert "id" in zone


@pytest.mark.asyncio
async def test_create_privacy_zone_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/privacy/zones", json={
        "camera_id": "00000000-0000-0000-0000-000000000001",
        "name": "Unauth Zone",
        "polygon": _POLYGON,
    })
    assert r.status_code == 401


# ── Privacy Zones: List ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_privacy_zones_includes_created(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "ListPrivacyCam")
    zone = await _create_privacy_zone(auth_client, cam, name="List Check Privacy Zone")

    r = await auth_client.get("/api/v1/privacy/zones")
    assert r.status_code == 200
    ids = [z["id"] for z in r.json()]
    assert zone["id"] in ids


@pytest.mark.asyncio
async def test_list_privacy_zones_has_expected_fields(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "FieldsPrivacyCam")
    await _create_privacy_zone(auth_client, cam)

    r = await auth_client.get("/api/v1/privacy/zones")
    assert r.status_code == 200
    assert len(r.json()) >= 1
    zone = r.json()[0]
    for field in ("id", "camera_id", "name", "polygon", "fill_color", "is_active"):
        assert field in zone, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_list_privacy_zones_filter_by_camera_id(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "FilterPrivacyCam")
    await _create_privacy_zone(auth_client, cam, name="Cam-Filtered Zone")

    r = await auth_client.get(f"/api/v1/privacy/zones?camera_id={cam}")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    for row in rows:
        assert str(row["camera_id"]) == cam


@pytest.mark.asyncio
async def test_list_privacy_zones_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/privacy/zones")
    assert r.status_code == 401


# ── Privacy Zones: Public Camera Read (no auth) ───────────────────────────────

@pytest.mark.asyncio
async def test_get_camera_privacy_zones_public(auth_client: AsyncClient, client: AsyncClient):
    cam = await _make_camera(auth_client, "PublicPrivacyCam")
    await _create_privacy_zone(auth_client, cam, name="Worker Zone")

    # This endpoint has no auth requirement — AI workers call it
    r = await client.get(f"/api/v1/privacy/zones/camera/{cam}")
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert len(rows) >= 1
    for row in rows:
        assert "polygon" in row
        assert "fill_color" in row


@pytest.mark.asyncio
async def test_get_camera_privacy_zones_unknown_camera_empty(client: AsyncClient):
    # Non-existent camera → empty list (not 404), no auth needed
    r = await client.get("/api/v1/privacy/zones/camera/00000000-0000-0000-0000-000000000099")
    assert r.status_code == 200
    assert r.json() == []


# ── Privacy Zones: Delete ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_privacy_zone_returns_deleted_true(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "DeletePrivacyCam")
    zone = await _create_privacy_zone(auth_client, cam, name="Zone To Delete")

    r = await auth_client.delete(f"/api/v1/privacy/zones/{zone['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is True
    assert str(body["id"]) == zone["id"]


@pytest.mark.asyncio
async def test_delete_privacy_zone_is_hard_delete(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "HardDelPrivacyCam")
    zone = await _create_privacy_zone(auth_client, cam, name="Hard Delete Zone")

    await auth_client.delete(f"/api/v1/privacy/zones/{zone['id']}")

    # After hard delete, zone should no longer appear in list
    r = await auth_client.get(f"/api/v1/privacy/zones?camera_id={cam}")
    ids = [z["id"] for z in r.json()]
    assert zone["id"] not in ids


@pytest.mark.asyncio
async def test_delete_nonexistent_privacy_zone_404(auth_client: AsyncClient):
    r = await auth_client.delete(
        "/api/v1/privacy/zones/00000000-0000-0000-0000-000000000099"
    )
    assert r.status_code == 404


# ── PDPA Consents: Create ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_consent_returns_id(auth_client: AsyncClient):
    consent = await _create_consent(auth_client, subject_name="Consent Test Subject")
    assert "id" in consent


@pytest.mark.asyncio
async def test_create_consent_false_records_revocation(auth_client: AsyncClient):
    consent = await _create_consent(
        auth_client, subject_name="Revoke Subject", consented=False
    )
    assert "id" in consent


@pytest.mark.asyncio
async def test_create_consent_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/pdpa/consents", json={
        "data_subject_name": "Unauth Subject",
        "consent_type": "face_recognition",
        "consented": True,
        "consent_method": "digital",
    })
    assert r.status_code == 401


# ── PDPA Consents: List ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_consents_includes_created(auth_client: AsyncClient):
    consent = await _create_consent(auth_client, subject_name="List Consent Subject")

    r = await auth_client.get("/api/v1/pdpa/consents")
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()]
    assert consent["id"] in ids


@pytest.mark.asyncio
async def test_list_consents_has_expected_fields(auth_client: AsyncClient):
    await _create_consent(auth_client, subject_name="Field Check Consent")

    r = await auth_client.get("/api/v1/pdpa/consents")
    assert r.status_code == 200
    assert len(r.json()) >= 1
    row = r.json()[0]
    for field in ("id", "data_subject_name", "consent_type", "consented",
                  "consent_method", "valid_from"):
        assert field in row, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_list_consents_filter_by_consent_type(auth_client: AsyncClient):
    await _create_consent(auth_client, subject_name="CCTV Consent", consent_type="cctv_monitoring")

    r = await auth_client.get("/api/v1/pdpa/consents?consent_type=cctv_monitoring")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    for row in rows:
        assert row["consent_type"] == "cctv_monitoring"


@pytest.mark.asyncio
async def test_list_consents_filter_by_consented_false(auth_client: AsyncClient):
    await _create_consent(auth_client, subject_name="Revoked Person", consented=False)

    r = await auth_client.get("/api/v1/pdpa/consents?consented=false")
    assert r.status_code == 200
    for row in r.json():
        assert row["consented"] is False


@pytest.mark.asyncio
async def test_list_consents_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/pdpa/consents")
    assert r.status_code == 401


# ── PDPA DSAR: Create ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_dsar_returns_id(auth_client: AsyncClient):
    dsar = await _create_dsar(auth_client, subject_name="Alice DSAR")
    assert "id" in dsar


@pytest.mark.asyncio
async def test_create_dsar_all_valid_request_types(auth_client: AsyncClient):
    for rtype in ("access", "erasure", "correction", "portability", "objection"):
        dsar = await _create_dsar(
            auth_client, subject_name=f"Subject-{rtype}", request_type=rtype
        )
        assert "id" in dsar, f"No id for request_type={rtype}"


@pytest.mark.asyncio
async def test_create_dsar_invalid_type_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/pdpa/dsar", json={
        "request_type": "deletion_please",   # not a valid type
        "data_subject_name": "Bad Request",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_dsar_returns_deadline_at(auth_client: AsyncClient):
    dsar = await _create_dsar(auth_client, subject_name="Deadline Check")
    # DSAR regulations require a response deadline — should be auto-computed
    assert "deadline_at" in dsar
    assert dsar["deadline_at"] is not None


@pytest.mark.asyncio
async def test_create_dsar_initial_status(auth_client: AsyncClient):
    dsar = await _create_dsar(auth_client, subject_name="Status Check")
    assert "status" in dsar
    # Should be some pending/open status — just assert it's a non-empty string
    assert dsar["status"] and isinstance(dsar["status"], str)


@pytest.mark.asyncio
async def test_create_dsar_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/pdpa/dsar", json={
        "request_type": "access",
        "data_subject_name": "Unauth Person",
    })
    assert r.status_code == 401


# ── PDPA DSAR: List ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_dsars_includes_created(auth_client: AsyncClient):
    dsar = await _create_dsar(auth_client, subject_name="List DSAR Subject")

    r = await auth_client.get("/api/v1/pdpa/dsar")
    assert r.status_code == 200
    ids = [d["id"] for d in r.json()]
    assert dsar["id"] in ids


@pytest.mark.asyncio
async def test_list_dsars_has_expected_fields(auth_client: AsyncClient):
    await _create_dsar(auth_client, subject_name="Fields DSAR")

    r = await auth_client.get("/api/v1/pdpa/dsar")
    assert r.status_code == 200
    assert len(r.json()) >= 1
    row = r.json()[0]
    for field in ("id", "request_type", "status", "deadline_at",
                  "data_subject_name", "is_overdue"):
        assert field in row, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_list_dsars_is_overdue_false_for_new_dsar(auth_client: AsyncClient):
    dsar = await _create_dsar(auth_client, subject_name="Not Overdue")

    r = await auth_client.get("/api/v1/pdpa/dsar")
    row = next((d for d in r.json() if d["id"] == dsar["id"]), None)
    assert row is not None
    # Newly created DSAR has a future deadline — must not be overdue
    assert row["is_overdue"] is False


@pytest.mark.asyncio
async def test_list_dsars_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/pdpa/dsar")
    assert r.status_code == 401


# ── PDPA DSAR: Update ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_dsar_status_in_review(auth_client: AsyncClient):
    dsar = await _create_dsar(auth_client, subject_name="Update DSAR Subject")

    r = await auth_client.put(f"/api/v1/pdpa/dsar/{dsar['id']}", json={
        "status": "in_review",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "in_review"


@pytest.mark.asyncio
async def test_update_dsar_fulfilled_sets_fulfilled_at(auth_client: AsyncClient):
    dsar = await _create_dsar(auth_client, subject_name="Fulfill DSAR")

    r = await auth_client.put(f"/api/v1/pdpa/dsar/{dsar['id']}", json={
        "status": "fulfilled",
        "fulfillment_notes": "All data provided to subject.",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "fulfilled"
    assert body["fulfilled_at"] is not None


@pytest.mark.asyncio
async def test_update_dsar_all_valid_statuses(auth_client: AsyncClient):
    for status_val in ("in_review", "fulfilled", "rejected", "partial"):
        dsar = await _create_dsar(auth_client, subject_name=f"Status-{status_val}")
        r = await auth_client.put(f"/api/v1/pdpa/dsar/{dsar['id']}", json={"status": status_val})
        assert r.status_code == 200, f"Failed for status={status_val}: {r.text}"


@pytest.mark.asyncio
async def test_update_dsar_invalid_status_422(auth_client: AsyncClient):
    dsar = await _create_dsar(auth_client, subject_name="Bad Status Subject")

    r = await auth_client.put(f"/api/v1/pdpa/dsar/{dsar['id']}", json={
        "status": "cancelled_by_robot",   # not a valid status
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_nonexistent_dsar_404(auth_client: AsyncClient):
    r = await auth_client.put(
        "/api/v1/pdpa/dsar/00000000-0000-0000-0000-000000000099",
        json={"status": "in_review"},
    )
    assert r.status_code == 404
