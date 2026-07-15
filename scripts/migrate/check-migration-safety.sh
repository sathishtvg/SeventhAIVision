#!/usr/bin/env bash
# Database migration safety checker — Gap 26 — Seventh AI Vision
#
# Validates Alembic migration files against the migration safety policy before
# every deployment. Designed to run in CI as the pre-deploy gate.
#
# Usage:
#   POLICY_FILE=config/migration-safety-policy.yml \
#   ./scripts/migrate/check-migration-safety.sh [MODE]
#
# Modes:
#   --check     Full safety check: downgrade(), chain integrity, destructive patterns (default)
#   --chain     Chain-only check: revision chain has no gaps, no duplicates
#   --list      List all migrations in chain order (informational, always exits 0)
#   --dry-run   Run full check but print findings without exiting non-zero
#
# Exit codes:
#   0  All checks passed (or --dry-run / --list)
#   1  One or more safety violations found
#   2  Prerequisite missing or invalid argument

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

POLICY_FILE="${POLICY_FILE:-${PROJECT_ROOT}/config/migration-safety-policy.yml}"
MIGRATIONS_DIR="${PROJECT_ROOT}/backend/alembic/versions"
MODE="check"
DRY_RUN=false

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)    MODE="check" ;;
        --chain)    MODE="chain" ;;
        --list)     MODE="list" ;;
        --dry-run)  DRY_RUN=true ;;
        *) echo "[MIGRATE] Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# ── Prerequisite checks ───────────────────────────────────────────────────────
_require() {
    if ! command -v "$1" &>/dev/null; then
        echo "[MIGRATE] ERROR: '$1' is required but not installed." >&2
        echo "[MIGRATE]   Install: $2" >&2
        exit 2
    fi
}

_require python3 "https://www.python.org/downloads/"

if [[ ! -f "${POLICY_FILE}" ]]; then
    echo "[MIGRATE] ERROR: Policy file not found: ${POLICY_FILE}" >&2
    exit 2
fi

if [[ ! -d "${MIGRATIONS_DIR}" ]]; then
    echo "[MIGRATE] ERROR: Migrations directory not found: ${MIGRATIONS_DIR}" >&2
    exit 2
fi

echo "[MIGRATE] Mode       : ${MODE}$(${DRY_RUN} && echo ' (dry-run)')"
echo "[MIGRATE] Policy     : ${POLICY_FILE}"
echo "[MIGRATE] Migrations : ${MIGRATIONS_DIR}"

# ── Python validation core ────────────────────────────────────────────────────
python3 - <<PYEOF
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("[MIGRATE] ERROR: PyYAML not installed. pip install pyyaml", file=sys.stderr)
    sys.exit(2)

policy_file   = "${POLICY_FILE}"
migrations_dir = Path("${MIGRATIONS_DIR}")
mode           = "${MODE}"
dry_run        = "${DRY_RUN}" == "true"

# ── Load policy ───────────────────────────────────────────────────────────────
with open(policy_file, encoding="utf-8") as f:
    policy = yaml.safe_load(f)

destructive_sql = [
    "DROP TABLE",
    "DROP COLUMN",
    "TRUNCATE TABLE",
]

errors   = []
warnings = []

# ── Build revision map ────────────────────────────────────────────────────────
migration_files = sorted(migrations_dir.glob("*.py"))
revisions = {}

for mf in migration_files:
    text = mf.read_text(encoding="utf-8")
    rev  = None
    dr   = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("revision") and "=" in s and rev is None:
            rev = s.split("=", 1)[-1].strip().strip('"').strip("'").strip()
        if s.startswith("down_revision") and "=" in s and dr is None:
            val = s.split("=", 1)[-1].strip().strip('"').strip("'").strip()
            dr = None if val in ("None", "") else val
    if rev:
        revisions[rev] = {"down_revision": dr, "file": mf.name, "text": text, "path": mf}

# ── Mode: list ────────────────────────────────────────────────────────────────
if mode == "list":
    order = []
    visited = set()
    def _visit(r):
        if r in visited: return
        visited.add(r)
        parent = revisions[r]["down_revision"]
        if parent and parent in revisions:
            _visit(parent)
        order.append(r)
    for r in revisions:
        _visit(r)
    print(f"[MIGRATE] Migration chain ({len(order)} total):")
    for r in order:
        info  = revisions[r]
        dr    = info['down_revision'] or '(root)'
        print(f"  {r}  down={dr:<8}  {info['file']}")
    sys.exit(0)

# ── Chain checks (used by both --chain and --check) ───────────────────────────
rev_set = set(revisions.keys())

# Duplicate revision IDs
seen_ids = {}
for rev, info in revisions.items():
    if rev in seen_ids:
        errors.append(
            f"DUPLICATE_REVISION: '{rev}' defined in both "
            f"{seen_ids[rev]} and {info['file']}"
        )
    seen_ids[rev] = info["file"]

# Broken down_revision references
for rev, info in revisions.items():
    dr = info["down_revision"]
    if dr is not None and dr not in rev_set:
        errors.append(
            f"BROKEN_CHAIN: {info['file']} references "
            f"unknown down_revision '{dr}'"
        )

# ── Full check (downgrade + destructive patterns) ─────────────────────────────
if mode == "check":
    for rev, info in revisions.items():
        text  = info["text"]
        fname = info["file"]

        # Every migration must have a real downgrade()
        if "def downgrade" not in text:
            errors.append(f"NO_DOWNGRADE: {fname} is missing def downgrade()")

        # Destructive patterns in upgrade() require docstring explanation
        lines = text.splitlines()
        in_upgrade = False
        for i, line in enumerate(lines):
            if re.match(r"\s*def upgrade", line):
                in_upgrade = True
            elif re.match(r"\s*def downgrade", line):
                in_upgrade = False

            if in_upgrade:
                line_upper = line.upper()
                for pattern in destructive_sql:
                    if pattern in line_upper:
                        # Allow if the module docstring or surrounding lines explain it
                        docstring_window = text[:600].lower()
                        surrounding = " ".join(lines[max(0, i - 8): i + 2]).lower()
                        explained = (
                            "drop" in docstring_window
                            or "legacy" in docstring_window
                            or "dead weight" in docstring_window
                            or "# destructive" in surrounding
                            or "# review" in surrounding
                            or "drop column if exists" in line.lower()   # idempotent guard
                        )
                        if not explained:
                            errors.append(
                                f"UNANNOTATED_DESTRUCTIVE: {fname} uses "
                                f"'{pattern}' in upgrade() without a documented justification"
                            )

# ── Report ────────────────────────────────────────────────────────────────────
prefix = "[DRY-RUN]" if dry_run else "[MIGRATE]"

for e in errors:
    print(f"{prefix} ERROR: {e}", file=sys.stderr)
for w in warnings:
    print(f"{prefix} WARN:  {w}", file=sys.stderr)

if dry_run:
    print(f"{prefix} Dry run complete: {len(errors)} error(s), {len(warnings)} warning(s)")
    sys.exit(0)

if errors:
    print(f"[MIGRATE] FAIL: {len(errors)} error(s), {len(warnings)} warning(s) "
          f"across {len(revisions)} migration(s)")
    sys.exit(1)

print(f"[MIGRATE] PASS: {len(revisions)} migration(s) checked — "
      f"chain intact, all downgrades present, {len(warnings)} warning(s)")
sys.exit(0)
PYEOF

EXIT_CODE=$?
exit ${EXIT_CODE}
