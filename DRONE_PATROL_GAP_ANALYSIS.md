# Autonomous Drone Security Patrol — Gap Analysis

**Date:** 2026-09-24
**Status:** All 14 phases complete as of 2026-10-04 — on the simulator; no physical aircraft has been connected. Decisions D1–D7 approved 2026-09-24. Everything left open at the final validation was closed on 2026-10-05 (§26); two design decisions remain as built. What was built is summarised in `DRONE_PATROL_IMPLEMENTATION.md`.
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
| 5 | Edge integration | **Done** — migration 0125, the isolated `drone-edge` service (§1.3) and its sync API, exercised against the simulator. The recording columns 0079 designed for edge sync are used as designed. Offline, a gateway finishes flights in the air but starts none; see `DRONE_PATROL_EDGE.md`, "Decision for the owner" |
| 6 | AI integration | **Done** — migration 0126 and the context and risk engine; no worker changed. §1.2's open question answered by reading every worker: six work frame by frame, intrusion and crowd need an image zone (a full-frame zone makes intrusion the drone's person detector), behaviour, abandoned and tampering are unreliable on a moving camera. Workers still alert on their own — a decision for the owner in `DRONE_PATROL_AI.md` |
| 7 | CCTV correlation | **Done** — migration 0127. Nearby by distance; covering from the optional surveyed coverage (D2); corroboration by a matching fixed-camera detection verifies the event; operator view with live and playback paths, no stream addresses |
| 8 | Incident integration | **Done** — migration 0128. Incidents, notes, status history and dispatch are the platform's own; nearest free guard from existing shift and position data; verify with drone as a checked pause and resume |
| 9 | Frontend | **Done** — eight routes in the existing web app; route and zone drawing by a small in-house editor on react-leaflet, no new dependency (§7, §20) |
| 10 | Mobile and desktop | **Done** — the phone: drone events, snapshot, clip, location, acknowledge, escalate, open incident, dispatch a guard; incident update on the existing screens. Desktop: nothing separate — it is the web build (§21) |
| 11 | Reporting | **Done** — migration 0129. PDF and workbook per flight, stored with a checksum; immediate, daily, weekly and monthly emails through a retrying queue run by the drone runner (§22) |
| 12 | Analytics | **Done** — no migration. Patrol statistics, the analytical risk map, recurring locations, trends, mission success, false-positive and incident-conversion rates, and rule-based recommendations, all counted on request (§23) |
| 13 | Security and hardening | **Done** — migration 0130. A review against the platform's own rules, each property pinned by a test that walks the route table; what it found and changed is §24 |
| 14 | Final validation | **Done** — every suite, build, lint and type check, the migrations down to before the module and back, one patrol flown by the real runner process, and the security checks the repository has. What it found is §25 |

**Built in Phase 2 (migration 0123), 18 tables:** `drone_module_licenses`,
`drone_provider_configs`, `drone_edge_gateways`, `drones`,
`drone_maintenance_logs`, `drone_security_zones`, `drone_security_profiles`,
`drone_profile_rules`, `drone_routes`, `drone_waypoints`, `drone_missions`,
`drone_schedules`, `drone_patrol_sessions` (unique `(schedule_id, scheduled_for)`),
`drone_session_waypoints`, `drone_telemetry` (partitioned), `drone_events`
(→ `alerts`, `incidents`; `detection_id` without an FK, §19.3), `drone_event_media`,
`drone_event_cameras`. Every one points at existing rows; no existing table gained
a column.

**Built in Phase 11 (migration 0129), as deferred:** `drone_reports`,
`drone_report_recipients` and `drone_report_email_queue`.

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

## 20. Addendum — found while building Phase 9

### 20.1 The API could not set a drone's camera

`drones.camera_id` has existed since `0123`, and Phases 6–8 read events from it,
but the create and update bodies never accepted it — only a database write could
link one. Added to the module's own router (`routers/drones.py`) with its checks
(`DRONE_PATROL_API.md`, Camera link); no schema change.

### 20.2 Drone media takes its token in a header only

The existing image and video endpoints (evidence, streams) accept `?token=` so an
`<img>` or `<video>` can load them; the drone media endpoint does not. Rather than
widen its authentication, the screens fetch the file and show it from memory. The
same for playing a fixed camera's recording at the moment of an event.

### 20.3 Registration in existing files

Three existing frontend files gained lines, nothing changed: eight routes in
`App.tsx`, a "Drone Patrol" section in `Sidebar.tsx`, the drone permission codes
in `usePermission.ts`'s fallback matrix (the backend's `/me/permissions` remains
the source). One test added to `Sidebar.test.tsx`. Drone cameras stay visible in
the fixed-camera screens, per §3; naming them clearly on the fleet screen is the
recommended practice.

## 21. Addendum — found while building Phase 10

### 21.1 An alert does not say which drone event raised it

The alert list returns no `message_params`, so a client holding a drone alert
could not reach its event. The drone event list now filters by `alert_id` and
`incident_id` (the module's own router; no schema change, and the existing
alerts endpoint is untouched).

### 21.2 The phone has no video player and no map

Expo SDK 51 here ships neither `expo-av` nor a map library, and adding native
dependencies means a new build of the app. A clip therefore plays in the web
view the app already uses for live video, and location is the coordinates with a
hand-off to the phone's own maps app. Both avoid a new dependency; neither has
been seen on a device (see the test plan).

### 21.3 A worker's own alert on a drone camera has no link on the phone

The link from an alert to its event is offered on alerts the drone module raised
(`module_type = drone_patrol`). When the pipeline links a worker's alert instead
of raising its own, that alert opens as an ordinary alert; the event is still in
More → Drone Events. This is the phone-side face of the decision on whether
workers should alert at all on drone cameras (`DRONE_PATROL_AI.md`) — decided on
2026-10-06: they do, as built.

### 21.4 The existing dispatch screen cannot name a guard

`DispatchScreen` has no guard picker and posts an empty `guard_user_id` (its own
comment says the picker "would come … in a full impl"), so the phone's general
dispatch cannot name anyone. Left as found; dispatch from a drone event uses the
drone endpoint, which lists the guards on shift or picks the nearest free one.
Worth fixing separately.

*Fixed 2026-10-06:* the sheet lists the guards on duty now, from the live
attendance board, with those at the incident's site first; nothing is sent
until one is chosen, and the notes go under the name the server reads.

### 21.5 Registration in existing files

Four existing mobile files gained lines, nothing changed: the navigator, the More
menu, the alert detail screen and the Alerts module filter. The desktop app
needed none.

## 22. Addendum — found while building Phase 11

### 22.1 There is no shared email queue to reuse

"Use the existing email/notification queue": the platform has two, and neither is
general. `scheduled_reports` sends from the scheduler loop itself, for three fixed
report types; Virtual Patrolling's `virtual_patrol_email_queue` is keyed to its own
schedules and sessions. Extending either would change existing behaviour, so the
drone module has its own queue table and reuses what *is* shared: Virtual
Patrolling's backoff and period arithmetic (imported, not copied), the platform's
SMTP settings, reportlab and openpyxl, and the evidence store.

### 22.2 No new permission was needed

`drone:report:read` and `drone:report:export` were seeded in 0123 for this. Export
covers the workbook and choosing recipients — emailing a report to an outside
address is exporting it — so no `…:email` permission was added, and no role grant
changed.

### 22.3 The object store has no read

`core/object_store.py` can put, delete and presign, not get. With evidence in S3
the report reads a snapshot through that module's client directly rather than
adding a function to a core file. That path, and writing reports to S3, are not
exercised by any test (see the test plan).

### 22.4 The runner's tenant list would have dropped owed reports

`drone_runner_tenants()` lists tenants with a live licence or a flight in the
air. A weekly summary is owed after the week ends — possibly after the licence
does. The report job has its own list, `drone_report_tenants()`.

### 22.5 Registration in existing files

`main.py` gained the router and its tag; the `drone-runner` service in
`docker-compose.yml` gained one environment line. Nothing else existing changed.

## 23. Addendum — found while building Phase 12

### 23.1 No analytics tables, and no chart library

The existing Analytics and Heatmap screens count from the live tables and draw
with layout boxes; there is no summary table and no chart dependency to reuse or
to break. The drone analytics do the same: aggregated in SQL when asked, drawn
with boxes. If a year of a large fleet's events proves too slow to count on
request, a summary table is the fix — a Phase 13 measurement, not a guess made now.

### 23.2 "Risk" needed two honest limits

The brief asks for a risk view "based on actual historical events and clearly
labelled as an analytical score". Two things follow that the brief does not
spell out. A false positive must weigh nothing, or the map would mark the places
where the AI is most often wrong. And the score only knows where drones have
looked: an area with no events may be safe or may be unpatrolled, and the
operations guide says so.

### 23.3 Risk colours are told apart by their labels

The module's HIGH and CRITICAL colours (in use since Phase 9) are close enough
that colour alone does not separate them reliably. They were left as they are;
every level on the analytics screen is written out beside its colour, and the
risk map carries a legend and a table.

### 23.4 Registration in existing files

`main.py` gained the router and its tag. In the web app: one route in `App.tsx`,
one sidebar entry, one tab in the drone screens' own tab bar, and an optional
prop on the drone map so a page scroll does not zoom it. Nothing else changed.

## 24. Addendum — found in the Phase 13 security review

### 24.1 The telemetry partitions had no row security

`drone_telemetry` has had forced row security since Phase 2, and the Phase 2 test
checked every drone *table*. Postgres does not pass row security to partitions,
a partition is an ordinary table, and the test skipped them: a session scoped to
one tenant that selected from `drone_telemetry_p20261001` by name saw every
tenant's flight track. Nothing in the application names a partition, so nothing
leaked through the API — what was missing was the guarantee the rest of the
system rests on.

The platform already had the cure — `apply_partition_rls()` (migration 0083),
run daily by the scheduler. The drone migrations never called it for the
partitions they created, which left them open until the scheduler's next daily
run; on the development machine, which is switched off at night, that run had
not happened since the table was created and all eight were open. Migration 0130
calls it. The test now checks partitions, selects from one by name as the
application's own role, and proves the daily sweep protects a partition made
later.

### 24.2 Taking evidence, or a report, left no trace

The brief requires "evidence accessed", "evidence exported" and "report
exported" in the audit log. Every operation that changes something was audited;
the two that take something *out* were not, because they are GETs. Now:

| Action | When | Detail |
|---|---|---|
| `drone.media.access` | A snapshot or clip is handed over | Its kind, event, flight and stored SHA-256 |
| `drone.report.export` | A flight's PDF or workbook, or a period workbook, is handed over | Format, flight or period and scope, size, and the SHA-256 of the exact bytes |

A refusal — held at the site, not found, over the limit — took nothing and
records nothing. Reading a report on screen is not an export. The platform's own
evidence and report downloads are not audited either; that is outside this
module and was left alone.

### 24.3 A failed email could show this system's own error

A report email that failed stored the exception's text as `last_error`, which the
Deliveries tab shows. For a mail server's refusal that is right — a full mailbox
is the organisation's to fix. For a failure of this system the text could hold a
query, a file path or the mail host's name. Now a mail server's own answer is
shown as it gave it; anything else is "could not be reached" or "could not be
prepared", and the full text goes to the server log.

### 24.4 The platform owner could not see the drone service

Phase 1 planned for Super Admin to see drone service health through the existing
console. Nothing had been built: a stopped runner showed nowhere. The console now
has a `drone-patrol` row (architecture, *Security review*). It reads `ok` on an
installation that does not use the module, so nothing changes for one.

### 24.5 Rate limits: what is actually in force

Checked by sending the requests rather than by reading the configuration.

- **Gateway endpoints.** The explicit limits work, counted per address and exact
  URL: 600 a minute on sync; 120 a minute per session on claim; 300 a minute per
  file on upload. They are counted inside the operation, so a request with a
  wrong credential is refused before it is counted. The limit therefore caps a
  working gateway that floods; it is not what stops guessing — the credential's
  256 random bits are. A lockout on wrong credentials was considered and left
  out: on the path a site uses to report an emergency, one misconfigured device
  locking out its neighbours is the worse failure.
- **Report exports.** Were unlimited. Rendering runs in the service that carries
  flight commands, so they are now limited to 30 a minute per person across the
  three exports.
- **Everything else** relies on the platform's default limit — which is not
  being applied (§24.6).

### 24.6 Found in the platform, reported, not changed

Both are outside the module and changing them would change existing behaviour,
so they were written up for a decision rather than fixed here.

1. **Nightly partition maintenance is failing.** The scheduler's
   `run_maintenance_proc()` aborts at its first table with `invalid input syntax
   for type uuid: ""`: pg_partman reads a partition by name, the partition's
   policy casts the tenant setting, and on a pooled connection that setting is an
   empty string. It is logged as a warning and skipped. On the development
   database the newest partition of `audit_logs`, `detections` and the others is
   September's, so the first row dated October goes to a default partition.
   `drone_telemetry` has partitions to December only because its migration
   created them recently.

   *Fixed on 2026-10-05, on the owner's decision (migration `0131`).* Two things
   in the paragraph above turned out to be incomplete. The cast error was the
   second reason, not the only one: the job ran as the app role, which does not
   own the tables and cannot add a partition to them at all, so it had never made
   one on any installation. And nothing had yet landed in a default partition on
   the development database — no audit or detection row had been written in
   October — so the first version of this note, which said rows were already
   there, stated an inference as a fact. The job now runs on the superuser
   session the audit archive uses; partitions keep being made for a quiet table;
   and rows stranded in a default are moved with the foreign-key triggers
   suspended, because pg_partman's own move was measured to cascade-delete every
   event row linked to a moved detection. See `docs/UPGRADE.md`.
2. **The default rate limit of 100 a minute is not applied** (*fixed 2026-10-05,
   §26.2*)**.** On this FastAPI
   version the limiter's middleware cannot match a request to its handler and
   exempts every route that is not explicitly limited: 115 requests in a few
   seconds to one route were all answered. Only the nine decorated endpoints are
   limited. The platform's own test for this sends five requests and so cannot
   fail.

### 24.7 Measured: a year of a large fleet

Phase 12 deferred this to measurement. One tenant, 20 drones flying 12 times a
day for a year — 87,600 flights and 262,800 events — on the 7.7 GB development
machine, as the application's database role, best of three:

| Query | Time |
|---|---:|
| Analytics overview, one year | 1.5 s |
| Analytics overview, 30 days (the screen's default) | 0.24 s |
| Risk map, one year | 0.74 s |
| Risk map, 30 days | 0.07 s |
| Recommendations, one year (recomputes both) | 3.8 s |
| Report summary, 92 days (22,000 flights listed) | 1.2 s |
| Report summary, 7 days | 0.06 s |
| Event list, first 50 | 0.04–0.09 s |
| Flight list, first 25 | 0.12 s → 0.02 s with the new index |
| Fleet dashboard | 0.08–0.14 s |

Counting on request holds; no summary table is needed at this size. The one
change made was the index the flight list was missing
(`idx_dps_tenant_created`). The year view of recommendations is the slowest thing
on any drone screen and is the first candidate if a larger fleet makes it
matter. Telemetry volume was not measured: nothing was flown at that scale.

### 24.8 Open: how long drone footage is kept

*Decided and built on 2026-10-05 — §26.3.*

The brief asks for retention to follow the platform's policy. Drone media was
deliberately kept out of the platform's evidence purge (§1.5, D5), because that
purge deletes evidence linked to open incidents. The consequence is that nothing
deletes drone snapshots, clips, stored reports or telemetry at all: they are kept
until someone decides otherwise, and the organisation's `evidence.retention_days`
does not apply to them. Deleting evidence cannot be undone, so no purge was built
on a guess. The decision needed: whether drone media not linked to an incident
should follow the organisation's evidence retention, and how long telemetry is
kept.

### 24.9 Registration in existing files

`services/platform_health.py` gained one probe in `collect()` — the drone row,
asked after the existing ones. Nothing else outside the module changed.

## 25. Addendum — found in the Phase 14 final validation

### 25.1 A flight never recorded how far it flew

Found by flying a patrol with the real runner process and reading the session it
left: `distance_m` was empty. Nothing in the flight code wrote it. The tests that
show a distance — the report, the period summary, the analytics — had each set
the column by hand, so every one passed while every real flight would have shown
"—" on the flight page, in its PDF and in the totals.

Now the distance is the length of the recorded track. It grows as positions
arrive (counting only samples later than the last one stored, so a resent sample
adds nothing) and is taken again from the whole stored track when the flight
ends. The first version of that final sum added half the Earth's circumference
to every flight: SQL's `LEAST` skips a NULL, and the first sample has no
predecessor. The test that caught it works the track length out independently.

### 25.2 Rolling the module back left one table behind

Downgrading the eight drone migrations removed every table, function, permission
and billing row — and left `template_public_drone_telemetry`, the template
pg_partman makes for a partitioned table. The platform's own migrations drop
theirs; 0123's downgrade now does too. Checked by downgrading the test database
to 0122, finding nothing with "drone" in its name, and upgrading again.

### 25.3 The runner logged every pass

The runner is meant to log only when something happens. Its test for "something
happened" on the flight pass asked whether the tally of commands existed, not
whether any count in it was above zero — so with one licensed organisation it
logged every two seconds, 43,000 lines a day. Seen in the real process's log;
fixed.

### 25.4 Small things

Six unused imports (two in the module, four in its tests), found by a static
pass written for the purpose because no linter is installed here. Removed.

### 25.5 Found in the platform, reported, not changed

Outside the module, and present before it:

- `scripts/migrate/check-migration-safety.sh --check` fails on migration 0104
  (`DROP TABLE` in an upgrade without the annotation the policy requires). The
  eight drone migrations pass, and the chain check passes for all 130.
- `scripts/security/check-container-hardening.sh --check` fails on
  `frontend.Dockerfile` (no non-root `USER`). The module added no Dockerfile; its
  two compose services are unprivileged, publish no ports and use the default
  network.
- `npm audit` on production dependencies: the web app has 3 high advisories
  (axios, react-router), the desktop 1 high (js-yaml), the phone 1 critical and
  48 high, nearly all in Expo and React Native build tooling. The module added no
  dependency to any of the four manifests.
- The two from Phase 13 (§24.6): nightly partition maintenance failing (since
  fixed, migration `0131`), and the default rate limit not applied.

No Python linter, type checker, dependency audit or secret scanner is installed
on this machine or in the backend image, and none was installed to run once.
Their place was taken by a standard-library static pass over the module's 67
Python files (compiles; no unused imports; no `eval`, `exec`, `pickle`, shell
calls or disabled certificate checks; every value interpolated into SQL is a
module constant or a clause built from bind parameters) and a pattern scan of
every line the module added for credentials (none).

### 25.6 The desktop release does not contain the drone screens

*Released as 1.0.2 on 2026-10-05 — §26.5.*

The desktop app is the web build inside an Electron shell, bundled at build time.
The released 1.0.1 installers were built on 2026-09-24, before the screens
existed, and contain none of them. The current code packages correctly — an
unpacked build made in a scratch folder contains the drone screens — but it was
not released: a release needs a version number and, when the owner is ready, the
code-signing certificate. Until then the drone screens are in the web app and on
the phone, not on the desktop.

## 26. Addendum — the open items, closed (2026-10-05)

On the owner's instruction to finish everything left open. Each of these changes
existing platform behaviour, which is why none was done without being asked.

### 26.1 Partition maintenance

Migration `0131` and the scheduler job, described under §24.6. One more cause
turned up when the fixed job still did not run on the development machine: the
scheduler compares each job's last run with the event loop's clock, which counts
from when the machine booted, and every "last run" started at zero. On a server
up for weeks that made every job due at start. On a machine booted an hour ago it
made the daily cycle wait for a full day of uptime — so a computer switched off
every night never ran its backup, its evidence purge or its partition
maintenance at all. Every job now starts as "never run".

### 26.2 The default rate limit

Replaced by one that is applied (`dependencies/rate_limit.py`). The original was
100 a minute per address. Sent as written, that would have broken the product:
behind the web proxy every browser has the same address, and one operator's
sixteen-camera live wall legitimately asks a single route 480 times a minute.
So the limit is per signed-in user and per route — 300 a minute, 1,200 for video,
live overlays and stored pictures — on reads only, because an abort or an SOS
refused for being "too many requests" would be worse than the load. If the
counter cannot be reached, requests go through.

The drone module's own limits are unchanged: report exports 30 a minute per
person; the gateway's explicit limits.

### 26.3 Retention

Built as proposed in §24.8, with two things made more conservative than the
proposal. Footage of a *confirmed event that is still open* is kept as well as
footage of an incident — nobody has finished with it. And a period of zero,
which the platform's setting validator accepts, is read as "not set" rather than
"delete everything tonight". Flight tracks are kept a year by default, per
organisation. Stored report documents are kept. See the operations guide.

### 26.4 The repository's security checks

- **Migration safety** passes: 0104's reason for dropping a table was written
  beside it without the marker the policy looks for.
- **Container hardening** passes: the web container runs as the unprivileged
  nginx user. It cannot bind ports below 1024, so it listens on 8080 and 8443
  inside and the published ports are unchanged; a one-shot compose step hands an
  existing certificate volume to the new user so an upgrade keeps its certificate.
  Tried on a volume left as root had it: the container now says what is wrong
  instead of failing on a bare permission error.
- **Dependencies:** the web app has no advisories left in what it ships (axios
  1.20.0, react-router 7.18.4); the desktop app's one high advisory is gone
  (js-yaml 4.3.2) and four moderate ones remain behind a breaking upgrade of its
  settings store; the phone's HTTP client is updated, and its remaining 1 critical
  and 47 high are all in Expo and React Native build tooling, which needs an SDK
  upgrade — a project of its own, not attempted. *The desktop's four are gone and
  the phone's are fewer — §27.4, §27.5.*

### 26.5 The desktop release

1.0.2: the first build with the drone screens, and with the updated HTTP client
and router. Unsigned, as 1.0.1 was — signing waits for the certificate. Same app
id and MSI upgrade code, so it installs over 1.0.1. *Followed by 1.0.3 — §27.4.*

### 26.6 The development stack

The API, scheduler and web containers were rebuilt from the current code and
recreated; until then the API had been running files copied into an image built
before the module, and the scheduler was two weeks behind the repository. The
drone runner now starts as its own container. On restart the scheduler ran its
daily cycle at once: partition maintenance for all fourteen tables, a backup, and
an evidence purge that found nothing past its period.

### 26.7 Still as built

Two design decisions, neither a defect: the AI workers still raise their own
alerts for drone cameras (the drone pipeline links and escalates them rather than
repeating them), and a gateway that cannot reach the centre finishes its flights
and starts no new ones.

---

## 27. The leftovers (2026-10-05)

§26 ended with four things not done: the phone app's build tooling, the desktop
app's four advisories, an ingestion container on an old image, and a line in
the restarted scheduler's log that had not been explained. All four were taken up
the same day.

### 27.1 The scheduler said every recording was missing

Restarted on current code, the scheduler ran its recording integrity sweep and
marked all ten recordings it checked `FILE_MISSING`. The files were all there.
The sweep re-reads each recorded file and compares its checksum — and the
scheduler had never been given the volume the files are on, so it was looking at
an empty directory. It has it now, read-only. The ten verify as `PASSED`.

A recording wrongly marked missing corrects itself: each sweep takes the hundred
least recently checked per organisation, so the marked ones come round again.

**The Kubernetes chart had the same gap and a worse one beside it.** It claims a
recordings volume and mounted it on the API alone. The recorder runs in the
ingestion pod, which had no such mount: on a cluster it wrote every recording to
its own pod's disk, where the API could not play it and a restart lost it. Both
pods now mount the claim the API already used, the scheduler's read-only.

What that fix is worth, exactly: Helm is not installed on this machine or in CI
(the chart's render and lint tests skip), so the chart was **not rendered**.
Three tests read the templates as text — the recorder and the API share a disk,
the scheduler can read it, every volume a pod mounts is one it declares. Render
it once (`helm template`) before relying on it.

One limit left as found: with `minio.enabled` the chart creates neither claim,
for any pod. Recordings are always files on disk, whatever the evidence store
is, so on that configuration they still have no volume. Changing it would give
every such installation a new 200 Gi shared claim it has never had — the
owner's decision, not made here.

### 27.2 One backup a day

Rotation kept "the newest seven files". That was sound while the daily cycle ran
once a day. Since §26.1 it also runs whenever the scheduler starts, so a machine
restarted seven times in a day would have kept seven copies of today and nothing
of the week before. Rotation is now by day: the newest of each day, for seven
days. A file whose name carries no date is never rotated.

### 27.3 The ingestion container

Rebuilt from the current code and recreated; healthy. With it, every backend
container running on the development machine runs the repository's code.

### 27.4 The desktop app: 1.0.3

The four moderate advisories were one: the schema validator inside the app's
settings store. The store went from 7.0.3 to 8.2.0 — the newest the app can load
as written — and the audit of what the desktop app ships now reports nothing.
The app's use of the store did not change and neither does the settings file:
tried with the app's own schema, an existing file is read, a value written, a
wrong type refused, a missing file given its defaults; then the packaged 1.0.3
code was started in the Electron runtime with an empty profile and wrote its
settings.

An attempt to keep the old store and replace only the validator under it was
abandoned: it loaded, and then could not report a refused setting properly.

Released as **1.0.3**: unsigned, same app id and MSI upgrade code, installs over
1.0.1 and 1.0.2.

### 27.5 The phone app's build tooling

`npm audit fix` was tried and undone. It "fixed" the tooling by installing a
second React Native, 0.87, inside the 0.74 the app is built on; the two screen
tests that mount a real component failed on it at once. The lockfile was put
back, and the seven packages with a fixed release inside the versions the app
already allows were updated by name — fourteen lockfile entries, none added or
removed, nothing the app imports. Type check, 170 tests and a real Android
bundle build pass.

| | Critical | High | Moderate | Low |
|---|---:|---:|---:|---:|
| Before | 1 | 47 | 22 | 1 |
| Now | 1 | 41 | 21 | 1 |

What is left is all in packages that run on the build machine, not in the app,
and every one of them is fixed by the same thing: moving the app from Expo SDK
51 to a current one (57 today), which takes React Native from 0.74 to 0.87 and
React Navigation and the notifications library to new major versions with it.
That was **assessed and not attempted**. Six SDK versions change the native
build, the navigation library and the notifications library under an app whose
job includes SOS and man-down alerts, and none of it can be proven without the
app on real Android and iOS devices. It is a project of its own, to be planned
with device testing. *The owner said to proceed, and it was done the same day —
§28.*

---

## 28. The phone app on Expo SDK 57 (2026-10-05)

§27.5 ended with the SDK upgrade assessed and not attempted. The owner said to
proceed.

| | Was | Now |
|---|---|---|
| Expo SDK | 51 | 57 |
| React Native | 0.74.5 | 0.86.3, on its New Architecture (the old one no longer exists) |
| React | 18.2 | 19.2 |
| React Navigation | 6 | 7 |
| TypeScript | 5.6 | 6.0 |
| Packages installed | 1,490 | 853 |
| Published advisories | 39, one critical | 3, none critical |

The three that remain are in tools that run on the build machine, and no
upgrade removes them: two (`braces`, `node-forge`) have no fixed release at all,
and the third (`uuid`) is pinned by the library that writes the iOS project.
`npm audit` counts every package that depends on them, so it still prints 25
"high"; they are those two advisories, counted 25 times.

### 28.1 What the upgrade broke, and what found it

1. **A style helper React Native removed.** `StyleSheet.absoluteFillObject` is
   gone, from the types and at run time. Eight places used it; spread into a
   style it would now have contributed nothing, silently — the camera in the
   check-in photo and the patrol scan would have had no size. *The type check.*
2. **Returning to an open screen.** Until version 7, `navigate()` to a screen
   already in a stack went back to it. Now it pushes a second copy: the
   dashboard's check-in reminder would have laid a new My Shifts over a patrol
   in progress, and choosing a site would have stacked a second camera list.
   Eight calls say `pop: true` and two use `popTo`, so each behaves as it did.
   *Reading all 29 navigation calls; then a test that mounts the real navigators
   with the real Dashboard and Sites screens. It fails against the screens as
   they were.*
3. **Notifications inside Expo Go.** The notifications package now throws while
   it loads in Expo Go on Android, which lost push in SDK 53: a full-screen
   error at every sign-in during development. The app skips registering there.
   A real build is not Expo Go and registers as before. *The emulator — nothing
   else showed it.*
4. **TypeScript 6** no longer accepts `baseUrl` and no longer includes the test
   globals by itself. *The type check.*
5. **The test setup** mocked a React Native file that no longer exists, and two
   Expo packages the app's own files rely on have to be named as dependencies
   or npm installs them out of reach. *Every test failing to start.*
6. **The splash colour** was a top-level setting that the configuration no
   longer has; it is the splash-screen plugin's now, same colour. *Expo's
   project check; then the generated native configuration, read back.*
7. **The Android build patch** the project carried for `expo-modules-core` is
   in the SDK itself. The patch and the tool that applied it are removed.

### 28.2 What was verified

- Type check; 174 tests (four new); Android and iOS bundles built to bytecode;
  Expo's project check, 21 of 21; the native configuration evaluated — seven
  plugins, the Android permissions and the iOS usage strings; a clean install
  from the lockfile.
- **On the Android 16 emulator**, in Expo Go 57, signed in as the read-only
  demo viewer against the development API: the login screen; sign-in; the
  session restored on restart from the token the SDK 51 app had stored, so
  secure storage survives the upgrade; the dashboard, laid out as in the SDK 51
  screenshots; all six tabs with data; all 25 More-menu screens that role can
  see; an alert's detail; Back. No error after the notifications fix.

### 28.3 What was not

- **iOS, at all.** There is no Mac here. The iOS bundle builds; nothing ran.
- **A release build.** Nothing was built with EAS. The native side changed with
  the SDK, so guards get this only from a new build, and that build is the
  first time the native code is compiled.
- **The hardware paths:** the camera and QR scan, GPS check-in, biometrics,
  push, and man-down — which was deliberately left alone, because signed in as
  a guard it raises a real SOS.
- **The on-screen keyboard over forms.** Android now draws the app edge to
  edge; whether a field at the bottom of a form stays above the keyboard was
  the next check when the emulator session ended. Look at this first on a
  device.
- Live video and clip playback in the web view.

### 28.4 Before guards get it

A new build and a pass on real phones, as §27.5 said. The minimum iOS version
is now 16.4 (Expo's SDK 56 notes) and the minimum Android is 7.0. The build
still needs `google-services.json`, which is not in the repository — as before.
Expo Go for SDK 57 is not in the app stores: Expo's command-line tool installs
it on an Android emulator, and the old one refuses the project.

### 28.5 Found on the way, not caused by the upgrade

Each was handed to the owner as its own task and left as found:

- A refused refresh token is retried in a loop until the API's rate limit stops
  it — five refusals and a 429 from one app start.
- The alert and incident detail screens look for their record in the newest
  page of the list, so on a busy organisation most open alerts answer "Alert
  not found".
- The Action Center sends "check in" to a screen name that does not exist, and
  "patrol due" to one it cannot reach.
