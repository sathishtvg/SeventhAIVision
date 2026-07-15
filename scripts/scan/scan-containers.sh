#!/usr/bin/env bash
# Container security scan — Gap 19 — Seventh AI Vision
#
# Runs Trivy in two modes:
#   1. Image scan    — scans built Docker images for OS + library CVEs
#   2. Config scan   — scans Dockerfiles and docker-compose files for misconfigurations
#
# Requires:
#   trivy >= 0.50  (https://github.com/aquasecurity/trivy)
#
# Install on Linux/macOS:
#   curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
#
# Usage:
#   ./scripts/scan/scan-containers.sh                    # scan all images + configs
#   ./scripts/scan/scan-containers.sh --images-only      # skip config scan
#   ./scripts/scan/scan-containers.sh --config-only      # skip image scan
#   IMAGE_TAG=1.4.2 ./scripts/scan/scan-containers.sh    # scan a specific release tag
#
# Environment variables:
#   IMAGE_TAG        — Docker image tag to scan (default: latest)
#   TRIVY_CONFIG     — Path to trivy config file (default: config/trivy.yaml)
#   REPORT_DIR       — Output directory for JSON reports (default: reports/trivy)
#   FAIL_ON_FINDINGS — Exit non-zero if findings exist (default: true)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

IMAGE_TAG="${IMAGE_TAG:-latest}"
TRIVY_CONFIG="${TRIVY_CONFIG:-${PROJECT_ROOT}/config/trivy.yaml}"
REPORT_DIR="${REPORT_DIR:-${PROJECT_ROOT}/reports/trivy}"
FAIL_ON_FINDINGS="${FAIL_ON_FINDINGS:-true}"
IMAGES_ONLY=false
CONFIG_ONLY=false

# ── Argument parsing ──────────────────────────────────────────────────────────
for arg in "$@"; do
    case "${arg}" in
        --images-only) IMAGES_ONLY=true ;;
        --config-only) CONFIG_ONLY=true ;;
    esac
done

# ── Prerequisite check ────────────────────────────────────────────────────────
if ! command -v trivy &>/dev/null; then
    echo "[SCAN] ERROR: 'trivy' is not installed or not on PATH." >&2
    echo "[SCAN]   Install: https://github.com/aquasecurity/trivy#installation" >&2
    exit 1
fi

mkdir -p "${REPORT_DIR}"

SCAN_EXIT=0

# ── Image names ───────────────────────────────────────────────────────────────
# Matches the service names built by docker compose.
IMAGES=(
    "seventh-ai-vision/api:${IMAGE_TAG}"
    "seventh-ai-vision/ingestion:${IMAGE_TAG}"
    "seventh-ai-vision/ai-worker:${IMAGE_TAG}"
    "seventh-ai-vision/frontend:${IMAGE_TAG}"
    "seventh-ai-vision/scheduler:${IMAGE_TAG}"
)

# ── Step 1: Image vulnerability scan ─────────────────────────────────────────
if [[ "${CONFIG_ONLY}" != "true" ]]; then
    echo "[SCAN] Step 1 — Scanning Docker images for vulnerabilities (tag: ${IMAGE_TAG})..."
    for image in "${IMAGES[@]}"; do
        image_slug="${image//[:\\/]/_}"
        report_file="${REPORT_DIR}/${image_slug}.json"

        echo "[SCAN]   → ${image}"
        trivy image \
            --config  "${TRIVY_CONFIG}" \
            --format  json \
            --output  "${report_file}" \
            --ignorefile "${PROJECT_ROOT}/.trivyignore" \
            --exit-code 0 \
            "${image}" 2>/dev/null || true

        # Re-run with exit-code 1 for CRITICAL/HIGH gate
        trivy image \
            --config  "${TRIVY_CONFIG}" \
            --format  table \
            --ignorefile "${PROJECT_ROOT}/.trivyignore" \
            --exit-code 1 \
            "${image}" || SCAN_EXIT=$?
    done
fi

# ── Step 2: Dockerfile / IaC misconfiguration scan ───────────────────────────
if [[ "${IMAGES_ONLY}" != "true" ]]; then
    echo "[SCAN] Step 2 — Scanning Dockerfiles and compose files for misconfigurations..."
    trivy config \
        --config  "${TRIVY_CONFIG}" \
        --format  json \
        --output  "${REPORT_DIR}/iac-misconfigurations.json" \
        --exit-code 0 \
        "${PROJECT_ROOT}/docker" 2>/dev/null || true

    trivy config \
        --config  "${TRIVY_CONFIG}" \
        --format  table \
        --exit-code 1 \
        "${PROJECT_ROOT}/docker" || SCAN_EXIT=$?
fi

# ── Result summary ────────────────────────────────────────────────────────────
echo ""
echo "[SCAN] Reports written to ${REPORT_DIR}/"

if [[ "${SCAN_EXIT}" -ne 0 ]]; then
    echo "[SCAN] WARNING: Trivy found CRITICAL or HIGH findings." >&2
    echo "[SCAN] Review ${REPORT_DIR}/ and address before releasing." >&2
    echo "[SCAN] To add exceptions, update .trivyignore with expiry + justification." >&2
    if [[ "${FAIL_ON_FINDINGS}" == "true" ]]; then
        exit "${SCAN_EXIT}"
    fi
fi

echo "[SCAN] Scan complete."
