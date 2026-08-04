"""Gap 22 — Dependency Vulnerability Auditing: infrastructure-file-validation tests.

Validates:
  A. File structure (policy file, exceptions file, audit script)
  B. Audit policy validity (YAML, required top-level keys)
  C. Audit policy content (scan_targets, severity_gates, fix_policy timing)
  D. Exception file validity (YAML, version, metadata, exceptions list)
  E. Exception file discipline (format requirements, no expired exceptions, max window)
  F. Audit script structure (shebang, error handling, modes present)
  G. Audit script content (pip-audit invocation, targets, report, exit code handling)
  H. Integration discipline (CRITICAL fix <= 7 days, both targets declared, report dir defined)
"""

from __future__ import annotations

import re
import yaml
import pytest
from datetime import date, timedelta
from pathlib import Path

from tests._repo import REPO_ROOT, requires_repo_tree

# Skips the whole module when the repository tree is absent (e.g. inside the
# api image, which ships only backend/). See tests/_repo.py for why failing
# would be the wrong signal here.
pytestmark = requires_repo_tree


_HERE         = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]           # /app/backend/tests/ → /app/
_POLICY_PATH  = _PROJECT_ROOT / "config" / "dependency-audit-policy.yml"
_EXCEPTIONS_PATH = _PROJECT_ROOT / "config" / "vulnerability-exceptions.yml"
_SCRIPT_PATH  = _PROJECT_ROOT / "scripts" / "audit" / "audit-dependencies.sh"

REQUIRED_SCAN_LABELS = {"api-backend", "ai-worker"}
REQUIRED_FAIL_ON_SEVERITIES = {"CRITICAL", "HIGH"}
MAX_EXCEPTION_DAYS = 90


def _load_yaml(path: Path) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _script_text() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_audit_policy_exists(self):
        assert _POLICY_PATH.exists(), f"Missing: {_POLICY_PATH}"

    def test_exceptions_file_exists(self):
        assert _EXCEPTIONS_PATH.exists(), f"Missing: {_EXCEPTIONS_PATH}"

    def test_audit_script_exists(self):
        assert _SCRIPT_PATH.exists(), f"Missing: {_SCRIPT_PATH}"

    def test_reports_audit_directory_parent_exists(self):
        # The reports/ directory itself may not exist pre-run, but the script
        # must create it. We validate the project root is correct.
        assert _PROJECT_ROOT.is_dir(), (
            "Project root must be a valid directory"
        )


# ─────────────────────────────────────────────────────────────
# B — Audit policy validity
# ─────────────────────────────────────────────────────────────

class TestBAuditPolicyValidity:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_policy_valid_yaml(self, policy):
        assert isinstance(policy, dict), "dependency-audit-policy.yml must be a YAML mapping"

    def test_policy_has_version(self, policy):
        assert "version" in policy, "policy missing 'version'"

    def test_policy_has_metadata(self, policy):
        assert "metadata" in policy, "policy missing 'metadata'"

    def test_policy_has_scan_targets(self, policy):
        targets = policy.get("scan_targets")
        assert isinstance(targets, list) and len(targets) > 0, (
            "policy must have a non-empty 'scan_targets' list"
        )

    def test_policy_has_severity_gates(self, policy):
        assert "severity_gates" in policy, "policy missing 'severity_gates'"

    def test_policy_has_fix_policy(self, policy):
        assert "fix_policy" in policy, "policy missing 'fix_policy'"

    def test_policy_has_exception_policy(self, policy):
        assert "exception_policy" in policy, "policy missing 'exception_policy'"


# ─────────────────────────────────────────────────────────────
# C — Audit policy content
# ─────────────────────────────────────────────────────────────

class TestCAuditPolicyContent:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_scan_targets_have_required_fields(self, policy):
        for i, target in enumerate(policy.get("scan_targets", [])):
            assert "label" in target, f"scan_targets[{i}] missing 'label'"
            assert "pyproject" in target, f"scan_targets[{i}] missing 'pyproject'"

    def test_required_scan_labels_present(self, policy):
        labels = {t.get("label") for t in policy.get("scan_targets", [])}
        missing = REQUIRED_SCAN_LABELS - labels
        assert not missing, (
            f"scan_targets must include both backends: missing {missing}"
        )

    def test_severity_fail_on_includes_critical_and_high(self, policy):
        gates = policy.get("severity_gates", {})
        fail_on = set(gates.get("fail_on", []))
        missing = REQUIRED_FAIL_ON_SEVERITIES - fail_on
        assert not missing, (
            f"severity_gates.fail_on must include CRITICAL and HIGH; missing: {missing}"
        )

    def test_fix_policy_has_delay_days(self, policy):
        fix = policy.get("fix_policy", {})
        assert "max_fix_delay_days" in fix, "fix_policy missing 'max_fix_delay_days'"

    def test_fix_delay_days_has_critical_and_high(self, policy):
        delay = policy.get("fix_policy", {}).get("max_fix_delay_days", {})
        assert "CRITICAL" in delay, "fix_policy.max_fix_delay_days missing 'CRITICAL'"
        assert "HIGH" in delay, "fix_policy.max_fix_delay_days missing 'HIGH'"

    def test_exception_policy_references_exceptions_file(self, policy):
        exc_policy = policy.get("exception_policy", {})
        assert "exceptions_file" in exc_policy, (
            "exception_policy must specify the exceptions_file path"
        )

    def test_exception_policy_requires_expiry(self, policy):
        exc_policy = policy.get("exception_policy", {})
        assert exc_policy.get("require_expiry") is True, (
            "exception_policy.require_expiry must be true"
        )

    def test_output_section_defines_report_dir(self, policy):
        output = policy.get("output", {})
        assert "report_dir" in output, (
            "policy must define output.report_dir for report storage"
        )


# ─────────────────────────────────────────────────────────────
# D — Exception file validity
# ─────────────────────────────────────────────────────────────

class TestDExceptionFileValidity:

    @pytest.fixture(scope="class")
    def exc_doc(self):
        return _load_yaml(_EXCEPTIONS_PATH)

    def test_exceptions_file_valid_yaml(self, exc_doc):
        assert isinstance(exc_doc, dict), "vulnerability-exceptions.yml must be a YAML mapping"

    def test_exceptions_file_has_version(self, exc_doc):
        assert "version" in exc_doc, "exceptions file missing 'version'"

    def test_exceptions_file_has_metadata(self, exc_doc):
        assert "metadata" in exc_doc, "exceptions file missing 'metadata'"

    def test_exceptions_key_is_list(self, exc_doc):
        exceptions = exc_doc.get("exceptions")
        assert exceptions is None or isinstance(exceptions, list), (
            "'exceptions' key must be a list (or null/empty)"
        )

    def test_metadata_has_max_exception_days(self, exc_doc):
        meta = exc_doc.get("metadata", {})
        assert "max_exception_days" in meta, (
            "exceptions file metadata must document max_exception_days"
        )

    def test_max_exception_days_reasonable(self, exc_doc):
        meta = exc_doc.get("metadata", {})
        days = meta.get("max_exception_days", 0)
        assert 30 <= days <= 180, (
            f"max_exception_days {days} should be between 30 and 180"
        )


# ─────────────────────────────────────────────────────────────
# E — Exception file discipline
# ─────────────────────────────────────────────────────────────

class TestEExceptionFileDiscipline:

    @pytest.fixture(scope="class")
    def exceptions(self):
        doc = _load_yaml(_EXCEPTIONS_PATH)
        return doc.get("exceptions", []) or []

    def test_no_expired_exceptions(self, exceptions):
        today = date.today()
        expired = [
            e for e in exceptions
            if e.get("expires") and today > date.fromisoformat(str(e["expires"]))
        ]
        assert not expired, (
            f"{len(expired)} expired exception(s) found — remove or renew them: "
            + ", ".join(e.get("id", "unknown") for e in expired)
        )

    def test_each_exception_has_id(self, exceptions):
        for i, exc in enumerate(exceptions):
            assert "id" in exc, f"exceptions[{i}] missing 'id' (GHSA-/CVE- identifier)"

    def test_each_exception_has_package(self, exceptions):
        for i, exc in enumerate(exceptions):
            assert "package" in exc, f"exceptions[{i}] missing 'package'"

    def test_each_exception_has_reason(self, exceptions):
        for i, exc in enumerate(exceptions):
            reason = exc.get("reason", "").strip()
            assert reason, f"exceptions[{i}] ({exc.get('id', '?')}) has empty 'reason'"

    def test_each_exception_has_expiry(self, exceptions):
        for i, exc in enumerate(exceptions):
            assert "expires" in exc, (
                f"exceptions[{i}] ({exc.get('id', '?')}) missing 'expires' date"
            )

    def test_no_exception_window_exceeds_max_days(self, exceptions):
        doc = _load_yaml(_EXCEPTIONS_PATH)
        max_days = doc.get("metadata", {}).get("max_exception_days", MAX_EXCEPTION_DAYS)
        today = date.today()
        for exc in exceptions:
            expires = exc.get("expires")
            if expires is None:
                continue
            exp_date = date.fromisoformat(str(expires))
            window = (exp_date - today).days
            assert window <= max_days, (
                f"Exception {exc.get('id')} expires in {window} days, "
                f"exceeding max_exception_days={max_days}"
            )


# ─────────────────────────────────────────────────────────────
# F — Audit script structure
# ─────────────────────────────────────────────────────────────

class TestFAuditScriptStructure:

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_has_shebang(self, script):
        first_line = script.splitlines()[0]
        assert first_line.startswith("#!/"), "audit script must start with a shebang"
        assert "bash" in first_line or "sh" in first_line

    def test_script_has_error_handling(self, script):
        assert "set -e" in script or "set -euo" in script, (
            "audit script must use 'set -e' or 'set -euo pipefail'"
        )

    def test_script_has_scan_mode(self, script):
        assert "--scan" in script, "audit script must implement --scan mode"

    def test_script_has_check_exceptions_mode(self, script):
        assert "--check-exceptions" in script, (
            "audit script must implement --check-exceptions mode"
        )

    def test_script_has_report_mode(self, script):
        assert "--report" in script, "audit script must implement --report mode"

    def test_script_has_prerequisite_checks(self, script):
        assert "_require" in script or "command -v" in script, (
            "audit script must check for required tools before running"
        )

    def test_script_has_enough_lines(self, script):
        non_empty = [l for l in script.splitlines() if l.strip()]
        assert len(non_empty) > 30, (
            f"audit script has only {len(non_empty)} non-empty lines — seems incomplete"
        )


# ─────────────────────────────────────────────────────────────
# G — Audit script content
# ─────────────────────────────────────────────────────────────

class TestGAuditScriptContent:

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_invokes_pip_audit(self, script):
        assert "pip-audit" in script, (
            "audit script must invoke the 'pip-audit' CLI"
        )

    def test_script_references_policy_file(self, script):
        assert "dependency-audit-policy" in script or "POLICY_FILE" in script, (
            "audit script must reference the dependency audit policy file"
        )

    def test_script_references_exceptions_file(self, script):
        assert "vulnerability-exceptions" in script or "EXCEPTIONS_FILE" in script, (
            "audit script must reference the vulnerability exceptions file"
        )

    def test_script_uses_json_output_format(self, script):
        assert "json" in script.lower(), (
            "audit script must request JSON output format for machine-readable reports"
        )

    def test_script_tracks_exit_code(self, script):
        assert "AUDIT_EXIT" in script or "exit 1" in script, (
            "audit script must track exit codes to propagate CI failures"
        )

    def test_script_creates_report_directory(self, script):
        assert "mkdir" in script, (
            "audit script must create the report directory if it doesn't exist"
        )

    def test_script_saves_report_file(self, script):
        assert "REPORT_FILE" in script or "reports/" in script, (
            "audit script must save findings to a report file"
        )


# ─────────────────────────────────────────────────────────────
# H — Integration discipline
# ─────────────────────────────────────────────────────────────

class TestHIntegrationDiscipline:

    def test_critical_fix_delay_is_7_days_or_less(self):
        policy = _load_yaml(_POLICY_PATH)
        critical_days = policy.get("fix_policy", {}).get("max_fix_delay_days", {}).get("CRITICAL")
        assert critical_days is not None, "fix_policy.max_fix_delay_days.CRITICAL is not defined"
        assert critical_days <= 7, (
            f"CRITICAL vulnerabilities must be fixed within 7 days; policy says {critical_days}"
        )

    def test_high_fix_delay_is_30_days_or_less(self):
        policy = _load_yaml(_POLICY_PATH)
        high_days = policy.get("fix_policy", {}).get("max_fix_delay_days", {}).get("HIGH")
        assert high_days is not None, "fix_policy.max_fix_delay_days.HIGH is not defined"
        assert high_days <= 30, (
            f"HIGH vulnerabilities must be fixed within 30 days; policy says {high_days}"
        )

    def test_both_services_have_scan_targets(self):
        policy = _load_yaml(_POLICY_PATH)
        labels = {t.get("label") for t in policy.get("scan_targets", [])}
        assert "api-backend" in labels, (
            "api-backend must be a declared scan target — it handles user-facing auth"
        )
        assert "ai-worker" in labels, (
            "ai-worker must be a declared scan target — it runs model inference in the pipeline"
        )

    def test_severity_gates_include_warn_on_medium(self):
        policy = _load_yaml(_POLICY_PATH)
        gates = policy.get("severity_gates", {})
        warn_on = gates.get("warn_on", [])
        assert "MEDIUM" in warn_on, (
            "severity_gates.warn_on should include MEDIUM for visibility without blocking CI"
        )

    def test_exception_file_referenced_in_policy_exists(self):
        policy = _load_yaml(_POLICY_PATH)
        exc_file_rel = policy.get("exception_policy", {}).get("exceptions_file", "")
        exc_path = _PROJECT_ROOT / exc_file_rel
        assert exc_path.exists(), (
            f"exception_policy.exceptions_file '{exc_file_rel}' does not exist at {exc_path}"
        )

    def test_scan_target_pyproject_paths_are_strings(self):
        policy = _load_yaml(_POLICY_PATH)
        for t in policy.get("scan_targets", []):
            pyproject = t.get("pyproject", "")
            assert isinstance(pyproject, str) and len(pyproject) > 0, (
                f"scan_target '{t.get('label')}' has invalid pyproject path"
            )
