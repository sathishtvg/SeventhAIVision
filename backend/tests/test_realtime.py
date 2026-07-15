import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import redis as sync_redis
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.security import create_access_token
from app.main import app
from app.realtime.connection_manager import manager
from shared.constants import tenant_events_channel

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _publish(tenant_id, event_type: str, payload: dict) -> None:
    r = sync_redis.from_url(REDIS_URL)
    message = json.dumps({
        "schema_version": 1, "event_type": event_type, "tenant_id": str(tenant_id),
        "payload": payload, "occurred_at": datetime.now(timezone.utc).isoformat(),
    })
    r.publish(tenant_events_channel(str(tenant_id)), message)
    r.close()


def _receive_text_timeout(ws, timeout: float = 5.0) -> str:
    """ws.receive_text() wrapped in a daemon thread so it never blocks indefinitely.
    Raises TimeoutError if no message arrives within `timeout` seconds."""
    result: list[str] = []
    exc: list[BaseException] = []

    def _target() -> None:
        try:
            result.append(ws.receive_text())
        except BaseException as e:  # noqa: BLE001
            exc.append(e)

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        raise TimeoutError(
            f"ws.receive_text() did not return within {timeout}s — "
            "Redis pub/sub message was not delivered to WebSocket"
        )
    if exc:
        raise exc[0]
    return result[0]


def test_ws_rejects_invalid_token():
    # Server accept()s then immediately close(1008) so TestClient's session
    # doesn't hang waiting for an accept that never arrives.
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live?token=not-a-real-token") as ws:
            try:
                ws.receive_text()
                raise AssertionError("expected WebSocketDisconnect with code 1008")
            except WebSocketDisconnect as exc:
                assert exc.code == 1008  # WS_1008_POLICY_VIOLATION


def test_ws_receives_published_event():
    tenant_id = uuid.uuid4()
    token = create_access_token(str(uuid.uuid4()), str(tenant_id), role_id=2)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/live?token={token}") as ws:
            # Give the asyncio.create_task(redis_pubsub_listener) time to run its
            # psubscribe call inside the TestClient's background event loop.
            time.sleep(1.0)
            _publish(tenant_id, "alert_created", {"alert_id": "abc-123"})
            received = _receive_text_timeout(ws, timeout=5.0)
            data = json.loads(received)
            assert data["event_type"] == "alert_created"
            assert data["tenant_id"] == str(tenant_id)
            assert data["payload"]["alert_id"] == "abc-123"


def test_ws_tenant_isolation():
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    token_a = create_access_token(str(uuid.uuid4()), str(tenant_a), role_id=2)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/live?token={token_a}") as ws:
            time.sleep(1.0)
            _publish(tenant_b, "alert_created", {"alert_id": "should-not-arrive"})
            _publish(tenant_a, "alert_created", {"alert_id": "marker-for-tenant-a"})

            received = _receive_text_timeout(ws, timeout=5.0)
            data = json.loads(received)
            # Tenant A's socket sees its own marker first/only — tenant B's
            # message was never delivered to it at all.
            assert data["payload"]["alert_id"] == "marker-for-tenant-a"


def test_ws_ip_limit_enforced(monkeypatch):
    """Third connection from the same IP (all TestClient requests come from
    127.0.0.1) must be rejected with 1008 when the per-IP cap is lowered to 2."""
    import app.realtime.router as ws_router

    monkeypatch.setattr(ws_router, "_MAX_PER_IP", 2)

    tenant_id = uuid.uuid4()

    def tok() -> str:
        return create_access_token(str(uuid.uuid4()), str(tenant_id), role_id=2)

    with TestClient(app) as client:
        open_sessions = []
        try:
            for _ in range(2):
                sess = client.websocket_connect(f"/ws/live?token={tok()}")
                sess.__enter__()
                open_sessions.append(sess)

            # Third connection from same IP (127.0.0.1) must be rejected.
            with client.websocket_connect(f"/ws/live?token={tok()}") as ws3:
                try:
                    ws3.receive_text()
                    raise AssertionError("expected WebSocketDisconnect with code 1008")
                except WebSocketDisconnect as exc:
                    assert exc.code == 1008
        finally:
            for sess in open_sessions:
                try:
                    sess.__exit__(None, None, None)
                except Exception:
                    pass


def test_disconnect_pruned_from_manager():
    tenant_id = uuid.uuid4()
    token = create_access_token(str(uuid.uuid4()), str(tenant_id), role_id=2)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/live?token={token}") as ws:
            time.sleep(0.2)
            assert manager.connection_count(tenant_id) == 1
        time.sleep(0.2)

    assert manager.connection_count(tenant_id) == 0
