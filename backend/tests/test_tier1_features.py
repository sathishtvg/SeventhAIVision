"""Tier 1 security gap feature tests.

Covers all 8 Tier 1 backend features:
1. Per-account login lockout (10 fails → 30 min lock, admin unlock)
2. Active session management (list / revoke / revoke-all)
3. Alert auto-escalation (scheduler escalates unacknowledged alerts)
4. Incident extended workflow (new statuses + status history timeline)
5. Visitor overstay alert (scheduler creates visitor.overstay alerts)
6. Multi-camera alert correlation (same module_type in 5-min window → shared correlation_id)
7. Checkpoint scan QR verification (scanned_code matched against stored qr_code)
8. Shift handover report (aggregates open incidents/alerts/patrol stats)
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _auth(user_id, tenant_id, role_id=2):
    token = create_access_token(str(user_id), str(tenant_id), role_id=role_id)
    return {"Authorization": f"Bearer {token}"}


# ─── 1. Per-account login lockout ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lockout_triggered_after_threshold(app_client, admin_session):
    """Pre-set failed_login_count to 9, one more bad attempt locks the account."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    slug = (await admin_session.execute(
        text("SELECT slug FROM tenants WHERE id = :tid"), {"tid": tenant_id}
    )).first().slug
    email = (await admin_session.execute(
        text("SELECT email FROM users WHERE id = :uid"), {"uid": user_id}
    )).first().email

    await admin_session.execute(
        text("UPDATE users SET failed_login_count = 9 WHERE id = :uid"), {"uid": user_id}
    )
    await admin_session.commit()

    # 10th bad attempt — triggers lockout (still returns 401 on this attempt)
    resp = await app_client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": slug, "email": email, "password": "wrong-password"},
    )
    assert resp.status_code == 401

    # Confirm locked_until was set
    lock_row = (await admin_session.execute(
        text("SELECT locked_until, failed_login_count FROM users WHERE id = :uid"), {"uid": user_id}
    )).first()
    assert lock_row.locked_until is not None
    assert lock_row.failed_login_count >= 10

    # Next attempt (correct password) → 403 account locked
    resp2 = await app_client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": slug, "email": email, "password": "test-pass"},
    )
    assert resp2.status_code == 403
    assert "locked" in resp2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_counter_resets_on_success(app_client, admin_session):
    """Successful login resets failed_login_count to 0."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    slug = (await admin_session.execute(
        text("SELECT slug FROM tenants WHERE id = :tid"), {"tid": tenant_id}
    )).first().slug
    email = (await admin_session.execute(
        text("SELECT email FROM users WHERE id = :uid"), {"uid": user_id}
    )).first().email

    await admin_session.execute(
        text("UPDATE users SET failed_login_count = 5 WHERE id = :uid"), {"uid": user_id}
    )
    await admin_session.commit()

    resp = await app_client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": slug, "email": email, "password": "x"},
    )
    assert resp.status_code == 200

    count = (await admin_session.execute(
        text("SELECT failed_login_count FROM users WHERE id = :uid"), {"uid": user_id}
    )).first().failed_login_count
    assert count == 0


@pytest.mark.asyncio
async def test_admin_unlock_clears_lockout(app_client, admin_session):
    """POST /auth/users/{id}/unlock clears locked_until and resets counter."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)

    locked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
    await admin_session.execute(
        text("UPDATE users SET locked_until = :lu, failed_login_count = 10 WHERE id = :uid"),
        {"lu": locked_until, "uid": user_id},
    )
    await admin_session.commit()

    resp = await app_client.post(
        f"/api/v1/auth/users/{user_id}/unlock",
        headers=_auth(user_id, tenant_id, role_id=2),
    )
    assert resp.status_code == 200
    assert resp.json()["locked"] is False

    lock_row = (await admin_session.execute(
        text("SELECT locked_until, failed_login_count FROM users WHERE id = :uid"), {"uid": user_id}
    )).first()
    assert lock_row.locked_until is None
    assert lock_row.failed_login_count == 0


# ─── 2. Active session management ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_my_sessions(app_client, admin_session):
    """GET /sessions/me returns active refresh_token rows for the current user."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)

    tok_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO refresh_tokens (id, tenant_id, user_id, token_hash, expires_at, device_name) "
            "VALUES (:id, :tid, :uid, :hash, now() + interval '7 days', 'TestPhone')"
        ),
        {"id": tok_id, "tid": tenant_id, "uid": user_id, "hash": f"h-{tok_id.hex}"},
    )
    await admin_session.commit()

    resp = await app_client.get("/api/v1/sessions/me", headers=_auth(user_id, tenant_id))
    assert resp.status_code == 200
    sessions = resp.json()
    assert any(str(s["id"]) == str(tok_id) for s in sessions)


@pytest.mark.asyncio
async def test_revoke_session(app_client, admin_session):
    """DELETE /sessions/me/{id} marks the session as revoked; it disappears from the list."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)

    tok_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO refresh_tokens (id, tenant_id, user_id, token_hash, expires_at) "
            "VALUES (:id, :tid, :uid, :hash, now() + interval '7 days')"
        ),
        {"id": tok_id, "tid": tenant_id, "uid": user_id, "hash": f"h-{tok_id.hex}"},
    )
    await admin_session.commit()

    resp = await app_client.delete(
        f"/api/v1/sessions/me/{tok_id}",
        headers=_auth(user_id, tenant_id),
    )
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True

    sessions = (await app_client.get("/api/v1/sessions/me", headers=_auth(user_id, tenant_id))).json()
    assert not any(str(s["id"]) == str(tok_id) for s in sessions)


@pytest.mark.asyncio
async def test_admin_revoke_all_user_sessions(app_client, admin_session):
    """DELETE /sessions/users/{uid} revokes all sessions for the specified user."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)

    for i in range(2):
        await admin_session.execute(
            text(
                "INSERT INTO refresh_tokens (id, tenant_id, user_id, token_hash, expires_at) "
                "VALUES (:id, :tid, :uid, :hash, now() + interval '7 days')"
            ),
            {"id": uuid.uuid4(), "tid": tenant_id, "uid": user_id, "hash": f"h-{i}-{user_id.hex}"},
        )
    await admin_session.commit()

    resp = await app_client.delete(
        f"/api/v1/sessions/users/{user_id}",
        headers=_auth(user_id, tenant_id, role_id=2),
    )
    assert resp.status_code == 200
    assert resp.json()["revoked_count"] >= 2

    remaining = (await app_client.get("/api/v1/sessions/me", headers=_auth(user_id, tenant_id))).json()
    assert remaining == []


# ─── 3. Alert auto-escalation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escalation_bumps_severity(admin_session):
    """Insert an old medium alert; scheduler escalates it to high."""
    from app.scheduler_main import escalate_unacknowledged_alerts

    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)

    cam_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) "
             "VALUES (:id, :tid, 'EscCam', '[]'::jsonb)"),
        {"id": cam_id, "tid": tenant_id},
    )
    alert_id = uuid.uuid4()
    old_time = datetime.now(timezone.utc) - timedelta(minutes=70)  # exceeds 60-min medium window
    await admin_session.execute(
        text(
            "INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, status, "
            "                   title, alert_code, created_at) "
            "VALUES (:id, :tid, :cid, 'intrusion', 'medium', 'open', 'Esc Test', 'test.esc', :ts)"
        ),
        {"id": alert_id, "tid": tenant_id, "cid": cam_id, "ts": old_time},
    )
    await admin_session.commit()

    redis_mock = AsyncMock()
    await escalate_unacknowledged_alerts(admin_session, redis_mock)

    row = (await admin_session.execute(
        text("SELECT severity, escalated_at, original_severity FROM alerts WHERE id = :id"),
        {"id": str(alert_id)},
    )).first()
    assert row.severity == "high"
    assert row.escalated_at is not None
    assert row.original_severity == "medium"


@pytest.mark.asyncio
async def test_escalation_skips_already_escalated(admin_session):
    """An alert with escalated_at already set is not re-processed."""
    from app.scheduler_main import escalate_unacknowledged_alerts

    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    cam_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) "
             "VALUES (:id, :tid, 'EscCam2', '[]'::jsonb)"),
        {"id": cam_id, "tid": tenant_id},
    )
    alert_id = uuid.uuid4()
    old_time = datetime.now(timezone.utc) - timedelta(minutes=70)
    await admin_session.execute(
        text(
            "INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, status, title, "
            "                   alert_code, created_at, escalated_at, original_severity) "
            "VALUES (:id, :tid, :cid, 'face', 'high', 'open', 'AlrEsc', 'test.esc2', "
            "        :ts, now(), 'medium')"
        ),
        {"id": alert_id, "tid": tenant_id, "cid": cam_id, "ts": old_time},
    )
    await admin_session.commit()

    redis_mock = AsyncMock()
    await escalate_unacknowledged_alerts(admin_session, redis_mock)

    severity = (await admin_session.execute(
        text("SELECT severity FROM alerts WHERE id = :id"), {"id": str(alert_id)}
    )).first().severity
    assert severity == "high"  # unchanged; would be "critical" if re-escalated


# ─── 4. Incident extended workflow ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_incident_status_advance_and_timeline(app_client, admin_session):
    """Advance incident through dispatched → en_route; timeline endpoint reflects history."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)  # operator

    cam_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) "
             "VALUES (:id, :tid, 'WfCam', '[]'::jsonb)"),
        {"id": cam_id, "tid": tenant_id},
    )
    incident_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, alert_code) "
            "VALUES (:id, :tid, :cid, 'WF Incident', 'high', 'open', 'intrusion.breach')"
        ),
        {"id": incident_id, "tid": tenant_id, "cid": cam_id},
    )
    await admin_session.commit()

    headers = _auth(user_id, tenant_id, role_id=4)

    resp = await app_client.put(
        f"/api/v1/incidents/{incident_id}/status",
        json={"status": "dispatched", "notes": "Guard dispatched"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "dispatched"

    resp = await app_client.put(
        f"/api/v1/incidents/{incident_id}/status",
        json={"status": "en_route", "latitude": 1.3521, "longitude": 103.8198},
        headers=headers,
    )
    assert resp.status_code == 200

    timeline = (await app_client.get(
        f"/api/v1/incidents/{incident_id}/timeline", headers=headers
    )).json()
    assert len(timeline) >= 2
    to_statuses = [e.get("to_status") for e in timeline if e.get("type") == "status_change"]
    assert "dispatched" in to_statuses
    assert "en_route" in to_statuses


@pytest.mark.asyncio
async def test_invalid_status_returns_422(app_client, admin_session):
    """PUT /incidents/{id}/status with an unknown status → 422."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    cam_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) "
             "VALUES (:id, :tid, 'BadCam', '[]'::jsonb)"),
        {"id": cam_id, "tid": tenant_id},
    )
    incident_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, alert_code) "
            "VALUES (:id, :tid, :cid, 'Bad Wf', 'high', 'open', 'test.bad')"
        ),
        {"id": incident_id, "tid": tenant_id, "cid": cam_id},
    )
    await admin_session.commit()

    resp = await app_client.put(
        f"/api/v1/incidents/{incident_id}/status",
        json={"status": "flying"},
        headers=_auth(user_id, tenant_id, role_id=4),
    )
    assert resp.status_code == 422


# ─── 5. Visitor overstay alert ───────────────────────────────────────────────

async def _seed_visitor_overstay(admin_session, tenant_id, visitor_name="Overstay Visitor"):
    """Seed site → camera → overstay visitor. Returns visitor_id."""
    site_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :n)"),
        {"id": site_id, "tid": tenant_id, "n": f"OvSite-{site_id.hex[:4]}"},
    )
    cam_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name, site_id, ai_modules_enabled) "
             "VALUES (:id, :tid, 'OvCam', :sid, '[]'::jsonb)"),
        {"id": cam_id, "tid": tenant_id, "sid": site_id},
    )
    visitor_id = uuid.uuid4()
    expired = datetime.now(timezone.utc) - timedelta(hours=2)
    await admin_session.execute(
        text(
            "INSERT INTO visitors (id, tenant_id, full_name, expected_until, site_id, qr_token) "
            "VALUES (:id, :tid, :n, :eu, :sid, :qr)"
        ),
        {"id": visitor_id, "tid": tenant_id, "n": visitor_name, "eu": expired, "sid": site_id,
         "qr": str(uuid.uuid4())},
    )
    await admin_session.commit()
    return visitor_id


@pytest.mark.asyncio
async def test_overstay_alert_created(admin_session):
    """Expired visitor with site+camera → scheduler creates visitor.overstay alert."""
    from app.scheduler_main import check_visitor_overstays

    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    visitor_id = await _seed_visitor_overstay(admin_session, tenant_id)

    await check_visitor_overstays(admin_session, AsyncMock())

    row = (await admin_session.execute(
        text(
            "SELECT id FROM alerts WHERE alert_code = 'visitor.overstay' "
            "AND (message_params->>'visitor_id')::text = :vid"
        ),
        {"vid": str(visitor_id)},
    )).first()
    assert row is not None, "Expected a visitor.overstay alert to be created"


@pytest.mark.asyncio
async def test_overstay_alert_not_duplicated(admin_session):
    """Running scheduler twice for same visitor creates only one alert."""
    from app.scheduler_main import check_visitor_overstays

    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    visitor_id = await _seed_visitor_overstay(admin_session, tenant_id, visitor_name="Dedup Visitor")

    redis_mock = AsyncMock()
    await check_visitor_overstays(admin_session, redis_mock)
    await check_visitor_overstays(admin_session, redis_mock)  # second run

    count = (await admin_session.execute(
        text(
            "SELECT COUNT(*) FROM alerts WHERE alert_code = 'visitor.overstay' "
            "AND (message_params->>'visitor_id')::text = :vid"
        ),
        {"vid": str(visitor_id)},
    )).scalar()
    assert count == 1, f"Expected exactly 1 overstay alert, got {count}"


@pytest.mark.asyncio
async def test_overstay_alert_created_without_site(admin_session):
    """Visitor with no site_id still gets an overstay alert via any-tenant-camera fallback.

    Regression for the bug where `WHERE c.site_id = :sid` with sid=NULL evaluated
    to `c.site_id = NULL` (always false in SQL), silently dropping alerts for every
    visitor not assigned to a site.
    """
    from unittest.mock import AsyncMock
    from app.scheduler_main import check_visitor_overstays

    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)

    # Camera with NO site_id — acts as the tenant's fallback camera
    cam_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) "
             "VALUES (:id, :tid, 'FallbackCam', '[]'::jsonb)"),
        {"id": cam_id, "tid": tenant_id},
    )

    # Visitor with NO site_id, already past expected_until
    visitor_id = uuid.uuid4()
    expired = datetime.now(timezone.utc) - timedelta(hours=3)
    await admin_session.execute(
        text(
            "INSERT INTO visitors (id, tenant_id, full_name, expected_until, qr_token) "
            "VALUES (:id, :tid, :n, :eu, :qr)"
        ),
        {"id": visitor_id, "tid": tenant_id, "n": "No-Site Visitor", "eu": expired,
         "qr": str(uuid.uuid4())},
    )
    await admin_session.commit()

    await check_visitor_overstays(admin_session, AsyncMock())

    row = (await admin_session.execute(
        text(
            "SELECT id FROM alerts WHERE alert_code = 'visitor.overstay' "
            "AND (message_params->>'visitor_id')::text = :vid"
        ),
        {"vid": str(visitor_id)},
    )).first()
    assert row is not None, (
        "Expected visitor.overstay alert even when visitor has no site_id"
    )


# ─── 6. Multi-camera alert correlation ───────────────────────────────────────

@pytest.mark.asyncio
async def test_correlation_assigns_shared_id(admin_session):
    """assign_alert_correlation links two same-module alerts within 5 min."""
    from app.routers.alerts import assign_alert_correlation

    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    cam_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) "
             "VALUES (:id, :tid, 'CorrCam', '[]'::jsonb)"),
        {"id": cam_id, "tid": tenant_id},
    )
    # Use unique module_type to avoid collisions with alerts from other tests
    mod_type = f"test_corr_{uuid.uuid4().hex[:8]}"

    alert1_id = uuid.uuid4()
    alert2_id = uuid.uuid4()
    for aid in (alert1_id, alert2_id):
        await admin_session.execute(
            text(
                "INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, status, "
                "                   title, alert_code) "
                "VALUES (:id, :tid, :cid, :mod, 'medium', 'open', 'Corr Test', 'test.corr')"
            ),
            {"id": aid, "tid": tenant_id, "cid": cam_id, "mod": mod_type},
        )
    await admin_session.commit()

    await assign_alert_correlation(admin_session, str(alert2_id), str(tenant_id), mod_type)
    await admin_session.commit()

    corr1 = (await admin_session.execute(
        text("SELECT correlation_id FROM alerts WHERE id = :id"), {"id": str(alert1_id)}
    )).first().correlation_id
    corr2 = (await admin_session.execute(
        text("SELECT correlation_id FROM alerts WHERE id = :id"), {"id": str(alert2_id)}
    )).first().correlation_id

    assert corr1 is not None
    assert corr2 is not None
    assert corr1 == corr2


@pytest.mark.asyncio
async def test_correlated_endpoint_returns_siblings(app_client, admin_session):
    """GET /alerts/{id}/correlated returns other alerts sharing the same correlation_id."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    cam_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name, ai_modules_enabled) "
             "VALUES (:id, :tid, 'CorrCam2', '[]'::jsonb)"),
        {"id": cam_id, "tid": tenant_id},
    )

    corr_id = uuid.uuid4()
    alert1_id = uuid.uuid4()
    alert2_id = uuid.uuid4()
    for aid in (alert1_id, alert2_id):
        await admin_session.execute(
            text(
                "INSERT INTO alerts (id, tenant_id, camera_id, module_type, severity, status, "
                "                   title, alert_code, correlation_id) "
                "VALUES (:id, :tid, :cid, 'lpr', 'high', 'open', 'Sibling', 'test.sib', :crid)"
            ),
            {"id": aid, "tid": tenant_id, "cid": cam_id, "crid": corr_id},
        )
    await admin_session.commit()

    resp = await app_client.get(
        f"/api/v1/alerts/{alert1_id}/correlated",
        headers=_auth(user_id, tenant_id, role_id=2),
    )
    assert resp.status_code == 200
    sibling_ids = [a["id"] for a in resp.json()]
    assert str(alert2_id) in sibling_ids


# ─── 7. Checkpoint scan QR verification ──────────────────────────────────────

async def _setup_patrol(admin_session, tenant_id, user_id, qr_code=None):
    """Seed patrol route → checkpoint → session. Returns (checkpoint_id, session_id)."""
    site_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :n)"),
        {"id": site_id, "tid": tenant_id, "n": f"PSite-{site_id.hex[:4]}"},
    )
    route_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO patrol_routes (id, tenant_id, site_id, name) "
             "VALUES (:id, :tid, :sid, :n)"),
        {"id": route_id, "tid": tenant_id, "sid": site_id, "n": f"Rt-{route_id.hex[:6]}"},
    )
    checkpoint_id = uuid.uuid4()
    if qr_code is not None:
        await admin_session.execute(
            text("INSERT INTO patrol_checkpoints (id, tenant_id, route_id, sequence, name, qr_code) "
                 "VALUES (:id, :tid, :rid, 1, 'Gate', :qr)"),
            {"id": checkpoint_id, "tid": tenant_id, "rid": route_id, "qr": qr_code},
        )
    else:
        await admin_session.execute(
            text("INSERT INTO patrol_checkpoints (id, tenant_id, route_id, sequence, name) "
                 "VALUES (:id, :tid, :rid, 1, 'Gate')"),
            {"id": checkpoint_id, "tid": tenant_id, "rid": route_id},
        )
    session_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO patrol_sessions (id, tenant_id, route_id, guard_user_id, status, started_at) "
            "VALUES (:id, :tid, :rid, :uid, 'in_progress', now())"
        ),
        {"id": session_id, "tid": tenant_id, "rid": route_id, "uid": user_id},
    )
    await admin_session.commit()
    return checkpoint_id, session_id


@pytest.mark.asyncio
async def test_qr_scan_verified_on_match(app_client, admin_session):
    """QR scan with matching code → verified=True."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=5)  # security_guard

    qr = f"QR-{uuid.uuid4().hex[:12]}"
    checkpoint_id, session_id = await _setup_patrol(admin_session, tenant_id, user_id, qr_code=qr)

    resp = await app_client.post(
        f"/api/v1/patrols/sessions/{session_id}/scan",
        json={"checkpoint_id": str(checkpoint_id), "scan_method": "qr", "scanned_code": qr},
        headers=_auth(user_id, tenant_id, role_id=5),
    )
    assert resp.status_code == 200
    assert resp.json()["verified"] is True


@pytest.mark.asyncio
async def test_qr_scan_unverified_on_mismatch(app_client, admin_session):
    """QR scan with wrong code → verified=False."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=5)

    checkpoint_id, session_id = await _setup_patrol(
        admin_session, tenant_id, user_id, qr_code="CORRECT-QR"
    )

    resp = await app_client.post(
        f"/api/v1/patrols/sessions/{session_id}/scan",
        json={"checkpoint_id": str(checkpoint_id), "scan_method": "qr", "scanned_code": "WRONG-QR"},
        headers=_auth(user_id, tenant_id, role_id=5),
    )
    assert resp.status_code == 200
    assert resp.json()["verified"] is False


@pytest.mark.asyncio
async def test_manual_scan_always_verified(app_client, admin_session):
    """scan_method=manual (no QR code needed) → always verified=True."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=5)

    checkpoint_id, session_id = await _setup_patrol(admin_session, tenant_id, user_id)

    resp = await app_client.post(
        f"/api/v1/patrols/sessions/{session_id}/scan",
        json={"checkpoint_id": str(checkpoint_id), "scan_method": "manual"},
        headers=_auth(user_id, tenant_id, role_id=5),
    )
    assert resp.status_code == 200
    assert resp.json()["verified"] is True


# ─── 8. Shift handover report ─────────────────────────────────────────────────

async def _seed_shift(admin_session, tenant_id, user_id, hours_ago=8):
    """Seed site + active shift for user. Returns shift_id."""
    site_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :tid, :n)"),
        {"id": site_id, "tid": tenant_id, "n": f"ShiftSite-{site_id.hex[:4]}"},
    )
    shift_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO shifts (id, tenant_id, guard_user_id, site_id, scheduled_start, "
            "                   scheduled_end, status, actual_start) "
            "VALUES (:id, :tid, :uid, :sid, "
            "        now() - :h * interval '1 hour', now(), 'active', "
            "        now() - :h * interval '1 hour')"
        ),
        {"id": shift_id, "tid": tenant_id, "uid": user_id, "sid": site_id, "h": hours_ago},
    )
    await admin_session.commit()
    return shift_id


@pytest.mark.asyncio
async def test_shift_handover_creates_report(app_client, admin_session):
    """POST /shifts/{id}/handover returns aggregated stats."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    shift_id = await _seed_shift(admin_session, tenant_id, user_id)

    resp = await app_client.post(
        f"/api/v1/shifts/{shift_id}/handover",
        params={"outgoing_notes": "All clear."},
        headers=_auth(user_id, tenant_id, role_id=2),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "open_incidents" in body
    assert "open_alerts" in body
    assert "patrol_routes_completed" in body
    assert "checkpoints_scanned" in body
    assert body["shift_id"] == str(shift_id)
    assert "id" in body


@pytest.mark.asyncio
async def test_shift_handover_retrievable(app_client, admin_session):
    """After generating, GET /shifts/{id}/handover returns the stored report."""
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    shift_id = await _seed_shift(admin_session, tenant_id, user_id, hours_ago=4)

    headers = _auth(user_id, tenant_id, role_id=2)
    post_resp = await app_client.post(f"/api/v1/shifts/{shift_id}/handover", headers=headers)
    assert post_resp.status_code == 200

    get_resp = await app_client.get(f"/api/v1/shifts/{shift_id}/handover", headers=headers)
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["shift_id"] == str(shift_id)
    assert "outgoing_guard_id" in body
