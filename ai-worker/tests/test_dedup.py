import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import redis

from worker.dedup import BreachTracker

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://redis:6379/1")


def _fresh_tracker() -> tuple[BreachTracker, redis.Redis]:
    r = redis.from_url(REDIS_URL)
    return BreachTracker(r), r


def test_first_breach_is_new():
    tracker, r = _fresh_tracker()
    tenant_id, camera_id, zone_id = uuid4(), uuid4(), uuid4()
    try:
        is_new, dwell = tracker.register(tenant_id, camera_id, zone_id, datetime.now(timezone.utc), cooldown_seconds=5)
        assert is_new is True
        assert dwell == 0.0
    finally:
        r.delete(tracker._key(tenant_id, camera_id, zone_id))


def test_repeat_within_cooldown_suppressed():
    tracker, r = _fresh_tracker()
    tenant_id, camera_id, zone_id = uuid4(), uuid4(), uuid4()
    try:
        now = datetime.now(timezone.utc)
        tracker.register(tenant_id, camera_id, zone_id, now, cooldown_seconds=5)
        is_new, dwell = tracker.register(tenant_id, camera_id, zone_id, now + timedelta(seconds=2), cooldown_seconds=5)
        assert is_new is False
        assert 1.5 < dwell < 2.5
    finally:
        r.delete(tracker._key(tenant_id, camera_id, zone_id))


def test_cooldown_expiry_allows_new_breach():
    tracker, r = _fresh_tracker()
    tenant_id, camera_id, zone_id = uuid4(), uuid4(), uuid4()
    try:
        now = datetime.now(timezone.utc)
        tracker.register(tenant_id, camera_id, zone_id, now, cooldown_seconds=1)
        import time

        time.sleep(1.3)  # let the real Redis TTL actually expire
        is_new, dwell = tracker.register(tenant_id, camera_id, zone_id, now + timedelta(seconds=2), cooldown_seconds=1)
        assert is_new is True
        assert dwell == 0.0
    finally:
        r.delete(tracker._key(tenant_id, camera_id, zone_id))


def test_sliding_window_extends_on_continued_presence():
    tracker, r = _fresh_tracker()
    tenant_id, camera_id, zone_id = uuid4(), uuid4(), uuid4()
    key = tracker._key(tenant_id, camera_id, zone_id)
    try:
        now = datetime.now(timezone.utc)
        tracker.register(tenant_id, camera_id, zone_id, now, cooldown_seconds=3)
        ttl_before = r.ttl(key)
        import time

        time.sleep(1.5)
        tracker.register(tenant_id, camera_id, zone_id, now + timedelta(seconds=1.5), cooldown_seconds=3)
        ttl_after = r.ttl(key)
        # TTL should have been pushed back out close to the full cooldown again,
        # not kept counting down from the original SETNX.
        assert ttl_after > ttl_before - 1
    finally:
        r.delete(key)
