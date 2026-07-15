# Disaster Recovery Runbook — Seventh AI Vision

**Audience:** System administrator, DevOps  
**Last updated:** 2026-06-30  
**RTO target:** < 2 hours | **RPO target:** < 24 hours (daily backup)

---

## 1. Backup Overview

The `scheduler` container runs `pg_dump` once per 24-hour maintenance cycle and stores
gzip-compressed SQL dumps in the `backups_data` Docker named volume at:

```
/data/backups/daily/backup_YYYYMMDD_HHMMSS.sql.gz
```

The last **7 daily backups** are retained by default (configurable via `BACKUP_KEEP_DAILY`).

### On-demand backup

```bash
# Via API (admin JWT required)
curl -X POST https://<host>/api/v1/system/backup \
  -H "Authorization: Bearer <admin_token>"

# Or directly inside the scheduler container
docker exec docker-scheduler-1 \
  python -c "import asyncio; from app.core.backup import run_database_backup; asyncio.run(run_database_backup())"
```

### List existing backups

```bash
curl https://<host>/api/v1/system/backups \
  -H "Authorization: Bearer <admin_token>"
```

---

## 2. Backup File Extraction

The backup volume is a Docker named volume. To copy files out:

```bash
# Copy all daily backups to the current host directory
docker run --rm \
  -v docker_backups_data:/data/backups \
  -v "$(pwd)":/out \
  alpine \
  sh -c "cp /data/backups/daily/backup_*.sql.gz /out/"
```

To inspect a backup without restoring:

```bash
# Verify gzip integrity
gzip -t backup_YYYYMMDD_HHMMSS.sql.gz && echo "OK"

# Count tables in the dump
zcat backup_YYYYMMDD_HHMMSS.sql.gz | grep -c "^CREATE TABLE"
```

---

## 3. Full Restore Procedure

### 3.1 Prerequisites

- Docker Compose is available on the recovery host
- The backup file is accessible (on host or in the volume)
- `POSTGRES_PASSWORD` / `POSTGRES_USER` / `POSTGRES_DB` match the original deployment

### 3.2 Steps

```bash
# Step 1 — bring up only the database (no migrations yet)
cd docker/
docker compose up -d postgres
docker compose exec postgres pg_isready -U postgres   # wait until healthy

# Step 2 — drop existing data and recreate the empty database
docker compose exec postgres \
  psql -U postgres -c "DROP DATABASE IF EXISTS seventh_ai_vision;"
docker compose exec postgres \
  psql -U postgres -c "CREATE DATABASE seventh_ai_vision OWNER postgres;"

# Step 3 — copy the backup file into the container and restore
docker cp backup_YYYYMMDD_HHMMSS.sql.gz docker-postgres-1:/tmp/

docker compose exec postgres \
  sh -c "zcat /tmp/backup_YYYYMMDD_HHMMSS.sql.gz | psql -U postgres -d seventh_ai_vision"

# Step 4 — verify row counts on key tables
docker compose exec postgres \
  psql -U postgres -d seventh_ai_vision -c "
    SELECT relname AS table, n_live_tup AS rows
    FROM pg_stat_user_tables
    WHERE relname IN ('tenants','users','cameras','alerts','incidents','audit_logs')
    ORDER BY relname;
  "

# Step 5 — start the rest of the stack
docker compose up -d

# Step 6 — run the smoke test
pwsh scripts/smoke-test.ps1
```

### 3.3 Restore from a specific file

To restore from a file that is NOT in the Docker volume, copy it first:

```bash
# From S3-compatible store (if MinIO/S3 backup offloading is configured)
docker run --rm \
  -v docker_backups_data:/data/backups \
  minio/mc:latest \
  mc cp s3/backups/backup_YYYYMMDD.sql.gz /data/backups/daily/

# Then proceed from Step 2 above
```

---

## 4. Point-in-Time Recovery (PITR)

The default setup does **not** configure WAL archiving. If PITR is required:

1. Enable `wal_level = replica` and `archive_mode = on` in `postgres.Dockerfile`
2. Configure `archive_command` to push WAL segments to an off-host S3 bucket
3. Use `pg_basebackup` + WAL replay for granular point-in-time recovery

This is deferred to a future milestone. Current RPO = last daily backup (~24 h).

---

## 5. Data Loss Matrix

| Scenario | Affected data | Recovery |
|----------|---------------|----------|
| Single container restart | None — Postgres data in named volume | Auto-recover on restart |
| `backups_data` volume corrupted | Backup files only; live DB intact | Re-run `POST /api/v1/system/backup` |
| `postgres_data` volume corrupted | All live data since last backup | Restore from `backups_data` (§3) |
| Host machine destroyed | All volumes | Restore from off-host backup copy (§4) |
| Tenant data accidentally deleted | Tenant rows only | Selective restore from dump using `psql -c "COPY ..."` |

---

## 6. Off-Host Backup Offloading (Recommended for Production)

Run this after each successful backup to push to object storage:

```bash
# MinIO / S3 (adjust endpoint and bucket)
docker run --rm \
  -v docker_backups_data:/data/backups \
  minio/mc:latest sh -c "
    mc alias set store http://minio:9000 minioadmin minioadmin &&
    mc cp /data/backups/daily/backup_$(date +%Y%m%d)*.sql.gz store/backups/
  "
```

Automate this in a weekly `cron` on the host or as a second scheduler job.

---

## 7. Security Notes

- `PGPASSWORD` is the **superuser** password (`POSTGRES_PASSWORD`), not the app-user password. It is passed via environment variable only, never written to disk.
- Backup files contain full schema + data in plaintext SQL (gzip-compressed). Treat them as sensitive — apply filesystem permissions (`chmod 600`) when extracting to host.
- Access to `POST /api/v1/system/backup` requires the `settings:write` permission (admin+).

---

## 8. Contacts

| Role | Action |
|------|--------|
| On-call admin | Initiate restore, validate row counts |
| DBA / super-admin | Cross-tenant data verification after restore |
| Seventh AI Vision Support | `api@seventh.ai` for backup procedure questions |
