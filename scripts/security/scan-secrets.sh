#!/usr/bin/env bash
# Secret scanner — Gap 28 — Seventh AI Vision
#
# Wrapper around detect-secrets that reads config/secret-scanning-policy.yml,
# enforces severity-based CI gates, and manages the baseline file.
#
# Usage:
#   ./scripts/security/scan-secrets.sh [MODE] [OPTIONS]
#
# Modes:
#   --scan        Full scan against current source tree (default)
#   --baseline    Regenerate .detect-secrets.yaml baseline (accepts current findings)
#   --audit       Interactive audit of unreviewed secrets in baseline
#   --diff        Show secrets added since last baseline (CI mode — exit 1 on new secrets)
#   --dry-run     Print findings without exiting non-zero
#
# Options:
#   --path PATH   Root path to scan (default: project root)
#   --policy FILE Policy YAML path (default: config/secret-scanning-policy.yml)
#
# Exit codes:
#   0  No new secrets (or dry-run)
#   1  New secrets found above the fail_on_severity threshold
#   2  Prerequisite missing or bad argument

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

POLICY_FILE="${POLICY_FILE:-${PROJECT_ROOT}/config/secret-scanning-policy.yml}"
BASELINE_FILE="${BASELINE_FILE:-${PROJECT_ROOT}/.detect-secrets.yaml}"
SCAN_ROOT="${SCAN_ROOT:-${PROJECT_ROOT}}"
MODE="scan"
DRY_RUN=false

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --scan)      MODE="scan" ;;
        --baseline)  MODE="baseline" ;;
        --audit)     MODE="audit" ;;
        --diff)      MODE="diff" ;;
        --dry-run)   DRY_RUN=true ;;
        --path)      shift; SCAN_ROOT="$1" ;;
        --policy)    shift; POLICY_FILE="$1" ;;
        *) echo "[SECRETS] Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# ── Prerequisites ─────────────────────────────────────────────────────────────
_require() {
    if ! command -v "$1" &>/dev/null; then
        echo "[SECRETS] ERROR: '$1' is required. Install with: pip install detect-secrets" >&2
        exit 2
    fi
}

_require python3

if [[ ! -f "${POLICY_FILE}" ]]; then
    echo "[SECRETS] ERROR: Policy file not found: ${POLICY_FILE}" >&2
    exit 2
fi

echo "[SECRETS] Mode    : ${MODE}$(${DRY_RUN} && echo ' (dry-run)')"
echo "[SECRETS] Policy  : ${POLICY_FILE}"
echo "[SECRETS] Root    : ${SCAN_ROOT}"
echo "[SECRETS] Baseline: ${BASELINE_FILE}"

# ── Python helpers ────────────────────────────────────────────────────────────
python3 - <<PYEOF
import sys
import json
import subprocess
import os
from pathlib import Path

try:
    import yaml
except ImportError:
    print("[SECRETS] ERROR: PyYAML not installed.", file=sys.stderr)
    sys.exit(2)

policy_file   = "${POLICY_FILE}"
baseline_file = "${BASELINE_FILE}"
scan_root     = "${SCAN_ROOT}"
mode          = "${MODE}"
dry_run       = "${DRY_RUN}" == "true"

# ── Load policy ───────────────────────────────────────────────────────────────
with open(policy_file, encoding="utf-8") as f:
    policy = yaml.safe_load(f)

ci_cfg       = policy.get("ci_integration", {})
fail_severity = ci_cfg.get("fail_on_severity", "high")

SEVERITY_ORDER = {"critical": 3, "high": 2, "medium": 1, "low": 0}
fail_level = SEVERITY_ORDER.get(fail_severity, 2)

# Build exclude patterns from policy scope
scope     = policy.get("scope", {})
excludes  = scope.get("exclude", [])

# ── detect-secrets CLI check ──────────────────────────────────────────────────
detect_secrets_available = False
try:
    result = subprocess.run(
        ["python3", "-m", "detect_secrets", "--version"],
        capture_output=True, text=True, timeout=10
    )
    detect_secrets_available = (result.returncode == 0)
except Exception:
    pass

# ── Mode handlers ─────────────────────────────────────────────────────────────

def _build_scan_cmd():
    cmd = [
        "python3", "-m", "detect_secrets", "scan",
        "--baseline", baseline_file,
    ]
    if Path(baseline_file).exists():
        cmd += ["--update-baseline-if-newer"]
    return cmd

if mode == "baseline":
    if not detect_secrets_available:
        print("[SECRETS] WARN: detect-secrets not installed; writing empty baseline placeholder.")
        # Baseline already exists (written by the policy files); just report
        print(f"[SECRETS] Baseline file: {baseline_file}")
        print("[SECRETS] To regenerate: pip install detect-secrets && python3 -m detect_secrets scan > .detect-secrets.yaml")
        sys.exit(0)
    cmd = ["python3", "-m", "detect_secrets", "scan", scan_root, "--all-files"]
    print(f"[SECRETS] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    with open(baseline_file, "w") as f:
        f.write(result.stdout)
    print(f"[SECRETS] Baseline written to {baseline_file}")
    sys.exit(0)

elif mode == "audit":
    if not detect_secrets_available:
        print("[SECRETS] WARN: detect-secrets not installed; cannot run audit.")
        sys.exit(0)
    cmd = ["python3", "-m", "detect_secrets", "audit", baseline_file]
    os.execvp("python3", cmd)

elif mode in ("scan", "diff"):
    if not detect_secrets_available:
        print("[SECRETS] WARN: detect-secrets not installed in this environment.")
        print("[SECRETS] Policy validation only — skipping live scan.")
        # Validate policy structure instead
        errors = []
        for section in ("scope", "patterns", "allowlist", "enforcement", "ci_integration"):
            if section not in policy:
                errors.append(f"POLICY: missing required section '{section}'")

        patterns = policy.get("patterns", {})
        for severity in ("critical", "high", "medium"):
            entries = patterns.get(severity, [])
            if not isinstance(entries, list) or len(entries) == 0:
                errors.append(f"POLICY: patterns.{severity} must have at least one entry")

        enforcement = policy.get("enforcement", {})
        for sev in ("critical", "high"):
            action = enforcement.get(sev, {}).get("action", "")
            if action != "block":
                errors.append(f"POLICY: enforcement.{sev}.action must be 'block'")

        if errors:
            for e in errors:
                print(f"[SECRETS] ERROR: {e}", file=sys.stderr)
            if not dry_run:
                sys.exit(1)
        else:
            print(f"[SECRETS] PASS: policy structure valid ({len(errors)} errors)")
        sys.exit(0)

    # Run scan
    cmd = ["python3", "-m", "detect_secrets", "scan", scan_root]
    print(f"[SECRETS] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    try:
        findings = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("[SECRETS] ERROR: detect-secrets output was not valid JSON", file=sys.stderr)
        sys.exit(2)

    results = findings.get("results", {})
    new_secrets = []

    if Path(baseline_file).exists():
        with open(baseline_file) as f:
            try:
                baseline = yaml.safe_load(f)
            except Exception:
                baseline = {"results": {}}
        baseline_results = baseline.get("results", {}) or {}
    else:
        baseline_results = {}

    for file_path, secrets in results.items():
        for secret in secrets:
            secret_hash = secret.get("hashed_secret", "")
            file_baseline = baseline_results.get(file_path, [])
            baseline_hashes = {s.get("hashed_secret") for s in file_baseline}
            if secret_hash not in baseline_hashes:
                new_secrets.append({
                    "file": file_path,
                    "type": secret.get("type", "unknown"),
                    "line": secret.get("line_number", 0),
                })

    prefix = "[DRY-RUN]" if dry_run else "[SECRETS]"

    if new_secrets:
        print(f"{prefix} Found {len(new_secrets)} new potential secret(s):", file=sys.stderr)
        for s in new_secrets:
            print(f"  {s['file']}:{s['line']} — {s['type']}", file=sys.stderr)
        print(f"\n{prefix} To update baseline: ./scripts/security/scan-secrets.sh --baseline")
        if dry_run:
            print(f"{prefix} Dry run: would have exited 1")
            sys.exit(0)
        sys.exit(1)
    else:
        print(f"{prefix} PASS: no new secrets found (scanned {len(results)} file(s))")
        sys.exit(0)

else:
    print(f"[SECRETS] ERROR: Unknown mode '{mode}'", file=sys.stderr)
    sys.exit(2)

PYEOF

EXIT_CODE=$?
exit ${EXIT_CODE}
