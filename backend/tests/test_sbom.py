"""Gap 17 — SBOM (Software Bill of Materials): infrastructure-file-validation tests.

Validates:
  A. File structure (scripts/sbom directory + 3 deliverable files)
  B. Generate script content (shebang, tools, formats, error handling)
  C. syft-config.yml validity (YAML, output formats, exclude patterns)
  D. sbom-policy.yml validity (YAML, top-level sections)
  E. License policy content (allowed/blocked SPDX IDs, key licenses)
  F. Vulnerability policy content (fail_on/warn_on, critical threshold, exceptions)
  G. Output format compliance (cyclonedx-json, spdx-json, output paths)
  H. Script completeness (sbom dir, line count, grype exit-code handling)
"""

from __future__ import annotations

import yaml
import pytest
from pathlib import Path

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]  # /app/backend/tests/ → /app/
_SBOM_DIR = _PROJECT_ROOT / "scripts" / "sbom"
_SCRIPT_PATH = _SBOM_DIR / "generate-sbom.sh"
_SYFT_CONFIG_PATH = _SBOM_DIR / "syft-config.yml"
_POLICY_PATH = _SBOM_DIR / "sbom-policy.yml"

ALLOWED_GRYPE_SEVERITIES = {"negligible", "low", "medium", "high", "critical"}


def _load_yaml(path: Path) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _script_text() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_sbom_scripts_directory_exists(self):
        assert _SBOM_DIR.is_dir(), f"Missing: {_SBOM_DIR}"

    def test_generate_sbom_script_exists(self):
        assert _SCRIPT_PATH.exists(), f"Missing: {_SCRIPT_PATH}"

    def test_syft_config_yml_exists(self):
        assert _SYFT_CONFIG_PATH.exists(), f"Missing: {_SYFT_CONFIG_PATH}"

    def test_sbom_policy_yml_exists(self):
        assert _POLICY_PATH.exists(), f"Missing: {_POLICY_PATH}"


# ─────────────────────────────────────────────────────────────
# B — Generate script content
# ─────────────────────────────────────────────────────────────

class TestBGenerateScriptContent:

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_has_bash_shebang(self, script):
        first_line = script.splitlines()[0]
        assert first_line.startswith("#!/"), (
            f"generate-sbom.sh must start with a shebang, got: '{first_line}'"
        )
        assert "bash" in first_line or "sh" in first_line, (
            f"Shebang should reference bash or sh, got: '{first_line}'"
        )

    def test_script_references_syft(self, script):
        assert "syft" in script, "generate-sbom.sh must invoke syft for component cataloging"

    def test_script_references_grype(self, script):
        assert "grype" in script, "generate-sbom.sh must invoke grype for vulnerability scanning"

    def test_script_references_cyclonedx_format(self, script):
        assert "cyclonedx" in script.lower(), (
            "generate-sbom.sh must reference cyclonedx output format"
        )

    def test_script_references_spdx_format(self, script):
        assert "spdx" in script.lower(), (
            "generate-sbom.sh must reference spdx output format"
        )

    def test_script_has_error_handling(self, script):
        assert "set -e" in script or "set -euo" in script or "set -eu" in script, (
            "generate-sbom.sh must use 'set -e' or 'set -euo pipefail' for error handling"
        )


# ─────────────────────────────────────────────────────────────
# C — syft-config.yml validity
# ─────────────────────────────────────────────────────────────

class TestCSyftConfigValidity:

    @pytest.fixture(scope="class")
    def config(self):
        return _load_yaml(_SYFT_CONFIG_PATH)

    def test_syft_config_valid_yaml(self, config):
        assert config is not None and isinstance(config, dict)

    def test_syft_config_has_output_section(self, config):
        assert "output" in config, "syft-config.yml missing 'output' section"

    def test_syft_config_output_is_list(self, config):
        assert isinstance(config.get("output"), list), "'output' must be a list of format entries"

    def test_syft_config_output_has_cyclonedx(self, config):
        formats = [str(e.get("format", "")).lower() for e in config.get("output", [])]
        assert any("cyclonedx" in f for f in formats), (
            "syft-config.yml must include a cyclonedx output format"
        )

    def test_syft_config_has_exclude_patterns(self, config):
        assert "exclude" in config, "syft-config.yml missing 'exclude' section"

    def test_syft_config_excludes_common_noise_paths(self, config):
        excludes = [str(e) for e in config.get("exclude", [])]
        noise_paths = ["node_modules", "__pycache__", "venv"]
        for noise in noise_paths:
            has_noise = any(noise in e for e in excludes)
            assert has_noise, f"syft-config.yml should exclude '{noise}' paths"


# ─────────────────────────────────────────────────────────────
# D — sbom-policy.yml validity
# ─────────────────────────────────────────────────────────────

class TestDSbomPolicyValidity:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_sbom_policy_valid_yaml(self, policy):
        assert isinstance(policy, dict)

    def test_sbom_policy_has_vulnerability_policy(self, policy):
        assert "vulnerability_policy" in policy, "sbom-policy.yml missing 'vulnerability_policy'"

    def test_sbom_policy_has_license_policy(self, policy):
        assert "license_policy" in policy, "sbom-policy.yml missing 'license_policy'"

    def test_vulnerability_policy_has_fail_on(self, policy):
        vp = policy.get("vulnerability_policy", {})
        assert "fail_on" in vp, "vulnerability_policy missing 'fail_on'"

    def test_vulnerability_policy_has_warn_on(self, policy):
        vp = policy.get("vulnerability_policy", {})
        assert "warn_on" in vp, "vulnerability_policy missing 'warn_on'"

    def test_license_policy_has_allowed(self, policy):
        lp = policy.get("license_policy", {})
        assert "allowed" in lp, "license_policy missing 'allowed'"

    def test_license_policy_has_blocked(self, policy):
        lp = policy.get("license_policy", {})
        assert "blocked" in lp, "license_policy missing 'blocked'"


# ─────────────────────────────────────────────────────────────
# E — License policy content
# ─────────────────────────────────────────────────────────────

class TestELicensePolicyContent:

    @pytest.fixture(scope="class")
    def license_policy(self):
        return _load_yaml(_POLICY_PATH).get("license_policy", {})

    def test_mit_is_in_allowed_licenses(self, license_policy):
        assert "MIT" in license_policy.get("allowed", []), "MIT must be in allowed licenses"

    def test_apache2_is_in_allowed_licenses(self, license_policy):
        assert "Apache-2.0" in license_policy.get("allowed", []), (
            "Apache-2.0 must be in allowed licenses"
        )

    def test_gpl3_is_in_blocked_licenses(self, license_policy):
        blocked = license_policy.get("blocked", [])
        gpl3_variants = [l for l in blocked if "GPL-3.0" in l]
        assert gpl3_variants, "GPL-3.0 (or variant) must be in blocked licenses"

    def test_agpl_is_in_blocked_licenses(self, license_policy):
        blocked = license_policy.get("blocked", [])
        agpl_variants = [l for l in blocked if "AGPL" in l]
        assert agpl_variants, "AGPL-3.0 (or variant) must be in blocked licenses"

    def test_allowed_list_has_minimum_entries(self, license_policy):
        allowed = license_policy.get("allowed", [])
        assert len(allowed) >= 3, f"Expected at least 3 allowed licenses, got {len(allowed)}"

    def test_blocked_list_has_minimum_entries(self, license_policy):
        blocked = license_policy.get("blocked", [])
        assert len(blocked) >= 3, f"Expected at least 3 blocked licenses, got {len(blocked)}"

    def test_allowed_and_blocked_are_disjoint(self, license_policy):
        allowed = set(license_policy.get("allowed", []))
        blocked = set(license_policy.get("blocked", []))
        overlap = allowed & blocked
        assert not overlap, f"Licenses appear in both allowed and blocked: {overlap}"


# ─────────────────────────────────────────────────────────────
# F — Vulnerability policy content
# ─────────────────────────────────────────────────────────────

class TestFVulnerabilityPolicyContent:

    @pytest.fixture(scope="class")
    def vuln_policy(self):
        return _load_yaml(_POLICY_PATH).get("vulnerability_policy", {})

    def test_fail_on_has_at_least_two_entries(self, vuln_policy):
        fail_on = vuln_policy.get("fail_on", [])
        assert len(fail_on) >= 2, f"Expected at least 2 fail_on entries, got {len(fail_on)}"

    def test_every_fail_on_entry_has_severity(self, vuln_policy):
        for entry in vuln_policy.get("fail_on", []):
            assert "severity" in entry, f"fail_on entry missing 'severity': {entry}"

    def test_critical_severity_is_in_fail_on(self, vuln_policy):
        severities = [e.get("severity", "").lower() for e in vuln_policy.get("fail_on", [])]
        assert "critical" in severities, "critical severity must be in fail_on"

    def test_every_fail_on_entry_has_count(self, vuln_policy):
        for entry in vuln_policy.get("fail_on", []):
            assert "count" in entry, f"fail_on entry missing 'count': {entry}"

    def test_exceptions_key_exists(self, vuln_policy):
        assert "exceptions" in vuln_policy, (
            "vulnerability_policy missing 'exceptions' key "
            "(may be empty list, but must exist for documentation)"
        )


# ─────────────────────────────────────────────────────────────
# G — Output format compliance
# ─────────────────────────────────────────────────────────────

class TestGOutputFormatCompliance:

    @pytest.fixture(scope="class")
    def config(self):
        return _load_yaml(_SYFT_CONFIG_PATH)

    def test_cyclonedx_json_format_listed(self, config):
        formats = [str(e.get("format", "")) for e in config.get("output", [])]
        assert any("cyclonedx-json" in f for f in formats), (
            "syft-config.yml must list 'cyclonedx-json' output format"
        )

    def test_spdx_json_format_listed(self, config):
        formats = [str(e.get("format", "")) for e in config.get("output", [])]
        assert any("spdx-json" in f for f in formats), (
            "syft-config.yml must list 'spdx-json' output format"
        )

    def test_output_file_paths_reference_sbom_directory(self, config):
        files = [str(e.get("file", "")) for e in config.get("output", []) if "file" in e]
        assert files, "syft-config.yml output entries should include file paths"
        for f in files:
            assert "sbom" in f.lower() or f == "", (
                f"Output file path '{f}' should be under sbom/ directory"
            )

    def test_source_section_has_name(self, config):
        source = config.get("source", {})
        assert "name" in source, "syft-config.yml source section missing 'name'"
        assert source["name"], "syft-config.yml source.name must be non-empty"


# ─────────────────────────────────────────────────────────────
# H — Script completeness
# ─────────────────────────────────────────────────────────────

class TestHScriptCompleteness:

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_references_sbom_output_directory(self, script):
        assert "sbom" in script.lower(), (
            "generate-sbom.sh must reference the sbom/ output directory"
        )

    def test_script_has_more_than_10_lines(self, script):
        lines = [l for l in script.splitlines() if l.strip()]
        assert len(lines) > 10, (
            f"generate-sbom.sh has only {len(lines)} non-empty lines — "
            "expected a complete script"
        )

    def test_script_handles_grype_exit_code(self, script):
        has_exit_handling = (
            "VULN_EXIT" in script
            or "|| exit" in script
            or "|| {" in script
            or "$?" in script
        )
        assert has_exit_handling, (
            "generate-sbom.sh must handle grype exit code so CI gates on vulnerability findings"
        )
