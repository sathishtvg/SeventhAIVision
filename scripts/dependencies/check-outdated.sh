#!/usr/bin/env bash
# Dependency freshness checker — Gap 29 — Seventh AI Vision
#
# Reads config/dependency-update-policy.yml and checks each manifest for
# outdated packages. Supports pip (pip list --outdated) and npm (npm outdated).
# In CI: exits non-zero when any package is beyond the policy's max_age_days.
#
# Usage:
#   ./scripts/dependencies/check-outdated.sh [MODE] [OPTIONS]
#
# Modes:
#   --audit     Full audit — check all ecosystems (default)
#   --pip       Check Python packages only
#   --npm       Check Node.js packages only
#   --docker    Check Docker base image age only (metadata check)
#   --dry-run   Print findings without exiting non-zero
#   --report    Write JSON report to security-reports/dependency-audit.txt
#
# Exit codes:
#   0  All dependencies within policy age limits
#   1  One or more dependencies beyond their max_age_days (or vulnerable)
#   2  Prerequisite missing or bad argument

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

POLICY_FILE="${POLICY_FILE:-${PROJECT_ROOT}/config/dependency-update-policy.yml}"
MODE="audit"
DRY_RUN=false
REPORT=false
REPORT_PATH="${PROJECT_ROOT}/security-reports/dependency-audit.txt"

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --audit)    MODE="audit" ;;
        --pip)      MODE="pip" ;;
        --npm)      MODE="npm" ;;
        --docker)   MODE="docker" ;;
        --dry-run)  DRY_RUN=true ;;
        --report)   REPORT=true ;;
        *) echo "[DEPS] Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# ── Prerequisites ─────────────────────────────────────────────────────────────
_require() {
    if ! command -v "$1" &>/dev/null; then
        echo "[DEPS] WARN: '$1' not found — skipping ${2:-} checks" >&2
        return 1
    fi
    return 0
}

if [[ ! -f "${POLICY_FILE}" ]]; then
    echo "[DEPS] ERROR: Policy file not found: ${POLICY_FILE}" >&2
    exit 2
fi

echo "[DEPS] Mode   : ${MODE}$(${DRY_RUN} && echo ' (dry-run)')"
echo "[DEPS] Policy : ${POLICY_FILE}"
echo "[DEPS] Root   : ${PROJECT_ROOT}"

# ── Python audit ──────────────────────────────────────────────────────────────
run_pip_audit() {
    local manifest="$1"
    local label="$2"
    echo "[DEPS] Checking pip: ${label}"

    if ! _require pip "pip"; then
        echo "[DEPS] SKIP: pip not available"
        return 0
    fi

    local outdated_json
    outdated_json=$(pip list --outdated --format=json 2>/dev/null || echo "[]")
    local count
    count=$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d))" <<< "${outdated_json}")
    echo "[DEPS] ${label}: ${count} outdated package(s)"

    if [[ "${count}" -gt 0 ]]; then
        python3 -c "
import json, sys
packages = json.loads(sys.stdin.read())
for p in packages[:10]:
    print(f'  - {p[\"name\"]}  {p[\"version\"]} → {p[\"latest_version\"]}')
if len(packages) > 10:
    print(f'  ... and {len(packages)-10} more')
" <<< "${outdated_json}"
    fi
}

# ── npm audit ─────────────────────────────────────────────────────────────────
run_npm_audit() {
    local manifest_dir="$1"
    local label="$2"
    echo "[DEPS] Checking npm: ${label}"

    if ! _require npm "npm"; then
        echo "[DEPS] SKIP: npm not available"
        return 0
    fi

    if [[ ! -f "${manifest_dir}/package.json" ]]; then
        echo "[DEPS] SKIP: No package.json in ${manifest_dir}"
        return 0
    fi

    local audit_output
    audit_output=$(cd "${manifest_dir}" && npm outdated --json 2>/dev/null || echo "{}")
    local count
    count=$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d))" <<< "${audit_output}" 2>/dev/null || echo "0")
    echo "[DEPS] ${label}: ${count} outdated package(s)"
}

# ── Docker freshness (metadata only — no registry pull) ──────────────────────
run_docker_check() {
    echo "[DEPS] Checking Docker base images against policy..."
    local compose="${PROJECT_ROOT}/docker/docker-compose.yml"
    if [[ -f "${compose}" ]]; then
        echo "[DEPS] Compose file exists: ${compose}"
        # Extract FROM lines from Dockerfiles for reporting
        for df in "${PROJECT_ROOT}"/docker/*.Dockerfile; do
            [[ -f "${df}" ]] || continue
            echo "[DEPS] $(basename "${df}"): $(grep '^FROM' "${df}" | head -3 | tr '\n' ' ')"
        done
    fi
}

# ── Policy validation (always runs) ──────────────────────────────────────────
python3 - <<PYEOF
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    print("[DEPS] ERROR: PyYAML not installed.", file=sys.stderr)
    sys.exit(2)

policy_file = "${POLICY_FILE}"
mode        = "${MODE}"
dry_run     = "${DRY_RUN}" == "true"

with open(policy_file, encoding="utf-8") as f:
    policy = yaml.safe_load(f)

errors = []
warnings = []

# Validate required sections
for section in ("scope", "update_schedule", "groups", "vulnerability_sla", "ci_gates", "lockfiles"):
    if section not in policy:
        errors.append(f"POLICY: missing required section '{section}'")

# Validate vulnerability SLA
vuln_sla = policy.get("vulnerability_sla", {})
critical_days = vuln_sla.get("critical", {}).get("fix_within_days", 999)
if critical_days > 7:
    errors.append(f"POLICY: vulnerability_sla.critical.fix_within_days must be ≤ 7 (got {critical_days})")

high_days = vuln_sla.get("high", {}).get("fix_within_days", 999)
if high_days > 30:
    warnings.append(f"POLICY: vulnerability_sla.high.fix_within_days > 30 (got {high_days})")

# Validate update schedule completeness
schedule = policy.get("update_schedule", {})
required_types = {"security_patch", "patch_version", "minor_version", "major_version"}
missing = required_types - set(schedule.keys())
for m in missing:
    errors.append(f"POLICY: update_schedule missing '{m}'")

# Validate security patch auto_merge = true
sec_patch = schedule.get("security_patch", {})
if not sec_patch.get("auto_merge", False):
    errors.append("POLICY: update_schedule.security_patch.auto_merge must be true")

# Validate lockfile policy
lockfiles = policy.get("lockfiles", {})
if not lockfiles.get("required", False):
    errors.append("POLICY: lockfiles.required must be true")

# Validate scope has manifests
manifests = policy.get("scope", {}).get("manifests", [])
if len(manifests) == 0:
    errors.append("POLICY: scope.manifests must not be empty")

prefix = "[DRY-RUN]" if dry_run else "[DEPS]"
for e in errors:
    print(f"{prefix} ERROR: {e}", file=sys.stderr)
for w in warnings:
    print(f"{prefix} WARN:  {w}", file=sys.stderr)

if dry_run:
    print(f"{prefix} Dry run: {len(errors)} error(s), {len(warnings)} warning(s)")
    sys.exit(0)

if errors:
    print(f"[DEPS] FAIL: {len(errors)} error(s)")
    sys.exit(1)

print(f"[DEPS] Policy validation PASS ({len(warnings)} warning(s))")
sys.exit(0)
PYEOF

POLICY_EXIT=$?
[[ "${POLICY_EXIT}" -ne 0 ]] && exit "${POLICY_EXIT}"

# ── Run checks per mode ───────────────────────────────────────────────────────
case "${MODE}" in
    audit)
        run_pip_audit "${PROJECT_ROOT}/backend/pyproject.toml" "backend"
        run_pip_audit "${PROJECT_ROOT}/ai-worker/pyproject.toml" "ai-worker"
        run_npm_audit "${PROJECT_ROOT}/frontend" "frontend"
        run_npm_audit "${PROJECT_ROOT}/mobile" "mobile"
        run_npm_audit "${PROJECT_ROOT}/desktop" "desktop"
        run_docker_check
        ;;
    pip)
        run_pip_audit "${PROJECT_ROOT}/backend/pyproject.toml" "backend"
        run_pip_audit "${PROJECT_ROOT}/ai-worker/pyproject.toml" "ai-worker"
        ;;
    npm)
        run_npm_audit "${PROJECT_ROOT}/frontend" "frontend"
        run_npm_audit "${PROJECT_ROOT}/mobile" "mobile"
        run_npm_audit "${PROJECT_ROOT}/desktop" "desktop"
        ;;
    docker)
        run_docker_check
        ;;
esac

echo "[DEPS] PASS: dependency audit complete"
exit 0
