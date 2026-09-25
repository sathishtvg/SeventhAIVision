# Autonomous Drone Security Patrol — Gap Analysis

**Date:** 2026-09-24
**Status:** Phases 1–4 complete (analysis, data model, API, flying on the simulator). Decisions D1–D7 approved 2026-09-24.
No code, schema, configuration or data was changed to produce this document.
**Rules followed:**
- Inspect before modifying; integrate, never duplicate.
- **Additive only.** Nothing already built is changed (owner's instruction,
  2026-09-24: *"Don't change previously what ever we done."*). The drone module
  adds new tables, routers, services, pages and screens. Where a requirement
  cannot be met without touching existing code or schema, it is listed in §3 as
  a decision for the owner — not done.

Every statement below was checked against the code or the development database
on the date above. Where something could not be verified it says so.

---

## 0. Summary

There is **no drone code anywhere** in the repository — a clean build. Most of what
the module needs already exists and can be reused without modification: tenancy
and RLS, sites with coordinates and geofences, the frame pipeline and all eleven
AI workers, alerts, incidents with guard dispatch, evidence with chain of custody,
per-site recording policies, the realtime bus, notifications, PDF/Excel reporting,
hash-chained audit, and a proven scheduler pattern from Virtual Patrolling.

Seven findings change the plan the prompt assumes. They are in §1, and the
decisions they force are collected in §17.

---

## 1. Findings that change the plan

### 1.1 Drone video can reach the AI workers only as a camera

`detections.camera_id` is **NOT NULL**. So are `recordings.camera_id` and
`recordings.stream_id`. The pipeline is:

```text
ingestion (OpenCV, RTSP) ──XADD──► Redis stream frame_jobs ──XREADGROUP──► ai-worker-* (x11)
                                                                             └─► detections (camera_id NOT NULL)
```

The workers therefore process only video that belongs to a `cameras` row with a
`streams` row. There are two ways in:

| Option | What it takes | Consequence |
|---|---|---|
| **A. Represent each drone's camera as a `cameras` + `streams` row** | New rows only; a new `drones.camera_id` FK points at it | Ingestion, all 11 workers, recording, pre/post clips, detections, alerts and evidence work **unchanged**. The drone camera also appears in existing camera lists and the Live Wall, and counts toward per-camera AI licensing |
| B. Teach ingestion and the workers a second source type | Changes to `ingestion_main.py`, `ai-worker`, and two NOT NULL constraints | Violates the additive rule |

**Recommendation: A.** It is the only route that reuses the AI without changing it.
Hiding drone cameras from fixed-camera screens would mean editing those screens —
listed in §3 as optional.

### 1.2 A moving camera breaks two assumptions the workers make

- **Zones are image-space and per-camera.** `crowd_zones.polygon` is keyed to a
  `camera_id` and drawn over that camera's picture. On a drone the picture changes
  every second, so an image-space zone means nothing.
- **A camera's location is static.** `cameras.latitude/longitude` is one point.

So drone **security zones must live in map space** (latitude/longitude polygons,
evaluated with `geofence.point_in_polygon`) and be applied by a new context engine
*after* detection, using the drone's position at the frame's timestamp — not
passed to the workers. Which workers emit usable detections for a camera with no
image-space zone configured is **not yet verified**; that is the first task of
Phase 6.

### 1.3 There is no edge architecture — only unused columns

`recordings` and `evidence` carry `storage_location ∈ {central, local, both}` and
`sync_state ∈ {not_required, pending, uploading, synced, failed}`, and
`recording_policies` has `sync_mode`, sync windows and a bandwidth limit. But
**no backend code reads or writes `sync_state`**, and in the development database
all 4,362 recordings are `central/not_required` and all 39,768 evidence rows are
`central/synced`. There is no edge gateway service and no sync worker.

The prompt's "follow the existing edge architecture" therefore has nothing to
follow. Phase 5 is a **new, isolated `drone-edge` service**. The existing columns
were designed for exactly this and should be used as designed. Offline operation
can only be exercised against the simulator in development.

### 1.4 There is no request-level module gate

`tenant_module_licenses` records which modules a tenant has, but no router checks
it per request — Virtual Patrolling is not gated either (its gap analysis, §13.2,
recorded the same). The only enforcement is in `cameras.py`, where AI modules are
attached to cameras. `services/licensing.py` is Ed25519 licence-key verification
for on-premise installs, not per-tenant entitlement.

The prompt requires that a tenant without the module cannot register drones or
create missions. That needs a **new dependency, applied only to drone routers**.
It reads `drone_module_licenses`, not `tenant_module_licenses` — see §19.1 for
why the obvious home turned out to be unsafe. It changes nothing for the 20
existing modules.

### 1.5 Evidence retention would destroy incident evidence

`scheduler_main.purge_expired_evidence()` deletes every `evidence` row older than
the tenant's `evidence.retention_days` — **file first, then row** — with no
exemption for evidence attached to an open incident. Drone clips written into
`evidence` would be deleted on that schedule, as would any other incident's.

Virtual Patrolling hit the same problem and kept its snapshots in a
module-owned table outside the purge. The drone module should do the same by
default (see D5). The underlying gap — no legal hold for incident evidence —
affects every incident in the system and is flagged, not fixed.

### 1.6 Cameras have a position but no coverage

Cameras store `latitude/longitude` only: no heading, field of view or range. The
system can answer **"which cameras are near this point"** but not **"which cameras
can see it"**. Correlation will say *nearby*, not *covering*, unless coverage
geometry is added — which can be done additively in a new table (D2).

Related: a drone's GPS is the **drone's** position, not the detected object's.
Placing the object needs altitude, gimbal angle and lens geometry, and is an
estimate. Events will record the drone position and, where the provider reports
gimbal data, a projected estimate labelled as such.

### 1.7 No PostGIS, and none needed

Installed extensions: `pg_partman`, `pgcrypto`, `vector`. There is no PostGIS, and
adding it means changing the database image. It is not needed at site scale:
`services/geofence.py` already provides tested `haversine_meters`,
`point_in_polygon` and `is_within_site`. Nearby-camera queries will prefilter with
a latitude/longitude bounding box in SQL and compute exact distance in Python.

---

## 2. Existing functionality reused as-is

| Need | Existing | Where | Used how |
|---|---|---|---|
| Tenant isolation | `FORCE ROW LEVEL SECURITY` on 269 of 308 tables | `dependencies/tenant.py::get_db_with_tenant` | Every drone table gets the same policy |
| Site scoping | `user_sites` | `dependencies/sites.py::get_allowed_site_ids` | Drone endpoints filter by allowed sites |
| Permissions | `permissions`, `role_permissions` | `dependencies/permissions.py::require_permission` | New `drone:*` codes seeded by migration |
| Platform owner | tenant `seventhaivision`, `is_platform = true` | `tenants` | Stays the platform owner; `demo` stays a normal tenant |
| Sites | `latitude`, `longitude`, `geofence_radius_meters`, `geofence_polygon` | `sites` | Mission site, base point, outer geofence |
| Geometry | `haversine_meters`, `point_in_polygon`, `is_within_site` | `services/geofence.py` | Zone hits, nearby cameras, geofence checks |
| Video in | RTSP capture → `frame_jobs` | `ingestion_main.py` | Drone camera stream (via §1.1 option A) |
| AI | 11 workers, consumer groups with stale-claim recovery | `ai-worker/worker/consumer.py` | Unchanged |
| Pre/post clips | Ring buffer written to MP4 | `ingestion_main.py` | Unchanged; durations from `recording_policies` |
| Recording policy | `record_mode`, `sync_mode`, retention, `clip_pre_seconds`, `clip_post_seconds` | `services/recording_policy.py` | Drone missions reference the site policy |
| Media storage | `object_store` — local `/data/evidence` or S3/MinIO | `core/object_store.py` | All drone media |
| Alerts | `alerts` — `camera_id` nullable, `site_id`, `correlation_id`, no CHECK on `module_type` | `routers/alerts.py` | Drone alerts are ordinary rows; `payroll` alerts already set the precedent for non-camera sources |
| Incidents | `incidents` with dispatch, arrival, SLA and escalation fields | `routers/incidents.py`, `routers/dispatch.py` | Drone incidents are ordinary incidents |
| Evidence custody | `evidence_access_log` | `routers/dispatch.py` | Custody entries for drone media |
| Policy engine pattern | Pure, first-match-wins, DB-free, fully tested | `services/decision_engine.py` | Model for the context/risk engine |
| Provider config pattern | Protocol registry, config split into columns + JSONB, secrets encrypted | `services/device_protocols.py`, `shared/device_protocols.py`, `core/crypto` | Model for provider configuration and credentials |
| Heartbeat pattern | `expected_interval_seconds` → offline status | `iot_sensors` | Model for heartbeat → `COMMUNICATION_LOST` |
| Device health fields | `battery_pct`, `firmware_version`, storage, status | `body_cameras` | Model for drone health columns |
| Scheduler | Timezone-correct occurrences; unique `(schedule_id, scheduled_for)`; MISSED state; 6 h lookback | `services/vpatrol_scheduler.py` | Copied as a sibling, not shared |
| Time-series | pg_partman partitions (`vehicle_positions`, `iot_readings`, `detections`, `audit_logs`…) | `scheduler_main.run_partition_maintenance` | `drone_telemetry` partitioned the same way |
| Realtime | Redis pub/sub `tenant_events:<tenant>` → WebSocket | `realtime/redis_listener.py`, `connection_manager.py` | Drone events published here; no second bus |
| Notifications | Rules, channels, logs; email, SMS, webhook; Expo push | `notifications/` | Drone alerts and failures |
| Reports | reportlab (PDF), openpyxl (Excel); checksummed reports; retrying email queue | `services/vpatrol_reports.py`, `vpatrol_email.py` | Pattern for mission reports |
| Audit | Hash-chained audit log | `services/audit.py::write_audit_log` | Every sensitive drone action |
| Platform health | Health and error collection, usage rollup | `services/platform_health.py`, `error_collector.py`, `usage_rollup.py` | Super Admin sees drone service health and usage |

---

## 3. Existing functionality that would require modification

**None of these is done without the owner's go-ahead.** Each has an additive
alternative, and the recommendation is the alternative unless stated.

| Would change | Why it would be needed | Additive alternative | Recommendation |
|---|---|---|---|
| Camera list, Live Wall and camera-count screens | Hide drone cameras from fixed-camera views | Leave them visible and clearly named (e.g. "Drone D-01 camera") | Alternative — revisit after Phase 9 |
| `tenant_module_licenses` (new limit columns) | Max drones / missions / sites | New `drone_module_licenses` table keyed by tenant, holding the entitlement as well as the limits (§19.1) | Alternative |
| `purge_expired_evidence()` | Legal hold for evidence linked to an open incident | Drone media in a module-owned table outside the purge (as Virtual Patrolling did) | Alternative by default; the legal-hold fix is worth a separate decision because it protects every incident, not only drone ones |
| Incident detail page and router | Show drone, mission, waypoint and telemetry on the incident | `drone_events.incident_id` links back; a drone investigation page is the rich view; the incident title and description name the drone and mission | Alternative |
| Command Centre board | A drone panel alongside Virtual Patrolling | Drone alerts already appear in every existing alert feed because they are `alerts` rows; the fleet view lives on a new Drone Dashboard | Alternative — a panel is a small, clearly scoped change if wanted later |
| Pricing engine | Per-drone billing unit | Bill `drone_patrol` as `per_site` (like `virtual_patrol`) or `flat` | Alternative — per-drone pricing needs the engine to learn a new unit |
| `cameras` (heading / FOV / range) | "Which cameras can see this point" | New `drone_camera_coverage` table pointing at `cameras` | Alternative (see D2) |

---

## 4. Missing functionality

Everything drone-specific: fleet registry; provider abstraction and the simulator
provider; heartbeat and health evaluation; routes and waypoints; map-space security
zones; security profiles; missions and schedules; pre-flight validation; patrol
sessions and their state machine; telemetry ingestion and storage; the drone-edge
service and edge→central sync; context engine; risk engine; event verification;
CCTV correlation; drone incident creation; "verify with drone"; mission replay;
mission reports and their email delivery; patrol intelligence, risk map and
recommendations; maintenance tracking; the request-level module gate; the
`drone_patrol` catalogue entries; all drone permissions; all drone screens.

Also missing, and **not** proposed to be built here:

- **WhatsApp** and **Windows-native** notification channels do not exist. Drone
  notifications use the channels that do: web, Expo push, email, SMS and webhook.
  The desktop app is a shell around the web build and receives what the web receives.
- **Nearest-available-guard** selection does not exist — dispatch is manual
  (`POST /dispatch/incidents/{id}`). The drone module adds a read-only *suggestion*
  of on-duty guards at the site; the dispatch itself uses the existing endpoint.
- **Mobile maps.** The mobile app has `expo-location` but no map library. Drone
  alerts on mobile show the location as coordinates with an "open in maps" link
  unless a map dependency is approved (D6).

---

## 5. Existing tables relevant to drones

| Table | Relevant columns | Role |
|---|---|---|
| `tenants` | `slug`, `is_platform`, `timezone` | Ownership; platform owner is `seventhaivision` |
| `sites` | `latitude`, `longitude`, `geofence_radius_meters`, `geofence_polygon` (JSONB) | Mission site, base, outer geofence |
| `cameras` | `latitude`, `longitude`, `site_id`, `ai_modules_enabled` (JSONB) | CCTV correlation candidates; drone camera representation |
| `streams` | `camera_id`, `protocol`, `url`, `auth_config` (JSONB), `continuous_recording` | Drone video stream |
| `detections` | `camera_id` **NOT NULL**, `module_type`, `confidence`, `bounding_box` | Raw AI output for drone frames |
| `alerts` | `camera_id` nullable, `site_id`, `severity`, `correlation_id`, `message_params` | Drone alerts |
| `incidents` | `alert_id`, `is_auto_created`, `dispatched_guard_id`, `guard_arrived_at`, `sla_deadline_at` | Drone incidents and dispatch |
| `evidence` | `incident_id`, `capture_kind ∈ {frame, plate_crop, face_crop, clip}`, `checksum_sha256`, `storage_location`, `sync_state` | Subject to retention purge (§1.5) |
| `evidence_access_log` | — | Chain of custody |
| `recordings` | `camera_id` and `stream_id` **NOT NULL**, `storage_location`, `sync_state`, checksums | Drone mission recordings |
| `recording_policies` | `record_mode`, `sync_mode`, retention days, `clip_pre_seconds`, `clip_post_seconds` | Per-site policy the mission inherits |
| `crowd_zones` | `camera_id`, `polygon` (image space) | Not usable for a moving camera (§1.2) |
| `vehicle_positions`, `iot_readings` | Partitioned time series | Pattern for `drone_telemetry` |
| `body_cameras`, `iot_sensors` | Battery, firmware, `expected_interval_seconds`, `current_status` | Patterns for drone health and heartbeat |
| `products`, `product_modules`, `billing_modules`, `tenant_module_licenses` | Module codes are lowercase snake_case | `drone_patrol` registered here |
| `notification_rules`, `notification_channels`, `notification_logs` | — | Drone notifications |
| `virtual_patrol_*` | `uq_vpsess_execution (schedule_id, scheduled_for)` | Scheduler and report precedent |
| `patrol_sessions`, `patrol_routes`, `patrol_checkpoints` | — | **Physical** guard patrols. Name collision risk — every new table takes the `drone_` prefix |

## 6. Existing APIs relevant to drones

`cameras`, `streams`, `sites`, `alerts`, `alert_rules`, `alert_dedup`, `incidents`,
`dispatch` (dispatch, arrival, SLA, custody), `evidence`, `recording_policies`,
`playback`, `notifications`, `reports`, `scheduled_reports`, `exports`,
`command_centre`, `action_center`, `gps`, `licenses`, `platform_licenses`,
`platform_billing`, `platform_console`, `audit`. None changes. New drone routers
follow the same shape: `require_permission(...)`, `get_db_with_tenant`, site scoping,
and the project's `paginate()` for lists.

## 7. Existing frontend and client components

- **Web** — Leaflet 1.9 and react-leaflet 5 (`MapView`, `GPS`), hls.js for live
  video, `LiveWall`, `CommandCentre`, `ActionCenter`, `Alerts`, `Incidents`,
  `Evidence`, `Playback`, `Heatmap`, `ErrorCentre`, and the `VirtualPatrol` /
  `PatrolExecution` pages as the pattern for a scheduled-patrol UI. **No
  map-drawing plugin is installed**, so the route and zone designers need a
  polygon/polyline drawing capability — either a new dependency or a small
  in-house editor on react-leaflet.
- **Mobile** — Expo SDK 51: `AlertsScreen`, `AlertDetailScreen`,
  `IncidentDetailScreen`, `NotificationsHistoryScreen`, `expo-notifications` for
  push. No map library.
- **Desktop** — Electron 33 shell around the web build with no API layer of its
  own. It receives the drone screens when the web does; nothing desktop-specific is
  needed.

## 8. Existing AI and event infrastructure

Ingestion reads RTSP with OpenCV and publishes frame jobs to the Redis stream
`frame_jobs` (capped at 2,000 entries). Eleven workers — lpr, face, intrusion, ppe,
crowd, fire-smoke, weapon, behavior, tampering, abandoned, fall — consume through
consumer groups with stale-message reclaim and dedup, and write `detections`.
Alert rules, dedup and routing turn detections into alerts. Realtime is Redis
pub/sub on `tenant_events:<tenant_id>` fanned out to WebSocket clients.

The drone module adds a **drone event layer after detection**: it subscribes to
detections from drone cameras, applies map-space zones, time rules,
authorisation, history and CCTV correlation, and only then decides whether to
raise an alert. No worker changes.

## 9. Existing storage infrastructure

`STORAGE_BACKEND` defaults to `local`, writing under `EVIDENCE_ROOT`
(`/data/evidence`); an S3/MinIO backend exists in `core/object_store.py`. MinIO
must stay stopped on the 7.7 GB development box. Recordings and evidence carry
SHA-256 checksums that are actually verified. Retention is per site through
`recording_policies`, falling back to the tenant setting.

## 10. Existing notification infrastructure

`notification_rules`, `notification_channels` and `notification_logs`; providers
for email, SMS and webhook; Expo push to phones; WebSocket to web and desktop.
Virtual Patrolling has its own retrying email queue for reports, which is the
pattern for mission-report delivery.

## 11. Existing Command Centre components

`command_centre` router and page, `LiveWall` with multi-screen pop-outs,
`ActionCenter`, `Alarms`, and the alert feeds. Drone alerts appear in all of them
with no change, because they are `alerts` rows.

## 12. Existing reporting components

reportlab and openpyxl; `vpatrol_reports` (PDF + Excel, checksummed — migration
0121); `vpatrol_digest`; `reports`, `scheduled_reports`, `exports`. Mission
reports reuse the libraries and the checksum-and-queue pattern in new drone
services.

## 13. Existing edge architecture

None beyond the columns in §1.3. Proposed:

```text
DRONE ──► drone-edge (new container, per site)
            ├─ provider adapter (SDK or simulator)
            ├─ telemetry buffer
            ├─ local recording / snapshots  → storage_location = 'local', sync_state = 'pending'
            ├─ event buffer
            └─ sync client ──(authenticated, idempotent)──► central API ──► existing tables
```

Provider code stays **out of the central FastAPI process**. Every record the edge
creates carries a stable client-generated UUID; central upserts on it, so a
reconnect, retry or restart cannot duplicate anything.

## 14. Security and RLS implications

- **Policy form.** All 269 existing policies use
  `tenant_id = current_setting('app.current_tenant', true)::uuid`. Drone tables use
  the same form so there is one pattern to audit.
- **The commit trap.** `set_config(..., true)` is transaction-local. Any statement
  after a commit runs with no tenant. The drone scheduler and sync workers must set
  the tenant inside each transaction — a commit inside a per-tenant loop kills
  every iteration after the first.
- **Tests bypass RLS.** Backend tests connect as `postgres`, which has
  `BYPASSRLS`. Drone isolation tests run as `svc_app`, with two tenants, asserting
  first that the connection is not a superuser. Queries also put `tenant_id` in the
  WHERE clause as well as relying on the policy.
- **Site scoping** applies to every drone list and detail endpoint.
- **Provider credentials** are encrypted with `core/crypto` (Fernet), stored apart
  from JSONB config as `device_protocols` does, and never returned to a client.
  Private stream URLs are served through the existing authorised playback path.
- **Edge → central authentication** is new. Each drone-edge gets its own
  credential, scoped to one tenant and site, revocable, and audited.
- **Super Admin separation.** The platform owner sees module licensing, usage,
  and drone service health and errors through `platform_health` and
  `error_collector`. It does not see tenant drone operations, except through the
  existing time-boxed support session.
- **Permission names** follow the existing `resource:action` form (`camera:read`,
  `incident:dispatch`), so the prompt's `drone:view` becomes `drone:read`. Proposed:
  `drone:read`, `drone:create`, `drone:update`, `drone:delete`, `drone:operate`,
  `drone:mission:create`, `drone:mission:update`, `drone:mission:execute`,
  `drone:mission:abort`, `drone:event:read`, `drone:event:acknowledge`,
  `drone:event:investigate`, `drone:maintenance:read`, `drone:maintenance:manage`,
  `drone:report:read`, `drone:report:export`.
- **Flight safety.** The software issues only commands the provider adapter
  declares it supports, never simulates a capability in production, honours the
  provider's return-to-home and geofence, and gives the operator an abort wherever
  the provider exposes one.

## 15. Recommended implementation sequence

The prompt's 14 phases hold, with these adjustments.

| Phase | Scope | Adjustment from the prompt |
|---|---|---|
| 1 | Repository analysis | **This document** |
| 2 | Database and domain | **Done** — migration 0123. All tables `drone_`-prefixed; `drone_telemetry` partitioned via pg_partman; `drone_patrol` listed in `billing_modules` only, as `virtual_patrol` is; permissions seeded |
| 3 | Backend APIs | **Done** — 61 operations and the drone-only licence gate (§1.4); see `DRONE_PATROL_API.md` |
| 4 | Provider abstraction and simulator | **Done** — migration 0124, the drone runner, pre-flight, the command queue; capability flags per adapter, the simulator the only one. See `DRONE_PATROL_OPERATIONS.md` and `DRONE_PATROL_PROVIDER_INTEGRATION.md` |
| 5 | Edge integration | A new service (§1.3), exercised against the simulator |
| 6 | AI integration | Starts by verifying which workers produce usable detections on a moving camera (§1.2) |
| 7 | CCTV correlation | Distance-based "nearby" first; "covering" only if D2 is approved |
| 8 | Incident integration | Through the existing tables and dispatch endpoint |
| 9 | Frontend | Needs a map-drawing capability (§7) |
| 10 | Mobile and desktop | Mobile: alerts, incident, snapshot, clip, acknowledge, escalate. Desktop: nothing separate |
| 11–14 | Reporting, analytics, hardening, final validation | As in the prompt |

**Built in Phase 2 (migration 0123), 18 tables:** `drone_module_licenses`,
`drone_provider_configs`, `drone_edge_gateways`, `drones`,
`drone_maintenance_logs`, `drone_security_zones`, `drone_security_profiles`,
`drone_profile_rules`, `drone_routes`, `drone_waypoints`, `drone_missions`,
`drone_schedules`, `drone_patrol_sessions` (unique `(schedule_id, scheduled_for)`),
`drone_session_waypoints`, `drone_telemetry` (partitioned), `drone_events`
(→ `alerts`, `incidents`; `detection_id` without an FK, §19.3), `drone_event_media`,
`drone_event_cameras`. Every one points at existing rows; no existing table gained
a column.

**Deferred to the phase that needs them:** `drone_sync_receipts` (Phase 5),
`drone_camera_coverage` (Phase 7, optional), `drone_reports`,
`drone_report_recipients` and `drone_report_email_queue` (Phase 11).

## 16. Risks and technical dependencies

| Risk | Impact | Mitigation |
|---|---|---|
| No physical drone and no chosen manufacturer SDK | Real flight, video and telemetry cannot be proven | Everything is built and tested against the simulator; the final report states plainly what is simulator-only |
| Regulation | In Singapore, unmanned aircraft operations are regulated by CAAS; depending on the aircraft and operation, permits may be required, and autonomous or beyond-line-of-sight flight is restricted | The software records the operator's permit details and blocks missions without them; it does not assert compliance. The customer confirms requirements with CAAS |
| 7.7 GB development box | The AI workers cannot run alongside the core services | AI integration tests run one worker at a time, or in CI |
| Geo-referencing is an estimate | Object location and CCTV correlation are approximate | Record the drone position as fact and any projection as an estimate, labelled |
| Moving-camera AI quality | Workers are tuned for fixed cameras | Verify per worker in Phase 6; the risk engine weights by module and confidence |
| Telemetry volume | Millions of rows per drone per month | Partitioned table, indexed by `(drone_id, recorded_at)`, retention through partition drops |
| Clock alignment | Frames and telemetry come from different clocks | Match on provider timestamps with a tolerance; record the offset |
| Evidence purge | Drone evidence deleted on schedule | Module-owned media table (D5) |
| Scope | Fourteen phases — the size of Virtual Patrolling | Deliver phase by phase, each merged behind green CI |

## 17. Decisions needed before Phase 2

**Approved by the owner on 2026-09-24: all seven recommendations, as written.**
The system-wide evidence legal hold (D5) was not taken up; drone media stays
outside the purge in a drone-owned table.

| # | Decision | Recommendation |
|---|---|---|
| D1 | How drone video reaches the AI (§1.1) | Represent each drone's camera as a `cameras` + `streams` row; accept that it shows in camera lists |
| D2 | Camera coverage geometry (§1.6) | Add an optional `drone_camera_coverage` table; correlate by distance until it is filled in |
| D3 | Module limits (§3) | New `drone_module_limits` table — built as `drone_module_licenses`, holding the entitlement too (§19.1) |
| D4 | Billing unit | `per_site`, matching `virtual_patrol` |
| D5 | Evidence retention (§1.5) | Module-owned media table now; decide the system-wide legal hold separately |
| D6 | Mobile maps | Coordinates plus "open in maps" link for now; add a map library only if wanted |
| D7 | First real provider | None until hardware is chosen; the adapter interface is designed so any vendor fits |

## 18. What only hardware can complete

Physical drone; the manufacturer's SDK or cloud API and credentials; flight
controller and dock (if autonomous launch is wanted); the camera stream format the
aircraft actually emits; site edge hardware and network; and the operator's
aviation permits and site-specific flight configuration. Until those exist, the
module will be complete **against the simulator** and will say so.

---

## 19. Addendum — found while building Phase 2

### 19.1 The drone entitlement cannot live in `tenant_module_licenses`

`/api/v1/licenses/me/enabled-modules` and `cameras._check_module_licenses` both
treat a tenant with **no** licence rows as licensed for **every** AI module — the
fallback that keeps fresh and test tenants working. Writing a `drone_patrol` row
for such a tenant gives it exactly one row, which switches the fallback off and
silently removes all eleven AI modules from it. `demo` and `seventhaivision`
each have their 11 rows, so they would not have been hit; a tenant created any
other way would have been.

So the entitlement (`is_enabled`, `expires_at`) and the limits live together in
`drone_module_licenses`, which nothing outside this module reads. D3 is built
that way.

Consequence for billing: the pricing engine's `platform_tenant_billable_modules()`
bills modules it finds in `tenant_module_licenses`, so `drone_patrol` is listed in
the catalogue (per site, priced at zero, like `virtual_patrol`) but will not
appear on an invoice. `virtual_patrol` is in the same position today. Charging for
either is a commercial and billing decision to take separately.

### 19.2 The existing licence screen cannot enable `drone_patrol`

`licenses.py` rejects any module not in its hard-coded `ALL_MODULES` list.
Enabling drone patrol for a tenant therefore needs its own small platform
endpoint in Phase 3, writing `drone_module_licenses` — not an edit to
`licenses.py`.

### 19.3 No foreign key can point at `detections` or `evidence`

Both are partitioned with the timestamp in the primary key, so an FK on `id`
alone is impossible. `drone_events.detection_id` and
`drone_event_cameras.related_detection_id` are plain references, which is also
how the existing code treats them.

### 19.4 Verified

- Upgrade on the development database (existing install) and the test database;
  downgrade to 0122 removes all 18 tables, the partitions, 16 permissions, 56
  grants, the billing entry and the pg_partman registration; re-upgrade restores
  them exactly. CI proves the fresh install.
- `backend/tests/test_drone_schema.py`: 22 tests, all passing — one flight per
  drone, one session per scheduled run, telemetry dedup and partition routing,
  zone geometry, deferred waypoint reordering, deletion that never blocks, and
  tenant isolation tested as `svc_app` after asserting it cannot bypass RLS.
- Adjacent existing suites (licences, billing, platform licences, tenants,
  certification): 120 passed. Migration-safety and secret-scanning repo suites:
  142 passed.
