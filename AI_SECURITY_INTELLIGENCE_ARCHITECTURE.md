# AI Security Intelligence — Architecture

**As of:** 2026-10-05 · **Phases 1–5 of 15 built**: the gap analysis, the
normalised security event pipeline (migration `0132`), the context engine with
site and camera profiles (`0133`), correlation into situations (`0134`,
described in `AI_EVENT_CORRELATION.md`), and normality and risk (`0135`,
described in `AI_RISK_ENGINE.md`).

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
CONTEXT                                             yes  (phase 3) — on request, and kept with each assessment
   ▼
CORRELATE ─► security_situations                    yes  (phase 4)
   ▼
NORMALITY · RISK ─► security_assessments            yes  (phase 5)
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
| `security_site_profiles` | What an administrator says a site expects: business hours by weekday, time zone, whether it closes on public holidays, criticality. One per site |
| `security_camera_profiles` | What a camera watches: an area name, criticality, whether it is a restricted area. One per camera; goes when the camera goes |
| `security_situations` | One matter, however many alerts fed it: number, title and severity of its most severe event, counts, sources, `ACTIVE` or `SETTLED` |
| `security_situation_events` | Each event's place in a situation, with the method, the reason in words and the confidence of the link. An event is in at most one situation |
| `security_camera_links` | Cameras an administrator has said are next to each other, and the walk between them |
| `security_assessments` | What the layer made of a situation, each time the answer changed: a label, the risk and every factor behind it, how unusual it is, three confidences, and what was known then. Added to, never changed: the application's role may only insert and read |

All have `FORCE ROW LEVEL SECURITY` with the platform's standard tenant policy.
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
| `status` | `NEW` when read; `LINKED` once it has been placed in a situation |

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
| Alarm panels | `alerts` (`alarm`) + the alarm event, zone and panel | `ALARM`, placed at the panel's site and the zone's own camera | — |
| IoT sensors | `alerts` (`iot`) + the sensor | `SENSOR`, placed at the sensor's site | — |
| Fleet GPS | `alerts` (`gps`) | `SENSOR`, at the position the tracker reported; no site | — |
| Drone flight problems | `alerts` (`drone_patrol`) | `DRONE_PATROL` | not the alert of a drone *event* — that is read below |
| Drone sightings | `drone_events` | `DRONE_PATROL`, carrying the drone's own risk as evidence | verified only |
| Guard SOS, and a man-down nobody cancelled | `incidents` (`guard.sos`) | `GUARD` | — |
| Virtual patrol | `virtual_patrol_session_answers` | `VIRTUAL_PATROL` on the camera checked | exceptions only |
| Camera stopped sending | `camera_health_events` | `SYSTEM` | `stream_disconnected` |
| Any other alert module | `alerts` | `OTHER` — kept, never dropped for being unknown | — |

**Not read:** `payroll` and `roster` alerts. They are about the workforce and
stay on the screens that handle them.

Three details that are easy to get wrong and are tested:

- **An alert's camera is not always where it happened.** An alarm panel's alert
  is attached to its zone's camera when the zone has one — and to an arbitrary
  camera of the tenant when it does not, because an alert once needed a camera
  to exist. So an alarm's place is taken from its panel and zone, a sensor's
  from the sensor, and a tracker's from the position it reported. An alarm put
  at the wrong site would be joined to events it has nothing to do with.
- **A guard's SOS takes its place from the guard.** The incident an SOS opens is
  hung on an arbitrary active camera for the same reason. That camera says
  nothing about where the guard is, so the event takes the position the phone
  reported and the site of the shift the guard was working.
- **A drone sighting is read once.** The drone module raises an alert for a
  verified event; that alert is skipped and the event itself is read, which says
  more. Its risk score comes along as `drone_risk_score` — evidence, not this
  layer's own judgement.

## Context

`backend/app/services/intel_context.py`. An event says "a person, at Gate 1,
0.87". The context says what that means there: is the site open, is the zone in
force, is anybody meant to be on site, was a door refused nearby a minute ago,
is a patrol under way, what has this camera reported before. It does not decide
how much any of it matters — that is risk (phase 5), which reads this.

Two halves. `load()` reads the facts from the database; `build()` turns facts
into a context with no database at all, so every rule is a test that needs
nothing running and the same facts always give the same context.

| Group | Asked of | Says |
|---|---|---|
| Place | site and camera profiles, `restricted_zones`, the drone event's zone | Criticality (the camera's overrides the site's) and where it came from; restricted area; each zone in force or not, and why |
| Time | site profile, tenant time zone, `public_holidays` | Local time; inside or outside business hours with the hours; a public holiday |
| People | the event's watchlist verdict, `shifts`, `visitors`, `work_permits` | Allowed, blocked or not identified; guards on shift; visitors signed in; contractor permits in force |
| Access | `access_events` at the site's doors, `alarm_events` on zones linked to the camera | Denied, forced and granted within ten minutes either side; alarms |
| Operations | `virtual_patrol_sessions`, `drone_patrol_sessions` | A virtual patrol in progress; a drone in the air |
| History | `alerts`, `incidents` at the camera, 30 days | Earlier alerts of this kind; the share marked false; incidents |

Four rules, each held by tests:

- **Every statement names its source** — the table or setting behind it — so an
  explanation is made only of things the platform recorded.
- **Unknown is an answer.** `business_hours` is null until someone sets it, and
  null means *not defined*: the context says so and does not claim "after
  hours". Criticality that is not set is not "medium". A false-positive share is
  not stated on fewer than five decided alerts. Everything not known is listed
  under `unknowns`.
- **Not identified is not unauthorised.** A face or plate that matched no
  watchlist is "not identified", and the context says the two are different.
  The words *unauthorised* and *intruder* do not appear.
- **As of the event, not as of now.** Guards on shift, visitors on site, a zone
  bypass, a patrol under way are all asked about the moment the event happened.
  A patrol or flight started more than six hours earlier and never closed is not
  counted as still under way.

`expected` lists what might ordinarily explain the event — the site was open,
visitors were signed in, a permit was in force, someone was let in nearby. It is
an input to normality (phase 5), not a conclusion.

**Business hours** are an object keyed by weekday (`mon` … `sun`), each a list
of `["HH:MM", "HH:MM"]` periods in the site's time zone. A day left out is a
closed day. A period that ends before it starts runs past midnight and belongs
to the day it starts on. A zone's schedule is judged by the same rule the zones
API and the intrusion worker already use.

One limit the tests record rather than hide: a contractor permit later marked
completed cannot be shown as in force at an earlier time, because the table
keeps a status and not when it changed.

## Correlation

`backend/app/services/intel_correlation.py`; the rules, the confidences and the
limits are in `AI_EVENT_CORRELATION.md`. In short: a new event joins an active
situation at its site only through a named rule — the same alert, the drone
module's own corroboration, the same plate or watchlist entry, the same camera
repeating itself, a door or an alarm and what a camera saw then, a neighbouring
camera, a drone near a camera, a virtual-patrol finding, a guard's SOS beside a
serious event — and otherwise opens a situation of its own. Every link stores
its method, a reason an officer can read, and how sure it is. Repeats are folded
as duplicates and nothing is dropped: the alerts are untouched.

It never joins across sites, and it never says two sightings are the same
person unless it has an identity for them.

## Normality and risk

`backend/app/services/intel_risk.py`; the factors, the points and the limits are
in `AI_RISK_ENGINE.md`. In short: each situation whose events have changed is
assessed. **Normality** is the camera's own habit — how often it has raised this
kind of alert in this hour of the week — and is not stated at all on too little
history. **Risk** is a score from 0 to 100 made of named factors, each with its
points and a sentence: the event's severity, a zone in force, the place's
criticality, the hour, a block or allow list, a door, how many kinds of source
agree, repetition, the camera's record, what was expected, and the drone's or a
guard's own alarm.

Three things are held to. What is not known adds no risk and lowers the *risk
confidence* instead. The model's confidence, the confidence of the correlation
and the confidence of the risk are three numbers and are never made one. And an
assessment is written once: a different answer is a new row beside the old one,
which the application's database role cannot alter.

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
| Pass | Every tenant with the feature on, under that tenant's setting, in its own sessions: normalise what is new from every source, place each new event in a situation and announce it, then assess the situations that changed and announce each new assessment |
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
| `intel.risk_weights` | Tenant setting, the same way: a multiplier from 0 to 3 per risk factor | every factor as shipped |
| `INTEL_RUNNER_TICK_SECONDS`, `INTEL_RUNNER_MIN_GAP_SECONDS` | Runner environment | 3, 0.5 |
| `INTEL_BACKFILL_MINUTES`, `INTEL_OVERLAP_SECONDS`, `INTEL_INGEST_BATCH` | Runner environment | 60, 120, 200 |
| `INTEL_SITUATION_QUIET_MINUTES`, `INTEL_CORRELATE_BATCH` | Runner environment | 30, 100 |
| `INTEL_ASSESS_BATCH` | Runner environment | 50 |
| Camera links | Per site, through the API (`intel:manage`) | none |

## API

`backend/app/routers/security_intelligence.py`. It changes no alert, incident or
other existing record; the only things it writes are the layer's own site and
camera profiles. A caller restricted to certain sites sees those sites' events
and profiles; an event with no site is not shown to them, and anything they may
not see answers 404, the same as something that does not exist. Request bodies
refuse fields they do not know.

| Method | Path | Permission | Returns |
|---|---|---|---|
| GET | `/security-intelligence/status` | `intel:read` | Whether the layer is on, the runner's state (`running`, `degraded`, `stopped`, or `unknown` when it could not be asked), each source's cursor, and events in the last 24 hours by source |
| GET | `/security-intelligence/events` | `intel:read` | Events, newest first. Filters: `site_id`, `camera_id`, `source_type`, `severity`, `from`, `to`; `limit` ≤ 200, `offset` |
| GET | `/security-intelligence/events/{event_id}` | `intel:read` | One event |
| GET | `/security-intelligence/events/{event_id}/context` | `intel:read` | The event's context: place, time, people, access, operations, history, with `statements` (each with its source), `unknowns` and `expected` |
| GET | `/security-intelligence/site-profiles` | `intel:read` | Every site the caller may see with its profile; `has_profile` false where nobody has described it |
| GET | `/security-intelligence/site-profiles/{site_id}` | `intel:read` | One site's profile and its cameras' profiles |
| PUT | `/security-intelligence/site-profiles/{site_id}` | `intel:read` `intel:manage` | Replace the site's profile. A field left out is no longer set. Audited |
| PUT | `/security-intelligence/site-profiles/{site_id}/cameras/{camera_id}` | `intel:read` `intel:manage` | Replace a camera's profile; the camera must belong to the site. Audited |
| GET | `/security-intelligence/site-profiles/{site_id}/camera-links` | `intel:read` | Which of the site's cameras are next to each other |
| PUT | `/security-intelligence/site-profiles/{site_id}/camera-links` | `intel:read` `intel:manage` | Replace the site's camera links. Audited |
| GET | `/security-intelligence/situations` | `intel:read` | Situations with their latest risk, the one heard from most recently first, or the highest risk first with `sort=risk`. Filters: `status`, `site_id`, `severity`, `risk_level`, `source_type`, `from`, `to` |
| GET | `/security-intelligence/situations/{situation_id}` | `intel:read` | One situation, its sources, every event with the reason it is there, and its latest assessment with the reasons behind it |
| GET | `/security-intelligence/situations/{situation_id}/assessments` | `intel:read` | Every assessment of the situation, oldest first |

## Permissions

Seeded by `0132`. `intel:read` and `intel:manage` are used so far; the rest are
in place for the phases that need them.

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
- The context of an event is built only from the caller's own tenant: an event
  pointed at another tenant's site or camera by id finds nothing there.
- Changes to a profile are written to the tenant's hash-chained audit log with
  the actor, their role, the site, the request id and the result
  (`intel.site_profile.update`, `intel.camera_profile.update`,
  `intel.camera_links.update`).

## Live events

Published on the tenant's existing channel, `tenant_events:{tenant}`, and
forwarded to that tenant's clients by the existing listener with no change to it:
`intel_situation_opened`, `intel_situation_updated` and
`intel_assessment_ready`. Each is saved before it is announced, and an
assessment that says what the last one said is not announced at all.

## Touch points in existing files

| File | Addition |
|---|---|
| `backend/app/main.py` | Registers the router |
| `backend/app/core/config_keys.py` | The `intel.enabled` and `intel.risk_weights` settings |
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

`backend/tests/test_intel_events.py` (57): each source's mapping with nothing
running; reading from the database, once, from where it should, and never the
wrong rows; only for tenants that asked, and never another tenant's rows; the
runner's imports and writes; the API's permissions, site scope, filters and the
switch; the schema.

`backend/tests/test_intel_context.py` (29): hours, holidays, overnight periods
and zone schedules with nothing running; that a site nobody has described is
never said to be after hours; that not identified is never reported as not
authorised; that every statement names its source; the facts read as of the
event's moment, with a row beside each that had ended or lies outside the
window; another tenant's facts never read; the profile API's permissions,
validation, site scope and audit entries; the schema.

`backend/tests/test_intel_correlation.py` (33): see `AI_EVENT_CORRELATION.md`.

`backend/tests/test_intel_risk.py` (39): see `AI_RISK_ENGINE.md`.

`backend/tests/test_intel_docs.py` (13) checks the API tables of these documents
against the application's route table, and the rules written in the correlation
and risk documents against the code.

## Not built yet

Recommendations (6) · human decisions, actions and the decision
policy (7) · the command centre screens (8) · the guard's phone (9) · drone and
virtual patrol integration beyond reading their events (10) · the unified
timeline (11) · evidence and summaries (12) · the dashboard and site security
score (13) · feedback (14) · platform health for the vendor, the Helm
deployment and final validation (15).

Until phase 8 there is no screen: what exists is reachable through the API
above.
