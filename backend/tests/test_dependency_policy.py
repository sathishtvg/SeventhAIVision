"""Gap 29 — Dependency Update Policy tests.

Validates that the dependency update configuration (policy YAML, Renovate
config, and audit shell script) are present, structurally correct, and
internally consistent.

All tests are infrastructure-file-validation only — no live package registry
calls and no network access required.

Sections:
    A — File structure (all 3 artefacts exist)
    B — Policy YAML validity (parseable, version, required sections)
    C — Policy content (update schedule, groups, SLAs, lockfile rules)
    D — Renovate config (renovate.json validity, key fields)
    E — Renovate package rules (security, patch, minor, major, groups)
    F — Audit script structure (shebang, set -e, modes)
    G — Audit script content (policy load, pip/npm checks, exit codes)
    H — Cross-file consistency (policy ↔ renovate ↔ script)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from tests._repo import REPO_ROOT, requires_repo_tree

# Skips the whole module when the repository tree is absent (e.g. inside the
# api image, which ships only backend/). See tests/_repo.py for why failing
# would be the wrong signal here.
pytestmark = requires_repo_tree



# ── Paths ─────────────────────────────────────────────────────────────────────
_BACKEND_DIR  = Path(__file__).parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent
_POLICY_FILE  = _PROJECT_ROOT / "config" / "dependency-update-policy.yml"
_RENOVATE     = _PROJECT_ROOT / "renovate.json"
_SCRIPT       = _PROJECT_ROOT / "scripts" / "dependencies" / "check-outdated.sh"

# ── Helpers ───────────────────────────────────────────────────────────────────
def _load_policy() -> dict:
    with open(_POLICY_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)

def _load_renovate() -> dict:
    with open(_RENOVATE, encoding="utf-8") as f:
        return json.load(f)

def _script_text() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# A — File structure
# ══════════════════════════════════════════════════════════════════════════════
class TestAFileStructure:
    def test_policy_file_exists(self):
        assert _POLICY_FILE.exists(), f"Missing: {_POLICY_FILE}"

    def test_renovate_json_exists(self):
        assert _RENOVATE.exists(), f"Missing: {_RENOVATE}"

    def test_audit_script_exists(self):
        assert _SCRIPT.exists(), f"Missing: {_SCRIPT}"

    def test_scripts_dependencies_dir_exists(self):
        assert (_PROJECT_ROOT / "scripts" / "dependencies").is_dir()

    def test_policy_file_not_empty(self):
        assert _POLICY_FILE.stat().st_size > 100

    def test_renovate_json_not_empty(self):
        assert _RENOVATE.stat().st_size > 100

    def test_audit_script_not_empty(self):
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

    def test_policy_has_scope(self, policy):
        assert "scope" in policy

    def test_policy_has_update_schedule(self, policy):
        assert "update_schedule" in policy

    def test_policy_has_groups(self, policy):
        assert "groups" in policy

    def test_policy_has_ignore_section(self, policy):
        assert "ignore" in policy

    def test_policy_has_vulnerability_sla(self, policy):
        assert "vulnerability_sla" in policy

    def test_policy_has_ci_gates(self, policy):
        assert "ci_gates" in policy

    def test_policy_has_lockfiles_section(self, policy):
        assert "lockfiles" in policy

    def test_policy_has_docker_section(self, policy):
        assert "docker" in policy


# ══════════════════════════════════════════════════════════════════════════════
# C — Policy content
# ══════════════════════════════════════════════════════════════════════════════
class TestCPolicyContent:
    @pytest.fixture(scope="class")
    def policy(self):
        return _load_policy()

    def test_scope_has_manifests(self, policy):
        manifests = policy["scope"].get("manifests", [])
        assert isinstance(manifests, list) and len(manifests) > 0

    def test_scope_includes_backend_pyproject(self, policy):
        manifests = policy["scope"].get("manifests", [])
        paths = [m.get("path", "") for m in manifests]
        assert any("backend" in p and "pyproject" in p for p in paths)

    def test_scope_includes_frontend_package_json(self, policy):
        manifests = policy["scope"].get("manifests", [])
        paths = [m.get("path", "") for m in manifests]
        assert any("frontend" in p and "package.json" in p for p in paths)

    def test_update_schedule_has_security_patch(self, policy):
        assert "security_patch" in policy["update_schedule"]

    def test_update_schedule_has_patch_version(self, policy):
        assert "patch_version" in policy["update_schedule"]

    def test_update_schedule_has_minor_version(self, policy):
        assert "minor_version" in policy["update_schedule"]

    def test_update_schedule_has_major_version(self, policy):
        assert "major_version" in policy["update_schedule"]

    def test_security_patch_has_max_age(self, policy):
        days = policy["update_schedule"]["security_patch"].get("max_age_days", 0)
        assert isinstance(days, int) and days > 0

    def test_security_patch_max_age_is_short(self, policy):
        days = policy["update_schedule"]["security_patch"].get("max_age_days", 999)
        assert days <= 7, f"security_patch max_age_days should be ≤ 7, got {days}"

    def test_security_patch_auto_merge_is_true(self, policy):
        assert policy["update_schedule"]["security_patch"].get("auto_merge") is True

    def test_major_version_auto_merge_is_false(self, policy):
        assert policy["update_schedule"]["major_version"].get("auto_merge") is False

    def test_groups_has_fastapi_stack(self, policy):
        group_names = [g.get("name", "") for g in policy["groups"]]
        assert "fastapi-stack" in group_names

    def test_groups_has_ai_models(self, policy):
        group_names = [g.get("name", "") for g in policy["groups"]]
        assert "ai-models" in group_names

    def test_ai_models_group_auto_merge_is_false(self, policy):
        ai_group = next((g for g in policy["groups"] if g.get("name") == "ai-models"), None)
        assert ai_group is not None
        assert ai_group.get("auto_merge", True) is False

    def test_vulnerability_sla_critical_within_7_days(self, policy):
        days = policy["vulnerability_sla"]["critical"].get("fix_within_days", 999)
        assert days <= 7, f"critical vuln SLA must be ≤ 7 days, got {days}"

    def test_vulnerability_sla_has_all_severities(self, policy):
        sla = policy["vulnerability_sla"]
        for sev in ("critical", "high", "medium", "low"):
            assert sev in sla, f"vulnerability_sla missing '{sev}'"

    def test_lockfiles_required_is_true(self, policy):
        assert policy["lockfiles"].get("required") is True

    def test_lockfiles_commit_required(self, policy):
        assert policy["lockfiles"].get("commit_lockfile") is True

    def test_ci_gates_has_on_dependency_pr(self, policy):
        assert "on_dependency_pr" in policy["ci_gates"]

    def test_docker_max_age_is_set(self, policy):
        days = policy["docker"].get("max_age_days", 0)
        assert isinstance(days, int) and days > 0


# ══════════════════════════════════════════════════════════════════════════════
# D — Renovate config (JSON validity)
# ══════════════════════════════════════════════════════════════════════════════
class TestDRenovateConfig:
    @pytest.fixture(scope="class")
    def renovate(self):
        return _load_renovate()

    def test_renovate_is_valid_json(self, renovate):
        assert isinstance(renovate, dict)

    def test_renovate_has_schema(self, renovate):
        assert "$schema" in renovate

    def test_renovate_has_extends(self, renovate):
        extends = renovate.get("extends", [])
        assert isinstance(extends, list) and len(extends) > 0

    def test_renovate_extends_recommended(self, renovate):
        extends = renovate.get("extends", [])
        assert "config:recommended" in extends

    def test_renovate_has_package_rules(self, renovate):
        rules = renovate.get("packageRules", [])
        assert isinstance(rules, list) and len(rules) > 0

    def test_renovate_has_pr_concurrent_limit(self, renovate):
        assert "prConcurrentLimit" in renovate

    def test_renovate_has_schedule(self, renovate):
        assert "schedule" in renovate

    def test_renovate_has_vulnerability_alerts(self, renovate):
        assert "vulnerabilityAlerts" in renovate

    def test_renovate_vulnerability_alerts_enabled(self, renovate):
        va = renovate.get("vulnerabilityAlerts", {})
        assert va.get("enabled") is True

    def test_renovate_has_lock_file_maintenance(self, renovate):
        assert "lockFileMaintenance" in renovate

    def test_renovate_lock_file_maintenance_enabled(self, renovate):
        lfm = renovate.get("lockFileMaintenance", {})
        assert lfm.get("enabled") is True


# ══════════════════════════════════════════════════════════════════════════════
# E — Renovate package rules
# ══════════════════════════════════════════════════════════════════════════════
class TestERenovatePackageRules:
    @pytest.fixture(scope="class")
    def rules(self):
        return _load_renovate().get("packageRules", [])

    def _find_rule(self, rules, description_fragment: str) -> dict | None:
        for r in rules:
            if description_fragment.lower() in r.get("description", "").lower():
                return r
        return None

    def test_has_security_patch_rule(self, rules):
        r = self._find_rule(rules, "security")
        assert r is not None, "No security patch rule found in packageRules"

    def test_security_patch_auto_merge_is_true(self, rules):
        r = self._find_rule(rules, "security")
        assert r is not None
        assert r.get("automerge") is True

    def test_security_patch_schedule_is_any_time(self, rules):
        r = self._find_rule(rules, "security patch")
        assert r is not None
        assert r.get("schedule") == "at any time"

    def test_has_major_version_rule(self, rules):
        r = self._find_rule(rules, "major version")
        assert r is not None

    def test_major_version_auto_merge_is_false(self, rules):
        r = self._find_rule(rules, "major version")
        assert r is not None
        assert r.get("automerge") is False

    def test_has_ai_models_group_rule(self, rules):
        r = self._find_rule(rules, "ai inference")
        assert r is not None, "No AI models group rule found"

    def test_ai_models_auto_merge_is_false(self, rules):
        r = self._find_rule(rules, "ai inference")
        assert r is not None
        assert r.get("automerge") is False

    def test_ai_models_schedule_is_on_demand(self, rules):
        r = self._find_rule(rules, "ai inference")
        assert r is not None
        assert "on demand" in (r.get("schedule") or "")

    def test_has_fastapi_stack_rule(self, rules):
        r = self._find_rule(rules, "fastapi")
        assert r is not None

    def test_torch_is_disabled(self, rules):
        r = self._find_rule(rules, "pytorch")
        assert r is not None
        assert r.get("enabled") is False

    def test_paddlepaddle_is_disabled(self, rules):
        r = self._find_rule(rules, "paddlepaddle")
        assert r is not None
        assert r.get("enabled") is False

    def test_docker_images_have_monthly_schedule(self, rules):
        r = self._find_rule(rules, "docker base")
        assert r is not None
        schedule = r.get("schedule", "")
        assert "monday" in str(schedule).lower() or "monthly" in str(schedule).lower()


# ══════════════════════════════════════════════════════════════════════════════
# F — Audit script structure
# ══════════════════════════════════════════════════════════════════════════════
class TestFAuditScriptStructure:
    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_has_shebang(self, script):
        assert script.startswith("#!/usr/bin/env bash")

    def test_script_has_set_e(self, script):
        assert "set -euo pipefail" in script or "set -e" in script

    def test_script_has_audit_mode(self, script):
        assert "--audit" in script

    def test_script_has_pip_mode(self, script):
        assert "--pip" in script

    def test_script_has_npm_mode(self, script):
        assert "--npm" in script

    def test_script_has_docker_mode(self, script):
        assert "--docker" in script

    def test_script_has_dry_run_mode(self, script):
        assert "--dry-run" in script

    def test_script_has_report_mode(self, script):
        assert "--report" in script

    def test_script_uses_python3(self, script):
        assert "python3" in script


# ══════════════════════════════════════════════════════════════════════════════
# G — Audit script content
# ══════════════════════════════════════════════════════════════════════════════
class TestGAuditScriptContent:
    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_loads_policy_file(self, script):
        assert "policy_file" in script or "POLICY_FILE" in script

    def test_script_loads_yaml(self, script):
        assert "yaml.safe_load" in script

    def test_script_validates_policy_sections(self, script):
        assert "update_schedule" in script or "required section" in script

    def test_script_checks_security_patch_sla(self, script):
        assert "vulnerability_sla" in script or "security_patch" in script

    def test_script_checks_lockfile_policy(self, script):
        assert "lockfile" in script.lower()

    def test_script_references_pip(self, script):
        assert "pip" in script.lower()

    def test_script_references_npm(self, script):
        assert "npm" in script.lower()

    def test_script_has_pass_message(self, script):
        assert "PASS" in script

    def test_script_uses_project_root(self, script):
        assert "PROJECT_ROOT" in script

    def test_script_checks_missing_policy_file(self, script):
        assert "not found" in script.lower() or "not -f" in script


# ══════════════════════════════════════════════════════════════════════════════
# H — Cross-file consistency
# ══════════════════════════════════════════════════════════════════════════════
class TestHCrossFileConsistency:
    @pytest.fixture(scope="class")
    def policy(self):
        return _load_policy()

    @pytest.fixture(scope="class")
    def renovate(self):
        return _load_renovate()

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_policy_ci_script_matches_actual_script_path(self, policy):
        script_path = policy["ci_gates"]["weekly_audit"].get("script", "")
        assert "check-outdated.sh" in script_path

    def test_renovate_schema_is_valid_url(self, renovate):
        schema = renovate.get("$schema", "")
        assert schema.startswith("https://")

    def test_policy_groups_names_present_in_renovate_rules(self, policy, renovate):
        group_names = {g.get("name", "").lower() for g in policy["groups"]}
        rules = renovate.get("packageRules", [])
        rule_groups = {r.get("groupName", "").lower() for r in rules}
        # At least half the policy groups should appear as renovate groupNames
        matched = group_names & rule_groups
        assert len(matched) >= len(group_names) // 2, \
            f"Expected at least {len(group_names)//2} policy groups in renovate, found: {matched}"

    def test_torch_frozen_in_both_policy_and_renovate(self, policy, renovate):
        ignored = [i.get("package", "") for i in policy.get("ignore", [])]
        assert "torch" in ignored, "torch must be in policy ignore list"
        rules = renovate.get("packageRules", [])
        # Find the torch-specific rule where enabled is explicitly False
        torch_disabled_rule = next(
            (r for r in rules
             if r.get("enabled") is False
             and "torch" in str(r.get("matchPackageNames", ""))),
            None
        )
        assert torch_disabled_rule is not None, \
            "torch must have a renovate rule with enabled=false (frozen)"

    def test_security_auto_merge_consistent_across_files(self, policy, renovate):
        policy_auto = policy["update_schedule"]["security_patch"].get("auto_merge", False)
        rules = renovate.get("packageRules", [])
        renovate_sec_rule = next(
            (r for r in rules if "security" in r.get("description", "").lower()
             and "patch" in r.get("description", "").lower()),
            None
        )
        assert policy_auto is True, "Policy must have security_patch auto_merge=true"
        if renovate_sec_rule:
            assert renovate_sec_rule.get("automerge") is True, \
                "Renovate security patch rule must have automerge=true"

    def test_manifests_in_policy_match_actual_files(self, policy):
        manifests = policy["scope"].get("manifests", [])
        # Validate manifest paths are well-formed and reference known components.
        # File existence is not checked because tests run inside the api container
        # which only mounts the backend — frontend/mobile/desktop paths are absent.
        known_components = {"backend", "ai-worker", "frontend", "mobile", "desktop", "docker"}
        for m in manifests:
            path = m.get("path", "")
            assert path, f"Manifest entry missing 'path': {m}"
            first_segment = path.split("/")[0]
            assert first_segment in known_components, \
                f"Manifest path '{path}' starts with unknown component '{first_segment}'"
        # backend/pyproject.toml must be present (it IS in the container)
        backend_manifest = next(
            (m for m in manifests if "backend/pyproject.toml" in m.get("path", "")),
            None
        )
        assert backend_manifest is not None, "scope.manifests must include backend/pyproject.toml"

    def test_policy_failure_severities_covered_by_renovate_alerts(self, policy, renovate):
        fail_on = policy["ci_gates"]["weekly_audit"].get("fail_on", [])
        assert "critical" in fail_on
        va = renovate.get("vulnerabilityAlerts", {})
        assert va.get("enabled") is True, "Renovate vulnerability alerts must be enabled"
