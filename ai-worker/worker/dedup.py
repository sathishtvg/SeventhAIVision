"""Redis-backed breach de-duplication for intrusion detection (plan §6c) — a
person standing in a zone across many consecutive frames must not spam one
alert per frame. Redis-backed (not in-memory) so it stays correct if this
module is ever scaled to N>1 replicas; reusable pattern for future modules
with the same "don't re-alert on the same ongoing condition" need."""

import os
from datetime import datetime

import redis

DEFAULT_COOLDOWN_SECONDS = int(os.environ.get("INTRUSION_BREACH_COOLDOWN_SECONDS", "60"))


class BreachTracker:
    def __init__(self, redis_client: redis.Redis):
        self.r = redis_client

    def _key(self, tenant_id, camera_id, zone_id) -> str:
        return f"intrusion:active_breach:{tenant_id}:{camera_id}:{zone_id}"

    def register(self, tenant_id, camera_id, zone_id, detected_at: datetime, cooldown_seconds: int | None = None) -> tuple[bool, float]:
        """Returns (is_new_breach, dwell_time_seconds).

        First call for a camera+zone: SETNX wins -> new breach, dwell=0, sets
        TTL=cooldown. Subsequent calls within the cooldown window: SETNX fails
        -> not new, but the original start timestamp is read to compute a
        running dwell time, and the TTL is extended (sliding window) so dwell
        keeps accumulating for as long as the person remains, while duplicate
        alerts stay suppressed.
        """
        cooldown = cooldown_seconds if cooldown_seconds is not None else DEFAULT_COOLDOWN_SECONDS
        key = self._key(tenant_id, camera_id, zone_id)
        now_iso = detected_at.isoformat()

        was_set = self.r.set(key, now_iso, nx=True, ex=cooldown)
        if was_set:
            return True, 0.0

        start_raw = self.r.get(key)
        self.r.expire(key, cooldown)  # slide the window forward
        if start_raw is None:
            # Race: key expired between the SETNX miss and this GET — treat as new.
            self.r.set(key, now_iso, ex=cooldown)
            return True, 0.0

        start_iso = start_raw.decode() if isinstance(start_raw, bytes) else start_raw
        start_dt = datetime.fromisoformat(start_iso)
        dwell = (detected_at - start_dt).total_seconds()
        return False, dwell
