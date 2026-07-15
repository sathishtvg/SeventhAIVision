"""Redis-backed rate limiting (plan §11) — same REDIS_URL already used for
Streams/Pub-Sub, no new infra. Keyed on IP for both tiers, not per-tenant/
user: IP is what an attacker actually controls, so it's what stops
credential-stuffing. A per-tenant billing-style *quota* is a separate,
explicitly out-of-scope concern, not rate limiting."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
    default_limits=["100/minute"],
)
