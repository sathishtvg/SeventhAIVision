#!/usr/bin/env bash
# PostgreSQL backup script — Gap 25 — Seventh AI Vision
#
# Creates an encrypted pg_dump of the seventh_ai_vision database.
# Applies retention policy by pruning old backups after a successful new backup.
#
# Usage:
#   POSTGRES_HOST=postgres POSTGRES_DB=seventh_ai_vision \
#   POSTGRES_USER=svc_app POSTGRES_PASSWORD=secret \
#   BACKUP_LOCAL_PATH=/data/backups \
#   BACKUP_AGE_PUBLIC_KEY=age1... \
#   ./scripts/backup/backup-postgres.sh
#
# Optional flags:
#   --dry-run       Print what would be done without writing any files
#   --no-encrypt    Skip age encryption (DEVELOPMENT ONLY — never use in prod)
#   --skip-retention  Skip old backup pruning (useful when running manually)
#
# Exit codes:
#   0 — backup completed and encrypted successfully
#   1 — backup failed
#   2 — prerequisite missing

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

POLICY_FILE="${POLICY_FILE:-${PROJECT_ROOT}/config/backup-policy.yml}"
BACKUP_LOCAL_PATH="${BACKUP_LOCAL_PATH:-/data/backups}"
POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-seventh_ai_vision}"
POSTGRES_USER="${POSTGRES_USER:-svc_app}"
DRY_RUN=false
NO_ENCRYPT=false
SKIP_RETENTION=false
BACKUP_EXIT=0

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)       DRY_RUN=true ;;
        --no-encrypt)    NO_ENCRYPT=true ;;
        --skip-retention) SKIP_RETENTION=true ;;
        *) echo "[BACKUP] Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# ── Prerequisite checks ───────────────────────────────────────────────────────
_require() {
    if ! command -v "$1" &>/dev/null; then
        echo "[BACKUP] ERROR: '$1' is required but not installed." >&2
        echo "[BACKUP]   Install: $2" >&2
        exit 2
    fi
}

_require pg_dump "apt-get install -y postgresql-client"
_require python3 "https://www.python.org/downloads/"

if [[ "${NO_ENCRYPT}" == "false" ]]; then
    _require age "https://github.com/FiloSottile/age#installation"
fi

if [[ ! -f "${POLICY_FILE}" ]]; then
    echo "[BACKUP] ERROR: Policy file not found: ${POLICY_FILE}" >&2
    exit 2
fi

# ── Read retention config from policy ─────────────────────────────────────────
read -r DAILY_KEEP WEEKLY_KEEP MONTHLY_KEEP MIN_KEEP < <(python3 - <<PYEOF
import yaml
with open("${POLICY_FILE}") as f:
    policy = yaml.safe_load(f)
r = policy.get("retention", {})
print(
    r.get("daily_backups_kept", 7),
    r.get("weekly_backups_kept", 4),
    r.get("monthly_backups_kept", 3),
    r.get("min_backups_always_kept", 3),
)
PYEOF
)

# ── File naming ────────────────────────────────────────────────────────────────
BACKUP_DATE="$(date -u +%Y%m%d)"
BACKUP_TIME="$(date -u +%H%M%S)"
DUMP_FILENAME="postgres-full-${BACKUP_DATE}-${BACKUP_TIME}.dump"
MANIFEST_FILE="${BACKUP_LOCAL_PATH}/backup-manifest.json"

if [[ "${NO_ENCRYPT}" == "true" ]]; then
    FINAL_FILENAME="${DUMP_FILENAME}"
    echo "[BACKUP] WARNING: Encryption disabled — FOR DEVELOPMENT USE ONLY" >&2
else
    FINAL_FILENAME="${DUMP_FILENAME}.age"
fi

DUMP_PATH="${BACKUP_LOCAL_PATH}/${DUMP_FILENAME}"
FINAL_PATH="${BACKUP_LOCAL_PATH}/${FINAL_FILENAME}"

echo "[BACKUP] Target   : ${POSTGRES_USER}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
echo "[BACKUP] Output   : ${FINAL_PATH}"
echo "[BACKUP] Retention: daily=${DAILY_KEEP}, weekly=${WEEKLY_KEEP}, monthly=${MONTHLY_KEEP}"

if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[BACKUP] DRY RUN — no files written"
    exit 0
fi

# ── Create backup directory ────────────────────────────────────────────────────
mkdir -p "${BACKUP_LOCAL_PATH}"

# ── pg_dump ───────────────────────────────────────────────────────────────────
echo "[BACKUP] Running pg_dump..."
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
    --host="${POSTGRES_HOST}" \
    --port="${POSTGRES_PORT}" \
    --username="${POSTGRES_USER}" \
    --dbname="${POSTGRES_DB}" \
    --format=custom \
    --compress=9 \
    --no-password \
    --file="${DUMP_PATH}"

DUMP_SIZE=$(wc -c < "${DUMP_PATH}")
echo "[BACKUP] pg_dump complete — ${DUMP_SIZE} bytes"

# ── Encrypt with age ───────────────────────────────────────────────────────────
if [[ "${NO_ENCRYPT}" == "false" ]]; then
    AGE_PUBLIC_KEY="${BACKUP_AGE_PUBLIC_KEY:-}"
    if [[ -z "${AGE_PUBLIC_KEY}" ]]; then
        echo "[BACKUP] ERROR: BACKUP_AGE_PUBLIC_KEY is not set" >&2
        rm -f "${DUMP_PATH}"
        exit 1
    fi

    echo "[BACKUP] Encrypting with age..."
    age --recipient "${AGE_PUBLIC_KEY}" --output "${FINAL_PATH}" "${DUMP_PATH}"
    rm -f "${DUMP_PATH}"    # Remove plaintext dump immediately after encryption
    echo "[BACKUP] Encrypted: ${FINAL_PATH}"
else
    # No encryption — move dump to final path as-is
    mv "${DUMP_PATH}" "${FINAL_PATH}"
fi

FINAL_SIZE=$(wc -c < "${FINAL_PATH}")
echo "[BACKUP] Final backup size: ${FINAL_SIZE} bytes"

# ── Compute checksum ─────────────────────────────────────────────────────────
CHECKSUM=$(python3 -c "
import hashlib
with open('${FINAL_PATH}', 'rb') as f:
    h = hashlib.sha256(f.read())
print(h.hexdigest())
")
echo "[BACKUP] SHA256: ${CHECKSUM}"

# ── Write manifest entry ──────────────────────────────────────────────────────
python3 - <<PYEOF
import json, os
from datetime import datetime, timezone

manifest_path = "${MANIFEST_FILE}"
entry = {
    "filename": "${FINAL_FILENAME}",
    "database": "${POSTGRES_DB}",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "size_bytes": ${FINAL_SIZE},
    "checksum_sha256": "${CHECKSUM}",
    "encrypted": ${NO_ENCRYPT} == "false" if False else True,
    "pg_dump_format": "custom",
}

# Load or initialise manifest
if os.path.exists(manifest_path):
    with open(manifest_path) as f:
        manifest = json.load(f)
else:
    manifest = {"backups": []}

manifest["backups"].append(entry)

with open(manifest_path, "w") as f:
    json.dump(manifest, f, indent=2)
print(f"[BACKUP] Manifest updated: {manifest_path}")
PYEOF

# ── Retention pruning ─────────────────────────────────────────────────────────
if [[ "${SKIP_RETENTION}" == "false" ]]; then
    echo "[BACKUP] Applying retention policy..."
    python3 - <<PYEOF
import json, os, pathlib
from datetime import datetime, timezone

backup_dir   = pathlib.Path("${BACKUP_LOCAL_PATH}")
min_keep     = ${MIN_KEEP}
daily_keep   = ${DAILY_KEEP}

# Find all backup files (sorted newest first)
backups = sorted(
    [f for f in backup_dir.glob("postgres-full-*.dump*")],
    key=lambda f: f.stat().st_mtime,
    reverse=True,
)

to_delete = backups[max(daily_keep, min_keep):]
for f in to_delete:
    print(f"[BACKUP] Pruning old backup: {f.name}")
    f.unlink()

kept = len(backups) - len(to_delete)
print(f"[BACKUP] Retention: kept {kept}, pruned {len(to_delete)}")
PYEOF
fi

echo "[BACKUP] Backup completed successfully: ${FINAL_FILENAME}"
exit 0
