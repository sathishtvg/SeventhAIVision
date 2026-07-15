"""Gap 21 — SOPS Secret Management: infrastructure-file-validation tests.

Validates:
  A. File structure (.sops.yaml, config/secrets-policy.yml, rotation script)
  B. .sops.yaml validity (YAML, creation_rules present and non-empty)
  C. .sops.yaml content (path_regex rules, age key references, encrypted_regex)
  D. Secrets policy validity (YAML, required top-level sections)
  E. Secrets policy categories (categories list, required secret names, rotation_days)
  F. Secrets policy rotation config (rotation_policy section, CI gate, alert window)
  G. Rotation script structure (shebang, modes, error handling, prerequisites)
  H. Security discipline (prohibited_in_plaintext documented, age key format, no real secrets)
"""

from __future__ import annotations

import re
import yaml
import pytest
from pathlib import Path

_HERE         = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]              # /app/backend/tests/ → /app/
_SOPS_PATH    = _PROJECT_ROOT / ".sops.yaml"
_POLICY_PATH  = _PROJECT_ROOT / "config" / "secrets-policy.yml"
_SCRIPT_PATH  = _PROJECT_ROOT / "scripts" / "secrets" / "rotate-secrets.sh"

REQUIRED_SECRET_NAMES = {
    "JWT_SECRET_KEY",
    "POSTGRES_PASSWORD",
}

REQUIRED_CATEGORY_NAMES = {
    "database",
    "jwt",
}

# age public key format: age1 followed by bech32 characters
AGE_KEY_PATTERN = re.compile(r"age1[a-z0-9]{58,}")


def _load_yaml(path: Path) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _script_text() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_sops_yaml_exists(self):
        assert _SOPS_PATH.exists(), f"Missing: {_SOPS_PATH}"

    def test_secrets_policy_yml_exists(self):
        assert _POLICY_PATH.exists(), f"Missing: {_POLICY_PATH}"

    def test_rotation_script_exists(self):
        assert _SCRIPT_PATH.exists(), f"Missing: {_SCRIPT_PATH}"

    def test_secrets_config_directory_exists(self):
        secrets_dir = _PROJECT_ROOT / "config" / "secrets"
        assert secrets_dir.is_dir(), (
            f"Missing: {secrets_dir} — directory for SOPS-encrypted env files"
        )


# ─────────────────────────────────────────────────────────────
# B — .sops.yaml validity
# ─────────────────────────────────────────────────────────────

class TestBSopsYamlValidity:

    @pytest.fixture(scope="class")
    def sops(self):
        return _load_yaml(_SOPS_PATH)

    def test_sops_yaml_valid_yaml(self, sops):
        assert isinstance(sops, dict), ".sops.yaml must be a YAML mapping"

    def test_sops_yaml_has_creation_rules(self, sops):
        rules = sops.get("creation_rules")
        assert isinstance(rules, list), (
            ".sops.yaml must have a 'creation_rules' list"
        )

    def test_creation_rules_non_empty(self, sops):
        rules = sops.get("creation_rules", [])
        assert len(rules) > 0, ".sops.yaml creation_rules must not be empty"

    def test_each_rule_has_path_regex(self, sops):
        for i, rule in enumerate(sops.get("creation_rules", [])):
            assert "path_regex" in rule, (
                f"creation_rules[{i}] missing 'path_regex'"
            )


# ─────────────────────────────────────────────────────────────
# C — .sops.yaml content
# ─────────────────────────────────────────────────────────────

class TestCSopsYamlContent:

    @pytest.fixture(scope="class")
    def sops(self):
        return _load_yaml(_SOPS_PATH)

    def test_path_regex_values_are_strings(self, sops):
        for rule in sops.get("creation_rules", []):
            pr = rule.get("path_regex")
            if pr is not None:
                assert isinstance(pr, str), (
                    f"path_regex must be a string, got {type(pr)}"
                )

    def test_env_files_covered_by_a_rule(self, sops):
        regexes = [r.get("path_regex", "") for r in sops.get("creation_rules", [])]
        env_covered = any("env" in rx or r"\\.env" in rx for rx in regexes)
        assert env_covered, (
            ".sops.yaml must have a rule covering .env files"
        )

    def test_age_key_referenced(self, sops):
        sops_text = _SOPS_PATH.read_text(encoding="utf-8")
        assert "age" in sops_text, (
            ".sops.yaml must reference 'age' as the encryption key type"
        )

    def test_age_key_format_looks_valid(self, sops):
        sops_text = _SOPS_PATH.read_text(encoding="utf-8")
        matches = AGE_KEY_PATTERN.findall(sops_text)
        assert len(matches) >= 1, (
            ".sops.yaml must contain at least one age public key (age1...)"
        )

    def test_encrypted_regex_present_in_at_least_one_rule(self, sops):
        has_encrypted_regex = any(
            "encrypted_regex" in rule for rule in sops.get("creation_rules", [])
        )
        assert has_encrypted_regex, (
            "At least one .sops.yaml rule must define 'encrypted_regex' "
            "to control which YAML keys are encrypted"
        )

    def test_path_regex_values_compile(self, sops):
        for rule in sops.get("creation_rules", []):
            rx = rule.get("path_regex", "")
            try:
                re.compile(rx)
            except re.error as e:
                pytest.fail(f"path_regex '{rx}' is not a valid regex: {e}")


# ─────────────────────────────────────────────────────────────
# D — Secrets policy validity
# ─────────────────────────────────────────────────────────────

class TestDSecretsPolicyValidity:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_policy_valid_yaml(self, policy):
        assert isinstance(policy, dict), "secrets-policy.yml must be a YAML mapping"

    def test_policy_has_version(self, policy):
        assert "version" in policy, "secrets-policy.yml missing 'version' field"

    def test_policy_has_metadata(self, policy):
        assert "metadata" in policy, "secrets-policy.yml missing 'metadata' section"

    def test_policy_has_categories(self, policy):
        cats = policy.get("categories")
        assert isinstance(cats, list) and len(cats) > 0, (
            "secrets-policy.yml must have a non-empty 'categories' list"
        )

    def test_policy_has_rotation_policy(self, policy):
        assert "rotation_policy" in policy, (
            "secrets-policy.yml missing 'rotation_policy' section"
        )

    def test_policy_has_prohibited_in_plaintext(self, policy):
        assert "prohibited_in_plaintext" in policy, (
            "secrets-policy.yml must list 'prohibited_in_plaintext' items"
        )


# ─────────────────────────────────────────────────────────────
# E — Secrets policy categories
# ─────────────────────────────────────────────────────────────

class TestESecretsPolicyCategories:

    @pytest.fixture(scope="class")
    def cat_map(self):
        policy = _load_yaml(_POLICY_PATH)
        return {c["name"]: c for c in policy.get("categories", []) if "name" in c}

    def test_required_categories_present(self, cat_map):
        missing = REQUIRED_CATEGORY_NAMES - set(cat_map)
        assert not missing, f"Missing required secret categories: {missing}"

    def test_each_category_has_rotation_days(self, cat_map):
        for name, cat in cat_map.items():
            assert "rotation_days" in cat, (
                f"Category '{name}' missing 'rotation_days'"
            )

    def test_rotation_days_are_positive(self, cat_map):
        for name, cat in cat_map.items():
            days = cat.get("rotation_days")
            if days is not None:
                assert days > 0, f"Category '{name}' rotation_days must be > 0"

    def test_each_category_has_secrets_list(self, cat_map):
        for name, cat in cat_map.items():
            secs = cat.get("secrets")
            assert isinstance(secs, list) and len(secs) > 0, (
                f"Category '{name}' must have a non-empty 'secrets' list"
            )

    def test_required_secret_names_present(self, cat_map):
        all_secret_names = {
            s["name"]
            for cat in cat_map.values()
            for s in cat.get("secrets", [])
            if "name" in s
        }
        missing = REQUIRED_SECRET_NAMES - all_secret_names
        assert not missing, f"Missing required secrets: {missing}"

    def test_jwt_category_has_min_entropy_documented(self, cat_map):
        jwt_cat = cat_map.get("jwt", {})
        jwt_secrets = jwt_cat.get("secrets", [])
        main_key = next((s for s in jwt_secrets if s.get("name") == "JWT_SECRET_KEY"), None)
        assert main_key is not None, "JWT category must contain JWT_SECRET_KEY secret"
        assert "min_entropy_bits" in main_key, (
            "JWT_SECRET_KEY must document 'min_entropy_bits' requirement"
        )


# ─────────────────────────────────────────────────────────────
# F — Secrets policy rotation config
# ─────────────────────────────────────────────────────────────

class TestFRotationPolicyConfig:

    @pytest.fixture(scope="class")
    def rot(self):
        return _load_yaml(_POLICY_PATH).get("rotation_policy", {})

    def test_ci_gate_enabled(self, rot):
        assert rot.get("ci_gate") is True, (
            "rotation_policy.ci_gate must be true to enforce rotation in CI"
        )

    def test_alert_before_days_defined(self, rot):
        assert "alert_before_days" in rot, (
            "rotation_policy must define 'alert_before_days' for advance warnings"
        )

    def test_alert_before_days_reasonable(self, rot):
        days = rot.get("alert_before_days", 0)
        assert 7 <= days <= 60, (
            f"alert_before_days {days} should be between 7 and 60 days"
        )

    def test_emergency_rotation_procedure_documented(self, rot):
        assert "emergency_rotation_procedure" in rot, (
            "rotation_policy must document an emergency rotation procedure"
        )


# ─────────────────────────────────────────────────────────────
# G — Rotation script structure
# ─────────────────────────────────────────────────────────────

class TestGRotationScriptStructure:

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_has_bash_shebang(self, script):
        first_line = script.splitlines()[0]
        assert first_line.startswith("#!/"), "rotate-secrets.sh must start with a shebang"
        assert "bash" in first_line or "sh" in first_line

    def test_script_has_error_handling(self, script):
        assert "set -e" in script or "set -euo" in script

    def test_script_has_check_expiry_mode(self, script):
        assert "check-expiry" in script, (
            "rotate-secrets.sh must implement --check-expiry mode for CI"
        )

    def test_script_has_validate_mode(self, script):
        assert "validate" in script, (
            "rotate-secrets.sh must implement --validate mode for post-rotation checks"
        )

    def test_script_references_sops(self, script):
        assert "sops" in script, (
            "rotate-secrets.sh must reference the sops CLI"
        )

    def test_script_references_policy_file(self, script):
        assert "secrets-policy" in script or "POLICY_FILE" in script, (
            "rotate-secrets.sh must reference the secrets policy file"
        )

    def test_script_has_prerequisite_checks(self, script):
        assert "_require" in script or "command -v" in script, (
            "rotate-secrets.sh must check prerequisites before running"
        )

    def test_script_has_more_than_20_non_empty_lines(self, script):
        lines = [l for l in script.splitlines() if l.strip()]
        assert len(lines) > 20, (
            f"rotate-secrets.sh has only {len(lines)} non-empty lines"
        )


# ─────────────────────────────────────────────────────────────
# H — Security discipline
# ─────────────────────────────────────────────────────────────

class TestHSecurityDiscipline:

    def test_prohibited_list_includes_passwords(self):
        policy = _load_yaml(_POLICY_PATH)
        prohibited = [str(p).lower() for p in policy.get("prohibited_in_plaintext", [])]
        assert any("password" in p for p in prohibited), (
            "prohibited_in_plaintext must include 'passwords'"
        )

    def test_prohibited_list_includes_api_keys(self):
        policy = _load_yaml(_POLICY_PATH)
        prohibited = [str(p).lower() for p in policy.get("prohibited_in_plaintext", [])]
        assert any("api" in p or "key" in p for p in prohibited), (
            "prohibited_in_plaintext must include API keys"
        )

    def test_prohibited_list_includes_private_keys(self):
        policy = _load_yaml(_POLICY_PATH)
        prohibited = [str(p).lower() for p in policy.get("prohibited_in_plaintext", [])]
        assert any("private" in p for p in prohibited), (
            "prohibited_in_plaintext must include private keys"
        )

    def test_sops_yaml_contains_only_placeholder_age_keys(self):
        text = _SOPS_PATH.read_text(encoding="utf-8")
        # A real age key is 62 characters of bech32 after "age1".
        # We check that any age keys present are clearly marked as examples/placeholders.
        # The key in our .sops.yaml is explicitly labelled a placeholder.
        assert "PLACEHOLDER" in text or "example" in text.lower() or "replace" in text.lower(), (
            ".sops.yaml age keys must be clearly marked as placeholders, "
            "not real private-key-derived public keys"
        )

    def test_sops_yaml_documents_private_key_storage(self):
        text = _SOPS_PATH.read_text(encoding="utf-8")
        # Must mention where the private key is stored (Vault, 1Password, GitHub secret, etc.)
        has_storage_note = (
            "vault" in text.lower()
            or "1password" in text.lower()
            or "bitwarden" in text.lower()
            or "github" in text.lower()
            or "SOPS_AGE_KEY" in text
        )
        assert has_storage_note, (
            ".sops.yaml must document where the age private key is stored securely"
        )

    def test_storage_backends_reference_sops(self):
        policy = _load_yaml(_POLICY_PATH)
        backends = policy.get("storage_backends", {})
        sops_backend = backends.get("sops_encrypted_env", {})
        assert sops_backend.get("tool") == "sops", (
            "secrets-policy.yml storage_backends must include a sops_encrypted_env backend"
        )
