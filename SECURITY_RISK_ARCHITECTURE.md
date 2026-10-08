# Risk Patterns and Advice — Architecture

**Phase 9 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-08, migration `0151`. This document says what was built, the
rules it is built under, and what it deliberately does not do.

Before this phase the platform could say where and in which hours *situations*
begin (the intelligence layer's insight), show alert activity by camera and
hour, and weigh a drone zone. Nothing read incidents, refused doors, missed
patrols, device outages and missed response clocks the same way; no statement
said how much history it rested on; and nobody could say what they had made of
one.

```
 what the platform already keeps ──► COUNTED when somebody asks ──► where it gathers:
 incidents · doors · patrols ·        (nothing of it is stored)      hour and day · week · place
 device outages · missed clocks                                            │
                                                                           ▼
                                        ADVICE: a statement, the count under it,
                                        how much history it rests on, what to consider
                                                                           │
                                                                           ▼
                              a person's ANSWER, kept:  ACCEPTED │ NOT_ACCEPTED, with why
                                        (it changes nothing else)
```

---

## 1. The rules

1. **A pattern that recurred is not a forecast.** Every statement is a count
   over a stated period, with the count in it. Nothing says what will happen.
   Both readings (`/patterns`, `/advice`) carry `is_forecast: false` and a note
   that says so, each piece of advice carries it too, and the screen shows the
   note.
2. **Confidence is how much history a statement rests on. It is not a
   probability.** It is made of how many records, over how many weeks, and in
   how many of those weeks the same held. The screen's words for it are "rests
   on much / some / little history".
3. **A week counts only when the pattern holds within that week by itself.**
   One busy afternoon can put half a month's records into one band of hours;
   that is one week in which it held, not four.
4. **Nothing before is not the same as a rise.** When the earlier half of a
   period has no records at all, the counts cannot tell a change from the start
   of recording, and the statement says exactly that, at `LOW`.
5. **Fixed rules, counted when asked.** No model, nothing learned, nothing
   stored of the counting (owner decision E2). The same records give the same
   advice.
6. **Nobody is named.** A place is a camera, a door, a patrol, a device or a
   clock. No person is counted, ranked or described.
7. **The platform advises; a person answers; the answer changes nothing
   else.** Accepting advice raises no work, moves no guard and alters no
   roster. It is a person's record that they read it and what they made of it.
8. **The answer is kept with the advice as it stood.** The server counts the
   advice again before it keeps an answer, and keeps its own statement — not
   one the caller sent. An answer is added and never rewritten: somebody who
   changes their mind answers again, and both stay.
9. **An answer is a signed-in person's** — not an API key, not a support
   session — and is audited.
10. **Super Admin, a guard and the client role hold neither of the new
    permissions.**

---

## 2. What is counted

Five kinds of thing that went wrong, each from rows the platform already keeps
(`services/risk_patterns.py`):

| Kind | Counted from | Its place is |
|---|---|---|
| `INCIDENT` | Every incident, when it was opened, at the site of its camera | The camera |
| `ACCESS` | Door events recorded as `denied`, `forced` or `tamper` | The door |
| `PATROL` | Virtual patrols `MISSED` or `FAILED`; drone patrols `MISSED`, `FAILED` or `BLOCKED`; guard tours `missed` — each at the time it was due | The schedule, the mission or the tour |
| `DEVICE` | Each time a camera's stream disconnected; each time another device was read as `DOWN` (phase 8's log) | The device |
| `SLA` | Each response clock recorded as missed (phase 4), once an organisation has switched the clocks on (`response.sla_enabled`) | The clock |

Situations are the intelligence layer's and are counted there
(`services/intel_insight.py`); this phase does not count them again. Alerts
keep their own heatmap. A door that let somebody in, a patrol that was done and
a device that is working are not counted: this is a count of what went wrong,
not of activity.

A period is whole weeks ending now: 1 to 12, 4 unless asked. Each kind is
counted three ways:

- **by weekday and hour** — seven rows of twenty-four, Monday first, in the
  time zone of the site's security profile when it has one and the
  organisation's otherwise;
- **by week** of the period, oldest first;
- **by place**, the most first, with each place's share. The answer gives the
  first eight.

One answer counts at most 20,000 records of a kind. Past that it counts the
first 20,000 and says so (`cut_at`).

Somebody held to particular sites is given those sites only. Somebody who is
not is given every site, and also what has no site — an incident with no
camera, a recorder that belongs to the organisation.

---

## 3. Advice

Every rule but the last speaks only when it has at least 5 records to speak
of. Each piece of advice is a statement with its count, what it rests on, how
much history that is, and one thing a person might consider.

| Code | Speaks when | It held in a week when |
|---|---|---|
| `RECURRING_HOURS` | One band of 4 hours — wrapping past midnight, the earliest on a tie — holds 50% or more of the records | That band holds 50% or more of that week's own records |
| `RECURRING_DAY` | The period is 2 weeks or more and one weekday holds 40% or more | That day holds 40% or more of that week's own records |
| `RECURRING_PLACE` | There is more than one place and one holds 40% or more | That place holds 40% or more of that week's own records |
| `RISING` | The later half of the period has 5 or more, the earlier half has some, and the later has at least twice as many | — it compares two halves, and is never `HIGH` |
| `FIRST_RECORDED` | The later half has 5 or more and the earlier half has none | — always `LOW` |
| `REPEATED_DEVICE` | A device went down 3 times or more in the period; the three most only | It went down in that week |

A period of one week has no halves to compare. One of an odd number of weeks
is halved without its oldest week.

What a statement looks like, from the development organisation's own records:

> 87% of the missed patrols of the last 4 weeks fell between 08:00 and 12:00
> (13 of 15). *Rests on 15 records over 4 weeks; the same held within 4 of
> those weeks.* To consider: whether those hours have the people, patrols and
> attention the rest of the day has.

> 125 device outages in the last 6 weeks; none were recorded in the 6 weeks
> before. Whether that is a change, or only when recording began, cannot be
> told from the counts.

The second is rule 4 at work: the earliest device outage on record there is
five weeks old.

**Confidence** (`confidence()`):

| Level | Needs |
|---|---|
| `HIGH` | 30 records or more, over 4 weeks or more, and it held in three weeks of every four |
| `MEDIUM` | 10 records or more, over 2 weeks or more, and it held in half of them — or it is a statement with no weeks to hold in |
| `LOW` | Anything less |

Every confidence comes with its reason in words (`why`) and the numbers it was
made of (`records`, `weeks`, `held_in_weeks`). What rests on the most history
is given first; then what rests on the most records.

---

## 4. A person's answer

Advice is answered for **one site**: the site is part of what a piece of advice
is known by (`code:kind:site:subject`). Advice for every site together is read,
not answered, and the answer says why (`answer_note`).

- `ACCEPTED`, or `NOT_ACCEPTED` with a reason. Without the reason the server
  refuses (422), and so does the database (`ck_advans_reason`).
- The server counts the advice again first. If it no longer stands for that
  site and period the answer is refused (409) and nothing is kept.
- What is kept in `risk_advice_answers`: the statement as it stood, what it
  rested on, its confidence, the period, the answer, the reason, who and when.
- The application's role may read and add rows, and nothing else. There is no
  route that changes or removes an answer.
- When advice is shown again, the latest answer to it is shown with it. If the
  statement is no longer the one that was answered — the counts moved, or
  another period is being looked at — what it said then is shown beside it
  (`said_then`).
- An answer sends no notification and starts nothing.

---

## 5. API

Under `/api/v1/security-advice`, all needing `advice:read`:

| | | Also needs |
|---|---|---|
| `GET` | `/patterns` | |
| `GET` | `/advice` | |
| `POST` | `/advice/answer` | `advice:answer` |
| `GET` | `/advice/answers` | |

There is no `PUT`, `PATCH` or `DELETE`. Audited: `advice.answer`. Reading is
not audited: it reads counts, not a person's record.

**Permissions** (migration `0151`):

| | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Guard 5 | Viewer 6 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|
| `advice:read` | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – |
| `advice:answer` | ✓ | ✓ | ✓ | – | – | – | – | – |

---

## 6. Screens

- **Risk & Advice** (`/risk-advice`), under Security Intelligence, for a site
  or every site and a period, in three parts — **What stands out**: the note
  that none of it is a forecast, what confidence means, and each piece of
  advice with how much history it rests on, what to consider, its answer, and
  Accept / Not accepted for whoever may answer. **Where it gathers**: the five
  kinds with their counts; one kind as a week of hours shaded in four steps,
  with a key that says in counts what each shade stands for, every cell
  readable without its shade, and the numbers themselves on request; its places
  and its weeks. **Answers given**: what was answered, by whom, why, and what
  the advice said then.

The phone is not changed in this phase. The existing heatmap (`/heatmap`), the
intelligence layer's insight (`/security-insight`) and the drone analytics are
unchanged.

---

## 7. Files

| | |
|---|---|
| `backend/alembic/versions/0151_risk_advice_answers.py` | One table, its policy and grants, two permissions |
| `backend/app/services/risk_patterns.py` | What is counted, the counting, the rules, confidence. Reads only |
| `backend/app/routers/security_advice.py` | Patterns, advice, answering, the answers given |
| `frontend/src/api/securityAdvice.ts` | The typed client |
| `frontend/src/pages/risk/` | The screen |
| `frontend/src/components/risk/` | Its wording and shading |

Existing files changed, by additions only: `backend/app/main.py` (the router is
registered), `frontend/src/App.tsx` (one route),
`frontend/src/components/layout/Sidebar.tsx` (one menu entry),
`frontend/src/hooks/usePermission.ts` (the two permissions).

No existing table is altered. The new table refers to `sites` and `users`. The
service reads `incidents`, `cameras`, `access_events`, `access_doors`,
`virtual_patrol_sessions`, `drone_patrol_sessions`, `tour_occurrences`,
`camera_health_events`, `device_health_changes` and `incident_escalations` —
and, for what a place is called, `tour_schedules`, `patrol_routes`,
`iot_sensors`, `drones`, `drone_edge_gateways`, `alarm_panels` and
`nvr_connections` — and writes to none of them.

The table is `risk_advice_answers` and not `security_advice`, as the plan had
it: tables named `security_…` are the intelligence layer's own.

---

## 8. Tests

| | |
|---|---|
| `backend/tests/test_security_advice.py` | The counting, the band of hours, confidence, each rule, one busy afternoon, a rise and nothing-before; the five kinds from the database and what is left out; advice and its answers; sites and organisations; what the application's role and the database refuse; who holds what |
| `backend/tests/test_security_risk_docs.py` | That this document says what the code does |
| `frontend/src/pages/risk/riskAdvice.test.tsx` | The screen: its words, the shading and its key, answering, refusals |

---

## 9. What this does not do

- **It forecasts nothing.** There is no prediction, no likelihood and no
  model. "Most fell between 20:00 and 00:00" is what was recorded, and is not
  a statement about tonight.
- **It makes no risk score.** A single number for a site or a place would mix
  kinds that are not alike and hide the counts it was made of. The counts are
  shown instead.
- **It does not allow for how much is watched.** A place with more cameras,
  more doors or more patrols has more recorded of it. More records at a place
  is not more danger there, and nothing here says it is.
- **It does not tell a real record from a test.** A burst of incidents raised
  while a camera was being set up is counted like any other; the weeks and the
  "held in" figure are what show it for what it is.
- **It does not know when recording began.** It says so where that matters
  (`FIRST_RECORDED`) rather than guess.
- **Its places are not zones.** A place is the camera, door, patrol, device or
  clock a record already names. The optional places model (E4) is not used, and
  an incident with no camera has no place.
- **It does not count situations, alerts, visitors or people.** Situations and
  alerts have their own readings, which are unchanged; no person is counted.
- **It does not act on an answer.** No work order, patrol, post or roster is
  changed, nobody is told, and nothing is trained on what was answered.
- **It does not answer for every site at once**, and it has no report, export
  or scheduled delivery.
- **The phone is not part of it.**
- **It has run on the development organisation's incidents, missed patrols,
  camera disconnections and three missed clocks.** That organisation has no
  door events, so doors have run on test data only.
