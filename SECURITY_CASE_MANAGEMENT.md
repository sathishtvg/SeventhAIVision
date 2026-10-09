# Security Case Management — Architecture

**Phase 12 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-08, migration `0155`. This document says what was built, the
rules it is built under, and what it deliberately does not do.

Before this phase an incident had a status, notes and an assignee; an
investigation held what a search found; an evidence package held what was to
be handed on. Nothing held the three together as one matter, with the people
working it, what each had to do, who was named in it, and a closing somebody
else had to agree to.

```
 an incident · an investigation · an evidence package ──► LINKED, by reference, to a CASE
                                                                   │
        a lead and investigators · tasks · notes · the people and vehicles named in it
                                                                   │
        OPEN ──► the lead asks to close it, with what was found ──► AWAITING_APPROVAL
                                                                   │
                          somebody else approves ──► CLOSED     or declines, with why ──► OPEN
                                                        │
                                          reopened, with why ──► OPEN
```

---

## 1. The rules

1. **A case refers to what it is about; it copies none of it.** An incident,
   an investigation and an evidence package are linked by reference and stay
   where they are. Taking a link off a case removes nothing of theirs.
2. **A linked record is read under its own permission and the reader's
   sites.** A link to a record the reader may not read says that it is one,
   and what kind, and nothing else — no title, no detail, no id.
3. **A person works on a case they are on.** Its lead and its investigators,
   and whoever manages cases. Somebody given a task finishes that task, and
   does nothing else to the case.
4. **Closing takes two people.** Whoever asks for a case to be closed says
   what was found; somebody else approves. The screen does not offer approving
   to whoever asked, the route refuses it, and the database refuses it again.
5. **A closed case is not changed.** A trigger refuses any change to it but
   reopening it, and refuses anything added to it.
6. **Nothing is removed.** An investigator, a link and a named person or
   vehicle are taken off — a link and a name with why — and the row stays. A
   task that will not be done is dropped, with why. A note is not changed or
   removed afterwards. A closed case goes only if the organisation has set a
   retention period for closed cases, and then whole
   (`ENTERPRISE_SECURITY_HARDENING.md`, section 2).
7. **A case has its own history.** Each step — opened, a note, who leads, who
   was put on or taken off, closing asked for, approved or declined, reopened
   — is added to it, and it is never rewritten: the application's role may
   read and add, and nothing else.
8. **Being named in a case is not an accusation.** A person or a vehicle is
   recorded with how it is connected — reported it, saw or heard it, was
   affected by it, is named in it — by a person. There is no way to record
   anybody as a suspect, and nothing here names anybody by itself.
9. **Every step is a signed-in person's** — not an API key, not a support
   session — and is audited.
10. **Nobody is told.** Being put on a case, given a task or asked to approve
    sends no notification: each is found on the list of cases.
11. **Super Admin, a guard and the client role hold none of the new
    permissions.**

---

## 2. A case

A case has a number (`CASE-0001`, numbered for each organisation), a title,
what it is about, a kind, a priority, a site or no one site, a lead, and a
status:

| Status | Means | Then |
|---|---|---|
| `OPEN` | Being worked on | `AWAITING_APPROVAL` |
| `AWAITING_APPROVAL` | Somebody has asked for it to be closed and said what was found. Nothing is added to it | `CLOSED`, or back to `OPEN` |
| `CLOSED` | Approved by somebody else. Not changed | `OPEN`, by reopening |

Its kind is one of `THEFT`, `TRESPASS`, `DAMAGE`, `SAFETY`, `ACCESS` and
`OTHER`. There is none for a member of staff's conduct: that is not a security
case.

**Opening.** Whoever opens a case leads it, unless somebody who manages cases
names another lead. Opened from an incident, an investigation or an evidence
package the opener may read, that record is its first link and its site is the
case's unless one is given. Somebody held to particular sites opens a case at
one of them, not one that spans every site.

**Who is on it.** The lead and the investigators, set by whoever manages
cases, from the people who may work on cases (`case:work`).

**Tasks** are given to somebody or to nobody yet. A task ends `DONE`, with
what was done, or `DROPPED`, with why. A case is not asked to be closed while
a task is still `OPEN`.

**Linked records** (`services/case_files.py`):

| Kind | Read under |
|---|---|
| `INCIDENT` | `incident:read` |
| `INVESTIGATION` | `investigation:read` |
| `EVIDENCE_PACKAGE` | `evidence:package:read` |

Each link is shown to a reader as `SHOWN`, with what the record is;
`NOT_PERMITTED`, when the reader does not hold its permission; or
`NOT_AVAILABLE`, when it is at a site the reader is not shown or is no longer
there.

**People and vehicles named in it** are a name or a plate, as whoever added it
wrote it, with one of `REPORTED_IT`, `WITNESS`, `AFFECTED`, `NAMED` and
`OTHER`.

**Closing.** `request-close` takes what was found and what was done.
`approve-close` is for somebody who manages cases and did not ask.
`decline-close` puts it back to `OPEN` with why; what was written as found
stays in the history. `reopen` opens a closed case again, with why.

**The report** is the whole case in order — what it is about, what was found,
who is on it, its tasks, its links as the reader may see each, who is named in
it, and its history — as a reading (`/report`) and as a PDF (`/report.pdf`). A
linked record the reader may not read is in it as that. Taking the report is
audited, and a support session takes none.

---

## 3. API

Under `/api/v1/cases`, all needing `case:read`:

| | | Also needs |
|---|---|---|
| `GET` | `/` | |
| `GET` | `/options` | |
| `POST` | `/` | `case:work` |
| `GET` | `/{id}` | |
| `PATCH` | `/{id}` | `case:work` |
| `PUT` | `/{id}/lead` | `case:manage` |
| `POST` | `/{id}/investigators` | `case:manage` |
| `POST` | `/{id}/investigators/{user}/remove` | `case:manage` |
| `POST` | `/{id}/notes` | `case:work` |
| `POST` | `/{id}/tasks` | `case:work` |
| `POST` | `/{id}/tasks/{task}/done` | `case:work` |
| `POST` | `/{id}/tasks/{task}/drop` | `case:work` |
| `POST` | `/{id}/links` | `case:work` |
| `POST` | `/{id}/links/{link}/remove` | `case:work` |
| `POST` | `/{id}/parties` | `case:work` |
| `POST` | `/{id}/parties/{party}/remove` | `case:work` |
| `POST` | `/{id}/request-close` | `case:work` |
| `POST` | `/{id}/approve-close` | `case:manage` |
| `POST` | `/{id}/decline-close` | `case:manage` |
| `POST` | `/{id}/reopen` | `case:manage` |
| `GET` | `/{id}/report` | |
| `GET` | `/{id}/report.pdf` | |

There is no `DELETE`. Holding `case:work` is not enough to work on a case: the
caller is also on it, or manages cases, or — for finishing a task — was given
it. Audited: `case.open`, `case.update`, `case.lead`,
`case.investigator.add`, `case.investigator.remove`, `case.note`,
`case.task.add`, `case.task.done`, `case.task.drop`, `case.link.add`,
`case.link.remove`, `case.party.add`, `case.party.remove`,
`case.close.request`, `case.close.approve`, `case.close.decline`,
`case.reopen`, `case.report`.

**Permissions** (migration `0155`):

| | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Guard 5 | Viewer 6 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|
| `case:read` | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – |
| `case:work` | ✓ | ✓ | ✓ | ✓ | – | – | – | – |
| `case:manage` | ✓ | ✓ | ✓ | – | – | – | – | – |

---

## 4. Screens

- **Cases** (`/cases`), under Investigate: the cases the reader may read, the
  newest first, by status and by "mine"; opening a case, from a recent record
  the reader may read or from nothing; and a case itself — where it stands,
  who is on it, its tasks, its linked records as the reader may see each, the
  people and vehicles named in it with the note that being named is not an
  accusation, its history, and its closing with the note that it takes two
  people. A button is there only when the server offers the act.

The phone is not changed in this phase. The existing incident
(`/incidents`), investigation (`/investigations`) and evidence package
(`/evidence-packages`) screens are unchanged.

---

## 5. Files

| | |
|---|---|
| `backend/alembic/versions/0155_case_files.py` | Six tables, their policies, grants and triggers, three permissions |
| `backend/app/services/case_files.py` | What a case may hold, who may do what, linked records as the reader may see them, the PDF |
| `backend/app/routers/cases.py` | The cases, and every step of one |
| `frontend/src/api/cases.ts` | The typed client |
| `frontend/src/pages/cases/` | The screen |
| `frontend/src/components/cases/` | Its dialogs and wording |

Existing files changed, by additions only: `backend/app/main.py` (the router is
registered), `frontend/src/App.tsx` (one route),
`frontend/src/components/layout/Sidebar.tsx` (one menu entry),
`frontend/src/hooks/usePermission.ts` (the three permissions).

No existing table is altered. The six tables are `case_files`,
`case_investigators`, `case_tasks`, `case_entries`, `case_links` and
`case_parties`; they refer to `sites` and `users`, and to no table of the
records a case is about — a link is a kind and an id.

The tables are not named `security_cases…`, as the plan had them: tables named
`security_…` are the intelligence layer's own.

---

## 6. Tests

| | |
|---|---|
| `backend/tests/test_cases.py` | Who may do what; a case from opening through closing by two to reopening; whose case somebody may read and what of its links; what the application's role and the database refuse |
| `backend/tests/test_case_docs.py` | That this document says what the code does |
| `frontend/src/pages/cases/cases.test.tsx` | The screen: its words, the list, opening a case, a case and every step of it, closing |

---

## 7. What this does not do

- **It does not open a case by itself.** No rule, incident or situation opens
  one: a person does.
- **It names nobody by itself**, and has no word for a suspect. The people and
  vehicles in a case are those a person wrote in.
- **It does not connect a named person to the platform's own records.** A name
  is text. It is not matched to a visitor, a member of staff, a watchlist
  entry or a face, and a plate is not matched to a vehicle read at a gate.
- **It does not change what a case is about.** Closing a case does not resolve
  its incident, close its investigation or seal its evidence package.
- **It tells nobody.** There is no notification for being put on a case,
  given a task, or asked to approve.
- **It has no deadlines of its own.** A task may have a date it is due; nothing
  escalates, and no clock runs on a case.
- **A task cannot be edited or given to somebody else.** It is dropped and
  added again.
- **A situation is not linked.** The intelligence layer's situations stay
  with the layer; an investigation opened from one can be linked.
- **Its report is a PDF and a reading.** It is not signed, and it is not put
  into an evidence package.
- **The phone finishes a task and nothing else.** Somebody given a task says
  on the phone that it is done, or why it is dropped (My Case Tasks, added
  on 2026-10-09 with no new route). The case itself is worked on the web.
- **It has run on test data only.** The development organisation has no cases.
