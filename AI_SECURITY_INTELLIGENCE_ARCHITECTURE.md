# AI Security Intelligence — Architecture

**As of:** 2026-10-05 · **Phases 1–9 of 15 built**: the gap analysis, the
normalised security event pipeline (migration `0132`), the context engine with
site and camera profiles (`0133`), correlation into situations (`0134`,
described in `AI_EVENT_CORRELATION.md`), normality and risk (`0135`, described
in `AI_RISK_ENGINE.md`), recommendations (`0136`) and human decisions with
their actions (`0137`), both described in `AI_DECISION_WORKFLOW.md` and
`AI_HUMAN_DECISION_MODEL.md`, the web screens (phase 8, below) and the guard's
phone with reports from the ground (phase 9, `0138`). **The
runner still does not act**: it reads, records and suggests. Something is
carried out only when a person decides it through the API.

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
  services and the database, and writes only `security_*` tables. Three tests
  hold that line; one follows its imports to their end and checks that they
  never arrive at the code that decides or acts.
- **Acting is one module, reached one way.** `intel_actions` is the only part of
  the layer that changes anything outside it. It is called by the decisions API,
  for a decision a signed-in person has made, and calls nothing but the
  platform's existing functions.
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
RECOMMEND ─► security_recommendations               yes  (phase 6) — suggestions only
   ▼
HUMAN DECISION ─► security_decisions                yes  (phase 7) — by a person, through the API
   ▼
AUTHORISED ACTION ─► security_actions               yes  (phase 7) — through the platform's existing functions
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
| `security_reviews` | That a person looked at what was suggested: once per person per assessment |
| `security_decisions` | What a person decided: who, in what role, the step, whether it followed or overrode what was suggested, the reason, the assessment and risk it was made on, and how the policy let it be made. Added to, never changed |
| `security_decision_approvals` | A second person's verdict on a decision that needed one. One per decision |
| `security_actions` | What the platform then did for a decision: the step, the existing function it went through, how it ended, and under whose authority |
| `security_decision_policies` | Who may decide: per role, how far alone and how far with approval. One for the organisation, at most one per site |
| `security_observations` | What a person reported from the ground about a situation: accepted, arrived, or what they saw, with a position when the phone gave one. A statement, not a decision. Added to, never changed |
| `security_recommendations` | What the layer suggests an officer do, per assessment: the step, the reason, how sure, whether it can be done now and if not why. A suggestion and nothing else; added to, never changed |
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

## Recommendations

`backend/app/services/intel_recommend.py`; the rules, the steps and the limits
are in `AI_DECISION_WORKFLOW.md`. In short: each new assessment gets a set of
suggested steps — watch, verify, view a camera, verify with a drone,
investigate, dispatch a guard, escalate, contact the site, open an incident —
chosen by rule from the kind of situation and its risk, each with a reason.

**A recommendation is a row. It does nothing**, and every response that carries
one says `is_decision: false`. A step that cannot be taken right now — nobody
on shift, no drone ready, an incident already open — is kept and says why. A
step that only looks is as sure as its rule; a step that sends someone or
raises something is no surer than the detection, the correlation or the risk it
rests on, so an incomplete picture puts looking first. A guard's SOS is the
exception: help is not held back by what is not known about the site.

## Decisions and actions

`backend/app/services/intel_decisions.py` (the rules and the records),
`intel_actions.py` (carrying out) and `backend/app/routers/security_decisions.py`;
described in `AI_HUMAN_DECISION_MODEL.md` and part 2 of
`AI_DECISION_WORKFLOW.md`. In short: a signed-in person with `intel:decide`,
the site, and the authority the **decision policy** gives their role at this
risk records what they have decided. If it follows a suggestion it is recorded
as followed; if it goes against one it is an override, accepted from someone
who may override and always with a reason. Where the policy lets the role decide
only with approval, nothing happens until a second person approves.

A decision in effect is carried out step by step through the platform's
existing functions — acknowledge, assign, open an incident, dispatch, resolve —
under the permissions of the person who decided or approved, and each step
leaves a row saying what it went through and how it ended. Every decision,
verdict and step is in the tenant's hash-chained audit log.

A situation carries `decision_status`, set only by a person; and what stands
behind any incident — nothing, one the platform opened by itself
(`PRELIMINARY`), or one a person opened or confirmed (`CONFIRMED`).

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
| Pass | Every tenant with the feature on, under that tenant's setting, in its own sessions: normalise what is new from every source, place each new event in a situation and announce it, assess the situations that changed and announce each new assessment, then write what it suggests for each new assessment and announce that it is ready |
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
| `INTEL_ASSESS_BATCH`, `INTEL_RECOMMEND_BATCH` | Runner environment | 50, 50 |
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
| GET | `/security-intelligence/situations` | `intel:read` | Situations with their latest risk and where they stand, the one heard from most recently first, or the highest risk first with `sort=risk`. Filters: `status`, `site_id`, `severity`, `risk_level`, `decision_status`, `open`, `source_type`, `from`, `to` |
| GET | `/security-intelligence/situations/{situation_id}` | `intel:read` | One situation, its sources, every event with the reason it is there, its latest assessment with the reasons behind it, where it stands, and what stands behind any incident |
| GET | `/security-intelligence/situations/{situation_id}/assessments` | `intel:read` | Every assessment of the situation, oldest first |
| GET | `/security-intelligence/situations/{situation_id}/recommendations` | `intel:read` `intel:recommendation:read` | What the layer suggests doing, surest first, with the reason for each and why any cannot be done now. Always `is_decision: false` |
| POST | `/security-intelligence/situations/{situation_id}/reviews` | `intel:read` `intel:recommendation:read` | Records that the caller looked at what was suggested |
| GET | `/security-intelligence/my-situations` | `intel:read` | The open situations in front of the caller; for a guard, what they were dispatched to and their shift's site where the policy lets a guard decide |
| POST | `/security-intelligence/situations/{situation_id}/observations` | `intel:read` `intel:decide` | Records a report from the ground. Changes nothing else |
| GET | `/security-intelligence/situations/{situation_id}/observations` | `intel:read` | What was reported from the ground, oldest first |
| GET | `/security-intelligence/situations/{situation_id}/authority` | `intel:read` | What the caller may decide here, how, and why not |
| GET | `/security-intelligence/situations/{situation_id}/responders` | `intel:read` `intel:decide` | The guards and senior staff a decision can name, to choose from |
| POST | `/security-intelligence/situations/{situation_id}/decisions` | `intel:read` `intel:decide` | Records the caller's decision and carries it out, or holds it for approval |
| GET | `/security-intelligence/situations/{situation_id}/decisions` | `intel:read` | The decision trail of a situation |
| GET | `/security-intelligence/decisions` | `intel:read` | Decisions across situations; `state=pending_approval` is the approver's queue |
| GET | `/security-intelligence/decisions/{decision_id}` | `intel:read` | One decision |
| POST | `/security-intelligence/decisions/{decision_id}/approve` | `intel:read` `intel:approve` | Approves a waiting decision; it is then carried out |
| POST | `/security-intelligence/decisions/{decision_id}/reject` | `intel:read` `intel:approve` | Rejects a waiting decision, with a note |
| GET | `/security-intelligence/decision-policy` | `intel:read` | Who may decide: the default, the organisation's policy, each site's own |
| PUT | `/security-intelligence/decision-policy` | `intel:read` `intel:manage` | Sets the organisation's policy. Audited |
| PUT | `/security-intelligence/decision-policy/sites/{site_id}` | `intel:read` `intel:manage` | Gives a site its own policy. Audited |
| DELETE | `/security-intelligence/decision-policy/sites/{site_id}` | `intel:read` `intel:manage` | Removes a site's own policy. Audited |

## Permissions

Seeded by `0132`. All are in use except `intel:feedback:export`, which is in
place for phase 14.

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

A guard's `intel:decide` does nothing by itself: the decision policy says at
which sites and up to which risk a guard may decide, and until an administrator
sets one it lets no guard decide — except to ask the command centre for help.
And `intel:decide` carries nothing out without the platform's own permission
for each step.

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
  `intel.camera_links.update`). So is every look at a suggestion, decision,
  verdict, step carried out and change of policy; the entries are listed in
  `AI_HUMAN_DECISION_MODEL.md`.
- A decision is not accepted from an API key or from a vendor's support
  session. The platform owner holds no `intel:*` permission.

## The web screens

`frontend/src/pages/intel/`, `frontend/src/components/intel/`, and the API
client `frontend/src/api/securityIntelligence.ts`. Four screens under a section
of their own in the sidebar, and one panel on the existing Command Centre.

| Screen | Path | Shows |
|---|---|---|
| Situations | `/situations` | The open situations, highest risk first: risk as the layer assessed it, and beside it where each stands with people. Filters by site, risk and standing |
| A situation | `/situations/:id` | The situation in one card (risk, location, started, sources, AI assessment, AI recommendation, human decision, incident); why the risk is what it is, factor by factor; the confidences; what was not known; what was known and from where; the related events and why each is there; the cameras worth opening; what the layer suggests; the buttons a person decides with; the decision history |
| Decisions | `/situation-decisions` | Every decision, most recent first: what the layer had suggested, what the person decided, the state, what was carried out. Approve and reject for those who may |
| Setup | `/intelligence-setup` | The switch; the runner's state; who may decide, with the three policies of the specification each one press; each site's hours and criticality |
| Command Centre panel | on `/command-centre` | A strip of the open situations by risk. A card opens the situation |

What the screens hold to, each with a test:

- **A suggestion never looks like a decision.** What the layer suggests is a
  dashed, violet-edged card marked "AI suggests" and "A suggestion, not a
  decision". What a person decided is a solid, green-edged card that names them.
  What the platform then did is listed under that decision, under its own
  heading. The list says "Risk (AI)" over one column and "Stands (people)" over
  another.
- **Four confidences, four lines.** Detection, correlation, risk and
  recommendation each have their own name, bar and number. One that no source
  gave says so; it is not shown as 0% or 100%. There is no overall figure.
- **A step that cannot be taken is shown, greyed, with the reason.** So is a
  decision the caller may not take: the button is disabled and says why, in the
  server's own sentence.
- **An override is possible and asks why.** Choosing a step that was not
  suggested opens a dialog that says what was suggested, and will not record
  without a reason; "other" not without a note.
- **The officer chooses.** The guard to send and the person to escalate to are
  picked from a list by the officer. The screen opens a camera when asked and
  says that the layer moves none.
- **One press, one decision.** The reference sent with a decision is made when
  the dialog opens, so a retry is the same decision.
- **A viewer sees the situation and its assessment**, not the suggestions and
  not the buttons; the requests for them are not even made.
- **Nothing for a tenant that has not switched it on.** The Command Centre
  panel renders nothing at all — not a heading, not a gap — when the layer is
  off or the user may not read it, so that page is as it was. The layer's own
  screens say plainly when it is off, or when its runner is not running.
- **Opening a situation records that the officer looked**, once per assessment.

## The phone

`mobile/src/screens/SituationsScreen.tsx`, `SituationDetailScreen.tsx`,
`mobile/src/api/securityIntelligence.ts`, `mobile/src/lib/situations.ts`; under
More → Security Situations, for anyone who holds `intel:read`.

The list is what is in front of this person: what the command centre sent them
first, marked, then the rest by risk. A situation shows what it is and where,
the evidence as the sources that reported, what the layer suggests — marked "AI
suggests — not a decision" — and why the risk is what it is, with the detection
and risk confidences each named.

Then what is theirs to do, in two kinds that are kept apart:

- **Reports** — *Accept*, then *Arrived* (with the phone's position if it
  gives one), and *Record what I see*. Each says on the screen that it decides
  nothing and changes no incident. *Navigate* opens the phone's maps;
  *View live* opens the situation's camera.
- **Decisions** — under "Your decision": *Accept: …* where what the layer put
  first is a phone's to take, then acknowledge, investigate, monitor, escalate,
  request assistance, resolve, false positive. One the person may not take is
  greyed and, when pressed, says why in the server's own sentence. One that
  needs approval says so before and after.

Setup, the decision history and the command centre's own steps — dispatching,
incidents, calling the site, a drone — are on the web.

## Live events

Published on the tenant's existing channel, `tenant_events:{tenant}`, and
forwarded to that tenant's clients by the existing listener with no change to it:
`intel_situation_opened`, `intel_situation_updated`, `intel_assessment_ready`
and `intel_recommendation_ready` from the runner; `intel_decision_recorded`,
`intel_decision_pending_approval`, `intel_decision_approved`,
`intel_decision_rejected` and `intel_observation_recorded` from the API. Each is saved before it is announced,
and an assessment that says what the last one said is not announced at all.

## Touch points in existing files

| File | Addition |
|---|---|
| `backend/app/main.py` | Registers the two routers |
| `backend/app/core/config_keys.py` | The `intel.enabled` and `intel.risk_weights` settings |
| `docker/docker-compose.yml` | The `intelligence-runner` service |
| `frontend/src/App.tsx` | Four routes |
| `frontend/src/components/layout/Sidebar.tsx` | One section, three entries |
| `frontend/src/hooks/usePermission.ts` | The seven `intel:*` permissions, per role as migration `0132` grants them |
| `frontend/src/pages/CommandCentre.tsx` | One import and one line: the panel, between the figures and the site grid |
| `mobile/src/navigation/index.tsx` | Three screens in the More stack |
| `mobile/src/screens/MoreMenuScreen.tsx` | One row, behind `intel:read` |

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

`backend/tests/test_intel_recommend.py` (82): see `AI_DECISION_WORKFLOW.md`.

`backend/tests/test_intel_decisions.py` (39): see `AI_HUMAN_DECISION_MODEL.md`.

`frontend/src/pages/intel/intel.test.tsx` (26): the screens' claims above.

`backend/tests/test_intel_field.py` (11): see `AI_HUMAN_DECISION_MODEL.md`.

`mobile/src/api/securityIntelligence.test.ts` (13) and
`mobile/__tests__/situationScreen.test.tsx` (12): the phone's calls on the wire, the rules of
its screens, and the screen mounted — a report is not a decision, a suggestion is marked as one,
and a decision a guard may not take says why.

`backend/tests/test_intel_clients.py` (11): every call the web and phone clients make is an
operation the API serves; their filters and the bodies of a decision and a report hold only what
the API accepts; the web's permission table gives each role exactly what migration `0132`
grants; the screens are registered and guarded on both; and the Command Centre page gained one
panel and nothing else.

`backend/tests/test_intel_docs.py` (24) checks the API tables of these documents
against the application's route table, and the rules written in the
correlation, risk, workflow and decision documents against the code — the
recommendation rules by running the engine for every kind at every level, and
what each decision carries out by running the planner.

## Not built yet

Drone and virtual patrol integration beyond reading
their events (10) · the unified timeline (11) · evidence and summaries (12) ·
the dashboard and site security score (13) · feedback (14) · platform health for
the vendor, the Helm deployment and final validation (15).

The screens show evidence as references so far — the events, the alerts behind
them, and the cameras to open. Snapshots, clips and recordings beside them come
with phase 12, and the single timeline with phase 11.
