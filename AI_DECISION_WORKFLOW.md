# AI Security Intelligence — From Suggestion to Decision

**As of:** 2026-10-05 · **Part 1 is built** (phase 6, migration `0136`,
`backend/app/services/intel_recommend.py`). **Part 2 is not built** and is
described only as what it will be.

```
assessment ─► RECOMMENDATION ─► HUMAN DECISION ─► AUTHORISED ACTION ─► outcome
              part 1 (built)    part 2 (phase 7, not built)
              a suggestion      a person's choice   what the platform then did
```

Three records in three tables, so that none can be mistaken for another. This
document covers the first.

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
| `engine_version` | `rules-1` |

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
  argued with; phase 14 collects what officers actually decided.
- **Availability is a snapshot**, as above.
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

# Part 2 — Decisions and actions (phase 7, not built)

A person with the authority to decide will choose what to do: accept a
suggestion, choose a different step, or override with a reason from a fixed
list. That choice will be its own record — who, in what role, what, whether it
followed the suggestion, and why if not. What the platform then does, through
its existing functions, will be a third record with its result. Who may decide
what, at which sites and up to which risk, will be configuration.

None of that exists yet. Today a recommendation can be read, and nothing else
can be done with it.
