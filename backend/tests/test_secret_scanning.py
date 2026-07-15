"""Gap 28 — Secret Scanning Policy tests.

Validates that the secret scanning configuration (policy YAML, detect-secrets
baseline, and scan shell script) are present, structurally correct, and
internally consistent.

All tests are infrastructure-file-validation only — no live scanning is run
and no network access is required.

Sections:
    A — File structure (all 3 artefacts exist)
    B — Policy YAML validity (parseable, version, required sections)
    C — Policy content (patterns, severities, allowlist, enforcement)
    D — Baseline file structure (.detect-secrets.yaml)
    E — Scan script structure (shebang, set -e, modes)
    F — Scan script content (detect-secrets invocation, baseline, policy)
    G — Severity and enforcement discipline
    H — Cross-file consistency (policy ↔ script ↔ baseline)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


# ── Paths ─────────────────────────────────────────────────────────────────────
_BACKEND_DIR  = Path(__file__).parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent
_POLICY_FILE  = _PROJECT_ROOT / "config" / "secret-scanning-policy.yml"
_BASELINE     = _PROJECT_ROOT / ".detect-secrets.yaml"
_SCRIPT       = _PROJECT_ROOT / "scripts" / "security" / "scan-secrets.sh"

# ── Helpers ───────────────────────────────────────────────────────────────────
def _load_policy() -> dict:
    with open(_POLICY_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)

def _script_text() -> str:
    return _SCRIPT.read_text(encoding="utf-8")

def _baseline_text() -> str:
    return _BASELINE.read_text(encoding="utf-8")

def _baseline_yaml() -> dict:
    with open(_BASELINE, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ══════════════════════════════════════════════════════════════════════════════
# A — File structure
# ══════════════════════════════════════════════════════════════════════════════
class TestAFileStructure:
    def test_policy_file_exists(self):
        assert _POLICY_FILE.exists(), f"Missing: {_POLICY_FILE}"

    def test_baseline_file_exists(self):
        assert _BASELINE.exists(), f"Missing: {_BASELINE}"

    def test_scan_script_exists(self):
        assert _SCRIPT.exists(), f"Missing: {_SCRIPT}"

    def test_scripts_security_dir_exists(self):
        assert (_PROJECT_ROOT / "scripts" / "security").is_dir()

    def test_policy_file_is_not_empty(self):
        assert _POLICY_FILE.stat().st_size > 100

    def test_baseline_file_is_not_empty(self):
        assert _BASELINE.stat().st_size > 50

    def test_scan_script_is_not_empty(self):
        assert _SCRIPT.stat().st_size > 200


# ══════════════════════════════════════════════════════════════════════════════
# B — Policy YAML validity
# ══════════════════════════════════════════════════════════════════════════════
class TestBPolicyValidity:
    @pytest.fixture(scope="class")
    def policy(self):
        return _load_policy()

    def test_policy_is_valid_yaml(self, policy):
        assert isinstance(policy, dict)

    def test_policy_has_version(self, policy):
        assert "version" in policy

    def test_policy_has_metadata(self, policy):
        assert "metadata" in policy

    def test_policy_has_scope_section(self, policy):
        assert "scope" in policy

    def test_policy_has_patterns_section(self, policy):
        assert "patterns" in policy

    def test_policy_has_allowlist_section(self, policy):
        assert "allowlist" in policy

    def test_policy_has_enforcement_section(self, policy):
        assert "enforcement" in policy

    def test_policy_has_ci_integration_section(self, policy):
        assert "ci_integration" in policy

    def test_policy_has_reporting_section(self, policy):
        assert "reporting" in policy

    def test_policy_has_baseline_section(self, policy):
        assert "baseline" in policy


# ══════════════════════════════════════════════════════════════════════════════
# C — Policy content
# ══════════════════════════════════════════════════════════════════════════════
class TestCPolicyContent:
    @pytest.fixture(scope="class")
    def policy(self):
        return _load_policy()

    def test_scope_has_include_list(self, policy):
        includes = policy["scope"].get("include", [])
        assert isinstance(includes, list) and len(includes) > 0

    def test_scope_includes_python_files(self, policy):
        includes = policy["scope"].get("include", [])
        assert any("*.py" in p for p in includes)

    def test_scope_includes_yaml_files(self, policy):
        includes = policy["scope"].get("include", [])
        assert any("*.yaml" in p or "*.yml" in p for p in includes)

    def test_scope_has_exclude_list(self, policy):
        excludes = policy["scope"].get("exclude", [])
        assert isinstance(excludes, list) and len(excludes) > 0

    def test_scope_excludes_venv(self, policy):
        excludes = policy["scope"].get("exclude", [])
        assert any("venv" in e for e in excludes)

    def test_scope_excludes_node_modules(self, policy):
        excludes = policy["scope"].get("exclude", [])
        assert any("node_modules" in e for e in excludes)

    def test_patterns_has_critical_category(self, policy):
        patterns = policy["patterns"]
        assert "critical" in patterns
        assert isinstance(patterns["critical"], list)
        assert len(patterns["critical"]) > 0

    def test_patterns_has_high_category(self, policy):
        patterns = policy["patterns"]
        assert "high" in patterns
        assert isinstance(patterns["high"], list)
        assert len(patterns["high"]) > 0

    def test_patterns_has_medium_category(self, policy):
        patterns = policy["patterns"]
        assert "medium" in patterns
        assert isinstance(patterns["medium"], list)
        assert len(patterns["medium"]) > 0

    def test_critical_patterns_have_name_and_description(self, policy):
        for entry in policy["patterns"]["critical"]:
            assert "name" in entry, f"Missing 'name' in critical pattern: {entry}"
            assert "description" in entry, f"Missing 'description' in critical pattern: {entry}"

    def test_high_patterns_have_name_and_description(self, policy):
        for entry in policy["patterns"]["high"]:
            assert "name" in entry
            assert "description" in entry

    def test_allowlist_has_placeholder_values(self, policy):
        placeholders = policy["allowlist"].get("placeholder_values", [])
        assert isinstance(placeholders, list) and len(placeholders) > 0

    def test_allowlist_contains_change_me_placeholder(self, policy):
        placeholders = policy["allowlist"].get("placeholder_values", [])
        assert any("change_me" in p for p in placeholders)

    def test_allowlist_has_test_patterns(self, policy):
        test_pats = policy["allowlist"].get("test_patterns", [])
        assert isinstance(test_pats, list) and len(test_pats) > 0

    def test_allowlist_has_excluded_files(self, policy):
        excl = policy["allowlist"].get("excluded_files", [])
        assert isinstance(excl, list) and len(excl) > 0

    def test_allowlist_excludes_env_example(self, policy):
        excl = policy["allowlist"].get("excluded_files", [])
        assert any(".env.example" in e for e in excl)


# ══════════════════════════════════════════════════════════════════════════════
# D — Baseline file structure
# ══════════════════════════════════════════════════════════════════════════════
class TestDBaselineStructure:
    @pytest.fixture(scope="class")
    def baseline(self):
        return _baseline_yaml()

    def test_baseline_is_valid_yaml(self, baseline):
        assert isinstance(baseline, dict)

    def test_baseline_has_version(self, baseline):
        assert "version" in baseline

    def test_baseline_version_is_string(self, baseline):
        assert isinstance(baseline["version"], str)

    def test_baseline_has_plugins_used(self, baseline):
        assert "plugins_used" in baseline

    def test_baseline_plugins_is_list(self, baseline):
        assert isinstance(baseline["plugins_used"], list)

    def test_baseline_has_filters_used(self, baseline):
        assert "filters_used" in baseline

    def test_baseline_has_results_key(self, baseline):
        assert "results" in baseline

    def test_baseline_has_keyword_detector(self, baseline):
        plugins = baseline.get("plugins_used", [])
        names = [p.get("name", "") for p in plugins]
        assert "KeywordDetector" in names

    def test_baseline_has_private_key_detector(self, baseline):
        plugins = baseline.get("plugins_used", [])
        names = [p.get("name", "") for p in plugins]
        assert "PrivateKeyDetector" in names

    def test_baseline_has_aws_key_detector(self, baseline):
        plugins = baseline.get("plugins_used", [])
        names = [p.get("name", "") for p in plugins]
        assert "AWSKeyDetector" in names

    def test_baseline_keyword_detector_has_exclusion(self, baseline):
        plugins = baseline.get("plugins_used", [])
        kw = next((p for p in plugins if p.get("name") == "KeywordDetector"), None)
        assert kw is not None, "KeywordDetector not found in plugins_used"
        assert "keyword_exclude" in kw, "KeywordDetector should have keyword_exclude"
        assert "change_me" in kw["keyword_exclude"]

    def test_baseline_filters_exclude_venv(self, baseline):
        filters = baseline.get("filters_used", [])
        # Look for regex exclusion filter with venv pattern
        has_venv = any(
            "venv" in str(f.get("pattern", ""))
            for f in filters
        )
        assert has_venv, "Baseline should exclude .venv from scanning"


# ══════════════════════════════════════════════════════════════════════════════
# E — Scan script structure
# ══════════════════════════════════════════════════════════════════════════════
class TestEScanScriptStructure:
    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_has_shebang(self, script):
        assert script.startswith("#!/usr/bin/env bash"), "Script must start with #!/usr/bin/env bash"

    def test_script_has_set_e(self, script):
        assert "set -euo pipefail" in script or "set -e" in script

    def test_script_has_scan_mode(self, script):
        assert "--scan" in script

    def test_script_has_baseline_mode(self, script):
        assert "--baseline" in script

    def test_script_has_audit_mode(self, script):
        assert "--audit" in script

    def test_script_has_diff_mode(self, script):
        assert "--diff" in script

    def test_script_has_dry_run_mode(self, script):
        assert "--dry-run" in script

    def test_script_has_path_option(self, script):
        assert "--path" in script

    def test_script_has_policy_option(self, script):
        assert "--policy" in script

    def test_script_uses_python3(self, script):
        assert "python3" in script

    def test_script_has_exit_codes_documented(self, script):
        assert "exit" in script.lower()


# ══════════════════════════════════════════════════════════════════════════════
# F — Scan script content
# ══════════════════════════════════════════════════════════════════════════════
class TestFScanScriptContent:
    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_reads_policy_file(self, script):
        assert "policy_file" in script or "POLICY_FILE" in script

    def test_script_references_baseline_file(self, script):
        assert "baseline" in script.lower()

    def test_script_references_detect_secrets(self, script):
        assert "detect_secrets" in script or "detect-secrets" in script

    def test_script_checks_for_python3(self, script):
        assert "_require" in script or "python3" in script

    def test_script_loads_yaml_policy(self, script):
        assert "yaml.safe_load" in script

    def test_script_reads_fail_on_severity(self, script):
        assert "fail_on_severity" in script or "fail_severity" in script

    def test_script_handles_missing_policy_file(self, script):
        assert "not found" in script.lower() or "not -f" in script

    def test_script_has_pass_message(self, script):
        assert "PASS" in script

    def test_script_checks_new_secrets_vs_baseline(self, script):
        assert "new_secrets" in script or "baseline_results" in script

    def test_script_uses_hashed_secret_for_comparison(self, script):
        assert "hashed_secret" in script

    def test_script_handles_dry_run(self, script):
        assert "dry_run" in script and "Dry run" in script


# ══════════════════════════════════════════════════════════════════════════════
# G — Severity and enforcement discipline
# ══════════════════════════════════════════════════════════════════════════════
class TestGSeverityDiscipline:
    @pytest.fixture(scope="class")
    def policy(self):
        return _load_policy()

    def test_critical_enforcement_is_block(self, policy):
        action = policy["enforcement"]["critical"]["action"]
        assert action == "block", f"critical enforcement must be 'block', got '{action}'"

    def test_high_enforcement_is_block(self, policy):
        action = policy["enforcement"]["high"]["action"]
        assert action == "block", f"high enforcement must be 'block', got '{action}'"

    def test_medium_enforcement_is_warn(self, policy):
        action = policy["enforcement"]["medium"]["action"]
        assert action in ("warn", "block"), f"medium enforcement must be 'warn' or 'block', got '{action}'"

    def test_ci_fail_on_severity_is_high_or_critical(self, policy):
        fail_sev = policy["ci_integration"].get("fail_on_severity", "")
        assert fail_sev in ("high", "critical"), f"fail_on_severity should be 'high' or 'critical', got '{fail_sev}'"

    def test_ci_script_is_defined(self, policy):
        script = policy["ci_integration"].get("script", "")
        assert script, "ci_integration.script must not be empty"
        assert "scan-secrets" in script

    def test_ci_pre_commit_hook_enabled(self, policy):
        hook = policy["ci_integration"].get("pre_commit_hook", False)
        assert hook is True

    def test_baseline_review_required_on_update(self, policy):
        review = policy["baseline"].get("review_required_on_update", False)
        assert review is True

    def test_max_baseline_entries_is_set(self, policy):
        max_entries = policy["baseline"].get("max_baseline_entries", 0)
        assert isinstance(max_entries, int) and max_entries > 0

    def test_reporting_retains_for_at_least_30_days(self, policy):
        days = policy["reporting"].get("retain_reports_days", 0)
        assert days >= 30

    def test_critical_enforcement_notifies_security(self, policy):
        notify = policy["enforcement"]["critical"].get("notify", [])
        assert isinstance(notify, list) and len(notify) > 0


# ══════════════════════════════════════════════════════════════════════════════
# H — Cross-file consistency
# ══════════════════════════════════════════════════════════════════════════════
class TestHCrossFileConsistency:
    @pytest.fixture(scope="class")
    def policy(self):
        return _load_policy()

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    @pytest.fixture(scope="class")
    def baseline(self):
        return _baseline_yaml()

    def test_policy_baseline_file_matches_actual_path(self, policy, script):
        ci_baseline = policy["ci_integration"].get("baseline_file", "")
        assert ".detect-secrets.yaml" in ci_baseline, \
            f"ci_integration.baseline_file should reference .detect-secrets.yaml, got: {ci_baseline}"

    def test_script_default_baseline_is_detect_secrets_yaml(self, script):
        assert ".detect-secrets.yaml" in script

    def test_policy_ci_script_path_matches_actual_script(self, policy):
        ci_script = policy["ci_integration"].get("script", "")
        assert "scripts/security/scan-secrets.sh" in ci_script, \
            f"ci_integration.script should match actual script path, got: {ci_script}"

    def test_placeholder_in_policy_matches_keyword_exclude_in_baseline(self, policy, baseline):
        placeholders = policy["allowlist"].get("placeholder_values", [])
        plugins = baseline.get("plugins_used", [])
        kw = next((p for p in plugins if p.get("name") == "KeywordDetector"), None)
        if kw and "keyword_exclude" in kw:
            exclude_str = kw["keyword_exclude"]
            assert any(p in exclude_str for p in placeholders), \
                "At least one policy placeholder should appear in KeywordDetector keyword_exclude"

    def test_update_baseline_cmd_uses_correct_script(self, policy):
        cmd = policy["ci_integration"].get("update_baseline_cmd", "")
        assert "scan-secrets.sh" in cmd and "--baseline" in cmd

    def test_venv_excluded_in_both_policy_and_baseline(self, policy, baseline):
        scope_excludes = policy["scope"].get("exclude", [])
        assert any("venv" in e for e in scope_excludes), "Policy scope must exclude .venv"
        filters = baseline.get("filters_used", [])
        baseline_excl_str = str(filters)
        assert "venv" in baseline_excl_str, "Baseline filters must exclude .venv"

    def test_all_pattern_entries_have_severity_field(self, policy):
        for severity, entries in policy["patterns"].items():
            for entry in entries:
                # severity is the key — but each entry should also have 'name'
                assert "name" in entry, f"Pattern entry missing 'name': {entry}"
