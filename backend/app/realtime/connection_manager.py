"""Tracks active WebSocket connections per tenant for this API process (plan
§9). One instance lives on app.state, created at startup. Each replica has its
own instance and its own Redis Pub/Sub subscription (redis_listener.py) —
there is no cross-replica connection state; cross-replica fan-out is handled
entirely by Redis Pub/Sub delivering to every subscriber.

Task 10: also tracks per-IP connection counts so the router can enforce a
per-IP limit before accepting new connections.
"""

import asyncio
import logging
from uuid import UUID

from fastapi import WebSocket

from app.core.metrics import ws_connections_active

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[UUID, set[WebSocket]] = {}
        self._ip_counts: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def connect(self, tenant_id: UUID, websocket: WebSocket, client_ip: str = "") -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(tenant_id, set()).add(websocket)
            if client_ip:
                self._ip_counts[client_ip] = self._ip_counts.get(client_ip, 0) + 1
        ws_connections_active.labels(tenant_id=str(tenant_id)).set(len(self._connections[tenant_id]))
        logger.info("ws_connect tenant_id=%s ip=%s active=%d", tenant_id, client_ip or "?", len(self._connections[tenant_id]))

    async def disconnect(self, tenant_id: UUID, websocket: WebSocket, client_ip: str = "") -> None:
        async with self._lock:
            conns = self._connections.get(tenant_id)
            if conns is not None:
                conns.discard(websocket)
                if not conns:
                    del self._connections[tenant_id]
            if client_ip and client_ip in self._ip_counts:
                self._ip_counts[client_ip] = max(0, self._ip_counts[client_ip] - 1)
                if self._ip_counts[client_ip] == 0:
                    del self._ip_counts[client_ip]
        ws_connections_active.labels(tenant_id=str(tenant_id)).set(self.connection_count(tenant_id))
        logger.info("ws_disconnect tenant_id=%s ip=%s", tenant_id, client_ip or "?")

    async def broadcast_to_tenant(self, tenant_id: UUID, message: str) -> None:
        """Dead sockets are pruned on send failure rather than via a separate sweep."""
        async with self._lock:
            conns = list(self._connections.get(tenant_id, ()))
        if not conns:
            return
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                live = self._connections.get(tenant_id)
                if live is not None:
                    for ws in dead:
                        live.discard(ws)
                    if not live:
                        self._connections.pop(tenant_id, None)

    def connection_count(self, tenant_id: UUID | None = None) -> int:
        if tenant_id is not None:
            return len(self._connections.get(tenant_id, ()))
        return sum(len(s) for s in self._connections.values())

    def ip_connection_count(self, ip: str) -> int:
        return self._ip_counts.get(ip, 0)


manager = ConnectionManager()
