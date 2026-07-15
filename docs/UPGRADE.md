# Upgrade & Rollback Runbook

All services share a single semantic version (`MAJOR.MINOR.PATCH`). Images are tagged per release (e.g. `seventh-ai-vision/api:1.4.2`). Never deploy `:latest` in production.

---

## Pre-Upgrade Checklist

- [ ] Read the release notes for every version between current and target.
- [ ] Identify any breaking API changes (new `/api/v2` paths, removed `/api/v1` endpoints, changed JWT claims).
- [ ] Confirm the Alembic migration chain has no gaps (`alembic history` lists applied revisions).
- [ ] Verify at least **2× the current DB volume** in free disk (migrations on large partitioned tables can be slow and generate WAL).
- [ ] Schedule a maintenance window if the migration touches the `detections` parent table or adds a column to any partitioned child — these lock briefly per partition.
- [ ] Notify connected mobile/desktop clients of the maintenance window (WebSocket will drop; clients reconnect automatically).

---

## Standard Upgrade Procedure

### 1. Backup

```bash
# Stop the scheduler first so no partition maintenance runs mid-backup.
docker compose stop scheduler

# Postgres full backup — adjust path as needed.
docker exec docker-postgres-1 pg_dump \
  -U postgres seventh_ai_vision \
  --format=custom \
  --file=/tmp/backup_$(date +%Y%m%d_%H%M%S).pgdump

docker cp docker-postgres-1:/tmp/backup_*.pgdump ./backups/

# MinIO backup (only if STORAGE_BACKEND=s3)
# mc mirror local/evidence ./backups/evidence/
```

### 2. Pull New Images

```bash
export APP_VERSION=1.5.0   # target version

docker pull seventh-ai-vision/api:${APP_VERSION}
docker pull seventh-ai-vision/ai-worker:${APP_VERSION}
docker pull seventh-ai-vision/frontend:${APP_VERSION}
```

Or if building from source:

```bash
git fetch && git checkout v${APP_VERSION}
docker compose build
```

### 3. Run Migrations

Migrations are run by the `api` container's startup command (`alembic upgrade head`) automatically. To run them manually first (recommended for production):

```bash
docker run --rm \
  --network docker_default \
  -e DATABASE_URL="postgresql+asyncpg://..." \
  seventh-ai-vision/api:${APP_VERSION} \
  sh -c "cd /app/backend && alembic upgrade head"
```

Check the output — every `Running upgrade <rev> -> <rev>` line must appear with no errors before proceeding.

### 4. Deploy

```bash
APP_VERSION=${APP_VERSION} docker compose up -d
```

Docker Compose will restart only containers whose image or config changed.

### 5. Verify

```bash
# Run the smoke test suite against the live stack.
./scripts/smoke-test.ps1

# Manual spot checks:
curl http://localhost:8000/health          # {"status":"ok"}
curl http://localhost:8000/api/v1/system/version  # {"version":"1.5.0",...}
```

### 6. Restart Scheduler

```bash
docker compose start scheduler
```

---

## Rollback Procedure

Use this when the smoke test fails or a critical bug is discovered post-deploy.

### Option A — Image Rollback (no schema changes)

If the new migration added no schema changes (patch releases, frontend-only changes):

```bash
export PREV_VERSION=1.4.2

APP_VERSION=${PREV_VERSION} docker compose up -d

# Confirm old version is running:
curl http://localhost:8000/api/v1/system/version
```

### Option B — Migration Rollback (schema changed)

Every Alembic revision implements a `downgrade()` function. Roll back one revision at a time:

```bash
# Check current head:
docker exec docker-api-1 sh -c "cd /app/backend && alembic current"

# Roll back one step:
docker exec docker-api-1 sh -c "cd /app/backend && alembic downgrade -1"

# Repeat until you reach the revision matching the previous release.
# Then deploy the previous image:
APP_VERSION=${PREV_VERSION} docker compose up -d
```

If `downgrade()` is destructive (e.g. a column drop), **restore from backup** instead:

```bash
# Stop all services first.
docker compose down

# Restore Postgres from backup:
docker compose up -d postgres
docker exec -i docker-postgres-1 pg_restore \
  -U postgres -d seventh_ai_vision \
  --clean --if-exists < ./backups/backup_<timestamp>.pgdump

# Bring up the previous image:
APP_VERSION=${PREV_VERSION} docker compose up -d
```

---

## Zero-Downtime Upgrades (Multi-Replica Deployments)

For deployments with multiple `api` replicas behind a load balancer:

1. Run `alembic upgrade head` on one container before restarting any replica.
2. Deploy new replicas one at a time (rolling restart), waiting for each health check to pass before proceeding.
3. Old replicas must still function with the new schema during the rollout window — write migrations to be backward-compatible (add columns as nullable, rename in two stages, etc.).

---

## JWT Key Rotation

To rotate the signing key without invalidating existing sessions:

1. Generate a new key and choose a new kid (e.g. `2027-01`).
2. In `.env` (or your secrets manager):
   ```
   JWT_SECRET_KEY_CURRENT=<new-key>
   JWT_ACTIVE_KID=2027-01
   JWT_SECRET_KEY_PREVIOUS=<old-key>
   JWT_PREVIOUS_KID=2026-06
   ```
3. Deploy with the new config (`docker compose up -d api`).
4. New tokens are signed with the new kid; existing tokens still verify via `JWT_SECRET_KEY_PREVIOUS`.
5. After `ACCESS_TOKEN_TTL_MIN` (15 min) all access tokens have rotated. After `REFRESH_TOKEN_TTL_DAYS` (7 days) all refresh tokens have rotated.
6. Remove `JWT_SECRET_KEY_PREVIOUS` and `JWT_PREVIOUS_KID` and redeploy.

---

## Database Partition Maintenance

`pg_partman` creates 3 future monthly partitions automatically. The `scheduler` container runs this daily. If the scheduler was stopped for an extended period, run maintenance manually:

```bash
docker exec docker-postgres-1 psql -U postgres seventh_ai_vision \
  -c "SELECT partman.run_maintenance_proc();"
```

To inspect current partitions:

```bash
docker exec docker-postgres-1 psql -U postgres seventh_ai_vision \
  -c "SELECT * FROM partman.show_partitions('public.detections');"
```

---

## Versioning Policy

| Change type | Version bump | Notes |
|-------------|-------------|-------|
| New endpoint, new optional field | `MINOR` | Backward-compatible; `/api/v1` stays |
| Breaking API change | `MAJOR` | New `/api/v2` route; `/api/v1` kept for deprecation window |
| Bug fix, performance | `PATCH` | No schema change |
| Schema change with `downgrade()` | `MINOR` or `MAJOR` | Mark clearly in release notes |
| Schema change without `downgrade()` | `MAJOR` | Rollback requires backup restore |

---

## Migration History Quick Reference

| Revision | Description |
|----------|-------------|
| 0001 | Initial schema — tenants, users, cameras, detections, LPR, face, intrusion, audit, RLS |
| 0002 | Phase 3 AI modules — PPE, crowd, fire/smoke, weapon, behavior event tables |
| 0003 | Notification channels, rules, logs |
| 0004 | Tenant management — `tenant:manage` permission |
| 0005 | pgvector extension — `embedding_v` vector(512) on face tables, HNSW index |
| 0006 | Sites, module licensing, stream recordings, `auth_config` on streams |
| 0007 | 2FA fields, false_positive alert status, camera offline tracking |
| 0008 | Guard operations — shifts, patrols, checkpoints, occurrence book |
| 0009 | Dispatch, SLA configs, escalation events, evidence custody log |
| 0010 | Visitor management, privacy zones, PDPA consents and DSARs |
| 0011 | Advanced AI modules — tampering, abandoned, fall event tables |
| 0012 | Client portal role (id=7) |
| 0013 | Tier 1 gaps — login lockout, session device tracking, alert correlation, shift handovers, incident status history |
| 0014 | API keys table |
| 0015 | API key lookup function |
| 0016 | IP allowlist table |
| 0017 | Audit tamper-evident HMAC chain |
| 0018 | 2FA enforcement policies |
| 0019 | Alert deduplication rules |
| 0020 | Scheduled reports |
| 0021 | Drop legacy FLOAT4[] embeddings, add partial HNSW index on face_events.embedding_v |
