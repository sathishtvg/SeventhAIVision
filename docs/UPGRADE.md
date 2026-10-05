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

Fourteen tables are partitioned by month through `pg_partman` (installed in the
`public` schema — there is no `partman` schema). Each always has the current
month and three ahead. The `scheduler` container keeps it that way in its daily
cycle (`run_partition_maintenance`), and migration `0131` does it once when it is
applied.

**It needs the superuser credentials.** Adding a partition requires owning the
table, which the app role does not. The scheduler uses `POSTGRES_USER` and
`PGPASSWORD` (or `POSTGRES_PASSWORD`) — the same ones the backup and the audit
archive use. Without them it logs `partition maintenance SKIPPED` as an error and
no partitions are made. Until migration `0131` the job ran as the app role and had
never made one; an installation older than three months should be checked after
upgrading (below).

To inspect, as the superuser:

```bash
docker exec docker-postgres-1 psql -U postgres seventh_ai_vision -c "
  SELECT parent_table,
         (SELECT max(partition_tablename) FROM public.show_partitions(parent_table)) AS newest
    FROM public.part_config ORDER BY 1;"
```

Every `newest` should be three months ahead of today. To make them without
waiting for the scheduler:

```bash
docker exec docker-postgres-1 psql -U postgres seventh_ai_vision \
  -c "SELECT public.run_maintenance(p_analyze => false); SELECT public.apply_partition_rls();"
```

`apply_partition_rls()` matters: a new partition does not inherit its table's
row-level security, and without it is readable across tenants by name.

### Rows in a default partition

A row arriving when no partition exists for its date goes to the table's
`_default` partition, and that month's partition then cannot be made until the
row is moved. Check with:

```bash
docker exec docker-postgres-1 psql -U postgres seventh_ai_vision -c "
  SELECT 'detections' AS t, count(*) FROM ONLY detections_default
  UNION ALL SELECT 'audit_logs', count(*) FROM ONLY audit_logs_default;"
```

The scheduler moves such rows by itself, a month per transaction, before it makes
partitions, and reports what it moved (`partition maintenance: … stranded rows
moved …`).

**Do not move them by hand with `partition_data_time()` or `partition_data_proc()`.**
pg_partman moves a row by deleting it and inserting it again, and eleven event
tables reference `detections` with `ON DELETE CASCADE`: moving a detection that
way deletes its plate, face, intrusion and other event rows. The scheduler
suspends the foreign-key triggers for the one transaction of each move
(`session_replication_role = replica`), which is why it needs a superuser. If
they must be moved by hand, do exactly that, one table per transaction.

---

## The Scheduler's Daily Cycle Runs at Start

The scheduler runs its daily cycle — partition maintenance, the evidence purge,
the audit archive, the database backup — when it starts, and every 24 hours after.
It always did on a server that had been up for more than a day. On a machine
booted more recently it used to wait until the machine had been up a full day,
so a computer switched off every night never ran it. Restarting the scheduler
therefore takes a backup and runs the purge, every time.

Backups are rotated by day: the newest of each day is kept, for seven days.
Several restarts in one day leave one backup of that day and do not push earlier
days out. A file in the backup folder whose name carries no date is left alone.

## The Scheduler and the Recorder Need the Recordings Volume

The scheduler's recording integrity sweep re-reads recorded files. It was never
given the volume they are on, so it marked every recording it checked
`FILE_MISSING` while the files were intact.

- **Docker Compose:** the `scheduler` service now mounts `recordings_data`
  read-only. `docker compose up -d scheduler` recreates it with the mount.
- **Kubernetes:** the chart mounted the recordings claim on the API only. The
  `ingestion` pod — which is the recorder — and the `scheduler` pod now mount it
  too. **Before `helm upgrade`:** recordings made so far are on the ingestion
  pod's own disk, not on the claim, and go when the pod is replaced. If they
  matter, copy them out of the pod first (`kubectl cp <ingestion-pod>:/data/recordings ./recordings`)
  and into the claim afterwards. The chart was changed without being rendered
  (Helm is not installed where it was written): run `helm template` on it once
  before upgrading.
- With `minio.enabled: true` the chart creates no recordings claim at all, as
  before. Recordings are files on disk whatever the evidence store is, so on
  that configuration they are still on the pod's own disk.

Recordings already marked missing need nothing done: each sweep re-checks the
hundred least recently checked per organisation and corrects them.

## The Web Container Runs Without Root

The `frontend` container runs as the image's unprivileged `nginx` user (uid 101).
What that changes for an operator:

- **Ports inside the container** are 8080 (HTTP) and 8443 (HTTPS). The ports
  `docker-compose.yml` publishes — 5173 and 443 — are unchanged, and so is the
  Helm chart's Service, which targets the container port by name. Anything of your
  own that addressed the container's port 80 or 443 directly (a compose override,
  an ingress, a health check) must use 8080 / 8443.
- **An existing certificate volume** was written by root and holds a private key
  only root can read. `docker compose up` deals with it: the one-shot
  `frontend-certs` service hands the volume to the nginx user before the web
  container starts, and your certificate is kept. If the container is started
  some other way and exits with `[ssl-init] ERROR: … cannot read it`, run
  `docker compose run --rm frontend-certs` once.
- **Replacing the certificate** is as before — copy `server.crt` and `server.key`
  into the volume — and then run `frontend-certs` again, or make them owned by
  uid 101 yourself.

## The Default API Rate Limit

Every read (GET) is limited per caller and per route:

| Setting | Default | Applies to |
|---|---|---|
| `API_READ_RATE_LIMIT` | `300/minute` | Every read |
| `API_MEDIA_RATE_LIMIT` | `1200/minute` | Video playlists and segments, the live detection overlay, stored pictures and files |

A caller is the signed-in user when the request carries a valid token, otherwise
the client address. A refused request answers **429** with a `Retry-After`
header. Requests that change something are never refused by the default; sign-in,
alarm ingest and the drone gateway keep their own explicit limits.

The defaults are sized from what the screens do: ordinary polling asks any one
route a few times a minute, while one operator's sixteen-camera live wall asks
its overlay route 480 times a minute. Lower them only with that in mind. If Redis,
where the counts live, cannot be reached, requests are let through and a warning
is logged.

Until this release the configured default of 100 a minute was never applied to
any route.

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
