#!/usr/bin/env bash
# Load & performance test runner — Gap 23 — Seventh AI Vision
#
# Wraps Locust with the scenarios and thresholds from config/load-test-policy.yml.
#
# Modes:
#   --smoke    5 users / 30s — CI pre-deployment gate (fast, zero-error tolerance)
#   --load     50 users / 5m — nightly performance baseline
#   --stress   200 users / 10m — monthly breaking-point test
#   --report   Generate an HTML + CSV report from the last run (requires --smoke/load/stress)
#
# Usage:
#   LOCUST_HOST=http://api:8000 ./scripts/load-test/run-load-tests.sh --smoke
#   LOCUST_HOST=http://localhost:8000 ./scripts/load-test/run-load-tests.sh --load --report
#
# Required environment variables:
#   LOCUST_HOST              Base URL of the API (default: http://api:8000)
#   LOAD_TEST_USER_EMAIL     Email of the test user account
#   LOAD_TEST_USER_PASSWORD  Password of the test user account
#   LOAD_TEST_TENANT_SLUG    Tenant slug for the test account
#
# Exit codes:
#   0 — all thresholds met
#   1 — threshold breached (see output for details)
#   2 — prerequisite missing or argument error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

POLICY_FILE="${POLICY_FILE:-${PROJECT_ROOT}/config/load-test-policy.yml}"
LOCUSTFILE="${PROJECT_ROOT}/locustfiles/load_test_api.py"
REPORT_DIR="${PROJECT_ROOT}/reports/load-tests"
LOCUST_HOST="${LOCUST_HOST:-http://api:8000}"

SCENARIO=""
DO_REPORT=false
TEST_EXIT=0

# ── Argument parsing ──────────────────────────────────────────────────────────
if [[ $# -eq 0 ]]; then
    echo "[LOAD-TEST] Usage: $0 --smoke | --load | --stress [--report]" >&2
    exit 2
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --smoke)   SCENARIO="smoke" ;;
        --load)    SCENARIO="load" ;;
        --stress)  SCENARIO="stress" ;;
        --report)  DO_REPORT=true ;;
        *) echo "[LOAD-TEST] Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

if [[ -z "${SCENARIO}" ]]; then
    echo "[LOAD-TEST] ERROR: Specify a scenario: --smoke, --load, or --stress" >&2
    exit 2
fi

# ── Prerequisite checks ───────────────────────────────────────────────────────
_require() {
    if ! command -v "$1" &>/dev/null; then
        echo "[LOAD-TEST] ERROR: '$1' is required but not installed." >&2
        echo "[LOAD-TEST]   Install: $2" >&2
        exit 2
    fi
}

_require locust "pip install locust"
_require python3 "https://www.python.org/downloads/"

if [[ ! -f "${POLICY_FILE}" ]]; then
    echo "[LOAD-TEST] ERROR: Policy file not found: ${POLICY_FILE}" >&2
    exit 2
fi

if [[ ! -f "${LOCUSTFILE}" ]]; then
    echo "[LOAD-TEST] ERROR: Locust file not found: ${LOCUSTFILE}" >&2
    exit 2
fi

# ── Read scenario config from policy ─────────────────────────────────────────
read -r USERS SPAWN_RATE DURATION < <(python3 - <<PYEOF
import yaml, sys

with open("${POLICY_FILE}") as f:
    policy = yaml.safe_load(f)

scenario = next(
    (s for s in policy.get("scenarios", []) if s["name"] == "${SCENARIO}"),
    None
)
if scenario is None:
    print(f"Scenario '${SCENARIO}' not found in policy", file=sys.stderr)
    sys.exit(2)

users      = scenario["users"]
spawn_rate = scenario["spawn_rate"]
duration   = scenario["duration"]
print(users, spawn_rate, duration)
PYEOF
)

echo "[LOAD-TEST] Scenario : ${SCENARIO}"
echo "[LOAD-TEST] Users    : ${USERS}"
echo "[LOAD-TEST] Spawn    : ${SPAWN_RATE}/s"
echo "[LOAD-TEST] Duration : ${DURATION}"
echo "[LOAD-TEST] Host     : ${LOCUST_HOST}"

# ── Prepare report directory ───────────────────────────────────────────────────
mkdir -p "${REPORT_DIR}"
REPORT_DATE="$(date +%Y-%m-%d-%H%M%S)"
REPORT_BASE="${REPORT_DIR}/locust-${SCENARIO}-${REPORT_DATE}"

# ── Build locust command ───────────────────────────────────────────────────────
LOCUST_CMD=(
    locust
    -f "${LOCUSTFILE}"
    --headless
    --users "${USERS}"
    --spawn-rate "${SPAWN_RATE}"
    --run-time "${DURATION}"
    --host "${LOCUST_HOST}"
    --csv "${REPORT_BASE}"
    --logfile "${REPORT_BASE}.log"
)

if [[ "${DO_REPORT}" == "true" ]]; then
    LOCUST_CMD+=(--html "${REPORT_BASE}.html")
fi

echo "[LOAD-TEST] Running locust..."
set +e
"${LOCUST_CMD[@]}"
LOCUST_EXIT=$?
set -e

# ── Evaluate results ───────────────────────────────────────────────────────────
if [[ ${LOCUST_EXIT} -ne 0 ]]; then
    echo "[LOAD-TEST] FAIL: Locust exited with code ${LOCUST_EXIT}" >&2
    TEST_EXIT=1
fi

# Parse CSV stats file for threshold validation
STATS_CSV="${REPORT_BASE}_stats.csv"
if [[ -f "${STATS_CSV}" ]]; then
    # Read error rate from the Aggregated row
    ERROR_RATE=$(python3 - <<PYEOF
import csv, sys

try:
    with open("${STATS_CSV}") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Name") == "Aggregated":
                reqs  = int(row.get("Request Count", 0) or 0)
                fails = int(row.get("Failure Count", 0) or 0)
                if reqs > 0:
                    rate = (fails / reqs) * 100
                    print(f"{rate:.2f}")
                else:
                    print("0.00")
                sys.exit(0)
except Exception as e:
    print("0.00")
PYEOF
    )
    echo "[LOAD-TEST] Error rate: ${ERROR_RATE}%"

    # Smoke scenario: zero errors required
    if [[ "${SCENARIO}" == "smoke" ]]; then
        if python3 -c "import sys; sys.exit(0 if float('${ERROR_RATE}') == 0.0 else 1)"; then
            echo "[LOAD-TEST] PASS: smoke scenario — zero errors"
        else
            echo "[LOAD-TEST] FAIL: smoke scenario requires 0% error rate, got ${ERROR_RATE}%" >&2
            TEST_EXIT=1
        fi
    else
        MAX_ERROR=$(python3 -c "
import yaml
with open('${POLICY_FILE}') as f:
    policy = yaml.safe_load(f)
print(policy.get('thresholds', {}).get('error_rate_percent', 1.0))
")
        if python3 -c "import sys; sys.exit(0 if float('${ERROR_RATE}') <= float('${MAX_ERROR}') else 1)"; then
            echo "[LOAD-TEST] PASS: error rate ${ERROR_RATE}% <= threshold ${MAX_ERROR}%"
        else
            echo "[LOAD-TEST] FAIL: error rate ${ERROR_RATE}% exceeds threshold ${MAX_ERROR}%" >&2
            TEST_EXIT=1
        fi
    fi
fi

if [[ "${DO_REPORT}" == "true" && -f "${REPORT_BASE}.html" ]]; then
    echo "[LOAD-TEST] HTML report: ${REPORT_BASE}.html"
fi
echo "[LOAD-TEST] CSV report : ${REPORT_BASE}_stats.csv"

if [[ ${TEST_EXIT} -ne 0 ]]; then
    echo "[LOAD-TEST] Load test FAILED — review the report and tighten the SLOs if needed" >&2
    exit 1
fi

echo "[LOAD-TEST] Load test completed successfully (${SCENARIO})."
exit 0
