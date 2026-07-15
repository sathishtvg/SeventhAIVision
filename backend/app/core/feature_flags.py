"""Gap 15 — Feature flag registry for Seventh AI Vision.

Lightweight, zero-dependency feature flag system with a three-tier precedence chain:
  1. Environment variable override  (FEATURE_<NAME_UPPER>=true/false/...)
  2. Hard-coded default in FLAG_DEFINITIONS
  3. Caller-supplied default (for unknown flags only)

Tenant-specific overrides (tier 0) are reserved for a future DB-level implementation
via tenant_settings (key: "feature.<flag_name>"). The `tenant_id` param on
`is_enabled` and `get_flag_value` is already part of the public API to avoid a
breaking change when that tier is added.

Usage:
    from app.core.feature_flags import is_enabled, get_flag_value

    if is_enabled("live_wall"):
        ...
    if is_enabled("scim_provisioning", tenant_id=current_tenant_id):
        ...
    max_cams = get_flag_value("max_cameras_per_tenant", default=100)

Adding a flag: add an entry to FLAG_DEFINITIONS and add the matching JSON entry
to config/feature_flags.json. No restart required for env-var overrides.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_BOOL = "boolean"
_STR = "string"
_NUM = "number"
_JSON = "json"

# ── Flag definitions (single source of truth) ─────────────────────────────────
FLAG_DEFINITIONS: dict[str, dict] = {
    # ── UI / UX features
    "live_wall": {
        "type": _BOOL, "default": True,
        "description": "Multi-camera LiveWall page with up to 4x4 stream grid",
    },
    "electron_desktop": {
        "type": _BOOL, "default": True,
        "description": "Windows Electron desktop app packaging and first-run wizard",
    },
    "dark_mode_only": {
        "type": _BOOL, "default": False,
        "description": "Force dark glassmorphism theme and disable light-mode toggle",
    },
    "enhanced_dashboard": {
        "type": _BOOL, "default": True,
        "description": "Phase 10 enhanced dashboard with site selector, sparklines, health strip",
    },

    # ── AI / Detection features
    "face_enrollment_ui": {
        "type": _BOOL, "default": True,
        "description": "Face enrollment dialog in Watchlists page (requires insightface model)",
    },
    "stream_validation": {
        "type": _BOOL, "default": True,
        "description": "Test Connection button for RTSP URL validation before saving",
    },
    "pgvector_face_matching": {
        "type": _BOOL, "default": True,
        "description": "Use pgvector ANN for face matching instead of brute-force Python",
    },
    "gpu_acceleration": {
        "type": _BOOL, "default": False,
        "description": "Enable GPU device for AI workers (requires NVIDIA Container Toolkit)",
    },

    # ── Enterprise integration features
    "scim_provisioning": {
        "type": _BOOL, "default": True,
        "description": "SCIM 2.0 user/group provisioning endpoint (/api/v1/scim/v2)",
    },
    "webhooks": {
        "type": _BOOL, "default": True,
        "description": "Outbound webhook subscriptions for platform events",
    },
    "sso_saml": {
        "type": _BOOL, "default": False,
        "description": "SAML 2.0 / OAuth2 single sign-on (requires SSO provider config)",
    },
    "platform_licensing": {
        "type": _BOOL, "default": True,
        "description": "Per-tenant product licensing API (/api/v1/platform)",
    },

    # ── Observability features
    "opentelemetry_tracing": {
        "type": _BOOL, "default": False,
        "description": "OpenTelemetry distributed tracing (requires otel-collector sidecar)",
    },
    "prometheus_metrics": {
        "type": _BOOL, "default": True,
        "description": "Expose /metrics endpoint for Prometheus scraping",
    },

    # ── Storage / data features
    "s3_evidence_storage": {
        "type": _BOOL, "default": False,
        "description": "Route evidence files through MinIO/S3 (STORAGE_BACKEND=s3)",
    },
    "stream_recording": {
        "type": _BOOL, "default": True,
        "description": "Per-stream MP4 recording (start/stop via API, stored in recordings_data volume)",
    },

    # ── Configurable limits (non-boolean flags)
    "max_cameras_per_tenant": {
        "type": _NUM, "default": 0,
        "description": "Maximum cameras per tenant (0 = unlimited); enforced at camera:create",
    },
    "max_webhook_endpoints_per_tenant": {
        "type": _NUM, "default": 20,
        "description": "Maximum active webhook subscriptions per tenant",
    },
    "max_watchlist_faces_per_tenant": {
        "type": _NUM, "default": 0,
        "description": "Maximum face watchlist entries per tenant (0 = unlimited)",
    },
}

# Path to the JSON catalog (mirrors FLAG_DEFINITIONS, kept for tooling/docs)
_PROJECT_ROOT = Path(__file__).parents[3]   # /app/backend/app/core/ → /app/
FLAG_CATALOG_PATH: Path = _PROJECT_ROOT / "config" / "feature_flags.json"


# ── Resolution helpers ─────────────────────────────────────────────────────────

def _env_key(flag_name: str) -> str:
    """Convert 'live_wall' → 'FEATURE_LIVE_WALL'."""
    return f"FEATURE_{flag_name.upper()}"


def _parse_bool(value: str) -> bool:
    return value.lower() not in ("false", "0", "no", "off", "")


def _resolve_from_env(flag_name: str, flag_def: dict) -> Any | None:
    """Return the env-var override value, or None if the env var is not set."""
    raw = os.getenv(_env_key(flag_name))
    if raw is None:
        return None
    flag_type = flag_def.get("type", _BOOL)
    if flag_type == _BOOL:
        return _parse_bool(raw)
    if flag_type == _NUM:
        try:
            return int(raw) if "." not in raw else float(raw)
        except ValueError:
            return flag_def["default"]
    if flag_type == _JSON:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return flag_def["default"]
    return raw  # string type


def get_flag_value(
    flag_name: str,
    tenant_id: str | None = None,
    default: Any = None,
) -> Any:
    """Return the resolved value for a feature flag.

    Precedence (highest → lowest):
        1. Environment variable  (FEATURE_<FLAG_NAME_UPPER>)
        2. FLAG_DEFINITIONS default
        3. Caller-supplied `default` (only when flag_name not in FLAG_DEFINITIONS)
    """
    flag_def = FLAG_DEFINITIONS.get(flag_name)
    if flag_def is None:
        env_value = _resolve_from_env(flag_name, {"type": _BOOL, "default": default})
        return env_value if env_value is not None else default

    env_value = _resolve_from_env(flag_name, flag_def)
    if env_value is not None:
        return env_value

    return flag_def["default"]


def is_enabled(flag_name: str, tenant_id: str | None = None) -> bool:
    """Return True if a boolean feature flag is enabled.

    Unknown flags default to False (fail-closed — missing flag names never grant access).
    """
    if flag_name not in FLAG_DEFINITIONS:
        return False
    return bool(get_flag_value(flag_name, tenant_id=tenant_id))


def all_flags() -> list[dict]:
    """Return all flag definitions as a list with env-key metadata (for admin UI / API)."""
    return [
        {"name": name, **defn, "env_key": _env_key(name)}
        for name, defn in FLAG_DEFINITIONS.items()
    ]
