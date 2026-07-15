#!/usr/bin/env bash
# OpenAPI spec lint runner — Gap 24 — Seventh AI Vision
#
# Exports the FastAPI OpenAPI spec and validates it with Spectral.
#
# Modes:
#   --export   Export the FastAPI OpenAPI schema to reports/openapi/openapi.json
#   --lint     Run Spectral on the exported spec (requires --export first, or spec exists)
#   --validate Run Python-level spec validation (no Spectral dependency)
#
# Combine modes:
#   ./scripts/lint/lint-openapi.sh --export --lint
#
# Exit codes:
#   0 — spec is valid; no errors
#   1 — lint errors found (CI should fail)
#   2 — prerequisite missing or argument error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

POLICY_FILE="${POLICY_FILE:-${PROJECT_ROOT}/config/openapi-lint-policy.yml}"
SPECTRAL_CONFIG="${PROJECT_ROOT}/config/.spectral.yml"
SPEC_OUTPUT="${PROJECT_ROOT}/reports/openapi/openapi.json"
LINT_EXIT=0
MODE_EXPORT=false
MODE_LINT=false
MODE_VALIDATE=false

# ── Argument parsing ──────────────────────────────────────────────────────────
if [[ $# -eq 0 ]]; then
    echo "[OPENAPI-LINT] Usage: $0 --export | --lint | --validate" >&2
    exit 2
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --export)   MODE_EXPORT=true ;;
        --lint)     MODE_LINT=true ;;
        --validate) MODE_VALIDATE=true ;;
        *) echo "[OPENAPI-LINT] Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# ── Prerequisite checks ───────────────────────────────────────────────────────
_require() {
    if ! command -v "$1" &>/dev/null; then
        echo "[OPENAPI-LINT] ERROR: '$1' is required but not installed." >&2
        echo "[OPENAPI-LINT]   Install: $2" >&2
        exit 2
    fi
}

_require python3 "https://www.python.org/downloads/"

if [[ ! -f "${POLICY_FILE}" ]]; then
    echo "[OPENAPI-LINT] ERROR: Policy file not found: ${POLICY_FILE}" >&2
    exit 2
fi

# ── Mode: export ──────────────────────────────────────────────────────────────
if [[ "${MODE_EXPORT}" == "true" ]]; then
    echo "[OPENAPI-LINT] Exporting OpenAPI spec from FastAPI app..."
    mkdir -p "$(dirname "${SPEC_OUTPUT}")"

    # Read export command from policy
    EXPORT_CMD=$(python3 -c "
import yaml
with open('${POLICY_FILE}') as f:
    policy = yaml.safe_load(f)
print(policy.get('export', {}).get('command', ''))
")

    if [[ -z "${EXPORT_CMD}" ]]; then
        echo "[OPENAPI-LINT] ERROR: No export.command defined in ${POLICY_FILE}" >&2
        exit 2
    fi

    # Run the export command from the project root
    (cd "${PROJECT_ROOT}/backend" && eval "${EXPORT_CMD}") > "${SPEC_OUTPUT}"

    if [[ ! -s "${SPEC_OUTPUT}" ]]; then
        echo "[OPENAPI-LINT] ERROR: Export produced an empty file: ${SPEC_OUTPUT}" >&2
        exit 1
    fi

    ENDPOINT_COUNT=$(python3 -c "
import json
with open('${SPEC_OUTPUT}') as f:
    spec = json.load(f)
paths = spec.get('paths', {})
total = sum(len(methods) for methods in paths.values())
print(total)
")
    echo "[OPENAPI-LINT] Exported spec: ${ENDPOINT_COUNT} operations → ${SPEC_OUTPUT}"
fi

# ── Mode: validate (Python-native — no Node.js/Spectral dependency) ───────────
if [[ "${MODE_VALIDATE}" == "true" ]]; then
    if [[ ! -f "${SPEC_OUTPUT}" ]]; then
        echo "[OPENAPI-LINT] ERROR: Spec file not found: ${SPEC_OUTPUT}" >&2
        echo "[OPENAPI-LINT]   Run --export first to generate it." >&2
        exit 2
    fi

    echo "[OPENAPI-LINT] Validating spec structure (Python)..."
    python3 - <<PYEOF
import json, sys

with open("${SPEC_OUTPUT}") as f:
    spec = json.load(f)

errors = []

# Basic OAS3 structure
if "openapi" not in spec:
    errors.append("Missing 'openapi' version field")
if "info" not in spec or not spec["info"].get("title"):
    errors.append("Missing 'info.title'")
if "paths" not in spec or not spec["paths"]:
    errors.append("Missing or empty 'paths'")

# operationId check
allowed_prefixes = ["/api/v1/", "/health", "/ws/", "/metrics", "/docs", "/openapi.json"]
missing_op_ids = []
bad_paths = []
for path, path_item in spec.get("paths", {}).items():
    # Check path prefix
    if not any(path.startswith(pfx) or path == pfx.rstrip("/") for pfx in allowed_prefixes):
        bad_paths.append(path)
    # Check operationId
    for method, op in path_item.items():
        if method not in ("get","post","put","patch","delete","head","options"):
            continue
        if not isinstance(op, dict):
            continue
        if not op.get("operationId"):
            missing_op_ids.append(f"{method.upper()} {path}")

if missing_op_ids:
    errors.append(f"{len(missing_op_ids)} operations missing operationId: {missing_op_ids[:5]}")
if bad_paths:
    errors.append(f"{len(bad_paths)} paths don't follow versioning convention: {bad_paths[:5]}")

if errors:
    for e in errors:
        print(f"[OPENAPI-LINT] ERROR: {e}", file=sys.stderr)
    sys.exit(1)

total_paths = len(spec.get("paths", {}))
total_ops = sum(
    len([m for m in v if m in ("get","post","put","patch","delete","head","options")])
    for v in spec.get("paths", {}).values()
)
print(f"[OPENAPI-LINT] PASS: {total_paths} paths, {total_ops} operations — all valid")
PYEOF
    VALIDATE_EXIT=$?
    if [[ ${VALIDATE_EXIT} -ne 0 ]]; then
        LINT_EXIT=1
    fi
fi

# ── Mode: lint (Spectral) ─────────────────────────────────────────────────────
if [[ "${MODE_LINT}" == "true" ]]; then
    _require spectral "npm install -g @stoplight/spectral-cli"

    if [[ ! -f "${SPEC_OUTPUT}" ]]; then
        echo "[OPENAPI-LINT] ERROR: Spec not found — run --export first." >&2
        exit 2
    fi

    if [[ ! -f "${SPECTRAL_CONFIG}" ]]; then
        echo "[OPENAPI-LINT] ERROR: Spectral config not found: ${SPECTRAL_CONFIG}" >&2
        exit 2
    fi

    echo "[OPENAPI-LINT] Running Spectral on ${SPEC_OUTPUT}..."
    set +e
    spectral lint "${SPEC_OUTPUT}" \
        --ruleset "${SPECTRAL_CONFIG}" \
        --format stylish \
        --fail-severity error
    SPECTRAL_EXIT=$?
    set -e

    if [[ ${SPECTRAL_EXIT} -ne 0 ]]; then
        echo "[OPENAPI-LINT] FAIL: Spectral found errors" >&2
        LINT_EXIT=1
    else
        echo "[OPENAPI-LINT] PASS: Spectral found no errors"
    fi
fi

if [[ ${LINT_EXIT} -ne 0 ]]; then
    echo "[OPENAPI-LINT] OpenAPI lint FAILED" >&2
    exit 1
fi

echo "[OPENAPI-LINT] OpenAPI lint completed successfully."
exit 0
