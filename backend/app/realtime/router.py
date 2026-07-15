"""GET /ws/live?token=<jwt> — query-param token, not a post-connect auth
message: browsers' native WebSocket API can't set custom Authorization headers
at handshake time, and resolving auth *before* accept() means unauthorized
clients are rejected at the upgrade layer without consuming a connection slot
(plan §9). Reuses the exact same decode_access_token used by every REST
dependency — no parallel auth scheme.

Task 10: per-IP connection count limit (_MAX_PER_IP). Checked before accept()
so an attacker flooding /ws/live never consumes connection slots on this
process. The limit is per-replica and in-process; for multi-replica deployments
a Redis counter would be needed, but a single-node deployment is the primary
target and in-process tracking is simpler and crash-safe.
"""

import os
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.core.security import InvalidTokenError, decode_access_token
from app.realtime.connection_manager import manager

router = APIRouter()

_MAX_PER_IP: int = int(os.environ.get("WS_MAX_CONNECTIONS_PER_IP", "10"))


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket, token: str = Query(...)) -> None:
    # ── 1. Auth ──────────────────────────────────────────────────────────────
    try:
        payload = decode_access_token(token)
    except InvalidTokenError:
        # Must accept() before close(); TestClient's WebSocket session blocks
        # indefinitely waiting for the accept if we close() without accepting.
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # ── 2. Per-IP rate limit ─────────────────────────────────────────────────
    client_ip: str = websocket.client.host if websocket.client else "unknown"
    if manager.ip_connection_count(client_ip) >= _MAX_PER_IP:
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # ── 3. Connect ───────────────────────────────────────────────────────────
    tenant_id = UUID(payload["tenant_id"])  # JWT claims are JSON, tenant_id is a string there

    await manager.connect(tenant_id, websocket, client_ip)
    try:
        while True:
            # Client isn't expected to send data; this just keeps the receive
            # loop alive so a disconnect is detected promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(tenant_id, websocket, client_ip)
