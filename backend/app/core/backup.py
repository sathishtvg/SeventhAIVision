"""Database backup utility — pg_dump to compressed file with rotation.

Shared by:
  - scheduler_main.py  (daily automated run)
  - routers/system.py  (on-demand trigger via POST /api/v1/system/backup)

Environment variables:
  BACKUP_ROOT       destination directory  (default /data/backups)
  BACKUP_KEEP_DAILY number of daily backups to retain (default 7)
  POSTGRES_HOST     pg_dump target host   (default postgres)
  POSTGRES_PORT     pg_dump target port   (default 5432)
  POSTGRES_USER     pg_dump superuser     (default postgres)
  POSTGRES_PASSWORD passed via PGPASSWORD (sourced from environment)
  POSTGRES_DB       database name         (default seventh_ai_vision)
"""

import asyncio
import gzip
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_ROOT     = Path(os.environ.get("BACKUP_ROOT", "/data/backups"))
BACKUP_KEEP_DAILY = int(os.environ.get("BACKUP_KEEP_DAILY", "7"))


async def run_database_backup() -> dict:
    """Run pg_dump, gzip-compress the output, rotate old files.

    Returns dict with filename, size_bytes, timestamp on success.
    Raises RuntimeError on pg_dump failure or suspiciously small output.
    """
    daily_dir = BACKUP_ROOT / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    outfile  = daily_dir / f"backup_{now_str}.sql.gz"

    # Inherit full environment so PGPASSWORD is picked up automatically
    env = os.environ.copy()

    pg_host = env.get("POSTGRES_HOST", "postgres")
    pg_port = env.get("POSTGRES_PORT", "5432")
    pg_user = env.get("POSTGRES_USER", "postgres")
    pg_db   = env.get("POSTGRES_DB",   "seventh_ai_vision")

    logger.info("backup: starting pg_dump → %s", outfile)

    proc = await asyncio.create_subprocess_exec(
        "pg_dump",
        "-h", pg_host,
        "-p", pg_port,
        "-U", pg_user,
        "-d", pg_db,
        "--format=plain",
        "--no-owner",
        "--no-acl",
        "--no-password",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        msg = stderr.decode(errors="replace")[:500]
        logger.error("backup: pg_dump exited %d: %s", proc.returncode, msg)
        raise RuntimeError(f"pg_dump failed (exit {proc.returncode}): {msg}")

    with gzip.open(outfile, "wb", compresslevel=6) as fh:
        fh.write(stdout)

    size = outfile.stat().st_size
    if size < 1024:
        outfile.unlink(missing_ok=True)
        raise RuntimeError(f"Backup file suspiciously small ({size} bytes) — aborting")

    # Rotate: keep only the N most-recent daily backups
    all_backups = sorted(daily_dir.glob("backup_*.sql.gz"), reverse=True)
    for old in all_backups[BACKUP_KEEP_DAILY:]:
        old.unlink(missing_ok=True)
        logger.info("backup: rotated %s", old.name)

    logger.info("backup: complete %s (%d bytes)", outfile.name, size)
    return {
        "filename":   outfile.name,
        "size_bytes": size,
        "timestamp":  now_str,
    }


def list_backups() -> list[dict]:
    """Return metadata for all existing daily backup files, newest first."""
    daily_dir = BACKUP_ROOT / "daily"
    if not daily_dir.exists():
        return []
    files = sorted(daily_dir.glob("backup_*.sql.gz"), reverse=True)
    return [
        {
            "filename":   f.name,
            "size_bytes": f.stat().st_size,
            "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
        }
        for f in files
    ]
