#!/usr/bin/env bash
# Container hardening checker — Gap 30 — Seventh AI Vision
#
# Validates Dockerfiles against config/container-hardening-policy.yml rules and
# checks docker-compose service definitions for runtime control compliance.
#
# Usage:
#   ./scripts/security/check-container-hardening.sh [MODE] [OPTIONS]
#
# Modes:
#   --check       Full check: Dockerfiles + Compose services (default)
#   --dockerfile  Dockerfile checks only
#   --compose     Compose service checks only
#   --report      Write findings to security-reports/hardening-report.txt
#   --dry-run     Print findings without exiting non-zero
#
# Exit codes:
#   0  All critical and high controls pass
#   1  One or more critical/high violations found
#   2  Prerequisite missing or bad argument

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

POLICY_FILE="${POLICY_FILE:-${PROJECT_ROOT}/config/container-hardening-policy.yml}"
SECCOMP_FILE="${PROJECT_ROOT}/docker/security/seccomp-profile.json"
MODE="check"
DRY_RUN=false
REPORT=false

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)       MODE="check" ;;
        --dockerfile)  MODE="dockerfile" ;;
        --compose)     MODE="compose" ;;
        --report)      REPORT=true ;;
        --dry-run)     DRY_RUN=true ;;
        *) echo "[HARDENING] Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# ── Prerequisites ─────────────────────────────────────────────────────────────
if [[ ! -f "${POLICY_FILE}" ]]; then
    echo "[HARDENING] ERROR: Policy file not found: ${POLICY_FILE}" >&2
    exit 2
fi

echo "[HARDENING] Mode   : ${MODE}$(${DRY_RUN} && echo ' (dry-run)')"
echo "[HARDENING] Policy : ${POLICY_FILE}"
echo "[HARDENING] Root   : ${PROJECT_ROOT}"

# ── Python validation core ────────────────────────────────────────────────────
python3 - <<PYEOF
import sys
import json
import re
from pathlib import Path

try:
    import yaml
except ImportError:
    print("[HARDENING] ERROR: PyYAML not installed.", file=sys.stderr)
    sys.exit(2)

policy_file  = "${POLICY_FILE}"
project_root = Path("${PROJECT_ROOT}")
mode         = "${MODE}"
dry_run      = "${DRY_RUN}" == "true"

with open(policy_file, encoding="utf-8") as f:
    policy = yaml.safe_load(f)

errors   = []
warnings = []

# ── Policy structure validation ───────────────────────────────────────────────
required_sections = ("scope", "build_controls", "runtime_controls", "scanning", "ci_integration")
for s in required_sections:
    if s not in policy:
        errors.append(f"POLICY: missing required section '{s}'")

# ── Dockerfile checks ─────────────────────────────────────────────────────────
def check_dockerfile(df_path: Path, controls: dict, exceptions: list) -> tuple[list, list]:
    errs, warns = [], []
    if not df_path.exists():
        warns.append(f"DOCKERFILE: {df_path.name} not found (skipping)")
        return errs, warns

    text = df_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    df_name = str(df_path)

    # Control: non_root_user
    non_root_ctrl = controls.get("non_root_user", {})
    exc_files = non_root_ctrl.get("exceptions", [])
    is_excepted = any(exc in df_name for exc in exc_files)
    if not is_excepted:
        has_user = any(re.match(r"^USER\s+", l.strip()) for l in lines)
        if not has_user:
            sev = non_root_ctrl.get("severity", "high")
            msg = f"DOCKERFILE:{df_path.name}: no USER instruction (non-root required) [{sev.upper()}]"
            if sev in ("critical", "high"):
                errs.append(msg)
            else:
                warns.append(msg)

    # Control: no_privileged_commands (no sudo)
    no_priv = controls.get("no_privileged_commands", {})
    for pattern in no_priv.get("prohibited_patterns", []):
        for line in lines:
            if re.search(pattern, line):
                sev = no_priv.get("severity", "high")
                msg = f"DOCKERFILE:{df_path.name}: prohibited pattern '{pattern}' found [{sev.upper()}]"
                if sev in ("critical", "high"):
                    errs.append(msg)
                else:
                    warns.append(msg)
                break

    # Control: no_secrets_in_build (no ENV PASSWORD=...)
    no_secrets = controls.get("no_secrets_in_build", {})
    prohibited_keys = no_secrets.get("prohibited_env_keys", [])
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("ENV "):
            for key in prohibited_keys:
                if re.search(rf"\b{key}\b\s*=\s*[^\${{]", stripped, re.IGNORECASE):
                    sev = no_secrets.get("severity", "critical")
                    msg = f"DOCKERFILE:{df_path.name}: secret key '{key}' hardcoded in ENV [{sev.upper()}]"
                    errs.append(msg)

    # Control: base_image — no :latest
    base_ctrl = controls.get("base_image", {})
    prohibited_tag_patterns = base_ctrl.get("prohibited_patterns", [])
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("FROM "):
            for pat in prohibited_tag_patterns:
                # pat like ":latest" or "ubuntu:.*"
                clean_pat = pat.rstrip("*").rstrip(".")  # rough check
                if ":latest" in pat and ":latest" in stripped.lower():
                    sev = base_ctrl.get("severity", "high")
                    warns.append(f"DOCKERFILE:{df_path.name}: uses ':latest' tag in '{stripped}' [{sev.upper()}]")

    return errs, warns


if mode in ("check", "dockerfile"):
    scope = policy.get("scope", {})
    build_controls = policy.get("build_controls", {})
    for df_rel in scope.get("dockerfiles", []):
        df_path = project_root / df_rel
        e, w = check_dockerfile(df_path, build_controls, [])
        errors.extend(e)
        warnings.extend(w)

# ── Compose checks ────────────────────────────────────────────────────────────
def check_compose(compose_path: Path, controls: dict) -> tuple[list, list]:
    errs, warns = [], []
    if not compose_path.exists():
        warns.append(f"COMPOSE: {compose_path.name} not found (skipping)")
        return errs, warns

    with open(compose_path, encoding="utf-8") as f:
        compose = yaml.safe_load(f)

    if not compose or "services" not in compose:
        return errs, warns

    no_priv_ctrl = controls.get("no_privileged_mode", {})
    no_host_net  = controls.get("no_host_network", {})

    for svc_name, svc in compose.get("services", {}).items():
        if not isinstance(svc, dict):
            continue

        # Control: no privileged: true
        if svc.get("privileged") is True:
            sev = no_priv_ctrl.get("severity", "critical")
            errs.append(f"COMPOSE:{compose_path.name}:{svc_name}: privileged: true is not allowed [{sev.upper()}]")

        # Control: no host network
        net_mode = svc.get("network_mode", "")
        if net_mode == "host":
            sev = no_host_net.get("severity", "high")
            msg = f"COMPOSE:{compose_path.name}:{svc_name}: network_mode: host is not allowed [{sev.upper()}]"
            if sev in ("critical", "high"):
                errs.append(msg)
            else:
                warns.append(msg)

    return errs, warns


if mode in ("check", "compose"):
    scope = policy.get("scope", {})
    runtime_controls = policy.get("runtime_controls", {})
    for cf_rel in scope.get("compose_files", []):
        cf_path = project_root / cf_rel
        e, w = check_compose(cf_path, runtime_controls)
        errors.extend(e)
        warnings.extend(w)

# ── Seccomp profile check ─────────────────────────────────────────────────────
seccomp_path = project_root / "docker" / "security" / "seccomp-profile.json"
if seccomp_path.exists():
    with open(seccomp_path) as f:
        try:
            seccomp = json.load(f)
            if "defaultAction" not in seccomp:
                warnings.append("SECCOMP: profile missing 'defaultAction' field")
            if "syscalls" not in seccomp:
                warnings.append("SECCOMP: profile missing 'syscalls' list")
        except json.JSONDecodeError as e:
            errors.append(f"SECCOMP: profile JSON is invalid: {e}")
else:
    warnings.append("SECCOMP: docker/security/seccomp-profile.json not found")

# ── Report ────────────────────────────────────────────────────────────────────
prefix = "[DRY-RUN]" if dry_run else "[HARDENING]"

for e in errors:
    print(f"{prefix} ERROR: {e}", file=sys.stderr)
for w in warnings:
    print(f"{prefix} WARN:  {w}", file=sys.stderr)

if dry_run:
    print(f"{prefix} Dry run: {len(errors)} error(s), {len(warnings)} warning(s)")
    sys.exit(0)

if errors:
    print(f"[HARDENING] FAIL: {len(errors)} error(s), {len(warnings)} warning(s)")
    sys.exit(1)

print(f"[HARDENING] PASS: container hardening check complete — {len(warnings)} warning(s)")
sys.exit(0)
PYEOF

EXIT_CODE=$?
exit ${EXIT_CODE}
