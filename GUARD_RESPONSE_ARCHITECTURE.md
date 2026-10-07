# Guard Response — Architecture

**Phase 4 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-07, migration `0146`. This document says what was built, the
rules it is built under, and what it deliberately does not do.

Before this phase a guard could be sent to an incident and marked as arrived,
and that was all. The guard was told nothing: the dispatch wrote a name on the
incident. Nothing recorded whether they accepted, set off or could not come.
The platform stored three times per severity — acknowledge within, arrive
within, resolve within — and compared none of them with anything; the column
`incidents.sla_breached` had never been set, and the table `escalation_events`
had never been written to.

```
 a person DISPATCHES            ┌───────────────────────────────┐
 (the existing endpoint,  ────► │  A RESPONSE: one guard, one   │ ──► written through to the
  unchanged)                    │  sending, and the steps the   │     incident's own status,
                                │  guard takes on their phone   │     history and arrival time
 WHO TO SEND: a ranked    ────► └───────────────────────────────┘
 suggestion with reasons                       │
                                               ▼
                                THE CLOCKS, when switched on: a clock that
                                runs out is recorded once and TOLD to people.
                                Nothing is reassigned, re-dispatched or closed.
```

---

## 1. The rules

1. **The dispatch is a person's, through the existing endpoint.** Nothing in
   this phase sends a guard. `POST /api/v1/dispatch/incidents/{id}` is as it
   was; the new router has no route that dispatches.
2. **A suggestion is not a decision.** "Who to send" ranks the guards on shift
   and shows the parts each score is made of. It answers `is_decision: false`,
   writes nothing, and the person may choose anybody on the list.
3. **Only the guard who was sent answers for a response**, as a signed-in
   person: never an API key, never a support session, and nobody else — not
   an administrator either.
4. **What a guard does is written through to the incident.** Setting off moves
   the incident to `en_route`; arriving moves it to `on_scene` and stamps
   `guard_arrived_at` — the same columns and the same `incident_status_history`
   the incident's own status endpoint writes, so every screen that already
   reads an incident shows it.
5. **A guard who is not coming gives the incident back.** Declining, like being
   stood down, leaves the incident with nobody sent, and open again if the
   guard had not arrived. Nobody is sent in their place: a person does that.
6. **A step is added to and never rewritten.** The application's role may
   insert a step and read it. It cannot update or delete one.
7. **The clocks are off until switched on, and never reach back.** Switched
   on, they judge the incidents opened from that moment. Nothing older is
   marked late.
8. **A clock that runs out tells people and changes nothing else.** The
   incident is not reassigned, re-dispatched, closed or raised in severity.
9. **Whoever is told may see the incident.** A step addressed to a role goes to
   the active people in that role who may see the incident's site. How many
   people that was is part of the record, so a step that reached nobody shows
   as that.
10. **Super Admin and the client role hold neither new permission**, and a
    policy cannot be addressed to either.

---

## 2. A response and its steps

A **response** is one guard's answer to one sending. The existing dispatch
writes the guard and the time on the incident and nothing else; the record of
the sending (`incident_responses`) is made the first time anything looks — the
desk, the guard's own phone, or the scheduler's pass a minute later — and that
is when the guard is told. A sending older than an hour is given its record
without waking anybody's phone.

| State | Means |
|---|---|
| `SENT` | Sent, not yet answered |
| `ACCEPTED` | The guard said they are taking it |
| `EN_ROUTE` | The guard said they have set off |
| `ARRIVED` | The guard said they are there |
| `DECLINED` | The guard said they cannot attend, and why |
| `STOOD_DOWN` | Called off by whoever may dispatch, with a reason — or closed because the incident was dispatched again |

| A guard's step | From | Moves the response to | And the incident |
|---|---|---|---|
| `ACCEPTED` | `SENT` | `ACCEPTED` | — |
| `EN_ROUTE` | `SENT`, `ACCEPTED` | `EN_ROUTE` | status `en_route`, a line of history with where it was said from |
| `ARRIVED` | `SENT`, `ACCEPTED`, `EN_ROUTE` | `ARRIVED` | status `on_scene`, `guard_arrived_at` stamped, a line of history |
| `DECLINED` | `SENT`, `ACCEPTED` | `DECLINED` | nobody sent; status `open`; a line of history with the reason |
| `REPORTED` | any state that is not over | no change | — |

Setting off or arriving without having accepted first counts as accepting. A
decline and a stand-down each require a reason, and the database refuses one
without. A report is what the guard found, said from the ground; it is added to
the steps and changes no state. Each step carries the phone's position if the
phone will give one, and is recorded without rather than not recorded.

**Sending somebody else** is standing the first guard down and dispatching
again — or just dispatching again, which closes the first response as stood
down with the reason "The incident was dispatched again." Each sending has a
response of its own (`uq_response_sending`).

**The status `in_progress`.** The existing dispatch moves an open incident to
`in_progress`, a status the incident workflow (`open`, `dispatched`,
`en_route`, `on_scene`, …) does not otherwise use. This phase does not change
the dispatch: it treats `in_progress` as "sent, not yet set off", which is what
it means.

---

## 3. Who to send

`GET /recommend` reads who is clocked in at the incident's site and where each
last recorded being — the same reading the security map uses
(`services/guard_positions.py`, decision E3: there is no live position) — and
gives each guard a score made of stated parts.

| Part | Points |
|---|---|
| `AVAILABILITY` | +40 free; 0 already sent on an incident that is still open; −100 has an emergency of their own open |
| `DISTANCE` | +30 within 100 m; +20 within 300 m; +10 within 1000 m; 0 farther, or not known |
| `POSITION_AGE` | +10 recorded in the last 15 minutes; 0 older; −10 stale (over an hour) |
| `WORKLOAD` | −5 for each incident already sent on this shift, at most −15 |
| `CERTIFICATION` | +10 holds, in date, every certification the site requires; 0 does not. Scored only when the site requires one |

Highest score first; among equals, somebody free before somebody already sent,
then the nearer. Each part comes with a sentence saying why. When there is
nobody to suggest the answer says why: the incident has no site, or nobody is
clocked in there. It needs `incident:dispatch`, because it is for the person
about to dispatch.

---

## 4. The clocks

| Clock | Runs from | To | Is met when |
|---|---|---|---|
| `ACKNOWLEDGE` | the incident was opened | `ack_within_seconds` later | somebody first did something with it: changed its status, or sent a guard |
| `ARRIVAL` | a guard was sent | the deadline the existing dispatch stamps (`sla_deadline_at`) | the guard arrived |
| `RESOLVE` | the incident was opened | `resolve_within_seconds` later | it was resolved |

The times are the existing `sla_configs`, one row per severity, saved through
the existing `PUT /api/v1/sla/configs/{severity}` — which until this phase had
no screen. A severity with no times set has no clocks: there is nothing to be
late for. A clock met after its time is a breach all the same.

**The switch** is the setting `response.sla_enabled`, off by default. The
moment it was last switched on is the moment the clocks judge from; switching
it through `PUT /settings` leaves the row alone when nothing changes, so that
saying "on" again does not move that moment. The desk shows the clocks either
way, and says when nothing is being judged.

**The pass.** The scheduler runs `response_sla.run` every minute, one
organisation at a time, each in a transaction of its own, as the application's
role with row-level security in force. It looks at incidents opened since the
switch that are still open or were resolved in the last 24 hours, at most 2000
a pass. An arrival clock belongs to a sending: an incident sent twice can be
late twice.

---

## 5. Who is told

The first time a clock is found to have run out:

- `incidents.sla_breached` is set, and `escalated_at` if it was not;
- the breach is recorded once in `incident_escalations`: which clock, when it
  fell due, who it was addressed to, how many people that was;
- a row is written to the existing `escalation_events` (`sla_acknowledge`,
  `sla_arrival`, `sla_resolve`), where whether the telling went out is marked;
- the person the severity's settings name is told.

**An escalation policy** adds further steps: *when an incident is still not
acknowledged after ten minutes, tell the supervisors*. A policy watches one of
`NOT_ACKNOWLEDGED`, `NOT_ARRIVED` or `NOT_RESOLVED`, for every site or one, for
every severity or one, after 30 seconds to 7 days, and tells everybody in one
role or one person. Each step is recorded once per incident (`policy_step` in
the existing log) — once per sending, for `NOT_ARRIVED`. A policy needs no
times to be set, but like the clocks it does nothing until the switch is on.
A policy is changed and retired, never removed, and a recorded
step keeps the name the policy had then. A step is not a breach:
`sla_breached` is set by a clock running out, not by a policy.

**Telling** is done three ways, all of them already the platform's, after the
record is committed so that a telling that fails cannot undo it:

| | |
|---|---|
| the organisation's own screens | an event on the organisation's channel: `incident_sla_breached`, `incident_escalated`, and for a response `incident_response_sent`, `incident_response_declined`, `incident_response_stood_down` |
| the phones of the people addressed | Expo push, to the devices those people registered |
| the organisation's notification rules | whichever rule asks for that kind of event — email, SMS or webhook |

A person held to other sites is not told of an incident at a site they may not
see, and nothing is put on their phone. `notification_sent` is true when the
event went to the screens or reached a phone.

---

## 6. API

All under `/api/v1/incident-responses`.

| | | Needs |
|---|---|---|
| `GET` | `/desk` | `response:read` |
| `GET` | `/recommend` | `response:read`, `incident:dispatch` |
| `GET` | `/mine` | `response:act` |
| `GET` | `/settings` | `response:read` |
| `PUT` | `/settings` | `response:read`, `sla:manage` |
| `GET` | `/policies` | `response:read` |
| `POST` | `/policies` | `response:read`, `sla:manage` |
| `PATCH` | `/policies/{id}` | `response:read`, `sla:manage` |
| `POST` | `/policies/{id}/retire` | `response:read`, `sla:manage` |
| `POST` | `/policies/{id}/restore` | `response:read`, `sla:manage` |
| `GET` | `/escalations` | `response:read` |
| `GET` | `/{incident}` | |
| `POST` | `/{incident}/accept` | `response:act` |
| `POST` | `/{incident}/decline` | `response:act` |
| `POST` | `/{incident}/en-route` | `response:act` |
| `POST` | `/{incident}/arrived` | `response:act` |
| `POST` | `/{incident}/report` | `response:act` |
| `POST` | `/{incident}/stand-down` | `response:read`, `incident:dispatch` |

`GET /{incident}` is decided inside: whoever holds `response:read` reads it at
the sites they may see; a guard who was sent on the incident reads their own
response to it and nothing of who else was told; anybody else is answered 404.
There is no `DELETE`, and no route here dispatches. Somebody held to particular
sites writes policies for those sites only and does not switch the clocks.

Each change is made by a signed-in person and audited: `response.accept`,
`response.decline`, `response.en_route`, `response.arrive`, `response.report`,
`response.stand_down`, `response.sla.enable`, `response.sla.disable`,
`response.policy.create`, `response.policy.update`, `response.policy.retire`,
`response.policy.restore`.

**Permissions** (migration `0146`):

| | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Guard 5 | Viewer 6 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|
| `response:read` | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – |
| `response:act` | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | – |

`incident:dispatch` and `sla:manage` are the existing permissions, held as
before. A guard does not read the desk; they read what they were sent on.

---

## 7. Screens

- **Response Desk** (`/response-desk`), under Monitoring — every open incident
  with who was sent, where the response stands and the three clocks; counts of
  what is waiting; *Who to send*, which shows the ranked suggestion and sends
  the guard the person chooses through the existing dispatch; *Stand down*;
  and one incident's responses, steps and who was told.
- **Response Settings** (`/response-settings`), under Configuration — the
  switch, the times by severity, the policies, and what was told in the last 24
  hours.
- **The phone** — a card on the first page while the person has been sent
  somewhere, and on the incident the buttons the server says may be pressed:
  Accept, On my way, I am there, Report what I found, I cannot attend.

The existing Incidents screen and its dispatch dialog are unchanged.

---

## 8. Files

| | |
|---|---|
| `backend/alembic/versions/0146_incident_responses.py` | Four tables, their policies and grants, two permissions |
| `backend/app/services/incident_response.py` | A response, its steps, and who is suggested |
| `backend/app/services/response_sla.py` | The clocks, the policies, and the pass |
| `backend/app/services/response_notify.py` | Telling people |
| `backend/app/routers/incident_responses.py` | The API |
| `frontend/src/api/incidentResponses.ts` | The typed client |
| `frontend/src/pages/response/` | The two screens |
| `frontend/src/components/response/` | Their dialogs and shared wording |
| `mobile/src/api/responses.ts` | The phone's calls |
| `mobile/src/lib/responses.ts` | The rules of the phone's card |
| `mobile/src/components/ResponseCard.tsx` | The two cards |

Existing files changed, by additions only: `backend/app/main.py` (the router
is registered), `backend/app/core/config_keys.py` (the setting is known),
`backend/app/scheduler_main.py` (the pass is run every minute),
`frontend/src/App.tsx` (two routes),
`frontend/src/components/layout/Sidebar.tsx` (two menu entries),
`frontend/src/hooks/usePermission.ts` (the two permissions),
`mobile/src/screens/DashboardScreen.tsx` (the card),
`mobile/src/screens/IncidentDetailScreen.tsx` (the card).

**Existing columns and an existing table now written for the first time:**
`incidents.sla_breached`, `incidents.escalated_at`,
`incidents.escalated_to_user_id`, and `escalation_events`. With the clocks off
— the default — none of them is written and every figure that reads them is as
it was. With the clocks on, the incident report and the site security score,
which already read `sla_breached`, begin to show breaches.

---

## 9. Tests

| | |
|---|---|
| `backend/tests/test_incident_responses.py` | Which step from which state; how a guard is scored and ranked; a response through the API; not coming, called off, sent again; who may answer; the suggestion; the desk; what the application's role and the database refuse |
| `backend/tests/test_response_sla.py` | The three clocks; when a step falls due; the switch; the pass as the application's role across two organisations; each clock; policies and who they reach; writing a policy; what was told |
| `backend/tests/test_guard_response_docs.py` | That this document says what the code does |
| `frontend/src/pages/response/response.test.tsx` | The two screens |
| `mobile/src/api/responses.test.ts` | What the phone puts on the wire |
| `mobile/src/lib/responses.test.ts` | The rules of the phone's card |

---

## 10. What this does not do

- **It does not dispatch, reassign or re-dispatch.** A guard who declines, or a
  clock that runs out, leaves the next move to a person.
- **It does not track a guard.** "On my way" is what the guard said, with where
  the phone was when they said it. Nothing follows them to the scene, and
  nothing estimates when they will arrive.
- **It does not change the existing dispatch**, the incident status endpoint or
  the Incidents screen. A guard still cannot use the status endpoint — that
  needs `incident:update`, which guards do not hold — and the phone's *Mark as…*
  button on an incident is as it was; the response card is the guard's way.
- **It does not measure a guard.** Response times are recorded as facts of an
  incident. Nothing here scores a person, and no employment decision is made
  from them (workforce intelligence is phase 11, and advises).
- **It does not escalate alerts.** Unacknowledged alerts have their severity
  raised by the scheduler as before; this phase is about incidents.
- **The phone part has not been run on a device.** Its rules and its calls are
  tested; the cards have been type-checked and not seen on a phone. Push
  reaches a phone only when the app has registered a device.
- **It has been exercised on test data.** On the development database nobody is
  dispatched and the clocks are off. It has not been run on a real site.
