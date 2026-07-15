"""Gap 16 — Chaos Engineering: infrastructure-file-validation tests.

Validates:
  A. File structure (chaos YAML files + scripts/chaos directory)
  B. Toxiproxy config YAML validity (parseable, top-level keys, key proxies present)
  C. Toxiproxy proxy schema (name/listen/upstream per proxy, no port loops)
  D. Toxiproxy scenario schema (name/proxy/toxic_type, valid types, no duplicates)
  E. Chaos experiments YAML validity (parseable, experiments list, count)
  F. Experiment schema (description/hypothesis/services/blast_radius/duration)
  G. Abort conditions and SLA assertions (required, valid metrics, postgres experiment)
  H. Docker Compose chaos overlay (valid YAML, services key, toxiproxy, control port)
"""

from __future__ import annotations

import yaml
import pytest
from pathlib import Path

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]  # /app/backend/tests/ → /app/
_CHAOS_DIR = _PROJECT_ROOT / "scripts" / "chaos"
_EXPERIMENTS_PATH = _CHAOS_DIR / "chaos-experiments.yml"
_TOXIPROXY_PATH = _CHAOS_DIR / "toxiproxy-config.yml"
_COMPOSE_CHAOS_PATH = _PROJECT_ROOT / "docker" / "docker-compose.chaos.yml"

VALID_TOXIC_TYPES = {"latency", "bandwidth", "slow_close", "timeout", "reset_peer", "slicer"}
VALID_BLAST_RADII = {"single_service", "single_dependency", "tenant_scope", "full_blast"}
VALID_ABORT_METRICS = {
    "error_rate_pct", "p99_latency_ms", "p95_latency_ms", "api_error_rate_pct",
    "pending_messages_age_seconds", "data_integrity_check",
}
VALID_SLA_METRICS = {
    "p95_latency_ms", "p99_latency_ms", "error_rate_pct", "health_endpoint_status",
    "http_4xx_5xx_ratio", "error_code_not_500", "api_p95_latency_ms", "redis_stream_lag",
    "recovery_time_seconds", "messages_lost", "data_integrity_check",
}


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_chaos_scripts_directory_exists(self):
        assert _CHAOS_DIR.is_dir(), f"Missing chaos scripts directory: {_CHAOS_DIR}"

    def test_chaos_experiments_yml_exists(self):
        assert _EXPERIMENTS_PATH.exists(), f"Missing: {_EXPERIMENTS_PATH}"

    def test_toxiproxy_config_yml_exists(self):
        assert _TOXIPROXY_PATH.exists(), f"Missing: {_TOXIPROXY_PATH}"

    def test_docker_compose_chaos_yml_exists(self):
        assert _COMPOSE_CHAOS_PATH.exists(), f"Missing: {_COMPOSE_CHAOS_PATH}"


# ─────────────────────────────────────────────────────────────
# B — Toxiproxy config YAML validity
# ─────────────────────────────────────────────────────────────

class TestBToxiproxyConfigValidity:

    @pytest.fixture(scope="class")
    def config(self):
        return _load_yaml(_TOXIPROXY_PATH)

    def test_toxiproxy_config_valid_yaml(self, config):
        assert isinstance(config, dict)

    def test_toxiproxy_config_has_proxies(self, config):
        assert "proxies" in config, "toxiproxy-config.yml missing 'proxies' key"

    def test_toxiproxy_config_has_scenarios(self, config):
        assert "scenarios" in config, "toxiproxy-config.yml missing 'scenarios' key"

    def test_toxiproxy_postgres_proxy_defined(self, config):
        names = [p.get("name") for p in config.get("proxies", [])]
        assert "postgres_proxy" in names, "postgres_proxy not found in Toxiproxy proxies"

    def test_toxiproxy_redis_proxy_defined(self, config):
        names = [p.get("name") for p in config.get("proxies", [])]
        assert "redis_proxy" in names, "redis_proxy not found in Toxiproxy proxies"

    def test_toxiproxy_api_proxy_defined(self, config):
        names = [p.get("name") for p in config.get("proxies", [])]
        assert "api_proxy" in names, "api_proxy not found in Toxiproxy proxies"


# ─────────────────────────────────────────────────────────────
# C — Toxiproxy proxy schema
# ─────────────────────────────────────────────────────────────

class TestCToxiproxyProxySchema:

    @pytest.fixture(scope="class")
    def proxies(self):
        return _load_yaml(_TOXIPROXY_PATH).get("proxies", [])

    def test_every_proxy_has_name(self, proxies):
        for p in proxies:
            assert "name" in p, f"Proxy missing 'name': {p}"

    def test_every_proxy_has_listen(self, proxies):
        for p in proxies:
            assert "listen" in p, f"Proxy '{p.get('name', '?')}' missing 'listen'"

    def test_every_proxy_has_upstream(self, proxies):
        for p in proxies:
            assert "upstream" in p, f"Proxy '{p.get('name', '?')}' missing 'upstream'"

    def test_proxy_listen_port_differs_from_upstream_port(self, proxies):
        for p in proxies:
            listen = str(p.get("listen", ""))
            upstream = str(p.get("upstream", ""))
            listen_port = listen.split(":")[-1] if ":" in listen else ""
            upstream_port = upstream.split(":")[-1] if ":" in upstream else ""
            assert listen_port != upstream_port, (
                f"Proxy '{p.get('name', '?')}' listen port equals upstream port — "
                "would create an infinite routing loop"
            )


# ─────────────────────────────────────────────────────────────
# D — Toxiproxy scenario schema
# ─────────────────────────────────────────────────────────────

class TestDToxiproxyScenarioSchema:

    @pytest.fixture(scope="class")
    def scenarios(self):
        return _load_yaml(_TOXIPROXY_PATH).get("scenarios", [])

    def test_every_scenario_has_name(self, scenarios):
        for s in scenarios:
            assert "name" in s, f"Scenario missing 'name': {s}"

    def test_every_scenario_has_proxy(self, scenarios):
        for s in scenarios:
            assert "proxy" in s, f"Scenario '{s.get('name', '?')}' missing 'proxy'"

    def test_every_scenario_has_toxic_type(self, scenarios):
        for s in scenarios:
            assert "toxic_type" in s, f"Scenario '{s.get('name', '?')}' missing 'toxic_type'"

    def test_every_scenario_toxic_type_is_valid(self, scenarios):
        for s in scenarios:
            t = s.get("toxic_type", "")
            assert t in VALID_TOXIC_TYPES, (
                f"Scenario '{s.get('name', '?')}' has invalid toxic_type '{t}'. "
                f"Must be one of {VALID_TOXIC_TYPES}"
            )

    def test_no_duplicate_scenario_names(self, scenarios):
        names = [s.get("name") for s in scenarios if "name" in s]
        assert len(names) == len(set(names)), "Duplicate scenario names in toxiproxy-config.yml"

    def test_has_postgres_latency_scenario(self, scenarios):
        matches = [s["name"] for s in scenarios if "postgres" in s.get("name", "") and "latency" in s.get("name", "")]
        assert matches, "No postgres latency scenario defined in toxiproxy-config.yml"

    def test_has_redis_scenario(self, scenarios):
        matches = [s["name"] for s in scenarios if "redis" in s.get("name", "")]
        assert matches, "No redis scenario defined in toxiproxy-config.yml"


# ─────────────────────────────────────────────────────────────
# E — Chaos experiments YAML validity
# ─────────────────────────────────────────────────────────────

class TestEChaosExperimentsValidity:

    @pytest.fixture(scope="class")
    def data(self):
        return _load_yaml(_EXPERIMENTS_PATH)

    def test_chaos_experiments_valid_yaml(self, data):
        assert isinstance(data, dict)

    def test_chaos_experiments_has_experiments_key(self, data):
        assert "experiments" in data, "chaos-experiments.yml missing 'experiments' key"

    def test_experiments_is_list(self, data):
        assert isinstance(data["experiments"], list), "'experiments' must be a list"

    def test_at_least_three_experiments(self, data):
        count = len(data.get("experiments", []))
        assert count >= 3, f"Expected at least 3 chaos experiments, got {count}"

    def test_no_duplicate_experiment_names(self, data):
        names = [e.get("name") for e in data.get("experiments", []) if "name" in e]
        assert len(names) == len(set(names)), "Duplicate experiment names in chaos-experiments.yml"


# ─────────────────────────────────────────────────────────────
# F — Experiment schema
# ─────────────────────────────────────────────────────────────

class TestFExperimentSchema:

    @pytest.fixture(scope="class")
    def experiments(self):
        return _load_yaml(_EXPERIMENTS_PATH).get("experiments", [])

    def test_every_experiment_has_name(self, experiments):
        for exp in experiments:
            assert "name" in exp, f"Experiment missing 'name': {exp}"

    def test_every_experiment_has_description(self, experiments):
        for exp in experiments:
            assert "description" in exp, (
                f"Experiment '{exp.get('name', '?')}' missing 'description'"
            )

    def test_every_experiment_has_hypothesis(self, experiments):
        for exp in experiments:
            assert "hypothesis" in exp, (
                f"Experiment '{exp.get('name', '?')}' missing 'hypothesis'"
            )

    def test_every_experiment_has_services_affected(self, experiments):
        for exp in experiments:
            assert "services_affected" in exp, (
                f"Experiment '{exp.get('name', '?')}' missing 'services_affected'"
            )
            assert isinstance(exp["services_affected"], list) and len(exp["services_affected"]) > 0, (
                f"Experiment '{exp.get('name', '?')}' 'services_affected' must be a non-empty list"
            )

    def test_every_experiment_has_blast_radius(self, experiments):
        for exp in experiments:
            assert "blast_radius" in exp, (
                f"Experiment '{exp.get('name', '?')}' missing 'blast_radius'"
            )

    def test_every_experiment_has_duration_seconds(self, experiments):
        for exp in experiments:
            assert "duration_seconds" in exp, (
                f"Experiment '{exp.get('name', '?')}' missing 'duration_seconds'"
            )

    def test_duration_seconds_within_valid_range(self, experiments):
        for exp in experiments:
            duration = exp.get("duration_seconds", 0)
            assert 1 <= duration <= 3600, (
                f"Experiment '{exp.get('name', '?')}' has duration_seconds={duration}, "
                "expected 1–3600"
            )


# ─────────────────────────────────────────────────────────────
# G — Abort conditions and SLA assertions
# ─────────────────────────────────────────────────────────────

class TestGAbortConditionsAndSLA:

    @pytest.fixture(scope="class")
    def experiments(self):
        return _load_yaml(_EXPERIMENTS_PATH).get("experiments", [])

    def test_every_experiment_has_abort_conditions(self, experiments):
        for exp in experiments:
            conds = exp.get("abort_conditions", [])
            assert isinstance(conds, list) and len(conds) > 0, (
                f"Experiment '{exp.get('name', '?')}' has no abort_conditions — "
                "running unbounded chaos is dangerous"
            )

    def test_every_experiment_has_sla_assertions(self, experiments):
        for exp in experiments:
            asserts = exp.get("sla_assertions", [])
            assert isinstance(asserts, list) and len(asserts) > 0, (
                f"Experiment '{exp.get('name', '?')}' has no sla_assertions — "
                "chaos without pass/fail criteria is not an experiment"
            )

    def test_abort_conditions_reference_valid_metrics(self, experiments):
        for exp in experiments:
            for cond in exp.get("abort_conditions", []):
                metric = cond.get("metric", "")
                assert metric in VALID_ABORT_METRICS, (
                    f"Experiment '{exp.get('name', '?')}' abort_condition references "
                    f"unknown metric '{metric}'"
                )

    def test_sla_assertions_reference_valid_metrics(self, experiments):
        for exp in experiments:
            for assertion in exp.get("sla_assertions", []):
                metric = assertion.get("metric", "")
                assert metric in VALID_SLA_METRICS, (
                    f"Experiment '{exp.get('name', '?')}' sla_assertion references "
                    f"unknown metric '{metric}'"
                )

    def test_postgres_experiment_defined(self, experiments):
        postgres_exps = [e for e in experiments if "postgres" in e.get("name", "")]
        assert postgres_exps, "No postgres-related chaos experiment defined"


# ─────────────────────────────────────────────────────────────
# H — Docker Compose chaos overlay
# ─────────────────────────────────────────────────────────────

class TestHDockerComposeChaosOverlay:

    @pytest.fixture(scope="class")
    def compose(self):
        return _load_yaml(_COMPOSE_CHAOS_PATH)

    def test_chaos_compose_valid_yaml(self, compose):
        assert isinstance(compose, dict)

    def test_chaos_compose_has_services(self, compose):
        assert "services" in compose, "docker-compose.chaos.yml missing 'services' key"

    def test_toxiproxy_service_in_overlay(self, compose):
        services = compose.get("services", {})
        assert "toxiproxy" in services, "toxiproxy service not found in chaos overlay"

    def test_toxiproxy_has_control_plane_port_8474(self, compose):
        toxiproxy = compose.get("services", {}).get("toxiproxy", {})
        ports = [str(p) for p in toxiproxy.get("ports", [])]
        assert any("8474" in p for p in ports), (
            "Toxiproxy service missing port 8474 (REST API / control plane)"
        )
