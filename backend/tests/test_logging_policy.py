"""Gap 27 — Structured Logging Policy: infrastructure-file-validation + module tests.

Validates:
  A. File structure (policy, logging module, validation script, scripts/logging dir)
  B. Logging policy validity (YAML, required top-level sections, metadata)
  C. Policy content (JSON format, required fields, PII masking, log levels, retention)
  D. Logging module structure (imports, ContextVar, key functions, env vars)
  E. Logging module content (JSON formatter, request_id/tenant_id, PII redaction)
  F. Logging module functional correctness (importable, JsonFormatter works,
     get_logger returns logger, set_request_context sets context vars)
  G. Validation script structure (shebang, modes, python3, error handling)
  H. Integration discipline (policy log_levels valid, retention minimums, PII
     policy fields covered by module, cross-consistency checks)
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
import yaml
import pytest
from pathlib import Path

from tests._repo import REPO_ROOT, requires_repo_tree

# Skips the whole module when the repository tree is absent (e.g. inside the
# api image, which ships only backend/). See tests/_repo.py for why failing
# would be the wrong signal here.
pytestmark = requires_repo_tree


_HERE         = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]
_POLICY_PATH  = _PROJECT_ROOT / "config" / "logging-policy.yml"
_MODULE_PATH  = _PROJECT_ROOT / "backend" / "app" / "core" / "logging.py"
_SCRIPT_PATH  = _PROJECT_ROOT / "scripts" / "logging" / "validate-logging.sh"

_VALID_PYTHON_LOG_LEVELS = {"debug", "info", "warning", "error", "critical"}
_MIN_APP_LOG_RETENTION_DAYS = 30
_MIN_AUDIT_LOG_RETENTION_DAYS = 365   # 1 year minimum; policy has 7 years
_REQUIRED_POLICY_FIELDS = {"timestamp", "level", "service", "message", "request_id", "tenant_id"}
_REQUIRED_PII_FIELDS = {"email", "password", "token"}


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _module_text() -> str:
    return _MODULE_PATH.read_text(encoding="utf-8")


def _script_text() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_logging_policy_exists(self):
        assert _POLICY_PATH.exists(), f"Missing: {_POLICY_PATH}"

    def test_logging_module_exists(self):
        assert _MODULE_PATH.exists(), (
            f"Missing: {_MODULE_PATH} — backend structured logging module not created"
        )

    def test_validate_script_exists(self):
        assert _SCRIPT_PATH.exists(), f"Missing: {_SCRIPT_PATH}"

    def test_scripts_logging_directory_exists(self):
        assert (_PROJECT_ROOT / "scripts" / "logging").is_dir()

    def test_logging_module_in_core_package(self):
        assert (_PROJECT_ROOT / "backend" / "app" / "core" / "__init__.py").exists() or \
               any((_PROJECT_ROOT / "backend" / "app" / "core").iterdir()), (
            "backend/app/core/ must exist as a package"
        )


# ─────────────────────────────────────────────────────────────
# B — Logging policy validity
# ─────────────────────────────────────────────────────────────

class TestBLoggingPolicyValidity:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_policy_is_valid_yaml(self, policy):
        assert isinstance(policy, dict)

    def test_policy_has_version(self, policy):
        assert "version" in policy

    def test_policy_has_metadata(self, policy):
        assert "metadata" in policy

    def test_policy_has_format_section(self, policy):
        assert "format" in policy, "policy missing 'format' section"

    def test_policy_has_required_fields_section(self, policy):
        assert "required_fields" in policy, "policy missing 'required_fields' section"

    def test_policy_has_pii_masking_section(self, policy):
        assert "pii_masking" in policy, "policy missing 'pii_masking' section"

    def test_policy_has_log_levels_section(self, policy):
        assert "log_levels" in policy, "policy missing 'log_levels' section"

    def test_policy_has_retention_section(self, policy):
        assert "retention" in policy, "policy missing 'retention' section"


# ─────────────────────────────────────────────────────────────
# C — Policy content
# ─────────────────────────────────────────────────────────────

class TestCPolicyContent:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_format_type_is_json(self, policy):
        assert policy.get("format", {}).get("type") == "json", (
            "format.type must be 'json' — plain-text logs cannot be reliably parsed by Loki/ELK"
        )

    def test_required_fields_contains_timestamp(self, policy):
        assert "timestamp" in policy.get("required_fields", [])

    def test_required_fields_contains_level(self, policy):
        assert "level" in policy.get("required_fields", [])

    def test_required_fields_contains_service(self, policy):
        assert "service" in policy.get("required_fields", [])

    def test_required_fields_contains_message(self, policy):
        assert "message" in policy.get("required_fields", [])

    def test_required_fields_contains_request_id(self, policy):
        assert "request_id" in policy.get("required_fields", []), (
            "request_id is essential for correlating all log lines in one HTTP request"
        )

    def test_required_fields_contains_tenant_id(self, policy):
        assert "tenant_id" in policy.get("required_fields", []), (
            "tenant_id is required for multi-tenant debugging and compliance"
        )

    def test_pii_masking_is_enabled(self, policy):
        assert policy.get("pii_masking", {}).get("enabled") is True, (
            "pii_masking.enabled must be true — GDPR/PDPA require PII not appear in plain logs"
        )

    def test_pii_masking_includes_email(self, policy):
        assert "email" in policy.get("pii_masking", {}).get("fields", [])

    def test_pii_masking_includes_password(self, policy):
        assert "password" in policy.get("pii_masking", {}).get("fields", []), (
            "password must be in pii_masking.fields — accidental credential logging is a P0 incident"
        )

    def test_pii_masking_includes_token(self, policy):
        assert "token" in policy.get("pii_masking", {}).get("fields", [])

    def test_log_levels_has_production_setting(self, policy):
        assert "production" in policy.get("log_levels", {}), (
            "log_levels must define the production log level"
        )

    def test_production_log_level_is_not_debug(self, policy):
        prod = policy.get("log_levels", {}).get("production", "debug")
        assert prod.lower() != "debug", (
            f"log_levels.production '{prod}' must not be 'debug' — DEBUG logs expose internal state in prod"
        )

    def test_log_levels_has_env_var(self, policy):
        assert policy.get("log_levels", {}).get("env_var"), (
            "log_levels.env_var must specify the environment variable name for runtime override"
        )

    def test_application_logs_retention_is_adequate(self, policy):
        days = policy.get("retention", {}).get("application_logs_days", 0)
        assert days >= _MIN_APP_LOG_RETENTION_DAYS, (
            f"retention.application_logs_days {days} < minimum {_MIN_APP_LOG_RETENTION_DAYS}"
        )

    def test_audit_logs_retention_is_adequate(self, policy):
        days = policy.get("retention", {}).get("audit_logs_days", 0)
        assert days >= _MIN_AUDIT_LOG_RETENTION_DAYS, (
            f"retention.audit_logs_days {days} < minimum {_MIN_AUDIT_LOG_RETENTION_DAYS} "
            "(ISO 27001 requires at least 1 year)"
        )


# ─────────────────────────────────────────────────────────────
# D — Logging module structure
# ─────────────────────────────────────────────────────────────

class TestDLoggingModuleStructure:

    @pytest.fixture(scope="class")
    def module_text(self):
        return _module_text()

    def test_module_imports_logging(self, module_text):
        assert "import logging" in module_text, (
            "logging.py must import Python's stdlib 'logging' module"
        )

    def test_module_imports_json(self, module_text):
        assert "import json" in module_text, (
            "logging.py must import 'json' to serialise records as JSON objects"
        )

    def test_module_uses_contextvars(self, module_text):
        assert "ContextVar" in module_text, (
            "logging.py must use contextvars.ContextVar for per-request correlation IDs "
            "(safe under asyncio without locks)"
        )

    def test_module_defines_get_logger(self, module_text):
        assert "def get_logger" in module_text, (
            "logging.py must define get_logger() as the application-wide logger factory"
        )

    def test_module_defines_configure_logging(self, module_text):
        assert "def configure_logging" in module_text, (
            "logging.py must define configure_logging() to set up the root handler at startup"
        )

    def test_module_defines_set_request_context(self, module_text):
        assert "def set_request_context" in module_text, (
            "logging.py must define set_request_context() for FastAPI middleware use"
        )

    def test_module_reads_log_level_from_env(self, module_text):
        assert "LOG_LEVEL" in module_text, (
            "logging.py must read LOG_LEVEL from environment to allow runtime override"
        )

    def test_module_reads_service_name_from_env(self, module_text):
        assert "SERVICE_NAME" in module_text, (
            "logging.py must read SERVICE_NAME from environment for the 'service' field"
        )


# ─────────────────────────────────────────────────────────────
# E — Logging module content
# ─────────────────────────────────────────────────────────────

class TestELoggingModuleContent:

    @pytest.fixture(scope="class")
    def module_text(self):
        return _module_text()

    def test_module_defines_json_formatter(self, module_text):
        assert "JsonFormatter" in module_text, (
            "logging.py must define a JsonFormatter class (or equivalent) for structured output"
        )

    def test_module_includes_request_id_in_output(self, module_text):
        assert "request_id" in module_text, (
            "logging.py must include request_id in every log record"
        )

    def test_module_includes_tenant_id_in_output(self, module_text):
        assert "tenant_id" in module_text, (
            "logging.py must include tenant_id in every log record for multi-tenant debugging"
        )

    def test_module_defines_pii_redaction(self, module_text):
        assert "[REDACTED]" in module_text or "REDACTED" in module_text, (
            "logging.py must define a PII redaction placeholder string"
        )

    def test_module_defines_pii_patterns(self, module_text):
        assert "_PII_PATTERNS" in module_text or "pii" in module_text.lower(), (
            "logging.py must define PII masking patterns (regex list or similar)"
        )

    def test_module_handles_sensitive_keys(self, module_text):
        assert "password" in module_text.lower() and "token" in module_text.lower(), (
            "logging.py must explicitly handle 'password' and 'token' as sensitive keys"
        )

    def test_module_includes_timestamp_in_output(self, module_text):
        assert "timestamp" in module_text, (
            "logging.py must include 'timestamp' as a field in JSON output"
        )

    def test_module_uses_utc_timezone(self, module_text):
        assert "utc" in module_text.lower() or "UTC" in module_text or "timezone.utc" in module_text, (
            "logging.py must use UTC timestamps for consistency across deployed regions"
        )


# ─────────────────────────────────────────────────────────────
# F — Logging module functional correctness
# ─────────────────────────────────────────────────────────────

class TestFLoggingModuleFunctional:

    @pytest.fixture(scope="module")
    def log_module(self):
        backend_dir = _PROJECT_ROOT / "backend"
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import importlib
        mod = importlib.import_module("app.core.logging")
        return mod

    def test_module_is_importable(self, log_module):
        assert log_module is not None

    def test_get_logger_returns_logger_instance(self, log_module):
        logger = log_module.get_logger("test.logger")
        assert isinstance(logger, logging.Logger), (
            "get_logger() must return a logging.Logger instance"
        )

    def test_set_request_context_is_callable(self, log_module):
        assert callable(log_module.set_request_context)

    def test_configure_logging_is_callable(self, log_module):
        assert callable(log_module.configure_logging)

    def test_json_formatter_produces_valid_json(self, log_module):
        formatter = log_module.JsonFormatter(service_name="test-service")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Test message", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert isinstance(parsed, dict), "JsonFormatter must emit a valid JSON object"

    def test_json_formatter_includes_required_fields(self, log_module):
        formatter = log_module.JsonFormatter(service_name="test-service")
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="", lineno=0,
            msg="A warning", args=(), exc_info=None,
        )
        parsed = json.loads(formatter.format(record))
        for field in ("timestamp", "level", "service", "message"):
            assert field in parsed, f"JSON log record missing required field '{field}'"

    def test_pii_masking_redacts_email(self, log_module):
        formatter = log_module.JsonFormatter(service_name="test-service")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="User logged in: attacker@evil.com", args=(), exc_info=None,
        )
        parsed = json.loads(formatter.format(record))
        assert "attacker@evil.com" not in parsed["message"], (
            "JsonFormatter must redact email addresses from log messages"
        )
        assert "[REDACTED]" in parsed["message"] or "REDACTED" in parsed["message"]

    def test_set_request_context_is_reflected_in_logs(self, log_module):
        log_module.set_request_context(
            request_id="req-test-1234", tenant_id="tenant-abc"
        )
        formatter = log_module.JsonFormatter(service_name="test-service")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Context test", args=(), exc_info=None,
        )
        parsed = json.loads(formatter.format(record))
        assert parsed.get("request_id") == "req-test-1234"
        assert parsed.get("tenant_id") == "tenant-abc"
        # Cleanup
        log_module.clear_request_context()


# ─────────────────────────────────────────────────────────────
# G — Validation script structure
# ─────────────────────────────────────────────────────────────

class TestGValidationScriptStructure:

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_has_shebang(self, script):
        first = script.splitlines()[0]
        assert first.startswith("#!/")
        assert "bash" in first or "sh" in first

    def test_script_has_set_e(self, script):
        assert "set -e" in script

    def test_script_has_validate_mode(self, script):
        assert "--validate" in script

    def test_script_has_check_policy_mode(self, script):
        assert "--check-policy" in script

    def test_script_has_check_module_mode(self, script):
        assert "--check-module" in script

    def test_script_has_dry_run_mode(self, script):
        assert "--dry-run" in script

    def test_script_uses_python3(self, script):
        assert "python3" in script

    def test_script_reads_policy_file(self, script):
        assert "POLICY_FILE" in script

    def test_script_checks_pii_masking(self, script):
        assert "pii" in script.lower() or "PII" in script

    def test_script_checks_required_fields(self, script):
        assert "required_fields" in script


# ─────────────────────────────────────────────────────────────
# H — Integration discipline
# ─────────────────────────────────────────────────────────────

class TestHIntegrationDiscipline:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_log_levels_valid_levels_are_python_logging_levels(self, policy):
        valid = [v.lower() for v in policy.get("log_levels", {}).get("valid_levels", [])]
        unknown = [v for v in valid if v not in _VALID_PYTHON_LOG_LEVELS]
        assert not unknown, (
            f"log_levels.valid_levels contains non-Python logging levels: {unknown}"
        )

    def test_production_level_is_a_valid_level(self, policy):
        prod = policy.get("log_levels", {}).get("production", "").lower()
        assert prod in _VALID_PYTHON_LOG_LEVELS, (
            f"log_levels.production '{prod}' is not a valid Python logging level"
        )

    def test_development_level_is_a_valid_level(self, policy):
        dev = policy.get("log_levels", {}).get("development", "").lower()
        assert dev in _VALID_PYTHON_LOG_LEVELS, (
            f"log_levels.development '{dev}' is not a valid Python logging level"
        )

    def test_pii_masking_patterns_keys_defined(self, policy):
        patterns = policy.get("pii_masking", {}).get("patterns", {})
        assert "email_pattern" in patterns, (
            "pii_masking.patterns must define email_pattern regex"
        )

    def test_pii_masking_patterns_email_is_valid_regex(self, policy):
        import re
        email_pat = policy.get("pii_masking", {}).get("patterns", {}).get("email_pattern", "")
        assert email_pat, "email_pattern must not be empty"
        compiled = re.compile(email_pat)  # raises re.error if invalid
        assert compiled.search("user@example.com"), (
            "email_pattern must match a standard email address"
        )

    def test_module_covers_required_pii_fields(self):
        module_text = _module_text()
        for field in _REQUIRED_PII_FIELDS:
            assert field in module_text.lower(), (
                f"logging.py must reference PII field '{field}' from the policy"
            )

    def test_policy_scope_includes_api_service(self, policy):
        scope = policy.get("metadata", {}).get("scope", [])
        assert "api" in scope, (
            "metadata.scope must include 'api' — the main API service must use structured logging"
        )

    def test_policy_env_var_is_log_level(self, policy):
        env_var = policy.get("log_levels", {}).get("env_var", "")
        assert env_var == "LOG_LEVEL", (
            f"log_levels.env_var should be 'LOG_LEVEL'; got '{env_var}'"
        )

    def test_logging_module_references_uvicorn(self):
        module_text = _module_text()
        assert "uvicorn" in module_text.lower(), (
            "logging.py must configure uvicorn to use the JSON formatter so access logs "
            "have the same structure as application logs"
        )
