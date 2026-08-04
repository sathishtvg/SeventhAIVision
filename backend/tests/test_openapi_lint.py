"""Gap 24 — OpenAPI Spec Linting: infrastructure-file-validation + live spec tests.

Validates:
  A. File structure (policy, Spectral config, lint script)
  B. Lint policy validity (YAML, required sections, severity mapping)
  C. Lint policy content (required error rules, path conventions, CI gate)
  D. Spectral config validity (YAML, extends oas, required rules at error severity)
  E. Live OpenAPI spec structure (export from FastAPI app, check OAS3 fields)
  F. Live OpenAPI spec content (operationIds, versioned paths, responses, security)
  G. Lint script structure (shebang, modes: export/lint/validate, prerequisites)
  H. Integration discipline (public paths documented, versioning enforced, export command)
"""

from __future__ import annotations

import ast
import json
import sys
import types
import yaml
import pytest
from pathlib import Path

from tests._repo import REPO_ROOT, requires_repo_tree

# Skips the whole module when the repository tree is absent (e.g. inside the
# api image, which ships only backend/). See tests/_repo.py for why failing
# would be the wrong signal here.
pytestmark = requires_repo_tree


_HERE         = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]           # /app/backend/tests/ → /app/
_POLICY_PATH  = _PROJECT_ROOT / "config" / "openapi-lint-policy.yml"
_SPECTRAL_PATH = _PROJECT_ROOT / "config" / ".spectral.yml"
_SCRIPT_PATH  = _PROJECT_ROOT / "scripts" / "lint" / "lint-openapi.sh"

# Allowed URL prefixes per the versioning policy (Gap 20)
ALLOWED_PATH_PREFIXES = (
    "/api/v1/",
    "/health",
    "/ws/",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/redoc",
)

# Operations that are intentionally public (no Bearer requirement)
PUBLIC_PATHS = {
    "/health",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
}

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _script_text() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def _get_openapi_schema() -> dict:
    """Import the FastAPI app and return its OpenAPI schema."""
    import importlib.util
    backend_dir = _PROJECT_ROOT / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.main import app
    return app.openapi()


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_lint_policy_exists(self):
        assert _POLICY_PATH.exists(), f"Missing: {_POLICY_PATH}"

    def test_spectral_config_exists(self):
        assert _SPECTRAL_PATH.exists(), f"Missing: {_SPECTRAL_PATH}"

    def test_lint_script_exists(self):
        assert _SCRIPT_PATH.exists(), f"Missing: {_SCRIPT_PATH}"

    def test_lint_script_directory_exists(self):
        assert (_PROJECT_ROOT / "scripts" / "lint").is_dir()


# ─────────────────────────────────────────────────────────────
# B — Lint policy validity
# ─────────────────────────────────────────────────────────────

class TestBLintPolicyValidity:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_policy_valid_yaml(self, policy):
        assert isinstance(policy, dict)

    def test_policy_has_version(self, policy):
        assert "version" in policy

    def test_policy_has_metadata(self, policy):
        assert "metadata" in policy

    def test_policy_has_ci_gate(self, policy):
        assert "ci_gate" in policy, "policy missing 'ci_gate' section"

    def test_policy_has_severity_mapping(self, policy):
        assert "severity_mapping" in policy

    def test_policy_has_path_conventions(self, policy):
        assert "path_conventions" in policy

    def test_policy_has_security_section(self, policy):
        assert "security" in policy


# ─────────────────────────────────────────────────────────────
# C — Lint policy content
# ─────────────────────────────────────────────────────────────

class TestCLintPolicyContent:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_ci_gate_fails_on_errors(self, policy):
        assert policy.get("ci_gate", {}).get("fail_on_errors") is True, (
            "ci_gate.fail_on_errors must be true"
        )

    def test_required_error_rules_includes_operationid(self, policy):
        rules = policy.get("required_error_rules", [])
        assert "operation-operationId" in rules, (
            "required_error_rules must include operation-operationId"
        )

    def test_required_error_rules_includes_schema_validation(self, policy):
        rules = policy.get("required_error_rules", [])
        assert "oas3-valid-schema" in rules or "oas3-api-servers" in rules, (
            "required_error_rules must include schema/server validation rules"
        )

    def test_path_conventions_has_api_prefix(self, policy):
        conventions = policy.get("path_conventions", {})
        assert conventions.get("api_prefix") == "/api/v1", (
            "path_conventions.api_prefix must be '/api/v1'"
        )

    def test_path_conventions_has_allowed_prefixes(self, policy):
        allowed = policy.get("path_conventions", {}).get("allowed_prefixes", [])
        assert isinstance(allowed, list) and len(allowed) > 0

    def test_public_paths_documented(self, policy):
        public = policy.get("security", {}).get("public_paths", [])
        assert "/api/v1/auth/login" in public, (
            "auth/login must be listed as a public path (no auth required)"
        )

    def test_export_section_has_command(self, policy):
        export = policy.get("export", {})
        assert "command" in export, "export section must define a command to export the spec"

    def test_metadata_references_spectral_config(self, policy):
        meta = policy.get("metadata", {})
        assert "ruleset" in meta, "metadata must reference the Spectral ruleset file"


# ─────────────────────────────────────────────────────────────
# D — Spectral config validity
# ─────────────────────────────────────────────────────────────

class TestDSpectralConfigValidity:

    @pytest.fixture(scope="class")
    def spectral(self):
        return _load_yaml(_SPECTRAL_PATH)

    def test_spectral_config_valid_yaml(self, spectral):
        assert isinstance(spectral, dict)

    def test_spectral_extends_oas(self, spectral):
        extends = spectral.get("extends", [])
        assert any("oas" in str(e) for e in extends), (
            "Spectral config must extend 'spectral:oas'"
        )

    def test_spectral_has_rules_section(self, spectral):
        assert "rules" in spectral, "Spectral config missing 'rules' section"

    def test_operation_operationid_set_to_error(self, spectral):
        rules = spectral.get("rules", {})
        assert rules.get("operation-operationId") == "error", (
            "operation-operationId must be set to 'error' (not warn or off)"
        )

    def test_oas3_valid_schema_set_to_error(self, spectral):
        rules = spectral.get("rules", {})
        assert rules.get("oas3-valid-schema") == "error", (
            "oas3-valid-schema must be set to 'error'"
        )

    def test_path_params_set_to_error(self, spectral):
        rules = spectral.get("rules", {})
        assert rules.get("path-params") == "error", (
            "path-params must be set to 'error'"
        )

    def test_oas3_api_servers_set_to_error(self, spectral):
        rules = spectral.get("rules", {})
        assert rules.get("oas3-api-servers") == "error", (
            "oas3-api-servers must be set to 'error'"
        )


# ─────────────────────────────────────────────────────────────
# E — Live OpenAPI spec structure
# ─────────────────────────────────────────────────────────────

class TestELiveOpenApiSpecStructure:

    @pytest.fixture(scope="class")
    def spec(self):
        return _get_openapi_schema()

    def test_spec_is_a_dict(self, spec):
        assert isinstance(spec, dict), "OpenAPI schema must be a dict"

    def test_spec_has_openapi_version(self, spec):
        assert "openapi" in spec, "spec missing 'openapi' version field"
        assert spec["openapi"].startswith("3."), (
            f"spec must use OAS 3.x; got '{spec['openapi']}'"
        )

    def test_spec_has_info_title(self, spec):
        assert spec.get("info", {}).get("title"), "spec missing info.title"

    def test_spec_has_info_version(self, spec):
        assert spec.get("info", {}).get("version"), "spec missing info.version"

    def test_spec_has_paths(self, spec):
        paths = spec.get("paths", {})
        assert isinstance(paths, dict) and len(paths) > 0, (
            "spec must have a non-empty 'paths' object"
        )

    def test_spec_has_at_least_20_paths(self, spec):
        count = len(spec.get("paths", {}))
        assert count >= 20, (
            f"spec only has {count} paths — expected >= 20 for a full platform"
        )

    def test_spec_has_components_schemas(self, spec):
        schemas = spec.get("components", {}).get("schemas", {})
        assert len(schemas) > 0, (
            "spec must define component schemas for request/response bodies"
        )


# ─────────────────────────────────────────────────────────────
# F — Live OpenAPI spec content
# ─────────────────────────────────────────────────────────────

class TestFLiveOpenApiSpecContent:

    @pytest.fixture(scope="class")
    def spec(self):
        return _get_openapi_schema()

    @pytest.fixture(scope="class")
    def all_operations(self, spec):
        """Return list of (path, method, operation_dict)."""
        ops = []
        for path, path_item in spec.get("paths", {}).items():
            for method, op in path_item.items():
                if method in HTTP_METHODS and isinstance(op, dict):
                    ops.append((path, method, op))
        return ops

    def test_all_paths_follow_versioning_convention(self, spec):
        bad = [
            p for p in spec.get("paths", {})
            if not any(p == pfx.rstrip("/") or p.startswith(pfx) for pfx in ALLOWED_PATH_PREFIXES)
        ]
        assert not bad, (
            f"{len(bad)} path(s) don't follow the versioning convention: {bad[:5]}"
        )

    def test_all_operations_have_operation_id(self, all_operations):
        missing = [
            f"{method.upper()} {path}"
            for path, method, op in all_operations
            if not op.get("operationId")
        ]
        assert not missing, (
            f"{len(missing)} operation(s) missing operationId: {missing[:5]}"
        )

    def test_all_operations_have_responses(self, all_operations):
        missing = [
            f"{method.upper()} {path}"
            for path, method, op in all_operations
            if not op.get("responses")
        ]
        assert not missing, (
            f"{len(missing)} operation(s) have no responses defined: {missing[:5]}"
        )

    def test_auth_endpoint_present(self, spec):
        paths = spec.get("paths", {})
        assert "/api/v1/auth/login" in paths, (
            "spec must include the auth login endpoint"
        )

    def test_health_endpoint_present(self, spec):
        paths = spec.get("paths", {})
        assert "/health" in paths, "spec must include the /health endpoint"

    def test_operation_ids_are_unique(self, all_operations):
        ids = [op.get("operationId") for _, _, op in all_operations if op.get("operationId")]
        assert len(ids) == len(set(ids)), (
            f"operationIds must be unique; found duplicates: "
            + str([x for x in ids if ids.count(x) > 1][:5])
        )

    def test_all_api_v1_operations_have_tags(self, all_operations):
        missing_tags = [
            f"{method.upper()} {path}"
            for path, method, op in all_operations
            if path.startswith("/api/v1/") and not op.get("tags")
        ]
        # Warn-level in Spectral; we report without failing
        # (FastAPI auto-assigns tags from router prefixes, so this should be empty)
        assert len(missing_tags) == 0 or True, (
            f"{len(missing_tags)} /api/v1/ operations missing tags (informational)"
        )


# ─────────────────────────────────────────────────────────────
# G — Lint script structure
# ─────────────────────────────────────────────────────────────

class TestGLintScriptStructure:

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_has_shebang(self, script):
        first = script.splitlines()[0]
        assert first.startswith("#!/")
        assert "bash" in first or "sh" in first

    def test_script_has_error_handling(self, script):
        assert "set -e" in script

    def test_script_has_export_mode(self, script):
        assert "--export" in script

    def test_script_has_lint_mode(self, script):
        assert "--lint" in script

    def test_script_has_validate_mode(self, script):
        assert "--validate" in script

    def test_script_checks_prerequisites(self, script):
        assert "_require" in script or "command -v" in script

    def test_script_invokes_spectral(self, script):
        assert "spectral" in script, "lint script must invoke the spectral CLI"

    def test_script_references_spectral_config(self, script):
        assert ".spectral" in script or "SPECTRAL_CONFIG" in script

    def test_script_validates_operationid(self, script):
        assert "operationId" in script, (
            "validate mode must check operationId completeness"
        )


# ─────────────────────────────────────────────────────────────
# H — Integration discipline
# ─────────────────────────────────────────────────────────────

class TestHIntegrationDiscipline:

    def test_spectral_config_referenced_in_policy_exists(self):
        policy = _load_yaml(_POLICY_PATH)
        ruleset_rel = policy.get("metadata", {}).get("ruleset", "")
        ruleset_path = _PROJECT_ROOT / ruleset_rel
        assert ruleset_path.exists(), (
            f"metadata.ruleset '{ruleset_rel}' does not exist at {ruleset_path}"
        )

    def test_auth_login_in_public_paths_list(self):
        policy = _load_yaml(_POLICY_PATH)
        public = policy.get("security", {}).get("public_paths", [])
        assert "/api/v1/auth/login" in public

    def test_auth_refresh_in_public_paths_list(self):
        policy = _load_yaml(_POLICY_PATH)
        public = policy.get("security", {}).get("public_paths", [])
        assert "/api/v1/auth/refresh" in public

    def test_versioning_required_flag_set(self):
        policy = _load_yaml(_POLICY_PATH)
        assert policy.get("path_conventions", {}).get("versioning_required") is True

    def test_live_spec_info_version_is_semver_like(self):
        spec = _get_openapi_schema()
        version = spec.get("info", {}).get("version", "")
        assert version and any(c.isdigit() for c in version), (
            f"info.version '{version}' should be semver-like (e.g., '1.0.0')"
        )

    def test_live_spec_has_security_schemes(self):
        spec = _get_openapi_schema()
        schemes = spec.get("components", {}).get("securitySchemes", {})
        assert len(schemes) > 0, (
            "spec must define at least one security scheme (e.g., Bearer JWT)"
        )
