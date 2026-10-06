# AI Security Intelligence & Human Decision Support — Gap Analysis

**As of:** 2026-10-05 · Phase 1 of 15 · written before any code.
**Since:** all fifteen phases are built (2026-10-06). Sections 1 to 18 are left
as they were written; what was built, and where it differs from this plan, is
in §19.
**Platform state inspected:** main at `1dc6858`, migration head `0131`, 230 tables,
91 API modules, 167 permissions, 8 roles.

The capability: every security source feeds one pipeline that normalises,
adds context, correlates, assesses risk and **recommends** — and an authorised
person decides. Nothing in this document, and nothing to be built from it, lets
the software take a consequential security action that a person has not
approved.

Figures marked *(Demo)* were counted on the development database's Demo tenant
on the date above. They are measurements of that database, not of any customer.

---

## 1. What this analysis found, in short

1. **The platform already raises the events; nobody joins them up.** Alerts are
   inserted from 21 different places (11 AI worker tasks, 5 API modules, 4
   services and the scheduler) with no shared function. The `alerts` table is
   the only place they meet, and each row stands alone.
2. **The flood is real.** *(Demo)* 8,628 of 9,213 alerts are open; one camera
   raised 196 alerts of one kind in a single hour; all 1,518 incidents were
   opened by the software, none by a person. An operator cannot work that list.
   This is the problem situations and duplicate suppression exist to solve.
3. **Correlation exists in name only.** `alerts.correlation_id` and
   `GET /alerts/{id}/correlated` are there, but the function that would fill
   the column, `assign_alert_correlation`, is not called from anywhere.
   *(Demo)* 0 of 9,213 alerts have one.
4. **The Drone Patrol module has already built this, for drones only.** A
   rules-based context and risk engine with written factors, a verification
   step, CCTV corroboration, incident response with nearest-free-guard, and
   "verify with drone" are all in production code with tests. The new layer
   should be the same idea made platform-wide — not a second, different engine.
5. **There is no language model anywhere in the platform**, and no external AI
   service. "AI" today is eleven computer-vision workers. This shapes how
   summaries and explanations can honestly be produced (§9, decision D3).
6. **Context data is thin in places.** Sites have a free-text `operating_hours`
   that is empty on all 4 sites *(Demo)*; there is no building or floor model;
   people are identified only by a face watchlist match or a number plate.
   The engine must say "not known" rather than guess (§6).
7. **It can be built additively.** Every stage can read existing tables and
   publish on the existing event channel without changing an alert producer, a
   worker, the notification path, or an existing table. The existing files that
   need a line added are listed in §12.

---

## 2. Existing functionality

### 2.1 Architecture

| Layer | What is there |
|---|---|
| Backend | FastAPI, SQL written by hand through SQLAlchemy `text()` on asyncpg; no ORM models. One router file per module (`backend/app/routers`, 91), logic in `backend/app/services` (71) |
| Database | PostgreSQL 16, 230 tables, 17 partitioned by month with pg_partman. `FORCE ROW LEVEL SECURITY` on tenant tables; the application role `svc_app` cannot bypass it |
| Tenancy | `get_db_with_tenant` sets `app.current_tenant` for the transaction. The setting ends at commit: any statement after a commit must set it again |
| Site scope | `dependencies/sites.py` narrows roles 3–7 to their assigned sites inside a tenant (`user_sites`) |
| Auth / RBAC | JWT; `require_permission("module:action")` checks `role_permissions`. Roles: 1 super_admin, 2 admin, 3 supervisor, 4 operator, 5 security_guard, 6 viewer, 7 client, 8 manager |
| Background | `scheduler` (12 timed jobs), `ingestion` (cameras, recording), 11 `ai-worker-*` (Redis Stream `frame_jobs`), `drone-runner`, `drone-edge` |
| Events | One Redis pub/sub channel per tenant, `tenant_events:{tenant_id}`, carrying `{event_type, payload, occurred_at}` |
| Live updates | `/ws/live`; `realtime/redis_listener.py` forwards **every** event type on the tenant's channel to that tenant's browsers and phones, unchanged |
| Clients | Web (React + MUI, 86 page files), desktop (the web build in Electron), phone (Expo SDK 57) |

### 2.2 Event sources

| Source | Where it lands | Raises an alert? |
|---|---|---|
| CCTV AI: intrusion, PPE, crowd, fire/smoke, weapon, behaviour, tampering, abandoned object, fall | `detections` + one `*_events` table each | Yes, by the worker, under the tenant's alert rules |
| LPR | `lpr_events` (plate, confidence, watchlist match) | Yes; also drives parking and the gate decision engine |
| Face recognition | `face_events` (watchlist match, 512-d embedding) | Yes |
| Drone Patrol | `drone_events`, `drone_observations`, `drone_event_cameras` | Yes, once verified |
| Virtual Patrol | `virtual_patrol_session_answers` (`is_exception`, reason, incident) | No — opens an incident on an exception |
| Access control | `access_events` (granted / denied, reason), `access_doors` | Yes when denied, forced or tampered |
| Alarm panels | `alarm_events`, `alarm_zones` (linked camera) | Yes |
| IoT sensors | `iot_alerts`, `iot_readings` | Yes |
| Guard: SOS, man-down, checkpoint scans, occurrence book | `man_down_events`, `checkpoint_scans`, `occurrence_book_entries` | SOS and man-down open incidents |
| Fleet GPS | `geofence_events` | Yes |
| System: camera offline, stream health | `camera_health_events` | No — a live `camera_status_changed` event only |

### 2.3 Alerts and incidents

- **Alerts** carry module, severity, camera, site, detection, status
  (open / acknowledged / resolved / dismissed), false-positive reason, assignee,
  escalation time and original severity. Operators can acknowledge, dismiss,
  mark false positive, assign, add notes, and bulk-create incidents.
- **Alert rules** (`alert_rules`, `shared/alert_rules.py`) set, per tenant and
  per trigger, the severity and whether an incident is opened automatically.
- **Duplicate rules** (`alert_dedup_rules`) suppress a repeat on the *same
  camera and module* within a window — but the check is called only when an
  alert is created by hand through the API. The AI workers, which raise nearly
  all alerts, each use their own cool-down setting instead.
- **Incidents** carry status, severity, SLA deadline and breach, assignee,
  dispatched guard with dispatch / arrival times, escalation, notes
  (`incident_notes`) and a status history with position
  (`incident_status_history`). `GET /incidents/{id}/timeline` merges the two.
- **Dispatch** (`routers/dispatch.py`) assigns a guard and computes the SLA
  deadline; **SLA** per severity is in `sla_configs`; **escalation** is a
  scheduler job writing `escalation_events`.
- **Routing** (`services/alert_routing.py`) decides who is pushed an alert:
  guards on an active shift at the camera's site plus users assigned to it.

### 2.4 Intelligence that already exists

| Capability | Where | Scope |
|---|---|---|
| Context + risk with written factors; confidence kept separate from risk | `services/drone_ai.py` (pure rules, no database) | Drone events |
| "One frame is not an incident": verification before anything is raised | `drone_ai.is_verified`, `drone_ai_pipeline.evaluate` | Drone events |
| Corroboration by a second sensor | `services/drone_cctv_correlation.py` → `drone_event_cameras` | Drone ↔ CCTV |
| Incident, nearest free guard, "verify with drone", resolution sync | `services/drone_response.py` | Drone events |
| Rule-based recommendations marked system-generated; an analytical risk map that says it is not a prediction | `services/drone_analytics.py` | Drone patrols |
| "What needs my attention now", by role | `routers/action_center.py` | Whole platform, but a count of items, not a judgement |
| One consolidated operations view | `routers/command_centre.py`, `pages/CommandCentre.tsx` | Sites, cameras, alert counts, guards, virtual patrols |
| Cross-module search | `routers/search.py` | Text search |
| Gate verdict from a plate read | `services/decision_engine.py` | Vehicles at barriers |

### 2.5 Evidence, audit, notifications, reporting

- **Evidence** (`evidence`, partitioned): snapshots and clips by detection or
  incident, with SHA-256; every view or download is logged in
  `evidence_access_log`. **Recordings** (`recordings`) with integrity
  verification by the scheduler.
- **Audit** (`services/audit.write_audit_log`): per-tenant hash chain; each row
  has user, action, resource, address and a JSON `detail`. Every request has an
  id (`request.state.request_id`, returned as `X-Request-Id`).
- **Notifications**: channels (email, SMS, webhook), rules by severity and
  module, delivery log; Expo push; outbound webhooks for five event types.
- **Reporting**: PDF/CSV reports, scheduled reports by email, exports.

### 2.6 The platform owner's side

The tenant `seventhaivision` is the vendor; `demo` is a customer.
`routers/platform_console.py` shows the vendor business figures and platform
health through `SECURITY DEFINER` functions that return **counts only** — there
is no route that can show a customer's incident. `services/platform_health.py`
probes each service and reports `unknown` when it cannot ask, never green.

### 2.7 Things the platform already does without asking anyone

The new principle is that software recommends and a person decides. These
existing behaviours act by themselves today. **None is changed by this work**;
they are listed so the owner knows exactly where the line already sits.

| Behaviour | Where | Why it exists |
|---|---|---|
| A barrier opens for a registered plate read with enough confidence | `decision_engine.py` (`AUTO_OPEN`) | Access policy set by the tenant; not a risk judgement |
| An incident is opened when an AI worker raises certain alerts | `alert_rules.create_incident`; *(Demo)* all 1,518 incidents | Tenant's alert rules |
| An incident is opened for a verified high-risk drone event | `drone_response.on_assessed` | Drone profile rules |
| A man-down that is not cancelled becomes an SOS | `services/mandown.py` | Guard safety; delay is the danger |
| Scheduled drone flights and virtual patrols start on time | their schedulers | Planned by a person in advance |

The new layer will treat software-opened incidents as **preliminary** and show
them as such (§7.6). If the owner wants any of the above brought under the
human-decision workflow, that is a separate change to existing behaviour and
needs its own decision.

---

## 3. What can be reused as it is

| Need | Reuse |
|---|---|
| Sites, cameras, streams, users, roles, tenants | Existing tables, read-only |
| The stream of noteworthy events | `alerts` — already normalised enough to start from: module, severity, camera, site, detection, time |
| Detail behind an event | The module's own `*_events` row via `detection_id`; `drone_events`; `access_events`; `alarm_events` |
| Restricted areas and their hours | `restricted_zones` (per camera, with schedule and bypass), `drone_security_zones` (geographic, with allowed people, roles and plates) |
| Holidays | `public_holidays` |
| Who is on duty, and where | `shifts`, `site_duty_assignments`, `drone_response.available_guards` |
| Planned activity | `work_permits` (contractors), `visitor_logs`, `tour_occurrences`, `virtual_patrol_sessions`, `drone_patrol_sessions` |
| Known people and vehicles | `face_watchlist_entries`, `watchlist_entries`, `access_credentials` |
| Camera position and what it sees | `cameras.latitude/longitude` (10 of 12 *(Demo)*), `drone_camera_coverage` |
| Opening an incident, dispatching, SLA, escalation | `incidents` and the existing dispatch function, as the drone module already calls them |
| Second look by drone | `drone_response.request_verification` |
| Live updates to web and phone | Publish on `tenant_events:{tenant}`; the listener forwards any event type |
| Audit | `write_audit_log` |
| Tenant settings with validation | `core/config_keys.py` + `tenant_settings` |
| Platform health for the vendor | The `platform_drone_health()` pattern: a function that returns counts |
| A background process of its own | The `drone-runner` pattern: one process, a heartbeat in Redis, per-tenant work under the tenant setting |
| Test patterns | Route-table sweeps, documents checked against routes, client calls checked against the API, runner tests as `svc_app` with two tenants |

---

## 4. What is missing

| # | Capability | Gap |
|---|---|---|
| 1 | Normalised event | No common record across sources; 21 producers, one shape each |
| 2 | Context | Only the drone engine builds one. No structured site hours, no site or area criticality, no single place that asks "who and what is expected here now" |
| 3 | Correlation | None in practice (§1.3). Nothing links a denied door, a camera and a drone sighting |
| 4 | Situation | No record that says "these seven alerts are one thing" |
| 5 | Duplicate suppression | Same camera + module only, and only for alerts created by hand |
| 6 | Normality / anomaly | Nothing compares now with what is usual for this place and hour |
| 7 | Risk | Drone events only. Alerts carry a severity fixed by rule, not a risk in context |
| 8 | Recommendation | None for live events (drone analytics recommends about patrol planning, not about an event) |
| 9 | Human decision record | An acknowledgement or a dispatch is recorded on the alert or incident, but not *what was recommended*, *whether it was followed*, or *why not* |
| 10 | Override reason | Only a free-text false-positive reason |
| 11 | Decision authority policy | Permissions say who *may* act; nothing says who *should decide* at which risk, per site |
| 12 | Explanation | Drone events show their factors; nothing else does |
| 13 | Separate confidences | Detection confidence only |
| 14 | Preliminary vs confirmed incident | `incidents.is_auto_created` exists; nothing records a person confirming one |
| 15 | Recommended cameras | Drone events only |
| 16 | Unified timeline | Per incident (status + notes). Not across source events, assessment, recommendation, decision and action |
| 17 | Assisted summary | None |
| 18 | Feedback dataset | None |
| 19 | Daily intelligence, site security score | Counts exist on dashboards; no explained score, no cross-source findings |
| 20 | Guard decision support on the phone | The phone shows alerts and incidents; no assessment, recommendation or decision |
| 21 | Platform-level health of the pipeline | Nothing to measure yet |

---

## 5. Design

### 5.1 Shape

A new background process, `intelligence-runner`, and a new API module. Both
only **read** existing tables, **write** new `security_*` tables, and **call**
existing functions when a person has decided something.

```
existing sources ──(alerts, drone_events, access_events, alarm_events,
                    man_down_events, virtual patrol exceptions)
        │  read-only, by cursor; woken early by the tenant's event channel
        ▼
  NORMALISE ─► security_events                    (original rows untouched)
        ▼
  CONTEXT ───► what is expected here, now          (pure function over facts)
        ▼
  CORRELATE ─► security_situations (+ links, each with a stated reason)
        ▼
  NORMALITY ─► how unusual, against this place's own history
        ▼
  RISK ──────► score, level, written factors
        ▼
  RECOMMEND ─► advisory actions with reason, confidence, evidence
        ▼
  ── published to the command centre as each stage completes ──
        ▼
  HUMAN DECISION  (API, by an authorised person only)
        ▼
  ACTION through the platform's existing incident / dispatch / drone functions
        ▼
  OUTCOME ─► FEEDBACK
```

Five properties hold throughout:

- **The runner never acts.** It has no code path to dispatch, escalate, open an
  incident, command a drone or touch a door. Those functions are reachable only
  from an API request made by a signed-in person with the permission, after the
  decision policy has been checked. This is enforced by where the code lives,
  and held by a test that reads the runner's imports.
- **Every stage is a pure function over recorded facts**, like `drone_ai`:
  the same facts give the same answer, and every point of risk and every link
  between events has a written reason. No reason, no claim.
- **Unknown is a value.** Where the platform has no data — no hours for a site,
  no identity for a person — the engine records "not known" and that is shown.
  It never fills the gap.
- **If the runner is down, nothing else notices.** Alerts, pushes, incidents,
  live wall and recording run exactly as today. Operators simply see no
  assessments until it is back, and the platform console says so.
- **Drone events keep their own assessment.** `drone_ai` already scored them;
  the platform layer carries that score and its factors in as evidence and adds
  only what it alone can see: other sources.

### 5.2 Normalised event

One row in `security_events` per source record, found by cursor on each source
and keyed by `(tenant, source_table, source_id)` so a re-run inserts nothing.
The row holds references and a small normalised core — source type, event type,
time, site, camera, drone, subject (person / vehicle / none, with the plate or
watchlist entry where there is one), confidence, severity, position, attributes
— and never copies media: snapshots and clips stay in `evidence` and
`recordings` and are reached by reference.

Source types: `CCTV_AI`, `DRONE_PATROL`, `VIRTUAL_PATROL`, `LPR`,
`FACE_RECOGNITION`, `ACCESS_CONTROL`, `ALARM`, `GUARD`, `SENSOR`, `SYSTEM`,
`OTHER`.

Why from `alerts` rather than raw `detections`: the workers and the tenant's
alert rules have already decided what is noteworthy, and `detections` is two
orders of magnitude larger. Events that raise no alert but matter as context
(a door *granted*, a visitor checked in, a checkpoint scanned) are read by the
context engine when it needs them; they are not events to assess.

### 5.3 Context

A function of (event, site facts at that moment) returning a context with a
source for every entry:

| Group | From | When missing |
|---|---|---|
| Place | site, camera, the camera's restricted zones in force, drone zones containing the position, site/area criticality from the new site profile | criticality "not set" |
| Time | tenant timezone; business hours from the new site profile; `public_holidays` | "hours not defined" — after-hours is then *not* claimed |
| People | watchlist verdict on a face; guards on shift; visitors on site; contractors with a permit in force | "unidentified" — never "unauthorised" unless a rule says who is allowed |
| Access | `access_events` at doors of the site or camera in the window; alarm zone state | none recorded |
| Operations | shifts, tours due, virtual patrol or drone patrol in progress, work permit in force | none recorded |
| History | earlier events at this camera/zone; false-positive share for this camera and module; earlier incidents | "insufficient history" below a floor of data |

The context is stored with the assessment it informed, so the explanation shown
later is what the engine knew *then*, not what is true now.

### 5.4 Correlation and situations

An event joins an open situation only through a named method:

| Method | Rule | Data today |
|---|---|---|
| `SAME_SOURCE_REPEAT` | same camera, same kind, within the window | all sources |
| `SAME_IDENTITY` | same plate, or same watchlist entry | LPR, face |
| `ADJACENT_CAMERA` | cameras on one site within a set distance, or linked in the site profile, within a walking-time window | 10 of 12 cameras have coordinates *(Demo)* |
| `ACCESS_AT_CAMERA` | an access event at a door tied to the camera or the site, shortly before or after | 1 door, 0 events *(Demo)* — built and tested with fixtures |
| `ALARM_ZONE_CAMERA` | an alarm in a zone linked to the camera | `alarm_zones.linked_camera_id` |
| `DRONE_CCTV` | the drone module's own corroboration | `drone_event_cameras` |
| `PATROL_FINDING` | a virtual-patrol exception at the same camera or site earlier in the window | `virtual_patrol_session_answers` |

Each link stores method, reason in words, and a confidence. Events on different
sites never join. An event that matches nothing opens a situation of one.

**What it will not claim.** The platform cannot re-identify an unknown person
between cameras. "Same person" is asserted only by plate or watchlist match;
otherwise the link says what it is — *nearby, moments later* — and its
confidence is lower. Face embeddings are stored and could support matching
unknown faces later; that is a privacy decision for the owner, not a default.

### 5.5 Duplicate suppression

Operators work **situations**. A situation shows its sources ("Camera 12,
Camera 13, Drone, Camera 14") and one card, however many alerts fed it. The
alerts themselves are untouched and remain in the existing Alerts page, in
audit and in search. Whether the existing *push notification per alert* should
also be held back for alerts folded into an open situation is decision D2: it
would change existing behaviour.

### 5.6 Normality and anomaly

A count of events of this kind, at this camera and site, in this hour of the
week, over the previous weeks, compared with now — plus the context (in hours
or out, zone in force, anyone expected). Output: `normality_score` and
`anomaly_score`, 0–100, with the numbers behind them. With too little history
it returns "insufficient history" and contributes nothing to risk. The words
used are *unusual*, *suspicious*, *requires review*; never a statement about a
person's intent.

### 5.7 Risk

Points per written factor, the way `drone_ai.assess` does it: severity of the
event, zone in force, out of hours, identity verdict, access denied nearby,
corroboration by a second kind of sensor, recurrence, unusualness, and
reductions for an expected activity (permit, patrol, guard present) or a high
false-positive share. Weights are per tenant with shipped defaults. Output:
`risk_score` 0–100, `risk_level` (INFO, LOW, MEDIUM, HIGH, CRITICAL — the
platform's existing five), and the factor list. An assessment is a new row each
time, never an edit: the history of what was believed is itself evidence.

### 5.8 Recommendation

Rules over (risk level, kind of situation, what is available now): `MONITOR`,
`VERIFY`, `VIEW_CAMERA`, `VERIFY_WITH_DRONE`, `DISPATCH_GUARD`, `ESCALATE`,
`INVESTIGATE`, `CONTACT_SITE`, `CREATE_INCIDENT`. Each carries reason,
priority, confidence and its supporting evidence. A recommendation is offered
only if it is possible — no "verify with drone" on a site with no drone in the
air, no "dispatch" with nobody on shift (it says so instead).

**Four confidences, never one.** Detection (from the model, unaltered),
correlation (how firmly the events belong together), risk (how complete the
context was), recommendation (how settled the rule is given the first three).

### 5.9 Human decision

The decision is its own record: who, in what role, what they chose, whether it
followed the recommendation, and — if not — a reason from a fixed list
(authorised activity, already handled, false detection, guard already
responding, maintenance activity, emergency situation, camera issue, other +
text). An override is never blocked; a missing reason is.

The **action** is a third record: what the platform then did, through which
existing function, and the result. Recommendation, decision and action are
three tables precisely so that none can be mistaken for another.

**Decision authority** is configuration, per tenant with per-site override:
which roles may decide up to which risk level, and above which level a guard's
decision needs command-centre approval. It narrows permissions; it never
widens them.

### 5.10 Incidents

An incident opened by a person's decision is a normal `incidents` row created
through the existing function. Incidents the software already opens
(§2.7) are shown as **preliminary** until a person confirms or dismisses them
in the situation; that confirmation is recorded in the new tables, not by
altering `incidents`.

### 5.11 Summary and explanation

Built from templates over recorded facts — time, place, sources, factors,
recommendation, decision, action, outcome — and labelled "AI-assisted
summary". Every sentence maps to a stored row, so it cannot state what was not
recorded. See decision D3 for the alternative.

### 5.12 Site security score and daily intelligence

Counted on request from recorded rows, like `drone_analytics`: a score that
starts at 100 and loses stated points for stated things (unresolved incidents,
cameras offline, a repeated location, SLA misses, patrols missed), with weights
the tenant can change. Findings and recommendations are fixed rules over the
same counts and say what they rest on.

---

## 6. Honest limits

- **Unknown people stay unknown across cameras** (§5.4).
- **No building or floor model.** Place is site → camera → the zones on it.
  The site profile adds an optional area label and criticality per camera; it
  does not invent a hierarchy the data lacks.
- **Site hours do not exist in usable form.** Until an administrator sets them
  in the new site profile, "after hours" is not asserted for that site.
- **Access control has no data here** *(Demo: 1 door, 0 events)*. That path is
  built against fixtures and remains unproven against real hardware.
- **Anomaly needs history.** A new site gets "insufficient history".
- **No model is trained or changed.** Feedback is a dataset for people to
  review; nothing learns from it automatically.
- **Risk is a rule-based judgement, not a prediction**, and its weights are
  opinions made explicit so they can be argued with.

---

## 7. Required database changes

All new tables; no existing table gains or loses a column. Every table has
`tenant_id`, `FORCE ROW LEVEL SECURITY` with the standard tenant policy,
grants to `svc_app`, timestamps, and foreign keys to existing tables with
`ON DELETE` chosen so that deleting a camera or user never deletes a decision.

| Phase | Table | Holds |
|---|---|---|
| 2 | `security_events` | One normalised row per source record; unique on `(tenant_id, source_table, source_id)` |
| 2 | `security_ingest_cursors` | Per tenant and source, how far the normaliser has read |
| 3 | `security_site_profiles` | Per site: business hours by weekday, criticality, per-camera area label and criticality, camera links |
| 4 | `security_situations` | One per situation: number, kind, status, site, started / last event, sources, current assessment, linked incident |
| 4 | `security_situation_events` | Event ↔ situation, with method, reason, confidence |
| 5 | `security_assessments` | Append-only: normality, anomaly, risk score / level / factors, the four confidences, the context used, engine version |
| 6 | `security_recommendations` | Per assessment: action, reason, priority, confidence, evidence, status |
| 7 | `security_decisions` | Who decided what, role, followed or override, reason code and note, request id |
| 7 | `security_actions` | What was executed for a decision, through what, and the result |
| 7 | `security_decision_policies` | Per tenant / site: roles and the risk level each may decide; approval threshold |
| 13 | `security_site_scores` | Daily snapshot of the explained score (the live score is always computed) |
| 14 | `security_feedback` | Recommendation → decision → outcome, with an outcome code and export state |

Also seeded by migration: new permissions and their role grants (§10), and a
`platform_security_intelligence_health()` function returning counts only.

Not partitioned at first: volume follows `alerts`, which is not partitioned
either. Retention follows `evidence.retention_days` unless the owner wants a
separate period; decisions and actions are kept as long as audit logs.

---

## 8. Required APIs

Under `/api/v1/security-intelligence`, in the existing style (one router file,
`require_permission`, site scope, pagination helper, audit on every change):

| Area | Operations |
|---|---|
| Situations | list (filter by status, risk, site, source, time), get one in full, its events, its timeline, its evidence, its recommended cameras |
| Assessments | history for a situation; the explanation ("why this alert", "why this risk", "why this recommendation") |
| Recommendations | current for a situation |
| Decisions | record a decision (accept / choose another action / override with reason); list decisions; request and give command-centre approval where policy requires it |
| Actions | results for a decision; each action calls the existing function (acknowledge, incident, dispatch, escalate, drone verification) |
| Guard | situations assigned to me; my decision and observations |
| Policy | read / set decision authority; read / set site profile; risk weights |
| Intelligence | dashboard, site security score with its factors, daily findings |
| Feedback | record outcome; export the dataset |
| Platform (vendor) | pipeline health, counts only, on the existing platform console |

Reads work with the feature off; nothing is written until a tenant turns it on.

---

## 9. Decisions for the owner

All four were put to the owner on 2026-10-05 and decided the same day.

| # | Decision | Decided | Why it was the owner's |
|---|---|---|---|
| D1 | May existing files be touched in the minimal ways listed in §12 (router registration, menu entries, one health probe, one compose service, one panel on the Command Centre page)? | **Yes, additions only** | The standing rule is that existing code is not changed without the owner's say |
| D2 | Should alerts folded into an open situation stop sending their own push notification? | **No — pushes stay as they are.** Situations are a new view; to be looked at again once the layer has been seen working | It would change what guards' phones do today |
| D3 | How are summaries and explanations written? | **From templates over recorded facts.** No language model | A language model would send security data to a third party and can state things that were not recorded |
| D4 | Who gets it? | **A switch per tenant, off by default**; a tenant administrator turns it on | The alternatives were on for everyone, or a paid licence like Drone Patrol |

Smaller choices made here unless the owner says otherwise: table prefix
`security_`; permission prefix `intel:`; its own background process;
face-embedding matching of unknown people **not** built; the five existing
self-acting behaviours in §2.7 left exactly as they are.

---

## 10. Security implications

- **New permissions** (seeded, none granted beyond the table below):

  | Permission | For | Roles by default |
  |---|---|---|
  | `intel:read` | See situations, assessments, evidence links | admin, manager, supervisor, operator, viewer; guard for own site |
  | `intel:recommendation:read` | See recommendations and their reasons | admin, manager, supervisor, operator; guard where policy allows |
  | `intel:decide` | Record a decision within the decision policy | admin, manager, supervisor, operator; guard where policy allows |
  | `intel:override` | Decide against a recommendation | admin, manager, supervisor, operator |
  | `intel:approve` | Give command-centre approval to a guard's high-risk decision | admin, manager, supervisor |
  | `intel:manage` | Decision policy, site profiles, risk weights | admin, manager |
  | `intel:feedback:export` | Export the feedback dataset | admin, manager |

  A decision still needs the permission for the action it triggers
  (`incident:dispatch`, `incident:create`, `drone:operate`, …). `intel:decide`
  alone executes nothing.
- **The client role (7) gets nothing.** Assessments describe a site's
  weaknesses.
- **Super admin (1) gets no tenant security data**: counts through one
  `SECURITY DEFINER` function, on the existing console, with `platform:read`.
- **Every consequential step is audited** with actor, role, site, action,
  source, request id and result in the existing hash-chained log: assessment
  and recommendation generated (system actor), recommendation viewed, accepted,
  rejected, override reason, guard assigned, escalation, incident opened or
  resolved, drone verification approved, evidence opened or exported.

  *As built (phases 5–7):* everything a person does is in that log.
  **Assessments and recommendations being generated are not**: the record of
  each is its own row, which the application's database role can add but not
  alter, and each decision's log entry names the assessment and suggestion it
  was made on. Writing them to the log would have meant the runner writing a
  table that is not the layer's own, and the rule that it writes only
  `security_*` tables was kept instead.
- **Evidence access** from a situation goes through the existing evidence
  endpoints and their chain-of-custody log; the new layer stores references.
- **No free text from an event is executed or interpolated**; SQL uses bind
  parameters and module constants, as the drone module's static pass checks.
- **Privacy.** Assessments about identified people (watchlist matches, plates)
  are personal data under PDPA: they inherit the tenant's retention, appear in
  data-subject exports, and "unauthorised" is never asserted without a rule
  that names who is allowed.

## 11. Multi-tenant implications

- Every new table is tenant-scoped under RLS; the runner works one tenant at a
  time under that tenant's setting and re-sets it after every commit.
- Correlation never crosses a tenant, and never crosses a site.
- The runner's tests run as `svc_app`, not as a superuser, with two tenants,
  and first assert the connection is not a superuser — the project's tests
  otherwise bypass RLS and would pass a leak.
- Live updates are published only on the owning tenant's channel.
- Site scope applies to every read: a supervisor assigned to two sites sees
  situations for those two.
- The vendor console receives counts, never rows.

---

## 12. Required changes to existing files

Additions only. Nothing listed here changes what an existing feature does.

| File | Addition | Phase |
|---|---|---|
| `backend/app/main.py` | Register the new routers | 2 |
| `backend/app/core/config_keys.py` | New `intel.*` setting keys | 2 |
| `docker/docker-compose.yml` | The `intelligence-runner` service (not the core file, which has no drone runner either) | 2 |
| `backend/app/services/platform_health.py` | One probe | 15 |
| `frontend/src/App.tsx`, `components/layout/Sidebar.tsx`, `hooks/usePermission.ts` | Routes, menu entries, permission names | 8 |
| `frontend/src/pages/CommandCentre.tsx` | One panel: open AI situations, linking to the new pages | 8 |
| `mobile/src/navigation/index.tsx`, `screens/MoreMenuScreen.tsx` | The guard's situation screens | 9 |
| `helm/` | A deployment for the runner — **cannot be rendered here** (no Helm) | 15 |

**Not touched:** any AI worker; any of the 21 alert producers; `alerts`,
`incidents` or any other existing table; the alerts, incidents, dispatch and
notification code; `redis_listener.py`; the Drone Patrol and Virtual Patrol
services; any existing RLS policy; the existing Alerts and Incidents pages.

## 13. Required frontend changes

New pages under Monitoring, in the existing visual language:

- **AI Security Alerts** — open situations, highest risk first, one card each
  with its sources.
- **Situation** — assessment, "why this alert / risk / recommendation", risk
  factors, the four confidences, evidence, related events, recommended cameras
  (open in the existing live view), timeline, decision history.
- **Security Intelligence** — daily findings and the site security score with
  its factors.
- **Decision policy and site profiles** — under Configuration.

**The one rule of the interface:** the AI recommendation and the human decision
are two differently styled panels that never share a box, a colour or a verb.
The AI panel says *recommends*; the human panel says *decided* and names the
person. While nobody has decided it reads "Human decision: pending".

Phone: a guard's assigned situation with reason, recommendation, evidence and
the guard's actions and observations (Phase 9). Desktop: the web build, in the
next release.

## 14. Required event integrations

Published on the existing `tenant_events:{tenant}` channel, forwarded to
clients by the existing listener with no change to it:

`intel_situation_opened`, `intel_situation_updated`, `intel_assessment_ready`,
`intel_recommendation_ready`, `intel_decision_recorded`,
`intel_action_completed`, `intel_situation_resolved`.

The runner *listens* on the same channel only to wake early when an alert is
created; the database cursor is the source of truth, so a missed message costs
seconds, not an event. Outbound webhooks and notification rules for the new
event types are not added by default (they would alter existing configuration
screens); they can follow once the layer is in use.

## 15. Required background workers

One: `python -m app.intelligence_main` — heartbeat in Redis, a tick every few
seconds, each stage incremental and idempotent so a crash resumes where it
stopped. Per tick and per tenant with the feature on: ingest new source rows →
correlate → assess situations that changed → recommend → publish. It holds no
lock on any existing table and writes none.

On the 7.7 GB development machine it is left stopped, like the drone runner,
and exercised in tests and by running the process against the test database.

## 16. Performance and observability

- The alert reaches the operator through today's path, unchanged and first.
  Context, risk and recommendation arrive as separate live updates.
- Measured per stage: delay from alert to situation, to assessment, to
  recommendation; backlog; failures; events folded as duplicates;
  recommendation accepted / overridden rates; false-positive rate.
- Exposed to the vendor on the existing console as counts and a status that is
  `unknown` when it cannot be measured.

---

## 17. Implementation phases

| # | Phase | Delivers |
|---|---|---|
| 1 | Inspection and gap analysis | This document |
| 2 | Normalised event pipeline | `security_events`, cursors, the runner skeleton with heartbeat, tenant switch, normalisers for each source, tests |
| 3 | Context engine | Site profiles, the context function and its sources, "not known" handling |
| 4 | Correlation and situations | Situations, links with reasons, duplicate folding |
| 5 | Risk | Normality / anomaly, risk with factors, the four confidences, append-only assessments |
| 6 | Recommendations | Rules, feasibility checks, reasons and evidence |
| 7 | Human decision workflow | Decisions, overrides, actions through existing functions, decision policy, preliminary / confirmed incidents, audit |
| 8 | Command Centre UI | The pages in §13, the panel, live updates |
| 9 | Guard / phone | Assigned situations, guard decision within policy, observations |
| 10 | Drone and Virtual Patrol | Verify-with-drone from a decision and reassessment after it; patrol findings as evidence and correlation |
| 11 | Unified timeline | Source events, assessment, recommendation, decision, action, outcome in one sequence |
| 12 | Evidence | Linked snapshots, clips and recordings through the existing custody log; summary |
| 13 | Dashboard and site score | Daily intelligence, explained score |
| 14 | Feedback and analytics | Outcome capture, dataset export, acceptance / override analytics |
| 15 | Validation | Full tests, performance, security audit, platform health, the six documents |

After every phase: backend tests, web and phone tests, migrations up and down,
RLS and RBAC checks, web build, existing functionality verified, regressions
fixed before the next.

## 18. Testing requirements

- **Normalisation:** one test per source (CCTV, drone, virtual patrol, access,
  alarm, guard, sensor); re-running inserts nothing; a source row that
  disappears does not break the event.
- **Correlation:** the same situation across cameras; drone + CCTV; access +
  CCTV; virtual patrol + drone; duplicates folded; different sites never
  joined; unrelated events never merged; every link has a reason.
- **Risk:** a normal event, a suspicious one, a high-risk one; missing context
  lowers confidence rather than raising risk; the same facts give the same
  score.
- **Recommendations:** the right one per case; low and high confidence;
  nothing infeasible is offered.
- **Decisions:** accept, reject, override, override without a reason refused,
  a user without the permission refused, a guard above their policy level
  refused, approval flow.
- **The runner cannot act:** a test over its imports and call graph.
- **Tenancy:** RLS on every new table as `svc_app` with two tenants;
  cross-tenant and cross-site reads refused on every route (route-table sweep).
- **Audit:** every consequential action writes its entry, with the fields in
  §10.
- **Failure:** runner stopped; Redis unavailable; a missing camera or drone; a
  stale event; a duplicate event; a failed notification; an action whose
  underlying function refuses.
- **Nothing existing changes:** the full existing suites — 3,220 backend,
  1,129 repository-inspection, 132 web, 174 phone — stay green at every phase.
- **Documents:** the API document checked against the route table, as for
  Drone Patrol.

---

## 19. As built

**Written 2026-10-06, after phase 15.** Sections 1 to 18 are the analysis and
the plan as they stood before any code, left as written so that the plan can be
held against the result. What exists is described in
`AI_SECURITY_INTELLIGENCE_ARCHITECTURE.md` and the four documents it names.

### 19.1 The fifteen phases

| # | Phase | Migration | What it added |
|---|---|---|---|
| 1 | Inspection and gap analysis | — | This document, and the owner's four decisions (§9) |
| 2 | Normalised event pipeline | `0132` | `security_events`, the cursors, the runner with its heartbeat, the per-tenant switch, a reader for every source |
| 3 | Context engine | `0133` | Site and camera profiles; what was expected at a place and time, with "not known" kept as not known |
| 4 | Correlation | `0134` | Situations, every link with a method, a reason and a confidence; duplicates folded |
| 5 | Normality and risk | `0135` | Assessments that are added to and never changed: the risk and every factor behind it, the confidences, what was known |
| 6 | Recommendations | `0136` | What the layer suggests, why, how sure, and whether it can be done now |
| 7 | Human decisions | `0137` | Decisions, overrides with reasons, approvals, the policy of who may decide, and the one module that carries a decision out — through the platform's existing functions |
| 8 | Command Centre screens | — | Situations, a situation, decisions, setup; one panel on the Command Centre page |
| 9 | Guard and phone | `0138` | A guard's situations, a decision within policy, reports from the ground |
| 10 | Drone and virtual patrol | `0139` | A decision can ask a flight to hold and look, or start a planned mission; what was seen comes back as an event; patrol findings as evidence and context |
| 11 | Unified timeline | — | One situation in the order it happened, each line drawn as what it is |
| 12 | Evidence and summary | — | A situation's evidence opened through the existing custody log; a summary in fixed words over recorded facts |
| 13 | Site score and daily intelligence | — | A score whose every point is a stated line; what a period looked like, with advisory findings |
| 14 | Feedback | `0140` | A person's review of a closed situation; the dataset of suggestion, decision and outcome; how the suggestions fared |
| 15 | Validation | `0141` | The vendor's row on the console; how long each stage takes; the runner on a cluster; a sweep of the whole layer; these documents |

### 19.2 Where the result differs from the plan

- **§7, tables.** `security_site_scores` was not built: the score is counted
  when it is asked for, from records that already exist, so there is no
  snapshot to fall out of date. `security_feedback` holds a person's review and
  no "export state": that an export was taken is in the audit log. Five tables
  the plan had folded into others stand on their own — `security_camera_profiles`,
  `security_camera_links`, `security_reviews`, `security_decision_approvals`,
  `security_observations`. Sixteen tables in all, where the plan listed twelve.
- **§8, a path.** What a drone saw for a situation is read at
  `/situations/{id}/aerial`. No path of the layer contains the word "drone",
  because the drone module's own tests take every such path for the drone
  module's.
- **§10, audit.** Assessments and suggestions being generated are their own
  unchangeable rows, not entries in the hash-chained log (stated in place, §10).
- **§12, existing files.** Every addition listed was made, and nothing listed
  as not touched was touched. Beyond the list: one block in the chart's
  `values.yaml`; and, between phases 7 and 8, two existing drone **tests** —
  not the code they test — were corrected because they failed only after
  midnight in Singapore.
- **§13, screens.** "AI Security Alerts" is *Situations*; "Security
  Intelligence" is *Insight*; *Decisions*, *Feedback* and *Setup* are screens of
  their own. The desktop application wraps the web build; it was rebuilt with
  these screens as 1.0.4 on 2026-10-06 (unsigned, same app id and upgrade
  code).
- **§14, events.** Nine are published rather than seven:
  `intel_situation_opened`, `intel_situation_updated`,
  `intel_assessment_ready`, `intel_recommendation_ready`,
  `intel_decision_recorded`, `intel_decision_pending_approval`,
  `intel_decision_approved`, `intel_decision_rejected`,
  `intel_observation_recorded`. `intel_action_completed` and
  `intel_situation_resolved` are not separate events: what was carried out, and
  that a matter closed, arrive with the decision that did it.
- **§16, measurement.** The delay of each stage is read at
  `/security-intelligence/pipeline`; backlog and failures are the vendor's row;
  accepted, overridden and false-positive rates are the feedback analytics.
  Measured once under volume: the architecture document, *How long it takes*.

### 19.3 What the final check found

- **The application's database role could empty eight of the layer's tables.**
  They had been granted ALL, which includes `TRUNCATE`, and a `TRUNCATE` is not
  subject to row security. No code used it. `0141` takes it away, and a test
  now asks the database for every table of the layer rather than naming them.
- **Thirty-nine tables outside the layer carried the same grant** — the Drone
  Patrol and Virtual Patrol modules' tables, `report_deliveries`,
  `report_schedules` and `tenant_pwm_floors`. They are existing schema, so the
  layer's own phases did not change them and the finding was put to the owner,
  who asked for it to be done: migration `0142` takes `TRUNCATE` away from every
  table and partition that carried it, and `test_app_role_privileges.py` asks
  the catalogue that none carries it again.
- **Rendering the chart with Helm for the first time showed two things in its
  older templates**, reported and not changed. With `minio.enabled=true` the
  chart creates no recordings claim and the API, ingestion and scheduler pods
  mount no recordings volume: continuous recordings would be written to the
  ingestion pod's own disk, where the API cannot play them, the scheduler's
  integrity sweep cannot find them, and a restart loses them (the eleven AI
  worker deployments also render empty `volumes:` and `volumeMounts:` keys in
  that configuration, which is harmless). And the chart has no deployment for
  the drone runner, so the Drone Patrol module cannot fly or report on a
  cluster deployed from it.
- **Two earlier tests proved nothing.** A check that a module "only reads"
  removed the module's SQL along with its docstrings before looking for writes
  (found and fixed in phase 12).
- **A list in the architecture document had fallen behind the code** (fixed in
  phase 14, and now held to the code by a test).

### 19.4 Left open

- An evidence bundle for a situation; the timeline and evidence on the phone;
  sending a drone to a place no mission was planned for; a drone flown without
  a person's decision; webhooks and notification rules for the layer's events.
  Each is deliberate and is described under *Limits* in the architecture
  document.
- The chart has been rendered by Helm (2026-10-06, and on every push since, in
  CI) and not yet installed on a cluster.
- The phone application with these screens has not been on a device: it needs
  a new build first.
- Access control and alarm panels have no data on the development database;
  the rules that use them rest on fixtures.

### 19.5 The suites at the end

Backend 3,629 passed; repository inspection 1,176; web 181; phone 200
— against 3,220, 1,129, 132 and 174 before the first line of this layer (§18).
Every suite that passed then passes now.
