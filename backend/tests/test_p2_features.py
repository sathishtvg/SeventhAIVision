"""P2 feature tests — Guard Training (P2-B), Emergency Broadcasts (P2-C), Visitor QR (P2-D).

Each test is self-contained: it seeds its own tenant + user via admin_session
(bypasses RLS) so it can run in any order without shared state.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


# ── Helpers ────────────────────────────────────────────────────────────────────

def _hdr(user_id, tenant_id, role_id=2) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=role_id)}"}


async def _seed_visitor(admin_session, tenant_id: uuid.UUID) -> tuple[str, str]:
    """Insert a visitor row; returns (visitor_id, qr_token)."""
    qr_token = str(uuid.uuid4())
    visitor_id = uuid.uuid4()
    await admin_session.execute(text("""
        INSERT INTO visitors
            (id, tenant_id, full_name, qr_token, status, is_active)
        VALUES (:id, :tid, 'Test Visitor', :qr, 'pending', TRUE)
    """), {"id": visitor_id, "tid": tenant_id, "qr": qr_token})
    await admin_session.commit()
    return str(visitor_id), qr_token


# ══════════════════════════════════════════════════════════════════════════════
# P2-B  Guard Training & Certification
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_training_course_crud(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create
    r = await app_client.post("/api/v1/training/courses", json={
        "name": "Fire Safety 101", "category": "fire_safety",
        "duration_hours": 4.0, "passing_score": 75, "validity_months": 12,
    }, headers=h)
    assert r.status_code == 201, r.text
    course_id = r.json()["id"]

    # List — new course appears
    r = await app_client.get("/api/v1/training/courses", headers=h)
    assert r.status_code == 200
    assert any(c["id"] == course_id and c["name"] == "Fire Safety 101" for c in r.json())

    # Update
    r = await app_client.put(f"/api/v1/training/courses/{course_id}",
                              json={"passing_score": 80}, headers=h)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Filter by category
    r = await app_client.get("/api/v1/training/courses?category=fire_safety", headers=h)
    assert r.status_code == 200
    assert all(c["category"] == "fire_safety" for c in r.json())


@pytest.mark.asyncio
async def test_training_course_invalid_category_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/training/courses", json={
        "name": "X", "category": "not_a_real_category",
    }, headers=h)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_training_record_create_and_list(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Seed a course first
    r = await app_client.post("/api/v1/training/courses", json={
        "name": "First Aid", "category": "first_aid",
    }, headers=h)
    assert r.status_code == 201
    course_id = r.json()["id"]

    # Create record
    r = await app_client.post("/api/v1/training/records", json={
        "user_id": str(user_id),
        "course_id": course_id,
        "score": 90,
        "passed": True,
        "notes": "Excellent performance",
    }, headers=h)
    assert r.status_code == 201, r.text
    record_id = r.json()["id"]

    # List records
    r = await app_client.get("/api/v1/training/records", headers=h)
    assert r.status_code == 200
    assert any(rec["id"] == record_id for rec in r.json())


@pytest.mark.asyncio
async def test_training_certification_create_and_revoke(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create certification
    r = await app_client.post("/api/v1/training/certifications", json={
        "user_id": str(user_id),
        "certification_type": "CCTV Operator Level 2",
        "issuing_body": "MAS Singapore",
        "certificate_number": "MAS-2026-001",
        "expires_at": "2027-06-30T00:00:00Z",
    }, headers=h)
    assert r.status_code == 201, r.text
    cert_id = r.json()["id"]

    # List certifications
    r = await app_client.get("/api/v1/training/certifications", headers=h)
    assert r.status_code == 200
    assert any(c["id"] == cert_id for c in r.json())

    # Revoke
    r = await app_client.post(f"/api/v1/training/certifications/{cert_id}/revoke",
                               json={"reason": "License expired early"}, headers=h)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_training_dashboard(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get("/api/v1/training/dashboard", headers=h)
    assert r.status_code == 200
    data = r.json()
    # Dashboard returns KPI-like summary fields
    assert "total_courses" in data
    assert "total_records" in data
    assert "total_certifications" in data


@pytest.mark.asyncio
async def test_training_requires_permission(app_client, admin_session):
    # role_id=6 = viewer; has training:read but not training:manage
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)
    h = _hdr(user_id, tenant_id, role_id=6)

    r = await app_client.post("/api/v1/training/courses", json={
        "name": "Unauthorised", "category": "general",
    }, headers=h)
    assert r.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# P2-C  Emergency Mass Notification (Broadcasts)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_broadcast_all_users(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/emergency/broadcasts", json={
        "title": "Drill Alert",
        "message": "This is a fire drill. Please evacuate calmly.",
        "severity": "drill",
        "broadcast_type": "all",
    }, headers=h)
    assert r.status_code == 201, r.text
    data = r.json()
    assert "id" in data
    broadcast_id = data["id"]

    # List broadcasts
    r = await app_client.get("/api/v1/emergency/broadcasts", headers=h)
    assert r.status_code == 200
    assert any(b["id"] == broadcast_id for b in r.json())


@pytest.mark.asyncio
async def test_broadcast_role_targeted(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/emergency/broadcasts", json={
        "title": "Guard Alert",
        "message": "Guards report to sector 7.",
        "severity": "warning",
        "broadcast_type": "role",
        "target_role_ids": [5],  # security_guard role
    }, headers=h)
    assert r.status_code == 201, r.text
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_broadcast_role_type_without_ids_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/emergency/broadcasts", json={
        "title": "X", "message": "Y",
        "severity": "info",
        "broadcast_type": "role",
        # missing target_role_ids
    }, headers=h)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_broadcast_invalid_severity_rejected(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/emergency/broadcasts", json={
        "title": "X", "message": "Y", "severity": "NUCLEAR",
    }, headers=h)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_broadcast_my_broadcasts_and_acknowledge(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Send a broadcast — the sender (user_id) should appear as a recipient (they're active users)
    r = await app_client.post("/api/v1/emergency/broadcasts", json={
        "title": "Critical Alert",
        "message": "Evacuate now.",
        "severity": "critical",
        "broadcast_type": "all",
    }, headers=h)
    assert r.status_code == 201
    broadcast_id = r.json()["id"]

    # Get my broadcasts
    r = await app_client.get("/api/v1/emergency/broadcasts/my", headers=h)
    assert r.status_code == 200
    my_list = r.json()
    matching = [b for b in my_list if b["id"] == broadcast_id]

    if matching:
        # Acknowledge if we are in the recipient list
        ack_r = await app_client.post(
            f"/api/v1/emergency/broadcasts/{broadcast_id}/acknowledge", headers=h
        )
        assert ack_r.status_code == 200


@pytest.mark.asyncio
async def test_broadcast_send_requires_permission(app_client, admin_session):
    # role_id=6 viewer — has broadcast:read but not broadcast:send
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)
    h = _hdr(user_id, tenant_id, role_id=6)

    r = await app_client.post("/api/v1/emergency/broadcasts", json={
        "title": "Sneaky", "message": "Not allowed.", "severity": "info",
    }, headers=h)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_broadcast_detail(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/emergency/broadcasts", json={
        "title": "Info Alert", "message": "Everything is fine.",
        "severity": "info", "broadcast_type": "all",
    }, headers=h)
    assert r.status_code == 201
    broadcast_id = r.json()["id"]

    r = await app_client.get(f"/api/v1/emergency/broadcasts/{broadcast_id}", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == broadcast_id
    assert data["title"] == "Info Alert"


# ══════════════════════════════════════════════════════════════════════════════
# P2-D  Visitor Pre-registration / QR
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_visitor_create_generates_qr_token(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/visitors", json={
        "full_name": "Alice Tan",
        "company": "ACME Corp",
        "host_name": "Bob Lee",
        "purpose": "Vendor meeting",
        "visitor_email": "alice@acme.com",
        "expected_from": "2026-07-01T09:00:00Z",
        "expected_until": "2026-07-01T17:00:00Z",
    }, headers=h)
    assert r.status_code == 201, r.text
    data = r.json()
    assert "id" in data
    assert "qr_token" in data
    assert len(data["qr_token"]) > 8  # non-empty UUID-like token
    visitor_id = data["id"]

    # List all visitors — new one appears
    r = await app_client.get("/api/v1/visitors", headers=h)
    assert r.status_code == 200
    assert any(v["id"] == visitor_id for v in r.json()["items"])


@pytest.mark.asyncio
async def test_visitor_upcoming_list(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Seed a visitor expected "now + 1 hour" via the API so they appear in upcoming
    from datetime import datetime, timedelta, timezone
    from_dt = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    until_dt = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    r = await app_client.post("/api/v1/visitors", json={
        "full_name": "Upcoming Bob", "expected_from": from_dt, "expected_until": until_dt,
    }, headers=h)
    assert r.status_code == 201
    visitor_id = r.json()["id"]

    r = await app_client.get("/api/v1/visitors/upcoming?hours=4", headers=h)
    assert r.status_code == 200
    assert any(v["id"] == visitor_id for v in r.json())


@pytest.mark.asyncio
async def test_visitor_qr_png_returns_image(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Create visitor to get a valid id
    r = await app_client.post("/api/v1/visitors", json={"full_name": "QR Test User"}, headers=h)
    assert r.status_code == 201
    visitor_id = r.json()["id"]

    # Fetch the QR PNG
    r = await app_client.get(f"/api/v1/visitors/{visitor_id}/qr.png", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    # PNG magic bytes: \x89PNG
    assert r.content[:4] == b"\x89PNG"


@pytest.mark.asyncio
async def test_visitor_lookup_by_qr_token(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/visitors", json={"full_name": "QR Lookup User"}, headers=h)
    assert r.status_code == 201
    qr_token = r.json()["qr_token"]

    r = await app_client.get(f"/api/v1/visitors/by-qr/{qr_token}", headers=h)
    assert r.status_code == 200
    assert r.json()["qr_token"] == qr_token
    assert r.json()["full_name"] == "QR Lookup User"


@pytest.mark.asyncio
async def test_visitor_lookup_invalid_token_404(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.get(f"/api/v1/visitors/by-qr/nonexistent-token-000", headers=h)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_visitor_qr_scan_checkin_then_checkout(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    # Guard role (5) is the one doing QR scans
    guard_id = user_id
    h = _hdr(guard_id, tenant_id, role_id=2)

    # Create visitor
    r = await app_client.post("/api/v1/visitors", json={"full_name": "Scan Test Visitor"}, headers=h)
    assert r.status_code == 201
    qr_token = r.json()["qr_token"]

    # First QR scan → arrival
    r = await app_client.post("/api/v1/visitors/qr-scan", json={
        "qr_token": qr_token, "badge_number": "B-001",
    }, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["event_type"] == "arrival"

    # Second scan (same token, now status='arrived') → departure toggle
    r = await app_client.post("/api/v1/visitors/qr-scan", json={
        "qr_token": qr_token, "badge_number": "B-001",
    }, headers=h)
    assert r.status_code == 200
    assert r.json()["event_type"] == "departure"


@pytest.mark.asyncio
async def test_visitor_qr_scan_nonexistent_token(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/visitors/qr-scan", json={
        "qr_token": "completely-fake-qr-token-12345",
    }, headers=h)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_visitor_deactivate(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/visitors", json={"full_name": "To Be Cancelled"}, headers=h)
    assert r.status_code == 201
    visitor_id = r.json()["id"]

    r = await app_client.delete(f"/api/v1/visitors/{visitor_id}", headers=h)
    assert r.status_code == 200
    assert r.json()["is_active"] is False


@pytest.mark.asyncio
async def test_visitor_checkin_and_checkout_manual(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    r = await app_client.post("/api/v1/visitors", json={"full_name": "Manual Checkin Bob"}, headers=h)
    assert r.status_code == 201
    visitor_id = r.json()["id"]

    # Manual check-in
    r = await app_client.post(f"/api/v1/visitors/{visitor_id}/checkin",
                               json={"badge_number": "M-999"}, headers=h)
    assert r.status_code == 200

    # Manual check-out
    r = await app_client.post(f"/api/v1/visitors/{visitor_id}/checkout",
                               json={"notes": "Completed visit"}, headers=h)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_visitor_send_qr_no_email_rejected(app_client, admin_session):
    """send-qr endpoint requires visitor_email to be set."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    h = _hdr(user_id, tenant_id)

    # Visitor without email
    r = await app_client.post("/api/v1/visitors", json={"full_name": "No Email Visitor"}, headers=h)
    assert r.status_code == 201
    visitor_id = r.json()["id"]

    r = await app_client.post(f"/api/v1/visitors/{visitor_id}/send-qr", headers=h)
    assert r.status_code == 422
