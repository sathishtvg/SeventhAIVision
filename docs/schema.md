# Database Schema

PostgreSQL 16 with Row-Level Security (RLS) and `pg_partman` monthly partitioning on high-volume tables. Every tenant-scoped table enforces:

```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <t>
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
```

`current_setting(..., true)` (missing-OK) means an unscoped session matches zero rows — fail-closed.

---

## Global Catalogues (no tenant_id, no RLS)

### `roles`
| Column | Type | Notes |
|--------|------|-------|
| `id` | SMALLINT PK | 1=super_admin 2=admin 3=supervisor 4=operator 5=security_guard 6=viewer 7=client_viewer |
| `code` | VARCHAR(50) | unique |
| `name` | VARCHAR(100) | |
| `description` | TEXT | |

### `permissions`
| Column | Type |
|--------|------|
| `id` | SERIAL PK |
| `code` | VARCHAR(100) unique |
| `description` | TEXT |
| `category` | VARCHAR(50) |

### `role_permissions`
Junction table: `(role_id, permission_id)` PK.

---

## Core Tenant / User Tables

### `tenants`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `name` | VARCHAR(255) | |
| `slug` | VARCHAR(100) | unique |
| `is_active` | BOOLEAN | |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

### `users`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK→tenants | RLS |
| `role_id` | SMALLINT FK→roles | |
| `email` | VARCHAR(255) | unique per tenant |
| `hashed_password` | VARCHAR(255) | bcrypt |
| `full_name` | VARCHAR(255) | |
| `is_active` | BOOLEAN | |
| `failed_login_count` | INT | login lockout counter |
| `locked_until` | TIMESTAMPTZ | NULL = not locked |
| `totp_secret` | TEXT | NULL = 2FA not enabled |
| `totp_enabled` | BOOLEAN | |
| `last_login_at` | TIMESTAMPTZ | |

### `refresh_tokens`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK | RLS |
| `user_id` | UUID FK→users | |
| `token_hash` | VARCHAR(255) | SHA-256 of raw token |
| `device_name` | TEXT | |
| `last_ip` | VARCHAR(45) | |
| `last_seen_at` | TIMESTAMPTZ | |
| `expires_at` | TIMESTAMPTZ | |
| `revoked_at` | TIMESTAMPTZ | NULL = live |

### `tenant_settings`
Key-value configuration (admin-editable at runtime, no redeploy needed).

| Column | Type |
|--------|------|
| `tenant_id` | UUID FK, part of UNIQUE(tenant_id, setting_key) |
| `setting_key` | TEXT |
| `setting_value` | JSONB |
| `updated_by_user_id` | UUID FK→users |
| `updated_at` | TIMESTAMPTZ |

Known keys: `lpr.confidence_threshold`, `face.match_threshold`, `intrusion.breach_cooldown_seconds`, `evidence.retention_days`.

---

## Camera / Stream Tables

### `sites`
Groups cameras by physical location.

| Column | Type |
|--------|------|
| `id` | UUID PK |
| `tenant_id` | UUID FK |
| `name` | VARCHAR(255) |
| `address` | TEXT |
| `latitude` / `longitude` | DOUBLE PRECISION |
| `is_active` | BOOLEAN |

### `cameras`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK | |
| `site_id` | UUID FK→sites | nullable |
| `name` | VARCHAR(255) | |
| `location` | VARCHAR(255) | |
| `latitude` / `longitude` | DOUBLE PRECISION | |
| `ai_modules_enabled` | JSONB | e.g. `["lpr","face"]` |
| `is_active` | BOOLEAN | |

### `streams`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` / `camera_id` | UUID FK | |
| `protocol` | VARCHAR(20) | rtsp \| rtmp \| onvif \| http |
| `url` | VARCHAR(500) | validated — no private IPs |
| `auth_config` | JSONB | `{"username":"..","password":".."}` |
| `status` | VARCHAR(20) | offline \| online \| degraded |
| `last_frame_at` | TIMESTAMPTZ | |

### `camera_health_events`
Append-only stream status transition log.

### `recordings`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` / `camera_id` / `stream_id` / `site_id` | UUID FK | |
| `status` | VARCHAR(20) | recording \| completed \| failed |
| `started_at` / `ended_at` | TIMESTAMPTZ | |
| `file_path` | VARCHAR(500) | under RECORDINGS_ROOT |
| `duration_seconds` | INTEGER | |
| `file_size_bytes` | BIGINT | |

---

## Detection Pipeline Tables (Partitioned)

All partitioned monthly by `detected_at` / `captured_at` / `created_at`. `pg_partman` pre-creates 3 future months.

### `detections` *(parent — monthly partitioned)*
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | part of composite PK (id, detected_at) |
| `tenant_id` / `camera_id` | UUID FK | |
| `module_type` | VARCHAR(30) | lpr \| face \| intrusion \| ppe \| crowd \| fire_smoke \| weapon \| behavior \| tampering \| abandoned \| fall |
| `confidence` | NUMERIC(5,4) | |
| `bounding_box` | JSONB | |
| `raw_metadata` | JSONB | always includes `{"model_version":"..."}` |
| `detected_at` | TIMESTAMPTZ | partition key |

**Child tables** (same monthly partitioning, FK back to detections via composite key):

| Table | Module | Notable columns |
|-------|--------|-----------------|
| `lpr_events` | LPR | `plate_number`, `plate_confidence`, `watchlist_match` (allow\|block\|NULL) |
| `face_events` | Face | `embedding_v` vector(512), `matched_watchlist_id`, `match_confidence`, `watchlist_match` |
| `intrusion_events` | Intrusion | `zone_id`, `person_bbox`, `dwell_time_seconds` |
| `ppe_events` | PPE | `violations` JSONB, `required_items` JSONB |
| `crowd_events` | Crowd | `person_count`, `capacity_ratio`, `capacity_threshold` |
| `fire_smoke_events` | Fire/Smoke | `detections` JSONB (class + confidence per bbox) |
| `weapon_events` | Weapon | `weapon_class`, `weapon_confidence` |
| `behavior_events` | Behavior | `behavior_type` (loitering\|running\|fighting\|tailgating), `dwell_seconds` |
| `tampering_events` | Tampering | `tamper_type` (blur\|obscured\|moved\|signal_loss), `confidence` |
| `abandoned_events` | Abandoned | `object_bbox`, `dwell_seconds`, `alert_threshold_seconds` |
| `fall_events` | Fall | `person_bbox`, `fall_confidence` |

### `evidence` *(monthly partitioned by captured_at)*
| Column | Type |
|--------|------|
| `id` | UUID |
| `tenant_id` / `detection_id` / `incident_id` | UUID (detection_id/incident_id are not FK — see note below) |
| `media_type` | VARCHAR(10) — image \| video |
| `storage_path` | VARCHAR(500) — doubles as S3 object key when STORAGE_BACKEND=s3 |
| `checksum_sha256` | VARCHAR(64) |
| `captured_at` | TIMESTAMPTZ — partition key |

**Note on dropped FKs:** `alerts.detection_id`, `evidence.detection_id`, and `evidence.incident_id` are intentionally not foreign keys. A real FK into a partitioned parent requires the partition key (`detected_at`) on every referencing row. The application guarantees referential consistency (same transaction writes both sides); the column stays indexed.

### `audit_logs` *(monthly partitioned by created_at — archived, never dropped)*
| Column | Type |
|--------|------|
| `id` | UUID |
| `tenant_id` / `user_id` | UUID |
| `action` | VARCHAR(100) |
| `resource_type` / `resource_id` | VARCHAR(50) / UUID |
| `ip_address` | VARCHAR(45) |
| `detail` | JSONB |
| `hmac_sha256` | VARCHAR(64) — tamper-evident chain |
| `prev_entry_id` | UUID — points to previous entry |

---

## Alert / Incident Tables

### `alerts`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` / `camera_id` | UUID FK | |
| `detection_id` | UUID (no FK) | |
| `module_type` | VARCHAR(30) | |
| `severity` | VARCHAR(10) | info \| low \| medium \| high \| critical |
| `alert_code` | VARCHAR(100) | stable machine key, e.g. `lpr.blocklist_hit` |
| `message_params` | JSONB | structured data backing alert_code |
| `title` / `message` | VARCHAR/TEXT | pre-rendered English |
| `status` | VARCHAR(20) | open \| acknowledged \| false_positive \| resolved \| closed |
| `correlation_id` | UUID | multi-camera correlation group |
| `is_false_positive` | BOOLEAN | |
| `dedup_key` | VARCHAR(200) | for alert dedup rules |
| `acknowledged_by_user_id` | UUID FK | |
| `acknowledged_at` | TIMESTAMPTZ | |

### `incidents`
Extended workflow: open → investigating → dispatched → on_scene → resolved → closed.

| Column | Type |
|--------|------|
| `id` | UUID PK |
| `tenant_id` / `camera_id` / `alert_id` | UUID |
| `title` / `description` | VARCHAR/TEXT |
| `alert_code` / `message_params` | i18n-ready fields |
| `severity` | VARCHAR(10) |
| `status` | VARCHAR(20) |
| `is_auto_created` | BOOLEAN |
| `dispatched_guard_id` / `dispatched_at` / `guard_arrived_at` | guard dispatch |
| `sla_deadline_at` / `sla_breached` | SLA tracking |
| `escalated_at` / `escalated_to_user_id` | escalation |

### `incident_notes`
Append-only notes thread on an incident.

### `incident_status_history`
Full audit trail of every status transition (from_status, to_status, changed_by_user_id, lat/long, notes).

### `alert_dedup_rules`
| Column | Type | Notes |
|--------|------|-------|
| `tenant_id` | UUID FK | |
| `module_type` | VARCHAR(30) | NULL = all modules |
| `camera_id` | UUID FK | NULL = all cameras |
| `window_seconds` | INTEGER | dedup window |
| `is_active` | BOOLEAN | |

Specificity tiers (highest wins): exact (camera+module) > camera-only > module-only > global.

---

## Watchlist / Zone Tables

### `watchlist_entries` (LPR)
`plate_number`, `list_type` (allow\|block), `reason`, `expires_at`.

### `face_watchlist_entries`
`person_name`, `embedding_v` vector(512), `list_type` (allow\|block), `expires_at`.

### `restricted_zones`
`camera_id`, `name`, `polygon` JSONB (normalized 0..1 coordinates), `severity`.

### `privacy_zones`
Per-camera pixel-mask polygons applied before AI processing (PDPA compliance).

---

## Guard Operations Tables

### `shifts`
Guard duty schedule: `site_id`, `guard_user_id`, `scheduled_start/end`, `actual_start/end`, `status`, `check_in/out_lat/long`, `handover_notes`.

### `patrol_routes` / `patrol_checkpoints`
Named routes with ordered waypoints (lat/long + NFC/QR codes).

### `patrol_sessions` / `checkpoint_scans`
A guard's execution of a route; individual tap/scan events with GPS, timestamp, scan method.

### `shift_handovers`
Structured handover report: open alert/incident counts, patrol completion stats, outgoing notes.

### `occurrence_book_entries`
Singapore Occurrence Book — append-only log (author, entry_type, severity, body, linked alert/incident).

---

## Visitor & Compliance Tables

### `visitors` / `visitor_logs`
Pre-registered visitors and arrival/departure events.

### `pdpa_consents` / `data_subject_requests`
Consent records and DSAR/erasure/portability requests.

### `sla_configs`
Per-tenant per-severity SLA thresholds (ack_within, dispatch_within, resolve_within seconds).

### `escalation_events`
Auto-escalation history with notification tracking.

### `evidence_access_log`
Chain-of-custody log: every view/download/export of evidence (user, IP, checksum verification).

---

## Licensing & Sites

### `tenant_module_licenses`
Per-tenant per-module enablement: `module_type`, `is_enabled`, `max_cameras`, `licensed_at`, `expires_at`.

---

## Notification Tables

### `notification_channels`
Configured delivery channels per tenant: `channel_type` (email\|sms\|webhook), `config` JSONB, `is_active`.

### `notification_rules`
Trigger rules: which alert severity/module_type fires which channel.

### `notification_logs`
Delivery history with `status` (sent\|failed\|suppressed) and `error_detail`.

---

## API & Security Tables

### `api_keys`
Long-lived machine credentials: `key_hash` (SHA-256), `name`, `scopes` JSONB, `expires_at`, `last_used_at`, `is_active`.

### `ip_allowlist`
Per-tenant CIDR blocks that restrict API access when any entry is active.

### `two_factor_policies`
Tenant-level 2FA enforcement policy: `require_2fa_for_roles` JSONB.

---

## Partitioning & Retention

`pg_partman` creates monthly child partitions for: `detections`, all `*_events` tables, `evidence`, `audit_logs`.

The `scheduler` container runs daily:
- **Detections/evidence**: drops partitions older than `evidence.retention_days` (default 90). Deletes on-disk files before dropping the partition row.
- **Audit logs**: detaches (never drops) partitions past `audit.retention_years` (default 7), archives to compressed export files under `/data/archive/`.
