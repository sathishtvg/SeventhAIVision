#!/usr/bin/env bash
# Generate SBOM (Software Bill of Materials) for Seventh AI Vision — Gap 17
#
# Produces:
#   sbom/seventh-ai-vision.cdx.json    — CycloneDX 1.5 JSON (primary, widely supported)
#   sbom/seventh-ai-vision.spdx.json   — SPDX 2.3 JSON (secondary, Linux Foundation format)
#   sbom/vulnerability-report.json     — Grype vulnerability scan against the CycloneDX SBOM
#   sbom/vulnerability-report.txt      — Human-readable grype table output
#
# Requires:
#   syft  >= 1.0  (https://github.com/anchore/syft)
#   grype >= 0.70 (https://github.com/anchore/grype)
#
# Install on Linux/macOS:
#   curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh | sh -s -- -b /usr/local/bin
#   curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh -s -- -b /usr/local/bin
#
# Usage:
#   ./scripts/sbom/generate-sbom.sh
#   ./scripts/sbom/generate-sbom.sh --version 1.4.2
#   APP_VERSION=1.4.2 GRYPE_FAIL_ON=high ./scripts/sbom/generate-sbom.sh
#
# Environment variables:
#   APP_VERSION      — embedded in SBOM metadata (default: 1.0.0)
#   GRYPE_FAIL_ON    — minimum severity that causes non-zero exit (default: critical)
#                      options: negligible, low, medium, high, critical

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SBOM_DIR="${PROJECT_ROOT}/sbom"
SYFT_CONFIG="${SCRIPT_DIR}/syft-config.yml"
APP_VERSION="${APP_VERSION:-1.0.0}"
GRYPE_FAIL_ON="${GRYPE_FAIL_ON:-critical}"

# ── Prerequisite check ────────────────────────────────────────────────────────
for cmd in syft grype; do
    if ! command -v "${cmd}" &>/dev/null; then
        echo "[SBOM] ERROR: '${cmd}' is not installed or not on PATH." >&2
        echo "[SBOM]   Install: https://github.com/anchore/${cmd}#installation" >&2
        exit 1
    fi
done

mkdir -p "${SBOM_DIR}"

# ── Step 1: Generate CycloneDX JSON SBOM ─────────────────────────────────────
echo "[SBOM] Step 1/3 — Cataloging components with syft (CycloneDX output)..."
syft "${PROJECT_ROOT}" \
    --config "${SYFT_CONFIG}" \
    --output "cyclonedx-json=${SBOM_DIR}/seventh-ai-vision.cdx.json" \
    --source-name "seventh-ai-vision" \
    --source-version "${APP_VERSION}"

# ── Step 2: Generate SPDX JSON SBOM ──────────────────────────────────────────
echo "[SBOM] Step 2/3 — Cataloging components with syft (SPDX output)..."
syft "${PROJECT_ROOT}" \
    --config "${SYFT_CONFIG}" \
    --output "spdx-json=${SBOM_DIR}/seventh-ai-vision.spdx.json" \
    --source-name "seventh-ai-vision" \
    --source-version "${APP_VERSION}"

# ── Step 3: Vulnerability scan ────────────────────────────────────────────────
echo "[SBOM] Step 3/3 — Scanning for vulnerabilities with grype (fail-on: ${GRYPE_FAIL_ON})..."
grype "sbom:${SBOM_DIR}/seventh-ai-vision.cdx.json" \
    --fail-on "${GRYPE_FAIL_ON}" \
    --output json \
    --file "${SBOM_DIR}/vulnerability-report.json" || VULN_EXIT=$?

grype "sbom:${SBOM_DIR}/seventh-ai-vision.cdx.json" \
    --output table \
    --file "${SBOM_DIR}/vulnerability-report.txt" 2>/dev/null || true

if [[ "${VULN_EXIT:-0}" -ne 0 ]]; then
    echo "[SBOM] WARNING: Grype found vulnerabilities at or above '${GRYPE_FAIL_ON}' severity." >&2
    echo "[SBOM] Review ${SBOM_DIR}/vulnerability-report.json before releasing." >&2
    echo "[SBOM] To add exceptions, update scripts/sbom/sbom-policy.yml." >&2
    exit "${VULN_EXIT}"
fi

echo ""
echo "[SBOM] All artifacts generated in ${SBOM_DIR}/"
echo "  CycloneDX:    ${SBOM_DIR}/seventh-ai-vision.cdx.json"
echo "  SPDX:         ${SBOM_DIR}/seventh-ai-vision.spdx.json"
echo "  Vuln (JSON):  ${SBOM_DIR}/vulnerability-report.json"
echo "  Vuln (table): ${SBOM_DIR}/vulnerability-report.txt"
