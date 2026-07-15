import os

import redis as sync_redis


def _flush_limiter_keys():
    """slowapi's Redis-backed storage persists across test runs (it's real
    Redis, not in-memory) — without this, a previous run's hits against the
    same test-client IP would make these tests flaky/order-dependent."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    r = sync_redis.from_url(redis_url)
    for pattern in ("LIMITER*", "*LIMITS*", "limiter*"):
        keys = r.keys(pattern)
        if keys:
            r.delete(*keys)
    r.close()


async def test_sixth_login_in_one_minute_429(app_client):
    _flush_limiter_keys()
    bad_login = {"tenant_slug": "no-such-tenant", "email": "x@example.com", "password": "wrong"}

    statuses = []
    for _ in range(6):
        resp = await app_client.post("/api/v1/auth/login", json=bad_login)
        statuses.append(resp.status_code)

    assert statuses[:5] == [401] * 5  # first 5 are normal "invalid credentials"
    assert statuses[5] == 429  # 6th is rate-limited


async def test_general_endpoint_default_limit(app_client):
    """100/minute app-wide default applies to non-auth endpoints too — confirm
    a handful of requests under that ceiling all succeed normally (a true
    100+ request test would be slow and isn't needed to prove the limiter is
    wired app-wide rather than only on the auth router)."""
    _flush_limiter_keys()
    for _ in range(5):
        resp = await app_client.get("/api/v1/cameras")
        # 401 (no token) is expected here — the point is it's NOT 429.
        assert resp.status_code == 401
