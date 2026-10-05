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


# ─── The default limit on reads ──────────────────────────────────────────────
#
# The test that stood here sent five requests and checked none was refused. It
# could not fail, and the limit it was written for was never applied to anything
# (dependencies/rate_limit.py says why). These send one more than the limit.

import uuid

import pytest
from limits import parse

from app.core.security import create_access_token
from app.dependencies import rate_limit

ROUTE = "/api/v1/cameras"                                  # an ordinary read
OVERLAY = f"/api/v1/cameras/{uuid.uuid4()}/overlay"        # one the live wall polls every two seconds


def _as(user: uuid.UUID | None = None) -> dict:
    """Headers of a signed-in user. No such user or tenant exists; the limit is
    counted before anything is looked up, which is all these tests need."""
    return {"Authorization": f"Bearer {create_access_token(str(user or uuid.uuid4()), str(uuid.uuid4()), 2)}"}


@pytest.fixture
def five_a_minute(monkeypatch):
    """The real limits are in the hundreds. The arithmetic is the same at five."""
    _flush_limiter_keys()
    monkeypatch.setattr(rate_limit, "READ_LIMIT", parse("5/minute"))
    monkeypatch.setattr(rate_limit, "MEDIA_LIMIT", parse("8/minute"))
    yield
    _flush_limiter_keys()


async def test_one_read_too_many_is_refused_and_says_when_to_come_back(app_client, five_a_minute):
    me = _as()
    answers = [await app_client.get(ROUTE, headers=me) for _ in range(6)]
    assert [a.status_code for a in answers[:5]].count(429) == 0
    refused = answers[5]
    assert refused.status_code == 429
    assert "Too many requests" in refused.json()["detail"]
    assert 1 <= int(refused.headers["retry-after"]) <= 61


async def test_the_limit_is_each_persons_own_and_each_routes_own(app_client, five_a_minute):
    """Behind the web proxy every browser has the same address. A limit counted
    by address would let one busy operator lock the whole control room out."""
    me, colleague = _as(), _as()
    for _ in range(6):
        mine = await app_client.get(ROUTE, headers=me)
    assert mine.status_code == 429
    assert (await app_client.get(ROUTE, headers=colleague)).status_code != 429, "somebody else, same address"
    assert (await app_client.get("/api/v1/sites", headers=me)).status_code != 429, "the same person, another route"


async def test_nothing_that_changes_something_is_ever_refused_by_the_default(app_client, five_a_minute):
    """An abort, an SOS or an acknowledgement turned away for being the sixth
    request in a minute would be worse than the load it saved."""
    me = _as()
    url = f"/api/v1/drone-patrols/{uuid.uuid4()}/abort"
    statuses = {(await app_client.post(url, headers=me, json={"reason": "now"})).status_code for _ in range(12)}
    assert 429 not in statuses, statuses


async def test_video_and_pictures_have_the_larger_allowance(app_client, five_a_minute):
    me = _as()
    answers = [(await app_client.get(OVERLAY, headers=me)).status_code for _ in range(9)]
    assert 429 not in answers[:8], "the live wall's own polling was refused"
    assert answers[8] == 429


async def test_a_token_in_the_address_counts_as_its_user(app_client, five_a_minute):
    """The video routes take their token as ?token=, because a <video> element
    cannot send a header. They must be counted per user too, or sixteen cells of
    one wall would all be the same anonymous caller."""
    cam, stream = uuid.uuid4(), uuid.uuid4()
    url = f"/api/v1/cameras/{cam}/streams/{stream}/hls/index.m3u8"

    def token(user):
        return create_access_token(str(user), str(uuid.uuid4()), 2)

    first, second = uuid.uuid4(), uuid.uuid4()
    mine = [(await app_client.get(url, params={"token": token(first)})).status_code for _ in range(9)]
    assert 429 not in mine[:8] and mine[8] == 429
    assert (await app_client.get(url, params={"token": token(second)})).status_code != 429


async def test_without_a_valid_token_the_caller_is_the_client_address(app_client, five_a_minute):
    there = {"X-Forwarded-For": "203.0.113.7"}
    elsewhere = {"X-Forwarded-For": "203.0.113.8"}
    forged = {"Authorization": "Bearer not-a-real-token", **there}
    answers = [(await app_client.get(ROUTE, headers=there)).status_code for _ in range(5)]
    assert answers == [401] * 5
    # A made-up token is not a fresh allowance: it is the same address.
    assert (await app_client.get(ROUTE, headers=forged)).status_code == 429
    assert (await app_client.get(ROUTE, headers=elsewhere)).status_code == 401


async def test_a_counter_that_cannot_be_reached_lets_the_request_through(app_client, five_a_minute, monkeypatch):
    def unreachable(*args, **kwargs):
        raise ConnectionError("redis is gone")

    monkeypatch.setattr(rate_limit.limiter.limiter, "hit", unreachable)
    me = _as()
    assert {(await app_client.get(ROUTE, headers=me)).status_code for _ in range(8)} != {429}
    assert 429 not in {(await app_client.get(ROUTE, headers=me)).status_code for _ in range(3)}


def test_the_real_limits_leave_room_for_the_live_wall():
    """The numbers, against what they were measured from: sixteen cells, each
    asking its overlay — and its playlist, and a segment — every two seconds."""
    from app.core.config import settings

    wall = 16 * (60 // 2)
    assert parse(settings.API_MEDIA_RATE_LIMIT).amount >= 2 * wall
    assert parse(settings.API_READ_RATE_LIMIT).amount >= 300
    assert rate_limit.limit_for("/api/v1/cameras/{camera_id}/overlay") is rate_limit.MEDIA_LIMIT
    assert rate_limit.limit_for("/api/v1/cameras/{camera_id}/streams/{stream_id}/hls/{segment}") is rate_limit.MEDIA_LIMIT
    assert rate_limit.limit_for("/api/v1/evidence/{evidence_id}/image") is rate_limit.MEDIA_LIMIT
    assert rate_limit.limit_for("/api/v1/cameras") is rate_limit.READ_LIMIT
    assert rate_limit.limit_for("/api/v1/drone-patrols/{session_id}/report/pdf") is rate_limit.READ_LIMIT


def test_every_route_carries_the_default():
    """It is a dependency of the application, so a router included tomorrow has
    it without anyone adding it."""
    from app.main import app

    assert rate_limit.default_rate_limit in [d.dependency for d in app.router.dependencies]
