"""The default rate limit — applied to every read, per caller and per route.

WHY THIS EXISTS BESIDE slowapi
    core/limiter.py configures `default_limits=["100/minute"]` and main.py adds
    slowapi's middleware to enforce it. It never has. The middleware finds a
    request's handler by walking `app.routes`, where an included router is now
    one object without an `.endpoint`, so it finds none, and a route it cannot
    find it exempts. Only the handful of endpoints with an explicit
    `@limiter.limit` were ever limited. (Measured: 115 requests to one route in a
    few seconds, none refused.) Just as well, in one respect — 100 a minute per
    address would have broken the product the day it worked: see the numbers
    below.

    So the default is applied here, as an application-level dependency, which
    runs for every route however it was included. The explicit decorators are
    untouched and still apply on top.

WHAT IS COUNTED
    Reads only — GET and HEAD. A request that changes something is never refused
    by the default: an operator's abort, a guard's SOS or an alarm acknowledgement
    turned away with "too many requests" would be worse than the load it saved.
    The sensitive writes carry their own explicit limits (sign-in, alarm ingest,
    the drone gateway).

WHO IS COUNTED
    The signed-in user, when the request carries a token that verifies — in the
    Authorization header, or in `?token=` as the video routes take it. Not the
    address: behind the web proxy every browser shares one, and an office behind
    one address is many people. Anything without a valid token is counted by
    client address, as the rest of the platform resolves it.

HOW MUCH
    Per caller and per route, so one busy screen cannot starve another.

      API_READ_RATE_LIMIT   300/minute   every read
      API_MEDIA_RATE_LIMIT  1200/minute  video, overlays and stored pictures

    The numbers come from what the screens really do. Ordinary polling asks any
    one route a handful of times a minute. The live wall is the exception: each
    of its sixteen cells polls its detection overlay every two seconds and pulls
    an HLS playlist and a segment about as often — 480 a minute on each of those
    routes, from one operator doing nothing wrong. 1,200 is two full walls with
    room to spare; 300 is fifty times what a polling screen needs.

IF THE COUNTER CANNOT BE REACHED
    The request is let through. The limit lives in Redis; a Redis that is down or
    slow must not take every read in the product down with it.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from fastapi import HTTPException
from limits import parse
from starlette.requests import HTTPConnection

from app.core.config import settings
from app.core.limiter import limiter
from app.core.security import InvalidTokenError, decode_access_token
from app.dependencies.tenant import _client_ip

logger = logging.getLogger(__name__)

READ_LIMIT = parse(settings.API_READ_RATE_LIMIT)
MEDIA_LIMIT = parse(settings.API_MEDIA_RATE_LIMIT)

#: Routes a single screen legitimately asks many times a minute: HLS playlists
#: and segments, the live detection overlay, and stored pictures, which a gallery
#: loads a page at a time. Matched against the route's path template.
HIGH_FREQUENCY = re.compile(r"/hls/|/overlay$|/(file|image|photo|snapshot)$|/photo/\{[a-z_]+\}$|/qr\.png$")

#: How long to wait on the counter before letting the request through.
STORE_TIMEOUT_S = 0.5

_last_store_warning = 0.0


def caller_of(conn: HTTPConnection) -> str:
    """Who to count this request against: the user a valid token names, else the
    client's address. A token that does not verify is not believed — otherwise a
    stream of made-up tokens would be a stream of fresh allowances."""
    token = None
    header = conn.headers.get("authorization", "")
    if header[:7].lower() == "bearer ":
        token = header[7:].strip()
    if not token:
        token = conn.query_params.get("token")
    if token:
        try:
            return f"user:{decode_access_token(token)['sub']}"
        except (InvalidTokenError, KeyError):
            pass
    return f"addr:{_client_ip(conn)}"


def limit_for(template: str):
    return MEDIA_LIMIT if HIGH_FREQUENCY.search(template) else READ_LIMIT


async def default_rate_limit(conn: HTTPConnection) -> None:
    """Application-level dependency: see the module docstring."""
    global _last_store_warning
    if conn.scope["type"] != "http" or conn.scope.get("method") not in ("GET", "HEAD") or not limiter.enabled:
        return
    route = conn.scope.get("route")
    template = getattr(route, "path", None) or conn.scope.get("path", "")
    item, caller = limit_for(template), caller_of(conn)
    try:
        allowed = await asyncio.wait_for(
            asyncio.to_thread(limiter.limiter.hit, item, caller, template), STORE_TIMEOUT_S)
    except Exception as exc:  # the store is down, slow or misbehaving: never the caller's problem
        now = time.monotonic()
        if now - _last_store_warning > 60:
            _last_store_warning = now
            logger.warning("default rate limit not applied — the counter could not be reached: %s",
                           type(exc).__name__)
        return
    if allowed:
        return
    try:
        reset = limiter.limiter.get_window_stats(item, caller, template).reset_time
        wait = max(1, int(reset - time.time()) + 1)
    except Exception:
        wait = 60
    raise HTTPException(
        429, "Too many requests. This is asked for more often than any screen needs; "
             f"wait {wait} seconds and try again.",
        headers={"Retry-After": str(wait)})
