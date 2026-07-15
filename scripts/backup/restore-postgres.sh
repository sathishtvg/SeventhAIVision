#!/usr/bin/env bash
# PostgreSQL restore script — Gap 25 — Seventh AI Vision
#
# Decrypts and restores a pg_dump backup, then verifies the database health.
#
# Usage:
#   BACKUP_FILE=/data/backups/postgres-full-20260701-020005.dump.age \
#   POSTGRES_HOST=postgres POSTGRES_DB=seventh_ai_vision \
#   POSTGRES_USER=svc_app POSTGRES_PASSWORD=secret \
#   SOPS_AGE_KEY_FILE=~/.config/sops/age/key.txt \
#   ./scripts/backup/restore-postgres.sh
#
# Flags:
#   --dry-run    Decrypt and verify the backup file without restoring
#   --list       List available backups in BACKUP_LOCAL_PATH
#   --verify     Verify checksums in the backup manifest without restoring
#
# Exit codes:
#   0 — restore completed and health checks passed
#   1 — restore failed or health check failed
#   2 — prerequisite missing or argument error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

POLICY_FILE="${POLICY_FILE:-${PROJECT_ROOT}/config/backup-policy.yml}"
BACKUP_FILE="${BACKUP_FILE:-}"
BACKUP_LOCAL_PATH="${BACKUP_LOCAL_PATH:-/data/backups}"
POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-seventh_ai_vision}"
POSTGRES_USER="${POSTGRES_USER:-svc_app}"
API_URL="${API_URL:-http://api:8000}"
DRY_RUN=false
DO_LIST=false
DO_VERIFY=false

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true ;;
        --list)    DO_LIST=true ;;
        --verify)  DO_VERIFY=true ;;
        *) echo "[RESTORE] Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# ── Prerequisite checks ───────────────────────────────────────────────────────
_require() {
    if ! command -v "$1" &>/dev/null; then
        echo "[RESTORE] ERROR: '$1' is required but not installed." >&2
        echo "[RESTORE]   Install: $2" >&2
        exit 2
    fi
}

_require python3 "https://www.python.org/downloads/"

# ── Mode: list ────────────────────────────────────────────────────────────────
if [[ "${DO_LIST}" == "true" ]]; then
    echo "[RESTORE] Available backups in ${BACKUP_LOCAL_PATH}:"
    MANIFEST="${BACKUP_LOCAL_PATH}/backup-manifest.json"
    if [[ -f "${MANIFEST}" ]]; then
        python3 -c "
import json
with open('${MANIFEST}') as f:
    m = json.load(f)
backups = sorted(m.get('backups', []), key=lambda b: b['created_at'], reverse=True)
for b in backups:
    size_mb = b['size_bytes'] / 1024 / 1024
    enc = '(encrypted)' if b.get('encrypted') else '(plaintext)'
    print(f\"  {b['filename']}  {b['created_at'][:19]}  {size_mb:.1f} MB  {enc}\")
print(f'Total: {len(backups)} backup(s)')
"
    else
        ls -lht "${BACKUP_LOCAL_PATH}"/*.dump* 2>/dev/null || echo "  (no backups found)"
    fi
    exit 0
fi

# ── Mode: verify checksums ────────────────────────────────────────────────────
if [[ "${DO_VERIFY}" == "true" ]]; then
    MANIFEST="${BACKUP_LOCAL_PATH}/backup-manifest.json"
    if [[ ! -f "${MANIFEST}" ]]; then
        echo "[RESTORE] ERROR: No backup manifest found at ${MANIFEST}" >&2
        exit 1
    fi

    echo "[RESTORE] Verifying backup checksums..."
    python3 - <<PYEOF
import json, hashlib, sys, os
from pathlib import Path

backup_dir = Path("${BACKUP_LOCAL_PATH}")
with open("${BACKUP_LOCAL_PATH}/backup-manifest.json") as f:
    manifest = json.load(f)

errors = 0
for entry in manifest.get("backups", []):
    fpath = backup_dir / entry["filename"]
    if not fpath.exists():
        print(f"[RESTORE] MISSING: {entry['filename']}", file=sys.stderr)
        errors += 1
        continue
    with open(fpath, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    if actual != entry.get("checksum_sha256"):
        print(f"[RESTORE] CORRUPT: {entry['filename']} — checksum mismatch", file=sys.stderr)
        errors += 1
    else:
        print(f"[RESTORE] OK: {entry['filename']}")

if errors:
    print(f"[RESTORE] {errors} file(s) failed verification", file=sys.stderr)
    sys.exit(1)
print("[RESTORE] All checksums verified.")
PYEOF
    exit $?
fi

# ── Mode: restore ─────────────────────────────────────────────────────────────
_require pg_restore "apt-get install -y postgresql-client"

if [[ -z "${BACKUP_FILE}" ]]; then
    echo "[RESTORE] ERROR: Set BACKUP_FILE to the backup to restore." >&2
    echo "[RESTORE]   Use --list to see available backups." >&2
    exit 2
fi

if [[ ! -f "${BACKUP_FILE}" ]]; then
    echo "[RESTORE] ERROR: Backup file not found: ${BACKUP_FILE}" >&2
    exit 1
fi

BASENAME="$(basename "${BACKUP_FILE}")"
echo "[RESTORE] Source  : ${BACKUP_FILE}"
echo "[RESTORE] Target  : ${POSTGRES_USER}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"

# ── Decrypt (if .age extension) ───────────────────────────────────────────────
DUMP_FILE="${BACKUP_FILE}"

if [[ "${BACKUP_FILE}" == *.age ]]; then
    _require age "https://github.com/FiloSottile/age#installation"

    AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-}"
    if [[ -z "${AGE_KEY_FILE}" && -z "${SOPS_AGE_KEY:-}" ]]; then
        echo "[RESTORE] ERROR: Set SOPS_AGE_KEY_FILE (path to private key) for decryption." >&2
        exit 2
    fi

    DUMP_FILE="/tmp/restore-$(date +%s).dump"
    echo "[RESTORE] Decrypting with age..."

    if [[ -n "${SOPS_AGE_KEY:-}" ]]; then
        # Inline key (CI environment)
        echo "${SOPS_AGE_KEY}" | age --decrypt --identity /dev/stdin \
            --output "${DUMP_FILE}" "${BACKUP_FILE}"
    else
        age --decrypt --identity "${AGE_KEY_FILE}" \
            --output "${DUMP_FILE}" "${BACKUP_FILE}"
    fi
    echo "[RESTORE] Decryption complete."
    DECRYPT_SIZE=$(wc -c < "${DUMP_FILE}")
    echo "[RESTORE] Decrypted size: ${DECRYPT_SIZE} bytes"
fi

if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[RESTORE] DRY RUN — decryption succeeded; skipping pg_restore"
    [[ "${DUMP_FILE}" == /tmp/* ]] && rm -f "${DUMP_FILE}"
    exit 0
fi

# ── Restore ────────────────────────────────────────────────────────────────────
echo "[RESTORE] Running pg_restore..."
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_restore \
    --host="${POSTGRES_HOST}" \
    --port="${POSTGRES_PORT}" \
    --username="${POSTGRES_USER}" \
    --dbname="${POSTGRES_DB}" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --exit-on-error \
    "${DUMP_FILE}"

# Clean up decrypted temp file immediately after restore
[[ "${DUMP_FILE}" == /tmp/* ]] && rm -f "${DUMP_FILE}"
echo "[RESTORE] pg_restore complete."

# ── Post-restore health verification ─────────────────────────────────────────
echo "[RESTORE] Running post-restore health checks..."

# 1. Basic row count — at least one tenant must exist
TENANT_COUNT=$(PGPASSWORD="${POSTGRES_PASSWORD:-}" psql \
    --host="${POSTGRES_HOST}" \
    --port="${POSTGRES_PORT}" \
    --username="${POSTGRES_USER}" \
    --dbname="${POSTGRES_DB}" \
    --tuples-only \
    --no-align \
    --command="SELECT COUNT(*) FROM tenants;" 2>/dev/null | tr -d '[:space:]')

if [[ -z "${TENANT_COUNT}" || "${TENANT_COUNT}" -lt 1 ]]; then
    echo "[RESTORE] WARN: tenants table is empty after restore — expected at least 1 row" >&2
else
    echo "[RESTORE] PASS: tenants table has ${TENANT_COUNT} row(s)"
fi

# 2. API health check (if API is reachable)
_require curl "apt-get install -y curl"
HTTP_STATUS="$(curl -sf -o /dev/null -w "%{http_code}" \
    --max-time 5 "${API_URL}/health" 2>/dev/null || echo "000")"

if [[ "${HTTP_STATUS}" == "200" ]]; then
    echo "[RESTORE] PASS: API health check returned HTTP 200"
elif [[ "${HTTP_STATUS}" == "000" ]]; then
    echo "[RESTORE] INFO: API not reachable at ${API_URL} — skipping health check"
else
    echo "[RESTORE] WARN: API health check returned HTTP ${HTTP_STATUS}" >&2
fi

echo "[RESTORE] Restore completed successfully from: ${BASENAME}"
exit 0
