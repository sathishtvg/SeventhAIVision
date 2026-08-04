"""Gap 14 — OpenTelemetry distributed tracing infrastructure tests.

Validates the OTEL configuration WITHOUT running the collector or requiring
opentelemetry packages to be installed.  Tests that import tracing.py directly
succeed because tracing.py uses conditional imports (same pattern as locustfile.py).

Coverage:
  Section A: File structure (otel-collector-config.yml, tracing.py, compose overlay)
  Section B: OTel Collector config — YAML validity and top-level keys
  Section C: OTel Collector config — receivers
  Section D: OTel Collector config — processors
  Section E: OTel Collector config — exporters
  Section F: OTel Collector config — service pipelines
  Section G: tracing.py — Python syntax and module structure
  Section H: tracing.py — environment variable defaults and sampling
  Section I: Docker Compose overlay — YAML validity
  Section J: Docker Compose overlay — Jaeger service
  Section K: Docker Compose overlay — OTel Collector service
"""

from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path

import pytest
import yaml

from tests._repo import REPO_ROOT, requires_repo_tree

# Skips the whole module when the repository tree is absent (e.g. inside the
# api image, which ships only backend/). See tests/_repo.py for why failing
# would be the wrong signal here.
pytestmark = requires_repo_tree


# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent           # /app/backend/tests/
_PROJECT_ROOT = _HERE.parents[1]        # /app/

OTEL_COLLECTOR_CONFIG = Path(
    os.environ.get("OTEL_COLLECTOR_CONFIG_PATH",
                   str(_PROJECT_ROOT / "observability" / "otel-collector-config.yml"))
)
TRACING_MODULE = Path(
    os.environ.get("TRACING_MODULE_PATH",
                   str(_PROJECT_ROOT / "backend" / "app" / "core" / "tracing.py"))
)
DOCKER_COMPOSE_OTEL = Path(
    os.environ.get("DOCKER_COMPOSE_OTEL_PATH",
                   str(_PROJECT_ROOT / "docker" / "docker-compose.otel.yml"))
)


# ── Section A: File structure ─────────────────────────────────────────────────

def test_otel_collector_config_exists():
    assert OTEL_COLLECTOR_CONFIG.is_file(), \
        f"observability/otel-collector-config.yml not found at {OTEL_COLLECTOR_CONFIG}"


def test_tracing_module_exists():
    assert TRACING_MODULE.is_file(), \
        f"backend/app/core/tracing.py not found at {TRACING_MODULE}"


def test_docker_compose_otel_overlay_exists():
    assert DOCKER_COMPOSE_OTEL.is_file(), \
        f"docker/docker-compose.otel.yml not found at {DOCKER_COMPOSE_OTEL}"


# ── Section B: OTel Collector config — YAML validity ─────────────────────────

def _load_otel_config() -> dict:
    return yaml.safe_load(OTEL_COLLECTOR_CONFIG.read_text(encoding="utf-8"))


def test_otel_collector_config_valid_yaml():
    try:
        cfg = _load_otel_config()
    except yaml.YAMLError as exc:
        pytest.fail(f"otel-collector-config.yml is not valid YAML: {exc}")
    assert isinstance(cfg, dict), "otel-collector-config.yml must parse to a dict"


def test_otel_collector_config_has_top_level_keys():
    cfg = _load_otel_config()
    for key in ("receivers", "processors", "exporters", "service"):
        assert key in cfg, f"otel-collector-config.yml missing top-level key '{key}'"


# ── Section C: OTel Collector config — receivers ──────────────────────────────

def test_otel_config_has_receivers():
    cfg = _load_otel_config()
    assert "receivers" in cfg and cfg["receivers"], "No receivers defined"


def test_otel_config_has_otlp_receiver():
    cfg = _load_otel_config()
    assert "otlp" in cfg.get("receivers", {}), "otlp receiver not configured"


def test_otel_config_otlp_grpc_endpoint_defined():
    cfg = _load_otel_config()
    grpc = cfg["receivers"]["otlp"]["protocols"]["grpc"]
    assert "endpoint" in grpc, "otlp.grpc.endpoint not set"
    assert "4317" in str(grpc["endpoint"]), \
        f"Expected port 4317 in otlp.grpc.endpoint, got: {grpc['endpoint']}"


def test_otel_config_otlp_http_endpoint_defined():
    cfg = _load_otel_config()
    http = cfg["receivers"]["otlp"]["protocols"]["http"]
    assert "endpoint" in http, "otlp.http.endpoint not set"
    assert "4318" in str(http["endpoint"]), \
        f"Expected port 4318 in otlp.http.endpoint, got: {http['endpoint']}"


# ── Section D: OTel Collector config — processors ────────────────────────────

def test_otel_config_has_batch_processor():
    cfg = _load_otel_config()
    assert "batch" in cfg.get("processors", {}), "batch processor not configured"


def test_otel_config_batch_processor_has_timeout():
    cfg = _load_otel_config()
    batch = cfg["processors"]["batch"]
    assert "timeout" in batch, "batch processor must define timeout"


def test_otel_config_has_memory_limiter_processor():
    cfg = _load_otel_config()
    assert "memory_limiter" in cfg.get("processors", {}), \
        "memory_limiter processor not configured"


def test_otel_config_memory_limiter_has_limit():
    cfg = _load_otel_config()
    ml = cfg["processors"]["memory_limiter"]
    assert "limit_mib" in ml, "memory_limiter must define limit_mib"
    assert int(ml["limit_mib"]) > 0, "limit_mib must be > 0"


# ── Section E: OTel Collector config — exporters ─────────────────────────────

def test_otel_config_has_exporters():
    cfg = _load_otel_config()
    assert cfg.get("exporters"), "No exporters defined"


def test_otel_config_has_jaeger_exporter():
    cfg = _load_otel_config()
    exporters = cfg.get("exporters", {})
    jaeger_keys = [k for k in exporters if "jaeger" in k.lower()]
    assert jaeger_keys, \
        "No Jaeger exporter found in exporters (expected 'otlp/jaeger' or similar)"


def test_otel_config_jaeger_exporter_has_endpoint():
    cfg = _load_otel_config()
    exporters = cfg.get("exporters", {})
    jaeger_key = next((k for k in exporters if "jaeger" in k.lower()), None)
    assert jaeger_key is not None
    jaeger_cfg = exporters[jaeger_key]
    assert "endpoint" in jaeger_cfg, f"Jaeger exporter ({jaeger_key}) missing endpoint"


def test_otel_config_has_prometheus_exporter():
    cfg = _load_otel_config()
    assert "prometheus" in cfg.get("exporters", {}), \
        "prometheus exporter not configured"


def test_otel_config_prometheus_exporter_has_endpoint():
    cfg = _load_otel_config()
    prom = cfg["exporters"]["prometheus"]
    assert "endpoint" in prom, "prometheus exporter missing endpoint"
    assert "8889" in str(prom["endpoint"]), \
        f"Expected port 8889 in prometheus exporter endpoint, got: {prom['endpoint']}"


# ── Section F: OTel Collector config — service pipelines ─────────────────────

def test_otel_config_has_service_section():
    cfg = _load_otel_config()
    assert "service" in cfg, "service section missing from otel-collector-config.yml"


def test_otel_config_has_traces_pipeline():
    cfg = _load_otel_config()
    pipelines = cfg.get("service", {}).get("pipelines", {})
    assert "traces" in pipelines, "traces pipeline not defined in service.pipelines"


def test_otel_config_traces_pipeline_has_receivers():
    cfg = _load_otel_config()
    traces = cfg["service"]["pipelines"]["traces"]
    assert traces.get("receivers"), "traces pipeline must define receivers"


def test_otel_config_traces_pipeline_has_exporters():
    cfg = _load_otel_config()
    traces = cfg["service"]["pipelines"]["traces"]
    assert traces.get("exporters"), "traces pipeline must define exporters"


def test_otel_config_traces_pipeline_exports_to_jaeger():
    cfg = _load_otel_config()
    traces_exporters = cfg["service"]["pipelines"]["traces"].get("exporters", [])
    has_jaeger = any("jaeger" in str(e).lower() for e in traces_exporters)
    assert has_jaeger, \
        f"traces pipeline must export to Jaeger; got: {traces_exporters}"


def test_otel_config_has_metrics_pipeline():
    cfg = _load_otel_config()
    pipelines = cfg.get("service", {}).get("pipelines", {})
    assert "metrics" in pipelines, "metrics pipeline not defined in service.pipelines"


# ── Section G: tracing.py — Python syntax and module structure ────────────────

def _tracing_source() -> str:
    return TRACING_MODULE.read_text(encoding="utf-8")


def test_tracing_module_valid_python_syntax():
    source = _tracing_source()
    try:
        ast.parse(source)
    except SyntaxError as exc:
        pytest.fail(f"tracing.py has a syntax error: {exc}")


def test_setup_tracing_function_defined():
    source = _tracing_source()
    assert "def setup_tracing" in source, "setup_tracing() not defined in tracing.py"


def test_get_tracer_function_defined():
    source = _tracing_source()
    assert "def get_tracer" in source, "get_tracer() not defined in tracing.py"


def test_otel_enabled_env_var_referenced():
    source = _tracing_source()
    assert "OTEL_ENABLED" in source, "OTEL_ENABLED env var not referenced in tracing.py"


def test_otel_endpoint_env_var_referenced():
    source = _tracing_source()
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in source, \
        "OTEL_EXPORTER_OTLP_ENDPOINT env var not referenced in tracing.py"


def test_otel_service_name_env_var_referenced():
    source = _tracing_source()
    assert "OTEL_SERVICE_NAME" in source, \
        "OTEL_SERVICE_NAME env var not referenced in tracing.py"


def test_fastapi_instrumentor_referenced():
    source = _tracing_source()
    assert "FastAPIInstrumentor" in source, \
        "FastAPIInstrumentor not referenced — FastAPI auto-instrumentation missing"


def test_sqlalchemy_instrumentor_referenced():
    source = _tracing_source()
    assert "SQLAlchemyInstrumentor" in source, \
        "SQLAlchemyInstrumentor not referenced — DB query tracing missing"


def test_redis_instrumentor_referenced():
    source = _tracing_source()
    assert "RedisInstrumentor" in source, \
        "RedisInstrumentor not referenced — Redis command tracing missing"


def test_batch_span_processor_referenced():
    source = _tracing_source()
    assert "BatchSpanProcessor" in source, \
        "BatchSpanProcessor not used — spans may be flushed synchronously (bad for latency)"


def test_otlp_exporter_referenced():
    source = _tracing_source()
    assert "OTLPSpanExporter" in source, \
        "OTLPSpanExporter not referenced in tracing.py"


# ── Section H: tracing.py — defaults and sampling ────────────────────────────

def _import_tracing():
    """Import tracing.py as a module (works even without opentelemetry installed)."""
    spec = importlib.util.spec_from_file_location("tracing_under_test", TRACING_MODULE)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_otel_endpoint_default_points_to_collector():
    mod = _import_tracing()
    assert "otel-collector" in mod.OTEL_ENDPOINT, \
        f"Default OTEL_ENDPOINT should point to otel-collector, got: {mod.OTEL_ENDPOINT}"


def test_otel_endpoint_default_uses_grpc_port():
    mod = _import_tracing()
    assert "4317" in mod.OTEL_ENDPOINT, \
        f"Default OTEL_ENDPOINT should use port 4317 (gRPC), got: {mod.OTEL_ENDPOINT}"


def test_otel_service_name_default_is_seventh_ai():
    mod = _import_tracing()
    assert "seventh" in mod.OTEL_SERVICE_NAME.lower() or "seventh-ai" in mod.OTEL_SERVICE_NAME, \
        f"Default OTEL_SERVICE_NAME should identify Seventh AI Vision, got: {mod.OTEL_SERVICE_NAME}"


def test_otel_sample_ratio_default_is_valid():
    mod = _import_tracing()
    assert 0.0 <= mod.OTEL_SAMPLE_RATIO <= 1.0, \
        f"Default OTEL_SAMPLE_RATIO must be in [0.0, 1.0], got: {mod.OTEL_SAMPLE_RATIO}"


def test_otel_enabled_default_is_true():
    mod = _import_tracing()
    assert mod.OTEL_ENABLED is True, \
        "OTEL_ENABLED should default to True (opt-out, not opt-in)"


def test_sampling_ratio_env_var_referenced():
    source = _tracing_source()
    assert "OTEL_TRACES_SAMPLER_ARG" in source, \
        "OTEL_TRACES_SAMPLER_ARG not referenced — sampling ratio is not configurable"


# ── Section I: Docker Compose overlay — YAML validity ────────────────────────

def _load_compose_otel() -> dict:
    return yaml.safe_load(DOCKER_COMPOSE_OTEL.read_text(encoding="utf-8"))


def test_docker_compose_otel_valid_yaml():
    try:
        cfg = _load_compose_otel()
    except yaml.YAMLError as exc:
        pytest.fail(f"docker-compose.otel.yml is not valid YAML: {exc}")
    assert isinstance(cfg, dict)


def test_docker_compose_otel_has_services():
    cfg = _load_compose_otel()
    assert "services" in cfg, "docker-compose.otel.yml missing 'services' key"
    assert cfg["services"], "docker-compose.otel.yml services section is empty"


def test_docker_compose_otel_has_at_least_two_new_services():
    cfg = _load_compose_otel()
    services = cfg.get("services", {})
    assert len(services) >= 2, \
        f"Expected at least 2 services (jaeger + otel-collector), got {len(services)}"


# ── Section J: Docker Compose overlay — Jaeger service ───────────────────────

def test_docker_compose_otel_has_jaeger_service():
    cfg = _load_compose_otel()
    assert "jaeger" in cfg.get("services", {}), \
        "jaeger service not in docker-compose.otel.yml"


def test_docker_compose_otel_jaeger_image_defined():
    cfg = _load_compose_otel()
    jaeger = cfg["services"]["jaeger"]
    assert "image" in jaeger, "jaeger service has no image"
    assert "jaeger" in jaeger["image"].lower(), \
        f"jaeger image does not reference jaeger: {jaeger['image']}"


def test_docker_compose_otel_jaeger_has_ui_port():
    cfg = _load_compose_otel()
    jaeger = cfg["services"]["jaeger"]
    ports = [str(p) for p in jaeger.get("ports", [])]
    has_ui_port = any("16686" in p for p in ports)
    assert has_ui_port, \
        f"jaeger service must expose Jaeger UI port 16686; got ports: {ports}"


def test_docker_compose_otel_jaeger_has_health_check():
    cfg = _load_compose_otel()
    jaeger = cfg["services"]["jaeger"]
    assert "healthcheck" in jaeger, \
        "jaeger service should have a healthcheck so otel-collector can depend on it"


# ── Section K: Docker Compose overlay — OTel Collector service ───────────────

def test_docker_compose_otel_has_otel_collector_service():
    cfg = _load_compose_otel()
    assert "otel-collector" in cfg.get("services", {}), \
        "otel-collector service not in docker-compose.otel.yml"


def test_docker_compose_otel_collector_image_defined():
    cfg = _load_compose_otel()
    svc = cfg["services"]["otel-collector"]
    assert "image" in svc, "otel-collector service has no image"
    assert "otel" in svc["image"].lower() or "opentelemetry" in svc["image"].lower(), \
        f"otel-collector image does not reference opentelemetry: {svc['image']}"


def test_docker_compose_otel_collector_mounts_config():
    cfg = _load_compose_otel()
    svc = cfg["services"]["otel-collector"]
    volumes = [str(v) for v in svc.get("volumes", [])]
    mounts_config = any("otel-collector-config" in v for v in volumes)
    assert mounts_config, \
        f"otel-collector must mount otel-collector-config.yml; got volumes: {volumes}"


def test_docker_compose_otel_collector_exposes_grpc_port():
    cfg = _load_compose_otel()
    svc = cfg["services"]["otel-collector"]
    ports = [str(p) for p in svc.get("ports", [])]
    assert any("4317" in p for p in ports), \
        f"otel-collector must expose OTLP gRPC port 4317; got: {ports}"


def test_docker_compose_otel_collector_exposes_http_port():
    cfg = _load_compose_otel()
    svc = cfg["services"]["otel-collector"]
    ports = [str(p) for p in svc.get("ports", [])]
    assert any("4318" in p for p in ports), \
        f"otel-collector must expose OTLP HTTP port 4318; got: {ports}"


def test_docker_compose_otel_collector_depends_on_jaeger():
    cfg = _load_compose_otel()
    svc = cfg["services"]["otel-collector"]
    depends = svc.get("depends_on", {})
    if isinstance(depends, dict):
        assert "jaeger" in depends, "otel-collector should depend on jaeger"
    else:
        assert "jaeger" in depends, "otel-collector should depend on jaeger"


def test_docker_compose_otel_api_service_has_otel_env():
    cfg = _load_compose_otel()
    services = cfg.get("services", {})
    if "api" not in services:
        pytest.skip("api service not extended in this overlay (acceptable)")
    api = services["api"]
    env = api.get("environment", {})
    if isinstance(env, dict):
        assert "OTEL_ENABLED" in env or "OTEL_EXPORTER_OTLP_ENDPOINT" in env, \
            "api service environment should include OTEL env vars"
    else:
        env_str = " ".join(str(e) for e in env)
        assert "OTEL_ENABLED" in env_str or "OTEL_EXPORTER_OTLP_ENDPOINT" in env_str, \
            "api service environment should include OTEL env vars"
