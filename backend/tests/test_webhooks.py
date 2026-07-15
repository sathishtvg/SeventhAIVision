"""Gap 12 — Webhook event subscription tests.

Section A: Subscription CRUD
Section B: Permission gates
Section C: HMAC signature verification
Section D: Delivery log
Section E: Stats overview
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.core.security import create_access_token
from app.services.webhook_dispatcher import _hmac_signature, _passes_filters, SUPPORTED_EVENTS
from tests.test_rbac import _seed_user_with_role

# ── Helpers ───────────────────────────────────────────────────────────────────


def _hdr(user_id, tenant_id, role_id=2) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user_id), str(tenant_id), role_id=role_id)}"}


_BASE_URL = "/api/v1/webhooks"


async def _create_sub(app_client, headers, **overrides):
    body = {
        "name": "Test Webhook",
        "url": "https://example.com/webhook",
        "event_types": ["alert_created"],
        **overrides,
    }
    r = await app_client.post(_BASE_URL + "/", json=body, headers=headers)
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# Section A — Subscription CRUD
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_create_subscription_returns_signing_secret(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    r = await _create_sub(app_client, _hdr(user_id, tenant_id))
    assert r.status_code == 201, r.text
    data = r.json()
    assert "signing_secret" in data
    assert len(data["signing_secret"]) == 64  # 32-byte hex
    assert "id" in data
    assert data["event_types"] == ["alert_created"]


@pytest.mark.asyncio
async def test_list_subscriptions_returns_created(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    headers = _hdr(user_id, tenant_id)
    await _create_sub(app_client, headers, name="WebhookA")
    await _create_sub(app_client, headers, name="WebhookB")
    r = await app_client.get(_BASE_URL + "/", headers=headers)
    assert r.status_code == 200
    names = [s["name"] for s in r.json()]
    assert "WebhookA" in names
    assert "WebhookB" in names


@pytest.mark.asyncio
async def test_get_subscription_by_id(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    headers = _hdr(user_id, tenant_id)
    created = (await _create_sub(app_client, headers)).json()
    sub_id = created["id"]
    r = await app_client.get(f"{_BASE_URL}/{sub_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["id"] == sub_id


@pytest.mark.asyncio
async def test_get_nonexistent_subscription_404(app_client, admin_session):
    import uuid
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    r = await app_client.get(f"{_BASE_URL}/{uuid.uuid4()}", headers=_hdr(user_id, tenant_id))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_subscription_name_and_active(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    headers = _hdr(user_id, tenant_id)
    sub_id = (await _create_sub(app_client, headers)).json()["id"]
    r = await app_client.put(
        f"{_BASE_URL}/{sub_id}",
        json={"name": "Renamed", "is_active": False},
        headers=headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Renamed"
    assert data["is_active"] is False


@pytest.mark.asyncio
async def test_delete_subscription(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    headers = _hdr(user_id, tenant_id)
    sub_id = (await _create_sub(app_client, headers)).json()["id"]
    r = await app_client.delete(f"{_BASE_URL}/{sub_id}", headers=headers)
    assert r.status_code == 204
    r2 = await app_client.get(f"{_BASE_URL}/{sub_id}", headers=headers)
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_create_subscription_with_filters(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    headers = _hdr(user_id, tenant_id)
    r = await _create_sub(
        app_client, headers,
        filters={"severity": ["high", "critical"]},
        event_types=["alert_created", "incident_created"],
    )
    assert r.status_code == 201
    data = r.json()
    assert data["filters"]["severity"] == ["high", "critical"]
    assert "incident_created" in data["event_types"]


@pytest.mark.asyncio
async def test_create_subscription_invalid_event_type_422(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    r = await _create_sub(
        app_client, _hdr(user_id, tenant_id),
        event_types=["nonexistent_event"],
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_filter_by_active(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    headers = _hdr(user_id, tenant_id)
    # active sub
    await _create_sub(app_client, headers, name="Active", is_active=True)
    # inactive sub
    created = (await _create_sub(app_client, headers, name="Inactive")).json()
    await app_client.put(
        f"{_BASE_URL}/{created['id']}", json={"is_active": False}, headers=headers
    )
    r = await app_client.get(_BASE_URL + "/?is_active=true", headers=headers)
    names = [s["name"] for s in r.json()]
    assert "Active" in names
    assert "Inactive" not in names


# ═══════════════════════════════════════════════════════════════════════════════
# Section B — Permission gates
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_create_subscription_denied_to_viewer(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)
    r = await _create_sub(app_client, _hdr(user_id, tenant_id, role_id=6))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_subscriptions_allowed_to_operator(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    r = await app_client.get(_BASE_URL + "/", headers=_hdr(user_id, tenant_id, role_id=4))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_delete_subscription_denied_to_operator(app_client, admin_session):
    import uuid
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    r = await app_client.delete(
        f"{_BASE_URL}/{uuid.uuid4()}",
        headers=_hdr(user_id, tenant_id, role_id=4),
    )
    assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# Section C — HMAC signature pure-function tests
# ═══════════════════════════════════════════════════════════════════════════════


def test_hmac_signature_format():
    sig = _hmac_signature("mysecret", b'{"test": true}')
    assert sig.startswith("sha256=")
    assert len(sig) == 7 + 64  # "sha256=" + 64 hex chars


def test_hmac_signature_is_deterministic():
    secret, body = "abc123", b"hello"
    assert _hmac_signature(secret, body) == _hmac_signature(secret, body)


def test_hmac_signature_changes_with_body():
    assert _hmac_signature("s", b"body1") != _hmac_signature("s", b"body2")


def test_hmac_signature_changes_with_secret():
    assert _hmac_signature("s1", b"body") != _hmac_signature("s2", b"body")


def test_hmac_signature_verifiable_by_recipient():
    secret = "super_secret"
    body = b'{"event_type":"alert_created"}'
    sig = _hmac_signature(secret, body)
    # Simulate recipient verification
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(sig, expected)


# ═══════════════════════════════════════════════════════════════════════════════
# Section D — Filter matching pure-function tests
# ═══════════════════════════════════════════════════════════════════════════════


def test_passes_filters_empty_filter_always_true():
    assert _passes_filters({"severity": "high"}, {}) is True


def test_passes_filters_matching_severity():
    assert _passes_filters({"severity": "critical"}, {"severity": ["high", "critical"]}) is True


def test_passes_filters_non_matching_severity():
    assert _passes_filters({"severity": "low"}, {"severity": ["high", "critical"]}) is False


def test_passes_filters_missing_key_in_payload_passes():
    # If the payload doesn't have the filter key at all, it passes (non-presence is not a mismatch)
    assert _passes_filters({}, {"severity": ["high"]}) is True


def test_passes_filters_multiple_keys_all_must_match():
    payload = {"severity": "high", "module_type": "lpr"}
    assert _passes_filters(payload, {"severity": ["high"], "module_type": ["lpr"]}) is True
    assert _passes_filters(payload, {"severity": ["high"], "module_type": ["face"]}) is False


# ═══════════════════════════════════════════════════════════════════════════════
# Section E — Stats overview + supported events
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_stats_overview_returns_structure(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    r = await app_client.get(f"{_BASE_URL}/stats/overview", headers=_hdr(user_id, tenant_id))
    assert r.status_code == 200
    data = r.json()
    assert "subscriptions" in data
    assert "deliveries" in data
    assert "active" in data["subscriptions"]
    assert "delivered" in data["deliveries"]


def test_supported_events_set():
    assert "alert_created" in SUPPORTED_EVENTS
    assert "incident_created" in SUPPORTED_EVENTS
    assert "camera_status_changed" in SUPPORTED_EVENTS
    assert "detection_created" in SUPPORTED_EVENTS
    assert "dsr_request_received" in SUPPORTED_EVENTS


@pytest.mark.asyncio
async def test_delivery_log_empty_for_new_subscription(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    headers = _hdr(user_id, tenant_id)
    sub_id = (await _create_sub(app_client, headers)).json()["id"]
    r = await app_client.get(f"{_BASE_URL}/{sub_id}/deliveries", headers=headers)
    assert r.status_code == 200
    assert r.json() == []
