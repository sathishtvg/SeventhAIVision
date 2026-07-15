"""Prometheus metrics for the api process (plan §10).

http_requests_total/http_request_duration_seconds are hand-rolled via a plain
Starlette middleware (metrics_middleware below) instead of
prometheus-fastapi-instrumentator: that library's route-name resolution
(routing.py::_get_route_name) calls `route.path` on every matched route, but
the installed Starlette version's route matching can hand back wrapper objects
without a `.path` attribute, crashing with AttributeError on every single
request — confirmed by actually running the test suite, not assumed. It's
already the latest released version (8.0.0), so there's no upgrade to wait for;
a few lines of direct prometheus_client usage avoids the dependency entirely
without losing the metric.

ws_connections_active is updated by ConnectionManager; ws_events_forwarded_total
by redis_listener, both at the exact point the corresponding state change happens.
"""

import time

from prometheus_client import Counter, Gauge, Histogram
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

http_requests_total = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "path", "status_code"]
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds", "HTTP request duration in seconds", ["method", "path"]
)

ws_connections_active = Gauge(
    "ws_connections_active", "Currently open WebSocket connections, per tenant", ["tenant_id"]
)

ws_events_forwarded_total = Counter(
    "ws_events_forwarded_total", "Realtime events forwarded from Redis Pub/Sub to WebSocket clients",
    ["tenant_id", "event_type"],
)


class PrometheusMiddleware:
    """Plain ASGI middleware — deliberately not Starlette's BaseHTTPMiddleware,
    which buffers the response body; this only needs the status code and timing,
    so a raw ASGI `send` wrapper avoids that overhead entirely."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        path = scope["path"]
        start = time.perf_counter()
        status_code_holder = {"code": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_code_holder["code"] = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)

        duration = time.perf_counter() - start
        http_requests_total.labels(method=method, path=path, status_code=status_code_holder["code"]).inc()
        http_request_duration_seconds.labels(method=method, path=path).observe(duration)
