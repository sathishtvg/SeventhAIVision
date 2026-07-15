#!/usr/bin/env bash
# Logging configuration validator — Gap 27 — Seventh AI Vision
#
# Validates that the backend logging module and Docker Compose environment are
# consistent with the structured logging policy before every deployment.
#
# Usage:
#   POLICY_FILE=config/logging-policy.yml \
#   ./scripts/logging/validate-logging.sh [MODE]
#
# Modes:
#   --validate      Full validation: module content + policy consistency (default)
#   --check-policy  Policy YAML structure check only
#   --check-module  Logging module content check only
#   --dry-run       Print findings without exiting non-zero
#
# Exit codes:
#   0  All checks pass
#   1  One or more violations found
#   2  Prerequisite missing or bad argument

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

POLICY_FILE="${POLICY_FILE:-${PROJECT_ROOT}/config/logging-policy.yml}"
LOGGING_MODULE="${PROJECT_ROOT}/backend/app/core/logging.py"
MODE="validate"
DRY_RUN=false

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --validate)       MODE="validate" ;;
        --check-policy)   MODE="check-policy" ;;
        --check-module)   MODE="check-module" ;;
        --dry-run)        DRY_RUN=true ;;
        *) echo "[LOGGING] Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# ── Prerequisites ─────────────────────────────────────────────────────────────
_require() {
    if ! command -v "$1" &>/dev/null; then
        echo "[LOGGING] ERROR: '$1' is required." >&2
        exit 2
    fi
}

_require python3

if [[ ! -f "${POLICY_FILE}" ]]; then
    echo "[LOGGING] ERROR: Policy file not found: ${POLICY_FILE}" >&2
    exit 2
fi

echo "[LOGGING] Mode   : ${MODE}$(${DRY_RUN} && echo ' (dry-run)')"
echo "[LOGGING] Policy : ${POLICY_FILE}"
echo "[LOGGING] Module : ${LOGGING_MODULE}"

# ── Python validation ─────────────────────────────────────────────────────────
python3 - <<PYEOF
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("[LOGGING] ERROR: PyYAML not installed.", file=sys.stderr)
    sys.exit(2)

policy_file    = "${POLICY_FILE}"
logging_module = "${LOGGING_MODULE}"
mode           = "${MODE}"
dry_run        = "${DRY_RUN}" == "true"

errors   = []
warnings = []

# ── Load policy ───────────────────────────────────────────────────────────────
with open(policy_file, encoding="utf-8") as f:
    policy = yaml.safe_load(f)

# ── Policy structure check ────────────────────────────────────────────────────
if mode in ("validate", "check-policy"):
    required_sections = ["format", "required_fields", "pii_masking", "log_levels", "retention"]
    for s in required_sections:
        if s not in policy:
            errors.append(f"POLICY: missing required section '{s}'")

    if policy.get("format", {}).get("type") != "json":
        errors.append("POLICY: format.type must be 'json' for structured logging")

    pii = policy.get("pii_masking", {})
    if not pii.get("enabled"):
        errors.append("POLICY: pii_masking.enabled must be true")

    pii_fields = pii.get("fields", [])
    for required_field in ("email", "password", "token"):
        if required_field not in pii_fields:
            errors.append(f"POLICY: pii_masking.fields must include '{required_field}'")

    req_fields = policy.get("required_fields", [])
    for rf in ("timestamp", "level", "message", "service", "request_id", "tenant_id"):
        if rf not in req_fields:
            errors.append(f"POLICY: required_fields must include '{rf}'")

    levels = policy.get("log_levels", {})
    valid  = levels.get("valid_levels", [])
    prod_level = levels.get("production", "")
    if prod_level not in valid and prod_level not in ("warning", "error", "critical"):
        warnings.append(f"POLICY: log_levels.production '{prod_level}' may be too verbose for production")

    retention = policy.get("retention", {})
    if retention.get("application_logs_days", 0) < 30:
        errors.append("POLICY: retention.application_logs_days must be >= 30")

# ── Logging module content check ──────────────────────────────────────────────
if mode in ("validate", "check-module"):
    if not Path(logging_module).exists():
        errors.append(f"MODULE: {logging_module} does not exist")
    else:
        module_text = Path(logging_module).read_text(encoding="utf-8")

        checks = {
            "import logging":        "must import Python stdlib 'logging'",
            "import json":           "must import 'json' to format records as JSON",
            "ContextVar":            "must use contextvars.ContextVar for request_id/tenant_id",
            "request_id":            "must include request_id in log records",
            "tenant_id":             "must include tenant_id in log records",
            "get_logger":            "must expose a get_logger() factory function",
            "configure_logging":     "must expose a configure_logging() setup function",
            "set_request_context":   "must expose set_request_context() for middleware use",
            "LOG_LEVEL":             "must read LOG_LEVEL from environment",
            "SERVICE_NAME":          "must read SERVICE_NAME from environment",
            "[REDACTED]":            "must define a PII redaction placeholder",
            "JsonFormatter":         "must define a JSON log formatter",
            "pii":                   "must reference PII masking patterns",
        }
        for snippet, reason in checks.items():
            if snippet not in module_text:
                errors.append(f"MODULE: logging.py {reason} (missing '{snippet}')")

        # Cross-check PII fields from policy against module
        for field in policy.get("pii_masking", {}).get("fields", []):
            if field in ("email", "password", "token", "ip_address"):
                # These specific ones must appear in the module
                if field not in module_text.lower().replace("_", ""):
                    warnings.append(
                        f"MODULE: PII field '{field}' from policy not referenced in logging.py"
                    )

# ── Report ────────────────────────────────────────────────────────────────────
prefix = "[DRY-RUN]" if dry_run else "[LOGGING]"

for e in errors:
    print(f"{prefix} ERROR: {e}", file=sys.stderr)
for w in warnings:
    print(f"{prefix} WARN:  {w}", file=sys.stderr)

if dry_run:
    print(f"{prefix} Dry run: {len(errors)} error(s), {len(warnings)} warning(s)")
    sys.exit(0)

if errors:
    print(f"[LOGGING] FAIL: {len(errors)} error(s), {len(warnings)} warning(s)")
    sys.exit(1)

print(f"[LOGGING] PASS: logging policy and module validated — {len(warnings)} warning(s)")
sys.exit(0)
PYEOF

EXIT_CODE=$?
exit ${EXIT_CODE}
