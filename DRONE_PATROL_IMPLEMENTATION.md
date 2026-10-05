# Drone Patrol — implementation summary

**As of:** 2026-10-05 · the module's last migration **`0130`** (platform head `0131`) ·
all 14 phases complete, and everything left open at the final validation closed
(*After the final validation*, below).

An autonomous drone security patrol, built into Seventh AI Vision rather than
beside it. A drone is assigned to a site and flies a planned route on a schedule
or on request; its camera is one of the platform's cameras, so the AI workers
that already watch fixed cameras watch it too; what they see is judged in the
context of where the drone was and what that place is, becomes a security event
with a risk level, is checked against nearby fixed cameras, and — when it is
serious — becomes an incident in the platform's own incident system, with a guard
dispatched through the platform's own dispatch. Each flight leaves a record, a
PDF and a workbook.

**Everything here has been proven against a simulator. No physical drone,
manufacturer SDK, flight controller or real video stream has been connected**, and
nothing below claims otherwise. What a real aircraft still needs is the last
section.

The other documents: `DRONE_PATROL_ARCHITECTURE.md` (how it is built),
`DRONE_PATROL_API.md` (the 100 operations), `DRONE_PATROL_OPERATIONS.md` (running
it), `DRONE_PATROL_EDGE.md` (the site gateway), `DRONE_PATROL_AI.md` (detection to
event), `DRONE_PATROL_PROVIDER_INTEGRATION.md` (adding a real aircraft),
`DRONE_PATROL_TEST_PLAN.md` (what is tested and what is not) and
`DRONE_PATROL_GAP_ANALYSIS.md` (the analysis, and what each phase found).

---

## Implementation summary

### Completed

| Phase | What | Migration |
|---|---|---|
| 1 | Repository analysis before any code | — |
| 2 | Data model, with its rules in the database | 0123 |
| 3 | API and the licence that gates it | — |
| 4 | Provider abstraction, simulator, pre-flight, commands, the drone runner | 0124 |
| 5 | Site edge gateway: flies offline, buffers, syncs exactly once | 0125 |
| 6 | Detection to security event: context rules and the risk engine | 0126 |
| 7 | CCTV correlation and corroboration | 0127 |
| 8 | Incident, guard dispatch, verify with drone | 0128 |
| 9 | Web screens | — |
| 10 | Phone screens; desktop is the web build | — |
| 11 | Patrol report (PDF, workbook) and scheduled emails | 0129 |
| 12 | Analytics, the analytical risk map, recommendations | — |
| 13 | Security review and hardening | 0130 |
| 14 | Final validation | — |

### Modified

Twelve files that existed before the module, each by addition. Across all of
them two lines were replaced (each by a superset of itself) and none removed.
No file was deleted or renamed, and no existing table was altered.

| File | What was added |
|---|---|
| `backend/app/main.py` | Seven drone routers and their documentation tags |
| `backend/app/services/platform_health.py` | One probe: the `drone-patrol` row on the platform console |
| `docker/docker-compose.yml` | Two services: `drone-runner`, and `drone-edge` under its own profile |
| `docker/.env.example` | The gateway's two settings, empty |
| `frontend/src/App.tsx` | Nine routes |
| `frontend/src/components/layout/Sidebar.tsx` | The Drone Patrol section |
| `frontend/src/components/layout/Sidebar.test.tsx` | Its test |
| `frontend/src/hooks/usePermission.ts` | The sixteen drone permissions per role |
| `mobile/src/navigation/index.tsx` | Three screens |
| `mobile/src/screens/MoreMenuScreen.tsx` | One menu row |
| `mobile/src/screens/AlertsScreen.tsx` | One filter chip |
| `mobile/src/screens/AlertDetailScreen.tsx` | "View the drone event" on drone alerts |

In the database, the migrations insert rows into three existing tables —
`permissions`, `role_permissions` and `billing_modules` — and change nothing else
that existed.

### Created

100 files, 30,360 lines:

| Kind | Files |
|---|---:|
| Migrations | 8 |
| Backend code (routers, services, providers, edge gateway, two entry points) | 36 |
| Backend tests | 21 |
| Web (API client, components, screens, tests) | 20 |
| Phone (API client, helpers, screens, tests) | 7 |
| Documents | 8, and this one |

---

## Database

**Migrations:** `0123`–`0130`, eight in a single chain, each with a downgrade.
Rolling all eight back leaves nothing of the module behind (checked in Phase 14);
the telemetry partitions keep their row security on the one downgrade that
would otherwise remove it.

**Models:** none in the ORM sense — like the rest of the platform, the module
speaks SQL through `text()`. 26 tables, all `drone`-prefixed:

| Area | Tables |
|---|---|
| Licence | `drone_module_licenses` |
| Fleet | `drones`, `drone_provider_configs`, `drone_edge_gateways`, `drone_maintenance_logs` |
| Planning | `drone_security_zones`, `drone_security_profiles`, `drone_profile_rules`, `drone_routes`, `drone_waypoints`, `drone_missions`, `drone_schedules` |
| Flying | `drone_patrol_sessions`, `drone_session_waypoints`, `drone_session_commands`, `drone_telemetry` (partitioned by month) |
| Edge | `drone_sync_receipts` |
| Events | `drone_observations`, `drone_events`, `drone_event_media`, `drone_event_cameras`, `drone_camera_coverage`, `drone_verification_requests` |
| Reports | `drone_reports`, `drone_report_recipients`, `drone_report_email_queue` |

The rules live in the database, not only in the code: 105 check constraints
(every status and vocabulary), 104 foreign keys (a tenant's deletion cascades;
everything else sets NULL, so deletion never blocks), and unique indexes that
make the important things impossible rather than unlikely — one live flight per
drone, one session per scheduled run, one telemetry sample per drone and instant,
one report email per flight.

**Indexes:** 101 on the 26 tables, including primary and unique keys. Period
queries walk `(tenant, time)` and `(site, time)` indexes; the open-events and
live-flights lists use partial indexes; two were added from measurement in
Phase 13.

**RLS:** forced on all 26 tables with the platform's own policy text, and on
every telemetry partition (34 policies). Three `SECURITY DEFINER` functions let
the workers and the platform console learn *which* tenants have work, or count
across them, without reading any tenant's rows: `drone_runner_tenants()`,
`drone_report_tenants()`, `platform_drone_health()`.

**Permissions:** 16 (`drone:read`, `create`, `update`, `delete`, `operate`,
`drone:mission:create|update|execute|abort`,
`drone:event:read|acknowledge|investigate`, `drone:maintenance:read|manage`,
`drone:report:read|export`). Super Admin and the client role hold none: the
platform owner licenses the module and does not operate a customer's drones.

---

## Backend

**APIs:** 100 operations (45 GET, 31 POST, 13 PUT, 10 DELETE, 1 PATCH).

| Router | Operations | Covers |
|---|---:|---|
| `routers/drones.py` | 26 | Fleet, dashboard, entitlement, providers, gateways, camera coverage, maintenance, telemetry |
| `routers/drone_planning.py` | 29 | Zones, profiles and rules, routes and waypoints, missions, schedules, pre-flight, run |
| `routers/drone_operations.py` | 26 | Flights, track, commands, events and their decisions, media, CCTV, incident, dispatch, verify with drone |
| `routers/drone_reports.py` | 11 | A flight's report, period summary, recipients, deliveries |
| `routers/drone_analytics.py` | 3 | Overview, risk map, recommendations |
| `routers/drone_edge.py` | 3 | The gateway's sync, claim and upload (`X-Gateway-Key`) |
| `routers/platform_drone_licenses.py` | 2 | Super Admin grants the licence |

Every operation requires a permission or a gateway credential; creating,
changing and starting a flight also require the tenant's licence; reads, event
decisions and flight commands never do, so history stays readable and a drone in
the air can always be brought down.

**Workers:** two processes, both from the backend image.

| Process | Entry point | What it does |
|---|---|---|
| Drone runner | `python -m app.drone_runner_main` | Flies centrally-flown drones through their provider; carries out commands; polls drone health and sweeps lost links; turns AI detections into drone events; stores reports and sends report emails; writes a heartbeat |
| Site edge gateway | `python -m app.drone_edge_main` | At a site: flies that site's drones with the same provider code, keeps flying when the link is down, buffers everything in a local store and syncs it exactly once |

**Schedulers:** the runner's own schedule job creates the sessions that schedules
owe, in each schedule's time zone, once per run however often it ticks, and
records a run it was not there for as `MISSED`. The platform's scheduler was not
changed.

| Runner job | Every | Setting |
|---|---|---|
| Commands, launches, live flights | 2 s | `DRONE_RUNNER_TICK_SECONDS` |
| Health polls, lost-link sweep | 15 s | `DRONE_RUNNER_HEALTH_SECONDS` |
| Sessions owed by schedules | 30 s | `DRONE_RUNNER_SCHEDULE_SECONDS` |
| Detections to events, CCTV correlation | 3 s | `DRONE_RUNNER_AI_SECONDS` |
| Reports stored, emails sent (beside the loop) | 60 s | `DRONE_RUNNER_REPORT_SECONDS` |
| Expired footage and flight tracks deleted (beside the loop) | 6 h | `DRONE_RUNNER_RETENTION_SECONDS` |

**Events:** announced on the platform's existing realtime channel, in its
existing envelope, after the transaction that made them true has committed:
`drone_telemetry`, `drone_session_updated`, `drone_command_processed`,
`drone_status_changed`, `drone_event_created`, `drone_event_updated`,
`drone_event_cctv_updated`, `drone_event_dispatched`,
`drone_verification_updated`, `drone_media_recorded`, `drone_media_synced`,
`drone_gateway_status_changed`, `drone_sync_completed`, and the platform's own
`alert_created`, `incident_created` and `incident_updated`.

Alerts go into the platform's alert table with module `drone_patrol`
(`drone.preflight_blocked`, `drone.mission_failed`, `drone.mission_missed`,
`drone.comms_lost`, `drone.flight_fault`, `drone.command_failed`,
`drone.gateway_offline`, and one per security event that warrants it), so the
existing Command Centre, notification rules and phone pushes carry them with no
change.

Every operation that changes something writes to the platform's hash-chained
audit log, as does every piece of evidence handed over and every report
exported.

---

## Frontend

**Screens (web):**

| Screen | Route |
|---|---|
| Fleet dashboard: drones, health, gateways, providers | `/drones` |
| Missions | `/drone-missions` |
| Mission designer: route and waypoints drawn on the map, pre-flight preview, schedules | `/drone-missions/:id` |
| Security zones and profiles | `/drone-zones` |
| Flights, period summary, report recipients, email deliveries | `/drone-patrols` |
| One flight: live while it flies, a replay afterwards, with commands and its report | `/drone-patrols/:id` |
| Security events | `/drone-events` |
| One event: evidence, related CCTV, the response actions | `/drone-events/:id` |
| Analytics, risk map, recommendations | `/drone-analytics` |

**Components:** `components/drones/` — `droneUi.tsx` (status, risk and confidence
chips, battery bar, licence banner, the map), `MapEditors.tsx` (the route and
zone drawing tools, built on the map library already in the app),
`charts.tsx` (column chart, bar list, stat tile — layout boxes, no chart
library), and the helpers `droneFormat.ts` and `geo.ts`. `api/drones.ts` is the
client.

**Routes:** nine, flat, in the existing router, each behind the existing
permission guard; a "Drone Patrol" section in the existing sidebar. No new
dependency was added to the web app.

**Phone:** two screens — the drone events list and one event with its snapshot,
clip, location hand-off to the maps app, and acknowledge, escalate, open incident
and dispatch a guard — plus a link from a drone alert to its event. No new
dependency.

**Desktop:** no code of its own — it is the web build in the Electron shell. The
1.0.1 installers predate the drone screens; 1.0.2 carries them — see *Deployment*.

---

## Edge

**Services:** `drone-edge` (`backend/app/drone_edge/`: `agent.py`, `store.py`,
`central.py`; entry point `app/drone_edge_main.py`), started only with
`--profile drone-edge` on a machine at the site. It shares the provider code with
the central runner and nothing else with the API.

**Sync:** the gateway authenticates with a credential shown once when it is
registered and stored centrally only as a hash. It records everything it does in
a local SQLite store in the same transaction that queues it for sending, numbers
each flight's updates, and resends a batch unchanged until it is acknowledged —
so a flight flown through an outage arrives complete and exactly once, a resent
batch gets its first answer, and one bad item never blocks the rest. Without the
centre it finishes the flights it has and starts no new ones. The centre ties
every item to the gateway that sent it and records what it refused, and why.

**Recording:** a file is described first — kind, time, size, SHA-256 — and its
bytes are sent only when the site's recording policy asks for them: always,
never, or inside a time window. A file is accepted only if it is exactly the file
described and of a kind it claims to be. A file kept at the site is shown as
"held at the site", not as a broken link.

---

## AI

**Detection:** the platform's existing AI workers, unchanged. A drone's camera is
a row in the platform's camera table, so whichever modules are enabled on it —
number plates, faces, intrusion, PPE, crowd, fire and smoke, weapon, behaviour,
abandoned object, fall — run on its frames exactly as on a fixed camera's, and
write the same detections. A gateway can also report a sighting made at the site;
it goes through the same pipeline.

**Context:** `services/drone_ai.py` — pure rules over a detection and where it
happened: the security zone under the drone and its type, the zone's hours, who
and which vehicles it allows, whether a guard is on duty, the mission's security
profile and its per-module rules, how long and how often the thing was seen.

**Risk:** a score from 0 to 100 and a level (LOW from 15, MEDIUM from 35, HIGH
from 55, CRITICAL from 80), with every factor that contributed recorded on the
event and shown to the officer beside — never merged with — the AI's own
confidence. A sighting must be confirmed over several frames before it alerts;
an unconfirmed one is closed and, if it looked serious, raised for review.

**Correlation:** `services/drone_cctv.py` and `drone_cctv_correlation.py` — which
fixed cameras are near the event, which of them actually face it (where their
coverage has been surveyed), and whether one of them saw the same thing at the
same time. A matching fixed-camera detection verifies the event and raises its
risk; the officer is offered those cameras' live view and playback at the right
moment, without any stream address leaving the server.

No model was trained, tuned or evaluated for aerial footage. Whether the existing
models are accurate from the air is unmeasured — see *Known limitations*.

---

## Testing

**Tests:** 405 written for the module.

| Where | Tests | Kind |
|---|---:|---|
| Backend, `backend/tests/test_drone_*.py` (21 files) | 335 | Database, API, pure logic, the real gateway end to end, repository inspection |
| Web, `frontend/src/pages/drones/*.test.tsx` and the sidebar | 34 | Screens with the API mocked |
| Phone | 36 | API client, helpers, the event screen mounted |

**Passed:** all of them, and everything else. On the merged commit CI ran the
platform's whole suite: 3,200 backend tests, 1,119
repository-inspection tests, 132 web tests and 170 phone tests.

**Failed:** none.

### Final validation (Phase 14)

| Check | Result |
|---|---|
| Backend tests, whole platform (CI, clean database built from migration 0001) | 3,200 passed, 0 failed |
| Repository-inspection tests (CI) | 1,119 passed, 0 failed |
| Web: type check, lint, tests, production build | Clean · 0 errors (214 warnings, as before the module) · 132 passed · built |
| Phone: type check, tests | Clean · 170 passed |
| Backend image build and API start (CI) | Built; healthy |
| Migrations, from nothing to head (CI) | Applied |
| Migrations, the module rolled back and re-applied | 0130 → 0122 → 0130: nothing of the module left at 0122; 26 tables, 8 of 8 partitions protected and 16 permissions back at head |
| Migration chain check | 130 migrations, chain intact, every downgrade present |
| One patrol flown by the real runner process | Launched, paused, four worker-shaped detections became one verified CRITICAL event with its alert and incident, resumed, landed `COMPLETED`; 83 track samples, 214 m; 34 announcements heard on the tenant's channel; console row `ok` |
| Gateway process | Refuses to start without a credential; with one the centre does not know, says so once, keeps running, stops cleanly |
| Compose file | Parses; both drone services unprivileged, no published ports |
| Desktop packaging | The current code packages (unpacked build, scratch folder) and contains the drone screens |
| API document against the code | 100 operations documented, 100 served, every permission as written — now a test |
| Static pass over the module's 67 Python files | Compiles; no unused imports; no risky calls; nothing but constants and bind-parameter clauses interpolated into SQL |
| Credential scan of every line the module added | None |
| Dependency manifests | Unchanged by the module — no dependency added anywhere |

It found four defects in the module, all fixed (gap analysis §25): a flight never
recorded its distance; rolling the migrations back left one helper table; the
runner logged every pass; six unused imports.

### Definition of done

| Area | Requirement | Holds | Evidence |
|---|---|:-:|---|
| Fleet | Drone registration, status, health and heartbeat | ✓ | `test_drone_api`, `test_drone_runner` (lost link, recovery), gateway-reported health in `test_drone_edge_sync` |
| Mission | Mission, route, waypoints, zones, profile, schedule | ✓ | `test_drone_api`, `test_drone_geometry`, `test_drone_schedule`, scheduled runs in `test_drone_runner` |
| Execution | Pre-flight; a complete simulated mission; the session recorded; mission and failure states | ✓ | `test_drone_preflight`, `test_drone_simulator`, `test_drone_runner`; the real-process flight above |
| Video | Stream and recording architecture | ◐ | The drone's camera is a platform camera, so live view, recording and policy are the platform's. Proven with files of the right kind, not with video from an aircraft |
| Video | Event clips preserved; local and central storage policy | ✓ | `test_drone_edge_sync` (policy, window, checksum, format), `test_drone_edge_agent` (offline sighting and its snapshot) |
| AI | Existing workers process drone feeds | ◐ | The pipeline is proven with detections in the workers' exact shape. No worker has been run on drone video: there is none, and this machine cannot run the workers |
| AI | Context rules, risk evaluation, suspicious events | ✓ | `test_drone_ai`, `test_drone_ai_pipeline`; the real-process flight |
| Correlation | Related cameras identified; related events shown | ✓ | `test_drone_cctv`, `test_drone_cctv_pipeline` |
| Incident | Incidents from high-risk events; alerts to the Command Centre; acknowledge and investigate; guard dispatch | ✓ | `test_drone_incidents`; `alert_created` and `incident_created` heard in the real-process flight |
| Evidence | Snapshots, clips and metadata preserved; linked to incidents | ✓ | `test_drone_edge_sync`, `test_drone_reports`, `test_drone_incidents` |
| Reporting | PDF, Excel, scheduled email | ✓ | `test_drone_reports` builds every real document and queues, retries and sends through a recording sender. A real mail server was not used |
| Security | Tenant isolation, RLS, RBAC, audit | ✓ | `test_drone_security`, `test_drone_schema` |
| Edge | Offline buffering, synchronisation, duplicate prevention | ✓ | `test_drone_edge_store`, `test_drone_edge_sync`, `test_drone_edge_agent` |
| UI | Fleet dashboard, mission designer, live mission, investigation, replay, reports | ◐ | Every screen's behaviour is tested with the API mocked, and the analytics screen was looked at in a browser with sample data. The others have not been watched in a browser against a running flight — signing in needs a password this work never handles |
| Quality | Tests pass; build passes; no critical errors; nothing existing broken; documents match | ✓ | The table above; twelve existing files touched, by addition |

✓ holds as tested · ◐ holds as far as it can without hardware or a signed-in
session, and says so.

### Known limitations

- **Simulator only.** No aircraft, SDK, flight controller or real video.
- **AI from the air is unmeasured.** The existing models were trained on fixed
  cameras; their accuracy on aerial footage is unknown.
- **Not exercised:** a real mail server; evidence in an S3-compatible store; the
  phone on a device (a clip playing in its web view, a push arriving); the
  desktop app opened with the drone screens; the two compose services started as
  containers on the development machine (their processes were run inside the API
  container instead, because its image predates the module and the machine has
  7.7 GB of memory).
- **The desktop installers are unsigned**, as 1.0.1 was: Windows warns on first
  run until a code-signing certificate is supplied.
- **Load is measured, not tested.** A year of a 20-drone fleet was timed once;
  telemetry at fleet scale was not.
- **No Python linter, type checker, dependency audit or secret scanner** is
  installed here; a standard-library pass stood in for them.
- **The risk score is analytical, not predictive,** and its weights and the
  recommendation thresholds are judgements stated in the API so they can be
  argued with.

### After the final validation

The final validation reported five things outside the module and left three
decisions open. On 2026-10-05, on the owner's instruction, all were closed except
the two design decisions below (gap analysis §26):

| Was | Now |
|---|---|
| Nightly partition maintenance had never made a partition | Fixed: migration `0131`, the scheduler job on its superuser session, and a scheduler that runs its daily cycle at start instead of after a day of uptime |
| The platform's default rate limit was configured but never applied | Replaced by one that is: every read, per signed-in user and per route |
| Nothing deleted drone footage or flight tracks | Footage follows the organisation's evidence retention, keeping anything an incident or an open confirmed event depends on; tracks are kept a year by default |
| Migration-safety check failed on migration 0104 | Passes |
| Container-hardening check failed on the web image | Passes: the web container no longer runs as root |
| Published advisories in the client apps | None left in what the web app ships; the desktop's high one fixed; the phone's HTTP client updated. Remaining: the phone's build tooling (an Expo SDK upgrade) and four moderate ones in the desktop's settings store |
| Desktop 1.0.1 without the drone screens | 1.0.2 built with them |
| Development API and scheduler running pre-module images | Rebuilt and recreated; the drone runner starts as its own container |

With those, the platform's suite is 3,220 backend tests and 1,119
repository-inspection tests, and the module's own is 413 (343 backend in 22
files, 34 web, 36 phone).

### Open decisions

1. Should the AI workers skip their own alerts and incidents for drone cameras?
   Built: the drone pipeline links and escalates a worker's alert instead of
   repeating it.
2. Should a gateway launch scheduled runs while it cannot reach the centre?
   Built: no — it finishes what it has and starts nothing new.

How long drone footage is kept was the third; it is decided and built (above).

---

## Deployment

**Environment variables.** The module adds these; everything else it uses is the
platform's own (`DATABASE_URL`, `REDIS_URL`, `EVIDENCE_ROOT`, `STORAGE_BACKEND`
and the `S3_*` settings, `SMTP_*`, `CREDENTIALS_ENCRYPTION_KEY`).

| Variable | Where | Default | Meaning |
|---|---|---|---|
| `DRONE_RUNNER_TICK_SECONDS` | runner | 2 | Commands and live flights |
| `DRONE_RUNNER_HEALTH_SECONDS` | runner | 15 | Health polls, lost-link sweep |
| `DRONE_RUNNER_SCHEDULE_SECONDS` | runner | 30 | Sessions owed by schedules |
| `DRONE_RUNNER_AI_SECONDS` | runner | 3 | Detections to events |
| `DRONE_RUNNER_REPORT_SECONDS` | runner | 60 | Reports and their emails |
| `DRONE_RUNNER_RETENTION_SECONDS` | runner | 21600 | Expired footage and flight tracks; `0` never deletes |
| `DRONE_TELEMETRY_RETENTION_DAYS` | runner | 365 | How long flight tracks are kept, unless the organisation sets `drone.telemetry_retention_days` |
| `DRONE_EDGE_KEY` | gateway | — | The credential shown once at registration. Required |
| `DRONE_EDGE_CENTRAL_URL` | gateway | `http://api:8000` | Where the centre is |
| `DRONE_EDGE_DATA_DIR` | gateway | `/data/drone-edge` | Its local store and files |
| `DRONE_EDGE_TICK_SECONDS` / `DRONE_EDGE_HEALTH_SECONDS` | gateway | 2 / 15 | Its cadence |
| `DRONE_EDGE_PROVIDER_SECRETS` / `_FILE` | gateway | — | The aircraft credentials, installed at the site; the centre never sends them |

**Services.** `drone-runner` (central, always) and `drone-edge` (per site, only
where aircraft are flown from the site). Neither publishes a port.

**Migration commands.**

```bash
docker compose -f docker/docker-compose.yml exec api sh -c 'cd /app/backend && alembic upgrade head'
```

The API container also runs this when it starts. To remove the module's schema:
`alembic downgrade 0122`.

**Worker commands.**

```bash
docker compose -f docker/docker-compose.yml up -d drone-runner
docker compose -f docker/docker-compose.yml --profile drone-edge up -d drone-edge   # at a site
```

Then, per customer: Super Admin grants the licence
(`PUT /api/v1/platform/tenants/{id}/drone-license`), and the customer's
administrator registers a provider connection, drones, zones, a route and a
mission.

**Build commands.**

```bash
docker compose -f docker/docker-compose.yml build api      # the backend image, used by api, drone-runner and drone-edge
cd frontend && npm ci && npm run build                     # the web app
cd desktop && npm run dist                                 # the desktop app — a new release is needed, see below
```

The backend image must be rebuilt before the drone services are started: an
image built before the module does not contain it. The phone app needs a new
build through its usual release to carry the two drone screens.

**The desktop app.** Its installers bundle the web build, so the drone screens
reach desktop users only in a release built after them. **1.0.2** is that
release: built on 2026-10-05, unsigned like 1.0.1, with the same app id and MSI
upgrade code so it installs over it. The installers are in `desktop/release/`
(not in the repository). To build again: `cd desktop && npm run dist`.

**The web container** runs without root and listens on 8080 and 8443 inside; the
published ports are unchanged, and `docker compose up` hands an existing
certificate volume to it. **Rate limits** are `API_READ_RATE_LIMIT` and
`API_MEDIA_RATE_LIMIT`. Both are described in `docs/UPGRADE.md`.

---

## Remaining hardware integration

Nothing below exists yet. The module was built so that each is an addition in
one place rather than a change everywhere.

| Needs | What has to be done | Where it plugs in |
|---|---|---|
| **A physical drone** | An aircraft, with a dock if launches are to be unattended. Every state, command and failure path has only ever been produced by the simulator | Registered like any drone; its provider connection names the adapter below |
| **The manufacturer's SDK or cloud API** | An adapter implementing the provider interface — status, start mission, telemetry, pause, resume, abort, return to home — with the manufacturer's credentials, and its capability flags set honestly (a capability it lacks is refused to the operator, not attempted) | `services/drone_providers/`, registered in `drone_provider_registry.py`; the contract and its rules are `DRONE_PATROL_PROVIDER_INTEGRATION.md` |
| **The flight controller** | Mapping the aircraft's own mission format, waypoint actions (hover, gimbal, zoom), geofence and its lost-link, low-battery and fault behaviour onto the module's phases and events. Safety behaviour stays the aircraft's: the platform records and reports, it does not out-think the autopilot | Inside the adapter |
| **The camera stream** | The aircraft's video delivered as a stream the platform can ingest (RTSP or equivalent), registered as the drone's camera, so the existing AI workers and recorder take it. Then the unmeasured part: how well the existing models work from the air, and what a moving camera does to zone-based modules | The platform's existing cameras, streams, recording and AI workers; `DRONE_PATROL_AI.md` |
| **An edge gateway** | A machine at the site running `drone-edge`, with network reach to the aircraft or its dock, storage for footage, a correct clock, and its credential. Proven only against the simulator, on one machine | `DRONE_PATROL_EDGE.md` |
| **Aviation and operational configuration** | The operator's permits and the site's flight rules: permitted areas and heights, no-fly zones, hours, weather limits, visual-line-of-sight or beyond, privacy notices for recorded areas, and who may command the aircraft. In Singapore unmanned aircraft operations are regulated by CAAS and permits may be required depending on the aircraft and the operation — confirm with CAAS before any real flight | The site's zones, routes, schedules and roles; outside the software, the operator's own approvals |

Until those exist, physical drone integration is **not** complete, and this
document does not say it is.
