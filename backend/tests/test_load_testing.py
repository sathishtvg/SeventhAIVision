"""Gap 13 — Load testing infrastructure tests.

Validates the Locust-based load test suite WITHOUT running Locust or requiring it
to be installed.  Tests that actually invoke the locust CLI are skipped when it is
not present (matching the pattern of test_k8s_helm.py for the Helm CLI).

Coverage:
  Section A: File structure (locustfile.py, locust.conf)
  Section B: Python syntax and module structure
  Section C: SLA threshold sanity
  Section D: StepLoadShape stage validation
  Section E: locust.conf key validation
  Section F: Locust import + class hierarchy (skipped when locust not installed)
  Section G: Task coverage (endpoint-mention checks)
"""

from __future__ import annotations

import ast
import configparser
import importlib
import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

from tests._repo import REPO_ROOT, requires_repo_tree

# Skips the whole module when the repository tree is absent (e.g. inside the
# api image, which ships only backend/). See tests/_repo.py for why failing
# would be the wrong signal here.
pytestmark = requires_repo_tree


# ── Paths ─────────────────────────────────────────────────────────────────────
# /app/backend/tests/ → /app/ → scripts/load_tests/
_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]
LOAD_TESTS_DIR = Path(os.environ.get("LOAD_TESTS_DIR", str(_PROJECT_ROOT / "scripts" / "load_tests")))
LOCUSTFILE = LOAD_TESTS_DIR / "locustfile.py"
LOCUST_CONF = LOAD_TESTS_DIR / "locust.conf"

LOCUST_CLI_AVAILABLE = shutil.which("locust") is not None
LOCUST_PKG_AVAILABLE = importlib.util.find_spec("locust") is not None


# ── Section A: File structure ─────────────────────────────────────────────────

def test_load_tests_directory_exists():
    assert LOAD_TESTS_DIR.is_dir(), f"load_tests directory not found: {LOAD_TESTS_DIR}"


def test_locustfile_exists():
    assert LOCUSTFILE.is_file(), f"locustfile.py not found at {LOCUSTFILE}"


def test_locust_conf_exists():
    assert LOCUST_CONF.is_file(), f"locust.conf not found at {LOCUST_CONF}"


# ── Section B: Python syntax and module structure ─────────────────────────────

def _locustfile_source() -> str:
    return LOCUSTFILE.read_text(encoding="utf-8")


def test_locustfile_valid_python_syntax():
    """locustfile.py must parse without syntax errors."""
    source = _locustfile_source()
    try:
        ast.parse(source)
    except SyntaxError as exc:
        pytest.fail(f"locustfile.py has a syntax error: {exc}")


def test_seventh_ai_user_class_defined():
    source = _locustfile_source()
    assert "class SeventhAIUser" in source, "SeventhAIUser class not found in locustfile.py"


def test_read_tasks_class_defined():
    source = _locustfile_source()
    assert "class ReadTasks" in source, "ReadTasks TaskSet not found in locustfile.py"


def test_write_tasks_class_defined():
    source = _locustfile_source()
    assert "class WriteTasks" in source, "WriteTasks TaskSet not found in locustfile.py"


def test_step_load_shape_class_defined():
    source = _locustfile_source()
    assert "class StepLoadShape" in source, "StepLoadShape class not found in locustfile.py"


def test_sla_dict_defined():
    source = _locustfile_source()
    assert "SLA" in source and "p95_ms" in source, "SLA dict with p95_ms not found in locustfile.py"


def test_wait_time_defined():
    source = _locustfile_source()
    assert "wait_time" in source and "between" in source, \
        "wait_time = between(...) not found in locustfile.py"


# ── Section C: SLA threshold sanity ──────────────────────────────────────────
# Import the module for value checks — works even without locust because
# locustfile.py uses conditional imports.

def _import_locustfile():
    """Import scripts/load_tests/locustfile.py as a module without needing locust."""
    spec = importlib.util.spec_from_file_location("locustfile_under_test", LOCUSTFILE)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_sla_p95_is_defined():
    mod = _import_locustfile()
    assert "p95_ms" in mod.SLA, "SLA['p95_ms'] not defined"


def test_sla_p95_is_reasonable():
    """P95 must be > 0 and <= 2000 ms — prevents overly permissive targets."""
    mod = _import_locustfile()
    p95 = mod.SLA["p95_ms"]
    assert 0 < p95 <= 2000, f"SLA p95_ms={p95} is outside sensible range (0, 2000]"


def test_sla_p99_is_reasonable():
    mod = _import_locustfile()
    p99 = mod.SLA["p99_ms"]
    assert 0 < p99 <= 5000, f"SLA p99_ms={p99} is outside sensible range (0, 5000]"


def test_sla_p99_gte_p95():
    """P99 must not be less than P95 — basic sanity."""
    mod = _import_locustfile()
    assert mod.SLA["p99_ms"] >= mod.SLA["p95_ms"], "SLA p99_ms must be >= p95_ms"


def test_sla_error_rate_is_reasonable():
    mod = _import_locustfile()
    err = mod.SLA["error_rate_pct"]
    assert 0 < err <= 5.0, f"SLA error_rate_pct={err} must be in (0, 5.0]"


def test_sla_rps_min_defined():
    mod = _import_locustfile()
    assert "rps_min" in mod.SLA, "SLA['rps_min'] not defined"
    assert mod.SLA["rps_min"] > 0, "SLA rps_min must be > 0"


# ── Section D: StepLoadShape stage validation ──────────────────────────────────

def _get_stages() -> list[dict]:
    mod = _import_locustfile()
    return mod.StepLoadShape.stages


def test_step_load_shape_has_stages_attr():
    mod = _import_locustfile()
    assert hasattr(mod.StepLoadShape, "stages"), "StepLoadShape.stages not defined"


def test_step_load_shape_minimum_stage_count():
    stages = _get_stages()
    assert len(stages) >= 3, f"StepLoadShape must have at least 3 stages, got {len(stages)}"


def test_step_load_shape_stages_have_required_keys():
    stages = _get_stages()
    for i, stage in enumerate(stages):
        for key in ("duration", "users", "spawn_rate"):
            assert key in stage, f"Stage {i} missing key '{key}': {stage}"


def test_step_load_shape_stages_ascending_durations():
    """Each stage duration must be strictly greater than the previous."""
    stages = _get_stages()
    durations = [s["duration"] for s in stages]
    for i in range(1, len(durations)):
        assert durations[i] > durations[i - 1], (
            f"Stage durations must be ascending; stage {i} duration {durations[i]} "
            f"<= stage {i-1} duration {durations[i-1]}"
        )


def test_step_load_shape_peak_users_at_least_100():
    """The peak user count must reach at least 100 for a meaningful load test."""
    stages = _get_stages()
    peak = max(s["users"] for s in stages)
    assert peak >= 100, f"Peak users {peak} < 100 — load test won't generate meaningful load"


def test_step_load_shape_all_spawn_rates_positive():
    stages = _get_stages()
    for i, stage in enumerate(stages):
        assert stage["spawn_rate"] >= 1, f"Stage {i} spawn_rate must be >= 1, got {stage['spawn_rate']}"


# ── Section E: locust.conf key validation ─────────────────────────────────────

def _parse_locust_conf() -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    # locust.conf uses flat key=value without section headers; add a dummy section.
    content = "[DEFAULT]\n" + LOCUST_CONF.read_text(encoding="utf-8")
    # Strip comment lines starting with '#' that configparser might mishandle
    lines = [l for l in content.splitlines() if not l.lstrip().startswith("#")]
    cp.read_string("\n".join(lines))
    return cp


def test_locust_conf_has_host():
    cp = _parse_locust_conf()
    assert cp.defaults().get("host"), "locust.conf must define 'host'"


def test_locust_conf_has_users():
    cp = _parse_locust_conf()
    assert cp.defaults().get("users"), "locust.conf must define 'users'"


def test_locust_conf_users_is_numeric():
    cp = _parse_locust_conf()
    users_str = cp.defaults().get("users", "0")
    assert users_str.strip().isdigit(), f"locust.conf users='{users_str}' is not an integer"


def test_locust_conf_has_spawn_rate():
    cp = _parse_locust_conf()
    assert cp.defaults().get("spawn-rate"), "locust.conf must define 'spawn-rate'"


def test_locust_conf_has_run_time():
    cp = _parse_locust_conf()
    assert cp.defaults().get("run-time"), "locust.conf must define 'run-time'"


def test_locust_conf_html_report_configured():
    cp = _parse_locust_conf()
    assert cp.defaults().get("html"), "locust.conf must define 'html' report path"


def test_locust_conf_csv_configured():
    cp = _parse_locust_conf()
    assert cp.defaults().get("csv"), "locust.conf must define 'csv' output path"


# ── Section F: Locust package + class hierarchy (skipped without locust) ───────

@pytest.mark.skipif(not LOCUST_PKG_AVAILABLE, reason="locust package not installed")
def test_locust_package_importable():
    import locust  # noqa: F401
    assert hasattr(locust, "HttpUser"), "locust.HttpUser not found"


@pytest.mark.skipif(not LOCUST_PKG_AVAILABLE, reason="locust package not installed")
def test_seventh_ai_user_inherits_http_user():
    from locust import HttpUser  # type: ignore
    mod = _import_locustfile()
    assert issubclass(mod.SeventhAIUser, HttpUser), \
        "SeventhAIUser must inherit from locust.HttpUser"


@pytest.mark.skipif(not LOCUST_PKG_AVAILABLE, reason="locust package not installed")
def test_step_load_shape_inherits_load_test_shape():
    from locust import LoadTestShape  # type: ignore
    mod = _import_locustfile()
    assert issubclass(mod.StepLoadShape, LoadTestShape), \
        "StepLoadShape must inherit from locust.LoadTestShape"


@pytest.mark.skipif(not LOCUST_PKG_AVAILABLE, reason="locust package not installed")
def test_read_tasks_inherits_task_set():
    from locust import TaskSet  # type: ignore
    mod = _import_locustfile()
    assert issubclass(mod.ReadTasks, TaskSet), \
        "ReadTasks must inherit from locust.TaskSet"


@pytest.mark.skipif(not LOCUST_CLI_AVAILABLE, reason="locust CLI not installed")
def test_locustfile_passes_locust_validate():
    """Run `locust --validate` to confirm locustfile is valid (locust >= 2.12)."""
    import subprocess
    result = subprocess.run(
        ["locust", "-f", str(LOCUSTFILE), "--validate"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"`locust --validate` failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


# ── Section G: Endpoint coverage checks ───────────────────────────────────────

def test_health_endpoint_in_locustfile():
    source = _locustfile_source()
    assert "/health" in source, "locustfile.py must include a /health task"


def test_auth_login_endpoint_in_locustfile():
    source = _locustfile_source()
    assert "/api/v1/auth/login" in source, "locustfile.py must include an auth/login task"


def test_alerts_endpoint_in_locustfile():
    source = _locustfile_source()
    assert "/api/v1/alerts" in source, "locustfile.py must include an /alerts task"


def test_incidents_endpoint_in_locustfile():
    source = _locustfile_source()
    assert "/api/v1/incidents" in source, "locustfile.py must include an /incidents task"


def test_analytics_endpoint_in_locustfile():
    source = _locustfile_source()
    assert "/api/v1/analytics" in source, "locustfile.py must include an /analytics task"
