"""Admin-editable per-tenant configuration, read side (plan §8). 30s in-process
TTL cache, refreshed lazily on read — no background poller, so an idle tenant
(no frames being processed for it) costs zero DB work for its settings. 30s
staleness is acceptable: these are AI tuning knobs, not security controls.

psycopg3 decodes jsonb columns to native Python types automatically (verified
directly against the real database: a jsonb '0.75' comes back as a float, a
jsonb object as a dict — no manual json.loads() needed)."""

import os
import time
from threading import Lock
from typing import Any

_CACHE_TTL_SECONDS = 30
_cache: dict[tuple[str, str], tuple[Any, float]] = {}
_lock = Lock()
_MISSING = object()

# env var fallback used when no tenant_settings row exists for a key (plan §8)
_ENV_FALLBACKS: dict[str, tuple[str, type, Any]] = {
    "lpr.confidence_threshold": ("LPR_CONFIDENCE_THRESHOLD", float, 0.55),
    "face.match_threshold": ("FACE_MATCH_THRESHOLD", float, 0.6),
    "intrusion.breach_cooldown_seconds": ("INTRUSION_BREACH_COOLDOWN_SECONDS", int, 60),
    "evidence.retention_days": ("EVIDENCE_RETENTION_DAYS", int, 90),
    # Phase 3 AI modules
    "ppe.confidence_threshold": ("PPE_CONFIDENCE_THRESHOLD", float, 0.5),
    "crowd.alert_threshold_ratio": ("CROWD_ALERT_THRESHOLD_RATIO", float, 0.8),
    "crowd.breach_cooldown_seconds": ("CROWD_BREACH_COOLDOWN_SECONDS", int, 60),
    "fire_smoke.confidence_threshold": ("FIRE_SMOKE_CONFIDENCE_THRESHOLD", float, 0.45),
    "weapon.confidence_threshold": ("WEAPON_CONFIDENCE_THRESHOLD", float, 0.55),
    "behavior.loitering_dwell_seconds": ("BEHAVIOR_LOITERING_DWELL_SECONDS", int, 30),
    "behavior.breach_cooldown_seconds": ("BEHAVIOR_BREACH_COOLDOWN_SECONDS", int, 120),
    # Phase 5 AI modules
    "tampering.score_threshold": ("TAMPERING_SCORE_THRESHOLD", float, 0.5),
    "abandoned.dwell_seconds": ("ABANDONED_DWELL_SECONDS", float, 30.0),
    "fall.confidence_threshold": ("FALL_CONFIDENCE_THRESHOLD", float, 0.45),
}


def _env_fallback(setting_key: str, default: Any = _MISSING) -> Any:
    entry = _ENV_FALLBACKS.get(setting_key)
    if entry is None:
        if default is not _MISSING:
            return default
        raise KeyError(f"No env fallback registered for setting key {setting_key!r}")
    env_var, cast, fallback_default = entry
    raw = os.environ.get(env_var)
    return cast(raw) if raw is not None else fallback_default


def get_tenant_setting(conn, tenant_id, setting_key: str, default: Any = _MISSING) -> Any:
    """`conn` must already have its RLS tenant context set (i.e. called within
    a `with get_tenant_session(tenant_id) as conn:` block) — this function
    doesn't set it itself, to avoid a second implicit transaction boundary."""
    cache_key = (str(tenant_id), setting_key)
    now = time.monotonic()

    with _lock:
        cached = _cache.get(cache_key)
        if cached is not None and (now - cached[1]) < _CACHE_TTL_SECONDS:
            return cached[0]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT setting_value FROM tenant_settings WHERE tenant_id = %s AND setting_key = %s",
            (str(tenant_id), setting_key),
        )
        row = cur.fetchone()

    value = row[0] if row is not None else _env_fallback(setting_key, default)

    with _lock:
        _cache[cache_key] = (value, now)

    return value


def clear_cache() -> None:
    """Test-only: the cache is module-level/global, so tests need a way to
    reset it between cases instead of waiting out the 30s TTL."""
    with _lock:
        _cache.clear()
