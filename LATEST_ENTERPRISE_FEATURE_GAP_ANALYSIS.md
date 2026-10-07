# Latest Enterprise Feature Expansion — Gap Analysis

**As of:** 2026-10-06 · Phase 0 of 14 · written before any code of this expansion.
**Platform state inspected:** `main` at `50eedcf`, migration head `0142` (142
migrations), 246 tables, 93 API modules serving 867 operations, 90 backend
services, 91 web routes, 52 phone screens, 174 permissions in 8 roles. Suites:
backend 3,640, repository inspection 1,188, web 186, phone 233 — all passing.

This document answers one question for each feature in the expansion brief:
**what is already here, and what is not.** Every statement below was checked
against the code or the development database on the date above. Nothing in it
is built yet.

Status words, as the brief defines them:

| | |
|---|---|
| **COMPLETE** | Database, API, screens, permissions, audit and tests all exist and work |
| **PARTIAL** | Real and in use, but part of what the brief asks is absent |
| **MISSING** | Nothing of it exists |
| **NEEDS INTEGRATION** | The parts exist separately and are not joined |
| **NEEDS REFACTOR** | Exists, but in a form the new work cannot build on without change |

---

## 1. What this audit found, in short

1. **Most of the brief's nouns already exist.** There is an occurrence book,
   shift handover, visitors and contractors, work permits, post orders, an
   equipment register, a defect log, dispatch, SLA settings, a site map, a
   heatmap, analytics, scheduled reports, privacy zones, data-subject requests,
   chain of custody and a hash-chained audit log. The expansion is mostly
   **joining and finishing**, not building from nothing. Rebuilding any of them
   would break the brief's own rule.
2. **The intelligence layer already is the "one flow".** `security_events` →
   context → correlation → risk → recommendation → human decision → action is
   built, tested and running (`AI_SECURITY_INTELLIGENCE_ARCHITECTURE.md`). The
   brief's §45 asks that nothing create a second decision engine; nothing here
   does. Visitors and access are the two sources it does not yet read fully.
3. **Three things look built and are not.**
   - `incidents.sla_breached` exists and **nothing ever sets it**. Nor is
     `sla_configs.ack_within_seconds` or `resolve_within_seconds` ever
     evaluated: only the dispatch deadline is stamped at dispatch. Every SLA
     figure the platform shows today is therefore zero breaches.
   - `escalation_events` exists and **nothing writes to it**. Unacknowledged
     alerts have their severity raised by the scheduler; no person is escalated
     to, and incidents are not escalated at all.
   - Guard positions: **there is no live position of a guard.** Positions exist
     only on records a guard made — a check-in, a checkpoint scan, a status
     change, an occurrence entry. "Nearest guard" today means nearest by those.
4. **There is no investigation.** `GET /search` matches a word against the
   titles of eight kinds of record. Nothing searches across sources by time,
   place, person or vehicle; nothing holds an investigation; an incident cannot
   become a case.
5. **Evidence is logged, not protected.** Access is written to a custody log
   and files carry a checksum, but evidence cannot be locked against the
   retention purge, there is no package, no export of a set, and the custody
   log knows four actions (`view`, `download`, `export`, `custody_transfer`)
   with no reason recorded.
6. **There is no building or floor.** Place is site → camera → the zones drawn
   on that camera's picture. Sites and cameras have coordinates; nothing else
   does except drone zones and patrol checkpoints.
7. **There is no language model anywhere in the platform**, and no dependency
   on one. The brief's natural-language features say "where the existing AI
   infrastructure supports it". It does not. See decision **E2**.
8. **Device health is one signal.** Cameras report `stream_disconnected`,
   `stream_degraded`, `stream_reconnected` (3,591 events on the development
   database), and a tampering detector reports covered, moved and blurred
   lenses. Frame rate, latency, packet loss and recording gaps are **not
   measured by anything**, and there is no register of security assets — the
   equipment register is for kit issued to guards.
9. **Reports exist for three things**: a site summary, the occurrence book and
   one incident. Scheduling and email delivery work. Everything else in the
   brief's reporting list is absent.
10. **The phone already has a screen for most guard work**: occurrence book,
    handover, post orders, visitors, contractors, defects, dispatch, incidents,
    situations, patrol. What it lacks is the response loop on an *incident*
    (accept, on my way, arrived, resolve) outside the intelligence layer.

---

## 2. The rules this work is built under

- **A person decides.** AI detects, relates, assesses and recommends. Nothing
  in this expansion takes a consequential action that a person did not decide.
  Recommendations are advisory, labelled as AI-assisted, and separate from the
  decision record. Predictions are stated as history, never as certainty.
- **Nothing that works is changed without being said.** The owner's standing
  instruction (2026-09-24) is that existing functionality is not altered. This
  expansion *extends* existing modules, which that rule does not automatically
  allow — see decision **E1**.
- **The platform owner is not a customer's command centre.** The
  `seventhaivision` tenant owns the platform; `demo` is a customer. Nothing
  here puts a tenant's incidents, evidence or investigations on the Super
  Admin console. Super Admin and the client role receive none of the new
  permissions unless a row below says otherwise.
- **Tenancy.** Every new table carries `tenant_id` under `FORCE ROW LEVEL
  SECURITY` with the standard policy; records that belong to a site carry
  `site_id` and honour the caller's site scope; records that must not be
  rewritten are granted `SELECT, INSERT` only. A search — typed or built —
  runs as the caller, under the same policies as every other reading.
- **Reuse.** One search, one timeline, one custody log, one notification
  system, one map. Where a table for a thing exists it is used; a new table is
  added only where the thing has no home.

---

## 3. Status by feature

Abbreviations: *R* router (`backend/app/routers/`), *S* service
(`backend/app/services/`), *W* web page, *P* phone screen, *M* migration.

### Priority 1 — Smart investigation and evidence intelligence

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| Search across security data | **PARTIAL** | R `search` (1 op): `ILIKE` over alerts, incidents, cameras, sites, both watchlists, zones, users. `security_events` already holds CCTV, LPR, face, drone, virtual patrol, alarm, sensor, guard and system events in one shape | Search by time, site, camera, drone, person, plate, face, event type, severity, risk, guard, visitor, contractor. Access events, visitor logs, occurrence entries and audit records are not searchable at all |
| Natural-language investigation | **MISSING** | — | Everything. No language model exists (E2) |
| Unified investigation timeline | **PARTIAL** | S `intel_timeline` builds one situation's timeline from events, assessments, suggestions, decisions, observations and actions; R `incidents` has a per-incident timeline of notes and status changes | A timeline over an arbitrary set of records (an investigation's), with access events, visitor movements and guard actions in it |
| Cross-camera following | **PARTIAL** | LPR reads carry the plate; face events carry the watchlist match; S `intel_correlation` links the same plate or watchlist entry across cameras, and neighbouring cameras for unidentified people | A reading of "every place this plate / this watchlist entry was seen", in order, with snapshot and confidence. **Unidentified people cannot be followed** and will not be claimed to be |
| Automatic evidence collection | **PARTIAL** | S `intel_evidence.collect` gathers a situation's snapshots, recordings covering its events, drone media and patrol snapshots, by reference | The same for an incident or an investigation; LPR, face, access and visitor records as evidence items |
| Evidence package | **MISSING** | R `reports` renders one incident as a PDF | A package: manifest of items with checksums, timeline, assessments, decisions; lock; export; watermark; download permission |
| Chain of custody | **PARTIAL** | `evidence_access_log` (M `0009`), R `dispatch` custody endpoints, 4 actions; the intelligence layer writes to it when evidence is opened; audit log is hash-chained and verifiable | Captured, locked, shared, released as recorded steps; a reason; the role; custody of a *package*; a lock the retention purge respects |

### Priority 2 — GIS security command map

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| Operational map | **PARTIAL** | W `MapView` (Leaflet): every site with live camera health and manning. Drone screens draw zones, routes and live flights. W `GPS`: vehicles and geofences | One map with cameras, guards, drones, incidents, alerts, situations, access points, alarms, sensors and patrol routes as layers with states |
| Site security map configuration | **PARTIAL** | `sites` has coordinates, a geofence radius and polygon; `cameras` have coordinates; `patrol_checkpoints` have coordinates; `drone_security_zones` are geographic polygons | Buildings, floors, gates, geographic security zones for people on foot, access points and emergency locations as places on a map (E4) |
| Live incident map | **MISSING** | Incidents take their place from their camera | Incidents and situations plotted; selecting one shows nearby cameras, guards, drone, access points, related events, evidence and response state |

### Priority 3 — Guard dispatch and response management

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| Dispatch | **PARTIAL** | R `dispatch`: send a named guard, mark arrived. A decision in the intelligence layer can dispatch (S `intel_actions` → the same function). W `Incidents` dialog; P `Dispatch` (repaired 2026-10-06) | Guard acknowledgement and refusal, "on my way", investigation and resolution as recorded steps of a response; reassignment |
| Intelligent guard recommendation | **PARTIAL** | S `drone_response.available_guards` ranks guards on shift at the event's site, free before busy, nearest first — for drone events only | The same for any incident; skill (certifications), workload and SLA in the ranking; the reasons shown. Advisory |
| Response SLA | **PARTIAL** | `sla_configs` per severity (ack / dispatch / resolve seconds, an escalation user); deadline stamped at dispatch | **The evaluator.** Nothing measures acknowledgement, response or resolution against the settings, and `sla_breached` is never set |
| Escalation | **PARTIAL** | Scheduler raises the severity of unacknowledged alerts and of pending man-down events; `escalation_events` table | Configurable chains (who, after how long, by severity, site and type); escalation of incidents; a record of each step; notification through the existing rules |
| Guard mobile response | **PARTIAL** | P `Situations` / `SituationDetail`: accept, arrived, report, decide within policy. P `Incidents`, `IncidentDetail`: notes, status, SOS | The response loop on an incident itself; photo and video with a report; the relevant SOP; navigation hand-off |

### Priority 4 — Digital occurrence book and shift handover

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| Occurrence book | **PARTIAL** | `occurrence_book_entries` (M `0008`), R `dob` (3 ops), 13 entry types, W in `GuardOps`, P `OccurrenceBook`, PDF report | Deliveries, unusual activity, instructions and supervisor notes as types; supervisor review; correction by a further entry (entries are not edited); search |
| Shift handover | **COMPLETE** | `shift_handovers`, templates, checklists (M `0099`), R `handover` (12 ops), S `handover` gathers open incidents, alerts, patrols, keys, lost property, defects and kit; accept, dispute, resolve; W `Handovers`, P `Handover` | Pending instructions carried from one handover to the next |
| AI-assisted shift summary | **MISSING** | The handover already counts the shift's facts | A written summary from those facts, marked AI-assisted, reviewed by the outgoing guard before it is handed over |

### Priority 5 — Security SOP and knowledge assistant

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| SOP library | **PARTIAL** | `post_orders` (M `0056`): per-site text with a category, a version number and acknowledgement tracking; R `post_orders` (7 ops); W `PostOrders`; P `PostOrders` | Uploaded documents, version history, effective and expiry dates, approval, incident-type tagging |
| SOP assistant | **MISSING** | — | Asking a question and being shown the approved passage with its source. No language model (E2) |
| Incident SOP assistance | **MISSING** | — | The approved SOP for an incident's type and site, shown on the incident and the situation |

### Priority 6 — Visitor and contractor security

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| Visitors | **PARTIAL** | `visitors`, `visitor_logs`, custom form fields (M `0010`, `0077`); R `visitors` (12 ops) and `vms` (7): pre-registration, QR pass by email, QR and manual check-in, check-out, overstay alerts, on-site board; W `VisitorPreReg`, `VmsOnsite`; P `Visitors` | Host approval, zones a visitor is authorised for, escort, pass expiry as a state, ID verification |
| Contractors | **PARTIAL** | `contractors`, accreditations, `work_permits`, `deliveries` (M `0025`); R `contractors` (18 ops): vetting, permits with approval and safety briefing, deliveries; expiry checks; W `Contractors`; P `Contractors` | Zone authorisation; check-in against a permit |
| Visitor security integration | **NEEDS INTEGRATION** | The intelligence layer's context already reads work permits and visitor logs as *planned activity* | Visitor and contractor movements as events; "authorised for zone A, seen in zone C" as context for a person to review — never an accusation |

### Priority 7 — Device health, assets and maintenance

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| Camera health | **PARTIAL** | `camera_health_events` (disconnected / degraded / reconnected); offline alerts from the scheduler; S `recording_integrity` verifies recorded files; tampering detector (covered, moved, blurred) | Frame rate, latency, packet loss, recording gaps, repeated-disconnect detection, darkness and glare — none is measured today |
| Other device health | **PARTIAL** | NVR probe status; IoT sensor status and expected interval; drone health and battery; edge gateway heartbeat; alarm panel state; platform health console | One reading of every device's health per site |
| Security asset register | **MISSING** | `equipment_items` is guard kit (radios, torches), 12 columns | Cameras, NVRs, servers, gateways, controllers, drones, sensors, UPS and network equipment as assets: make, model, serial, location, warranty, vendor, installed, status, service history |
| Maintenance | **PARTIAL** | `facility_defects` with refer and resolve (M `0098`); `drone_maintenance_logs` | Work orders, preventive schedules, technicians and vendors, parts, downtime, maintenance SLA; a work order *suggested* by a health problem and approved by a person |

### Priority 8 — Predictive risk and AI security advisor

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| Recurring-pattern analysis | **PARTIAL** | S `intel_insight`: where and in which hours situations begin, repeated vehicles and watchlist entries, false-alarm share — over 1 to 90 days, advisory. S `drone_analytics`: a weighted risk map per zone | The same over incidents, access violations, patrol failures and device failures; time-window statements with the history under them |
| Risk heatmap | **PARTIAL** | W `Heatmap` (alert activity by camera and hour); site security score per site | Risk by zone and by time; incident and event density; response risk; a historical view |
| AI security advisor | **PARTIAL** | Insight findings each say what they rest on and what to consider | Confidence on each; an acknowledgement by a person (accepted / not accepted, with a reason); findings from response times, device health and drone patrols |

### Priority 9 — Security operations analytics

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| Command centre figures | **PARTIAL** | R `command_centre` overview; R `analytics` (8 ops): trends, by module, by severity, top cameras, resolution time; W `Dashboard`, `Analytics`, `CommandCentre` | Response time, patrol compliance, guard status and device health on one board |
| Site manager / tenant / security company views | **PARTIAL** | W `ClientPortal` for the client role; `Compliance` dashboard; drone and intelligence analytics | A site view and a cross-site view of incidents, SLA, guards, patrols, visitors, equipment and risk; a per-customer view for a security company (`sites.client_id` exists) |
| AI daily security briefing | **PARTIAL** | The insight reading produces a period's findings on request | A dated briefing a person reviews and publishes, with its history |

### Priority 10 — Guard and workforce intelligence

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| Performance analysis | **PARTIAL** | Attendance, lateness, patrol integrity, violations, training records, certification compliance, handover acceptance — each on its own screen | One reading per guard and per site across them; response times. **Never an employment decision** |
| Training recommendations | **MISSING** | Training courses, attempts, certifications and requirements exist | A recommendation from repeated misses, for a manager to assign or not |
| Coverage optimisation | **PARTIAL** | Roster with an auto-scheduler, minimum guards per shift, coverage reading | A recommendation from incident volume, night activity and response times. Never a change to a roster |

### Priority 11 — Security case management

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| Cases | **MISSING** | Incidents have a status history, notes and an assignee | Cases: lifecycle, investigators, tasks, notes, linked incidents, evidence, people and vehicles, approvals, a report |

### Priority 12 — Compliance, privacy and hardening

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| Retention | **PARTIAL** | Evidence retention days and purge; recording policies per site; drone footage and track retention; audit-partition archiving | One place that states every retention period in force; a hold that stops a purge (shared with evidence lock) |
| Privacy | **PARTIAL** | Privacy zones per camera (masking); consents; data-subject requests with erasure; subject export | Access logging of *searches* about a person; a report of what is held about a subject across the new records |
| Hardening | **PARTIAL** | MFA, SSO, SCIM, sessions, IP allow-list, password policy, rate limits, API keys, hash-chained audit, RLS forced, application role without `TRUNCATE` | A sweep, as for the intelligence layer, over every table and operation this expansion adds |

### Cross-cutting

| Feature | Status | What exists | What is missing |
|---|---|---|---|
| Reports | **PARTIAL** | R `reports` (site summary, occurrence book, one incident, PDF); R `scheduled_reports` with email and webhook delivery; R `exports` (CSV); virtual patrol and drone reports (PDF, XLSX) | Reports for investigations, evidence, guard performance, device health, maintenance, visitors, access, SLA and risk; scheduled delivery of them |
| Notifications | **PARTIAL** | Channels (email, SMS, webhook), rules by severity, module, alert code, site and event; logs; webhooks; phone push; emergency broadcast | Rules by role and by escalation step — added to the existing rules, not beside them |
| Phone | **PARTIAL** | 52 screens (§1.10) | The incident response loop, SOP on an incident, photo with a report |
| Windows | **COMPLETE** (as a shell) | Desktop 1.0.4 wraps the web build | A new build once new screens exist |
| Edge and offline | **PARTIAL** | Drone site gateway with queued sync and receipts; phone outbox for scans and entries | Nothing new is promised offline by this expansion except what the phone's outbox already carries |
| Audit | **COMPLETE** | Hash-chained `audit_logs`, verifiable; actor, role, tenant, site, action, result, request id | New actions are written to it; nothing new is needed of it |

---

## 4. Decisions for the owner

**Decided by the owner on 2026-10-06: the recommended answer to each of the
four.** Additions only (E1); without a language model (E2); a guard's last
recorded position, shown with its age (E3); an optional model of buildings,
floors and places (E4). The table is kept as the record of what was asked.

| # | Decision | Recommended | Why it is the owner's |
|---|---|---|---|
| **E1** | May this expansion extend existing modules by **additions only** — new tables beside them, new operations on new routers, new menu entries and screens, and writing to existing columns that were made for the purpose and are never written (`incidents.sla_breached`, `escalated_at`)? Each existing file touched is listed in its phase's commit | **Yes, additions only** | The standing rule is that what works is not changed. Almost every item above extends something that works |
| **E2** | Natural-language investigation, the SOP assistant and AI-written summaries: build them **without a language model** — a typed phrase read by fixed rules into the same structured search anyone can build by hand, SOP answers that *are* the approved passage found by text search, summaries from templates over recorded facts — or add a language-model provider | **Without a model** | There is none in the platform. Adding one sends a customer's security questions to an outside service and adds a cost per question. It can be added later behind the same structured search without changing anything built now |
| **E3** | Guard position for recommendation and the map: the **last position a guard recorded** (check-in, scan, status change), shown with its age — or periodic location reporting from the phone while on shift | **Last recorded position** | Continuous tracking of staff is a privacy decision, drains a phone, and needs a new phone build that has not yet been on a device |
| **E4** | Add an optional **building / floor / place** model per site (buildings, floors, gates, access points, emergency points, zones drawn on the map), filled in by an administrator — or stay with site → camera → zone | **Add it, optional** | It is new data somebody must enter. Without it the map can show sites, cameras, checkpoints and drone zones only |

---

## 5. What will be added

**Database.** New tables only, each under the tenant policy. Counted per
phase; none duplicates a table that exists.

| Phase | Tables | Reuses |
|---|---|---|
| 1 | `investigations`, `investigation_items` | `security_events`, alerts, incidents, LPR and face events, access events, visitor logs, occurrence entries |
| 2 | `evidence_packages`, `evidence_package_items`, `evidence_holds` | `evidence`, `recordings`, `drone_event_media`, `evidence_access_log` (custody stays one log) |
| 3 | `site_places` (buildings, floors, gates, access points, emergency points, map zones — one table, typed) | `sites`, `cameras`, `patrol_checkpoints`, `drone_security_zones` |
| 4 | `incident_responses` (the steps of one response), `escalation_policies` | `incidents`, `sla_configs`, `escalation_events`, `shifts` |
| 5 | `shift_handover_summaries` | `occurrence_book_entries`, `shift_handovers` |
| 6 | `sop_documents`, `sop_versions`, `sop_incident_types` | `post_orders` stays as it is |
| 7 | `visitor_authorizations` | `visitors`, `contractors`, `work_permits`, `restricted_zones` |
| 8 | `security_assets`, `maintenance_work_orders`, `maintenance_schedules`, `device_health_readings` | `cameras`, `drones`, `nvr_connections`, `iot_sensors`, `camera_health_events`, `facility_defects` |
| 9 | `security_advice` (a finding and what a person said of it) | `intel_insight` counts |
| 10 | `security_briefings` | everything counted |
| 11 | none | attendance, patrols, training |
| 12 | `security_cases`, `security_case_links`, `security_case_tasks`, `security_case_notes` | `incidents`, `investigations`, `evidence_packages` |
| 13 | none | — |

**API.** Under `/api/v1`, in the existing style: `/investigations`,
`/evidence-packages`, `/site-map`, `/incident-responses`,
`/escalation-policies`, `/sop`, `/visitor-authorizations`, `/security-assets`,
`/maintenance`, `/security-advice`, `/security-briefings`, `/security-cases`.
The existing `/search`, `/dob`, `/handovers`, `/visitors`, `/contractors`,
`/dispatch`, `/custody` and `/reports` keep their operations.

**Permissions.** New codes per group (`investigation:*`, `evidence:package:*`,
`evidence:hold`, `sitemap:manage`, `response:*`, `escalation:manage`, `sop:*`,
`asset:*`, `maintenance:*`, `advice:*`, `briefing:*`, `case:*`), granted to the
roles that work a site. Not to Super Admin; to the client role only where a
row says so.

**Workers.** No new process. SLA evaluation and escalation run in the existing
scheduler, which already runs the jobs of that kind; visitor and access events
are read by the existing intelligence runner as two more sources.

---

## 6. Phases

Each phase ends with migrations up and down, backend / web / phone tests, type
checks, lint, a web build, RLS and RBAC checks, the existing suites green, its
documents written, and a merge to `main` once CI is green.

| # | Phase | Delivers | Document |
|---|---|---|---|
| 0 | Audit | This document | — |
| 1 | Smart investigation | Structured search over every source by time, place, person, plate, type and risk; a phrase read into the same search by fixed rules (E2); investigations that hold what was found; the "where was this seen" reading; an investigation timeline | `SMART_INVESTIGATION_ARCHITECTURE.md` |
| 2 | Evidence and custody | Evidence collected for an incident or investigation by reference; packages with a manifest and checksums; lock / hold honoured by the purge; export with watermark; custody steps with a reason | `EVIDENCE_CHAIN_OF_CUSTODY.md` |
| 3 | GIS | One operational map with layers and states; site places (E4); incidents and situations on the map with what is nearby | `GIS_SECURITY_ARCHITECTURE.md` |
| 4 | Dispatch, SLA, escalation | The response as recorded steps; guard recommendation with its reasons; the SLA evaluator that finally sets `sla_breached`; escalation policies and their record; the phone's response loop | `GUARD_RESPONSE_ARCHITECTURE.md` |
| 5 | Occurrence book and handover | The missing entry types, supervisor review, search; the AI-assisted shift summary, reviewed before handover | `DIGITAL_OCCURRENCE_BOOK.md` |
| 6 | SOP | A versioned, approved SOP library; retrieval that quotes its source; the SOP for an incident's type on the incident and the situation | `SECURITY_SOP_ARCHITECTURE.md` |
| 7 | Visitors and contractors | Host approval, zone authorisation, escort, expiry; visitor and access events into the intelligence layer | `VISITOR_CONTRACTOR_SECURITY.md` |
| 8 | Device health, assets, maintenance | A health reading per device from what is measured, and nothing claimed that is not; the asset register; work orders and schedules, suggested and approved | `DEVICE_HEALTH_ARCHITECTURE.md` |
| 9 | Risk and advisor | Recurrence over all sources; risk by zone and hour; advice with confidence and a person's answer | `SECURITY_RISK_ARCHITECTURE.md` |
| 10 | Analytics | The boards per role; the daily briefing, reviewed and published; the missing reports | `SECURITY_ANALYTICS_ARCHITECTURE.md` |
| 11 | Workforce intelligence | A reading per guard and site; training and coverage recommendations for a manager | (in the analytics document) |
| 12 | Case management | Cases from incidents and investigations | `SECURITY_CASE_MANAGEMENT.md` |
| 13 | Compliance and hardening | The retention statement, holds, subject reports, the sweep | `ENTERPRISE_SECURITY_HARDENING.md` |
| 14 | Integration testing | The brief's eleven end-to-end chains, as tests | — |

---

## 7. Honest limits

- **A typed question is not understood; it is parsed.** Without a language
  model (E2) the investigation box reads a fixed vocabulary — places, times,
  kinds of event, plates, names on a watchlist — and shows the search it made
  of the words, so that a person can correct it. A sentence outside that
  vocabulary gets "not understood", not a guess.
- **An unidentified person cannot be followed across cameras.** A plate can; a
  watchlist entry can. For anyone else the platform has "a person at this
  camera, then at a neighbouring one moments later", and says no more.
- **Device health will be thinner than the brief's list.** Frame rate, latency
  and packet loss are not measured and this expansion adds no measuring agent
  to cameras. Health is read from disconnections, degraded streams, recording
  verification, tampering detections, probe results and heartbeats.
- **Predictive risk is counting.** "Elevated between 01:00 and 03:30" means
  more was recorded there then, over a stated period, than elsewhere. It is
  shown with the count under it and is never a forecast.
- **Nothing here has hardware under it.** Access control and alarm panels have
  no data on the development database; drones are simulated; the phone has not
  been on a device since its SDK upgrade.
- **The scale is large.** Fourteen phases, each the size of one phase of the
  intelligence layer. They are built and merged one at a time; a later phase
  does not start on an earlier one's regressions.

---

## 8. Testing

Per phase: unit tests of the pure rules; API tests through the application;
reading and writing as the application's database role with two tenants; who
may and may not, by role and by site; the audit entry for each sensitive
action; failure of what it depends on. In phase 14, the eleven chains of the
brief end to end — CCTV → AI → incident, drone → AI → incident, virtual patrol
→ AI → incident, incident → dispatch, incident → investigation, investigation →
evidence, evidence → case, case → report, visitor → access → CCTV → alert,
device health → maintenance, risk → recommendation → human decision — and the
whole-layer sweep over every new table and operation, taken from the catalogue
and the route table rather than from a list.

---

## 9. As built

Sections 1 to 8 are the audit as it stood before any code and are kept as
written. This section is added to as each phase is finished.

| # | Phase | State | Where it is described |
|---|---|---|---|
| 0 | Audit | **Done 2026-10-06** | This document |
| 1 | Smart investigation | **Built 2026-10-06** — migration `0143` | `SMART_INVESTIGATION_ARCHITECTURE.md` |
| 2 | Evidence and custody | **Built 2026-10-07** — migration `0144` | `EVIDENCE_CHAIN_OF_CUSTODY.md` |
| 3 | GIS | **Built 2026-10-07** — migration `0145` | `GIS_SECURITY_ARCHITECTURE.md` |
| 4 | Dispatch, SLA, escalation | **Built 2026-10-07** — migration `0146` | `GUARD_RESPONSE_ARCHITECTURE.md` |
| 5 | Occurrence book and handover | **Built 2026-10-07** — migration `0147` | `DIGITAL_OCCURRENCE_BOOK.md` |
| 6 | SOP | **Built 2026-10-07** — migration `0148` | `SECURITY_SOP_ARCHITECTURE.md` |
| 7 | Visitors and contractors | Not started | |
| 8 | Device health, assets, maintenance | Not started | |
| 9 | Risk and advisor | Not started | |
| 10 | Analytics | Not started | |
| 11 | Workforce intelligence | Not started | |
| 12 | Case management | Not started | |
| 13 | Compliance and hardening | Not started | |
| 14 | Integration testing | Not started | |

### Phase 1 — what was built, against what section 3 found

| Section 3 said | Now |
|---|---|
| Search across security data: **PARTIAL** — a word against eight kinds of title | One search across fourteen sources by period, site, camera, kind, event type, severity, risk, plate, name, member of staff and words. Each source read under its own existing permission and the asker's sites; each source left out is named with the reason |
| Natural-language investigation: **MISSING** | A typed phrase read by fixed rules into that same search (decision E2), reporting which words became which filter and which were not used. No language model. A phrase with nothing understood is refused |
| Unified investigation timeline: **PARTIAL** | An investigation: why it was opened, and references to records of any of the fourteen kinds with notes, in the order they happened, each read live as the reader may see it |
| Cross-camera following: **PARTIAL** | A trail for one number plate or one face-watchlist entry: every sighting, with the time and distance between. Still nobody else: an unidentified person is not followed |

**Not done in phase 1, and where it goes:** collecting evidence for an
investigation, packages, export and custody are phase 2; a report of an
investigation, and cases, phase 12; searching by a picture of a face is not
planned. The phone app is unchanged: guards hold neither new permission.

**Existing files changed, by additions only:** `backend/app/main.py`,
`frontend/src/App.tsx`, `frontend/src/components/layout/Sidebar.tsx`,
`frontend/src/hooks/usePermission.ts`. The existing `GET /api/v1/search` is as
it was.

### Phase 2 — what was built, against what section 3 found

| Section 3 said | Now |
|---|---|
| Automatic evidence collection: **PARTIAL** — for a situation only | What the platform kept that belongs to the records of an investigation or an incident is found — the frame of that detection, the recording of that camera at that moment, the drone media of that sighting — and offered. A person chooses what goes in |
| Evidence package: **MISSING** | A package: a draft put together from what belongs, then sealed. Sealing writes a manifest of every item with its checksum, and the SHA-256 of the manifest; a database trigger then refuses every change. Exported as a ZIP of the manifest as sealed, each original byte for byte with its checksum computed again and compared, and a marked viewing copy of each picture |
| Chain of custody: **PARTIAL** — four actions, no reason, no package, no lock | One chain per package, from capture to release: captured, collected, accessed (the existing access log, unaltered), sealed, placed under a hold, exported, downloaded, shared, released, hold lifted — each with who, in what role, and why |
| Retention: no hold that stops a purge | A hold stops all three retention jobs. Sealing places one on every item; one is lifted by a person with a reason |

**The one change of behaviour to existing code in phase 2:** the three
retention jobs — `backend/app/scheduler_main.py` (frames and clips),
`backend/app/services/continuous_recording.py` (recordings) and
`backend/app/services/drone_retention.py` (drone media) — each gained one
predicate and now leave alone anything under a hold in force. With no hold,
each deletes exactly what it deleted before; the existing tests of all three
pass unchanged. The other existing files changed are by additions only:
`backend/app/main.py`, `frontend/src/App.tsx`,
`frontend/src/components/layout/Sidebar.tsx`,
`frontend/src/hooks/usePermission.ts`. The existing evidence, recording and
drone endpoints and the existing `evidence_access_log` are as they were.

### Phase 3 — what was built, against what section 3 found

| Section 3 said | Now |
|---|---|
| Operational map: **PARTIAL** — sites with counts; separate maps in the drone and vehicle screens | One map in ten layers: sites, cameras, guards, open incidents, live alerts, open situations, drones, patrol checkpoints, places and drone zones — each under its own screen's existing permission and the reader's sites. What has no position is counted under the map, not hidden |
| Site security map configuration: **PARTIAL** — no buildings, floors, gates, access points or emergency points | `site_places` (decision E4): a point, an outline, or a level of a building, drawn by an administrator. Optional; retired, never removed. An access point can name a door the platform knows, which gives door events a place to appear |
| Live incident map: **MISSING** | Incidents, alerts and situations are on the map; selecting one lists the cameras, drones, checkpoints and places within a radius and every guard on shift at the site, nearest first |
| Guard positions: none live | Still none live (decision E3). `guard_positions` reads the last position each guard recorded this shift and always gives its age; over an hour old is marked stale |

**Existing files changed in phase 3, by additions only:** `backend/app/main.py`,
`frontend/src/App.tsx`, `frontend/src/components/layout/Sidebar.tsx`,
`frontend/src/hooks/usePermission.ts`. The existing Site Map (`/map`) is as it
was.

**Not done in phase 3, and why:** alarm panels and sensors are not drawn —
they have no position of their own; there are no floor plans; distances are
straight lines, not routes; and the map dispatches nobody (that is phase 4,
and remains a person's act there too).

**Not done in phase 2, and why:** video is not watermarked; nothing is
digitally signed; the platform sends evidence to nobody (sharing and release
are records of what a person did); a hold placed by hand on one item has an
API and no screen; a virtual patrol's snapshot cannot be packaged; and the
object-store path of an export has not been run against a real store on the
development machine.

### Phase 4 — what was built, against what section 3 found

| Section 3 said | Now |
|---|---|
| Dispatch: **PARTIAL** — send a named guard, mark arrived | A response: the record of one sending and the steps the guard takes on it — accepted, set off, arrived, reported, or cannot attend with a reason — each written through to the incident's own status, history and arrival time. The guard is told on their phone. A guard who is not coming, or is stood down, gives the incident back; sending somebody else closes the first response. The dispatch itself is the existing endpoint, unchanged |
| Intelligent guard recommendation: **PARTIAL** — for drone events only | For any incident: the guards on shift at its site, ranked by a score made of stated parts — free or already sent, distance by last recorded position and how old that position is, what they were already sent on this shift, and the certifications the site requires. A suggestion with its reasons; a person chooses and dispatches |
| Response SLA: **PARTIAL** — settings and a deadline, no evaluator | **The evaluator.** Three clocks per incident — acknowledge, arrive, resolve — judged every minute against the existing settings, once an organisation switches them on and only for incidents opened since. A clock that runs out sets `sla_breached`, is recorded once, and is told to the person the settings name. The settings have a screen for the first time |
| Escalation: **PARTIAL** — alert severity raised; an unwritten table | Escalation policies: when an incident is still not acknowledged, reached or resolved after so long, tell a role or a person — by site and severity. Each step recorded once, with how many people it reached; told on the organisation's screens, the phones of those addressed, and through the existing notification rules. `escalation_events` is written for the first time |

**Two things the audit had wrong, found while building.** The incident status
workflow the audit described (`dispatched`, `en_route`, `on_scene`, …) exists,
but a guard could never use it: the status endpoint needs `incident:update`,
which guards do not hold. And the existing dispatch moves an incident to
`in_progress`, a status that workflow does not contain, so the phone's *Mark
as…* button offered nothing on a dispatched incident. Neither is changed; the
response steps are the guard's way, and they treat `in_progress` as what it
means — sent, not yet set off.

**Existing files changed in phase 4, by additions only:** `backend/app/main.py`,
`backend/app/core/config_keys.py`, `backend/app/scheduler_main.py`,
`frontend/src/App.tsx`, `frontend/src/components/layout/Sidebar.tsx`,
`frontend/src/hooks/usePermission.ts`,
`mobile/src/screens/DashboardScreen.tsx`,
`mobile/src/screens/IncidentDetailScreen.tsx`. The existing dispatch, incident
and SLA endpoints and the Incidents screen are as they were. The scheduler
gained one job, which with the clocks off — the default — writes only the
record of a sending and tells the guard who was sent.

**Existing columns and a table written for the first time, only once an
organisation switches the clocks on:** `incidents.sla_breached`,
`incidents.escalated_at`, `incidents.escalated_to_user_id`, and
`escalation_events`. The incident report and the site security score already
read `sla_breached` and will then show breaches where they showed none.

**Not done in phase 4, and why:** nothing dispatches, reassigns or re-dispatches
by itself; a guard is not tracked on the way and no arrival time is estimated;
alerts are not escalated to people (their severity is raised, as before);
response times are not turned into a judgement of a guard (phase 11, and
advisory there); and the phone's part has been type-checked and its rules
tested, not run on a device.

### Phase 5 — what was built, against what section 3 found

| Section 3 said | Now |
|---|---|
| Occurrence book: **PARTIAL** — written and listed by date; no search, review or correction | Searched by words, kind, site, author, shift, period, review and correction. Reviewed by a supervisor — noted, to be followed up, followed up — each review kept. Corrected by a further entry that records which entry it corrects and why; the first stays as written. Two more kinds of entry: a delivery, and unusual activity |
| Shift handover: **COMPLETE** — but pending instructions were not carried forward | Instructions in force at a site: issued with or without a date they run out on, read, closed with a reason. The guards on shift are told, and each one is written into the summary of every shift at the site until it ends |
| AI-assisted shift summary: **MISSING** | A summary of a shift drafted from what was recorded — the book, incidents, alerts, patrols, dispatches, visitors, what the site has in hand, instructions, follow-ups — in fixed sentences that count and quote (decision E2: no language model, and the database allows no other method). A person reads it, corrects it and confirms it; what the platform drafted is kept beside it; once confirmed it cannot be changed |

**What section 3 asked for and was built differently, and why.** It listed
"instructions and supervisor notes as types" of entry. They are not entry
types: the existing list endpoint shows every entry to the client role, and a
supervisor's note about a guard is not for a client. A supervisor's note is a
review of an entry, and an instruction is a thing of its own; both are served
only to the people who keep the book or read handovers.

**Found while building, and left for the owner (phase 13).** The occurrence
book is described as append-only and no code edits or removes an entry, but
the application's database role is permitted to update and delete rows of
`occurrence_book_entries`. Taking that away would change an existing table's
grants. Also: the existing endpoint that writes an entry takes no position, so
an occurrence book entry gives a guard's whereabouts only when it was written
by an SOS. The security map lists it as a source of a guard's position; in
practice it rarely is one.

**Existing files changed in phase 5, by additions only:** `backend/app/main.py`,
`backend/app/routers/dob.py`, `frontend/src/App.tsx`,
`frontend/src/components/layout/Sidebar.tsx`,
`frontend/src/hooks/usePermission.ts`, `frontend/src/pages/GuardOps.tsx`,
`mobile/src/api/dob.ts`, `mobile/src/screens/OccurrenceBookScreen.tsx`,
`mobile/src/screens/DashboardScreen.tsx`. The existing endpoint that writes
and lists entries accepts two more kinds and is otherwise as it was; the
handover and how it is made are unchanged.

**Not done in phase 5, and why:** the summary is not copied into the handover
— it is read beside it; nothing reminds anybody of an unread instruction or an
open follow-up; an entry still has no position; the summary writes no prose
and draws no conclusion; and the phone's part has been type-checked and its
rules tested, not run on a device.

### Phase 6 — what was built, against what section 3 found

| Section 3 said | Now |
|---|---|
| SOP library: **PARTIAL** — post orders: a text per site, a version number, acknowledgements | A library of procedures beside post orders. Each has a code, a category, a site or every site, and versions that are kept: drafted, submitted, approved by somebody other than their author to be in force from a date, or rejected with a reason. A version may run out. An approved version cannot be changed — a trigger holds it. The document as issued can be attached with its checksum. A procedure names the kinds of incident it is for |
| SOP assistant: **MISSING** | Asking the library returns the passages of procedures in force that use the words asked — word for word, most of the words first, each with its procedure, version and who approved it. Nothing composes an answer and no language model is involved (decision E2); when no passage uses those words it says so |
| Incident SOP assistance: **MISSING** | The procedures in force for an incident of that kind at its site are put beside it, word for word: on the response desk, on the phone's incident screen, and — for the kinds of event a situation is made of — on the situation screen |

**Existing files changed in phase 6, by additions only:** `backend/app/main.py`,
`frontend/src/App.tsx`, `frontend/src/components/layout/Sidebar.tsx`,
`frontend/src/hooks/usePermission.ts`, `frontend/src/pages/intel/Situation.tsx`,
`mobile/src/screens/IncidentDetailScreen.tsx`. Post orders, their endpoints,
their screen and their acknowledgements are as they were. The document as
issued is stored on the existing documents volume, in a folder of its own.

**Not done in phase 6, and why:** it finds passages by their words and does
not understand a question — a question in words no procedure uses finds
nothing; an attached PDF is kept and not read; nobody is recorded as having
read a procedure (post orders still do that for their own texts); an incident
raised by hand has no kind and gets no procedure put beside it; nobody is told
when a procedure runs out; the search reads English; the phone's situation
screen does not show the procedure, only its incident screen does; and the
phone's part has been type-checked and its rules tested, not run on a device.
