"""Gap 18 — SLO/SLI (Service Level Objectives): infrastructure-file-validation tests.

Validates:
  A. File structure (observability/slo directory + 3 deliverable files)
  B. SLO definitions validity (YAML, version, metadata, slos list)
  C. SLO definitions content (required SLOs, targets, error budget fields)
  D. Recording rules validity (YAML, groups structure, record entries)
  E. Recording rules content (multi-window coverage, naming convention)
  F. Alert rules validity (YAML, groups structure, alert entries)
  G. Alert rules content (multi-window burn-rate, severity labels, slo label)
  H. Prometheus config integration (rule_files reference, evaluation_interval)
"""

from __future__ import annotations

import yaml
import pytest
from pathlib import Path

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]          # /app/backend/tests/ → /app/
_OBS_DIR      = _PROJECT_ROOT / "observability"
_SLO_DIR      = _OBS_DIR / "slo"
_DEFS_PATH    = _SLO_DIR / "slo-definitions.yml"
_REC_PATH     = _SLO_DIR / "recording-rules.yml"
_ALERT_PATH   = _SLO_DIR / "alert-rules.yml"
_PROM_PATH    = _OBS_DIR / "prometheus.yml"

REQUIRED_SLO_NAMES = {
    "api_availability",
    "api_p95_latency",
    "ai_worker_processing",
}

VALID_SLI_TYPES = {"availability", "latency"}


def _load(path: Path) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_slo_directory_exists(self):
        assert _SLO_DIR.is_dir(), f"Missing: {_SLO_DIR}"

    def test_slo_definitions_yml_exists(self):
        assert _DEFS_PATH.exists(), f"Missing: {_DEFS_PATH}"

    def test_recording_rules_yml_exists(self):
        assert _REC_PATH.exists(), f"Missing: {_REC_PATH}"

    def test_alert_rules_yml_exists(self):
        assert _ALERT_PATH.exists(), f"Missing: {_ALERT_PATH}"


# ─────────────────────────────────────────────────────────────
# B — SLO definitions validity
# ─────────────────────────────────────────────────────────────

class TestBSloDefinitionsValidity:

    @pytest.fixture(scope="class")
    def defs(self):
        return _load(_DEFS_PATH)

    def test_slo_definitions_valid_yaml(self, defs):
        assert isinstance(defs, dict), "slo-definitions.yml must be a YAML mapping"

    def test_slo_definitions_has_version(self, defs):
        assert "version" in defs, "slo-definitions.yml missing 'version' field"

    def test_slo_definitions_has_metadata(self, defs):
        assert "metadata" in defs, "slo-definitions.yml missing 'metadata' section"

    def test_slo_definitions_has_slos_list(self, defs):
        slos = defs.get("slos")
        assert isinstance(slos, list) and len(slos) > 0, (
            "slo-definitions.yml 'slos' must be a non-empty list"
        )

    def test_metadata_has_product(self, defs):
        meta = defs.get("metadata", {})
        assert "product" in meta, "metadata section missing 'product' field"


# ─────────────────────────────────────────────────────────────
# C — SLO definitions content
# ─────────────────────────────────────────────────────────────

class TestCSloDefinitionsContent:

    @pytest.fixture(scope="class")
    def slos(self):
        return _load(_DEFS_PATH).get("slos", [])

    @pytest.fixture(scope="class")
    def slo_map(self, slos):
        return {s["name"]: s for s in slos if "name" in s}

    def test_required_slos_present(self, slo_map):
        missing = REQUIRED_SLO_NAMES - set(slo_map)
        assert not missing, f"Missing required SLOs: {missing}"

    def test_each_slo_has_target(self, slos):
        for slo in slos:
            assert "target" in slo, f"SLO '{slo.get('name')}' missing 'target'"

    def test_slo_targets_are_valid_fractions(self, slos):
        for slo in slos:
            t = slo.get("target")
            if t is not None:
                assert 0 < t < 1, (
                    f"SLO '{slo.get('name')}' target {t} must be between 0 and 1 exclusive"
                )

    def test_each_slo_has_sli_section(self, slos):
        for slo in slos:
            assert "sli" in slo, f"SLO '{slo.get('name')}' missing 'sli' section"

    def test_sli_types_are_known(self, slos):
        for slo in slos:
            sli_type = slo.get("sli", {}).get("type")
            if sli_type is not None:
                assert sli_type in VALID_SLI_TYPES, (
                    f"SLO '{slo.get('name')}' has unknown SLI type '{sli_type}'"
                )

    def test_api_availability_target_is_high(self, slo_map):
        api = slo_map.get("api_availability", {})
        target = api.get("target", 0)
        assert target >= 0.99, (
            f"api_availability target {target} should be >= 0.99 for production"
        )

    def test_each_slo_has_error_budget(self, slos):
        for slo in slos:
            has_budget = "error_budget" in slo
            assert has_budget, (
                f"SLO '{slo.get('name')}' missing 'error_budget' section"
            )


# ─────────────────────────────────────────────────────────────
# D — Recording rules validity
# ─────────────────────────────────────────────────────────────

class TestDRecordingRulesValidity:

    @pytest.fixture(scope="class")
    def rules(self):
        return _load(_REC_PATH)

    def test_recording_rules_valid_yaml(self, rules):
        assert isinstance(rules, dict), "recording-rules.yml must be a YAML mapping"

    def test_recording_rules_has_groups(self, rules):
        groups = rules.get("groups")
        assert isinstance(groups, list) and len(groups) > 0, (
            "recording-rules.yml must have a non-empty 'groups' list"
        )

    def test_each_group_has_name(self, rules):
        for grp in rules.get("groups", []):
            assert "name" in grp, f"Recording rule group missing 'name': {grp}"

    def test_each_group_has_rules(self, rules):
        for grp in rules.get("groups", []):
            assert "rules" in grp and len(grp["rules"]) > 0, (
                f"Recording rule group '{grp.get('name')}' has no 'rules'"
            )

    def test_each_record_rule_has_expr(self, rules):
        for grp in rules.get("groups", []):
            for rule in grp.get("rules", []):
                if "record" in rule:
                    assert "expr" in rule, (
                        f"Recording rule '{rule.get('record')}' missing 'expr'"
                    )


# ─────────────────────────────────────────────────────────────
# E — Recording rules content
# ─────────────────────────────────────────────────────────────

class TestERecordingRulesContent:

    @pytest.fixture(scope="class")
    def all_record_names(self):
        rules_doc = _load(_REC_PATH)
        names = []
        for grp in rules_doc.get("groups", []):
            for rule in grp.get("rules", []):
                if "record" in rule:
                    names.append(rule["record"])
        return names

    def test_record_names_follow_slo_prefix_convention(self, all_record_names):
        non_conforming = [n for n in all_record_names if not n.startswith("slo:")]
        assert not non_conforming, (
            f"Record names must start with 'slo:' — non-conforming: {non_conforming}"
        )

    def test_short_window_5m_present(self, all_record_names):
        has_5m = any(":5m" in n for n in all_record_names)
        assert has_5m, "Recording rules must include at least one 5-minute window"

    def test_long_window_1h_or_more_present(self, all_record_names):
        has_long = any(":1h" in n or ":6h" in n or ":24h" in n for n in all_record_names)
        assert has_long, "Recording rules must include at least one window of 1h or longer"

    def test_api_success_rate_recorded(self, all_record_names):
        api_rules = [n for n in all_record_names if "api_request_success_rate" in n]
        assert len(api_rules) >= 2, (
            f"Expected at least 2 api_request_success_rate recording windows, got {len(api_rules)}"
        )

    def test_ai_worker_rate_recorded(self, all_record_names):
        ai_rules = [n for n in all_record_names if "ai_worker_success_rate" in n]
        assert len(ai_rules) >= 2, (
            f"Expected at least 2 ai_worker_success_rate recording windows, got {len(ai_rules)}"
        )

    def test_error_budget_remaining_recorded(self, all_record_names):
        budget_rules = [n for n in all_record_names if "error_budget_remaining" in n]
        assert len(budget_rules) >= 1, (
            "At least one error_budget_remaining recording rule must exist"
        )


# ─────────────────────────────────────────────────────────────
# F — Alert rules validity
# ─────────────────────────────────────────────────────────────

class TestFAlertRulesValidity:

    @pytest.fixture(scope="class")
    def alerts_doc(self):
        return _load(_ALERT_PATH)

    def test_alert_rules_valid_yaml(self, alerts_doc):
        assert isinstance(alerts_doc, dict), "alert-rules.yml must be a YAML mapping"

    def test_alert_rules_has_groups(self, alerts_doc):
        groups = alerts_doc.get("groups")
        assert isinstance(groups, list) and len(groups) > 0, (
            "alert-rules.yml must have a non-empty 'groups' list"
        )

    def test_each_group_has_alert_rules(self, alerts_doc):
        for grp in alerts_doc.get("groups", []):
            alerts = [r for r in grp.get("rules", []) if "alert" in r]
            assert len(alerts) > 0, (
                f"Alert group '{grp.get('name')}' has no alert rules"
            )

    def test_each_alert_has_expr(self, alerts_doc):
        for grp in alerts_doc.get("groups", []):
            for rule in grp.get("rules", []):
                if "alert" in rule:
                    assert "expr" in rule, (
                        f"Alert '{rule.get('alert')}' missing 'expr'"
                    )

    def test_each_alert_has_labels(self, alerts_doc):
        for grp in alerts_doc.get("groups", []):
            for rule in grp.get("rules", []):
                if "alert" in rule:
                    assert "labels" in rule, (
                        f"Alert '{rule.get('alert')}' missing 'labels'"
                    )

    def test_each_alert_has_annotations(self, alerts_doc):
        for grp in alerts_doc.get("groups", []):
            for rule in grp.get("rules", []):
                if "alert" in rule:
                    assert "annotations" in rule, (
                        f"Alert '{rule.get('alert')}' missing 'annotations'"
                    )


# ─────────────────────────────────────────────────────────────
# G — Alert rules content
# ─────────────────────────────────────────────────────────────

class TestGAlertRulesContent:

    @pytest.fixture(scope="class")
    def all_alerts(self):
        doc = _load(_ALERT_PATH)
        result = []
        for grp in doc.get("groups", []):
            for rule in grp.get("rules", []):
                if "alert" in rule:
                    result.append(rule)
        return result

    def test_severity_label_present_on_all_alerts(self, all_alerts):
        missing = [a["alert"] for a in all_alerts if "severity" not in a.get("labels", {})]
        assert not missing, f"Alerts missing 'severity' label: {missing}"

    def test_severity_values_are_known(self, all_alerts):
        valid = {"info", "warning", "critical"}
        for alert in all_alerts:
            sev = alert.get("labels", {}).get("severity")
            if sev is not None:
                assert sev in valid, (
                    f"Alert '{alert['alert']}' has unknown severity '{sev}'"
                )

    def test_slo_label_present_on_burn_rate_alerts(self, all_alerts):
        burn_alerts = [a for a in all_alerts if "Burn" in a["alert"]]
        missing = [a["alert"] for a in burn_alerts if "slo" not in a.get("labels", {})]
        assert not missing, f"Burn-rate alerts missing 'slo' label: {missing}"

    def test_fast_burn_alerts_exist(self, all_alerts):
        fast_alerts = [a for a in all_alerts if "FastBurn" in a["alert"]]
        assert len(fast_alerts) >= 2, (
            f"Expected at least 2 fast-burn alerts, got {len(fast_alerts)}"
        )

    def test_medium_burn_alerts_exist(self, all_alerts):
        medium_alerts = [a for a in all_alerts if "MediumBurn" in a["alert"]]
        assert len(medium_alerts) >= 2, (
            f"Expected at least 2 medium-burn alerts, got {len(medium_alerts)}"
        )

    def test_fast_burn_uses_two_window_conjunctions(self, all_alerts):
        fast_alerts = [a for a in all_alerts if "FastBurn" in a["alert"]]
        for alert in fast_alerts:
            expr = alert.get("expr", "")
            assert "\nand\n" in expr or " and " in expr, (
                f"Fast-burn alert '{alert['alert']}' must use two-window conjunction (short AND long)"
            )

    def test_api_availability_alert_exists(self, all_alerts):
        api_alerts = [a for a in all_alerts if "ApiAvailability" in a["alert"]]
        assert len(api_alerts) >= 1, "No API availability alerts found"

    def test_ai_worker_alert_exists(self, all_alerts):
        ai_alerts = [a for a in all_alerts if "AiWorker" in a["alert"]]
        assert len(ai_alerts) >= 1, "No AI worker alerts found"

    def test_annotations_have_summary(self, all_alerts):
        missing = [a["alert"] for a in all_alerts if "summary" not in a.get("annotations", {})]
        assert not missing, f"Alerts missing 'annotations.summary': {missing}"


# ─────────────────────────────────────────────────────────────
# H — Prometheus config integration
# ─────────────────────────────────────────────────────────────

class TestHPrometheusIntegration:

    @pytest.fixture(scope="class")
    def prom(self):
        return _load(_PROM_PATH)

    def test_prometheus_yml_valid_yaml(self, prom):
        assert isinstance(prom, dict), "prometheus.yml must be a YAML mapping"

    def test_prometheus_has_rule_files(self, prom):
        rule_files = prom.get("rule_files")
        assert isinstance(rule_files, list) and len(rule_files) > 0, (
            "prometheus.yml must declare 'rule_files' to load SLO rules"
        )

    def test_recording_rules_referenced_in_prometheus(self, prom):
        rule_files = prom.get("rule_files", [])
        has_rec = any("recording" in rf for rf in rule_files)
        assert has_rec, (
            "prometheus.yml rule_files must reference recording-rules.yml"
        )

    def test_alert_rules_referenced_in_prometheus(self, prom):
        rule_files = prom.get("rule_files", [])
        has_alert = any("alert" in rf for rf in rule_files)
        assert has_alert, (
            "prometheus.yml rule_files must reference alert-rules.yml"
        )

    def test_prometheus_has_evaluation_interval(self, prom):
        global_cfg = prom.get("global", {})
        assert "evaluation_interval" in global_cfg, (
            "prometheus.yml global section must set evaluation_interval for rule evaluation"
        )
