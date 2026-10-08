# Enterprise Security Hardening — Retention, Subject Reports and the Sweep

**Phase 13 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-09, migration `0156`. This document says what was built, the
rules it is built under, what the sweep found, and what is left for the owner.

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
 the route table ──────────┴──► THE SWEEP: 36 tables and 176 routes asked the same
                                 questions, each exception named with its reason
```

---

## 1. The rules

1. **The statement reads; it sets nothing.** A period is changed where it has
   always been changed: in the organisation's settings and in a site's
   recording policy.
2. **It says what the jobs read, read the same way.** Each value comes from the
   job's own function or its own constant, so the statement cannot say one
   number while a job applies another.
3. **Where a period comes from is said with it**, because they are not equally
   sure. A period that falls back to the installation's default is read by the
   API from its own configuration, while the job that applies it runs in a
   service of its own: the statement says so beside every such period.
4. **No period is a statement too.** Most of what the platform keeps is removed
   by no job. Everything the expansion added is of that kind, and each of its
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
    the work around it found in what existed before the expansion is listed for
    the owner (section 9) and left as it is.
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
- **Kept, with no period.** The thirty-six tables the expansion added, in
  eleven groups, each table with whether it names a person. Three lose a row
  by a person's own step — an item taken out of a package that has not been
  sealed, and two lists that are replaced when they are set again — and an
  authorisation goes with its visitor when a data-subject erasure removes the
  visitor.
- **A data-subject erasure** is the existing step, unchanged, and the
  statement says what it removes or blanks.

Reading the statement is not audited: it names no person.

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

### Routes — 176, of 16 routers

72 `GET`, 87 `POST`, 10 `PATCH`, 6 `PUT`, 1 `DELETE`.

- **A permission on every one.** One asks for either of two in its own code:
  `GET /incident-responses/{incident}`, read by whoever reads responses or by
  the guard who was sent.
- **An id in an address is an id**; anything else is no route at all. Two
  addresses hold a word, each checked against a list: `{key}` of a report and
  `{kind}` of a device.
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
| `POST` | `/subjects/find` | `subject:report` |
| `GET` | `/subjects/staff/{user}` | `subject:report` |
| `GET` | `/subjects/visitor/{visitor}` | `subject:report` |
| `POST` | `/subjects/written` | `subject:report` |

Nothing is changed or removed by any of them. Audited: `subject.report`. A
subject report is counted against a limit of its own, thirty a minute for one
person.

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
  has no period; and what a data-subject erasure removes. Nothing on it changes
  a period. **About a person**, for whoever may ask: a member of staff or a
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
| `backend/app/services/subject_records.py` | Every column that names a person, and the three reports. Reads only |
| `backend/app/routers/data_governance.py` | The statement, finding somebody, and the reports |
| `frontend/src/api/dataGovernance.ts` | The typed client |
| `frontend/src/pages/governance/` | The screen |
| `frontend/src/components/governance/` | Its wording |

Existing files changed, by additions only: `backend/app/main.py` (the router is
registered), `frontend/src/App.tsx` (one route),
`frontend/src/components/layout/Sidebar.tsx` (one menu entry),
`frontend/src/hooks/usePermission.ts` (the two permissions).

No table is created and none is altered. No grant, policy or trigger of any
existing table is changed, and no route that existed is changed.

---

## 8. Tests

| | |
|---|---|
| `backend/tests/test_data_governance.py` | Every period as the job reads it, with the jobs' own functions beside it; what a job deletes and what the expansion added; who may read the statement and who may ask about a person; a member of staff, a visitor, and a name or a plate as typed; that each report is on the record |
| `backend/tests/test_expansion_hardening.py` | The sweep: every table's row level security, policy and rights; another organisation; every route's permission, parameters and body; every write audited and a person's; nobody without a token and no role without the permission |
| `backend/tests/test_hardening_docs.py` | That this document says what the code does |
| `frontend/src/pages/governance/dataRetention.test.tsx` | The screen: its words, the statement, asking about a person |

---

## 9. Found, and left for the owner

Each of these is in what existed before the expansion. Putting any of them
right changes how something that works today behaves, so none was changed.

1. **The occurrence book can be rewritten by the application's role.** The
   book is described as append-only and no code edits or removes an entry, but
   the role holds `UPDATE` and `DELETE` on `occurrence_book_entries`. Taking
   them away is one migration; it changes an existing table's grants.
2. **The existing CSV exports write a value as it is** (`/api/v1/export/…`:
   alerts, incidents, detections, the audit log). Text that begins with `=`,
   `+`, `-` or `@` — an incident's title, say — is run as a formula by a
   spreadsheet that opens the file. The operations reports make such text
   plain; the existing exports do not.
3. **The existing data-subject export is not about its subject in one part,
   and leaves no line of its own.** `POST /data-compliance/dsr-export/{user}`
   returns the organisation's latest hundred pieces of evidence whoever they
   show, can be asked to include where each file is kept, and writes nothing
   to the audit log.
4. **The audit log's period is the scheduler's own.** The installation gives
   `AUDIT_RETENTION_YEARS` to the scheduler and `DRONE_TELEMETRY_RETENTION_DAYS`
   to the drone runner, and not to the API. Changed for one service only, the
   statement would show the built-in default — which is why it says, beside
   each, that the job's own is the one in force. Giving the API the same two
   variables makes the statement certain; it is a change to the installation's
   files.
5. **Privacy masks are read without a token.** `GET /api/v1/privacy/zones/camera/{camera_id}`
   is open by design, for the AI workers: anybody who knows a camera's id can
   read where its masks are drawn.
6. **Nothing the expansion added has a retention period.** A period for a
   case, a visitor's authorisation or an answer about a guard is a decision
   with legal weight, and removing rows from tables that are added to and
   never rewritten needs a job that is allowed to. It was not decided here.

---

## 10. What this does not do

- **It sets no period and removes nothing.** The statement reads.
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
  written into a case is not erased by it: a case's rows are never removed.
- **The sweep is of what the expansion added.** The routes and tables that
  existed before it were not swept; what was noticed in passing is in
  section 9.
- **It changes no grant, policy or route to make anything stricter.** The
  sweep found none of the expansion's that needed it.
- **The phone is not part of it.**
- **It has run on test data only.** The development organisation has not been
  given migration `0156`.
