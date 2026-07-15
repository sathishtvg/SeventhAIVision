"""API deprecation middleware — Gap 20.

Adds RFC 8594 Sunset and RFC 9745 Deprecation headers to responses
for API versions that have been deprecated or are approaching sunset.

Loaded from: config/api-deprecation-policy.yml (relative to project root).

Header behaviour per version status:
  active    → no extra headers
  deprecated → Deprecation: <date> | true   (RFC 9745)
               Sunset: <RFC 7231 date>       (RFC 8594, when sunset_date is set)
               Warning: 299 - "..."          (when within warn_within_days of sunset)
  sunset     → same as deprecated (version should no longer be served, but if it
               is reachable the headers make the situation unambiguous to clients)
"""

from __future__ import annotations

import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Resolve policy path relative to this file's location.
# Layout: backend/app/middleware/deprecation.py → root = parents[3]
_POLICY_PATH = Path(__file__).parents[3] / "config" / "api-deprecation-policy.yml"

# HTTP-date format per RFC 7231 §7.1.1.1
_RFC7231_FMT = "%a, %d %b %Y %H:%M:%S GMT"

DEPRECATED_STATUSES = {"deprecated", "sunset"}


def _load_policy(path: Path = _POLICY_PATH) -> dict:
    """Load and return the deprecation policy YAML. Returns {} on missing file."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _version_from_path(path: str) -> Optional[str]:
    """Extract the API version slug from a request path.

    Examples:
        /api/v1/cameras  → 'v1'
        /api/v2/alerts   → 'v2'
        /health          → None
        /ws/live         → None
    """
    parts = path.lstrip("/").split("/")
    if len(parts) >= 2 and parts[0] == "api" and parts[1].startswith("v"):
        return parts[1]
    return None


class ApiDeprecationMiddleware(BaseHTTPMiddleware):
    """Starlette/FastAPI middleware that injects version deprecation headers.

    Attach to the FastAPI app in main.py:
        from backend.app.middleware.deprecation import ApiDeprecationMiddleware
        app.add_middleware(ApiDeprecationMiddleware)
    """

    def __init__(self, app, policy: Optional[dict] = None) -> None:
        super().__init__(app)
        self._policy: dict = policy if policy is not None else _load_policy()
        # Build a quick-lookup map: version slug → policy entry
        self._version_map: dict[str, dict] = {
            v["version"]: v
            for v in self._policy.get("versions", [])
            if isinstance(v, dict) and "version" in v
        }
        sunset_cfg = self._policy.get("sunset_header", {})
        deprecation_cfg = self._policy.get("deprecation_header", {})
        link_cfg = self._policy.get("link_header", {})
        self._sunset_enabled: bool = bool(sunset_cfg.get("enabled", True))
        self._warn_within_days: int = int(sunset_cfg.get("warn_within_days", 30))
        self._deprecation_enabled: bool = bool(deprecation_cfg.get("enabled", True))
        self._deprecation_fallback: str = str(deprecation_cfg.get("fallback_value", "true"))
        self._link_enabled: bool = bool(link_cfg.get("enabled", False))
        self._migration_url: str = str(link_cfg.get("migration_guide_url", ""))

    async def dispatch(self, request: Request, call_next) -> Response:
        response: Response = await call_next(request)

        api_version = _version_from_path(request.url.path)
        if not api_version:
            return response

        ver_entry = self._version_map.get(api_version)
        if not ver_entry:
            return response

        status = ver_entry.get("status", "active")
        if status not in DEPRECATED_STATUSES:
            return response

        deprecated_on: Optional[str] = ver_entry.get("deprecated_on")
        sunset_date_str: Optional[str] = ver_entry.get("sunset_date")

        # ── RFC 9745 — Deprecation header ────────────────────────────────────
        if self._deprecation_enabled:
            if deprecated_on:
                response.headers["Deprecation"] = deprecated_on
            else:
                response.headers["Deprecation"] = self._deprecation_fallback

        # ── RFC 8594 — Sunset header ──────────────────────────────────────────
        if self._sunset_enabled and sunset_date_str:
            try:
                sunset_dt = datetime.fromisoformat(sunset_date_str).replace(
                    tzinfo=timezone.utc
                )
                response.headers["Sunset"] = sunset_dt.strftime(_RFC7231_FMT)

                # Warning header when sunset is imminent
                days_remaining = (sunset_dt - datetime.now(timezone.utc)).days
                if 0 <= days_remaining <= self._warn_within_days:
                    response.headers["Warning"] = (
                        f'299 - "API {api_version} sunsets on '
                        f'{sunset_dt.strftime(_RFC7231_FMT)}"'
                    )
            except (ValueError, TypeError):
                pass  # malformed date — skip rather than crash

        # ── RFC 8288 — Link header for migration guide ────────────────────────
        if self._link_enabled and self._migration_url:
            response.headers["Link"] = (
                f'<{self._migration_url}>; rel="deprecation"; type="text/html"'
            )

        return response
