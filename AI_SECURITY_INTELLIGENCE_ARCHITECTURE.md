# AI Security Intelligence — Architecture

**As of:** 2026-10-05 · **Phases 1–2 of 15 built**: the gap analysis, and the
normalised security event pipeline (migration `0132`).

This document describes what exists. What is not yet built is listed at the end
and is not described as if it were. The analysis and the plan are in
`AI_SECURITY_INTELLIGENCE_GAP_ANALYSIS.md`.

## Principles

- **AI recommends; a person decides.** The layer detects, relates, assesses and
  recommends. Authorised security staff make the decision, and only a decision
  made by a person causes anything to happen.
- **The runner reads and records. It does not act.** The background process has
  no code path to dispatch a guard, open or change an incident, acknowledge an
  alert, command a drone or operate a door. It imports only its own `intel_*`
  services and the database, and writes only `security_*` tables. Two tests
  hold that line.
- **Additive.** Every table is new and prefixed `security_`. No existing table,
  policy, worker or alert producer was changed. The files that gained a line are
  listed under *Touch points*.
- **Unknown is a value.** What a source does not say is left empty and shown as
  not known. A face that matched nobody is *unknown*, never *unauthorised*.
- **Off until asked.** Nothing about a tenant is read until that tenant's
  administrator switches `intel.enabled` on.
- **Extra by design.** If the runner is stopped, alerts, pushes, incidents and
  video carry on exactly as before.

## The pipeline

```
existing sources                                   built?
   │  read-only
   ▼
NORMALISE ─► security_events                        yes  (phase 2)
   ▼
CONTEXT                                             no   (phase 3)
   ▼
CORRELATE ─► situations                             no   (phase 4)
   ▼
NORMALITY · RISK                                    no   (phase 5)
   ▼
RECOMMEND                                           no   (phase 6)
   ▼
HUMAN DECISION ─► ACTION                            no   (phase 7)
```

## Data model

| Table | Holds |
|---|---|
| `security_events` | One row per source record, in the common shape. Unique on `(tenant_id, source_table, source_id)`, so reading a source twice inserts nothing |
| `security_ingest_cursors` | Per tenant and source: where reading starts, when it last ran, how many it has read, and the kind of the last error if there was one |

Both have `FORCE ROW LEVEL SECURITY` with the platform's standard tenant policy.
Foreign keys to sites, cameras, drones, alerts and incidents are `ON DELETE SET
NULL`: removing a camera or an alert never removes the record that something
was reported. `detection_id` is a reference without a key, because `detections`
is partitioned.

A row is **not a copy** of its source. It holds what later stages need — where,
when, what kind, who or what if the source knows, and the source's own
confidence, unaltered — and references for the rest. Media stays in `evidence`
and `recordings`. The alert's status stays on the alert.

| Column | Meaning |
|---|---|
| `source_type` | `CCTV_AI`, `DRONE_PATROL`, `VIRTUAL_PATROL`, `LPR`, `FACE_RECOGNITION`, `ACCESS_CONTROL`, `ALARM`, `GUARD`, `SENSOR`, `SYSTEM`, `OTHER` |
| `event_type` | The source's own code where it has one (`intrusion.zone_breach`, `guard.sos`, `drone.intrusion`) |
| `subject_kind` | `PERSON`, `VEHICLE` or `NONE` |
| `subject_ref` | An identifier, **never a name**: a number plate, or a watchlist entry's id. Empty when the source identified nobody |
| `subject_verdict` | `ALLOW`, `BLOCK` or `UNKNOWN` — what a watchlist said, where one was asked |
| `confidence` | The model's certainty about what it saw, 0–1, copied. Empty for sources that have none (an alarm panel) |
| `severity` | The platform's five: `info`, `low`, `medium`, `high`, `critical` |
| `attributes` | A few named facts per source (zone, plate details, the drone's own risk). Not the source row |
| `status` | `NEW` when read; `LINKED` once it belongs to a situation (phase 4) |

`security_intel_tenants()` lists the tenants with the feature on. It is
`SECURITY DEFINER` because the runner connects outside any tenant, where
`tenant_settings` shows it nothing; it returns tenant ids and nothing else.

## Normalisation

`backend/app/services/intel_events.py`. Each source has a pure mapping function
(`from_*`) that takes a row and returns the common shape with no database, and
a select that finds the rows not yet read.

| Source | Read from | Becomes | Condition |
|---|---|---|---|
| AI workers (intrusion, PPE, crowd, fire/smoke, weapon, behaviour, tampering, abandoned, fall) | `alerts` + the detection | `CCTV_AI` | — |
| LPR | `alerts` + `lpr_events` | `LPR`, vehicle, plate, watchlist verdict | — |
| Face recognition | `alerts` + `face_events` | `FACE_RECOGNITION`, person, watchlist entry and verdict if matched | — |
| Access control | `alerts` (`access`) | `ACCESS_CONTROL` | denied, forced, tamper — the events that raise an alert |
| Alarm panels | `alerts` (`alarm`) | `ALARM` | — |
| IoT sensors, fleet GPS | `alerts` (`iot`, `gps`) | `SENSOR` | — |
| Drone flight problems | `alerts` (`drone_patrol`) | `DRONE_PATROL` | not the alert of a drone *event* — that is read below |
| Drone sightings | `drone_events` | `DRONE_PATROL`, carrying the drone's own risk as evidence | verified only |
| Guard SOS, and a man-down nobody cancelled | `incidents` (`guard.sos`) | `GUARD` | — |
| Virtual patrol | `virtual_patrol_session_answers` | `VIRTUAL_PATROL` on the camera checked | exceptions only |
| Camera stopped sending | `camera_health_events` | `SYSTEM` | `stream_disconnected` |
| Any other alert module | `alerts` | `OTHER` — kept, never dropped for being unknown | — |

**Not read:** `payroll` and `roster` alerts. They are about the workforce and
stay on the screens that handle them.

Two details that are easy to get wrong and are tested:

- **A guard's SOS takes its place from the guard.** The incident an SOS opens is
  hung on an arbitrary active camera so that it shows in the queue. That camera
  says nothing about where the guard is, so the event takes the position the
  phone reported and the site of the shift the guard was working.
- **A drone sighting is read once.** The drone module raises an alert for a
  verified event; that alert is skipped and the event itself is read, which says
  more. Its risk score comes along as `drone_risk_score` — evidence, not this
  layer's own judgement.

## Reading

- **Once per source record.** The select skips what is already in
  `security_events`, and the insert ignores a conflict, so a second pass — after
  a crash, or from the look-back below — adds nothing.
- **Where it starts.** The first time a tenant's source is read, reading starts
  `INTEL_BACKFILL_MINUTES` back (60) and no further. Turning the feature on does
  not bring in a tenant's history.
- **The look-back.** Each pass re-reads the last `INTEL_OVERLAP_SECONDS` (120).
  A row is stamped when its transaction starts and visible when it commits;
  without the look-back a slow writer's alert would fall behind the cursor and
  never be read.
- **A backlog** drains `INTEL_INGEST_BATCH` (200) at a time, and rows stamped at
  the same instant are not lost between batches.
- **The database's clock**, not the application's, decides what is new.
- **One source failing does not stop the others.** The failure is counted, and
  recorded on that source's cursor as the *kind* of error only — an error's text
  can carry row contents.

## The runner

`python -m app.intelligence_main`, compose service `intelligence-runner`. Its
own process so that nothing it does can hold up the API or the scheduler.

| | |
|---|---|
| Pass | Every tenant with the feature on, every source, under that tenant's setting, in its own sessions |
| Cadence | `INTEL_RUNNER_TICK_SECONDS` (3) |
| Wake | Early, when a tenant's event channel announces an alert, incident, SOS or camera change. A nudge only: the database is what is read, so a missed message costs a tick and never an event |
| Rest | At least `INTEL_RUNNER_MIN_GAP_SECONDS` (0.5) between passes, so an alert storm cannot turn it into a busy loop |
| Heartbeat | `intel_runner:heartbeat` in Redis, 60 s: when, whether the last pass ran clean, and counts. Never a tenant's data |
| Redis down | The tick still runs; the heartbeat is skipped and logged |
| Ports, volumes | None |

## Configuration

| Setting | Where | Default |
|---|---|---|
| `intel.enabled` | Tenant setting, through the settings API (`settings:write`) | off |
| `INTEL_RUNNER_TICK_SECONDS`, `INTEL_RUNNER_MIN_GAP_SECONDS` | Runner environment | 3, 0.5 |
| `INTEL_BACKFILL_MINUTES`, `INTEL_OVERLAP_SECONDS`, `INTEL_INGEST_BATCH` | Runner environment | 60, 120, 200 |

## API

`backend/app/routers/security_intelligence.py`. Read-only. A caller restricted
to certain sites sees those sites' events; an event with no site is not shown to
them, and an event they may not see answers 404, the same as one that does not
exist.

| Method | Path | Permission | Returns |
|---|---|---|---|
| GET | `/security-intelligence/status` | `intel:read` | Whether the layer is on, the runner's state (`running`, `degraded`, `stopped`, or `unknown` when it could not be asked), each source's cursor, and events in the last 24 hours by source |
| GET | `/security-intelligence/events` | `intel:read` | Events, newest first. Filters: `site_id`, `camera_id`, `source_type`, `severity`, `from`, `to`; `limit` ≤ 200, `offset` |
| GET | `/security-intelligence/events/{event_id}` | `intel:read` | One event |

## Permissions

Seeded by `0132`. Only `intel:read` is used so far; the rest are in place for
the phases that need them.

| Permission | Admin, Manager | Supervisor | Operator | Guard | Viewer |
|---|:-:|:-:|:-:|:-:|:-:|
| `intel:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `intel:recommendation:read` | ✓ | ✓ | ✓ | ✓ | |
| `intel:decide` | ✓ | ✓ | ✓ | ✓ | |
| `intel:override` | ✓ | ✓ | ✓ | | |
| `intel:approve` | ✓ | ✓ | | | |
| `intel:manage` | ✓ | | | | |
| `intel:feedback:export` | ✓ | | | | |

The **platform owner** (super admin) and the **client** role hold none. The
platform owner is not a customer's security operator; an assessment describes a
site's weaknesses and is not for the site's own customer.

A guard's `intel:decide` does nothing by itself: the decision policy of phase 7
says at which sites and up to which risk a guard may decide, and until an
administrator sets one it lets no guard decide.

## Tenancy and security

- Every row is tenant-scoped under RLS. The runner works one tenant at a time
  and each tenant's work is in its own sessions.
- The runner's tests run as the application's own database role with two
  tenants, and first check that the connection is not a superuser — the
  project's other tests connect as one, which no tenant policy applies to.
- A tenant that has not switched the feature on has none of its rows read and
  gets no cursor.
- Names are not copied. A guard's SOS carries the guard's user id; the name
  stays on the user.

## Touch points in existing files

| File | Addition |
|---|---|
| `backend/app/main.py` | Registers the router |
| `backend/app/core/config_keys.py` | The `intel.enabled` setting |
| `docker/docker-compose.yml` | The `intelligence-runner` service |

## Running it

```bash
docker compose -f docker/docker-compose.yml up -d intelligence-runner
```

Then, as a tenant administrator, `PUT /api/v1/settings/intel.enabled` with
`{"setting_value": true}`, and read `GET /api/v1/security-intelligence/status`.

On the 7.7 GB development machine the runner is left stopped, like the drone
runner; its behaviour is covered by tests that call its functions against the
test database.

## Tests

`backend/tests/test_intel_events.py` (54): each source's mapping with nothing
running; reading from the database, once, from where it should, and never the
wrong rows; only for tenants that asked, and never another tenant's rows; the
runner's imports and writes; the API's permissions, site scope, filters and the
switch; the schema. `backend/tests/test_intel_docs.py` checks the API table
above against the application's route table.

## Not built yet

Context and site profiles (3) · correlation and situations (4) · normality and
risk (5) · recommendations (6) · human decisions, actions and the decision
policy (7) · the command centre screens (8) · the guard's phone (9) · drone and
virtual patrol integration beyond reading their events (10) · the unified
timeline (11) · evidence and summaries (12) · the dashboard and site security
score (13) · feedback (14) · platform health for the vendor, the Helm
deployment and final validation (15).

Until phase 8 there is no screen: what exists is reachable through the API
above.
