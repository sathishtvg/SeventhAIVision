# AI Security Intelligence — Normality and Risk

**As of:** 2026-10-05 · phase 5 of 15 · migration `0135` ·
`backend/app/services/intel_risk.py` · rules `rules-2` since phase 10, which
added what a drone's second look is worth

A situation says *what happened*: these events, at this place, joined for these
reasons. This stage says two more things about it and nothing else — **how
unusual** it is for that place and hour, and **how much it matters there**. It
writes its answer down, with every reason, as an *assessment*.

An assessment is an opinion for a person to read. It dispatches nobody, opens no
incident, acknowledges no alert and changes no existing record. What an officer
does about it is theirs (phase 7).

## What it is, and is not

- **Rules, not a model.** Every point of risk is a named factor with a sentence
  beside it. Nothing was trained and nothing learns. The same situation, context
  and history always give the same score.
- **Not the event's severity.** Severity is fixed by the rule that raised the
  alert. Risk is a judgement in context, and can be lower or higher.
- **Not the model's confidence.** That is how sure the detector was about what
  it saw. It is carried beside the risk, never folded into it.
- **What is not known adds nothing.** A site with no hours set gets no
  out-of-hours points. A person nobody identified gets no points for being
  unidentified. Each unknown lowers the *risk confidence* instead.
- **The words are chosen.** *Unusual, suspicious, requires review.* An
  assessment never says a person is an intruder or is not authorised, and never
  states intent.

## Normality

How usual this is, 0 to 100, from **this camera's own past**: over
the last 8 weeks, in how many of them did this camera raise this kind of alert
within half an hour of this same time of the week. It is read from `alerts`,
which holds the history from before the layer was switched on.

| Factor | When | Points |
|---|---|---|
| `HABIT` | The share of those weeks in which it happened | 0 to 100 |
| `HOURS` | Outside the site's business hours | −20 |
| `ZONE` | A restricted zone on the camera was in force | −15 |
| `EXPECTED` | Each thing that could ordinarily explain it: the site was open, the person or vehicle is on an allow list, visitors were signed in, a contractor permit was in force, someone was let in nearby | +10 each |

The sum is held between 0 and 100. **Anomaly is 100 − normality.** Both are
stored with their factors.

**Too little history is said, not guessed.** With fewer than 4 weeks of alerts
of any kind from the camera — or an event with no camera, or one that is not an
alert kind — there is no normality score and no anomaly score at all. The
assessment records "Insufficient history" among what was not known, and risk
takes nothing from it.

## Risk

A score from 0 to 100: the sum of the factors below, held to that range.

| Factor | When | Points |
|---|---|---|
| `SEVERITY` | Always. The most severe event's own severity: info, low, medium, high, critical | +5, +15, +30, +45, +60 |
| `ZONE` | A restricted zone on the camera was in force, by the zone's severity: low, medium, high or critical | +10, +15, +20, +20 |
| | No zone in force, but the camera is marked a restricted area | +10 |
| | A drone sighting over a drone security zone, when no camera zone was in force: special inspection; restricted; no entry; critical | +5, +15, +20, +25 |
| `CRITICALITY` | The place's criticality — the camera's, else the site's: low, high, critical. Medium, and not set, add nothing | −5, +10, +15 |
| `TIME` | Outside the site's business hours, a closed public holiday included. Hours not defined: nothing | +15 |
| `IDENTITY` | A person or vehicle on a block list | +25 |
| | Identified on an allow list, with nobody in the situation left unidentified | −25 |
| `ACCESS` | A door was forced | +25 |
| | Access was refused | +15 |
| | Access was granted nearby in time, and none refused or forced | −10 |
| `CORROBORATION` | Three or more kinds of source reported it, duplicates not counted | +20 |
| | Two kinds of source | +10 |
| | One kind, but a neighbouring camera also saw it | +5 |
| `PERSISTENCE` | The same alert repeated 10 times or more | +10 |
| | Repeated 3 to 9 times | +5 |
| `HISTORY` | An incident at this camera in the last 30 days | +5 |
| | 80% or more of the decided alerts of this kind from this camera were marked false | −25 |
| | 60% to 79% | −15 |
| `EXPECTED` | A contractor permit was in force at the site | −10 |
| | Visitors were signed in, during business hours | −5 |
| `ANOMALY` | Anomaly of 80 or more: unusual for the place and hour | +10 |
| | Anomaly of 20 or less: usual | −10 |
| `DRONE` | The drone module's own assessment of its sighting: HIGH, CRITICAL | +10, +15 |
| | A drone held and looked again because a person asked, and saw more of the same thing | +5 |
| | …and saw nothing more | −5 |
| `GUARD` | A guard raised an SOS | +20 |
| `CONFIDENCE` | The model's highest confidence was 0.90 or more | +5 |
| | It was below 0.60 | −10 |

A person who is merely **not identified** appears nowhere in this table. One
allow-listed person does not vouch for an unidentified one beside them.

**A second look is worth a little, either way.** When an officer has a drone
hold and look again, what it saw comes back as an event. More detections of the
same thing is a confirmation; none is a small reason for less concern — a hold
of half a minute does not prove a place empty. Only the latest look counts, and
a look is the same kind of source as the sighting, so it does not make a
situation "reported by two kinds of source". Either way the assessment is
written again, which is the point: the officer decides next on what the drone
saw.

**A virtual patrol that reported nothing lowers nothing.** The last patrol check
of the camera is in the context — "checked this camera 40 min before: 3
question(s) answered, nothing reported" — and is not a factor. It says what an
officer saw then, not what is true now. A patrol that reported an exception is
an event in its own right, and counts as one.

A false-positive share is used only when at least five alerts of that kind from
that camera have been decided; three out of three is too few to call a camera
unreliable.

### Levels

The drone module's own bands, so that *HIGH* means one thing wherever an officer
reads it.

| Level | Score |
|---|---|
| `INFO` | 0–14 |
| `LOW` | 15–34 |
| `MEDIUM` | 35–54 |
| `HIGH` | 55–79 |
| `CRITICAL` | 80–100 |

### A tenant's own weights

`intel.risk_weights`, a tenant setting written through the settings API
(`settings:write`): an object of factor name to a multiplier from 0 to 3. A
factor left out counts as shipped; 0 switches a factor off. An unknown factor
name or a number out of range is refused.

```json
{"TIME": 2, "HISTORY": 0}
```

The multiplier is applied to each factor's points and the result rounded. The
factors written on the assessment are the weighted ones, so the explanation
always adds up to the score.

## Three confidences, kept apart

| | Is | Comes from |
|---|---|---|
| `detection` | How sure the model was about what it saw | The highest confidence among the situation's events, copied. Empty when no source gave one (an alarm panel) |
| `correlation` | How firmly the events belong together | The situation's weakest link. Empty for a situation of one event |
| `risk` | How complete the picture was when the risk was scored | 1 − 0.12 for each thing not known, never below 0.3 |

They are three columns in the database and three fields in the API, and are
never combined into one number. The fourth — how sure a recommendation is —
belongs to each recommendation and is described in `AI_DECISION_WORKFLOW.md`.

*Not known* means: no criticality set, business hours not defined, who a person
or vehicle is, too few decided alerts to say how reliable the camera is, too
little history to say what is usual, an event with no site.

## What it appears to be

A `kind`, which is a code that later stages and screens branch on, and a short
`label`, which is the same thing in the layer's own words. The first that
applies:

| When | Kind | Label |
|---|---|---|
| A guard's SOS is among the events | `GUARD_EMERGENCY` | Guard emergency |
| A weapon alert | `WEAPON` | Possible weapon — requires review |
| A fire or smoke alert | `FIRE_SMOKE` | Possible fire or smoke — requires review |
| A door was forced | `DOOR_FORCED` | Door forced |
| Access was refused, and a camera or a drone also reported | `ACCESS_REFUSED` | Access refused, with activity seen nearby |
| Access was refused | `ACCESS_REFUSED` | Access refused |
| An alarm | `ALARM` | Alarm |
| A fall alert | `FALL` | Possible fall — requires review |
| A block-listed person or vehicle | `BLOCK_LISTED` | Block-listed person · Block-listed vehicle |
| A restricted zone was in force, out of hours | `RESTRICTED_ZONE` | Suspicious activity in a restricted zone, out of hours |
| A restricted zone was in force | `RESTRICTED_ZONE` | Activity in a restricted zone |
| A virtual patrol exception, with no intrusion alert beside it | `PATROL_FINDING` | Virtual patrol finding |
| Only a camera that stopped sending | `CAMERA_OFFLINE` | Camera stopped sending |
| Anything else, out of hours | `ACTIVITY` | Unusual activity out of hours |
| Anything else | `ACTIVITY` | Activity that requires review |

"Door forced" and "Alarm" gain "— with activity seen nearby" when a camera or a
drone also reported.

The `summary` is a sentence made from the label, the level and score, the three
factors that raised the risk most, the two that lowered it most, and how many
things were not known. It is a template over the stored factors; no language
model writes it.

## The record

`security_assessments`, one row per assessment:

| Column | Holds |
|---|---|
| `situation_id`, `sequence` | Which situation, and which assessment of it: 1, 2, 3 … |
| `kind`, `label`, `summary` | What it appears to be, as a code and in words, and the sentence |
| `risk_score`, `risk_level`, `risk_factors` | The score, the band, and every factor with its points and its sentence. At least one factor, always |
| `normality_score`, `anomaly_score`, `normality_factors` | Both set or both empty |
| `detection_confidence`, `correlation_confidence`, `risk_confidence` | The three, separately |
| `context` | What was known about the place and the moment when it was scored: the statements with their sources, what was expected, and what was not known |
| `event_count`, `engine_version` | How many events it rested on; `rules-2` (`rules-1` before phase 10) |

**Written once, never changed.** A situation is assessed again whenever its
events change. If the answer is the same — the same score, label and factor
points — nothing is written: a repeat alert that changes nothing is not news. If
it differs, a **new row** is added with the next sequence. What was believed at
02:18, before the drone arrived, stays beside what was believed at 02:21, and an
officer who decided at 02:19 decided on the first.

The database holds that line, not only the code: the application's role is
granted `SELECT` and `INSERT` on this table and nothing else, so it cannot
update or delete an assessment.

**It carries its own reasons.** The explanation an officer reads later is read
from the row. It is not recomputed from a database that has since moved on: a
site profile filled in tomorrow does not rewrite what was known tonight.

The situation keeps the latest `risk_score`, `risk_level` and `assessed_at` so a
list can be sorted and filtered. Those are a convenience; the assessment rows
are the record.

## Announcement

`intel_assessment_ready` on the tenant's existing channel, only when a new
assessment was written, and after it was saved: the situation's id and number,
the label, the risk score and level, the three confidences as three fields, the
anomaly score, and the sentences of the top three factors.

## API

| Method | Path | Permission | Returns |
|---|---|---|---|
| GET | `/security-intelligence/situations` | `intel:read` | Each situation with its latest `risk_score`, `risk_level` and `assessed_at`. `risk_level` filters; `sort=risk` puts the highest risk first and those not yet assessed last |
| GET | `/security-intelligence/situations/{situation_id}` | `intel:read` | The situation, and `assessment`: the latest, with its factors, the three confidences, the statements it rested on with their sources, and what was not known. Null until the situation has been assessed |
| GET | `/security-intelligence/situations/{situation_id}/assessments` | `intel:read` | Every assessment of the situation, oldest first |

A caller restricted to certain sites gets 404 for another site's situation, the
same as for one that does not exist.

## Limits

Stated so that nobody reads more into a score than is there.

- **The weights are judgement.** They were chosen, not measured against
  outcomes. They are in one file and in a tenant's setting so that they can be
  argued with. The feedback dataset collects what officers decided and what
  reviewers said, so that a person can check them; nothing moves a weight by
  itself (`AI_SECURITY_INTELLIGENCE_ARCHITECTURE.md`, *Feedback*).
- **Habit counts alerts, not people.** A camera that was offline for a month
  looks like a camera where nothing happened, and what it then reports looks
  unusual. A camera whose alert rule changed has a past that no longer describes
  it.
- **History is measured from the camera's first alert of any kind.** A camera
  that has raised nothing has no history, however long it has been installed.
- **The context is that of the most serious event**, the earliest if there are
  several. A situation that moves across a site is scored from where its most
  serious event was.
- **Assessed when the events change, not when the site changes.** Setting a
  site's hours does not re-score the situations already assessed; the next event
  does.
- **No decay.** A situation's risk does not fall because time passes; it changes
  when an event joins it.
- **A restricted area and a drone zone can both count** under `ZONE` when a
  drone sighting is over a camera marked as a restricted area.
- **Not calibrated across tenants.** A HIGH at one tenant and a HIGH at another
  are the same arithmetic over different settings.

## Tests

`backend/tests/test_intel_risk.py` (39):

- **The rules, with nothing running:** the bands equal the drone engine's; no
  normality score on too little history; each factor moves the score the stated
  way; the same detection at an entrance by day and in a warehouse by night;
  what is not known adds nothing and lowers the risk confidence; a person nobody
  identified gets no points; the three confidences stay apart; a tenant's
  weights; the labels, and that no assessment contains *intruder*,
  *unauthorised*, *criminal*, *thief* or *trespass*.
- **Against the database, as the application's own role:** a camera's habit
  counted week by week beside alerts that must not count; a camera with no
  alerts has no history; assessed once, not again when nothing changed, not
  rewritten by a repeat, and a second row when a door is refused; the first row
  unchanged afterwards; the role cannot update or delete an assessment; two
  tenants assessed separately.
- **The runner and the API:** only tenants that asked; announced only when the
  answer changed, with three confidences on the wire; the assessment served with
  its reasons; sorting and filtering by risk; a restricted supervisor and another
  tenant get 404; the weights setting refuses nonsense.
- **The schema:** row-level security forced, select-and-insert only; no
  assessment without a factor, with scores that disagree, or out of range.

`backend/tests/test_intel_docs.py` checks this document's factors, points,
bands, labels and API table against the code.
