# Enterprise Security Hardening — Retention, Subject Reports and the Sweep

**Phase 13 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-09, migration `0156`; the owner's decisions of the same day are
migrations `0157` and `0158`. This document says what was built, the rules it
is built under, what the sweep found, and what the owner decided.

Before this phase each retention period was where the job that applies it reads
it — the organisation's settings, a site's recording policy, the installation's
configuration, a constant in a module — and nothing said them together. Nothing
said of the records no job removes that none does. Somebody asking what is held
about a person could be given their account and its audit entries, and nothing
of the twelve phases before this one. And each phase had tested its own tables
and routes; nothing asked the same questions of all of them at once.

```
 what each job reads ─────────► THE RETENTION STATEMENT: every period in force,
 settings · a site's policy ·     where it is set, what removes it, what a hold stops;
 the installation · the code      and, for the rest, that nothing removes it

 every column that names a person ──► A SUBJECT REPORT: where somebody appears
 in what the expansion added          and how often — not what each record says;
                                      asked by a person, and written down

 the database's catalogue ─┐
 the route table ──────────┴──► THE SWEEP: 36 tables and 177 routes asked the same
                                 questions, each exception named with its reason
```

---

## 1. The rules

1. **The statement reads; it sets nothing.** A period is changed where it has
   always been changed: in the organisation's settings and in a site's
   recording policy. Four kinds of the newer records may be given a period,
   through a route of their own that says first what the period would remove.
2. **It says what the jobs read, read the same way.** Each value comes from the
   job's own function or its own constant, so the statement cannot say one
   number while a job applies another.
3. **Where a period comes from is said with it**, because they are not equally
   sure. A period that falls back to the installation's default is read by the
   API from its own configuration, while the job that applies it runs in a
   service of its own: the statement says so beside every such period.
4. **No period is a statement too.** Most of what the platform keeps is removed
   by no job. Of what the expansion added, four kinds are kept until the
   organisation sets a period for them and the rest has none; each of its
   tables is listed with whether it names a person.
5. **It says what is configured, not what the law requires.** How long a
   record ought to be kept is the organisation's to decide and is written
   nowhere in the code.
6. **A subject report says where and how often, not what.** A case's history
   or an investigation's notes hold other people too; what of a record is one
   person's is for somebody to judge, reading it on its own screen under its
   own permission.
7. **Every column that refers to a person is read.** The list is compared with
   the database's own catalogue by a test, so a column added later cannot be
   left out without that test saying so.
8. **A name is text.** A name or a plate that somebody typed is found by the
   words, and what is found is text that matches — not an identification of
   anybody.
9. **A subject report is the organisation's own to ask for** — by a person who
   is signed in, not an API key and not a support session — and each one is a
   line in the audit log: who asked, about whom or about what words, and how
   much was found.
10. **It covers every site**, so it is read by somebody who is not held to
    particular sites. A count for some sites only would be taken for the whole
    answer.
11. **A name typed is sent in the body of a request, never in its address.**
12. **The sweep reads the catalogue and the route table, not a list somebody
    keeps.** A table or a route added later is asked the same questions.
13. **An exception is named, with its reason.** Nothing passes the sweep by
    being left out of it.
14. **Nothing that already worked was changed to pass it.** What the sweep and
    the work around it found in what existed before the expansion was put to
    the owner, who decided each on 2026-10-09 (section 9).
15. **Super Admin, an operator, a guard and the client role hold neither of the
    new permissions.**

---

## 2. The retention statement

`GET /api/v1/data-governance/retention` (`services/retention_statement.py`).
Six kinds of record have a period, because a job removes them:

| Kind | Records | The period is | Removed by | A hold |
|---|---|---|---|---|
| `EVIDENCE` | Pictures and clips kept as evidence | `evidence.retention_days`, or the installation's default | The scheduler, once a day | Stops it |
| `RECORDINGS` | Continuous recordings | The site's recording policy; or `recording.retention_days`; or the installation's default | The recording supervisor, about once an hour | Stops it |
| `DRONE_FOOTAGE` | Drone footage | `evidence.retention_days`, the same as evidence | The drone runner, every few hours | Stops it |
| `DRONE_TRACKS` | Drone flight tracks | `drone.telemetry_retention_days`, or the installation's default | The drone runner, every few hours | None applies |
| `DRONE_RECEIPTS` | Receipts of what a site gateway sent | Fixed in the code | The drone runner, as it works | None applies |
| `AUDIT` | The audit log | The installation's, in years; the same for every organisation | The scheduler, once a day | None applies |

- **The audit log is moved, not deleted**: a month at a time, out of the log
  and into a file, once every entry of that month is old enough.
- **Drone footage is also kept past its period** when it is of an event that
  became an incident, of an event that was confirmed and is still open, or of
  a flight still in progress.
- **A source** is one of `SITE_POLICY`, `TENANT_SETTING`, `INSTALLATION` and
  `FIXED`. A setting no job would accept — nothing, zero, a word — falls back
  exactly as the job falls back.
- **Site by site.** Each site the reader may see, with how long its recordings
  are kept centrally and where that is set, how long at the site, and whether
  its policy keeps nothing centrally at all.
- **Holds.** How many are in force, by kind, and beside each kind of record
  that a hold stops, how many of it are held now. Somebody held to particular
  sites is given those sites and their holds.
- **Kept until a period is set.** Four kinds of the newer records, twelve
  tables, may be given a period by the organisation (below).
- **Kept, with no period.** The other twenty-four tables the expansion added,
  in eight groups, each table with whether it names a person. Three tables of
  the thirty-six lose a row by a person's own step — an item taken out of a
  package that has not been sealed, and two lists that are replaced when they
  are set again — and an authorisation goes with its visitor when a
  data-subject erasure removes the visitor.
- **A data-subject erasure** is the existing step, unchanged, and the
  statement says what it removes or blanks.

Reading the statement is not audited: it names no person.

### Periods the organisation may set

`services/record_retention.py`. Nothing is removed unless a period is set: with
none, each kind is kept as before.

| Kind | What is removed, whole | Counted from | Kept whatever its age |
|---|---|---|---|
| `CASES` | A closed case with everything on it | When its closing was approved | A case that is open, or waiting for approval to close |
| `INVESTIGATIONS` | A closed investigation with what was put into it | When it was closed | One that is open; one an evidence package was made from; one linked to a case that is not closed |
| `VISITOR_AUTHORIZATIONS` | An authorisation with its places and its reviews | The end of the period it was for | — |
| `WORKFORCE_ANSWERS` | An answer to a recommendation | When the answer was given | — |

- **Only what is over has a period.** An open case is never removed, however
  old.
- **Evidence packages, their custody and their holds have none.** A chain of
  custody that expires is not one.
- **A period is at least thirty days**, in whole days. That is a guard against
  a slip of the hand, not a recommendation.
- **Setting one is asked for twice when it would remove something.**
  `PUT /retention/periods/{kind}` answers first with how many are already
  older than the period (409), and sets it when that number is said back. So
  nobody sets a period without having been told what it will remove.
- **It is set by a person who may change the organisation's settings**
  (`settings:write`) and who is not held to particular sites — not an API key,
  not a support session — and each change is audited with what it was and what
  it became.
- **Setting a period removes nothing by itself.** The scheduler removes what is
  over and older, once a day, on its superuser session: the application's own
  role may not delete from these tables at all. One organisation's removal is
  one line in its audit log — how many of each kind, under what period.
- **A parent is removed and its parts go with it**, by the database's own
  rule. The records a case or an investigation referred to are not touched.

---

## 3. A subject report

Where a person appears in what the expansion keeps
(`services/subject_records.py`). Three kinds of subject:

| Subject | Asked with | What is read |
|---|---|---|
| A member of staff | `GET /subjects/staff/{user}` | Each of the 71 columns, in 32 tables, that refers to a member of staff |
| A visitor | `GET /subjects/visitor/{visitor}` | Their authorisations, the places those are for, and the reviews of where their badge was used |
| A name or a plate, as typed | `POST /subjects/written` | Names and plates written into cases, and the name of whoever a work order was given to |

For each kind of record the report gives how many name the subject, as what,
and between which dates. A member of staff is named in two ways and the report
tells them apart: `ABOUT` — the record concerns them: the guard who was sent,
the host of a visit, whoever was given a task — and `BY` — they are the one
who took the step.

- **Who looked for them.** Every investigation search is already in the audit
  log with what it asked. The report counts those that asked about the member
  of staff, the plate or the words, and how many people made them.
- **A plate is the same plate however it was spaced** when it was written in.
- **What is typed is words, not a pattern**: `%` and `_` are read as
  themselves.
- **Choosing somebody.** `POST /subjects/find` matches a name to the members of
  staff or the visitors of the organisation, to choose one. It finds; it
  reports nothing, and is not audited.
- **What it does not read**, said in every report: the account, its sessions
  and its audit entries, which are in the existing data-subject export;
  attendance, rosters, leave, pay, training and violations, each on its own
  screen; what the cameras saw; and a name written in a title or a note.
- **Nothing of what a record says is in it.** No title, no note, no name of
  anybody else.

---

## 4. The sweep

`backend/tests/test_expansion_hardening.py`. It found nothing to change in what
the expansion added. What it holds in place:

### Tables — 36

Every one is under row level security, forced on its owner too, with one policy
named `tenant_isolation_…` that reads and writes the caller's organisation only,
for every command and every role. The application's role reads and adds to each.
It empties none, hangs no key on one and puts no trigger on one.

| The application's role may | Tables | Why |
|---|---|---|
| Delete a row | `evidence_package_items`, `sop_incident_types`, `visitor_authorization_places` | An item is taken out of a package that has not been sealed; two lists are replaced when they are set again |
| Change any column | `escalation_policies`, `site_places` | A setting a person edits as a whole. The policy's check keeps each row in its organisation |
| Add and read, and nothing else | 12: `case_entries`, `device_health_changes`, `evidence_custody_events`, `incident_escalations`, `incident_response_steps`, `occurrence_entry_corrections`, `occurrence_entry_reviews`, `risk_advice_answers`, `site_instruction_reads`, `sop_passages`, `visitor_movement_reviews`, `workforce_advice_answers` | Histories and answers: never rewritten |
| Change named columns only | The other 19 | Never a row's id or its organisation, and never when or by whom it was made — but for a briefing's draft, which says who counted it again and when |

Twelve tables have a trigger that holds a settled thing still: a closed case
and what is on it, a published briefing, a sealed package and its items, a work
order that is over, a confirmed shift summary, a decided version of a procedure.

**Another organisation** reads no row of any of the 36, and a row for one
organisation put in from another's scope is refused by the policy before
anything else is looked at. With no organisation in scope, nothing is read.

### Routes — 177, of 16 routers

72 `GET`, 87 `POST`, 10 `PATCH`, 7 `PUT`, 1 `DELETE`.

- **A permission on every one.** One asks for either of two in its own code:
  `GET /incident-responses/{incident}`, read by whoever reads responses or by
  the guard who was sent.
- **An id in an address is an id**; anything else is no route at all. Three
  addresses hold a word, each checked against a list: `{key}` of a report,
  `{kind}` of a device and `{kind}` of a retention period.
- **A body takes the fields it declares and no other.**
- **One route removes a row**: `DELETE /evidence-packages/{package}/items/{item}`.
- **Nobody without a token gets anything, and no role without the permission
  does.** Every route is asked with no token, and as a guard, a viewer, an
  operator and the client role.
- **The platform owner holds nothing that opens any of them.** The client role
  holds one existing permission that does, `dob:read`: it opens the occurrence
  book's search, one entry and the kinds of entry, as it opens the existing
  list. What reviewers wrote is served only to those who keep the book.
- **No route hands back where a file is kept.**

**Every write is audited**, but these:

| | | Why not |
|---|---|---|
| `POST` | `/occurrence-book/instructions/{instruction}/read` | The reading is the record: a row that says who read which instruction, and when |
| `PATCH` | `/occurrence-book/shift-summaries/{summary}` | A draft is corrected. Confirming or discarding it is audited, and says whether it was edited |
| `PATCH` | `/sop/versions/{version}` | A draft is corrected. Submitting, approving and rejecting it are audited |
| `POST` | `/sop/ask` | It reads |
| `POST` | `/data-governance/subjects/find` | It reads. The report that follows is audited |

**Every write is a person's** — not an API key, not a support session — but
`POST /sop/ask`: whoever may read the library may ask it.

Four requests are not a `GET` and change nothing: `POST /sop/ask`,
`POST /investigations/search`, `POST /data-governance/subjects/find` and
`POST /data-governance/subjects/written`. Each carries what is asked in its
body.

**Records leaving the platform are the organisation's own to take.** Each of
these refuses a support session, and is audited:

| | |
|---|---|
| `GET` | `/operations-reports/{key}` |
| `GET` | `/cases/{case}/report` |
| `GET` | `/cases/{case}/report.pdf` |
| `POST` | `/evidence-packages/{package}/export` |
| `GET` | `/evidence-packages/{package}/items/{item}/file` |
| `GET` | `/data-governance/subjects/staff/{user}` |
| `GET` | `/data-governance/subjects/visitor/{visitor}` |

---

## 5. API

Under `/api/v1/data-governance`:

| | | Needs |
|---|---|---|
| `GET` | `/retention` | `retention:read` |
| `PUT` | `/retention/periods/{kind}` | `retention:read`, `settings:write` |
| `POST` | `/subjects/find` | `subject:report` |
| `GET` | `/subjects/staff/{user}` | `subject:report` |
| `GET` | `/subjects/visitor/{visitor}` | `subject:report` |
| `POST` | `/subjects/written` | `subject:report` |

Nothing is changed or removed by any of them but a period being set or taken
away. Audited: `subject.report`, `retention.period.set`. A subject report is
counted against a limit of its own, thirty a minute for one person.

**Permissions** (migration `0156`):

| | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Guard 5 | Viewer 6 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|
| `retention:read` | ✓ | ✓ | ✓ | – | – | ✓ | – | – |
| `subject:report` | ✓ | ✓ | – | – | – | – | – | – |

---

## 6. Screens

- **Data Retention** (`/data-retention`), under Reports & Billing, in two
  parts — **Retention**: every period in force with where it is set, what
  removes it and what a hold does, and beside a period that falls back, that
  the job's own is the one in force; the sites' own periods; the holds; what
  has no period; and what a data-subject erasure removes. The periods in force
  have no control on it. The four kinds that may be given a period are shown
  with how each stands, and — for somebody who may change the organisation's
  settings — with setting, changing and taking away, each after being told
  what it will remove. **About a person**, for whoever may ask: a member of staff or a
  visitor chosen by name, or a name or a plate as it was typed; where they
  appear and how often, each line saying whether it concerns them or is a step
  they took; who searched for them; what is not read here; and, for words
  typed, that what matched is text.

The phone is not changed in this phase. The existing settings (`/settings`),
audit log (`/audit`) and recording policy screens are unchanged.

---

## 7. Files

| | |
|---|---|
| `backend/alembic/versions/0156_data_governance.py` | Two permissions. No table |
| `backend/app/services/retention_statement.py` | The periods, where each is read from, what is kept. Reads only |
| `backend/app/services/record_retention.py` | The four kinds that may be given a period, and the job that removes what is over and older |
| `backend/app/services/visitor_movement_events.py` | A visitor's door events handed to the intelligence layer, when asked |
| `backend/alembic/versions/0157_occurrence_book_append_only.py` | Two rights taken from the application's role |
| `backend/alembic/versions/0158_license_catalogue.py` | Fourteen modules added to the licence catalogue. Rows only |
| `backend/app/services/subject_records.py` | Every column that names a person, and the three reports. Reads only |
| `backend/app/routers/data_governance.py` | The statement, finding somebody, and the reports |
| `frontend/src/api/dataGovernance.ts` | The typed client |
| `frontend/src/pages/governance/` | The screen |
| `frontend/src/components/governance/` | Its wording |

Existing files changed, by additions only: `backend/app/main.py` (the router is
registered), `frontend/src/App.tsx` (one route),
`frontend/src/components/layout/Sidebar.tsx` (one menu entry),
`frontend/src/hooks/usePermission.ts` (the two permissions).

Phase 13 itself created no table and altered none, and changed no grant,
policy, trigger or route that existed. The owner's decisions of section 9
then changed one grant and three routes that existed, and each names the
files it changed.

---

## 8. Tests

| | |
|---|---|
| `backend/tests/test_data_governance.py` | Every period as the job reads it, with the jobs' own functions beside it; what a job deletes and what the expansion added; who may read the statement and who may ask about a person; a member of staff, a visitor, and a name or a plate as typed; that each report is on the record |
| `backend/tests/test_record_retention.py` | A period is whole days, thirty or more; nothing is removed until one is set, then only what is over and older, of that organisation's; setting one is a person's and is asked for twice; who removes |
| `backend/tests/test_visitor_movement_events.py` | Off until asked; each event once, naming nobody; placed by the layer, which decides nothing |
| `backend/tests/test_expansion_hardening.py` | The sweep: every table's row level security, policy and rights; another organisation; every route's permission, parameters and body; every write audited and a person's; nobody without a token and no role without the permission |
| `backend/tests/test_hardening_docs.py` | That this document says what the code does |
| `frontend/src/pages/governance/dataRetention.test.tsx` | The screen: its words, the statement, asking about a person |

---

## 9. Found, and what the owner decided

Each of these was in what existed before the expansion, or was left open by
it. Putting any of them right changes how something that worked behaved, so
each was put to the owner, who decided on 2026-10-09 that all seven be done.
What was changed, and where:

1. **The occurrence book can no longer be rewritten by the application's
   role.** Migration `0157` took `UPDATE` and `DELETE` on
   `occurrence_book_entries` away from it. No code used either: the role reads
   the book and adds to it.
2. **The existing CSV exports write text as text** (`/api/v1/export/…`: alerts,
   incidents, detections, the audit log). A value that begins with `=`, `+`,
   `-` or `@` is written with an apostrophe before it, as the operations
   reports write it. A number is still a number. Changed:
   `backend/app/routers/exports.py`.
3. **The existing data-subject export is about its subject, and leaves a
   line.** `POST /data-compliance/dsr-export/{user}` lists the evidence that
   person opened, downloaded or exported — not the organisation's latest —
   never says where a file is kept, and writes `dsr.export` to the audit log.
   `include_evidence_urls` is still accepted, and no longer acted on. Changed:
   `backend/app/routers/data_compliance.py`.
4. **The API is given the two default periods the jobs use.**
   `AUDIT_RETENTION_YEARS` and `DRONE_TELEMETRY_RETENTION_DAYS` are given to
   the API as well as to the scheduler and the drone runner, in the compose
   files and the chart. The statement still says, beside a period that falls
   back, that the job's own is the one in force: an installation put together
   by hand can still differ. Changed: `docker/docker-compose.yml`,
   `docker/docker-compose.core.yml`,
   `helm/seventh-ai-vision/templates/configmap.yaml`,
   `helm/seventh-ai-vision/values.yaml`.
5. **Privacy masks are read with a credential.**
   `GET /api/v1/privacy/zones/camera/{camera_id}` needs `camera:read` — a
   signed-in person, or the organisation's API key — and is read under row
   level security. Nothing in the repository called it without one. Changed:
   `backend/app/routers/pdpa.py`.
6. **Four kinds of the newer records may be given a retention period**
   (section 2). Nothing is removed until an organisation sets one. Changed:
   `backend/app/core/config_keys.py` (four settings),
   `backend/app/scheduler_main.py` (one step of the daily cycle).
7. **Visitor door events may be handed to the intelligence layer.** Off until
   an organisation switches `visitor.movements_to_intelligence` on; then each
   door event still to be looked at is handed over once, as a low-severity
   event that names nobody. The layer places it and decides nothing. Changed:
   `backend/app/core/config_keys.py` (one setting),
   `backend/app/services/intel_runner.py` (one call in the ingest tick),
   `frontend/src/pages/visitorAuth/VisitorAuthorisations.tsx` (one switch).

Done the same day, for the platform owner: the licence catalogue gained the
fourteen modules built since it was written (migration `0158`; rows only, and
every customer has each as before), and Manage Licenses gained Drone Patrol's
own licence — on or off, an expiry, and how many drones, missions and sites.
Changed: `frontend/src/pages/Tenants.tsx`, `frontend/src/api/platform_licenses.ts`.

Tests that held the old behaviour were changed with it:
`backend/tests/test_data_compliance.py`, `backend/tests/test_p5_pdpa.py`.

---

## 10. What this does not do

- **It removes nothing unless the organisation sets a period.** The statement
  reads. Four kinds may be given a period; with none set they are kept, as
  before.
- **It does not say what the law requires**, and does not judge a period as
  long or short.
- **It does not hand a person's records over.** A subject report says where
  they are. Each is read on its own screen by somebody who may.
- **It does not read everything.** It reads what the expansion added. The
  account has its own export; attendance, rosters, leave, pay, training and
  violations have their own screens.
- **It identifies nobody.** A name typed is matched as text, to what somebody
  wrote. It is not matched to a visitor, a member of staff, a watchlist entry
  or a face.
- **It erases nothing.** The existing data-subject erasure is unchanged. A name
  written into a case is not erased by it: a case goes only whole, under a
  period the organisation has set.
- **The sweep is of what the expansion added.** The routes and tables that
  existed before it were not swept; what was noticed in passing is in
  section 9.
- **The sweep changed nothing of the expansion's.** It found none that needed
  it. What was changed afterwards is what the owner decided (section 9).
- **The phone is not part of it.**
- **It has run on test data only.** The development organisation has not been
  given migrations `0156` to `0158`.
