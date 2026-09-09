"""Catch the failure on its way out and hand it to the collector.

WHAT IT CATCHES. Unhandled exceptions, and 5xx responses returned rather than
raised because a handler downstream already turned the failure into a response.
Not 4xx: a 404 or a 403 is the application working, and a console full of them
is a console nobody reads.

WHERE THE TENANT COMES FROM. Off the already-decoded token on request.state,
not by re-authenticating. This runs after the request has failed, and asking
the database who the caller was — on the connection that just broke — is a
second thing to go wrong at the worst moment. A failure before authentication
simply has no tenant, which is why that column is nullable.
"""
from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.services import error_collector


def _token_of(request: Request):
    return getattr(request.state, "token_payload", None)


class ErrorCaptureMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Correlates the console row with the container log line for the same
        # request, which is the first thing anyone wants when a stack trace is
        # not enough.
        request_id = uuid.uuid4().hex[:16]
        request.state.request_id = request_id

        from app.db.session import AsyncSessionLocal

        token = None
        try:
            response = await call_next(request)
        except Exception as exc:
            token = _token_of(request)
            await error_collector.record(
                AsyncSessionLocal,
                error_type=type(exc).__name__,
                message=str(exc),
                method=request.method,
                path=request.url.path,
                status_code=500,
                tenant_id=getattr(token, "tenant_id", None),
                user_id=getattr(token, "user_id", None),
                request_id=request_id,
                exc=exc,
            )
            raise

        if response.status_code >= 500:
            token = _token_of(request)
            await error_collector.record(
                AsyncSessionLocal,
                error_type="HTTPError",
                message=f"HTTP {response.status_code}",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                tenant_id=getattr(token, "tenant_id", None),
                user_id=getattr(token, "user_id", None),
                request_id=request_id,
            )
        response.headers["X-Request-Id"] = request_id
        return response
