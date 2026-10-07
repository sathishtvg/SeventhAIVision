"""A limit of its own for a route that is heavier than a read.

The default limit (dependencies/rate_limit.py) counts reads only, so that an
operator's abort or a guard's SOS is never turned away. A few requests that
change nothing still cost a great deal — building an archive of evidence, say —
and are counted here, per person, on the same counter and with the same rule
when the counter cannot be reached: the request goes ahead.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from fastapi import HTTPException, Request
from limits import parse

from app.core.limiter import limiter
from app.dependencies.rate_limit import STORE_TIMEOUT_S, caller_of


def paced(limit: str, bucket: str, refusal: str) -> Callable[[Request], Awaitable[None]]:
    """A dependency allowing `limit` (as "6/minute") requests per person in `bucket`."""
    item = parse(limit)

    async def check(request: Request) -> None:
        if not limiter.enabled:
            return
        try:
            allowed = await asyncio.wait_for(
                asyncio.to_thread(limiter.limiter.hit, item, caller_of(request), bucket), STORE_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — the counter being down is never the caller's problem
            return
        if not allowed:
            raise HTTPException(429, refusal, headers={"Retry-After": "60"})

    return check
