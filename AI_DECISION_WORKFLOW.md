# AI Security Intelligence — From Suggestion to Decision

**As of:** 2026-10-05 · Part 1 (phase 6, migration `0136`) and Part 2 (phase 7,
migration `0137`) are both built. Phase 10 (migration `0139`) lets a decision
ask a drone to look, through the drone module's own functions.

```
assessment ─► RECOMMENDATION ─► HUMAN DECISION ─► AUTHORISED ACTION ─► outcome
              part 1            part 2
              a suggestion      a person's choice   what the platform then did
```

Three records in three tables, so that none can be mistaken for another.

---

# Part 1 — Recommendations

## What a recommendation is

A row that says: *the layer suggests this step, for this reason, and is this
sure.* It is written when a situation is assessed.

**It does nothing.** No guard is sent, no incident is opened, nobody is told,
no drone flies. The code that writes recommendations imports nothing that could
do any of those and writes only its own table; two tests hold it to that, and a
third reads every alert, incident, escalation and drone flight before and after
a critical situation is given its suggestions and finds them identical.

Every response and every live message that carries a recommendation says
`is_decision: false`.

## The nine steps

| Step | Means | Kind |
|---|---|---|
| `MONITOR` | Keep watching; nothing else is called for yet | looks |
| `VERIFY` | Confirm by some other means — call the site, ask a guard — because no camera shows the place | looks |
| `VIEW_CAMERA` | Look at the cameras named in the suggestion | looks |
| `VERIFY_WITH_DRONE` | Have a drone look from above | looks |
| `INVESTIGATE` | Find out why: a camera that keeps being wrong, an alert that keeps repeating, a camera that stopped sending | looks |
| `DISPATCH_GUARD` | Send a guard on shift | acts |
| `ESCALATE` | Take it to a supervisor | acts |
| `CONTACT_SITE` | Call the site's contact | acts |
| `CREATE_INCIDENT` | Open an incident so that what is done is tracked | acts |

*Looks* and *acts* matter for the confidence, below.

## The rules

Rules, not a model: the same assessment and the same availability always give
the same suggestions. `Kind` and `Risk` come from the assessment
(`AI_RISK_ENGINE.md`). *Sources* is whether more than one kind of source
reported the situation. The number after each step is the rule's own
confidence in it.

| Kind | Risk | Sources | Suggested, surest first |
|---|---|---|---|
| `GUARD_EMERGENCY` | any | — | `DISPATCH_GUARD` 0.95 · `ESCALATE` 0.95 · `VIEW_CAMERA` 0.80 · `CONTACT_SITE` 0.60 |
| `WEAPON` | any | — | `VIEW_CAMERA` 0.90 · `ESCALATE` 0.90 · `CREATE_INCIDENT` 0.80 · `CONTACT_SITE` 0.70 |
| `FIRE_SMOKE` | `INFO` `LOW` `MEDIUM` | — | `VIEW_CAMERA` 0.90 · `ESCALATE` 0.90 · `CONTACT_SITE` 0.80 · `CREATE_INCIDENT` 0.75 |
| `FIRE_SMOKE` | `HIGH` `CRITICAL` | — | `VIEW_CAMERA` 0.90 · `ESCALATE` 0.90 · `CONTACT_SITE` 0.80 · `CREATE_INCIDENT` 0.75 · `DISPATCH_GUARD` 0.65 |
| `FALL` | `INFO` `LOW` `MEDIUM` | — | `VIEW_CAMERA` 0.90 · `DISPATCH_GUARD` 0.80 |
| `FALL` | `HIGH` `CRITICAL` | — | `VIEW_CAMERA` 0.90 · `DISPATCH_GUARD` 0.80 · `ESCALATE` 0.75 · `CREATE_INCIDENT` 0.75 |
| `CAMERA_OFFLINE` | `INFO` `LOW` `MEDIUM` | — | `INVESTIGATE` 0.85 · `MONITOR` 0.60 |
| `CAMERA_OFFLINE` | `HIGH` `CRITICAL` | — | `INVESTIGATE` 0.85 · `DISPATCH_GUARD` 0.60 · `MONITOR` 0.60 |
| `PATROL_FINDING` | `INFO` `LOW` `MEDIUM` | — | `INVESTIGATE` 0.80 · `VIEW_CAMERA` 0.75 |
| `PATROL_FINDING` | `HIGH` `CRITICAL` | — | `INVESTIGATE` 0.80 · `VIEW_CAMERA` 0.75 · `DISPATCH_GUARD` 0.70 · `CREATE_INCIDENT` 0.70 |
| any other | `INFO` | — | `MONITOR` 0.90 |
| any other | `LOW` | — | `MONITOR` 0.85 · `VIEW_CAMERA` 0.70 |
| any other | `MEDIUM` | — | `VIEW_CAMERA` 0.85 · `VERIFY_WITH_DRONE` 0.70 · `MONITOR` 0.60 |
| any other | `HIGH` | one kind | `VIEW_CAMERA` 0.85 · `DISPATCH_GUARD` 0.70 · `VERIFY_WITH_DRONE` 0.70 · `CREATE_INCIDENT` 0.70 |
| any other | `HIGH` | two or more | `DISPATCH_GUARD` 0.85 · `VIEW_CAMERA` 0.80 · `VERIFY_WITH_DRONE` 0.70 · `CREATE_INCIDENT` 0.70 |
| any other | `CRITICAL` | one kind | `ESCALATE` 0.85 · `CREATE_INCIDENT` 0.85 · `VIEW_CAMERA` 0.80 · `DISPATCH_GUARD` 0.75 · `VERIFY_WITH_DRONE` 0.65 |
| any other | `CRITICAL` | two or more | `DISPATCH_GUARD` 0.90 · `ESCALATE` 0.85 · `CREATE_INCIDENT` 0.85 · `VIEW_CAMERA` 0.80 · `VERIFY_WITH_DRONE` 0.65 |
| `BLOCK_LISTED` | `HIGH` | one kind | `VIEW_CAMERA` 0.85 · `ESCALATE` 0.80 · `DISPATCH_GUARD` 0.70 · `VERIFY_WITH_DRONE` 0.70 · `CREATE_INCIDENT` 0.70 |
| `BLOCK_LISTED` | `HIGH` | two or more | `DISPATCH_GUARD` 0.85 · `VIEW_CAMERA` 0.80 · `ESCALATE` 0.80 · `VERIFY_WITH_DRONE` 0.70 · `CREATE_INCIDENT` 0.70 |

The table is what the engine gives at a site where everything is possible — a
camera on the situation, guards on shift, a drone ready, a contact on record, no
incident yet — and with nothing holding a confidence down. A test runs the
engine for every kind at every level and compares it with these rows.

Read with it:

- **One kind of source, look first; two, send first.** At `HIGH`, a single
  source is confirmed on camera before anyone is sent; when a second kind of
  source agrees, the guard goes first and the camera follows them.
- **Nobody is sent towards a possible weapon.** `DISPATCH_GUARD` is not among
  the suggestions for `WEAPON` at any risk. It is confirmed on camera and taken
  to a supervisor. An officer may still decide to send someone; the layer does
  not suggest it.
- **A guard's SOS already has its incident**, so `CREATE_INCIDENT` is not
  suggested for it.
- **No camera on the situation:** `VIEW_CAMERA` becomes `VERIFY`, 0.15 less
  sure, and says that no camera shows the place.
- **No drone at the site:** `VERIFY_WITH_DRONE` is left out altogether. A site
  without a drone is not told in every suggestion that it has none.
- **A drone has already looked:** once a look a person asked for has come back
  as an event in the situation, `VERIFY_WITH_DRONE` is not suggested again. What
  the drone saw is in the assessment; the question is now what to do about it.
  An officer can still choose it — as an override, with a reason.
- **A drone in the air:** when one of the situation's events is a sighting by a
  drone that is still on its mission, that drone's own camera is listed among
  the cameras to look at, marked as the drone's. Once it has landed it is not:
  the camera then shows a dock.
- **Nobody on shift, at `HIGH` or `CRITICAL`:** `CONTACT_SITE` is added at 0.70 —
  the site itself is the next call.
- **A camera that keeps being wrong:** when the risk was lowered because most
  decided alerts of this kind from this camera were marked false,
  `INVESTIGATE` is added at 0.75. Otherwise, when the same alert has repeated
  ten times or more, `INVESTIGATE` is added at 0.70.
- **Never only steps that cannot be taken.** If nothing suggested can be done,
  `ESCALATE` is added at 0.70 for `HIGH` and `CRITICAL`, and `MONITOR` at 0.60
  below that.

## What cannot be done is said

A step the rules suggest but that is not possible right now is kept, marked
`available: false`, and listed after the steps that can be taken — so an
officer is neither offered a button that cannot work nor left wondering why
the obvious step is missing.

| Step | Not available when | It says |
|---|---|---|
| `VIEW_CAMERA` | Every camera on the situation is offline or disabled | The camera is not sending. · None of the cameras is sending. |
| `VERIFY_WITH_DRONE` | The site has a drone, but none is in the air and none is ready with a mission to fly | No drone at this site is ready to fly. |
| `DISPATCH_GUARD` | The situation has no site | The situation has no site, so there is no shift to send a guard from. |
| | An incident of this situation already has a guard dispatched | A guard has already been dispatched to this. |
| | Nobody is on shift at the site | No guard is on shift at this site. |
| | A guard's SOS, and that guard is the only one on shift | No other guard is on shift at this site. |
| `CONTACT_SITE` | The situation has no site | The situation has no site. |
| | The site has no contact number on record | No contact is recorded for this site. |
| `CREATE_INCIDENT` | An incident is already open for one of the situation's events | An incident is already open for this. |

**What is read, and from where** — all of it read-only:

| Fact | From |
|---|---|
| Cameras to look at | The cameras the situation's events came from, most recent first, then the neighbours an administrator linked to them |
| A camera's state | Its last health transition: `online`, `degraded`, `offline`; `disabled` if switched off; `not_known` if it has never reported one. A camera that is `not_known` is not said to be down |
| Guards on shift | Shifts at the site that have started and not ended |
| A guard already dispatched, an incident already open | Incidents the situation's events carry, or that were opened for their alerts, and are not resolved or closed |
| Drones | Drones at the site not disabled or under maintenance; *ready* means in a launchable state, link reported OK, and with an enabled mission; *in the air* means on a mission now |
| A site contact | Whether a contact number is on record. **Not the name or the number** — those stay on the site |

Availability is what was recorded **when the suggestion was written**. A guard
who came on shift a minute later does not rewrite it; the next assessment gets a
new set. Before anything is actually done, the decision step checks again.

## The fourth confidence

`recommendation_confidence`: how sure the layer is of a suggestion.

- A step that **looks** is as sure as its rule. Looking is never the wrong
  thing to do because the picture is unclear.
- A step that **acts** is no surer than the weakest thing it rests on: the
  rule's own confidence, the detection confidence, the correlation confidence
  or the risk confidence, whichever is lowest. `limited_by` says which:
  `RULE`, `DETECTION`, `CORRELATION` or `RISK`.

Suggestions are then listed surest first. So when the model was not sure, or
many things about the site were not known, the steps that act fall below the
steps that look — "confirm on camera" rises above "send a guard" — by
arithmetic, with no special case, and nothing is withheld: only the order and
the confidence change.

**The exception is a guard's SOS.** A person asking for help is not a
detection. Sending help and telling a supervisor keep the rule's own confidence
whatever is not known about the site.

It is stored and served beside the other three — detection, correlation, risk —
each under its own name. There is no single "confidence".

## Priority

From the risk level, per step:

| Risk | Priority |
|---|---|
| `INFO` | `LOW` |
| `LOW` | `LOW` |
| `MEDIUM` | `MEDIUM` |
| `HIGH` | `HIGH` |
| `CRITICAL` | `URGENT` |

`MONITOR` is always `LOW`. `INVESTIGATE` is never above `MEDIUM`: finding out
why a camera misfires is not what is urgent tonight.

## The record

`security_recommendations`, one row per suggested step:

| Column | Holds |
|---|---|
| `situation_id`, `assessment_id` | Which situation, and which assessment of it the suggestions were made for |
| `rank` | The order: 1 is first |
| `action`, `priority`, `reason` | The step, how urgent, and why — a sentence, never blank |
| `confidence`, `confidence_limited_by` | The fourth confidence and what held it down |
| `available`, `unavailable_reason` | Whether it can be done now, and if not, why — always one or the other |
| `supporting` | What it rests on: the risk level and score, the sentences of the top risk factors, and for the step itself the cameras to look at, how many guards were on shift, the drone's state, or the incident already open |
| `engine_version` | `rules-2` (`rules-1` before phase 10, which stopped suggesting a second drone look) |

**One set per assessment, written once.** A new assessment gets a new set; the
earlier one stays. The application's database role is granted `SELECT` and
`INSERT` on the table and nothing else. Which set is *current* is not a column
that could go stale: it is the set of the situation's latest assessment.

## Announcement

`intel_recommendation_ready` on the tenant's existing channel, after the rows
are saved: the situation's id and number, the assessment it belongs to, kind,
label, risk level and score, `is_decision: false`, the first step that can be
taken (`action`, `priority`, `reason`, `recommendation_confidence`,
`limited_by`), how many steps there are and how many of them cannot be taken.

## API

| Method | Path | Permission | Returns |
|---|---|---|---|
| GET | `/security-intelligence/situations/{situation_id}/recommendations` | `intel:read` `intel:recommendation:read` | The suggestions for the situation's latest assessment, or with `assessment_id` those made for an earlier one. `is_decision` is always false; `current` says whether it is the latest set; `assessment.confidence` holds the three confidences and each suggestion its own `recommendation_confidence` |

A viewer may read situations and assessments but not what the layer suggests
doing about them. A caller restricted to certain sites gets 404 for another
site's situation. A situation not yet assessed returns an empty list.

## Limits

- **The rules are judgement.** Which step for which kind, and how sure, were
  chosen and not measured. They are one table in one file so that they can be
  argued with. The feedback dataset collects what officers actually decided
  and what reviewers said of it, for a person to read; nothing changes a rule
  by itself (`AI_SECURITY_INTELLIGENCE_ARCHITECTURE.md`, *Feedback*).
- **Availability is a snapshot**, as above. When a decision is taken, what it
  would carry out is worked out again from the situation as it then is.
- **"On shift" is not "nearby" or "free".** The layer counts guards on shift at
  the site. It does not know which of them is closest or already busy; choosing
  the guard is the officer's.
- **Ready to fly is not cleared to fly.** A ready drone has still to pass the
  drone module's own pre-flight checks when someone asks it to fly.
- **A camera's state is its last transition.** A camera that has never reported
  one is `not_known`, and the suggestion to look at it stands.
- **A suggestion made for a superseded assessment may never exist.** If a
  second assessment is written before the first was given its suggestions, only
  the latest gets a set.

## Tests

`backend/tests/test_intel_recommend.py` (82):

- **The rules, with nothing running:** each level and each kind; one source
  looks first and two send first; an incomplete picture puts looking first while
  withholding nothing; a guard's SOS is not held back; nobody is sent towards a
  possible weapon; every unavailable step says why and comes last; never only
  unavailable steps; for every kind at every level, every suggestion has a
  reason, a priority, what it rests on, and none of *intruder*, *unauthorised*,
  *criminal*, *thief*, *trespass*, *suspect*.
- **Against the database, as the application's own role:** availability read
  from shifts, camera health, camera links, drones, incidents and the site —
  with a row beside each that must not count — and never a contact's name or
  number.
- **Written once:** a set per assessment; not again when nothing changed; the
  first set untouched by the second; the role cannot update or delete; two
  tenants apart, and a forged row for another tenant refused.
- **Nothing acted on:** every alert, incident, escalation, drone flight and
  verification request identical before and after a critical situation gets its
  suggestions.
- **The runner and the API:** only tenants that asked; announced once, after
  the assessment, saying it is not a decision; a viewer refused; another site
  and another tenant 404; an earlier set still readable.
- **The schema:** row-level security forced, select-and-insert only; no
  suggestion without a reason, none unavailable without saying why.

`backend/tests/test_intel_docs.py` checks the rules table above against the
engine itself, and the steps, messages, priorities and API table against the
code.

---

# Part 2 — Decisions and actions

Built in phase 7: migration `0137`, `backend/app/services/intel_decisions.py`,
`intel_actions.py`, `backend/app/routers/security_decisions.py`. Who may decide
and what an override is are in `AI_HUMAN_DECISION_MODEL.md`; this part is what
happens when someone does.

## The path of a decision

```
an officer opens the situation       POST …/reviews      recorded once: they looked
what may I do here?                  GET  …/authority    each decision: allowed, how, or why not
the officer chooses                  POST …/decisions    judged, then recorded as theirs
   │
   ├─ may decide alone ──────────────► each step carried out now, each leaving a row
   └─ may decide with approval ──────► nothing carried out; waits
                                       POST /decisions/{id}/approve   carried out under the approver
                                       POST /decisions/{id}/reject    nothing carried out; says why
the trail                            GET  …/decisions    looked · decided · approved · done · how it ended
```

The decision is saved before anything is carried out. If a step then fails, the
person's decision is still on record, and so is the failure.

## What each decision carries out

Fourteen decisions: the nine steps a recommendation can name, and five only a
person can choose. A step is taken only where there is something to take it on:
acknowledging acts on the alerts still open, closing on those not yet closed.

| Decision | With alerts open and no incident | With an incident already open |
|---|---|---|
| `MONITOR` | — | — |
| `VERIFY` | — | — |
| `VIEW_CAMERA` | — | — |
| `VERIFY_WITH_DRONE` | — | — |
| `INVESTIGATE` | — | — |
| `CONTACT_SITE` | — | — |
| `DISPATCH_GUARD` | `INCIDENT_CREATE` → `INCIDENT_DISPATCH` | `INCIDENT_DISPATCH` |
| `ESCALATE` | `ALERT_ASSIGN` | `ALERT_ASSIGN` → `INCIDENT_ASSIGN` |
| `CREATE_INCIDENT` | `INCIDENT_CREATE` | refused: confirm it instead |
| `ACKNOWLEDGE` | `ALERT_ACKNOWLEDGE` | `ALERT_ACKNOWLEDGE` |
| `CONFIRM_INCIDENT` | refused: there is none | `INCIDENT_CONFIRM` |
| `REQUEST_ASSISTANCE` | — | — |
| `FALSE_POSITIVE` | `ALERT_FALSE_POSITIVE` | `ALERT_FALSE_POSITIVE` |
| `RESOLVE` | `ALERT_DISMISS` | `ALERT_DISMISS` → `INCIDENT_RESOLVE` |

A dash is a decision that is a record and nothing more: it is the officer who
looks at the camera and makes the call.

`VERIFY_WITH_DRONE` is a record too **unless the officer says how the drone
should look** — see *Asking a drone*, below. The layer never makes that choice.

`DISPATCH_GUARD` needs the guard, chosen by the officer. `ESCALATE` needs the
person it goes to: an active admin, manager or supervisor other than the one
deciding.

## Asking a drone

```
officer decides VERIFY_WITH_DRONE, and says how
      ─► the drone module's own function, under the officer's own drone permission
      ─► the drone looks
      ─► what it saw comes back as an event in the same situation
      ─► the situation is assessed again, and suggestions are made again
      ─► the officer decides
```

A drone looks only because a person decided it should and chose how. There are
two ways, and the decision names one of them or neither:

| The officer chooses | Sent as | Step | What the drone module then does |
|---|---|---|---|
| The flight that saw a sighting holds and looks again | `drone_event_id`, `hold_seconds` (5 to 120, 30 if not said) | `DRONE_HOLD` | Its own checks — the flight active, the drone still within 75 m of the spot, a provider that can pause and resume, battery to come home — then an ordinary pause in its command queue, and a resume when the hold is over |
| A mission the site already has is started | `drone_mission_id` | `DRONE_LAUNCH` | Its own licence check and pre-flight. Passed, the flight is ready and its runner launches it; failed, it records the attempt as blocked, with every reason |
| Neither | — | — | Nothing. The decision is a record and the officer flies it from the drone screens |

- `drone_event_id` must be one of **this situation's** drone sightings;
  `drone_mission_id` a mission **switched on at this situation's site**.
  Anything else is refused (422) and nothing is recorded.
- The officer needs the drone module's own permission for the step:
  `drone:operate` to hold a flight, `drone:mission:execute` to start one. A
  guard who may decide here but may not operate a drone can record that a drone
  should look, and cannot ask one.
- An organisation without the Drone Patrol licence cannot start a flight from a
  decision (403, in that module's own words). A flight already in the air can
  still be asked to hold: that module does not licence-gate it either.
- **When the drone module says no, that is the step's record.** "409: The drone
  is 212 m from where it saw this…", "Pre-flight stopped flight DPS-…: Battery
  12% is below…". The decision stands; the step is `FAILED` with the reason.
- Where the policy asks for approval, nothing is asked of the drone until a
  second person approves, and it is then asked under the approver's name.
- **The layer chooses no flight and no mission, steers nothing, makes no mission
  and changes none.** The runner never does any of this: it cannot import the
  module that acts, and a test holds that.

`GET …/situations/{id}/aerial` is what the officer's screen reads first: the
situation's sightings and whether each one's flight could be asked to hold, the
site's missions and whether each could start now, and everything already asked
from this situation with what came of it. "Could" is a first answer — the
drone module decides when it is asked.

### What comes back

- **A look that was held** (`drone_verification_requests`, completed, at a
  sighting the drone module has verified) becomes an event: *Drone looked
  again: 4 more detection(s) — …*, or *…and saw nothing more*. It joins the
  situation its sighting is in, by `DRONE_LOOK`, with what it saw in the reason.
  A look asked for from the drone screens joins the same way.
- **A sighting from a flight a decision started** joins the situation that
  decision was about, by `DRONE_LOOK` at 0.60, and says why it is there: the
  flight follows its own route and may have seen something else. A surer
  ordinary link to another situation wins.
- **Never into a situation a person has closed.** Something seen after a matter
  was ended opens a matter of its own, where it will be seen.
- A situation that had gone quiet is woken by a look that comes back to it.
- The risk engine then counts the look (`AI_RISK_ENGINE.md`), a new assessment
  is written, new suggestions are made for it, and the situation is marked
  `reassessed_since_decision` until a person decides again. **The earlier
  decision stands; nothing is decided by the layer.**

## Through the platform's own functions

Nothing new is done to carry a decision out. Each step calls the function the
platform already uses when an officer presses the existing button, so a step
taken from a decision is exactly the step taken by hand — the same rows, the
same events on the wire. None of those functions was changed.

| Step | Goes through | Needs |
|---|---|---|
| `ALERT_ACKNOWLEDGE` | `app.routers.alerts.bulk_acknowledge_alerts` | `alert:acknowledge` |
| `ALERT_FALSE_POSITIVE` | `app.routers.alerts.mark_false_positive` | `alert:acknowledge` |
| `ALERT_DISMISS` | `app.routers.alerts.bulk_dismiss_alerts` | `alert:acknowledge` |
| `ALERT_ASSIGN` | `app.routers.alerts.bulk_assign_alerts` | `alert:acknowledge` |
| `INCIDENT_CREATE` | `app.routers.incidents.create_incident` | `incident:create` |
| `INCIDENT_DISPATCH` | `app.routers.dispatch.dispatch_guard` | `incident:dispatch` |
| `INCIDENT_ASSIGN` | `app.routers.incidents.assign_incident` | `incident:assign` |
| `INCIDENT_RESOLVE` | `app.routers.incidents.resolve_incident` | `incident:resolve` |
| `DRONE_HOLD` | `app.routers.drone_operations.verify_with_drone` | `drone:operate` |
| `DRONE_LAUNCH` | `app.routers.drone_planning.run_mission` | `drone:mission:execute` |
| `INCIDENT_CONFIRM` | — the layer's own record; the incident is not touched | — |

The person under whose authority a step runs must hold what it needs: the one
who decided, or the one who approved. So an operator, who may not resolve an
incident anywhere on the platform, cannot resolve one from here either.

An incident opened from a decision carries the situation's title and severity,
its first camera, and a description that says it was opened by a person's
decision from that situation, with the assessment at the time.

## How a step ends

Every step leaves a row in `security_actions`, whatever happened.

| Result | Means |
|---|---|
| `OK` | Done, through the function named |
| `SKIPPED` | The platform's own "nothing to do" — already acknowledged, already resolved |
| `FAILED` | It did not happen. The row holds the status and message the function gave, or the kind of error — never an error's text, which can carry row contents |
| `RECORDED` | Nothing was to be carried out, and the row says so |

A failed step does not undo the decision or the steps before it, and the next
step is still attempted unless it depended on the one that failed.

## Reports from the ground

Beside the decisions, and not one of them: the person dealing with a situation
says where they are with it and what they see.

```
the command centre dispatches a guard      a decision, as above
the guard's phone shows it                 GET  /my-situations
"I have this"                              POST …/observations   ACCEPTED
"I am there"                               POST …/observations   ARRIVED, with the phone's position
"this is what I see"                       POST …/observations   OBSERVATION, in words
what to do about it                        POST …/decisions      under the decision policy
```

A report changes no alert, incident or dispatch and does not move where the
situation stands. A guard reports from the site of the shift they are on, or
from a situation they were dispatched to. Nothing can be reported on a closed
situation.

## One press, one decision

A request can carry `client_ref`. Sent again with a retry it is answered with
the decision already recorded, and nothing is carried out twice.

## Live messages

On the tenant's existing channel, after the record is saved:
`intel_decision_recorded`, `intel_decision_pending_approval`,
`intel_decision_approved`, `intel_decision_rejected`, and
`intel_observation_recorded` for a report from the ground. Each carries ids,
codes and where things stand. Never a name, never a note, never what was said.

## API

| Method | Path | Permission | Returns |
|---|---|---|---|
| POST | `/security-intelligence/situations/{situation_id}/reviews` | `intel:read` `intel:recommendation:read` | Records that the caller looked at what was suggested. Once per person per assessment |
| GET | `/security-intelligence/my-situations` | `intel:read` | The open situations in front of the caller: what they were dispatched to first, then by risk. For a guard, those and their shift's site where the policy lets a guard decide |
| POST | `/security-intelligence/situations/{situation_id}/observations` | `intel:read` `intel:decide` | Records a report from the ground: accepted, arrived, or what was seen. Changes nothing else. 201; 200 for a retry |
| GET | `/security-intelligence/situations/{situation_id}/observations` | `intel:read` | What was reported from the ground, oldest first |
| GET | `/security-intelligence/situations/{situation_id}/authority` | `intel:read` | For each of the fourteen decisions: whether the caller may take it, alone or with approval, whether it would follow or override, whether a reason will be asked for, what it would carry out — or why not |
| GET | `/security-intelligence/situations/{situation_id}/responders` | `intel:read` `intel:decide` | The guards a decision could dispatch — those on shift at the site first — and the people it could be escalated to. A list to choose from; the layer does not choose |
| GET | `/security-intelligence/situations/{situation_id}/aerial` | `intel:read` | The situation's drone sightings and whether each one's flight could be asked to hold; the site's missions and whether each could start (for someone with `drone:read`); what decisions here asked of a drone and what came of it. Asks nothing of a drone |
| POST | `/security-intelligence/situations/{situation_id}/decisions` | `intel:read` `intel:decide` | Records the caller's decision and carries it out, or holds it for approval. 201; 200 for a retry |
| GET | `/security-intelligence/situations/{situation_id}/decisions` | `intel:read` | The trail: who looked, each decision, any verdict, each action, and how it ended |
| GET | `/security-intelligence/decisions` | `intel:read` | Decisions across situations, most recent first. `state=pending_approval` is the approver's queue |
| GET | `/security-intelligence/decisions/{decision_id}` | `intel:read` | One decision |
| POST | `/security-intelligence/decisions/{decision_id}/approve` | `intel:read` `intel:approve` | Approves a waiting decision; it is then carried out under the approver |
| POST | `/security-intelligence/decisions/{decision_id}/reject` | `intel:read` `intel:approve` | Rejects a waiting decision, with a note |
| GET | `/security-intelligence/decision-policy` | `intel:read` | The default, the organisation's policy and each site's own |
| PUT | `/security-intelligence/decision-policy` | `intel:read` `intel:manage` | Sets the organisation's policy. Audited |
| PUT | `/security-intelligence/decision-policy/sites/{site_id}` | `intel:read` `intel:manage` | Gives a site its own policy. Audited |
| DELETE | `/security-intelligence/decision-policy/sites/{site_id}` | `intel:read` `intel:manage` | Removes a site's own policy. Audited |

Request bodies refuse fields they do not know. A caller restricted to certain
sites gets 404 for another site's situation or decision.

## Tests

`backend/tests/test_intel_decisions.py` (39) and `backend/tests/test_intel_field.py`
(11): see `AI_HUMAN_DECISION_MODEL.md`.
`backend/tests/test_intel_drone.py` (34): asking a drone. The rules with nothing
running; then **real simulated flights** — an officer asks a flight to hold, the
drone module's own request, pause and audit entry appear under the officer's
name, the drone runner holds and resumes it, and what it saw comes back to the
same situation, which is assessed again; a look that saw nothing; the drone
module's refusal kept as the step's record; a mission started from a decision
and flown; pre-flight stopping one; no licence; approval; a look that comes back
after the matter was closed opening a new matter; a quiet situation woken; and
the runner's own ticks bringing a look back, announcing it, and changing no row
of the drone module, no alert and no incident.
`backend/tests/test_intel_docs.py` checks the two tables above against the code:
what each decision carries out by running the planner, and each step's function
by importing it.
