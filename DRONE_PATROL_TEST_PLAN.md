# Drone Patrol — Test Plan

**As of:** 2026-09-25 · Phases 1–8. **261 drone tests**, all passing, plus the
platform's repository-inspection suites (1,111) and the neighbouring suites the
drone module touches (204). This describes the tests that exist, what they prove,
and what no test here can prove.

## How to run

The backend tests run inside the API container, against the test database:

```bash
docker exec docker-api-1 python -m pytest /app/backend/tests/test_drone_*.py -q
```

The repository-inspection suites (OpenAPI lint, migration safety, container
hardening, secret scanning and more) run in a one-off container with the working
tree mounted, as CI does:

```bash
docker run --rm --network docker_default -v "$PWD:/app" -w /app -e DATABASE_URL="<app url>_test" -e REDIS_URL="redis://redis:6379/0" --entrypoint python docker-api:latest -m pytest -m repo_tree -q backend/tests/
```

**Run database-backed suites one at a time.** Each test deletes the tenants it
created; two runs against the same test database delete each other's data and
fail for reasons that have nothing to do with the code.

The screens' tests run with the frontend's own tooling:

```bash
cd frontend && npx vitest run src/pages/drones src/components/layout/Sidebar.test.tsx
```

And the phone's, with its own:

```bash
cd mobile && npx jest src/api/drones.test.ts src/lib/droneEvents.test.ts __tests__/droneEventScreen.test.tsx
```

CI (`.github/workflows/ci.yml`) runs every backend test, the repository
inspection, and the frontend and mobile checks on every push; nothing merges to
`main` without it.

## Principles

- **Row-level security is really enforced.** The app connects as `svc_app`,
  which cannot bypass RLS; the runner tests assert that before anything else.
  Fixtures are written by an admin connection; everything under test goes through
  the app's own connection. Isolation tests use two tenants.
- **Real database, real API.** Endpoints are called through the application
  (`httpx` + ASGI), not mocked; migrations are the ones production runs.
- **A chosen clock.** The runner, the edge agent and the AI job take `now`, so
  schedules, timeouts, night-time rules and watchdogs are tested at the moment
  they matter instead of by waiting.
- **Hardware-free.** The simulator provider stands in for a drone. Detections are
  written exactly as the AI workers write them. The edge gateway talks to the
  real API over a link the tests can cut.

## Suites

| Suite | Tests | Kind | What it proves |
|---|---:|---|---|
| `test_drone_schema.py` | 22 | DB | Phase 2 schema: RLS on every table, a tenant cannot write into another's, one flight per drone, unique scheduled runs, telemetry partitioning and dedup, cascades never block deletion |
| `test_drone_api.py` | 35 | API | Phase 3: every endpoint's permission, licence gate, site scoping (404 outside), validation with reasons, the one-site rule, event decisions and their race, licensing by Super Admin only |
| `test_drone_schedule.py` | 17 | pure | Schedules in the site's timezone: daily, weekly, selected days, specific dates, DST-free Singapore and a DST zone, half-open windows |
| `test_drone_geometry.py` | 15 | pure | Zone shapes, point-in-zone, route length, off-site waypoints |
| `test_drone_simulator.py` | 21 | pure | The simulator flies the plan, pauses, aborts, returns home, handles low battery, lost link, motor and battery faults; deterministic per session; one long gap flies the same mission as many short ticks |
| `test_drone_preflight.py` | 9 | pure | Every blocking check and warning, each with a reason; battery needs the estimate plus reserve |
| `test_drone_runner.py` | 18 | DB | Phase 4 end to end: manual and scheduled runs, blocked runs, commands (pause, resume, abort, cancel, duplicates, capability refusals), licence lapse never stops a flight coming down, alerts announced after commit, lost links, two tenants |
| `test_drone_edge_store.py` | 21 | pure | Phase 5 gateway store: ordered numbering, one-transaction record-and-queue, restart survival, the open batch re-offered unchanged, sample limits, refused items kept with reasons; the credential format; recording-policy rules; upload windows |
| `test_drone_edge_sync.py` | 25 | API | The gateway API: credential refusals indistinguishable, rotation, claiming with pre-flight, the runner keeping out, ordered and duplicate updates, resent batches, one bad item not blocking the rest, the EDGE_UNREACHABLE verdict corrected, command relay, health, events, file policy and uploads (checksum, format, not-asked-for), gateway offline with one alert, clock skew |
| `test_drone_edge_agent.py` | 9 | E2E | The real gateway: a whole patrol, drone health, flying through an outage and catching up exactly once, no new flight without the centre, restart mid-flight, a lost answer resent as the same batch, an operator's abort, an offline sighting and its snapshot uploaded, a malformed item set aside |
| `test_drone_ai.py` | 24 | pure | Phase 6 rules: the brief's two examples (HIGH at night in a restricted zone; LOW by day with a guard on duty), zones' hours and precedence, thresholds, unreliable modules, authorisation, verification, CCTV as a factor, levels, what alerts and what becomes an incident |
| `test_drone_ai_pipeline.py` | 11 | DB | Detections written as the workers write them become events: verification over several frames, unconfirmed sightings closed with a review alert, profile gating, worker alerts linked not repeated, context escalating past a worker's alert, allowed and blocklisted plates, re-reading adds nothing, detections after landing, pre-flight AI checks, gateway sightings |
| `test_drone_cctv.py` | 14 | pure | Phase 7 geometry: bearings, sectors and polygons, covering before nearby, facing-away cameras left out, what corroborates what |
| `test_drone_incidents.py` | 13 | DB + API | Phase 8: an incident in the platform's own system at the profile's level, at HIGH with no profile, from an `INCIDENT` zone; a worker's incident linked not duplicated; severity rising with risk; an officer's incident; the command card and the alert carrying it; guards ranked free and nearest first with the source of each position; dispatch through the existing dispatch; no free guard; resolution flowing back; verify with drone on a real simulated flight — pause, hold, three new detections, resume — and its refusals |
| `test_drone_camera_link.py` | 3 | API | Phase 9: a drone linked to its camera on create and update; the camera at the drone's site and in this organisation; one drone per camera, never a camera with fixed coverage |
| `frontend/src/pages/drones/drones.test.tsx` | 12 | UI | Phase 9 screens, API mocked: route length and zone drafts; events list with risk and AI confidence apart; the investigation offering each action only to a role that may take it, and a false positive needing a reason; the patrol list and its export gated; a blocked flight shown as a replay with why; live controls while flying; a new mission needing a site |
| `frontend/src/components/layout/Sidebar.test.tsx` | +1 | UI | The Drone Patrol section: every entry for an admin, only events for a guard |
| `test_drone_event_lookup.py` | 2 | API | Phase 10: an alert and an incident each lead to their drone event; the lookup stays inside site scoping and the tenant |
| `test_drone_clients.py` | 6 | repo | Every drone call in the web and mobile clients is an operation the API serves, with that method (and the scanner proven on each shape it reads); the event list accepts the clients' filters; the desktop content policy allows what the drone screens load; the phone registers its drone screens behind `drone:event:read` |
| `mobile/src/api/drones.test.ts` | 15 | unit | The phone's calls on the wire: paths, bodies, the paginated list unwrapped, a false positive refused without a reason before the round trip, "nearest guard" sent as null, media addressed to the signed-in server with the token in a header |
| `mobile/src/lib/droneEvents.test.ts` | 15 | unit | Which actions each role is offered (operator, guard, viewer; closed events; no second acknowledge, escalation or incident), wording, the maps link, which announcements refresh the screens, and the clip player page — token in a header, inputs unable to break out of it |
| `mobile/__tests__/droneEventScreen.test.tsx` | 6 | UI | The event screen mounted: the response in front of an operator, one-tap acknowledge, a false positive held until a reason is typed, a guard offered only the incident, a viewer told why there are no buttons, the link to the incident |
| `test_drone_reports.py` | 19 | DB + API | Phase 11, as the application's database user: the report as the flight's own record (unchanged by renaming the mission or route), the stored picture in the PDF and a plain statement when it is missing, held at the site, or the AI worker's; the workbook carrying the same numbers; a flight that never launched; stored once with a checksum matching the file, not before it has settled; the immediate email to those whose scope covers the flight and not to those added later; failure retried with backoff then left failed with its reason, and sent again on request; a claim abandoned mid-send taken again; summaries once per scope for a closed period with flights in it; the organisation's day, not UTC's, deciding the period; two tenants kept apart and a lapsed licence still served; who may read, export and choose recipients |
| `frontend/src/pages/drones/droneReports.test.tsx` | 12 | UI | The report tabs for those who may read reports and not for a guard; recipients listed with scope and frequency, paused, added only with a real address, read-only without export; a failed email with its reason and **Send again**; the period summary; the PDF and workbook offered once a flight has ended, the workbook only to those who may export; a refused export saying to wait, not "status code 429" |
| `test_drone_security.py` | 29 | DB + API + pure | Phase 13, walking the application's own route table: every partition under forced row security, one read by name as the application's role, and a later one protected by the daily sweep; all 100 operations behind a permission or a gateway credential, none answering without credentials, a user with no drone permission refused by every one; another tenant's ids answering 404 from all 66 id-addressed operations and another site's hidden from a supervisor, with every row of the victim's unchanged; no response carrying a stored secret, a credential hash, a password hash or a stream address; evidence and reports taken out recorded with their checksums, and every changing operation writing an audit entry; what a gateway may send; an unexpected failure and a failed email saying nothing of this system; the platform health verdicts, the heartbeat, and the counts seeing every tenant; reports limited per person, not per address |
| `test_drone_analytics.py` | 10 | pure + API | Phase 12: the score as weighted events per week, capped and levelled; nothing recommended below the thresholds, and each recommendation stating what it saw and marked system-generated; patrol statistics, mission success (cancelled and in-flight flights neither), the false-positive and incident-conversion rates; hours, weekdays and the daily trend in the organisation's own zone; areas ranked with false positives weighing nothing; hot spots and repeated intrusion locations; permission, site scope, tenant isolation; an empty period as zeros and no rate, not an error |
| `frontend/src/pages/drones/droneAnalytics.test.tsx` | 9 | UI | Equal values drawn as equal bars with only the largest labelled; headline numbers each saying what they are a share of; trends as separate charts; the risk map labelled as an analytical score with every level written, not only coloured; recommendations marked system-generated; the period control; the Analytics tab offered to those who may read reports |
| `test_drone_cctv_pipeline.py` | 7 | DB | Which cameras, best first; a fixed camera's matching detection verifying an event and raising its risk; same-plate only; live view and playback at the right offset with no stream address or credential exposed; correlation settling; surveying a camera and correlating again; coverage validation |

Beyond the drone suites: migration round trips (each drone migration down and up
on the test database), process smoke tests (the runner and the gateway start,
loop and stop cleanly; the gateway refuses to start without a credential), and
the neighbouring suites — alerts, alert rules, licences, billing, platform
licences, tenants, Virtual Patrolling, cameras, recording policy, evidence,
incidents and dispatch.

**The repository-inspection run is not safe alongside a database run either:**
it loads the same `conftest.py`, whose end-of-session sweep deletes every tenant
created while it ran — including another run's.

## What no test here can prove

- A real aircraft: its SDK, its telemetry, its video, its behaviour when a link
  drops. Everything is proven against the simulator.
- The AI on drone video: accuracy of any worker from the air. The pipeline is
  proven with detections in the workers' exact shape, not produced by the models.
- Site hardware: the gateway on real edge devices and networks.
- Load, continuously: a year of a large fleet was measured once in Phase 13 (gap
  analysis §24.7) and is not a test — nothing fails if a query slows. Telemetry
  at fleet scale was not measured at all.
- The screens in a browser against a live flight: their tests mock the API and
  stub the map, so they prove what each screen shows and offers, not the drawing.
- Analytics beyond the size measured: correct on the rows the tests write, and
  timed once on 87,600 flights and 262,800 events. And whether the score's
  weights and the recommendation thresholds suit a real site is a judgement no
  test makes — they are stated in the API response so they can be argued with.
- The security sweeps' reach: they prove what the API answers, as the roles the
  tests hold, for the operations that exist. They are not a penetration test:
  nothing here fuzzes inputs, attacks the token, or tests the deployment (TLS,
  the proxy, the object store's own access rules).
- The runner's heartbeat end to end, automatically: the verdicts, the write and
  the counts are tested. The real runner writing to the real Redis and the probe
  reading it back was done once by hand in Phase 13 (started, beat, read, stopped
  cleanly) and is not a test.
- Audit on the object-store path: with evidence in an S3-compatible store a file
  is handed over by redirect, and that branch writes its audit entry in code no
  test here runs.
- Mail actually leaving: the report tests build every real document and hand it
  to a sender that records instead of sending. The SMTP hand-off itself, a real
  mail server's limits on attachment size, and how a client displays the PDF are
  unproven. Reports with evidence in an S3-compatible store are written and read
  by code paths no test here exercises.
- The phone on a device: its tests run under jest with native modules stubbed.
  Not proven: a clip playing in the web view on Android and iOS, a snapshot
  loading over a real network, the maps hand-off, a push for a drone alert
  arriving. The desktop app was not rebuilt and opened for this phase; it is
  covered by the web tests and the content-policy check.
