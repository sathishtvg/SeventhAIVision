"""
Gap 7 — Kubernetes/Helm chart validation tests.
These run inside the API container (no helm CLI required for most tests).
Tests that call `helm` are skipped when it is not installed.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests._repo import REPO_ROOT, requires_repo_tree

# Skips the whole module when the repository tree is absent (e.g. inside the
# api image, which ships only backend/). See tests/_repo.py for why failing
# would be the wrong signal here.
pytestmark = requires_repo_tree


# ─── Paths ────────────────────────────────────────────────────────────────────

# /app/backend/tests/ → /app/ → helm/seventh-ai-vision/
_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]
CHART_DIR = _ENV_OVERRIDE = os.environ.get("HELM_CHART_DIR") or str(
    _PROJECT_ROOT / "helm" / "seventh-ai-vision"
)
CHART_DIR = Path(CHART_DIR)
TEMPLATES_DIR = CHART_DIR / "templates"

HELM_AVAILABLE = shutil.which("helm") is not None

# ─── Expected template files ───────────────────────────────────────────────────

EXPECTED_TEMPLATES = [
    "_helpers.tpl",
    "serviceaccount.yaml",
    "configmap.yaml",
    "secret.yaml",
    "pvc.yaml",
    "api-deployment.yaml",
    "api-service.yaml",
    "api-hpa.yaml",
    "ingestion-deployment.yaml",
    "scheduler-deployment.yaml",
    "ai-worker-deployment.yaml",
    "ai-worker-hpa.yaml",
    "frontend-deployment.yaml",
    "frontend-service.yaml",
    "postgres-statefulset.yaml",
    "postgres-service.yaml",
    "redis-statefulset.yaml",
    "redis-service.yaml",
    "minio-deployment.yaml",
    "minio-service.yaml",
    "ingress.yaml",
    "prometheus-deployment.yaml",
    "grafana-deployment.yaml",
]

EXPECTED_WORKER_NAMES = [
    "lpr",
    "face",
    "intrusion",
    "ppe",
    "crowd",
    "fire_smoke",
    "weapon",
    "behavior",
    "tampering",
    "abandoned",
    "fall",
]


def _load_values() -> dict:
    values_path = CHART_DIR / "values.yaml"
    with open(values_path) as f:
        return yaml.safe_load(f)


# ─── Chart.yaml structure ─────────────────────────────────────────────────────

def test_chart_yaml_exists():
    assert (CHART_DIR / "Chart.yaml").exists(), "Chart.yaml not found"


def test_chart_yaml_valid():
    with open(CHART_DIR / "Chart.yaml") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "Chart.yaml must be a YAML mapping"


def test_chart_yaml_api_version():
    with open(CHART_DIR / "Chart.yaml") as f:
        data = yaml.safe_load(f)
    assert data.get("apiVersion") == "v2", "apiVersion must be v2 (Helm 3)"


def test_chart_yaml_name():
    with open(CHART_DIR / "Chart.yaml") as f:
        data = yaml.safe_load(f)
    assert data.get("name") == "seventh-ai-vision"


def test_chart_yaml_version_semver():
    with open(CHART_DIR / "Chart.yaml") as f:
        data = yaml.safe_load(f)
    version = data.get("version", "")
    assert re.match(r"^\d+\.\d+\.\d+", version), f"version '{version}' is not semver"


def test_chart_yaml_app_version():
    with open(CHART_DIR / "Chart.yaml") as f:
        data = yaml.safe_load(f)
    assert "appVersion" in data, "appVersion is required"


# ─── values.yaml structure ────────────────────────────────────────────────────

def test_values_yaml_exists():
    assert (CHART_DIR / "values.yaml").exists(), "values.yaml not found"


def test_values_yaml_valid():
    values = _load_values()
    assert isinstance(values, dict)


def test_values_has_api_section():
    values = _load_values()
    assert "api" in values
    api = values["api"]
    assert "replicas" in api
    assert "resources" in api


def test_values_api_has_autoscaling():
    values = _load_values()
    hpa = values["api"]["autoscaling"]
    assert "enabled" in hpa
    assert "minReplicas" in hpa
    assert "maxReplicas" in hpa
    assert "targetCPUUtilizationPercentage" in hpa
    assert hpa["minReplicas"] <= hpa["maxReplicas"]


def test_values_api_has_resource_limits():
    values = _load_values()
    resources = values["api"]["resources"]
    assert "requests" in resources
    assert "limits" in resources
    assert "cpu" in resources["requests"]
    assert "memory" in resources["requests"]


def test_values_has_all_11_ai_workers():
    values = _load_values()
    worker_names = [w["name"] for w in values["aiWorkers"]]
    for expected in EXPECTED_WORKER_NAMES:
        assert expected in worker_names, f"aiWorker '{expected}' missing from values.yaml"


def test_all_ai_workers_have_enabled_flag():
    values = _load_values()
    for worker in values["aiWorkers"]:
        assert "enabled" in worker, f"Worker '{worker['name']}' missing 'enabled'"


def test_all_ai_workers_have_replicas():
    values = _load_values()
    for worker in values["aiWorkers"]:
        assert "replicas" in worker, f"Worker '{worker['name']}' missing 'replicas'"
        assert isinstance(worker["replicas"], int)
        assert worker["replicas"] >= 1


def test_all_ai_workers_have_resource_requests():
    values = _load_values()
    for worker in values["aiWorkers"]:
        assert "resources" in worker, f"Worker '{worker['name']}' missing 'resources'"
        assert "requests" in worker["resources"]
        assert "limits" in worker["resources"]


def test_all_ai_workers_have_gpu_config():
    values = _load_values()
    for worker in values["aiWorkers"]:
        assert "gpu" in worker, f"Worker '{worker['name']}' missing 'gpu' section"
        assert "enabled" in worker["gpu"]
        assert "count" in worker["gpu"]
        assert "resourceKey" in worker["gpu"]


def test_values_has_postgres_section():
    values = _load_values()
    pg = values["postgres"]
    assert "enabled" in pg
    assert "database" in pg
    assert "username" in pg
    assert "storage" in pg
    assert "size" in pg["storage"]


def test_values_has_redis_section():
    values = _load_values()
    rd = values["redis"]
    assert "enabled" in rd
    assert "storage" in rd
    assert "size" in rd["storage"]


def test_values_has_minio_section():
    values = _load_values()
    mn = values["minio"]
    assert "enabled" in mn
    assert "bucket" in mn
    assert "accessKey" in mn


def test_values_has_ingress_section():
    values = _load_values()
    ing = values["ingress"]
    assert "enabled" in ing
    assert "className" in ing
    assert "host" in ing
    assert "tls" in ing


def test_values_has_pvc_config():
    values = _load_values()
    pers = values["persistence"]
    assert "evidence" in pers
    assert "size" in pers["evidence"]
    assert "accessMode" in pers["evidence"]
    assert "recordings" in pers
    assert "size" in pers["recordings"]


def test_values_has_secrets_section():
    values = _load_values()
    sec = values["secrets"]
    assert "jwtSecretKeyCurrent" in sec
    assert "jwtActiveKid" in sec


def test_values_has_prometheus_and_grafana():
    values = _load_values()
    assert "prometheus" in values
    assert "grafana" in values
    assert "enabled" in values["prometheus"]
    assert "enabled" in values["grafana"]


# ─── Template files ───────────────────────────────────────────────────────────

def test_all_expected_template_files_present():
    missing = []
    for fname in EXPECTED_TEMPLATES:
        if not (TEMPLATES_DIR / fname).exists():
            missing.append(fname)
    assert not missing, f"Missing template files: {missing}"


def test_all_template_yaml_files_are_non_empty():
    for fname in EXPECTED_TEMPLATES:
        if not fname.endswith(".yaml"):
            continue
        path = TEMPLATES_DIR / fname
        content = path.read_text(encoding="utf-8").strip()
        assert content, f"Template {fname} is empty"


def test_all_template_yaml_files_contain_apiversion_or_conditional():
    """Every .yaml template (not _helpers.tpl) must either declare an
    apiVersion or be fully wrapped in a conditional block."""
    for fname in EXPECTED_TEMPLATES:
        if not fname.endswith(".yaml"):
            continue
        content = (TEMPLATES_DIR / fname).read_text(encoding="utf-8")
        has_apiversion = "apiVersion" in content
        has_if = "{{- if" in content or "{{if " in content
        assert has_apiversion or has_if, (
            f"{fname}: must contain 'apiVersion' or a conditional block"
        )


def test_helpers_tpl_defines_fullname():
    content = (TEMPLATES_DIR / "_helpers.tpl").read_text(encoding="utf-8")
    assert "seventh-ai-vision.fullname" in content
    assert "seventh-ai-vision.labels" in content
    assert "seventh-ai-vision.selectorLabels" in content


# ─── Helm CLI tests (skipped when helm not installed) ─────────────────────────

@pytest.mark.skipif(not HELM_AVAILABLE, reason="helm CLI not installed in container")
def test_helm_template_renders_without_error():
    result = subprocess.run(
        ["helm", "template", "test-release", str(CHART_DIR)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"helm template failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


@pytest.mark.skipif(not HELM_AVAILABLE, reason="helm CLI not installed in container")
def test_helm_template_contains_api_deployment():
    result = subprocess.run(
        ["helm", "template", "test-release", str(CHART_DIR)],
        capture_output=True,
        text=True,
    )
    assert "test-release-seventh-ai-vision-api" in result.stdout


@pytest.mark.skipif(not HELM_AVAILABLE, reason="helm CLI not installed in container")
def test_helm_template_contains_all_enabled_worker_deployments():
    result = subprocess.run(
        ["helm", "template", "test-release", str(CHART_DIR)],
        capture_output=True,
        text=True,
    )
    for worker in EXPECTED_WORKER_NAMES:
        safe_name = worker.replace("_", "-")
        assert f"ai-worker-{safe_name}" in result.stdout, (
            f"Worker deployment for '{worker}' not found in rendered output"
        )


@pytest.mark.skipif(not HELM_AVAILABLE, reason="helm CLI not installed in container")
def test_helm_lint_passes():
    result = subprocess.run(
        ["helm", "lint", str(CHART_DIR)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"helm lint failed:\n{result.stdout}\n{result.stderr}"
    )
