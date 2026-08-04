"""Gap 19 — Container Security Scanning (Trivy): infrastructure-file-validation tests.

Validates:
  A. File structure (config/trivy.yaml, .trivyignore, scripts/scan/scan-containers.sh)
  B. trivy.yaml validity (YAML, severity field, exit-code field)
  C. trivy.yaml content (severity values, exit-code gate, key sections present)
  D. .trivyignore format (file exists, header comment, expiry discipline documented)
  E. Scan script structure (shebang, error handling, trivy invocation, image list)
  F. Scan script content (both scan modes, exit-code handling, FAIL_ON_FINDINGS gate)
  G. License policy in trivy config (forbidden licenses, consistency with SBOM policy)
  H. Report output config (report directory, JSON format, config/iac scan outputs)
"""

from __future__ import annotations

import re
import yaml
import pytest
from pathlib import Path

from tests._repo import REPO_ROOT, requires_repo_tree

# Skips the whole module when the repository tree is absent (e.g. inside the
# api image, which ships only backend/). See tests/_repo.py for why failing
# would be the wrong signal here.
pytestmark = requires_repo_tree


_HERE         = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]          # /app/backend/tests/ → /app/
_CONFIG_PATH  = _PROJECT_ROOT / "config" / "trivy.yaml"
_IGNORE_PATH  = _PROJECT_ROOT / ".trivyignore"
_SCRIPT_PATH  = _PROJECT_ROOT / "scripts" / "scan" / "scan-containers.sh"
_REPORT_DIR   = _PROJECT_ROOT / "reports" / "trivy"

KNOWN_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"}

# SPDX IDs that sbom-policy.yml blocks — trivy.yaml must block the same set
# (minimum overlap check — trivy may block additional identifiers)
SBOM_BLOCKED_LICENSES = {
    "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later",
    "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
    "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
    "SSPL-1.0",
}


def _load_yaml(path: Path) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _script_text() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def _ignore_text() -> str:
    return _IGNORE_PATH.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_trivy_config_exists(self):
        assert _CONFIG_PATH.exists(), f"Missing: {_CONFIG_PATH}"

    def test_trivyignore_exists(self):
        assert _IGNORE_PATH.exists(), f"Missing: {_IGNORE_PATH}"

    def test_scan_script_exists(self):
        assert _SCRIPT_PATH.exists(), f"Missing: {_SCRIPT_PATH}"

    def test_report_directory_exists(self):
        assert _REPORT_DIR.is_dir(), f"Missing reports directory: {_REPORT_DIR}"


# ─────────────────────────────────────────────────────────────
# B — trivy.yaml validity
# ─────────────────────────────────────────────────────────────

class TestBTrivyConfigValidity:

    @pytest.fixture(scope="class")
    def cfg(self):
        return _load_yaml(_CONFIG_PATH)

    def test_trivy_yaml_valid_yaml(self, cfg):
        assert isinstance(cfg, dict), "config/trivy.yaml must be a YAML mapping"

    def test_trivy_yaml_has_severity(self, cfg):
        assert "severity" in cfg, "trivy.yaml missing 'severity' field"

    def test_trivy_yaml_severity_is_list(self, cfg):
        assert isinstance(cfg.get("severity"), list), (
            "trivy.yaml 'severity' must be a list"
        )

    def test_trivy_yaml_has_exit_code(self, cfg):
        assert "exit-code" in cfg, (
            "trivy.yaml missing 'exit-code' field — CI cannot gate on scan results"
        )

    def test_trivy_yaml_has_vulnerability_section(self, cfg):
        assert "vulnerability" in cfg, "trivy.yaml missing 'vulnerability' section"


# ─────────────────────────────────────────────────────────────
# C — trivy.yaml content
# ─────────────────────────────────────────────────────────────

class TestCTrivyConfigContent:

    @pytest.fixture(scope="class")
    def cfg(self):
        return _load_yaml(_CONFIG_PATH)

    def test_severity_values_are_known(self, cfg):
        for sev in cfg.get("severity", []):
            assert sev in KNOWN_SEVERITIES, (
                f"trivy.yaml severity '{sev}' is not a valid Trivy severity level"
            )

    def test_critical_severity_included(self, cfg):
        assert "CRITICAL" in cfg.get("severity", []), (
            "trivy.yaml must include CRITICAL in severity list"
        )

    def test_high_severity_included(self, cfg):
        assert "HIGH" in cfg.get("severity", []), (
            "trivy.yaml must include HIGH in severity list"
        )

    def test_exit_code_is_nonzero(self, cfg):
        exit_code = cfg.get("exit-code")
        assert exit_code == 1, (
            f"trivy.yaml exit-code must be 1 (fail on findings), got {exit_code}"
        )

    def test_vulnerability_type_includes_os(self, cfg):
        vuln_types = cfg.get("vulnerability", {}).get("type", [])
        assert "os" in vuln_types, (
            "trivy.yaml vulnerability.type must include 'os' for OS package scanning"
        )

    def test_vulnerability_type_includes_library(self, cfg):
        vuln_types = cfg.get("vulnerability", {}).get("type", [])
        assert "library" in vuln_types, (
            "trivy.yaml vulnerability.type must include 'library' for dependency scanning"
        )

    def test_cache_dir_defined(self, cfg):
        cache = cfg.get("cache", {})
        assert "dir" in cache, "trivy.yaml cache section must define a cache dir"


# ─────────────────────────────────────────────────────────────
# D — .trivyignore format
# ─────────────────────────────────────────────────────────────

class TestDTrivyIgnoreFormat:

    @pytest.fixture(scope="class")
    def text(self):
        return _ignore_text()

    def test_trivyignore_is_non_empty(self, text):
        assert text.strip(), ".trivyignore must not be completely empty"

    def test_trivyignore_has_header_comment(self, text):
        has_comment = any(line.startswith("#") for line in text.splitlines()[:5])
        assert has_comment, ".trivyignore must begin with a comment header"

    def test_trivyignore_documents_expiry_discipline(self, text):
        assert "exp:" in text or "expir" in text.lower(), (
            ".trivyignore must document the expiry (exp:) requirement for exceptions"
        )

    def test_trivyignore_documents_reason_requirement(self, text):
        assert "reason" in text.lower(), (
            ".trivyignore must document the mandatory 'reason:' field for exceptions"
        )

    def test_trivyignore_no_permanent_exceptions_without_expiry(self, text):
        cve_lines = [l.strip() for l in text.splitlines()
                     if l.strip().startswith("CVE-") and not l.strip().startswith("#")]
        exp_lines = [l.strip() for l in text.splitlines()
                     if l.strip().startswith("exp:")]
        # Each CVE entry should have a corresponding exp: line
        assert len(cve_lines) == len(exp_lines), (
            f"Each CVE exception must have an 'exp:' expiry line. "
            f"Found {len(cve_lines)} CVE entries but {len(exp_lines)} exp: lines."
        )


# ─────────────────────────────────────────────────────────────
# E — Scan script structure
# ─────────────────────────────────────────────────────────────

class TestEScanScriptStructure:

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_has_bash_shebang(self, script):
        first_line = script.splitlines()[0]
        assert first_line.startswith("#!/"), (
            "scan-containers.sh must start with a shebang"
        )
        assert "bash" in first_line or "sh" in first_line, (
            f"Shebang must reference bash or sh, got: '{first_line}'"
        )

    def test_script_has_error_handling(self, script):
        assert "set -e" in script or "set -euo" in script, (
            "scan-containers.sh must use 'set -e' or 'set -euo pipefail'"
        )

    def test_script_invokes_trivy(self, script):
        assert "trivy" in script, "scan-containers.sh must invoke trivy"

    def test_script_references_trivy_config(self, script):
        assert "trivy.yaml" in script or "TRIVY_CONFIG" in script, (
            "scan-containers.sh must reference the trivy config file"
        )

    def test_script_references_trivyignore(self, script):
        assert ".trivyignore" in script or "ignorefile" in script or "ignore-file" in script, (
            "scan-containers.sh must reference .trivyignore so exceptions are applied"
        )

    def test_script_has_minimum_image_list(self, script):
        assert "api" in script.lower(), (
            "scan-containers.sh must reference at least the 'api' image"
        )

    def test_script_has_more_than_15_non_empty_lines(self, script):
        non_empty = [l for l in script.splitlines() if l.strip()]
        assert len(non_empty) > 15, (
            f"scan-containers.sh has only {len(non_empty)} non-empty lines — expected a complete script"
        )


# ─────────────────────────────────────────────────────────────
# F — Scan script content
# ─────────────────────────────────────────────────────────────

class TestFScanScriptContent:

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_performs_image_scan(self, script):
        assert "trivy image" in script, (
            "scan-containers.sh must run 'trivy image' for container vulnerability scanning"
        )

    def test_script_performs_config_scan(self, script):
        assert "trivy config" in script or "trivy fs" in script, (
            "scan-containers.sh must run 'trivy config' or 'trivy fs' for Dockerfile scanning"
        )

    def test_script_handles_scan_exit_code(self, script):
        has_exit_handling = (
            "SCAN_EXIT" in script
            or "|| exit" in script
            or "exit_code" in script.lower()
            or "FAIL_ON_FINDINGS" in script
        )
        assert has_exit_handling, (
            "scan-containers.sh must handle trivy exit code so CI gates on findings"
        )

    def test_script_outputs_json_report(self, script):
        assert "json" in script.lower(), (
            "scan-containers.sh must produce JSON output for programmatic processing"
        )

    def test_script_writes_to_report_directory(self, script):
        assert "reports" in script.lower() or "REPORT_DIR" in script, (
            "scan-containers.sh must write reports to a reports/ directory"
        )


# ─────────────────────────────────────────────────────────────
# G — License policy consistency with SBOM
# ─────────────────────────────────────────────────────────────

class TestGLicensePolicyConsistency:

    @pytest.fixture(scope="class")
    def cfg(self):
        return _load_yaml(_CONFIG_PATH)

    def test_license_section_exists(self, cfg):
        assert "license" in cfg, (
            "trivy.yaml missing 'license' section — license scanning is not configured"
        )

    def test_license_forbidden_list_exists(self, cfg):
        forbidden = cfg.get("license", {}).get("forbidden")
        assert isinstance(forbidden, list), (
            "trivy.yaml license.forbidden must be a list of SPDX IDs"
        )

    def test_gpl3_blocked_in_trivy(self, cfg):
        forbidden = set(cfg.get("license", {}).get("forbidden", []))
        gpl3 = {l for l in forbidden if "GPL-3.0" in l}
        assert gpl3, "trivy.yaml must block GPL-3.0 variants in license.forbidden"

    def test_agpl_blocked_in_trivy(self, cfg):
        forbidden = set(cfg.get("license", {}).get("forbidden", []))
        agpl = {l for l in forbidden if "AGPL" in l}
        assert agpl, "trivy.yaml must block AGPL variants in license.forbidden"

    def test_trivy_blocks_all_sbom_policy_licenses(self, cfg):
        trivy_forbidden = set(cfg.get("license", {}).get("forbidden", []))
        missing = SBOM_BLOCKED_LICENSES - trivy_forbidden
        assert not missing, (
            f"trivy.yaml must block at minimum the same licenses as sbom-policy.yml. "
            f"Missing from trivy forbidden: {missing}"
        )


# ─────────────────────────────────────────────────────────────
# H — Report output config
# ─────────────────────────────────────────────────────────────

class TestHReportOutputConfig:

    @pytest.fixture(scope="class")
    def cfg(self):
        return _load_yaml(_CONFIG_PATH)

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_reports_trivy_dir_exists(self):
        assert _REPORT_DIR.is_dir(), f"Missing: {_REPORT_DIR}"

    def test_trivy_config_format_is_json(self, cfg):
        fmt = cfg.get("format", "")
        assert fmt == "json", (
            f"trivy.yaml format must be 'json' for machine-readable reports, got '{fmt}'"
        )

    def test_script_produces_iac_report(self, script):
        has_iac = (
            "iac" in script.lower()
            or "misconfiguration" in script.lower()
            or "trivy config" in script
        )
        assert has_iac, (
            "scan-containers.sh must produce an IaC/Dockerfile misconfiguration report"
        )

    def test_script_produces_named_report_files(self, script):
        has_named_output = "--output" in script or "-o " in script or "output" in script
        assert has_named_output, (
            "scan-containers.sh must write reports to named files, not just stdout"
        )

    def test_timeout_configured_in_trivy(self, cfg):
        assert "timeout" in cfg, (
            "trivy.yaml must set a timeout to prevent CI hangs on large images"
        )
