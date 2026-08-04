"""Gap 23 — Load & Performance Testing: infrastructure-file-validation tests.

Validates:
  A. File structure (policy file, Locust file, run script)
  B. Policy validity (YAML, required top-level sections)
  C. Policy scenarios (smoke/load/stress present, required fields, CI gate on smoke)
  D. Policy thresholds (P95 latency keys, error rate, smoke zero-error tolerance)
  E. Locust file structure (Python, HttpUser subclass, tasks, on_start)
  F. Locust file content (key endpoints covered, auth handling, threshold hook)
  G. Run script structure (shebang, modes, error handling, prerequisite checks)
  H. Integration discipline (SLO alignment, both services referenced, credentials via env)
"""

from __future__ import annotations

import ast
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
_POLICY_PATH  = _PROJECT_ROOT / "config" / "load-test-policy.yml"
_LOCUST_PATH  = _PROJECT_ROOT / "locustfiles" / "load_test_api.py"
_SCRIPT_PATH  = _PROJECT_ROOT / "scripts" / "load-test" / "run-load-tests.sh"

REQUIRED_SCENARIO_NAMES = {"smoke", "load", "stress"}
REQUIRED_ENDPOINT_NAMES = {"/health", "/api/v1/alerts", "/api/v1/cameras", "/api/v1/incidents"}
SLO_P95_LATENCY_MS = 500   # Must match observability/slo/slo-definitions.yml api_p95_latency


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _locust_text() -> str:
    return _LOCUST_PATH.read_text(encoding="utf-8")


def _script_text() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_load_test_policy_exists(self):
        assert _POLICY_PATH.exists(), f"Missing: {_POLICY_PATH}"

    def test_locustfile_exists(self):
        assert _LOCUST_PATH.exists(), f"Missing: {_LOCUST_PATH}"

    def test_run_script_exists(self):
        assert _SCRIPT_PATH.exists(), f"Missing: {_SCRIPT_PATH}"

    def test_locustfiles_directory_exists(self):
        assert (_PROJECT_ROOT / "locustfiles").is_dir(), (
            "locustfiles/ directory must exist at project root"
        )


# ─────────────────────────────────────────────────────────────
# B — Policy validity
# ─────────────────────────────────────────────────────────────

class TestBPolicyValidity:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_policy_valid_yaml(self, policy):
        assert isinstance(policy, dict), "load-test-policy.yml must be a YAML mapping"

    def test_policy_has_version(self, policy):
        assert "version" in policy, "policy missing 'version'"

    def test_policy_has_metadata(self, policy):
        assert "metadata" in policy, "policy missing 'metadata'"

    def test_policy_has_scenarios(self, policy):
        scenarios = policy.get("scenarios")
        assert isinstance(scenarios, list) and len(scenarios) > 0, (
            "policy must have a non-empty 'scenarios' list"
        )

    def test_policy_has_thresholds(self, policy):
        assert "thresholds" in policy, "policy missing 'thresholds' section"

    def test_policy_has_target(self, policy):
        assert "target" in policy, "policy missing 'target' section"

    def test_policy_metadata_references_locustfile(self, policy):
        meta = policy.get("metadata", {})
        assert "locustfile" in meta, (
            "policy metadata must reference the locustfile path"
        )


# ─────────────────────────────────────────────────────────────
# C — Policy scenarios
# ─────────────────────────────────────────────────────────────

class TestCPolicyScenarios:

    @pytest.fixture(scope="class")
    def scenario_map(self):
        policy = _load_yaml(_POLICY_PATH)
        return {s["name"]: s for s in policy.get("scenarios", []) if "name" in s}

    def test_required_scenarios_present(self, scenario_map):
        missing = REQUIRED_SCENARIO_NAMES - set(scenario_map)
        assert not missing, f"Missing required scenarios: {missing}"

    def test_each_scenario_has_users(self, scenario_map):
        for name, s in scenario_map.items():
            assert "users" in s, f"Scenario '{name}' missing 'users'"

    def test_each_scenario_has_spawn_rate(self, scenario_map):
        for name, s in scenario_map.items():
            assert "spawn_rate" in s, f"Scenario '{name}' missing 'spawn_rate'"

    def test_each_scenario_has_duration(self, scenario_map):
        for name, s in scenario_map.items():
            assert "duration" in s, f"Scenario '{name}' missing 'duration'"

    def test_smoke_has_ci_gate_true(self, scenario_map):
        smoke = scenario_map.get("smoke", {})
        assert smoke.get("ci_gate") is True, (
            "smoke scenario must have ci_gate: true — it is the pre-deployment CI gate"
        )

    def test_smoke_users_is_small(self, scenario_map):
        smoke_users = scenario_map.get("smoke", {}).get("users", 9999)
        assert smoke_users <= 10, (
            f"smoke scenario should have <= 10 users for fast CI runs; got {smoke_users}"
        )

    def test_stress_has_most_users(self, scenario_map):
        stress_users = scenario_map.get("stress", {}).get("users", 0)
        load_users   = scenario_map.get("load", {}).get("users", 0)
        smoke_users  = scenario_map.get("smoke", {}).get("users", 0)
        assert stress_users > load_users > smoke_users, (
            "stress must have more users than load, which must have more than smoke"
        )


# ─────────────────────────────────────────────────────────────
# D — Policy thresholds
# ─────────────────────────────────────────────────────────────

class TestDPolicyThresholds:

    @pytest.fixture(scope="class")
    def thresholds(self):
        return _load_yaml(_POLICY_PATH).get("thresholds", {})

    def test_thresholds_has_p95_latency(self, thresholds):
        assert "p95_latency_ms" in thresholds, (
            "thresholds must define p95_latency_ms per endpoint"
        )

    def test_thresholds_has_error_rate(self, thresholds):
        assert "error_rate_percent" in thresholds, (
            "thresholds must define error_rate_percent"
        )

    def test_error_rate_is_tight(self, thresholds):
        rate = thresholds.get("error_rate_percent", 100)
        assert rate <= 1.0, (
            f"error_rate_percent {rate}% is too loose — must be ≤ 1.0%"
        )

    def test_alerts_endpoint_threshold_matches_slo(self, thresholds):
        p95 = thresholds.get("p95_latency_ms", {})
        alerts_threshold = p95.get("GET /api/v1/alerts")
        assert alerts_threshold is not None, (
            "p95_latency_ms must include 'GET /api/v1/alerts'"
        )
        assert alerts_threshold <= SLO_P95_LATENCY_MS, (
            f"GET /api/v1/alerts P95 threshold {alerts_threshold}ms must be "
            f"<= SLO threshold {SLO_P95_LATENCY_MS}ms"
        )

    def test_health_endpoint_has_tightest_threshold(self, thresholds):
        p95 = thresholds.get("p95_latency_ms", {})
        health_ms = p95.get("GET /health")
        assert health_ms is not None, "p95_latency_ms must include 'GET /health'"
        assert health_ms <= 200, (
            f"GET /health P95 should be <= 200ms; got {health_ms}ms"
        )

    def test_smoke_zero_error_documented(self, thresholds):
        assert "smoke_error_rate_percent" in thresholds, (
            "thresholds must document smoke_error_rate_percent = 0.0 (pre-deploy gate)"
        )
        assert thresholds["smoke_error_rate_percent"] == 0.0, (
            "smoke scenario must require exactly 0.0% error rate"
        )


# ─────────────────────────────────────────────────────────────
# E — Locust file structure
# ─────────────────────────────────────────────────────────────

class TestELocustFileStructure:

    @pytest.fixture(scope="class")
    def locust_text(self):
        return _locust_text()

    def test_locust_file_is_valid_python(self, locust_text):
        try:
            ast.parse(locust_text)
        except SyntaxError as e:
            pytest.fail(f"load_test_api.py has a Python syntax error: {e}")

    def test_locust_imports_httpuser(self, locust_text):
        assert "HttpUser" in locust_text, (
            "Locust file must import and use HttpUser"
        )

    def test_locust_imports_task(self, locust_text):
        assert "task" in locust_text, (
            "Locust file must use @task decorator"
        )

    def test_locust_has_httpuser_subclass(self, locust_text):
        assert "class" in locust_text and "HttpUser" in locust_text, (
            "Locust file must define an HttpUser subclass"
        )

    def test_locust_has_on_start(self, locust_text):
        assert "on_start" in locust_text, (
            "HttpUser subclass must implement on_start() for auth"
        )

    def test_locust_has_wait_time(self, locust_text):
        assert "wait_time" in locust_text, (
            "HttpUser subclass must define wait_time"
        )


# ─────────────────────────────────────────────────────────────
# F — Locust file content
# ─────────────────────────────────────────────────────────────

class TestFLocustFileContent:

    @pytest.fixture(scope="class")
    def locust_text(self):
        return _locust_text()

    def test_health_endpoint_covered(self, locust_text):
        assert "/health" in locust_text, (
            "Locust file must test GET /health"
        )

    def test_alerts_endpoint_covered(self, locust_text):
        assert "/api/v1/alerts" in locust_text, (
            "Locust file must test GET /api/v1/alerts"
        )

    def test_cameras_endpoint_covered(self, locust_text):
        assert "/api/v1/cameras" in locust_text, (
            "Locust file must test GET /api/v1/cameras"
        )

    def test_incidents_endpoint_covered(self, locust_text):
        assert "/api/v1/incidents" in locust_text, (
            "Locust file must test GET /api/v1/incidents"
        )

    def test_auth_login_endpoint_covered(self, locust_text):
        assert "/api/v1/auth/login" in locust_text, (
            "Locust file must include auth login task"
        )

    def test_credentials_from_environment_variables(self, locust_text):
        assert "os.environ" in locust_text or "os.getenv" in locust_text, (
            "Locust file must read credentials from environment variables, not hardcode them"
        )

    def test_quitting_event_hook_present(self, locust_text):
        assert "quitting" in locust_text, (
            "Locust file must have a quitting event hook for threshold enforcement"
        )

    def test_catch_response_used(self, locust_text):
        assert "catch_response" in locust_text, (
            "Locust tasks must use catch_response=True for proper failure tracking"
        )


# ─────────────────────────────────────────────────────────────
# G — Run script structure
# ─────────────────────────────────────────────────────────────

class TestGRunScriptStructure:

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_has_shebang(self, script):
        first = script.splitlines()[0]
        assert first.startswith("#!/"), "run script must start with a shebang"
        assert "bash" in first or "sh" in first

    def test_script_has_error_handling(self, script):
        assert "set -e" in script, "run script must use 'set -e' or 'set -euo pipefail'"

    def test_script_has_smoke_mode(self, script):
        assert "--smoke" in script, "run script must implement --smoke mode"

    def test_script_has_load_mode(self, script):
        assert "--load" in script, "run script must implement --load mode"

    def test_script_has_stress_mode(self, script):
        assert "--stress" in script, "run script must implement --stress mode"

    def test_script_checks_prerequisites(self, script):
        assert "_require" in script or "command -v" in script

    def test_script_runs_headless(self, script):
        assert "--headless" in script, (
            "run script must invoke locust with --headless for CI compatibility"
        )

    def test_script_generates_csv_report(self, script):
        assert "--csv" in script, "run script must generate CSV reports"


# ─────────────────────────────────────────────────────────────
# H — Integration discipline
# ─────────────────────────────────────────────────────────────

class TestHIntegrationDiscipline:

    def test_locustfile_path_in_policy_metadata_exists(self):
        policy = _load_yaml(_POLICY_PATH)
        locustfile_rel = policy.get("metadata", {}).get("locustfile", "")
        locust_path = _PROJECT_ROOT / locustfile_rel
        assert locust_path.exists(), (
            f"metadata.locustfile '{locustfile_rel}' does not exist at {locust_path}"
        )

    def test_policy_target_has_base_url(self):
        policy = _load_yaml(_POLICY_PATH)
        base_url = policy.get("target", {}).get("base_url", "")
        assert base_url.startswith("http"), (
            "target.base_url must be a valid URL"
        )

    def test_policy_target_has_health_endpoint(self):
        policy = _load_yaml(_POLICY_PATH)
        assert "health_endpoint" in policy.get("target", {}), (
            "target must define health_endpoint"
        )

    def test_credentials_section_uses_env_vars(self):
        policy = _load_yaml(_POLICY_PATH)
        creds = policy.get("credentials", {})
        assert "env_var_email" in creds, (
            "credentials section must document env_var_email"
        )
        assert "env_var_password" in creds, (
            "credentials section must document env_var_password"
        )

    def test_script_reads_scenario_from_policy_file(self):
        script = _script_text()
        assert "POLICY_FILE" in script or "load-test-policy" in script, (
            "run script must read scenario config from the policy file"
        )

    def test_script_uses_locust_host_env_var(self):
        script = _script_text()
        assert "LOCUST_HOST" in script, (
            "run script must use LOCUST_HOST env var for the target URL"
        )

    def test_smoke_is_flagged_as_ci_gate_in_policy(self):
        policy = _load_yaml(_POLICY_PATH)
        smoke = next(
            (s for s in policy.get("scenarios", []) if s.get("name") == "smoke"),
            {}
        )
        assert smoke.get("ci_gate") is True, (
            "smoke scenario must be marked ci_gate: true in the policy"
        )
