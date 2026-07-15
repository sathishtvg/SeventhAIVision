"""
Gap 30 — Container Hardening Policy
Validates:
  config/container-hardening-policy.yml
  docker/security/seccomp-profile.json
  scripts/security/check-container-hardening.sh
  docker/backend.Dockerfile
  docker/ai-worker.Dockerfile

Pattern: infrastructure-file-validation — reads project files, no runtime deps.
"""
import json
import re
from pathlib import Path

import pytest
import yaml

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path("/app")

POLICY_FILE  = ROOT / "config" / "container-hardening-policy.yml"
SECCOMP_FILE = ROOT / "docker" / "security" / "seccomp-profile.json"
SCRIPT_FILE  = ROOT / "scripts" / "security" / "check-container-hardening.sh"
BACKEND_DF   = ROOT / "docker" / "backend.Dockerfile"
AIWORKER_DF  = ROOT / "docker" / "ai-worker.Dockerfile"

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def policy():
    with open(POLICY_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def seccomp():
    with open(SECCOMP_FILE, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def script_text():
    return SCRIPT_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def backend_df_text():
    return BACKEND_DF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def aiworker_df_text():
    return AIWORKER_DF.read_text(encoding="utf-8")


# ── A: File structure ─────────────────────────────────────────────────────────

class TestAFileStructure:
    def test_policy_file_exists(self):
        assert POLICY_FILE.exists(), f"Missing: {POLICY_FILE}"

    def test_seccomp_file_exists(self):
        assert SECCOMP_FILE.exists(), f"Missing: {SECCOMP_FILE}"

    def test_script_file_exists(self):
        assert SCRIPT_FILE.exists(), f"Missing: {SCRIPT_FILE}"

    def test_backend_dockerfile_exists(self):
        assert BACKEND_DF.exists(), f"Missing: {BACKEND_DF}"

    def test_aiworker_dockerfile_exists(self):
        assert AIWORKER_DF.exists(), f"Missing: {AIWORKER_DF}"

    def test_policy_file_not_empty(self):
        assert POLICY_FILE.stat().st_size > 100

    def test_seccomp_file_not_empty(self):
        assert SECCOMP_FILE.stat().st_size > 500


# ── B: Policy YAML validity ───────────────────────────────────────────────────

class TestBPolicyYamlValidity:
    def test_policy_is_valid_yaml(self, policy):
        assert isinstance(policy, dict)

    def test_policy_version_field(self, policy):
        assert "version" in policy
        assert policy["version"] == "1.0"

    def test_policy_metadata_section(self, policy):
        assert "metadata" in policy

    def test_policy_metadata_has_owner(self, policy):
        assert "owner" in policy["metadata"]

    def test_policy_scope_section(self, policy):
        assert "scope" in policy

    def test_policy_build_controls_section(self, policy):
        assert "build_controls" in policy

    def test_policy_runtime_controls_section(self, policy):
        assert "runtime_controls" in policy

    def test_policy_scanning_section(self, policy):
        assert "scanning" in policy

    def test_policy_ci_integration_section(self, policy):
        assert "ci_integration" in policy

    def test_policy_severity_actions_section(self, policy):
        assert "severity_actions" in policy


# ── C: Policy build controls content ─────────────────────────────────────────

class TestCPolicyBuildControls:
    def test_non_root_user_control_present(self, policy):
        assert "non_root_user" in policy["build_controls"]

    def test_non_root_user_severity_is_critical(self, policy):
        ctrl = policy["build_controls"]["non_root_user"]
        assert ctrl.get("severity") == "critical"

    def test_non_root_user_required_instruction_is_user(self, policy):
        ctrl = policy["build_controls"]["non_root_user"]
        assert ctrl.get("required_instruction") == "USER"

    def test_non_root_user_exceptions_list_present(self, policy):
        ctrl = policy["build_controls"]["non_root_user"]
        assert "exceptions" in ctrl
        assert isinstance(ctrl["exceptions"], list)

    def test_postgres_dockerfile_excepted_from_user_check(self, policy):
        exceptions = policy["build_controls"]["non_root_user"]["exceptions"]
        assert any("postgres" in exc.lower() for exc in exceptions)

    def test_no_privileged_commands_control_present(self, policy):
        assert "no_privileged_commands" in policy["build_controls"]

    def test_sudo_in_prohibited_patterns(self, policy):
        patterns = policy["build_controls"]["no_privileged_commands"]["prohibited_patterns"]
        assert any("sudo" in p for p in patterns)

    def test_su_in_prohibited_patterns(self, policy):
        patterns = policy["build_controls"]["no_privileged_commands"]["prohibited_patterns"]
        joined = " ".join(patterns)
        assert "su " in joined or "su\n" in joined

    def test_no_secrets_in_build_control_present(self, policy):
        assert "no_secrets_in_build" in policy["build_controls"]

    def test_password_in_prohibited_env_keys(self, policy):
        keys = policy["build_controls"]["no_secrets_in_build"]["prohibited_env_keys"]
        assert "PASSWORD" in keys

    def test_token_in_prohibited_env_keys(self, policy):
        keys = policy["build_controls"]["no_secrets_in_build"]["prohibited_env_keys"]
        assert "TOKEN" in keys

    def test_base_image_control_present(self, policy):
        assert "base_image" in policy["build_controls"]

    def test_latest_tag_in_prohibited_patterns(self, policy):
        patterns = policy["build_controls"]["base_image"].get("prohibited_patterns", [])
        assert any(":latest" in p for p in patterns)

    def test_healthcheck_required_applies_to_backend(self, policy):
        ctrl = policy["build_controls"].get("healthcheck_required", {})
        applies_to = ctrl.get("applies_to", [])
        assert any("backend" in path for path in applies_to)


# ── D: Policy runtime controls content ───────────────────────────────────────

class TestDPolicyRuntimeControls:
    def test_no_privileged_mode_present(self, policy):
        assert "no_privileged_mode" in policy["runtime_controls"]

    def test_no_privileged_mode_severity_is_critical(self, policy):
        ctrl = policy["runtime_controls"]["no_privileged_mode"]
        assert ctrl.get("severity") == "critical"

    def test_drop_all_capabilities_present(self, policy):
        assert "drop_all_capabilities" in policy["runtime_controls"]

    def test_drop_all_capabilities_recommends_all(self, policy):
        ctrl = policy["runtime_controls"]["drop_all_capabilities"]
        cap_drop = ctrl.get("recommended_cap_drop", [])
        assert "ALL" in cap_drop

    def test_resource_limits_present(self, policy):
        assert "resource_limits" in policy["runtime_controls"]

    def test_resource_limits_has_api_entry(self, policy):
        limits = policy["runtime_controls"]["resource_limits"].get("recommended_limits", {})
        assert "api" in limits

    def test_no_host_network_present(self, policy):
        assert "no_host_network" in policy["runtime_controls"]

    def test_no_host_network_severity_is_high(self, policy):
        ctrl = policy["runtime_controls"]["no_host_network"]
        assert ctrl.get("severity") == "high"

    def test_seccomp_profile_control_present(self, policy):
        assert "seccomp_profile" in policy["runtime_controls"]

    def test_seccomp_profile_path_defined(self, policy):
        ctrl = policy["runtime_controls"]["seccomp_profile"]
        assert "profile_path" in ctrl
        assert "seccomp-profile.json" in ctrl["profile_path"]


# ── E: Seccomp profile structure ──────────────────────────────────────────────

class TestESeccompProfile:
    def test_default_action_is_scmp_act_errno(self, seccomp):
        assert seccomp.get("defaultAction") == "SCMP_ACT_ERRNO"

    def test_syscalls_key_present(self, seccomp):
        assert "syscalls" in seccomp

    def test_syscalls_is_non_empty_list(self, seccomp):
        assert isinstance(seccomp["syscalls"], list)
        assert len(seccomp["syscalls"]) > 0

    def test_arch_map_present(self, seccomp):
        assert "archMap" in seccomp

    def test_x86_64_architecture_present(self, seccomp):
        arches = [a.get("architecture") for a in seccomp.get("archMap", [])]
        assert "SCMP_ARCH_X86_64" in arches

    def test_aarch64_architecture_present(self, seccomp):
        arches = [a.get("architecture") for a in seccomp.get("archMap", [])]
        assert "SCMP_ARCH_AARCH64" in arches

    def test_first_syscall_group_action_is_allow(self, seccomp):
        first = seccomp["syscalls"][0]
        assert first.get("action") == "SCMP_ACT_ALLOW"

    def test_allowed_syscalls_include_read(self, seccomp):
        all_names = [n for g in seccomp["syscalls"] for n in g.get("names", [])]
        assert "read" in all_names

    def test_allowed_syscalls_include_write(self, seccomp):
        all_names = [n for g in seccomp["syscalls"] for n in g.get("names", [])]
        assert "write" in all_names

    def test_allowed_syscalls_include_socket(self, seccomp):
        all_names = [n for g in seccomp["syscalls"] for n in g.get("names", [])]
        assert "socket" in all_names

    def test_blocked_syscalls_section_present(self, seccomp):
        assert "blocked_syscalls" in seccomp

    def test_ptrace_in_blocked_examples(self, seccomp):
        examples = seccomp.get("blocked_syscalls", {}).get("examples", [])
        assert "ptrace" in examples

    def test_kexec_load_in_blocked_examples(self, seccomp):
        examples = seccomp.get("blocked_syscalls", {}).get("examples", [])
        assert "kexec_load" in examples

    def test_mount_in_blocked_examples(self, seccomp):
        examples = seccomp.get("blocked_syscalls", {}).get("examples", [])
        assert "mount" in examples

    def test_reboot_in_blocked_examples(self, seccomp):
        examples = seccomp.get("blocked_syscalls", {}).get("examples", [])
        assert "reboot" in examples

    def test_personality_syscall_has_args_restriction(self, seccomp):
        personality_groups = [g for g in seccomp["syscalls"] if "personality" in g.get("names", [])]
        assert len(personality_groups) > 0, "personality syscall not in seccomp allowlist"
        assert "args" in personality_groups[0], "personality must be restricted via args, not open ALLOW"

    def test_clone_syscall_has_args_restriction(self, seccomp):
        clone_groups = [g for g in seccomp["syscalls"] if "clone" in g.get("names", [])]
        assert len(clone_groups) > 0, "clone syscall not in seccomp allowlist"
        assert "args" in clone_groups[0], "clone must be restricted to thread-creation only via args"

    def test_more_than_50_allowed_syscalls(self, seccomp):
        all_names = [n for g in seccomp["syscalls"] for n in g.get("names", [])]
        assert len(all_names) >= 50, "Allowlist seems too small — may block legitimate app syscalls"


# ── F: Script structure ───────────────────────────────────────────────────────

class TestFScriptStructure:
    def test_script_has_bash_shebang(self, script_text):
        assert script_text.startswith("#!/usr/bin/env bash")

    def test_script_has_errexit(self, script_text):
        assert "set -euo pipefail" in script_text

    def test_script_has_check_mode(self, script_text):
        assert "--check)" in script_text or "--check" in script_text

    def test_script_has_dockerfile_mode(self, script_text):
        assert "--dockerfile" in script_text

    def test_script_has_compose_mode(self, script_text):
        assert "--compose" in script_text

    def test_script_has_report_flag(self, script_text):
        assert "--report" in script_text

    def test_script_has_dry_run_flag(self, script_text):
        assert "--dry-run" in script_text

    def test_script_checks_policy_file_existence(self, script_text):
        assert "POLICY_FILE" in script_text
        # Either bash -f check or Python
        assert ("! -f" in script_text) or ("not found" in script_text)

    def test_script_has_exit_codes_documented(self, script_text):
        # Script uses Python heredoc — exit codes via sys.exit() or bash exit
        assert "sys.exit(0)" in script_text or "exit 0" in script_text
        assert "sys.exit(1)" in script_text or "exit 1" in script_text


# ── G: Script content ─────────────────────────────────────────────────────────

class TestGScriptContent:
    def test_script_defines_policy_file_variable(self, script_text):
        assert "POLICY_FILE" in script_text

    def test_script_uses_python_for_validation(self, script_text):
        assert "python3" in script_text

    def test_script_loads_yaml_policy(self, script_text):
        assert "yaml.safe_load" in script_text

    def test_script_checks_for_user_instruction(self, script_text):
        # Script must enforce non_root_user policy
        assert "USER" in script_text
        assert "non_root_user" in script_text

    def test_script_checks_prohibited_commands(self, script_text):
        assert "no_privileged_commands" in script_text

    def test_script_checks_env_secrets(self, script_text):
        assert "no_secrets_in_build" in script_text

    def test_script_checks_compose_privileged_mode(self, script_text):
        assert "privileged" in script_text

    def test_script_checks_host_network(self, script_text):
        assert "network_mode" in script_text
        assert "host" in script_text

    def test_script_validates_seccomp_json(self, script_text):
        assert "seccomp" in script_text.lower()
        assert "defaultAction" in script_text

    def test_script_distinguishes_errors_from_warnings(self, script_text):
        # Script must separate blocking (errors) from non-blocking (warnings)
        assert "errors" in script_text
        assert "warnings" in script_text


# ── H: Dockerfile compliance ──────────────────────────────────────────────────

class TestHDockerfileCompliance:
    def _user_lines(self, text):
        return [l for l in text.splitlines() if re.match(r"^\s*USER\s+", l)]

    def _from_lines(self, text):
        return [l for l in text.splitlines() if l.strip().startswith("FROM")]

    def test_backend_dockerfile_has_user_instruction(self, backend_df_text):
        user_lines = self._user_lines(backend_df_text)
        assert len(user_lines) > 0, "backend.Dockerfile must have a USER instruction (CIS 4.1)"

    def test_backend_dockerfile_user_is_not_root(self, backend_df_text):
        user_lines = self._user_lines(backend_df_text)
        for line in user_lines:
            assert "root" not in line.lower(), f"backend.Dockerfile USER must not be root: {line}"

    def test_backend_dockerfile_no_latest_base(self, backend_df_text):
        for line in self._from_lines(backend_df_text):
            assert ":latest" not in line.lower(), f"backend.Dockerfile uses :latest: {line}"

    def test_backend_dockerfile_no_sudo(self, backend_df_text):
        assert "sudo " not in backend_df_text, "backend.Dockerfile must not use sudo"

    def test_backend_dockerfile_no_hardcoded_env_secrets(self, backend_df_text):
        for line in backend_df_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("ENV "):
                for key in ("PASSWORD", "SECRET", "API_KEY", "TOKEN", "PRIVATE_KEY"):
                    m = re.search(rf"\b{key}\b\s*=\s*[^\${{]", stripped, re.IGNORECASE)
                    assert not m, f"backend.Dockerfile: hardcoded secret in ENV: {stripped}"

    def test_backend_dockerfile_uses_slim_base(self, backend_df_text):
        from_lines = self._from_lines(backend_df_text)
        assert any("slim" in l.lower() or "alpine" in l.lower() for l in from_lines), \
            "backend.Dockerfile should use a slim or alpine base image"

    def test_aiworker_dockerfile_has_user_instruction(self, aiworker_df_text):
        user_lines = self._user_lines(aiworker_df_text)
        assert len(user_lines) > 0, "ai-worker.Dockerfile must have a USER instruction (CIS 4.1)"

    def test_aiworker_dockerfile_user_is_not_root(self, aiworker_df_text):
        user_lines = self._user_lines(aiworker_df_text)
        for line in user_lines:
            assert "root" not in line.lower(), f"ai-worker.Dockerfile USER must not be root: {line}"

    def test_aiworker_dockerfile_no_latest_base(self, aiworker_df_text):
        for line in self._from_lines(aiworker_df_text):
            assert ":latest" not in line.lower(), f"ai-worker.Dockerfile uses :latest: {line}"

    def test_aiworker_dockerfile_no_sudo(self, aiworker_df_text):
        assert "sudo " not in aiworker_df_text, "ai-worker.Dockerfile must not use sudo"

    def test_aiworker_dockerfile_no_hardcoded_env_secrets(self, aiworker_df_text):
        for line in aiworker_df_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("ENV "):
                for key in ("PASSWORD", "SECRET", "API_KEY", "TOKEN", "PRIVATE_KEY"):
                    m = re.search(rf"\b{key}\b\s*=\s*[^\${{]", stripped, re.IGNORECASE)
                    assert not m, f"ai-worker.Dockerfile: hardcoded secret in ENV: {stripped}"

    def test_aiworker_dockerfile_uses_slim_base(self, aiworker_df_text):
        from_lines = self._from_lines(aiworker_df_text)
        assert any("slim" in l.lower() or "alpine" in l.lower() for l in from_lines), \
            "ai-worker.Dockerfile should use a slim or alpine base image"


# ── I: Cross-file consistency ─────────────────────────────────────────────────

class TestICrossFileConsistency:
    def test_seccomp_path_in_policy_matches_actual_file(self, policy):
        profile_path = policy["runtime_controls"]["seccomp_profile"]["profile_path"]
        actual = ROOT / profile_path
        assert actual.exists(), \
            f"seccomp_profile.profile_path in policy ({profile_path}) does not exist at {actual}"

    def test_ci_script_in_policy_points_to_existing_file(self, policy):
        check_script = policy["ci_integration"]["check_script"]
        script_part = check_script.split()[0]  # strip args like "--check"
        actual = ROOT / script_part
        assert actual.exists(), \
            f"ci_integration.check_script ({script_part}) does not exist"

    def test_policy_scope_dockerfiles_includes_backend(self, policy):
        dockerfiles = policy["scope"]["dockerfiles"]
        assert any("backend" in df.lower() for df in dockerfiles)

    def test_policy_scope_dockerfiles_includes_aiworker(self, policy):
        dockerfiles = policy["scope"]["dockerfiles"]
        assert any("ai-worker" in df.lower() for df in dockerfiles)

    def test_policy_scope_dockerfiles_includes_postgres(self, policy):
        dockerfiles = policy["scope"]["dockerfiles"]
        assert any("postgres" in df.lower() for df in dockerfiles)

    def test_policy_severity_actions_all_four_levels(self, policy):
        sa = policy["severity_actions"]
        for level in ("critical", "high", "medium", "low"):
            assert level in sa, f"severity_actions missing '{level}'"

    def test_script_references_seccomp_filename(self, policy, script_text):
        # Script and policy should agree on the seccomp profile filename
        profile_path = policy["runtime_controls"]["seccomp_profile"]["profile_path"]
        filename = Path(profile_path).name  # seccomp-profile.json
        assert filename in script_text, f"Script does not reference seccomp filename '{filename}'"

    def test_policy_scanning_tool_is_trivy(self, policy):
        assert policy["scanning"]["tool"] == "trivy"
