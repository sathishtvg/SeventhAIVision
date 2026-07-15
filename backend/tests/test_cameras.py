import uuid

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from app.core.rtsp_validate import validate_rtsp_url
from app.core.security import create_access_token
from app.main import app
from app.services.camera_service import StreamHealthTracker
from tests.test_rbac import _seed_user_with_role


def test_stream_health_online_to_degraded():
    tracker = StreamHealthTracker()
    transitions = [tracker.record_failure() for _ in range(3)]
    # Only the 3rd failure crosses the degraded threshold -> only one transition fires.
    assert transitions[0] is None
    assert transitions[1] is None
    assert transitions[2] is not None
    assert transitions[2].new_status == "degraded"
    assert transitions[2].event_type == "stream_degraded"
    assert tracker.status == "degraded"


def test_stream_health_degraded_to_offline():
    tracker = StreamHealthTracker()
    transitions = [tracker.record_failure() for _ in range(10)]
    non_none = [t for t in transitions if t is not None]
    # Exactly two transitions total across 10 failures: online->degraded (at 3),
    # degraded->offline (at 10) — not one event per failure.
    assert len(non_none) == 2
    assert non_none[0].new_status == "degraded"
    assert non_none[1].new_status == "offline"
    assert non_none[1].event_type == "stream_disconnected"
    assert tracker.status == "offline"


def test_reconnect_backoff_caps_at_60s():
    tracker = StreamHealthTracker()
    delays = []
    for _ in range(9):
        tracker.record_failure()
        delays.append(tracker.backoff_seconds())
    assert delays == [1, 2, 4, 8, 16, 32, 60, 60, 60]


def test_failure_then_success_resets_and_emits_reconnected():
    tracker = StreamHealthTracker()
    for _ in range(5):
        tracker.record_failure()
    assert tracker.status == "degraded"

    transition = tracker.record_success()
    assert transition is not None
    assert transition.new_status == "online"
    assert transition.event_type == "stream_reconnected"
    assert tracker.consecutive_failures == 0

    # A second success while already online emits nothing further (no repeat events).
    assert tracker.record_success() is None


@pytest.mark.asyncio
async def test_create_camera_requires_permission(app_client, admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=6)  # viewer: no camera:create
    token = create_access_token(str(user_id), str(tenant_id), role_id=6)

    resp = await app_client.post(
        "/api/v1/cameras", json={"name": "Cam"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


# ── SSRF / URL validation unit tests (pure, no DB) ───────────────────────────

@pytest.mark.parametrize("url", [
    "rtsp://203.0.113.10/stream",       # TEST-NET — valid public IP
    "rtsps://camera.example.com/live",  # rtsps scheme, hostname
    "rtsp://1.1.1.1/stream1",           # public IP
])
def test_rtsp_validate_allows_valid_urls(url):
    ok, reason = validate_rtsp_url(url)
    assert ok is True
    assert reason is None


@pytest.mark.parametrize("url,expected_fragment", [
    ("http://1.1.1.1/stream",          "not allowed"),   # wrong scheme
    ("https://example.com/stream",     "not allowed"),   # wrong scheme
    ("file:///etc/passwd",             "not allowed"),   # file scheme
    ("dict://1.1.1.1:11111/",          "not allowed"),   # dict scheme
    ("rtsp://127.0.0.1/stream",        "reserved range"), # loopback
    ("rtsp://localhost/stream",        "not allowed"),   # localhost hostname
    ("rtsp://10.0.0.1/stream",         "reserved range"), # RFC-1918
    ("rtsp://172.16.0.50/cam",         "reserved range"), # RFC-1918
    ("rtsp://192.168.1.1/stream",      "reserved range"), # RFC-1918
    ("rtsp://169.254.169.254/latest",  "reserved range"), # AWS IMDS
    ("rtsp://100.64.0.1/stream",       "reserved range"), # CGNAT
    ("rtsp://::1/stream",              "no hostname"),    # bare IPv6 without brackets is unparseable → rejected
    ("rtsp://[::1]/stream",            "reserved range"), # IPv6 loopback bracket form
    ("rtsp://",                        "no hostname"),    # no host
])
def test_rtsp_validate_blocks_ssrf_urls(url, expected_fragment):
    ok, reason = validate_rtsp_url(url)
    assert ok is False
    assert reason is not None
    assert expected_fragment.lower() in reason.lower(), f"Expected '{expected_fragment}' in '{reason}'"


def test_validate_stream_endpoint_rejects_private_ip():
    """POST /api/v1/cameras/validate-stream with a private-IP RTSP URL → 422."""
    tenant_id = uuid.uuid4()
    token = create_access_token(str(uuid.uuid4()), str(tenant_id), role_id=2)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/cameras/validate-stream",
            json={"url": "rtsp://192.168.1.100/stream"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422


def test_validate_stream_endpoint_rejects_wrong_scheme():
    """POST /api/v1/cameras/validate-stream with http:// URL → 422."""
    tenant_id = uuid.uuid4()
    token = create_access_token(str(uuid.uuid4()), str(tenant_id), role_id=2)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/cameras/validate-stream",
            json={"url": "http://1.2.3.4/stream"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422
