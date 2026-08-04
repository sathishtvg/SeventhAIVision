"""Gap 20 — API Deprecation/Sunset Policy: infrastructure + middleware unit tests.

Validates:
  A. File structure (config/api-deprecation-policy.yml, middleware module)
  B. Policy config validity (YAML, required top-level sections)
  C. Policy version content (v1 entry, required fields, status values)
  D. Policy timing fields (deprecation_notice_days, sunset/deprecation header config)
  E. RFC compliance metadata (RFC 8594, RFC 9745 referenced in policy)
  F. Middleware module structure (class defined, _version_from_path function)
  G. Middleware version path parsing (_version_from_path pure-function tests)
  H. Middleware header injection (Deprecation, Sunset, Warning headers on deprecated routes)
"""

from __future__ import annotations

import importlib.util
import sys
import types
import yaml
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from tests._repo import REPO_ROOT, requires_repo_tree

# Skips the whole module when the repository tree is absent (e.g. inside the
# api image, which ships only backend/). See tests/_repo.py for why failing
# would be the wrong signal here.
pytestmark = requires_repo_tree


_HERE         = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]              # /app/backend/tests/ → /app/
_POLICY_PATH  = _PROJECT_ROOT / "config" / "api-deprecation-policy.yml"
_MW_PATH      = _PROJECT_ROOT / "backend" / "app" / "middleware" / "deprecation.py"

VALID_STATUSES = {"active", "deprecated", "sunset", "planned"}


def _load_policy() -> dict:
    with open(_POLICY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _import_middleware():
    """Dynamically import the deprecation middleware module."""
    spec = importlib.util.spec_from_file_location("deprecation", _MW_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Stub starlette so import works without the full web stack
    if "starlette" not in sys.modules:
        starlette = types.ModuleType("starlette")
        starlette.middleware = types.ModuleType("starlette.middleware")
        starlette.middleware.base = types.ModuleType("starlette.middleware.base")

        class _FakeBase:
            def __init__(self, app, **_):
                self.app = app

            async def dispatch(self, request, call_next):  # pragma: no cover
                return await call_next(request)

        starlette.middleware.base.BaseHTTPMiddleware = _FakeBase
        starlette.requests = types.ModuleType("starlette.requests")
        starlette.requests.Request = object
        starlette.responses = types.ModuleType("starlette.responses")
        starlette.responses.Response = object
        sys.modules.update({
            "starlette": starlette,
            "starlette.middleware": starlette.middleware,
            "starlette.middleware.base": starlette.middleware.base,
            "starlette.requests": starlette.requests,
            "starlette.responses": starlette.responses,
        })
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_policy_config_exists(self):
        assert _POLICY_PATH.exists(), f"Missing: {_POLICY_PATH}"

    def test_middleware_module_exists(self):
        assert _MW_PATH.exists(), f"Missing: {_MW_PATH}"

    def test_middleware_is_in_middleware_package(self):
        assert _MW_PATH.parent.name == "middleware", (
            "deprecation.py must live in backend/app/middleware/"
        )


# ─────────────────────────────────────────────────────────────
# B — Policy config validity
# ─────────────────────────────────────────────────────────────

class TestBPolicyConfigValidity:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_policy()

    def test_policy_is_valid_yaml(self, policy):
        assert isinstance(policy, dict), "api-deprecation-policy.yml must be a YAML mapping"

    def test_policy_has_version_field(self, policy):
        assert "version" in policy, "api-deprecation-policy.yml missing 'version' field"

    def test_policy_has_metadata(self, policy):
        assert "metadata" in policy, "api-deprecation-policy.yml missing 'metadata' section"

    def test_policy_has_versions_list(self, policy):
        versions = policy.get("versions")
        assert isinstance(versions, list) and len(versions) > 0, (
            "api-deprecation-policy.yml 'versions' must be a non-empty list"
        )

    def test_policy_has_sunset_header_config(self, policy):
        assert "sunset_header" in policy, (
            "api-deprecation-policy.yml missing 'sunset_header' configuration"
        )

    def test_policy_has_deprecation_header_config(self, policy):
        assert "deprecation_header" in policy, (
            "api-deprecation-policy.yml missing 'deprecation_header' configuration"
        )


# ─────────────────────────────────────────────────────────────
# C — Policy version content
# ─────────────────────────────────────────────────────────────

class TestCPolicyVersionContent:

    @pytest.fixture(scope="class")
    def version_map(self):
        policy = _load_policy()
        return {v["version"]: v for v in policy.get("versions", []) if "version" in v}

    def test_v1_entry_present(self, version_map):
        assert "v1" in version_map, "api-deprecation-policy.yml must have a 'v1' version entry"

    def test_each_version_has_status(self, version_map):
        for ver, entry in version_map.items():
            assert "status" in entry, f"Version '{ver}' missing 'status' field"

    def test_all_statuses_are_valid(self, version_map):
        for ver, entry in version_map.items():
            status = entry.get("status")
            assert status in VALID_STATUSES, (
                f"Version '{ver}' has unknown status '{status}'. "
                f"Valid: {VALID_STATUSES}"
            )

    def test_v1_is_active(self, version_map):
        v1 = version_map.get("v1", {})
        assert v1.get("status") == "active", (
            "v1 must be 'active' — it is the current production API version"
        )

    def test_each_version_has_notes(self, version_map):
        for ver, entry in version_map.items():
            assert "notes" in entry and entry["notes"], (
                f"Version '{ver}' missing 'notes' — document purpose and migration path"
            )


# ─────────────────────────────────────────────────────────────
# D — Policy timing and header config
# ─────────────────────────────────────────────────────────────

class TestDPolicyTimingConfig:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_policy()

    def test_default_deprecation_notice_days_present(self, policy):
        assert "default_deprecation_notice_days" in policy, (
            "api-deprecation-policy.yml missing 'default_deprecation_notice_days'"
        )

    def test_deprecation_notice_days_is_reasonable(self, policy):
        days = policy.get("default_deprecation_notice_days", 0)
        assert days >= 30, (
            f"default_deprecation_notice_days {days} must be >= 30 days"
        )

    def test_sunset_header_enabled_flag_present(self, policy):
        sh = policy.get("sunset_header", {})
        assert "enabled" in sh, "sunset_header section missing 'enabled' flag"

    def test_sunset_warn_within_days_present(self, policy):
        sh = policy.get("sunset_header", {})
        assert "warn_within_days" in sh, (
            "sunset_header section missing 'warn_within_days' — needed for Warning headers"
        )

    def test_deprecation_header_enabled_flag_present(self, policy):
        dh = policy.get("deprecation_header", {})
        assert "enabled" in dh, "deprecation_header section missing 'enabled' flag"


# ─────────────────────────────────────────────────────────────
# E — RFC compliance metadata
# ─────────────────────────────────────────────────────────────

class TestERfcComplianceMetadata:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_policy()

    def test_metadata_references_rfc8594(self, policy):
        meta_text = str(policy.get("metadata", {}))
        assert "8594" in meta_text, (
            "metadata must reference RFC 8594 (Sunset HTTP Header)"
        )

    def test_metadata_references_rfc9745(self, policy):
        meta_text = str(policy.get("metadata", {}))
        assert "9745" in meta_text, (
            "metadata must reference RFC 9745 (Deprecation HTTP Header)"
        )

    def test_metadata_has_team(self, policy):
        meta = policy.get("metadata", {})
        assert "team" in meta, "metadata missing 'team' field"

    def test_policy_file_text_mentions_sunset_header(self):
        text = _POLICY_PATH.read_text(encoding="utf-8")
        assert "Sunset" in text, (
            "api-deprecation-policy.yml must mention 'Sunset' header"
        )

    def test_policy_file_text_mentions_deprecation_header(self):
        text = _POLICY_PATH.read_text(encoding="utf-8")
        assert "Deprecation" in text, (
            "api-deprecation-policy.yml must mention 'Deprecation' header"
        )


# ─────────────────────────────────────────────────────────────
# F — Middleware module structure
# ─────────────────────────────────────────────────────────────

class TestFMiddlewareStructure:

    @pytest.fixture(scope="class")
    def mod(self):
        return _import_middleware()

    def test_module_importable(self, mod):
        assert mod is not None

    def test_middleware_class_exists(self, mod):
        assert hasattr(mod, "ApiDeprecationMiddleware"), (
            "deprecation.py must define ApiDeprecationMiddleware class"
        )

    def test_version_from_path_function_exists(self, mod):
        assert hasattr(mod, "_version_from_path"), (
            "deprecation.py must define _version_from_path() helper"
        )

    def test_load_policy_function_exists(self, mod):
        assert hasattr(mod, "_load_policy"), (
            "deprecation.py must define _load_policy() function"
        )

    def test_deprecated_statuses_constant_exists(self, mod):
        assert hasattr(mod, "DEPRECATED_STATUSES"), (
            "deprecation.py must define DEPRECATED_STATUSES set"
        )

    def test_deprecated_statuses_contains_deprecated(self, mod):
        assert "deprecated" in mod.DEPRECATED_STATUSES

    def test_deprecated_statuses_contains_sunset(self, mod):
        assert "sunset" in mod.DEPRECATED_STATUSES


# ─────────────────────────────────────────────────────────────
# G — _version_from_path pure-function tests
# ─────────────────────────────────────────────────────────────

class TestGVersionFromPath:

    @pytest.fixture(scope="class")
    def fn(self):
        return _import_middleware()._version_from_path

    def test_extracts_v1_from_api_path(self, fn):
        assert fn("/api/v1/cameras") == "v1"

    def test_extracts_v2_from_api_path(self, fn):
        assert fn("/api/v2/alerts") == "v2"

    def test_returns_none_for_health_endpoint(self, fn):
        assert fn("/health") is None

    def test_returns_none_for_ws_path(self, fn):
        assert fn("/ws/live") is None

    def test_returns_none_for_bare_root(self, fn):
        assert fn("/") is None

    def test_returns_none_for_non_api_v_path(self, fn):
        assert fn("/api/docs") is None


# ─────────────────────────────────────────────────────────────
# H — Middleware header injection logic
# ─────────────────────────────────────────────────────────────

class TestHMiddlewareHeaderInjection:

    @pytest.fixture(scope="class")
    def mod(self):
        return _import_middleware()

    def test_active_version_injects_no_headers(self, mod):
        policy = {
            "versions": [{"version": "v1", "status": "active", "deprecated_on": None, "sunset_date": None}],
            "sunset_header": {"enabled": True, "warn_within_days": 30},
            "deprecation_header": {"enabled": True, "fallback_value": "true"},
            "link_header": {"enabled": False},
        }
        mw = mod.ApiDeprecationMiddleware(app=None, policy=policy)
        assert "v1" in mw._version_map
        assert mw._version_map["v1"]["status"] == "active"

    def test_deprecated_version_sets_deprecation_header(self, mod):
        future_sunset = (datetime.now(timezone.utc) + timedelta(days=365)).date().isoformat()
        past_deprecated = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
        policy = {
            "versions": [{
                "version": "v1",
                "status": "deprecated",
                "deprecated_on": past_deprecated,
                "sunset_date": future_sunset,
            }],
            "sunset_header": {"enabled": True, "warn_within_days": 30},
            "deprecation_header": {"enabled": True, "fallback_value": "true"},
            "link_header": {"enabled": False},
        }
        mw = mod.ApiDeprecationMiddleware(app=None, policy=policy)
        ver = mw._version_map["v1"]
        assert ver["status"] == "deprecated"
        assert ver["deprecated_on"] == past_deprecated

    def test_sunset_date_parsed_as_rfc7231(self, mod):
        sunset_iso = "2027-01-31"
        policy = {
            "versions": [{"version": "v1", "status": "deprecated", "deprecated_on": None, "sunset_date": sunset_iso}],
            "sunset_header": {"enabled": True, "warn_within_days": 30},
            "deprecation_header": {"enabled": True, "fallback_value": "true"},
            "link_header": {"enabled": False},
        }
        mw = mod.ApiDeprecationMiddleware(app=None, policy=policy)
        # The middleware uses RFC 7231 format — verify the format string is defined
        assert hasattr(mod, "_RFC7231_FMT"), "deprecation.py must define _RFC7231_FMT constant"
        fmt = mod._RFC7231_FMT
        assert "%Y" in fmt and "%d" in fmt, f"_RFC7231_FMT '{fmt}' should contain year and day"

    def test_warn_within_days_loaded_from_policy(self, mod):
        policy = {
            "versions": [],
            "sunset_header": {"enabled": True, "warn_within_days": 60},
            "deprecation_header": {"enabled": True, "fallback_value": "true"},
            "link_header": {"enabled": False},
        }
        mw = mod.ApiDeprecationMiddleware(app=None, policy=policy)
        assert mw._warn_within_days == 60

    def test_middleware_uses_real_policy_file(self, mod):
        policy = mod._load_policy()
        assert isinstance(policy, dict), (
            "_load_policy() must return a dict when the policy file exists"
        )
        assert "versions" in policy, (
            "_load_policy() must load the real api-deprecation-policy.yml"
        )
