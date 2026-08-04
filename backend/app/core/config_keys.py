"""Single source of truth for valid tenant_settings keys + validators (plan
§8) — referenced by the settings router below; the AI workers' own copy
(ai-worker/worker/common/tenant_settings_cache.py's _ENV_FALLBACKS) mirrors
the same key names and env-var fallback convention independently, since the
two codebases don't share a runtime dependency on each other."""

from typing import Any, Callable


def _range_validator(lo: float, hi: float) -> Callable[[Any], None]:
    def _validate(v: Any) -> None:
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not (lo <= v <= hi):
            raise ValueError(f"must be a number in [{lo}, {hi}]")

    return _validate


def _positive_int_validator(v: Any) -> None:
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise ValueError("must be a non-negative integer")


def _bool_validator(v: Any) -> None:
    if not isinstance(v, bool):
        raise ValueError("must be a boolean (true or false)")


def _choice_validator(*allowed: str) -> Callable[[Any], None]:
    """For settings whose value is one of a fixed set of strings — e.g. what
    the vehicle decision engine should do with an unrecognised plate. Keeps an
    invalid choice out of the database instead of letting it surface later as
    a ValueError deep inside a gate-opening decision."""

    def _validate(v: Any) -> None:
        if v not in allowed:
            raise ValueError(f"must be one of: {', '.join(allowed)}")

    return _validate


SETTING_VALIDATORS: dict[str, Callable[[Any], None]] = {
    "lpr.confidence_threshold": _range_validator(0.0, 1.0),
    "face.match_threshold": _range_validator(0.0, 1.0),
    "intrusion.breach_cooldown_seconds": _positive_int_validator,
    "evidence.retention_days": _positive_int_validator,
    # Phase 3
    "ppe.confidence_threshold": _range_validator(0.0, 1.0),
    "crowd.alert_threshold_ratio": _range_validator(0.0, 1.0),
    "crowd.breach_cooldown_seconds": _positive_int_validator,
    "fire_smoke.confidence_threshold": _range_validator(0.0, 1.0),
    "weapon.confidence_threshold": _range_validator(0.0, 1.0),
    "behavior.loitering_dwell_seconds": _positive_int_validator,
    "behavior.breach_cooldown_seconds": _positive_int_validator,
    # Phase 5 AI modules
    "tampering.score_threshold": _range_validator(0.0, 1.0),
    "abandoned.dwell_seconds": _positive_int_validator,
    "fall.confidence_threshold": _range_validator(0.0, 1.0),
    # Tier 2 — 2FA enforcement policy
    "2fa.required": _bool_validator,
    "2fa.grace_hours": _positive_int_validator,
    # Gap 85 — continuous recording retention
    "recording.retention_days": _positive_int_validator,
    # ShiftSecure Phase 2A — attendance hardening
    "attendance.geofence_radius_meters": _positive_int_validator,
    "attendance.late_grace_minutes": _positive_int_validator,
    "attendance.overtime_threshold_minutes": _positive_int_validator,
    # ShiftSecure Phase 2D — check-in/out selfie liveness
    "attendance.liveness_min_score": _range_validator(0.0, 1.0),
    # ShiftSecure Phase 2B — roster auto-scheduler
    "roster.max_consecutive_days": _positive_int_validator,
    "roster.min_rest_hours": _positive_int_validator,
    # Vehicle access decision engine (services/decision_engine.py). Defaults
    # live in that module's DEFAULTS dict; these only validate overrides.
    # Note this is a HIGHER bar than lpr.confidence_threshold above: that one
    # governs whether a plate read is recorded at all, this one governs
    # whether it is trusted enough to open a barrier unattended.
    "access.auto_open_min_confidence": _range_validator(0.0, 1.0),
    "access.unknown_plate_action": _choice_validator("require_operator", "block", "alarm"),
    "access.expired_action": _choice_validator("require_operator", "block", "alarm"),
}
