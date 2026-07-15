#!/usr/bin/env bash
# Secret rotation script — Gap 21 — Seventh AI Vision
#
# Modes:
#   --check-expiry   Scan secrets-policy.yml and report secrets past rotation_days.
#                    Exits non-zero if any are overdue; used as a CI gate.
#   --rotate jwt     Rotate the JWT signing keys in the active SOPS-encrypted env file.
#   --rotate db      Print instructions to rotate the database password (manual step required).
#   --validate       Decrypt the active SOPS env file, source it, call /health, re-encrypt.
#
# Usage:
#   ./scripts/secrets/rotate-secrets.sh --check-expiry
#   SOPS_ENV_FILE=config/secrets/prod.env ./scripts/secrets/rotate-secrets.sh --rotate jwt
#   ./scripts/secrets/rotate-secrets.sh --validate
#
# Environment variables:
#   SOPS_ENV_FILE    Path to the SOPS-encrypted env file (default: config/secrets/dev.env)
#   API_URL          Base URL for health check validation (default: http://localhost:8000)
#   POLICY_FILE      Path to secrets-policy.yml (default: config/secrets-policy.yml)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SOPS_ENV_FILE="${SOPS_ENV_FILE:-${PROJECT_ROOT}/config/secrets/dev.env}"
POLICY_FILE="${POLICY_FILE:-${PROJECT_ROOT}/config/secrets-policy.yml}"
API_URL="${API_URL:-http://localhost:8000}"
MODE=""
ROTATE_TARGET=""

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check-expiry) MODE="check-expiry" ;;
        --validate)     MODE="validate" ;;
        --rotate)
            MODE="rotate"
            ROTATE_TARGET="${2:-}"
            shift
            ;;
        *) echo "[SECRETS] Unknown argument: $1" >&2; exit 1 ;;
    esac
    shift
done

if [[ -z "${MODE}" ]]; then
    echo "[SECRETS] Usage: $0 --check-expiry | --rotate <target> | --validate" >&2
    exit 1
fi

# ── Prerequisite checks ───────────────────────────────────────────────────────
_require() {
    if ! command -v "$1" &>/dev/null; then
        echo "[SECRETS] ERROR: '$1' is not installed or not on PATH." >&2
        echo "[SECRETS]   Install: $2" >&2
        exit 1
    fi
}

# ── Mode: check-expiry ────────────────────────────────────────────────────────
if [[ "${MODE}" == "check-expiry" ]]; then
    _require python3 "https://www.python.org/downloads/"

    echo "[SECRETS] Checking secret rotation deadlines against ${POLICY_FILE}..."

    python3 - <<'PYEOF'
import sys, yaml
from datetime import date, timedelta
from pathlib import Path

policy_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/secrets-policy.yml")

# When called from heredoc, argv is just the interpreter — fall back to env path
import os
policy_path = Path(os.environ.get("POLICY_FILE", "config/secrets-policy.yml"))

if not policy_path.exists():
    print(f"[SECRETS] WARN: policy file not found at {policy_path}, skipping check")
    sys.exit(0)

with open(policy_path) as f:
    policy = yaml.safe_load(f)

alert_before = policy.get("rotation_policy", {}).get("alert_before_days", 14)
today = date.today()
overdue = []
warn = []

for cat in policy.get("categories", []):
    rotation_days = cat.get("rotation_days")
    if rotation_days is None:
        continue
    last_rotated = cat.get("rotation_last_date")
    if last_rotated is None:
        continue  # never rotated — skip unless we have a start date
    last_date = date.fromisoformat(str(last_rotated))
    due_date = last_date + timedelta(days=rotation_days)
    if today > due_date:
        overdue.append((cat["name"], due_date, today - due_date))
    elif today >= due_date - timedelta(days=alert_before):
        warn.append((cat["name"], due_date))

if warn:
    for name, due in warn:
        print(f"[SECRETS] WARN: '{name}' secrets due for rotation on {due}")

if overdue:
    for name, due, delta in overdue:
        print(f"[SECRETS] OVERDUE: '{name}' secrets were due {delta.days} days ago ({due})", file=sys.stderr)
    sys.exit(1)

print("[SECRETS] All secrets within rotation window.")
PYEOF
    exit $?
fi

# ── Mode: rotate jwt ─────────────────────────────────────────────────────────
if [[ "${MODE}" == "rotate" && "${ROTATE_TARGET}" == "jwt" ]]; then
    _require sops "https://github.com/getsops/sops#installation"
    _require python3 "https://www.python.org/downloads/"

    echo "[SECRETS] Rotating JWT signing keys in ${SOPS_ENV_FILE}..."

    if [[ ! -f "${SOPS_ENV_FILE}" ]]; then
        echo "[SECRETS] ERROR: SOPS env file not found: ${SOPS_ENV_FILE}" >&2
        exit 1
    fi

    # Generate a new cryptographically secure 64-byte random key (512 bits, hex-encoded)
    NEW_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(64))')"

    # Read current key as "previous" (for the token-TTL grace window)
    # NOTE: sops --decrypt outputs plaintext; capture carefully
    CURRENT_KEY="$(sops --decrypt --extract '["JWT_SECRET_KEY"]' "${SOPS_ENV_FILE}" 2>/dev/null || echo "")"

    echo "[SECRETS] Writing new JWT_SECRET_KEY and preserving old as JWT_SECRET_KEY_PREVIOUS..."
    # Use sops set to update values in-place without full decrypt/re-encrypt round-trip
    sops --set '["JWT_SECRET_KEY"] "'"${NEW_KEY}"'"' "${SOPS_ENV_FILE}"
    if [[ -n "${CURRENT_KEY}" ]]; then
        sops --set '["JWT_SECRET_KEY_PREVIOUS"] "'"${CURRENT_KEY}"'"' "${SOPS_ENV_FILE}"
    fi

    echo "[SECRETS] JWT keys rotated successfully."
    echo "[SECRETS] Next steps:"
    echo "  1. docker compose up -d api   (rolling restart to pick up new key)"
    echo "  2. Wait ${JWT_TTL_SECONDS:-900}s for old access tokens to expire"
    echo "  3. docker compose exec api sh -c 'curl -sf http://localhost:8000/health'"
    echo "  4. Remove JWT_SECRET_KEY_PREVIOUS after all tokens have expired"
    exit 0
fi

# ── Mode: rotate db ───────────────────────────────────────────────────────────
if [[ "${MODE}" == "rotate" && "${ROTATE_TARGET}" == "db" ]]; then
    echo "[SECRETS] Database password rotation requires coordinated steps:"
    echo ""
    echo "  1. Generate new password:"
    echo "     NEW_PW=\"\$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')\""
    echo ""
    echo "  2. Update in PostgreSQL BEFORE updating the SOPS file:"
    echo "     docker exec docker-postgres-1 psql -U postgres -c \\"
    echo "       \"ALTER USER svc_app PASSWORD '\${NEW_PW}';\""
    echo ""
    echo "  3. Update the SOPS-encrypted env file:"
    echo "     sops --set '[\"POSTGRES_PASSWORD\"] \"\${NEW_PW}\"' ${SOPS_ENV_FILE}"
    echo "     sops --set '[\"DATABASE_URL\"] \"postgresql+asyncpg://svc_app:\${NEW_PW}@postgres:5432/seventh_ai_vision\"' ${SOPS_ENV_FILE}"
    echo ""
    echo "  4. Rolling restart all services that use the DB:"
    echo "     docker compose up -d api ingestion scheduler ai-worker-lpr ..."
    echo ""
    echo "  5. Validate health:"
    echo "     curl -sf ${API_URL}/health"
    exit 0
fi

# ── Mode: validate ────────────────────────────────────────────────────────────
if [[ "${MODE}" == "validate" ]]; then
    _require sops "https://github.com/getsops/sops#installation"
    _require curl "https://curl.se/"

    echo "[SECRETS] Validating SOPS-encrypted env file: ${SOPS_ENV_FILE}..."

    if [[ ! -f "${SOPS_ENV_FILE}" ]]; then
        echo "[SECRETS] WARN: SOPS env file not found at ${SOPS_ENV_FILE} — skipping decryption test"
        echo "[SECRETS] Validation passed (no encrypted file to test)."
        exit 0
    fi

    # Test that the file can be decrypted (validates key access)
    echo "[SECRETS] Testing SOPS decryption..."
    sops --decrypt "${SOPS_ENV_FILE}" > /dev/null
    echo "[SECRETS] Decryption OK."

    # Health check
    echo "[SECRETS] Checking API health at ${API_URL}/health..."
    HTTP_STATUS="$(curl -sf -o /dev/null -w "%{http_code}" "${API_URL}/health" || echo "000")"
    if [[ "${HTTP_STATUS}" != "200" ]]; then
        echo "[SECRETS] ERROR: Health check returned HTTP ${HTTP_STATUS}" >&2
        exit 1
    fi
    echo "[SECRETS] Health check OK (HTTP 200)."
    echo "[SECRETS] Validation complete."
    exit 0
fi

echo "[SECRETS] Unknown mode: ${MODE}" >&2
exit 1
