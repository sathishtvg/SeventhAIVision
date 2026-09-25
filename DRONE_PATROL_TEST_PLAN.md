# Drone Patrol — Test Plan

**As of:** 2026-09-25 · Phases 1–7. **247 drone tests**, all passing, plus the
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
| `test_drone_ai.py` | 23 | pure | Phase 6 rules: the brief's two examples (HIGH at night in a restricted zone; LOW by day with a guard on duty), zones' hours and precedence, thresholds, unreliable modules, authorisation, verification, CCTV as a factor, levels and what alerts |
| `test_drone_ai_pipeline.py` | 11 | DB | Detections written as the workers write them become events: verification over several frames, unconfirmed sightings closed with a review alert, profile gating, worker alerts linked not repeated, context escalating past a worker's alert, allowed and blocklisted plates, re-reading adds nothing, detections after landing, pre-flight AI checks, gateway sightings |
| `test_drone_cctv.py` | 14 | pure | Phase 7 geometry: bearings, sectors and polygons, covering before nearby, facing-away cameras left out, what corroborates what |
| `test_drone_cctv_pipeline.py` | 7 | DB | Which cameras, best first; a fixed camera's matching detection verifying an event and raising its risk; same-plate only; live view and playback at the right offset with no stream address or credential exposed; correlation settling; surveying a camera and correlating again; coverage validation |

Beyond the drone suites: migration round trips (each drone migration down and up
on the test database), process smoke tests (the runner and the gateway start,
loop and stop cleanly; the gateway refuses to start without a credential), and
the neighbouring suites — alerts, alert rules, licences, billing, platform
licences, tenants, Virtual Patrolling, cameras, recording policy, evidence.

## What no test here can prove

- A real aircraft: its SDK, its telemetry, its video, its behaviour when a link
  drops. Everything is proven against the simulator.
- The AI on drone video: accuracy of any worker from the air. The pipeline is
  proven with detections in the workers' exact shape, not produced by the models.
- Site hardware: the gateway on real edge devices and networks.
- Load: fleet-scale telemetry and detection volumes (Phase 13).
- The screens (Phase 9) and mobile (Phase 10).
