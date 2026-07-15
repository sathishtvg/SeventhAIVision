"""Gap 26 — Database Migration Safety: infrastructure-file-validation tests.

Validates:
  A. File structure (policy, script, alembic.ini, versions directory)
  B. Migration safety policy validity (YAML, required sections, metadata)
  C. Policy content (downgrade rule, chain validation, destructive patterns, CI gate)
  D. Alembic config validity (alembic.ini, script_location, env.py)
  E. Migration chain integrity (no gaps, no duplicates, single root, single head)
  F. Migration safety patterns (every file has upgrade+downgrade, revision vars,
     naming convention, destructive ops annotated)
  G. Safety check script structure (shebang, modes, error handling, python3)
  H. Integration discipline (naming, revision prefix, count thresholds, policy
     references match actual paths, no TRUNCATE without justification)
"""

from __future__ import annotations

import configparser
import re
import yaml
import pytest
from pathlib import Path

_HERE          = Path(__file__).parent
_PROJECT_ROOT  = _HERE.parents[1]                                         # /app/
_POLICY_PATH   = _PROJECT_ROOT / "config" / "migration-safety-policy.yml"
_SCRIPT_PATH   = _PROJECT_ROOT / "scripts" / "migrate" / "check-migration-safety.sh"
_ALEMBIC_INI   = _PROJECT_ROOT / "backend" / "alembic.ini"
_VERSIONS_DIR  = _PROJECT_ROOT / "backend" / "alembic" / "versions"
_ENV_PY        = _PROJECT_ROOT / "backend" / "alembic" / "env.py"

_DESTRUCTIVE_PATTERNS = ["DROP TABLE", "DROP COLUMN", "TRUNCATE TABLE"]
_NAMING_RE     = re.compile(r"^\d{4}_[a-z0-9_]+\.py$")

_MIN_MIGRATIONS    = 40       # we have 47; this threshold catches accidental deletion
_EXPECTED_HEAD     = "0057"   # single head as of 2026-07-04


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _script_text() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def _build_revision_map() -> dict[str, dict]:
    """Parse every migration file and return {revision_id: {down_revision, file, text}}."""
    revisions: dict[str, dict] = {}
    for mf in sorted(_VERSIONS_DIR.glob("*.py")):
        text = mf.read_text(encoding="utf-8")
        rev = down_rev = None
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("revision") and "=" in s and rev is None:
                rev = s.split("=", 1)[-1].strip().strip('"').strip("'").strip()
            if s.startswith("down_revision") and "=" in s and down_rev is None:
                val = s.split("=", 1)[-1].strip().strip('"').strip("'").strip()
                down_rev = None if val in ("None", "") else val
        if rev:
            revisions[rev] = {
                "down_revision": down_rev,
                "file": mf.name,
                "text": text,
            }
    return revisions


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_migration_safety_policy_exists(self):
        assert _POLICY_PATH.exists(), f"Missing: {_POLICY_PATH}"

    def test_safety_check_script_exists(self):
        assert _SCRIPT_PATH.exists(), f"Missing: {_SCRIPT_PATH}"

    def test_scripts_migrate_directory_exists(self):
        assert (_PROJECT_ROOT / "scripts" / "migrate").is_dir()

    def test_alembic_ini_exists(self):
        assert _ALEMBIC_INI.exists(), (
            f"Missing alembic.ini — Alembic cannot run without it: {_ALEMBIC_INI}"
        )

    def test_alembic_versions_directory_exists(self):
        assert _VERSIONS_DIR.is_dir(), f"Missing versions directory: {_VERSIONS_DIR}"

    def test_alembic_env_py_exists(self):
        assert _ENV_PY.exists(), (
            f"Missing alembic/env.py — required for database connection: {_ENV_PY}"
        )


# ─────────────────────────────────────────────────────────────
# B — Migration safety policy validity
# ─────────────────────────────────────────────────────────────

class TestBMigrationSafetyPolicyValidity:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_policy_is_valid_yaml(self, policy):
        assert isinstance(policy, dict)

    def test_policy_has_version(self, policy):
        assert "version" in policy

    def test_policy_has_metadata(self, policy):
        assert "metadata" in policy

    def test_policy_has_rules_section(self, policy):
        assert "rules" in policy, "policy missing 'rules' section"

    def test_policy_has_review_gates(self, policy):
        assert "review_gates" in policy, "policy missing 'review_gates' section"

    def test_policy_has_ci_integration(self, policy):
        assert "ci_integration" in policy, "policy missing 'ci_integration' section"

    def test_policy_metadata_references_alembic_tool(self, policy):
        tool = policy.get("metadata", {}).get("tool")
        assert tool == "alembic", f"metadata.tool must be 'alembic'; got '{tool}'"

    def test_policy_metadata_references_migrations_dir(self, policy):
        mdir = policy.get("metadata", {}).get("migrations_dir")
        assert mdir, "metadata must specify migrations_dir"


# ─────────────────────────────────────────────────────────────
# C — Policy content
# ─────────────────────────────────────────────────────────────

class TestCPolicyContent:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_downgrade_required_is_true(self, policy):
        assert policy.get("rules", {}).get("downgrade_required") is True, (
            "rules.downgrade_required must be true — every migration needs a rollback path"
        )

    def test_chain_validation_enforced(self, policy):
        cv = policy.get("rules", {}).get("chain_validation", {})
        assert cv.get("enforce_unbroken_chain") is True, (
            "rules.chain_validation.enforce_unbroken_chain must be true"
        )

    def test_chain_validation_unique_revisions(self, policy):
        cv = policy.get("rules", {}).get("chain_validation", {})
        assert cv.get("enforce_unique_revisions") is True, (
            "rules.chain_validation.enforce_unique_revisions must be true"
        )

    def test_destructive_patterns_require_annotation_defined(self, policy):
        dp = policy.get("rules", {}).get("destructive_patterns", {})
        patterns = dp.get("require_annotation", [])
        assert isinstance(patterns, list) and len(patterns) > 0, (
            "rules.destructive_patterns.require_annotation must be a non-empty list"
        )

    def test_drop_table_in_annotation_required_list(self, policy):
        dp = policy.get("rules", {}).get("destructive_patterns", {})
        patterns = [p.upper() for p in dp.get("require_annotation", [])]
        assert "DROP TABLE" in patterns, (
            "DROP TABLE must be in rules.destructive_patterns.require_annotation"
        )

    def test_drop_column_in_annotation_required_list(self, policy):
        dp = policy.get("rules", {}).get("destructive_patterns", {})
        patterns = [p.upper() for p in dp.get("require_annotation", [])]
        assert "DROP COLUMN" in patterns, (
            "DROP COLUMN must be in rules.destructive_patterns.require_annotation"
        )

    def test_ci_gate_fails_on_error(self, policy):
        ci = policy.get("ci_integration", {})
        assert ci.get("fail_on_error") is True, (
            "ci_integration.fail_on_error must be true — violations must block deployment"
        )

    def test_rollback_procedure_documented(self, policy):
        rb = policy.get("rollback", {})
        assert rb.get("procedure_documented_in"), (
            "rollback.procedure_documented_in must reference the runbook file"
        )


# ─────────────────────────────────────────────────────────────
# D — Alembic config validity
# ─────────────────────────────────────────────────────────────

class TestDAlembicConfigValidity:

    @pytest.fixture(scope="class")
    def ini(self):
        cfg = configparser.ConfigParser()
        cfg.read(str(_ALEMBIC_INI), encoding="utf-8")
        return cfg

    def test_alembic_ini_is_valid_ini(self, ini):
        assert len(ini.sections()) > 0, "alembic.ini has no sections — invalid INI format"

    def test_alembic_ini_has_alembic_section(self, ini):
        assert ini.has_section("alembic"), (
            "alembic.ini must have an [alembic] section"
        )

    def test_alembic_ini_has_script_location(self, ini):
        assert ini.has_option("alembic", "script_location"), (
            "alembic.ini [alembic] section must define script_location"
        )

    def test_alembic_script_location_directory_exists(self, ini):
        loc = ini.get("alembic", "script_location")
        script_dir = (_PROJECT_ROOT / "backend" / loc).resolve()
        assert script_dir.is_dir(), (
            f"alembic.ini script_location '{loc}' does not exist at {script_dir}"
        )

    def test_alembic_env_py_references_database_url(self):
        env_text = _ENV_PY.read_text(encoding="utf-8")
        assert "DATABASE_URL" in env_text or "sqlalchemy.url" in env_text, (
            "alembic/env.py must reference DATABASE_URL or sqlalchemy.url"
        )


# ─────────────────────────────────────────────────────────────
# E — Migration chain integrity
# ─────────────────────────────────────────────────────────────

class TestEMigrationChainIntegrity:

    @pytest.fixture(scope="class")
    def revmap(self):
        return _build_revision_map()

    def test_at_least_minimum_migrations_exist(self, revmap):
        count = len(revmap)
        assert count >= _MIN_MIGRATIONS, (
            f"Expected >= {_MIN_MIGRATIONS} migrations; found {count}"
        )

    def test_no_broken_down_revision_references(self, revmap):
        rev_set = set(revmap.keys())
        broken = [
            f"{info['file']} → '{info['down_revision']}'"
            for rev, info in revmap.items()
            if info["down_revision"] is not None and info["down_revision"] not in rev_set
        ]
        assert not broken, (
            f"{len(broken)} migration(s) reference a non-existent down_revision: {broken[:3]}"
        )

    def test_no_duplicate_revision_ids(self, revmap):
        ids = list(revmap.keys())
        assert len(ids) == len(set(ids)), (
            "Duplicate revision IDs found — the chain would be ambiguous"
        )

    def test_exactly_one_root_migration(self, revmap):
        roots = [rev for rev, info in revmap.items() if info["down_revision"] is None]
        assert len(roots) == 1, (
            f"Expected exactly 1 root migration (down_revision=None); found {len(roots)}: {roots}"
        )

    def test_exactly_one_head_migration(self, revmap):
        rev_set = set(revmap.keys())
        referenced_as_parent = {info["down_revision"] for info in revmap.values() if info["down_revision"]}
        heads = rev_set - referenced_as_parent
        assert len(heads) == 1, (
            f"Expected exactly 1 head migration; found {len(heads)}: {heads} — "
            "multiple heads indicate a forked migration chain"
        )

    def test_head_is_latest_migration(self, revmap):
        rev_set = set(revmap.keys())
        referenced_as_parent = {info["down_revision"] for info in revmap.values() if info["down_revision"]}
        heads = rev_set - referenced_as_parent
        assert _EXPECTED_HEAD in heads, (
            f"Expected head to be '{_EXPECTED_HEAD}'; got {heads}"
        )

    def test_all_migrations_have_upgrade_function(self, revmap):
        missing = [
            info["file"]
            for info in revmap.values()
            if "def upgrade" not in info["text"]
        ]
        assert not missing, (
            f"{len(missing)} migration(s) missing def upgrade(): {missing[:3]}"
        )

    def test_all_migrations_have_downgrade_function(self, revmap):
        missing = [
            info["file"]
            for info in revmap.values()
            if "def downgrade" not in info["text"]
        ]
        assert not missing, (
            f"{len(missing)} migration(s) missing def downgrade(): {missing[:3]} — "
            "every migration must implement a rollback path"
        )


# ─────────────────────────────────────────────────────────────
# F — Migration safety patterns
# ─────────────────────────────────────────────────────────────

class TestFMigrationSafetyPatterns:

    @pytest.fixture(scope="class")
    def revmap(self):
        return _build_revision_map()

    def test_all_migrations_have_revision_variable(self, revmap):
        missing = [
            fname
            for info in revmap.values()
            if "revision" not in (fname := info["file"]) or True
            # Re-check from text
        ]
        # More precise check: scan text for `revision = ` or `revision: str =`
        missing = []
        for info in revmap.values():
            text = info["text"]
            has_rev = any(
                line.strip().startswith("revision") and "=" in line
                for line in text.splitlines()
            )
            if not has_rev:
                missing.append(info["file"])
        assert not missing, (
            f"{len(missing)} migration(s) missing revision variable: {missing[:3]}"
        )

    def test_all_migrations_have_down_revision_variable(self, revmap):
        missing = []
        for info in revmap.values():
            text = info["text"]
            has_dr = any(
                line.strip().startswith("down_revision") and "=" in line
                for line in text.splitlines()
            )
            if not has_dr:
                missing.append(info["file"])
        assert not missing, (
            f"{len(missing)} migration(s) missing down_revision variable: {missing[:3]}"
        )

    def test_migration_files_follow_naming_convention(self):
        bad = [
            f.name
            for f in _VERSIONS_DIR.glob("*.py")
            if not _NAMING_RE.match(f.name)
        ]
        assert not bad, (
            f"{len(bad)} migration file(s) do not follow 4-digit naming convention: {bad[:3]}"
        )

    def test_destructive_ops_in_upgrade_have_docstring_justification(self, revmap):
        violations = []
        for rev, info in revmap.items():
            text = info["text"]
            fname = info["file"]
            lines = text.splitlines()
            in_upgrade = False
            for i, line in enumerate(lines):
                if re.match(r"\s*def upgrade", line):
                    in_upgrade = True
                elif re.match(r"\s*def downgrade", line):
                    in_upgrade = False
                if not in_upgrade:
                    continue
                line_upper = line.upper()
                for pattern in _DESTRUCTIVE_PATTERNS:
                    if pattern in line_upper:
                        # Idempotent IF EXISTS forms are lower risk
                        if "IF EXISTS" in line_upper:
                            continue
                        # Module docstring must explain the destructive operation
                        docstring = text[:800].lower()
                        if (
                            "drop" not in docstring
                            and "legacy" not in docstring
                            and "# destructive" not in text.lower()
                            and "# review" not in text.lower()
                        ):
                            violations.append(f"{fname}: {line.strip()[:80]}")
        assert not violations, (
            f"{len(violations)} unannotated destructive operation(s) found in upgrade(): "
            + "; ".join(violations[:3])
        )

    def test_no_plain_drop_table_in_upgrade_without_annotation(self, revmap):
        bare_drops = []
        for rev, info in revmap.items():
            text  = info["text"]
            lines = text.splitlines()
            in_upgrade = False
            for line in lines:
                if re.match(r"\s*def upgrade", line):
                    in_upgrade = True
                elif re.match(r"\s*def downgrade", line):
                    in_upgrade = False
                if in_upgrade and "DROP TABLE" in line.upper() and "IF EXISTS" not in line.upper():
                    bare_drops.append(f"{info['file']}: {line.strip()[:70]}")
        assert not bare_drops, (
            f"{len(bare_drops)} bare DROP TABLE(s) in upgrade() without IF EXISTS guard: "
            + str(bare_drops[:3])
        )


# ─────────────────────────────────────────────────────────────
# G — Safety check script structure
# ─────────────────────────────────────────────────────────────

class TestGSafetyCheckScriptStructure:

    @pytest.fixture(scope="class")
    def script(self):
        return _script_text()

    def test_script_has_shebang(self, script):
        first_line = script.splitlines()[0]
        assert first_line.startswith("#!/"), "Script must begin with a shebang"
        assert "bash" in first_line or "sh" in first_line

    def test_script_has_set_e(self, script):
        assert "set -e" in script, "Script must use 'set -e' for error handling"

    def test_script_has_check_mode(self, script):
        assert "--check" in script, "Script must support --check mode"

    def test_script_has_chain_mode(self, script):
        assert "--chain" in script, "Script must support --chain mode"

    def test_script_has_list_mode(self, script):
        assert "--list" in script, "Script must support --list mode"

    def test_script_has_dry_run_mode(self, script):
        assert "--dry-run" in script, "Script must support --dry-run mode"

    def test_script_uses_python3(self, script):
        assert "python3" in script, "Script must invoke python3 for validation logic"

    def test_script_reads_policy_file(self, script):
        assert "POLICY_FILE" in script, (
            "Script must read the policy file (via POLICY_FILE env var)"
        )

    def test_script_validates_migrations_directory(self, script):
        assert "MIGRATIONS_DIR" in script or "alembic/versions" in script, (
            "Script must reference the migrations directory"
        )

    def test_script_checks_downgrade_presence(self, script):
        assert "downgrade" in script, (
            "Script must check for the presence of downgrade() in migration files"
        )


# ─────────────────────────────────────────────────────────────
# H — Integration discipline
# ─────────────────────────────────────────────────────────────

class TestHIntegrationDiscipline:

    @pytest.fixture(scope="class")
    def revmap(self):
        return _build_revision_map()

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_migration_count_meets_threshold(self, revmap):
        count = len(revmap)
        assert count >= _MIN_MIGRATIONS, (
            f"Migration count {count} is below minimum threshold {_MIN_MIGRATIONS} — "
            "check that no migrations were accidentally deleted"
        )

    def test_revision_ids_match_filename_prefix(self, revmap):
        mismatches = []
        for rev, info in revmap.items():
            fname = info["file"]
            prefix = fname.split("_")[0]
            if prefix.isdigit() and rev.lstrip("0") != prefix.lstrip("0") and rev != prefix:
                mismatches.append(f"{fname}: revision='{rev}' but prefix='{prefix}'")
        assert not mismatches, (
            f"{len(mismatches)} mismatch(es) between revision ID and filename prefix: "
            + str(mismatches[:3])
        )

    def test_policy_alembic_ini_path_matches_actual_file(self, policy):
        ini_rel = policy.get("metadata", {}).get("alembic_ini", "")
        ini_path = _PROJECT_ROOT / ini_rel
        assert ini_path.exists(), (
            f"policy metadata.alembic_ini '{ini_rel}' does not point to an existing file"
        )

    def test_policy_migrations_dir_path_matches_actual_directory(self, policy):
        mdir_rel = policy.get("metadata", {}).get("migrations_dir", "")
        mdir_path = _PROJECT_ROOT / mdir_rel
        assert mdir_path.is_dir(), (
            f"policy metadata.migrations_dir '{mdir_rel}' does not point to an existing directory"
        )

    def test_ci_script_reference_is_valid_path(self, policy):
        ci_script = policy.get("ci_integration", {}).get("script", "")
        # Remove any trailing flags (e.g. '--check') to get the file path
        script_file = ci_script.split()[0] if ci_script else ""
        script_path = _PROJECT_ROOT / script_file
        assert script_path.exists(), (
            f"ci_integration.script '{script_file}' does not exist at {script_path}"
        )

    def test_no_truncate_table_in_any_upgrade(self, revmap):
        found = []
        for rev, info in revmap.items():
            text  = info["text"]
            lines = text.splitlines()
            in_upgrade = False
            for line in lines:
                if re.match(r"\s*def upgrade", line):
                    in_upgrade = True
                elif re.match(r"\s*def downgrade", line):
                    in_upgrade = False
                if in_upgrade and "TRUNCATE" in line.upper():
                    found.append(f"{info['file']}: {line.strip()[:70]}")
        assert not found, (
            f"{len(found)} TRUNCATE statement(s) in upgrade() — "
            "TRUNCATE destroys all rows and has no safe rollback path: " + str(found[:3])
        )

    def test_safety_check_script_references_chain_validation(self):
        script = _script_text()
        assert "chain" in script.lower(), (
            "Safety check script must include chain integrity validation"
        )

    def test_policy_review_gates_has_destructive_category(self, policy):
        gates = policy.get("review_gates", {}).get("require_review_comment_for", [])
        assert any("destructive" in str(g).lower() for g in gates), (
            "review_gates must include 'destructive' category"
        )
