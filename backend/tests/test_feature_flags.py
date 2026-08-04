"""Gap 15 — Feature Flags: infrastructure-file-validation tests.

Validates:
  A. File structure (module + JSON catalog + config directory)
  B. JSON catalog validity (parseable, version, flags array, count, no duplicates)
  C. JSON flag schema (every flag has name/type/default/description, type is valid)
  D. Python module syntax (parseable, required names present)
  E. Module import and registry (FLAG_DEFINITIONS content, callables)
  F. Flag resolution logic (defaults, env-var overrides, unknown flags, tenant param)
  G. Platform-specific flags (key platform features are defined)
  H. JSON-Python catalog consistency (counts match, names align, defaults match)
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests._repo import REPO_ROOT, requires_repo_tree

# Skips the whole module when the repository tree is absent (e.g. inside the
# api image, which ships only backend/). See tests/_repo.py for why failing
# would be the wrong signal here.
pytestmark = requires_repo_tree


_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]  # /app/backend/tests/ -> /app/backend -> /app
_MODULE_PATH = _PROJECT_ROOT / "backend" / "app" / "core" / "feature_flags.py"
_CATALOG_PATH = _PROJECT_ROOT / "config" / "feature_flags.json"
_CONFIG_DIR = _PROJECT_ROOT / "config"

VALID_FLAG_TYPES = {"boolean", "string", "number", "json"}


def _import_feature_flags():
    """Dynamically import feature_flags.py without requiring it on sys.path."""
    spec = importlib.util.spec_from_file_location("feature_flags", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_catalog() -> dict:
    with open(_CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_feature_flags_module_exists(self):
        assert _MODULE_PATH.exists(), f"Missing: {_MODULE_PATH}"

    def test_feature_flags_json_catalog_exists(self):
        assert _CATALOG_PATH.exists(), f"Missing: {_CATALOG_PATH}"

    def test_config_directory_exists(self):
        assert _CONFIG_DIR.is_dir(), f"config/ directory not found at {_CONFIG_DIR}"


# ─────────────────────────────────────────────────────────────
# B — JSON catalog validity
# ─────────────────────────────────────────────────────────────

class TestBJsonCatalogValidity:

    def test_json_catalog_valid_json(self):
        content = _CATALOG_PATH.read_text(encoding="utf-8")
        data = json.loads(content)
        assert isinstance(data, dict)

    def test_json_catalog_has_version(self):
        data = _load_catalog()
        assert "version" in data, "catalog missing 'version' field"
        assert data["version"], "'version' must be non-empty"

    def test_json_catalog_has_flags_array(self):
        data = _load_catalog()
        assert "flags" in data, "catalog missing 'flags' field"

    def test_json_catalog_flags_is_list(self):
        data = _load_catalog()
        assert isinstance(data["flags"], list), "'flags' must be a list"

    def test_json_catalog_minimum_flag_count(self):
        data = _load_catalog()
        count = len(data["flags"])
        assert count >= 10, f"Expected at least 10 flags, got {count}"

    def test_json_catalog_no_duplicate_names(self):
        data = _load_catalog()
        names = [f["name"] for f in data["flags"] if "name" in f]
        assert len(names) == len(set(names)), "Duplicate flag names found in JSON catalog"


# ─────────────────────────────────────────────────────────────
# C — JSON flag schema
# ─────────────────────────────────────────────────────────────

class TestCJsonFlagSchema:

    @pytest.fixture(scope="class")
    def flags(self):
        return _load_catalog()["flags"]

    def test_every_json_flag_has_name(self, flags):
        for flag in flags:
            assert "name" in flag, f"Flag entry missing 'name': {flag}"

    def test_every_json_flag_has_type(self, flags):
        for flag in flags:
            assert "type" in flag, f"Flag '{flag.get('name', '?')}' missing 'type'"

    def test_every_json_flag_type_is_valid(self, flags):
        for flag in flags:
            t = flag.get("type", "")
            assert t in VALID_FLAG_TYPES, (
                f"Flag '{flag.get('name', '?')}' has invalid type '{t}'. "
                f"Must be one of {VALID_FLAG_TYPES}"
            )

    def test_every_json_flag_has_default(self, flags):
        for flag in flags:
            assert "default" in flag, f"Flag '{flag.get('name', '?')}' missing 'default'"

    def test_every_json_flag_has_description(self, flags):
        for flag in flags:
            assert "description" in flag, f"Flag '{flag.get('name', '?')}' missing 'description'"
            assert len(flag["description"].strip()) > 0, (
                f"Flag '{flag.get('name', '?')}' has empty description"
            )


# ─────────────────────────────────────────────────────────────
# D — Python module syntax
# ─────────────────────────────────────────────────────────────

class TestDPythonModuleSyntax:

    @pytest.fixture(scope="class")
    def source(self):
        return _MODULE_PATH.read_text(encoding="utf-8")

    def test_feature_flags_module_valid_python(self, source):
        ast.parse(source)  # raises SyntaxError if invalid

    def test_flag_definitions_dict_in_source(self, source):
        assert "FLAG_DEFINITIONS" in source

    def test_is_enabled_function_in_source(self, source):
        assert "def is_enabled" in source

    def test_get_flag_value_function_in_source(self, source):
        assert "def get_flag_value" in source


# ─────────────────────────────────────────────────────────────
# E — Module import and registry
# ─────────────────────────────────────────────────────────────

class TestEModuleImport:

    @pytest.fixture(scope="class")
    def mod(self):
        return _import_feature_flags()

    def test_module_imports_cleanly(self, mod):
        assert mod is not None

    def test_flag_definitions_is_dict(self, mod):
        assert isinstance(mod.FLAG_DEFINITIONS, dict)

    def test_flag_definitions_is_non_empty(self, mod):
        assert len(mod.FLAG_DEFINITIONS) > 0

    def test_all_flag_defs_have_type_key(self, mod):
        for name, defn in mod.FLAG_DEFINITIONS.items():
            assert "type" in defn, f"Flag '{name}' missing 'type' in FLAG_DEFINITIONS"

    def test_all_flag_defs_have_default_key(self, mod):
        for name, defn in mod.FLAG_DEFINITIONS.items():
            assert "default" in defn, f"Flag '{name}' missing 'default' in FLAG_DEFINITIONS"

    def test_all_flag_defs_have_description_key(self, mod):
        for name, defn in mod.FLAG_DEFINITIONS.items():
            assert "description" in defn, f"Flag '{name}' missing 'description' in FLAG_DEFINITIONS"

    def test_is_enabled_callable(self, mod):
        assert callable(mod.is_enabled)

    def test_get_flag_value_callable(self, mod):
        assert callable(mod.get_flag_value)


# ─────────────────────────────────────────────────────────────
# F — Flag resolution logic
# ─────────────────────────────────────────────────────────────

class TestFFlagResolution:

    @pytest.fixture(autouse=True)
    def clear_feature_env_vars(self, monkeypatch):
        for key in list(os.environ):
            if key.startswith("FEATURE_"):
                monkeypatch.delenv(key, raising=False)

    @pytest.fixture
    def mod(self):
        return _import_feature_flags()

    def test_is_enabled_returns_true_for_known_enabled_flag(self, mod):
        assert mod.is_enabled("live_wall") is True

    def test_is_enabled_returns_false_for_known_disabled_flag(self, mod):
        assert mod.is_enabled("gpu_acceleration") is False

    def test_is_enabled_returns_false_for_unknown_flag(self, mod):
        assert mod.is_enabled("nonexistent_flag_xyz_abc") is False

    def test_get_flag_value_returns_flag_default(self, mod):
        val = mod.get_flag_value("max_webhook_endpoints_per_tenant")
        assert val == 20

    def test_get_flag_value_unknown_flag_returns_caller_default(self, mod):
        val = mod.get_flag_value("nonexistent_xyz", default="fallback_value")
        assert val == "fallback_value"

    def test_env_var_override_enables_disabled_flag(self, mod, monkeypatch):
        monkeypatch.setenv("FEATURE_GPU_ACCELERATION", "true")
        assert mod.is_enabled("gpu_acceleration") is True

    def test_env_var_override_disables_enabled_flag(self, mod, monkeypatch):
        monkeypatch.setenv("FEATURE_LIVE_WALL", "false")
        assert mod.is_enabled("live_wall") is False

    def test_is_enabled_accepts_tenant_id_param(self, mod):
        result = mod.is_enabled("live_wall", tenant_id="tenant-uuid-placeholder")
        assert isinstance(result, bool)


# ─────────────────────────────────────────────────────────────
# G — Platform-specific flags
# ─────────────────────────────────────────────────────────────

class TestGPlatformFlags:

    @pytest.fixture(scope="class")
    def mod(self):
        return _import_feature_flags()

    def test_live_wall_flag_defined(self, mod):
        assert "live_wall" in mod.FLAG_DEFINITIONS

    def test_electron_desktop_flag_defined(self, mod):
        assert "electron_desktop" in mod.FLAG_DEFINITIONS

    def test_face_enrollment_ui_flag_defined(self, mod):
        assert "face_enrollment_ui" in mod.FLAG_DEFINITIONS

    def test_scim_provisioning_flag_defined(self, mod):
        assert "scim_provisioning" in mod.FLAG_DEFINITIONS

    def test_webhooks_flag_defined(self, mod):
        assert "webhooks" in mod.FLAG_DEFINITIONS

    def test_opentelemetry_tracing_flag_defined(self, mod):
        assert "opentelemetry_tracing" in mod.FLAG_DEFINITIONS

    def test_gpu_acceleration_flag_defined(self, mod):
        assert "gpu_acceleration" in mod.FLAG_DEFINITIONS


# ─────────────────────────────────────────────────────────────
# H — JSON-Python catalog consistency
# ─────────────────────────────────────────────────────────────

class TestHCatalogConsistency:

    @pytest.fixture(scope="class")
    def mod(self):
        return _import_feature_flags()

    def test_json_and_python_flag_counts_match(self, mod):
        catalog = _load_catalog()
        json_count = len(catalog["flags"])
        py_count = len(mod.FLAG_DEFINITIONS)
        assert json_count == py_count, (
            f"JSON catalog has {json_count} flags but Python FLAG_DEFINITIONS has {py_count}"
        )

    def test_json_flag_names_are_subset_of_python(self, mod):
        catalog = _load_catalog()
        json_names = {f["name"] for f in catalog["flags"]}
        py_names = set(mod.FLAG_DEFINITIONS.keys())
        missing = json_names - py_names
        assert not missing, f"JSON flag names not in Python FLAG_DEFINITIONS: {missing}"

    def test_json_boolean_defaults_match_python_defaults(self, mod):
        catalog = _load_catalog()
        mismatches = []
        for flag in catalog["flags"]:
            name = flag.get("name")
            if not name or flag.get("type") != "boolean":
                continue
            py_defn = mod.FLAG_DEFINITIONS.get(name)
            if py_defn is None:
                continue
            if py_defn["default"] != flag["default"]:
                mismatches.append(
                    f"'{name}': json_default={flag['default']}, python_default={py_defn['default']}"
                )
        assert not mismatches, f"Boolean default value mismatches: {mismatches}"

    def test_all_flags_function_returns_list(self, mod):
        result = mod.all_flags()
        assert isinstance(result, list)
        assert len(result) == len(mod.FLAG_DEFINITIONS)
        for item in result:
            assert "name" in item, "all_flags() entry missing 'name'"
            assert "env_key" in item, "all_flags() entry missing 'env_key'"
            assert item["env_key"].startswith("FEATURE_"), (
                f"env_key '{item['env_key']}' should start with 'FEATURE_'"
            )
