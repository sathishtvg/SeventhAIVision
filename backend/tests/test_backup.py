"""Gap 25 — Backup & Restore: infrastructure-file-validation tests.

Validates:
  A. File structure (policy, backup script, restore script)
  B. Backup policy validity (YAML, required top-level sections)
  C. Backup policy content (targets, retention, encryption required, RTO/RPO)
  D. Backup policy completeness (naming, schedule, storage, alerting, restore verification)
  E. Backup script structure (shebang, error handling, modes, prerequisites)
  F. Backup script content (pg_dump, age encryption, retention pruning, manifest)
  G. Restore script structure (shebang, error handling, modes: list/verify/dry-run)
  H. Restore script content (decryption, pg_restore, health checks, temp file cleanup)
"""

from __future__ import annotations

import yaml
import pytest
from pathlib import Path

from tests._repo import REPO_ROOT, requires_repo_tree

# Skips the whole module when the repository tree is absent (e.g. inside the
# api image, which ships only backend/). See tests/_repo.py for why failing
# would be the wrong signal here.
pytestmark = requires_repo_tree


_HERE         = Path(__file__).parent
_PROJECT_ROOT = _HERE.parents[1]
_POLICY_PATH  = _PROJECT_ROOT / "config" / "backup-policy.yml"
_BACKUP_SCRIPT = _PROJECT_ROOT / "scripts" / "backup" / "backup-postgres.sh"
_RESTORE_SCRIPT = _PROJECT_ROOT / "scripts" / "backup" / "restore-postgres.sh"

MAX_RTO_HOURS = 24   # recovery time objective ceiling
MAX_RPO_HOURS = 48   # recovery point objective ceiling


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _backup_text() -> str:
    return _BACKUP_SCRIPT.read_text(encoding="utf-8")


def _restore_text() -> str:
    return _RESTORE_SCRIPT.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# A — File structure
# ─────────────────────────────────────────────────────────────

class TestAFileStructure:

    def test_backup_policy_exists(self):
        assert _POLICY_PATH.exists(), f"Missing: {_POLICY_PATH}"

    def test_backup_script_exists(self):
        assert _BACKUP_SCRIPT.exists(), f"Missing: {_BACKUP_SCRIPT}"

    def test_restore_script_exists(self):
        assert _RESTORE_SCRIPT.exists(), f"Missing: {_RESTORE_SCRIPT}"

    def test_backup_scripts_directory_exists(self):
        assert (_PROJECT_ROOT / "scripts" / "backup").is_dir()


# ─────────────────────────────────────────────────────────────
# B — Backup policy validity
# ─────────────────────────────────────────────────────────────

class TestBBackupPolicyValidity:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_policy_valid_yaml(self, policy):
        assert isinstance(policy, dict)

    def test_policy_has_version(self, policy):
        assert "version" in policy

    def test_policy_has_metadata(self, policy):
        assert "metadata" in policy

    def test_policy_has_targets(self, policy):
        targets = policy.get("targets")
        assert isinstance(targets, list) and len(targets) > 0

    def test_policy_has_retention(self, policy):
        assert "retention" in policy

    def test_policy_has_encryption(self, policy):
        assert "encryption" in policy

    def test_policy_has_schedule(self, policy):
        assert "schedule" in policy


# ─────────────────────────────────────────────────────────────
# C — Backup policy content
# ─────────────────────────────────────────────────────────────

class TestCBackupPolicyContent:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_rto_is_defined(self, policy):
        rto = policy.get("metadata", {}).get("rto_hours")
        assert rto is not None, "metadata must define rto_hours (Recovery Time Objective)"

    def test_rto_is_within_limits(self, policy):
        rto = policy.get("metadata", {}).get("rto_hours", 9999)
        assert rto <= MAX_RTO_HOURS, (
            f"rto_hours {rto} exceeds maximum acceptable {MAX_RTO_HOURS}h"
        )

    def test_rpo_is_defined(self, policy):
        rpo = policy.get("metadata", {}).get("rpo_hours")
        assert rpo is not None, "metadata must define rpo_hours (Recovery Point Objective)"

    def test_rpo_is_within_limits(self, policy):
        rpo = policy.get("metadata", {}).get("rpo_hours", 9999)
        assert rpo <= MAX_RPO_HOURS, (
            f"rpo_hours {rpo} exceeds maximum acceptable {MAX_RPO_HOURS}h"
        )

    def test_encryption_is_required(self, policy):
        enc = policy.get("encryption", {})
        assert enc.get("required") is True, (
            "encryption.required must be true — plaintext backups are not acceptable"
        )

    def test_encryption_tool_specified(self, policy):
        enc = policy.get("encryption", {})
        assert enc.get("tool"), "encryption.tool must be specified (e.g., age)"

    def test_postgres_target_present(self, policy):
        targets = policy.get("targets", [])
        pg_targets = [t for t in targets if t.get("type") == "postgresql"]
        assert len(pg_targets) >= 1, (
            "targets must include at least one PostgreSQL backup target"
        )

    def test_retention_has_daily_count(self, policy):
        retention = policy.get("retention", {})
        assert "daily_backups_kept" in retention, (
            "retention must define daily_backups_kept"
        )

    def test_retention_daily_at_least_7(self, policy):
        days = policy.get("retention", {}).get("daily_backups_kept", 0)
        assert days >= 7, (
            f"daily_backups_kept {days} is too few — keep at least 7 days"
        )

    def test_retention_has_minimum_floor(self, policy):
        retention = policy.get("retention", {})
        assert "min_backups_always_kept" in retention, (
            "retention must define a min_backups_always_kept safety floor"
        )


# ─────────────────────────────────────────────────────────────
# D — Backup policy completeness
# ─────────────────────────────────────────────────────────────

class TestDBackupPolicyCompleteness:

    @pytest.fixture(scope="class")
    def policy(self):
        return _load_yaml(_POLICY_PATH)

    def test_naming_pattern_defined(self, policy):
        naming = policy.get("naming", {})
        assert "pattern" in naming, "naming section must define a filename pattern"

    def test_naming_pattern_contains_date(self, policy):
        pattern = policy.get("naming", {}).get("pattern", "")
        assert "date" in pattern, (
            "naming.pattern must include {date} for chronological sorting"
        )

    def test_schedule_has_full_backup(self, policy):
        schedule = policy.get("schedule", {})
        assert "full_backup" in schedule, "schedule must define full_backup timing"

    def test_schedule_has_cron(self, policy):
        cron = policy.get("schedule", {}).get("full_backup", {}).get("cron")
        assert cron, "full_backup schedule must include a cron expression"

    def test_storage_section_defined(self, policy):
        assert "storage" in policy, "policy must define storage locations"

    def test_storage_local_enabled(self, policy):
        local = policy.get("storage", {}).get("local", {})
        assert local.get("enabled") is True, (
            "local storage must be enabled as the baseline"
        )

    def test_restore_verification_steps_defined(self, policy):
        rv = policy.get("restore_verification", {})
        steps = rv.get("post_restore_checks", [])
        assert len(steps) >= 2, (
            "restore_verification must document at least 2 post-restore checks"
        )

    def test_alerting_on_backup_failure_configured(self, policy):
        alerting = policy.get("alerting", {})
        assert alerting.get("alert_on_backup_failure") is True, (
            "alerting.alert_on_backup_failure must be true"
        )

    def test_max_age_before_alert_defined(self, policy):
        alerting = policy.get("alerting", {})
        assert "max_age_before_alert_hours" in alerting, (
            "alerting must define max_age_before_alert_hours"
        )


# ─────────────────────────────────────────────────────────────
# E — Backup script structure
# ─────────────────────────────────────────────────────────────

class TestEBackupScriptStructure:

    @pytest.fixture(scope="class")
    def script(self):
        return _backup_text()

    def test_script_has_shebang(self, script):
        first = script.splitlines()[0]
        assert first.startswith("#!/")
        assert "bash" in first or "sh" in first

    def test_script_has_error_handling(self, script):
        assert "set -e" in script

    def test_script_has_dry_run_mode(self, script):
        assert "--dry-run" in script

    def test_script_checks_prerequisites(self, script):
        assert "_require" in script or "command -v" in script

    def test_script_checks_for_pg_dump(self, script):
        assert "pg_dump" in script

    def test_script_checks_for_age(self, script):
        assert "age" in script

    def test_script_reads_policy_file(self, script):
        assert "POLICY_FILE" in script or "backup-policy" in script

    def test_script_has_enough_lines(self, script):
        non_empty = [l for l in script.splitlines() if l.strip()]
        assert len(non_empty) > 40


# ─────────────────────────────────────────────────────────────
# F — Backup script content
# ─────────────────────────────────────────────────────────────

class TestFBackupScriptContent:

    @pytest.fixture(scope="class")
    def script(self):
        return _backup_text()

    def test_script_invokes_pg_dump(self, script):
        assert "pg_dump" in script

    def test_script_uses_custom_format(self, script):
        assert "custom" in script or "--format=custom" in script, (
            "pg_dump must use custom format (-Fc) for partial restore support"
        )

    def test_script_uses_compression(self, script):
        assert "compress" in script or "--compress" in script

    def test_script_encrypts_with_age(self, script):
        assert "age --recipient" in script or "age -r" in script, (
            "backup script must encrypt the dump with age"
        )

    def test_script_deletes_plaintext_after_encryption(self, script):
        assert "rm -f" in script, (
            "backup script must delete the plaintext dump after encryption"
        )

    def test_script_writes_manifest(self, script):
        assert "manifest" in script.lower(), (
            "backup script must write a manifest file for restore verification"
        )

    def test_script_computes_checksum(self, script):
        assert "sha256" in script.lower() or "hashlib" in script, (
            "backup script must compute and record a SHA-256 checksum"
        )

    def test_script_applies_retention(self, script):
        assert "retention" in script.lower() or "prune" in script.lower() or "DAILY_KEEP" in script, (
            "backup script must apply the retention policy to prune old backups"
        )


# ─────────────────────────────────────────────────────────────
# G — Restore script structure
# ─────────────────────────────────────────────────────────────

class TestGRestoreScriptStructure:

    @pytest.fixture(scope="class")
    def script(self):
        return _restore_text()

    def test_script_has_shebang(self, script):
        first = script.splitlines()[0]
        assert first.startswith("#!/")
        assert "bash" in first or "sh" in first

    def test_script_has_error_handling(self, script):
        assert "set -e" in script

    def test_script_has_dry_run_mode(self, script):
        assert "--dry-run" in script

    def test_script_has_list_mode(self, script):
        assert "--list" in script

    def test_script_has_verify_mode(self, script):
        assert "--verify" in script

    def test_script_checks_prerequisites(self, script):
        assert "_require" in script or "command -v" in script


# ─────────────────────────────────────────────────────────────
# H — Restore script content
# ─────────────────────────────────────────────────────────────

class TestHRestoreScriptContent:

    @pytest.fixture(scope="class")
    def script(self):
        return _restore_text()

    def test_script_decrypts_age_files(self, script):
        assert "age --decrypt" in script or "age -d" in script, (
            "restore script must decrypt .age files before restoring"
        )

    def test_script_invokes_pg_restore(self, script):
        assert "pg_restore" in script

    def test_script_uses_clean_flag(self, script):
        assert "--clean" in script, (
            "pg_restore must use --clean to drop existing objects before restore"
        )

    def test_script_removes_temp_decrypt_file(self, script):
        assert "rm -f" in script, (
            "restore script must delete the temporary decrypted dump after restore"
        )

    def test_script_checks_tenant_count_after_restore(self, script):
        assert "tenants" in script, (
            "restore script must verify tenants table is populated after restore"
        )

    def test_script_runs_api_health_check(self, script):
        assert "/health" in script, (
            "restore script must call the API health endpoint to verify service is up"
        )

    def test_script_handles_encrypted_and_plaintext_files(self, script):
        assert ".age" in script, (
            "restore script must handle .age encrypted backup files"
        )

    def test_script_validates_backup_file_exists(self, script):
        assert "not found" in script or "not -f" in script or "! -f" in script, (
            "restore script must verify the backup file exists before attempting restore"
        )
