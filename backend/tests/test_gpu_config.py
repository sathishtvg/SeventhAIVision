"""Gap 10 — GPU production config tests.

Validates that all GPU production config files are present and correctly structured:
  - docker/docker-compose.gpu.yml: Compose override enabling NVIDIA GPU for all 11 workers
  - scripts/gpu-setup.sh: NVIDIA Container Toolkit installation script
  - docker/ai-worker-gpu.Dockerfile: CUDA-based AI worker image definition
  - helm/seventh-ai-vision/values.yaml: GPU sections for all 11 AI workers (existing)
  - helm/.../ai-worker-deployment.yaml: runtimeClassName + /dev/shm + GPU toleration
  - observability/prometheus.yml: DCGM exporter scrape target

All tests are pure filesystem/YAML checks — no DB, no Redis, no network.
They run identically inside the container (paths relative to /app) and on the host.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

# ─── Paths ────────────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent                  # /app/backend/tests/
_APP = _HERE.parents[1]                        # /app/

GPU_COMPOSE = _APP / "docker" / "docker-compose.gpu.yml"
MAIN_COMPOSE = _APP / "docker" / "docker-compose.yml"
GPU_SETUP_SCRIPT = _APP / "scripts" / "gpu-setup.sh"
GPU_DOCKERFILE = _APP / "docker" / "ai-worker-gpu.Dockerfile"
HELM_VALUES = _APP / "helm" / "seventh-ai-vision" / "values.yaml"
AI_WORKER_TEMPLATE = _APP / "helm" / "seventh-ai-vision" / "templates" / "ai-worker-deployment.yaml"
PROMETHEUS_CFG = _APP / "observability" / "prometheus.yml"

# All 11 AI worker service names as they appear in docker-compose files.
ALL_WORKER_SERVICES = [
    "ai-worker-lpr",
    "ai-worker-face",
    "ai-worker-intrusion",
    "ai-worker-ppe",
    "ai-worker-crowd",
    "ai-worker-fire-smoke",
    "ai-worker-weapon",
    "ai-worker-behavior",
    "ai-worker-tampering",
    "ai-worker-abandoned",
    "ai-worker-fall",
]

# All 11 AI worker module names as they appear in Helm values.
ALL_WORKER_MODULES = [
    "lpr", "face", "intrusion", "ppe", "crowd",
    "fire_smoke", "weapon", "behavior", "tampering", "abandoned", "fall",
]


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ─── docker-compose.gpu.yml ───────────────────────────────────────────────────

class TestGpuComposeFile:
    def test_gpu_compose_override_exists(self):
        assert GPU_COMPOSE.exists(), f"{GPU_COMPOSE} must exist"

    def test_gpu_compose_is_valid_yaml(self):
        data = _load_yaml(GPU_COMPOSE)
        assert isinstance(data, dict)

    def test_gpu_compose_has_services_key(self):
        data = _load_yaml(GPU_COMPOSE)
        assert "services" in data, "docker-compose.gpu.yml must have a 'services' key"

    def test_gpu_compose_has_all_11_workers(self):
        data = _load_yaml(GPU_COMPOSE)
        services = data.get("services", {})
        missing = [w for w in ALL_WORKER_SERVICES if w not in services]
        assert not missing, f"Missing GPU override for workers: {missing}"

    def test_gpu_compose_all_workers_have_deploy_section(self):
        data = _load_yaml(GPU_COMPOSE)
        services = data["services"]
        for worker in ALL_WORKER_SERVICES:
            assert "deploy" in services[worker], (
                f"{worker} must have a 'deploy' section in docker-compose.gpu.yml"
            )

    def test_gpu_compose_all_workers_have_nvidia_reservation(self):
        data = _load_yaml(GPU_COMPOSE)
        services = data["services"]
        for worker in ALL_WORKER_SERVICES:
            devices = (
                services[worker]
                .get("deploy", {})
                .get("resources", {})
                .get("reservations", {})
                .get("devices", [])
            )
            assert devices, f"{worker}: deploy.resources.reservations.devices is empty"
            drivers = [d.get("driver") for d in devices]
            assert "nvidia" in drivers, f"{worker}: missing nvidia driver in device reservation"

    def test_gpu_compose_nvidia_capability_is_gpu(self):
        data = _load_yaml(GPU_COMPOSE)
        services = data["services"]
        for worker in ALL_WORKER_SERVICES:
            devices = (
                services[worker]
                .get("deploy", {})
                .get("resources", {})
                .get("reservations", {})
                .get("devices", [])
            )
            nvidia_devices = [d for d in devices if d.get("driver") == "nvidia"]
            assert nvidia_devices, f"{worker}: no nvidia device found"
            caps = nvidia_devices[0].get("capabilities", [])
            assert "gpu" in caps, f"{worker}: nvidia device missing 'gpu' capability"

    def test_gpu_compose_device_count_is_positive(self):
        data = _load_yaml(GPU_COMPOSE)
        services = data["services"]
        for worker in ALL_WORKER_SERVICES:
            devices = (
                services[worker]
                .get("deploy", {})
                .get("resources", {})
                .get("reservations", {})
                .get("devices", [])
            )
            nvidia_devices = [d for d in devices if d.get("driver") == "nvidia"]
            count = nvidia_devices[0].get("count", 0)
            assert count in (1, "all") or (isinstance(count, int) and count >= 1), (
                f"{worker}: nvidia device count must be >= 1 or 'all', got {count!r}"
            )

    def test_gpu_compose_all_workers_set_cuda_device_env(self):
        data = _load_yaml(GPU_COMPOSE)
        services = data["services"]
        for worker in ALL_WORKER_SERVICES:
            env = services[worker].get("environment", {})
            if isinstance(env, list):
                env_dict = dict(e.split("=", 1) for e in env if "=" in e)
            else:
                env_dict = env or {}
            assert env_dict.get("DEVICE") == "cuda", (
                f"{worker}: environment.DEVICE must be 'cuda' in docker-compose.gpu.yml"
            )

    def test_gpu_compose_all_workers_have_shm_size(self):
        data = _load_yaml(GPU_COMPOSE)
        services = data["services"]
        for worker in ALL_WORKER_SERVICES:
            assert "shm_size" in services[worker], (
                f"{worker}: shm_size must be set for GPU workers (CUDA needs >64MB shared memory)"
            )

    def test_gpu_compose_shm_size_is_at_least_1g(self):
        data = _load_yaml(GPU_COMPOSE)
        services = data["services"]
        for worker in ALL_WORKER_SERVICES:
            shm = services[worker].get("shm_size", "0")
            shm_str = str(shm).lower()
            assert re.search(r"[1-9][0-9]*g", shm_str), (
                f"{worker}: shm_size '{shm}' should be at least 1g for GPU workloads"
            )

    def test_gpu_compose_worker_count_matches_expected(self):
        data = _load_yaml(GPU_COMPOSE)
        services = data.get("services", {})
        gpu_workers = [s for s in services if s.startswith("ai-worker-")]
        assert len(gpu_workers) == len(ALL_WORKER_SERVICES), (
            f"Expected {len(ALL_WORKER_SERVICES)} AI workers, found {len(gpu_workers)}: {gpu_workers}"
        )


# ─── scripts/gpu-setup.sh ────────────────────────────────────────────────────

class TestGpuSetupScript:
    def test_gpu_setup_script_exists(self):
        assert GPU_SETUP_SCRIPT.exists(), f"{GPU_SETUP_SCRIPT} must exist"

    def test_gpu_setup_script_has_bash_shebang(self):
        content = GPU_SETUP_SCRIPT.read_text()
        first_line = content.splitlines()[0]
        assert first_line.startswith("#!") and "bash" in first_line, (
            f"gpu-setup.sh must start with a bash shebang, got: {first_line!r}"
        )

    def test_gpu_setup_script_has_set_e(self):
        content = GPU_SETUP_SCRIPT.read_text()
        assert "set -e" in content, "gpu-setup.sh must use 'set -e' to fail on errors"

    def test_gpu_setup_script_installs_nvidia_container_toolkit(self):
        content = GPU_SETUP_SCRIPT.read_text()
        assert "nvidia-container-toolkit" in content, (
            "gpu-setup.sh must install nvidia-container-toolkit"
        )

    def test_gpu_setup_script_configures_docker_runtime(self):
        content = GPU_SETUP_SCRIPT.read_text()
        assert "nvidia-ctk runtime configure" in content, (
            "gpu-setup.sh must run 'nvidia-ctk runtime configure' to wire the Docker daemon"
        )

    def test_gpu_setup_script_mentions_docker_restart(self):
        content = GPU_SETUP_SCRIPT.read_text()
        assert "restart docker" in content.lower() or "systemctl restart docker" in content, (
            "gpu-setup.sh must restart the Docker daemon after configuring the nvidia runtime"
        )

    def test_gpu_setup_script_mentions_nvidia_driver(self):
        content = GPU_SETUP_SCRIPT.read_text()
        assert "nvidia-driver" in content or "NVIDIA_DRIVER_VERSION" in content, (
            "gpu-setup.sh must reference nvidia-driver installation"
        )


# ─── docker/ai-worker-gpu.Dockerfile ─────────────────────────────────────────

class TestAiWorkerGpuDockerfile:
    def test_gpu_dockerfile_exists(self):
        assert GPU_DOCKERFILE.exists(), f"{GPU_DOCKERFILE} must exist"

    def test_gpu_dockerfile_uses_nvidia_cuda_base(self):
        content = GPU_DOCKERFILE.read_text()
        assert "FROM nvidia/cuda" in content, (
            "ai-worker-gpu.Dockerfile must use nvidia/cuda as the base image"
        )

    def test_gpu_dockerfile_cuda_version_is_pinned(self):
        content = GPU_DOCKERFILE.read_text()
        from_line = next(l for l in content.splitlines() if l.startswith("FROM"))
        assert ":" in from_line, "CUDA base image tag must be pinned (not 'latest')"
        assert "latest" not in from_line, "CUDA base image must not use 'latest' tag"

    def test_gpu_dockerfile_installs_cuda_torch(self):
        content = GPU_DOCKERFILE.read_text()
        assert "cu121" in content or "cu118" in content or "cu122" in content or "cu126" in content, (
            "ai-worker-gpu.Dockerfile must install the CUDA wheel of PyTorch "
            "(e.g. --index-url .../whl/cu121)"
        )

    def test_gpu_dockerfile_sets_device_env(self):
        content = GPU_DOCKERFILE.read_text()
        assert "ENV DEVICE=cuda" in content or "DEVICE cuda" in content, (
            "ai-worker-gpu.Dockerfile should set ENV DEVICE=cuda as the default"
        )

    def test_gpu_dockerfile_installs_onnxruntime_gpu(self):
        content = GPU_DOCKERFILE.read_text()
        assert "onnxruntime-gpu" in content, (
            "ai-worker-gpu.Dockerfile must install onnxruntime-gpu (not the CPU-only onnxruntime)"
        )

    def test_gpu_dockerfile_exposes_metrics_port(self):
        content = GPU_DOCKERFILE.read_text()
        assert "EXPOSE 8001" in content, (
            "ai-worker-gpu.Dockerfile must EXPOSE 8001 (Prometheus metrics port)"
        )


# ─── Helm chart GPU config ────────────────────────────────────────────────────

class TestHelmGpuConfig:
    def test_helm_values_all_workers_have_gpu_section(self):
        data = _load_yaml(HELM_VALUES)
        workers = data.get("aiWorkers", [])
        modules_in_values = [w["name"] for w in workers]
        for module in ALL_WORKER_MODULES:
            assert module in modules_in_values, (
                f"Helm values.yaml: GPU module '{module}' is missing from aiWorkers"
            )

    def test_helm_values_all_workers_have_gpu_resource_key(self):
        data = _load_yaml(HELM_VALUES)
        for worker in data.get("aiWorkers", []):
            gpu = worker.get("gpu", {})
            assert gpu.get("resourceKey") == "nvidia.com/gpu", (
                f"Worker '{worker['name']}': gpu.resourceKey must be 'nvidia.com/gpu'"
            )

    def test_helm_values_all_workers_have_shm_size(self):
        data = _load_yaml(HELM_VALUES)
        for worker in data.get("aiWorkers", []):
            gpu = worker.get("gpu", {})
            assert "shmSize" in gpu, (
                f"Worker '{worker['name']}': gpu.shmSize must be defined in values.yaml"
            )

    def test_helm_values_dcgm_exporter_section_exists(self):
        data = _load_yaml(HELM_VALUES)
        assert "dcgmExporter" in data, (
            "helm/values.yaml must have a 'dcgmExporter' section for GPU metrics"
        )

    def test_helm_values_dcgm_exporter_has_enabled_flag(self):
        data = _load_yaml(HELM_VALUES)
        dcgm = data.get("dcgmExporter", {})
        assert "enabled" in dcgm, "dcgmExporter.enabled must be defined (default false)"

    def test_helm_values_dcgm_exporter_has_nvidia_toleration(self):
        data = _load_yaml(HELM_VALUES)
        tolerations = data.get("dcgmExporter", {}).get("tolerations", [])
        keys = [t.get("key") for t in tolerations]
        assert "nvidia.com/gpu" in keys, (
            "dcgmExporter.tolerations must include the nvidia.com/gpu taint"
        )


# ─── Helm AI worker deployment template ──────────────────────────────────────

class TestHelmAiWorkerTemplate:
    def _template_content(self) -> str:
        return AI_WORKER_TEMPLATE.read_text()

    def test_helm_template_exists(self):
        assert AI_WORKER_TEMPLATE.exists()

    def test_helm_template_has_gpu_enabled_conditional(self):
        content = self._template_content()
        assert "gpu.enabled" in content, (
            "ai-worker-deployment.yaml must conditionally handle gpu.enabled"
        )

    def test_helm_template_sets_runtime_class_when_gpu_enabled(self):
        content = self._template_content()
        assert "runtimeClassName" in content, (
            "ai-worker-deployment.yaml must set runtimeClassName: nvidia when gpu.enabled"
        )
        assert "nvidia" in content, (
            "runtimeClassName must reference the 'nvidia' runtime"
        )

    def test_helm_template_mounts_dev_shm_when_gpu_enabled(self):
        content = self._template_content()
        assert "/dev/shm" in content, (
            "ai-worker-deployment.yaml must mount /dev/shm for GPU shared memory"
        )

    def test_helm_template_adds_gpu_resource_limit(self):
        content = self._template_content()
        assert ".gpu.resourceKey" in content and ".gpu.count" in content, (
            "ai-worker-deployment.yaml must inject gpu.resourceKey and gpu.count as resource limits"
        )

    def test_helm_template_adds_nvidia_toleration_when_gpu_enabled(self):
        content = self._template_content()
        assert "nvidia.com/gpu" in content, (
            "ai-worker-deployment.yaml must add the nvidia.com/gpu toleration when gpu.enabled"
        )
        assert "NoSchedule" in content, (
            "The GPU toleration must use effect: NoSchedule"
        )

    def test_helm_template_sets_device_env_when_gpu_enabled(self):
        content = self._template_content()
        assert "DEVICE" in content and "cuda" in content, (
            "ai-worker-deployment.yaml must set DEVICE=cuda env var when gpu.enabled"
        )


# ─── Prometheus DCGM scrape target ───────────────────────────────────────────

class TestPrometheusDcgmScrape:
    def test_prometheus_cfg_exists(self):
        assert PROMETHEUS_CFG.exists()

    def test_prometheus_dcgm_scrape_target_exists(self):
        data = _load_yaml(PROMETHEUS_CFG)
        jobs = [job.get("job_name") for job in data.get("scrape_configs", [])]
        assert "dcgm-exporter" in jobs, (
            "observability/prometheus.yml must have a 'dcgm-exporter' scrape job"
        )

    def test_prometheus_dcgm_port_is_9400(self):
        data = _load_yaml(PROMETHEUS_CFG)
        dcgm_job = next(
            (j for j in data.get("scrape_configs", []) if j.get("job_name") == "dcgm-exporter"),
            None,
        )
        assert dcgm_job is not None
        targets = dcgm_job.get("static_configs", [{}])[0].get("targets", [])
        assert any("9400" in str(t) for t in targets), (
            "dcgm-exporter scrape target must be on port 9400"
        )
